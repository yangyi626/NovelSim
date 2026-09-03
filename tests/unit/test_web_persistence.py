"""FastAPI 会话流程与持久化存储的轻量集成测试。"""

import asyncio
import importlib
import json

from engine import (
    ActionStep,
    DeterministicManuscriptWriter,
    ActorActionChain,
    JointPlan,
    NarrativePlannerError,
    PlanRuntimeStatus,
    SQLiteWorldStore,
    ToolCall,
    TurnResult,
    WorldPackageStore,
    commit_dialogue_perceptions,
    commit_event,
    create_plan_runtime,
    replay_events,
)
from engine.chapter_catalog import ChapterCatalogStore
from examples.huarong_lane import build_snapshot
from examples.huarong_lane.canonical_case import (
    JIYUE,
    PULL_INTO_SPACE,
    YEFU,
)
from examples.huarong_lane.scenario import LIN, NIGHT, QINGQING
from world_schema import NarrativeOutput, Operation, OperationKind, StatePatch


web_app = importlib.import_module("web.app")


class _FakePipeline:
    def run(self, user_text, state, default_actor_id, **kwargs):
        patch = StatePatch(
            operations=[
                Operation(
                    op=OperationKind.set_flag,
                    path="test.web_turn",
                    value=user_text,
                )
            ]
        )
        event, new_state = commit_event(
            state,
            action_id="act_web_test",
            event_type="observe",
            patch=patch,
            actor_ids=[default_actor_id],
            expected_version=state.version,
        )
        return TurnResult(
            status="committed",
            event=event,
            new_state=new_state,
        )


class _NarrativePipeline(_FakePipeline):
    def run(self, user_text, state, default_actor_id, **kwargs):
        result = super().run(user_text, state, default_actor_id, **kwargs)
        result.narrative = NarrativeOutput(
            narration="夜色压在巷口，她向前迈出一步。",
            grounded_event_ids=[result.event.event_id],
            referenced_entity_ids=[default_actor_id],
        )
        return result


class _CapturingManuscriptWriter:
    def __init__(self, manuscript_id):
        self.manuscript_id = manuscript_id
        self.previous_passage = None
        self.state_version = None

    def write(
        self,
        events,
        state,
        chapter_number=None,
        previous_passage=None,
    ):
        self.previous_passage = previous_passage
        self.state_version = state.version
        return DeterministicManuscriptWriter(
            manuscript_id=self.manuscript_id,
            events_per_passage=len(events),
        ).write(
            events,
            state,
            chapter_number=chapter_number,
            previous_passage=previous_passage,
        )


class _RejectingPipeline:
    def run(self, *_args, **_kwargs):
        return TurnResult(
            status="rejected",
            error="WORLD_CONCEPT_UNAVAILABLE",
        )


def test_auto_actor_selection_follows_protagonist_and_activated_plot_drivers():
    state = web_app.build_canonical_start_state()
    state.characters[NIGHT].location_id = "loc_yefu"

    selected = web_app._select_planning_actors(state, NIGHT, [])
    assert selected == [NIGHT, "char_lin_guanjia"]

    state.flags["canonical.lin_warning_done"] = True
    selected = web_app._select_planning_actors(state, NIGHT, [])
    assert selected == ["char_jiyue"]


def test_web_persists_dialogue_effects_that_activate_next_plot_driver(tmp_path):
    store = SQLiteWorldStore(tmp_path / "dialogue-effects.sqlite3")
    state = web_app.build_canonical_start_state()
    state.characters[NIGHT].location_id = "loc_yefu"
    session_id = store.create_session(
        state,
        default_actor_id=NIGHT,
        world_package_id=web_app.CANONICAL_CH1_PACKAGE_ID,
    )
    source_event, after_dialogue = commit_event(
        state,
        action_id="night_warns_lin",
        event_type="tool.talk_to",
        patch=StatePatch(),
        actor_ids=[NIGHT],
        target_ids=[LIN, "loc_yefu"],
        expected_version=state.version,
        presentation_events=[
            {
                "event_type": "dialogue",
                "payload": {
                    "speaker_id": NIGHT,
                    "to_id": LIN,
                    "line": "不要再参与夜清清的陷害。",
                    "tone": "警告",
                },
            }
        ],
    )
    store.commit_turn(
        session_id,
        expected_version=state.version,
        new_state=after_dialogue,
        event=source_event,
    )

    updated, derived = commit_dialogue_perceptions(
        after_dialogue,
        [source_event],
        store=store,
        session_id=session_id,
    )

    assert len(derived) == 1
    assert updated.flags["canonical.lin_warning_done"] is True
    assert store.get_state(session_id).flags["canonical.lin_warning_done"] is True
    assert [event.event_type for event in store.list_events(session_id)] == [
        "tool.talk_to",
        "system.dialogue_perceived",
    ]


class _FakeNarrativePlanner:
    def __init__(self):
        self.call_traces = []

    def generate(
        self,
        state,
        actor_ids,
        *,
        beat_goal,
        goal_id,
        permissions_by_actor,
        metadata,
    ):
        actor_id = actor_ids[0]
        return JointPlan(
            goal_id=goal_id,
            base_world_version=state.version,
            actor_chains={
                actor_id: ActorActionChain(
                    actor_id=actor_id,
                    steps=[
                        ActionStep(
                            step_id="web_talk",
                            tool_call=ToolCall(
                                actor_id=actor_id,
                                tool_name="talk_to",
                                arguments={
                                    "target_character_id": QINGQING,
                                    "message": "我会查清真相。",
                                    "tone": "冷静",
                                },
                            ),
                        )
                    ],
                )
            },
            metadata={**metadata, "beat_goal": beat_goal},
        )


class _RetryingWebPlanner:
    def __init__(self, *, always_fail=False):
        self.always_fail = always_fail
        self.attempts = []
        self.call_traces = []

    def generate(
        self,
        state,
        actor_ids,
        *,
        beat_goal,
        goal_id,
        permissions_by_actor,
        metadata,
    ):
        self.attempts.append({"attempt": 1, "error_category": "stagnant_dialogue"})
        if self.always_fail:
            self.attempts.append({"attempt": 2, "error_category": "stagnant_dialogue"})
            raise NarrativePlannerError(
                "real LLM narrative planning failed for char_jiyue after 2 "
                "attempts: ValueError: stagnant dialogue loop: choose a "
                "state-progressing action or a new target"
            )
        self.attempts.append({"attempt": 2, "tool_name": "invoke_ability"})
        actor_id = actor_ids[0]
        return JointPlan(
            goal_id=goal_id,
            base_world_version=state.version,
            actor_chains={
                actor_id: ActorActionChain(
                    actor_id=actor_id,
                    steps=[
                        ActionStep(
                            step_id="jiyue_invoke",
                            tool_call=ToolCall(
                                actor_id=actor_id,
                                tool_name="invoke_ability",
                                arguments={"ability_id": PULL_INTO_SPACE},
                            ),
                        )
                    ],
                )
            },
            metadata={**metadata, "beat_goal": beat_goal},
        )


