"""FastAPI 会话流程与持久化存储的轻量集成测试。"""

import asyncio
import importlib
import json

from engine import (
    ActionStep,
    ActorActionChain,
    JointPlan,
    SQLiteWorldStore,
    ToolCall,
    TurnResult,
    WorldPackageStore,
    commit_event,
    create_plan_runtime,
)
from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import NIGHT, QINGQING
from world_schema import Operation, OperationKind, StatePatch


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


class _RejectingPipeline:
    def run(self, *_args, **_kwargs):
        return TurnResult(
            status="rejected",
            error="WORLD_CONCEPT_UNAVAILABLE",
        )


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
    assert executed["state"]["version"] == 1
    assert executed["events"][0]["event_type"] == "tool.talk_to"
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
    assert (
        session_store.get_metadata(started["session_id"]).world_package_id
        == draft["package_id"]
    )


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