def _canonical_web_session(store):
    state = web_app.build_canonical_start_state()
    state.characters[NIGHT].location_id = YEFU
    state.flags["canonical.lin_warning_done"] = True
    return store.create_session(
        state,
        default_actor_id=NIGHT,
        world_package_id=web_app.CANONICAL_CH1_PACKAGE_ID,
    )


def test_web_joint_plan_retries_stagnant_jiyue_and_persists_only_final_plan(
    tmp_path, monkeypatch
):
    store = SQLiteWorldStore(tmp_path / "canonical-web.sqlite3")
    planner = _RetryingWebPlanner()
    monkeypatch.setattr(web_app, "SESSIONS", store)
    monkeypatch.setattr(web_app, "PLAN_PLANNER_FACTORY", lambda _package_id: planner)
    sid = _canonical_web_session(store)

    response = web_app.api_generate_joint_plan(
        web_app.JointPlanGenerateRequest(
            session_id=sid,
            goal="推进姬月与夜轻歌的关系",
        )
    )

    assert response["status"] == "ok"
    assert response["plan"]["status"] == "draft"
    assert response["plan"]["actor_chains"][0]["actor_id"] == JIYUE
    assert response["plan"]["actor_chains"][0]["steps"][0]["tool_call"][
        "tool_name"
    ] == "invoke_ability"
    assert [item["attempt"] for item in planner.attempts] == [1, 2]
    assert store.get_joint_plan_runtime(sid, response["plan"]["plan_id"]) is not None
    assert store.get_state(sid).version == 0


def test_web_joint_plan_stagnant_failure_returns_422_without_persisting_runtime(
    tmp_path, monkeypatch
):
    store = SQLiteWorldStore(tmp_path / "canonical-web-failure.sqlite3")
    planner = _RetryingWebPlanner(always_fail=True)
    monkeypatch.setattr(web_app, "SESSIONS", store)
    monkeypatch.setattr(web_app, "PLAN_PLANNER_FACTORY", lambda _package_id: planner)
    sid = _canonical_web_session(store)

    response = web_app.api_generate_joint_plan(
        web_app.JointPlanGenerateRequest(
            session_id=sid,
            goal="推进姬月与夜轻歌的关系",
        )
    )

    assert response.status_code == 422
    body = json.loads(response.body)
    assert body["status"] == "error"
    assert "stagnant dialogue loop" in body["error"]
    assert [item["attempt"] for item in planner.attempts] == [1, 2]
    assert store.list_joint_plan_runtimes(sid) == []
    assert store.get_state(sid).version == 0


def test_web_turn_is_restorable_after_store_recreation(
    tmp_path, monkeypatch
):
    database = tmp_path / "web.sqlite3"
    store = SQLiteWorldStore(database)
    monkeypatch.setattr(web_app, "SESSIONS", store)
    monkeypatch.setattr(web_app, "PIPELINE", _FakePipeline())

    started = web_app.api_start()
    sid = started["session_id"]
    result = web_app.api_turn(
        web_app.TurnRequest(
            session_id=sid,
            text="向前一步",
            use_npc_agents=False,
        )
    )

    assert result["status"] == "committed"
    reopened = SQLiteWorldStore(database)
    restored = reopened.get_state(sid)
    assert restored is not None
    assert restored.version == 1
    assert restored.flags["test.web_turn"] == "向前一步"


def test_web_turn_persists_manuscript_and_player_view_reads_it(
    tmp_path, monkeypatch
):
    database = tmp_path / "manuscript-web.sqlite3"
    store = SQLiteWorldStore(database)
    monkeypatch.setattr(web_app, "SESSIONS", store)
    monkeypatch.setattr(web_app, "PIPELINE", _NarrativePipeline())

    started = web_app.api_start()
    sid = started["session_id"]
    result = web_app.api_turn(
        web_app.TurnRequest(
            session_id=sid,
            text="向前一步",
            use_npc_agents=False,
        )
    )

    assert result["status"] == "committed"
    assert result["manuscript"]["status"] == "ready"
    passage_id = result["manuscript"]["passage_id"]

    reopened = SQLiteWorldStore(database)
    monkeypatch.setattr(web_app, "SESSIONS", reopened)
    passage = reopened.get_manuscript_passage(passage_id)
    assert passage is not None
    assert passage.paragraphs == ["夜色压在巷口，她向前迈出一步。"]
    assert passage.current_revision == 1

    player_view = web_app.api_player_view(session=sid)
    assert player_view["schema_version"] == "player_story_view.v2"
    assert player_view["manuscript"]["total_passages"] == 1
    projected = player_view["novel_passages"][0]
    assert projected["passage_id"] == passage_id
    assert projected["paragraphs"] == passage.paragraphs
    assert projected["generation_status"] == "ready"
    assert projected["generation_kind"] == "narrative_output"


def test_manuscript_retry_appends_revision_without_replaying_world(
    tmp_path, monkeypatch
):
    store = SQLiteWorldStore(tmp_path / "manuscript-retry.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    monkeypatch.setattr(web_app, "PIPELINE", _FakePipeline())

    started = web_app.api_start()
    sid = started["session_id"]
    result = web_app.api_turn(
        web_app.TurnRequest(session_id=sid, text="观察四周")
    )
    passage_id = result["manuscript"]["passage_id"]
    before_event_ids = [event.event_id for event in store.list_events(sid)]
    store.fail_manuscript_passage(passage_id, "临时 writer 失败")

    retried = web_app.api_retry_manuscript_passage(
        passage_id,
        web_app.ManuscriptRetryRequest(session_id=sid),
    )

    assert retried["status"] == "ok"
    assert retried["manuscript"]["revision"] == 2
    assert store.get_state(sid).version == 1
    assert [event.event_id for event in store.list_events(sid)] == before_event_ids
    assert len(store.list_manuscript_passage_revisions(passage_id)) == 2


def test_ready_manuscript_rewrite_appends_revision_without_world_changes(
    tmp_path, monkeypatch
):
    store = SQLiteWorldStore(tmp_path / "manuscript-ready-rewrite.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    monkeypatch.setattr(web_app, "PIPELINE", _FakePipeline())
    writer_holder = {}

    def writer_factory(manuscript_id):
        writer = _CapturingManuscriptWriter(manuscript_id)
        writer_holder["writer"] = writer
        return writer

    monkeypatch.setattr(web_app, "MANUSCRIPT_WRITER_FACTORY", writer_factory)
    monkeypatch.setattr(web_app, "_manuscript_writer_mode", lambda: "llm")

    started = web_app.api_start()
    sid = started["session_id"]
    result = web_app.api_turn(
        web_app.TurnRequest(session_id=sid, text="观察四周")
    )
    passage_id = result["manuscript"]["passage_id"]
    web_app.api_turn(
        web_app.TurnRequest(session_id=sid, text="继续向前")
    )
    before_state = store.get_state(sid).json(sort_keys=True)
    before_events = [event.json(sort_keys=True) for event in store.list_events(sid)]
    before_turns = [record.result for record in store.list_turns(sid)]

    rewritten = web_app.api_retry_manuscript_passage(
        passage_id,
        web_app.ManuscriptRetryRequest(
            session_id=sid,
            rewrite_ready=True,
            expected_revision=1,
        ),
    )

    assert rewritten["status"] == "ok"
    assert rewritten["manuscript"]["revision"] == 2
    assert store.get_state(sid).json(sort_keys=True) == before_state
    assert [event.json(sort_keys=True) for event in store.list_events(sid)] == before_events
    assert [record.result for record in store.list_turns(sid)] == before_turns
    assert len(store.list_manuscript_passage_revisions(passage_id)) == 2
    assert writer_holder["writer"].state_version == 1


def test_manuscript_reader_rewrite_smoke_preserves_authoritative_history(
    tmp_path, monkeypatch
):
    """完整串联事件提交、初次阅读、ready 重写和再次阅读。"""

    store = SQLiteWorldStore(tmp_path / "manuscript-reader-smoke.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    monkeypatch.setattr(web_app, "PIPELINE", _NarrativePipeline())
    monkeypatch.setattr(
        web_app,
        "_manuscript_writer_mode",
        lambda: "reuse_narrative",
    )

    started = web_app.api_start()
    sid = started["session_id"]
    committed = web_app.api_turn(
        web_app.TurnRequest(
            session_id=sid,
            text="向前一步",
            use_npc_agents=False,
        )
    )
    passage_id = committed["manuscript"]["passage_id"]

    first_view = web_app.api_player_view(session=sid)
    first_passage = first_view["novel_passages"][0]
    assert first_passage["passage_id"] == passage_id
    assert first_passage["revision"] == 1
    assert first_passage["paragraphs"] == [
        "夜色压在巷口，她向前迈出一步。"
    ]
    assert first_passage["reader_safe"] is True

    before_state = store.get_state(sid).json(sort_keys=True)
    before_events = [event.json(sort_keys=True) for event in store.list_events(sid)]
    before_turns = [record.result for record in store.list_turns(sid)]
    before_source_event_ids = list(first_passage["source_event_ids"])

    monkeypatch.setattr(
        web_app,
        "MANUSCRIPT_WRITER_FACTORY",
        lambda manuscript_id: _CapturingManuscriptWriter(manuscript_id),
    )
    monkeypatch.setattr(web_app, "_manuscript_writer_mode", lambda: "llm")
    rewritten = web_app.api_retry_manuscript_passage(
        passage_id,
        web_app.ManuscriptRetryRequest(
            session_id=sid,
            rewrite_ready=True,
            expected_revision=1,
        ),
    )

    assert rewritten["status"] == "ok"
    assert rewritten["manuscript"]["revision"] == 2
    second_view = web_app.api_player_view(session=sid)
    second_passage = second_view["novel_passages"][0]
    assert second_view["manuscript"]["total_passages"] == 1
    assert second_passage["passage_id"] == passage_id
    assert second_passage["revision"] == 2
    assert second_passage["paragraphs"] != first_passage["paragraphs"]
    assert second_passage["source_event_ids"] == before_source_event_ids
    assert second_passage["reader_safe"] is True
    assert store.get_state(sid).json(sort_keys=True) == before_state
    assert [event.json(sort_keys=True) for event in store.list_events(sid)] == before_events
    assert [record.result for record in store.list_turns(sid)] == before_turns
    assert len(store.list_manuscript_passage_revisions(passage_id)) == 2


def test_manuscript_revision_history_api_and_pointer_selection(
    tmp_path, monkeypatch
):
    store = SQLiteWorldStore(tmp_path / "manuscript-revision-api.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    monkeypatch.setattr(web_app, "PIPELINE", _FakePipeline())
    monkeypatch.setattr(web_app, "MANUSCRIPT_WRITER_FACTORY", lambda _id: None)

    started = web_app.api_start()
    sid = started["session_id"]
    first = web_app.api_turn(
        web_app.TurnRequest(session_id=sid, text="观察四周")
    )
    passage_id = first["manuscript"]["passage_id"]
    rewritten = web_app.api_retry_manuscript_passage(
        passage_id,
        web_app.ManuscriptRetryRequest(
            session_id=sid,
            rewrite_ready=True,
            expected_revision=1,
        ),
    )
    assert rewritten["status"] == "ok"

    history = web_app.api_list_manuscript_passage_revisions(
        passage_id,
        session=sid,
    )
    assert history["status"] == "ok"
    assert history["current_revision"] == 2
    assert [item["revision_number"] for item in history["revisions"]] == [1, 2]
    assert [item["selected"] for item in history["revisions"]] == [False, True]
    assert all("char_" not in item["text"] for item in history["revisions"])

    selected = web_app.api_select_manuscript_passage_revision(
        passage_id,
        web_app.ManuscriptRevisionSelectRequest(
            session_id=sid,
            revision_number=1,
            expected_revision=2,
        ),
    )
    assert selected["status"] == "ok"
    assert selected["manuscript"]["revision"] == 1
    assert len(store.list_manuscript_passage_revisions(passage_id)) == 2


def test_ready_manuscript_rewrite_requires_current_revision(
    tmp_path, monkeypatch
):
    store = SQLiteWorldStore(tmp_path / "manuscript-stale-rewrite.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    monkeypatch.setattr(web_app, "PIPELINE", _FakePipeline())

    started = web_app.api_start()
    sid = started["session_id"]
    result = web_app.api_turn(
        web_app.TurnRequest(session_id=sid, text="观察四周")
    )
    passage_id = result["manuscript"]["passage_id"]

    stale = web_app.api_retry_manuscript_passage(
        passage_id,
        web_app.ManuscriptRetryRequest(
            session_id=sid,
            rewrite_ready=True,
            expected_revision=0,
        ),
    )

    assert stale.status_code == 409
    assert json.loads(stale.body)["status"] == "conflict"
    assert store.get_manuscript_passage(passage_id).current_revision == 1
    assert len(store.list_manuscript_passage_revisions(passage_id)) == 1


def test_rejected_turn_returns_current_authoritative_state(
    tmp_path, monkeypatch
):
    store = SQLiteWorldStore(tmp_path / "web.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    monkeypatch.setattr(web_app, "PIPELINE", _FakePipeline())

    started = web_app.api_start()
    sid = started["session_id"]
    committed = web_app.api_turn(
        web_app.TurnRequest(session_id=sid, text="advance")
    )
    assert committed["state"]["version"] == 1

    monkeypatch.setattr(web_app, "PIPELINE", _RejectingPipeline())
    rejected = web_app.api_turn(
        web_app.TurnRequest(session_id=sid, text="fly away")
    )

    assert rejected["status"] == "rejected"
    assert rejected["state"]["version"] == 1
    assert rejected["state"]["timeline_id"] == committed["state"]["timeline_id"]
    assert store.get_state(sid).version == 1


def test_events_endpoint_returns_persisted_history(tmp_path, monkeypatch):
    store = SQLiteWorldStore(tmp_path / "web.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    monkeypatch.setattr(web_app, "PIPELINE", _FakePipeline())

    started = web_app.api_start()
    sid = started["session_id"]
    web_app.api_turn(
        web_app.TurnRequest(session_id=sid, text="观察四周")
    )

    payload = web_app.api_events(session=sid)
    assert payload["session_id"] == sid
    assert payload["state_version"] == 1
    assert len(payload["events"]) == 1
    assert payload["events"][0]["action_id"] == "act_web_test"


def test_joint_plan_endpoint_exposes_action_chain_and_wait_state(
    tmp_path,
    monkeypatch,
):
    store = SQLiteWorldStore(tmp_path / "web.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    started = web_app.api_start()
    state = store.get_state(started["session_id"])
    actor_id = started["default_actor"]
    plan = JointPlan(
        plan_id="web_joint_plan",
        goal_id="inspect_plan",
        base_world_version=state.version,
        actor_chains={
            actor_id: ActorActionChain(
                actor_id=actor_id,
                steps=[
                    ActionStep(
                        step_id="inspect_step",
                        tool_call=ToolCall(
                            actor_id=actor_id,
                            tool_name="move_to",
                            arguments={"destination_id": state.current_scene_id},
                        ),
                    )
                ],
            )
        },
    )
    runtime = create_plan_runtime(plan)
    runtime.blocked_reasons[actor_id] = "wait_state:character_at"
    store.save_joint_plan_runtime(started["session_id"], plan, runtime)

    payload = web_app.api_joint_plans(session=started["session_id"])

    assert payload["status"] == "ok"
    assert payload["plans"][0]["plan_id"] == "web_joint_plan"
    chain = payload["plans"][0]["actor_chains"][0]
    assert chain["blocked_reason"] == "wait_state:character_at"
    assert chain["steps"][0]["status"] == "blocked"


def test_web_joint_plan_requires_editable_draft_approval_before_execution(
    tmp_path,
    monkeypatch,
):
    store = SQLiteWorldStore(tmp_path / "web.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    monkeypatch.setattr(
        web_app,
        "PLAN_PLANNER_FACTORY",
        lambda _package_id: _FakeNarrativePlanner(),
    )
    started = web_app.api_start()
    sid = started["session_id"]

    generated = web_app.api_generate_joint_plan(
        web_app.JointPlanGenerateRequest(
            session_id=sid,
            goal="让夜轻歌质问夜清清",
            actor_ids=[NIGHT],
        )
    )
    plan_id = generated["plan"]["plan_id"]
    assert generated["plan"]["status"] == "draft"
    assert generated["plan"]["editable"] is True
    assert store.get_state(sid).version == 0

    blocked = asyncio.run(
        web_app.api_execute_joint_plan(
            plan_id,
            web_app.JointPlanExecuteRequest(session_id=sid),
        )
    )
    assert blocked.status_code == 409
    assert store.get_state(sid).version == 0

    edited_payload = generated["plan"]["raw_plan"]
    edited_payload["actor_chains"][NIGHT]["steps"][0]["tool_call"][
        "arguments"
    ]["message"] = "你隐瞒了什么？"
    edited = web_app.api_update_joint_plan(
        plan_id,
        web_app.JointPlanUpdateRequest(
            session_id=sid,
            plan=edited_payload,
        ),
    )
    assert edited["plan"]["raw_plan"]["actor_chains"][NIGHT]["steps"][0][
        "tool_call"
    ]["arguments"]["message"] == "你隐瞒了什么？"

    approved = web_app.api_approve_joint_plan(
        plan_id,
        web_app.JointPlanControlRequest(session_id=sid),
    )
    assert approved["plan"]["status"] == "approved"

    executed = asyncio.run(
        web_app.api_execute_joint_plan(
            plan_id,
            web_app.JointPlanExecuteRequest(
                session_id=sid,
                run_to_completion=True,
            ),
        )
    )
    assert executed["status"] == "ok"
    assert executed["plan"]["status"] == "completed"
    assert executed["state"]["version"] == 2
    assert [event.event_type for event in store.list_events(sid)] == [
        "tool.talk_to",
        "system.dialogue_perceived",
    ]
    assert executed["events"][0]["event_type"] == "tool.talk_to"
    assert executed["manuscript"]["status"] == "ready"
    passages = store.list_manuscript_passages(sid)
    assert len(passages) == 1
    assert passages[0].source_event_ids == [
        event.event_id for event in store.list_events(sid)
    ]
    assert "system.dialogue_perceived" not in passages[0].text
    assert store.list_turns(sid)[0].result["narrative"]["narration"]


def test_web_joint_plan_auto_approval_is_explicit(tmp_path, monkeypatch):
    store = SQLiteWorldStore(tmp_path / "web.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    monkeypatch.setattr(
        web_app,
        "PLAN_PLANNER_FACTORY",
        lambda _package_id: _FakeNarrativePlanner(),
    )
    started = web_app.api_start()
    generated = web_app.api_generate_joint_plan(
        web_app.JointPlanGenerateRequest(
            session_id=started["session_id"],
            goal="自动推动一次剧情",
            actor_ids=[NIGHT],
            auto_approve=True,
        )
    )

    assert generated["auto_approved"] is True
    assert generated["plan"]["status"] == "approved"


def test_abort_active_joint_plans_keeps_authoritative_world_state(
    tmp_path,
    monkeypatch,
):
    store = SQLiteWorldStore(tmp_path / "web.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    started = web_app.api_start()
    sid = started["session_id"]
    state = store.get_state(sid)

    def saved_plan(plan_id, status):
        plan = JointPlan(
            plan_id=plan_id,
            goal_id=f"goal_{plan_id}",
            base_world_version=state.version,
            actor_chains={
                NIGHT: ActorActionChain(actor_id=NIGHT, steps=[]),
            },
        )
        runtime = create_plan_runtime(plan)
        runtime.status = status
        store.save_joint_plan_runtime(sid, plan, runtime)

    saved_plan("unfinished_active", PlanRuntimeStatus.active)
    saved_plan("unfinished_stale", PlanRuntimeStatus.stale)
    saved_plan("already_completed", PlanRuntimeStatus.completed)

    conflict = web_app.api_generate_joint_plan(
        web_app.JointPlanGenerateRequest(
            session_id=sid,
            goal="不应绕过隐藏的旧规划",
            actor_ids=[NIGHT],
        )
    )
    assert conflict.status_code == 409
    assert json.loads(conflict.body)["plan"]["plan_id"] in {
        "unfinished_active",
        "unfinished_stale",
    }

    payload = web_app.api_abort_active_joint_plans(
        web_app.JointPlanControlRequest(session_id=sid)
    )

    assert set(payload["aborted_plan_ids"]) == {
        "unfinished_active",
        "unfinished_stale",
    }
    statuses = {item["plan_id"]: item["status"] for item in payload["plans"]}
    assert statuses["unfinished_active"] == "aborted"
    assert statuses["unfinished_stale"] == "aborted"
    assert statuses["already_completed"] == "completed"
    assert payload["world_version"] == 0
    assert store.get_state(sid).version == 0


def test_web_turn_retrieves_and_persists_character_memory(
    tmp_path, monkeypatch
):
    store = SQLiteWorldStore(tmp_path / "web.sqlite3")
    captured = {}

    class _CapturingPipeline(_FakePipeline):
        def run(self, user_text, state, default_actor_id, **kwargs):
            captured.update(kwargs)
            return super().run(
                user_text,
                state,
                default_actor_id,
                **kwargs,
            )

    monkeypatch.setattr(web_app, "SESSIONS", store)
    monkeypatch.setattr(web_app, "PIPELINE", _CapturingPipeline())
    started = web_app.api_start()
    sid = started["session_id"]
    store.record_character_memories(
        sid,
        [QINGQING],
        source_event_id="evt_old_book",
        world_version=0,
        content="夜清清曾在书店藏过一本秘密账本。",
        importance=0.8,
    )

    web_app.api_turn(
        web_app.TurnRequest(
            session_id=sid,
            text="去书店寻找账本",
            use_npc_agents=True,
        )
    )

    assert "秘密账本" in captured["npc_memory_context"][QINGQING][0]
    player_memories = store.search_character_memories(
        sid,
        NIGHT,
        "书店账本",
    )
    assert len(player_memories) == 1
    assert player_memories[0].world_version == 1


def test_memory_projection_triggers_reflection_on_configured_interval(
    tmp_path,
    monkeypatch,
):
    store = SQLiteWorldStore(tmp_path / "web.sqlite3")
    state = build_snapshot()
    session_id = store.create_session(
        state,
        default_actor_id=NIGHT,
        world_package_id="huarong_lane",
    )
    event, new_state = commit_event(
        state,
        action_id="reflection-trigger",
        event_type="observe",
        patch=StatePatch(),
        actor_ids=[NIGHT],
        target_ids=[QINGQING],
        expected_version=0,
    )
    new_state.version = 5
    result = TurnResult(
        status="committed",
        event=event,
        new_state=new_state,
    )
    reflected = []
    monkeypatch.setattr(web_app, "SESSIONS", store)
    monkeypatch.setattr(
        web_app,
        "record_event_memory",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        web_app,
        "reflect_character_memories",
        lambda _store, _session, _state, character_id, **_kwargs: (
            reflected.append(character_id)
        ),
    )
    monkeypatch.setenv("MEMORY_REFLECTIONS_ENABLED", "true")
    monkeypatch.setenv("MEMORY_REFLECTION_INTERVAL", "5")
    monkeypatch.setenv("MEMORY_REFLECTION_MIN_EPISODES", "3")

    web_app.persist_turn_memories(session_id, "观察", result)

    assert NIGHT not in reflected
    assert QINGQING in reflected


def test_session_endpoint_restores_boot_payload(tmp_path, monkeypatch):
    store = SQLiteWorldStore(tmp_path / "web.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)

    started = web_app.api_start()
    restored = web_app.api_session(session=started["session_id"])

    assert restored["status"] == "ok"
    assert restored["resumed"] is True
    assert restored["session_id"] == started["session_id"]
    assert restored["default_actor"] == started["default_actor"]
    assert restored["state"]["version"] == 0


def test_session_restores_story_feed_history(tmp_path, monkeypatch):
    store = SQLiteWorldStore(tmp_path / "web.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    monkeypatch.setattr(web_app, "PIPELINE", _FakePipeline())

    started = web_app.api_start()
    sid = started["session_id"]
    web_app.api_turn(web_app.TurnRequest(session_id=sid, text="踏入巷中"))

    restored = web_app.api_session(session=sid)
    assert restored["state"]["version"] == 1
    assert restored["turns"][0] == {"player_input": "踏入巷中"}
    assert restored["turns"][1]["status"] == "committed"


def test_clear_history_preserves_current_session_and_rejects_wrong_confirmation(
    tmp_path, monkeypatch
):
    store = SQLiteWorldStore(tmp_path / "clear-history.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    first = web_app.api_start()
    second = web_app.api_start()
    current_id = first["session_id"]
    old_id = second["session_id"]

    rejected = web_app.api_clear_history(
        web_app.ClearHistoryRequest(
            preserve_session_id=current_id,
            confirmation="错误短语",
        )
    )
    assert rejected.status_code == 422
    assert store.get_state(current_id) is not None
    assert store.get_state(old_id) is not None

    cleared = web_app.api_clear_history(
        web_app.ClearHistoryRequest(
            preserve_session_id=current_id,
            confirmation=web_app.CLEAR_HISTORY_CONFIRMATION,
        )
    )
    cleared_body = json.loads(cleared.body)
    assert cleared_body["status"] == "ok"
    assert cleared_body["candidate_count"] == 1
    assert cleared_body["deleted_count"] == 1
    assert cleared_body["deleted_session_ids"] == [old_id]
    assert cleared_body["preserved_session_id"] == current_id
    assert store.get_state(current_id) is not None
    assert store.get_state(old_id) is None


def test_clear_history_without_candidates_is_safe_noop(tmp_path, monkeypatch):
    store = SQLiteWorldStore(tmp_path / "clear-history-empty.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    response = web_app.api_clear_history(
        web_app.ClearHistoryRequest(
            confirmation=web_app.CLEAR_HISTORY_CONFIRMATION,
        )
    )
    response_body = json.loads(response.body)
    assert response_body["status"] == "ok"
    assert response_body["candidate_count"] == 0
    assert response_body["deleted_count"] == 0


def test_save_management_endpoints(tmp_path, monkeypatch):
    store = SQLiteWorldStore(tmp_path / "web.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)

    first = web_app.api_start()
    second = web_app.api_start()
    listed = web_app.api_saves()
    assert {item["session_id"] for item in listed["saves"]} == {
        first["session_id"],
        second["session_id"],
    }

    renamed = web_app.api_rename_save(
        first["session_id"],
        web_app.RenameSaveRequest(name="我的第一条世界线"),
    )
    assert renamed["save"]["name"] == "我的第一条世界线"

    deleted = web_app.api_delete_save(second["session_id"])
    assert deleted["status"] == "ok"
    assert store.get_state(second["session_id"]) is None


def test_save_listing_does_not_backfill_progression_lineage(tmp_path, monkeypatch):
    store = SQLiteWorldStore(tmp_path / "web.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)

    started = web_app.api_start()
    session_id = started["session_id"]

    listed = web_app.api_saves()

    assert listed["status"] == "ok"
    assert store.get_session_lineage(session_id) is None
    save = next(item for item in listed["saves"] if item["session_id"] == session_id)
    assert save["campaign_id"] == ""
    assert save["root_session_id"] == ""
    assert save["parent_session_id"] == ""


def test_health_and_readiness_endpoints_are_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "DATABASE_PATH", tmp_path / "world.sqlite3")
    monkeypatch.setattr(web_app, "COMPILER_DATABASE_PATH", tmp_path / "compiler.sqlite3")
    monkeypatch.setattr(web_app, "AUTH_DATABASE_PATH", tmp_path / "auth.sqlite3")
    monkeypatch.setattr(web_app, "WORLD_PACKAGE_DIR", tmp_path / "worlds")

    live = web_app.api_health()
    assert live["status"] == "ok"
    assert "E:\\" not in str(live)

    not_ready = web_app.api_ready()
    assert not_ready.status_code == 503
    body = json.loads(not_ready.body)
    assert body["status"] == "not_ready"
    assert set(body["checks"]) == {
        "world_database", "compiler_database", "auth_database", "world_package_directory",
    }
    assert str(tmp_path) not in not_ready.body.decode("utf-8")

    world_store = SQLiteWorldStore(tmp_path / "world.sqlite3")
    catalog_source = tmp_path / "catalog-source.txt"
    catalog_source.write_text(
        "测试书\n\n第1章 起点\n\n正文。\n",
        encoding="utf-8",
    )
    catalog = ChapterCatalogStore(tmp_path / "world.sqlite3")
    catalog.import_book(
        book_id="readiness-book",
        novel="测试书",
        source_path=catalog_source,
    )
    assert world_store.get_state("missing") is None
    from compiler.job_store import CompilationJobStore
    from web.auth import AuthStore
    CompilationJobStore(tmp_path / "compiler.sqlite3")
    AuthStore(tmp_path / "auth.sqlite3")
    (tmp_path / "worlds").mkdir()
    ready = web_app.api_ready()
    assert ready["status"] == "ok"
    assert all(value == "ok" for value in ready["checks"].values())


def test_save_export_import_endpoints_restore_full_history(
    tmp_path, monkeypatch
):
    store = SQLiteWorldStore(tmp_path / "web.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    monkeypatch.setattr(web_app, "PIPELINE", _FakePipeline())

    started = web_app.api_start()
    source_id = started["session_id"]
    web_app.api_turn(
        web_app.TurnRequest(session_id=source_id, text="记住这条世界线")
    )

    exported = web_app.api_export_save(source_id)
    assert exported.headers["content-disposition"].endswith(
        f'world-save-{source_id}.json"'
    )
    backup = json.loads(exported.body)
    imported = web_app.api_import_save(
        web_app.ImportSaveRequest(backup=backup)
    )

    assert imported["status"] == "ok"
    assert imported["session_id"] != source_id
    assert imported["state"]["version"] == 1
    assert imported["save"]["name"].endswith("（导入）")
    assert imported["turns"][0] == {"player_input": "记住这条世界线"}
    assert imported["turns"][1]["status"] == "committed"


def test_save_import_rejects_unknown_world_package(tmp_path, monkeypatch):
    store = SQLiteWorldStore(tmp_path / "web.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    started = web_app.api_start()
    backup = store.export_session(started["session_id"])
    backup["save"]["world_package_id"] = "unknown_world"

    response = web_app.api_import_save(
        web_app.ImportSaveRequest(backup=backup)
    )

    assert response.status_code == 400
    assert "当前不支持世界包" in response.body.decode("utf-8")


def _creator_package_store(tmp_path):
    return WorldPackageStore(
        tmp_path / "worlds",
        builtins={
            "huarong_lane": {
                "package_id": "huarong_lane",
                "novel": "第一狂妃：废柴三小姐",
                "scenario": "华容巷",
                "anchor": "夜轻歌被诬通奸、当众受辱",
                "default_actor_id": NIGHT,
                "source_chapters": [1, 2],
                "snapshot": build_snapshot().dict(),
            }
        },
    )


def test_creator_package_clone_edit_and_start(tmp_path, monkeypatch):
    package_store = _creator_package_store(tmp_path)
    session_store = SQLiteWorldStore(tmp_path / "web.sqlite3")
    monkeypatch.setattr(web_app, "PACKAGES", package_store)
    monkeypatch.setattr(web_app, "SESSIONS", session_store)

    cloned = web_app.api_creator_clone("huarong_lane")
    draft = cloned["package"]
    draft["scenario"] = "华容巷·创作者版"
    draft["anchor"] = "夜轻歌提前识破陷害"
    saved = web_app.api_creator_save(
        draft["package_id"],
        web_app.PackageDraftRequest(
            package=draft,
            expected_revision=draft["revision"],
        ),
    )
    started = web_app.api_start(
        web_app.StartRequest(package_id=draft["package_id"])
    )

    assert saved["package"]["revision"] == 2
    assert started["status"] == "ok"
    assert started["world_meta"]["scenario"] == "华容巷·创作者版"
    assert started["world_meta"]["anchor"] == "夜轻歌提前识破陷害"
    assert started["world_meta"]["package_id"] == draft["package_id"]
    assert started["world_meta"]["source_chapters"] == [1, 2]
    assert (
        session_store.get_metadata(started["session_id"]).world_package_id
        == draft["package_id"]
    )


def test_canonical_chapter_one_checkpoint_is_startable_from_web(
    tmp_path,
    monkeypatch,
):
    session_store = SQLiteWorldStore(tmp_path / "canonical-web.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", session_store)

    packages = web_app.api_world_catalog()["worlds"]
    checkpoint = next(
        item
        for item in packages
        if item["package_id"] == web_app.CANONICAL_CH1_PACKAGE_ID
    )
    assert checkpoint["manifest"]["entry_kind"] == "canonical_checkpoint"
    assert checkpoint["manifest"]["checkpoint_chapter"] == 1

    started = web_app.api_start(
        web_app.StartRequest(package_id=web_app.CANONICAL_CH1_PACKAGE_ID)
    )

    assert started["status"] == "ok"
    assert started["save"]["world_package_id"] == web_app.CANONICAL_CH1_PACKAGE_ID
    assert started["state"]["timeline_id"] == "canon_first_crazy_ch1_5"
    assert started["state"]["flags"]["canonical.checkpoint_chapter"] == 1
    assert started["state"]["flags"]["canonical.future_events_exposed_to_planner"] is False
    assert started["world_meta"]["source_chapters"] == [
        "第1章 华容巷",
        "第2章 那就脱！",
        "第3章 没身材没脸蛋的女人",
        "第4章 你也算香玉？",
        "第5章 狗仗人势的东西",
    ]

    player_view = web_app.api_player_view(session=started["session_id"])
    assert player_view["status"] == "ok"
    assert player_view["canonical_baseline_available"] is True
    assert player_view["story_beats"] == []
    assert len(player_view["comparison"]) == 10
    assert player_view["original_chapters"][0]["title"] == "第1章 华容巷"

    dashboard = web_app.api_world_run_dashboard(started["session_id"])
    assert dashboard["status"] == "ok"
    assert dashboard["dashboard"]["identity"] == "夜轻歌"
    assert dashboard["dashboard"]["current_scene"]["name"] == "华容巷"
    assert dashboard["dashboard"]["context_choices"]
    assert all("goals" not in item for item in dashboard["dashboard"]["present_characters"])


def test_creator_revision_diff_and_review_api(tmp_path, monkeypatch):
    package_store = _creator_package_store(tmp_path)
    monkeypatch.setattr(web_app, "PACKAGES", package_store)
    cloned = web_app.api_creator_clone("huarong_lane")["package"]
    cloned["scenario"] = "华容巷·审核版"
    saved = web_app.api_creator_save(
        cloned["package_id"],
        web_app.PackageDraftRequest(
            package=cloned,
            expected_revision=1,
        ),
    )["package"]

    revisions = web_app.api_creator_revisions(
        cloned["package_id"]
    )
    diff = web_app.api_creator_diff(
        cloned["package_id"],
        from_revision=1,
        to_revision=2,
    )
    pending = web_app.api_creator_review(
        cloned["package_id"],
        web_app.PackageReviewRequest(
            target_status="pending_review",
            expected_revision=saved["revision"],
            note="请审核。",
        ),
    )

    assert [item["revision"] for item in revisions["revisions"]] == [2, 1]
    assert diff["diff"]["change_count"] > 0
    assert pending["package"]["review_status"] == "pending_review"
    assert pending["package"]["revision"] == 3


def _make_canonical_terminal(store, sid):
    state = store.get_state(sid)
    patch = StatePatch(
        operations=[
            Operation(
                op=OperationKind.set_flag,
                path="canonical.hall_summons_issued",
                value=True,
            ),
            Operation(
                op=OperationKind.move_character,
                target_id=NIGHT,
                location_id="loc_ye_clan_hall",
            ),
        ]
    )
    event, new_state = commit_event(
        state,
        action_id="canonical-terminal",
        event_type="test.canonical_terminal",
        patch=patch,
        actor_ids=[NIGHT],
        expected_version=state.version,
    )
    store.commit_turn(sid, expected_version=state.version, new_state=new_state, event=event)


def _move_canonical_player(store, sid, destination_id):
    state = store.get_state(sid)
    event, new_state = commit_event(
        state,
        action_id=f"move-night-{destination_id}",
        event_type="tool.move_to",
        patch=StatePatch(
            operations=[
                Operation(
                    op=OperationKind.move_character,
                    target_id=NIGHT,
                    location_id=destination_id,
                )
            ]
        ),
        actor_ids=[NIGHT],
        target_ids=[destination_id],
        expected_version=state.version,
    )
    store.commit_turn(
        sid,
        expected_version=state.version,
        new_state=new_state,
        event=event,
    )


def test_terminal_world_rejects_plan_generation_before_calling_planner(
    tmp_path, monkeypatch
):
    store = SQLiteWorldStore(tmp_path / "terminal-plan.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    started = web_app.api_start(
        web_app.StartRequest(package_id=web_app.CANONICAL_CH1_PACKAGE_ID)
    )
    sid = started["session_id"]
    _make_canonical_terminal(store, sid)
    monkeypatch.setattr(
        web_app,
        "PLAN_PLANNER_FACTORY",
        lambda _package_id: (_ for _ in ()).throw(
            AssertionError("terminal world must not call planner")
        ),
    )

    response = web_app.api_generate_joint_plan(
        web_app.JointPlanGenerateRequest(session_id=sid)
    )
    body = json.loads(response.body)

    assert response.status_code == 409
    assert body["status"] == "settlement_required"
    assert body["settlement"]["status"] == "available"
    assert store.list_joint_plan_runtimes(sid) == []
    assert store.get_state(sid).version == 1


def test_historical_terminal_remains_available_after_player_leaves_hall(
    tmp_path, monkeypatch
):
    store = SQLiteWorldStore(tmp_path / "historical-terminal.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    started = web_app.api_start(
        web_app.StartRequest(package_id=web_app.CANONICAL_CH1_PACKAGE_ID)
    )
    sid = started["session_id"]
    _make_canonical_terminal(store, sid)
    _move_canonical_player(store, sid, YEFU)

    preview = web_app.api_world_run_settlement(sid)
    response = web_app.api_generate_joint_plan(
        web_app.JointPlanGenerateRequest(session_id=sid)
    )
    body = json.loads(response.body)

    assert store.get_state(sid).characters[NIGHT].location_id == YEFU
    assert preview["settlement"]["status"] == "available"
    assert response.status_code == 409
    assert body["status"] == "settlement_required"
    assert store.get_state(sid).version == 2


def test_execute_stops_after_first_tick_reaches_canonical_terminal(
    tmp_path, monkeypatch
):
    store = SQLiteWorldStore(tmp_path / "terminal-during-plan.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    state = web_app.build_canonical_start_state()
    state.characters[NIGHT].location_id = YEFU
    state.flags["canonical.hall_summons_issued"] = True
    sid = store.create_session(
        state,
        default_actor_id=NIGHT,
        world_package_id=web_app.CANONICAL_CH1_PACKAGE_ID,
    )
    plan = JointPlan(
        plan_id="cross-terminal-plan",
        goal_id="cross-terminal",
        base_world_version=0,
        actor_chains={
            NIGHT: ActorActionChain(
                actor_id=NIGHT,
                steps=[
                    ActionStep(
                        step_id="enter-hall",
                        tool_call=ToolCall(
                            actor_id=NIGHT,
                            tool_name="move_to",
                            arguments={"destination_id": "loc_ye_clan_hall"},
                        ),
                    ),
                    ActionStep(
                        step_id="leave-hall",
                        tool_call=ToolCall(
                            actor_id=NIGHT,
                            tool_name="move_to",
                            arguments={"destination_id": YEFU},
                        ),
                    ),
                ],
            )
        },
    )
    runtime = create_plan_runtime(plan)
    runtime.status = PlanRuntimeStatus.approved
    store.save_joint_plan_runtime(sid, plan, runtime)

    response = asyncio.run(
        web_app.api_execute_joint_plan(
            plan.plan_id,
            web_app.JointPlanExecuteRequest(
                session_id=sid,
                run_to_completion=True,
                auto_replan=False,
            ),
        )
    )

    assert response["status"] == "ok"
    assert response["ticks"] == 1
    assert response["plan"]["status"] == "aborted"
    assert response["plan"]["stale_reasons"] == ["settlement_required"]
    assert store.get_state(sid).characters[NIGHT].location_id == "loc_ye_clan_hall"
    assert store.get_state(sid).version == 1
    assert len(store.list_events(sid)) == 1


def test_terminal_world_aborts_preexisting_plan_without_executing(
    tmp_path, monkeypatch
):
    store = SQLiteWorldStore(tmp_path / "terminal-existing-plan.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    started = web_app.api_start(
        web_app.StartRequest(package_id=web_app.CANONICAL_CH1_PACKAGE_ID)
    )
    sid = started["session_id"]
    state = store.get_state(sid)
    plan = JointPlan(
        plan_id="pre-terminal-plan",
        goal_id="pre-terminal",
        base_world_version=state.version,
        actor_chains={NIGHT: ActorActionChain(actor_id=NIGHT, steps=[])},
    )
    runtime = create_plan_runtime(plan)
    runtime.status = PlanRuntimeStatus.approved
    store.save_joint_plan_runtime(sid, plan, runtime)
    _make_canonical_terminal(store, sid)

    response = asyncio.run(
        web_app.api_execute_joint_plan(
            plan.plan_id,
            web_app.JointPlanExecuteRequest(session_id=sid),
        )
    )
    body = json.loads(response.body)
    _, saved_runtime = store.get_joint_plan_runtime(sid, plan.plan_id)

    assert response.status_code == 409
    assert body["status"] == "settlement_required"
    assert saved_runtime.status == PlanRuntimeStatus.aborted
    assert saved_runtime.stale_reasons == ["settlement_required"]
    assert store.get_state(sid).version == 1
    assert len(store.list_events(sid)) == 1


def test_canonical_settlement_is_unavailable_at_start(tmp_path, monkeypatch):
    store = SQLiteWorldStore(tmp_path / "settlement.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    started = web_app.api_start(
        web_app.StartRequest(package_id=web_app.CANONICAL_CH1_PACKAGE_ID)
    )
    preview = web_app.api_world_run_settlement(started["session_id"])
    assert preview["settlement"]["status"] == "unavailable"
    rejected = web_app.api_settle_world_run(
        started["session_id"], web_app.SettlementRequest()
    )
    assert rejected.status_code == 409
    assert store.get_state(started["session_id"]).version == 0
    assert store.list_events(started["session_id"]) == []


def test_canonical_settlement_is_idempotent_and_replayable(tmp_path, monkeypatch):
    store = SQLiteWorldStore(tmp_path / "settlement.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    started = web_app.api_start(
        web_app.StartRequest(package_id=web_app.CANONICAL_CH1_PACKAGE_ID)
    )
    sid = started["session_id"]
    _make_canonical_terminal(store, sid)
    available = web_app.api_world_run_settlement(sid)
    assert available["settlement"]["status"] == "available"
    assert available["settlement"]["reward_preview"]["reward_points"] == 100

    first = web_app.api_settle_world_run(
        sid,
        web_app.SettlementRequest(expected_version=1, ending_id=web_app.CANONICAL_CH1_PACKAGE_ID),
    )
    assert first["settlement"]["status"] == "settled"
    assert first["reward"]["reward_points"] == 100
    assert store.get_state(sid).flags["settlement.reward_claimed"] is True
    assert [event.event_type for event in store.list_events(sid)] == [
        "test.canonical_terminal", "settlement.claimed"
    ]
    blocked_plan = web_app.api_generate_joint_plan(
        web_app.JointPlanGenerateRequest(session_id=sid)
    )
    blocked_body = json.loads(blocked_plan.body)
    assert blocked_plan.status_code == 409
    assert blocked_body["status"] == "settled"
    assert store.list_joint_plan_runtimes(sid) == []
    second = web_app.api_settle_world_run(sid, web_app.SettlementRequest(expected_version=1))
    assert second["settlement"]["status"] == "settled"
    assert second["event_id"] == ""
    assert len(store.list_events(sid)) == 2
    assert store.get_state(sid).version == 2
    backup = store.export_session(sid)
    imported_sid = store.import_session(backup)
    imported = web_app.api_world_run_settlement(imported_sid)
    assert imported["settlement"]["status"] == "settled"
    assert imported["settlement"]["reward_points"] == 100
    assert replay_events(web_app.build_canonical_start_state(), store.list_events(sid)).dict() == store.get_state(sid).dict()


def test_settlement_rejects_client_reward_and_stale_version(tmp_path, monkeypatch):
    store = SQLiteWorldStore(tmp_path / "settlement.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    started = web_app.api_start(
        web_app.StartRequest(package_id=web_app.CANONICAL_CH1_PACKAGE_ID)
    )
    sid = started["session_id"]
    _make_canonical_terminal(store, sid)
    stale = web_app.api_settle_world_run(
        sid,
        web_app.SettlementRequest(expected_version=0, ending_id="bad", request_id="x"),
    )
    assert stale.status_code == 422
    assert store.get_state(sid).version == 1
    assert not any(event.event_type == "settlement.claimed" for event in store.list_events(sid))


def test_creator_validation_returns_all_reference_errors(
    tmp_path, monkeypatch
):
    package_store = _creator_package_store(tmp_path)
    monkeypatch.setattr(web_app, "PACKAGES", package_store)
    package = package_store.get("huarong_lane").payload()
    package["package_id"] = "broken_package"
    package["snapshot"]["current_scene_id"] = "loc_missing"
    package["snapshot"]["relations"][0]["target_id"] = "char_missing"

    response = web_app.api_creator_validate(
        web_app.PackageDraftRequest(package=package)
    )
    payload = json.loads(response.body)

    assert response.status_code == 422
    assert payload["status"] == "invalid"
    assert len(payload["errors"]) >= 2
