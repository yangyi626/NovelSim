"""跨章节 progression 持久化与幂等转场测试。"""

import pytest

from engine import (
    PersistenceError,
    SQLiteWorldStore,
    commit_event,
)
from engine.chapter_progression import (
    TransitionRequest,
    UnlockGrant,
)
from examples.huarong_lane.canonical_case import build_canonical_start_state
from examples.huarong_lane.scenario import NIGHT
from world_schema import Operation, OperationKind, StatePatch


def _make_store(tmp_path):
    return SQLiteWorldStore(tmp_path / "progression.sqlite3")


def _make_session(store, *, package_id="pkg_ch1"):
    state = build_canonical_start_state()
    return store.create_session(
        state,
        default_actor_id=NIGHT,
        world_package_id=package_id,
    )


def _commit_settlement(store, sid, *, version=None):
    state = store.get_state(sid)
    event, new_state = commit_event(
        state,
        action_id="settle",
        event_type="settlement.claimed",
        patch=StatePatch(
            operations=[
                Operation(
                    op=OperationKind.set_flag,
                    path="settlement.status",
                    value="settled",
                )
            ]
        ),
        actor_ids=[NIGHT],
        expected_version=state.version,
    )
    store.commit_turn(
        sid,
        expected_version=state.version,
        new_state=new_state,
        event=event,
    )
    return event.event_id, new_state.version


def _record_receipt(store, sid, event_id, version, *, key="k-default"):
    return store.record_settlement_progression(
        sid,
        settlement_event_id=event_id,
        settled_world_version=version,
        ending_id="ending_a",
        ending_title="第一章终点",
        summary="完成第一章。",
        reward_points=150,
        idempotency_key=key,
        unlocks=[UnlockGrant(unlock_key="world:pkg_ch2")],
    )


def _child_snapshot(version=0):
    state = build_canonical_start_state()
    state.version = version
    return state


def _transition_request(sid, *, key="t-key-000001", child_id=None):
    import uuid
    from world_schema import WorldEvent

    child_state = _child_snapshot()
    child_state.timeline_id = f"timeline_{uuid.uuid4().hex[:16]}"
    genesis, child_state = commit_event(
        child_state,
        action_id="inherit",
        event_type="chapter.inherited",
        patch=StatePatch(
            operations=[
                Operation(
                    op=OperationKind.set_flag,
                    path="canonical.hall_summons_issued",
                    value=True,
                )
            ]
        ),
        actor_ids=[NIGHT],
        expected_version=0,
    )
    assert child_state.version == 1
    if child_id:
        genesis = genesis.copy(update={"event_id": f"{genesis.event_id}_{child_id}"})
    return TransitionRequest(
        parent_session_id=sid,
        target_world_package_id="pkg_ch2",
        child_state=child_state,
        genesis_event=genesis,
        manifest={"policy_version": 1, "entries": []},
        default_actor_id=NIGHT,
        idempotency_key=key,
        save_name="第二章世界线",
    )


def test_legacy_session_backfills_as_self_rooted_campaign(tmp_path):
    store = _make_store(tmp_path)
    sid = _make_session(store)

    lineage = store.ensure_session_lineage(sid)
    again = store.get_session_lineage(sid)

    assert lineage.campaign_id == f"campaign_{sid}"
    assert lineage.root_session_id == sid
    assert lineage.parent_session_id is None
    assert lineage.depth == 0
    assert again.session_id == sid
    assert again.campaign_id == lineage.campaign_id


def test_record_settlement_progression_is_idempotent(tmp_path):
    store = _make_store(tmp_path)
    sid = _make_session(store)
    event_id, version = _commit_settlement(store, sid)

    first = _record_receipt(store, sid, event_id, version)
    repeat = _record_receipt(store, sid, event_id, version, key="k-other")

    assert first.settlement_id == repeat.settlement_id
    assert first.reward_points == 150
    progression = store.list_campaign_progression(first.campaign_id)
    assert [item.unlock_key for item in progression.unlocks] == [
        "world:pkg_ch2"
    ]
    assert len(progression.rewards) == 1


def test_record_settlement_rejects_uncommitted_event_and_bad_key(tmp_path):
    store = _make_store(tmp_path)
    sid = _make_session(store)
    with pytest.raises(PersistenceError, match="结算幂等键不能为空"):
        _record_receipt(store, sid, "missing", 1, key="")
    with pytest.raises(PersistenceError, match="结算事件尚未提交"):
        _record_receipt(store, sid, "missing-event", 3)


def test_create_or_get_child_requires_authoritative_settlement(tmp_path):
    store = _make_store(tmp_path)
    sid = _make_session(store)
    with pytest.raises(PersistenceError, match="尚未记录权威结算"):
        store.create_or_get_child_session(_transition_request(sid))


def test_double_transition_returns_the_same_child(tmp_path):
    store = _make_store(tmp_path)
    sid = _make_session(store)
    event_id, version = _commit_settlement(store, sid)
    _record_receipt(store, sid, event_id, version)

    request = _transition_request(sid)
    first = store.create_or_get_child_session(request)
    second_by_other_key = store.create_or_get_child_session(
        _transition_request(sid, key="t-key-other-02")
    )

    assert first.created is True
    assert second_by_other_key.created is False
    assert (
        second_by_other_key.child_session_id == first.child_session_id
    )
    child_state = store.get_state(first.child_session_id)
    child_events = store.list_events(first.child_session_id)
    child_turns = store.list_turns(first.child_session_id)
    child_lineage = store.get_session_lineage(first.child_session_id)

    assert child_state.version == 1
    assert child_state.flags["canonical.hall_summons_issued"] is True
    assert child_state.timeline_id != "timeline_child_base"
    assert [event.event_type for event in child_events] == [
        "chapter.inherited"
    ]
    assert child_turns == []
    assert child_lineage.campaign_id.startswith("campaign_")
    assert child_lineage.parent_session_id == sid
    assert child_lineage.depth == 1

    progression = store.list_campaign_progression(child_lineage.campaign_id)
    assert {item.session_id for item in progression.lineage} >= {
        sid,
        first.child_session_id,
    }
    assert len(progression.transitions) == 1


# ---------------------------------------------------------------------------
# Web API 级跨章节黄金路径
# ---------------------------------------------------------------------------

import importlib
import json

from world_schema import WorldEvent  # noqa: E402


web_app = importlib.import_module("web.app")


def _drive_canonical_terminal(store, sid):
    from examples.huarong_lane.scenario import NIGHT as NIGHT_ID

    state = store.get_state(sid)
    event, new_state = commit_event(
        state,
        action_id="canonical-terminal",
        event_type="tool.move_to",
        patch=StatePatch(
            operations=[
                Operation(
                    op=OperationKind.set_flag,
                    path="canonical.hall_summons_issued",
                    value=True,
                ),
                Operation(
                    op=OperationKind.move_character,
                    target_id=NIGHT_ID,
                    location_id="loc_ye_clan_hall",
                ),
            ]
        ),
        actor_ids=[NIGHT_ID],
        expected_version=state.version,
    )
    store.commit_turn(
        sid,
        expected_version=state.version,
        new_state=new_state,
        event=event,
    )


def _post_transition(sid, *, key):
    return web_app.api_create_chapter_transition(
        sid,
        web_app.ChapterTransitionRequest(
            target_package_id=web_app.CANONICAL_CH5_PACKAGE_ID,
            idempotency_key=key,
        ),
    )


def test_web_settlement_unlocks_and_transitions_to_next_chapter(tmp_path):
    store = SQLiteWorldStore(tmp_path / "web-transition.sqlite3")
    original_store = web_app.SESSIONS
    web_app.SESSIONS = store
    try:
        started = web_app.api_start(
            web_app.StartRequest(package_id=web_app.CANONICAL_CH1_PACKAGE_ID)
        )
        parent_sid = started["session_id"]
        _drive_canonical_terminal(store, parent_sid)

        settled = web_app.api_settle_world_run(
            parent_sid,
            web_app.SettlementRequest(expected_version=1),
        )
        assert settled["settlement"]["status"] == "settled"
        next_chapter = settled["settlement"]["next_chapter"]
        assert next_chapter["status"] == "unlocked"
        assert next_chapter["package_id"] == (
            web_app.CANONICAL_CH5_PACKAGE_ID
        )

        first = _post_transition(parent_sid, key="e2e-key-first-001")
        assert first["status"] == "ok"
        assert first["transition"]["created"] is True
        child_sid = first["transition"]["child_session_id"]
        assert first["dashboard"]["session_id"] == child_sid
        inherited_paths = [
            entry["path"]
            for entry in first["inheritance_entries"]
            if entry["applied"]
        ]
        assert "canonical.hall_summons_issued" in inherited_paths

        repeat = _post_transition(parent_sid, key="e2e-key-second-002")
        assert repeat["status"] == "ok"
        assert repeat["transition"]["created"] is False
        assert repeat["transition"]["child_session_id"] == child_sid

        child_state = store.get_state(child_sid)
        assert child_state.version == 1
        assert child_state.world_package_id if hasattr(
            child_state, "world_package_id"
        ) else True
        events = store.list_events(child_sid)
        assert [event.event_type for event in events] == ["chapter.inherited"]
        assert child_state.flags["canonical.hall_summons_issued"] is True
        assert child_state.flags["inheritance.prev_reward_points"] >= 100
        assert store.list_turns(child_sid) == []
        metadata = store.get_metadata(child_sid)
        assert metadata.world_package_id == web_app.CANONICAL_CH5_PACKAGE_ID

        lineage_response = web_app.api_world_run_lineage(parent_sid)
        chain = lineage_response["chain"]
        assert [item["depth"] for item in chain] == [0, 1]
        assert chain[0]["settled"] is True

        # 已结算父世界线不能再跑普通回合。
        blocked_turn = web_app.api_turn(
            web_app.TurnRequest(session_id=parent_sid, text="我再看看")
        )
        assert blocked_turn.status_code == 409
        body = json.loads(blocked_turn.body)
        assert body["status"] == "settled"

        # 子世界线可以正常行动，不在结算终点上。
        child_view = web_app.api_world_run_settlement(child_sid)
        assert child_view["settlement"]["status"] == "unavailable"
    finally:
        web_app.SESSIONS = original_store


def test_web_transition_requires_settled_parent(tmp_path):
    store = SQLiteWorldStore(tmp_path / "web-unsettled.sqlite3")
    original_store = web_app.SESSIONS
    web_app.SESSIONS = store
    try:
        started = web_app.api_start(
            web_app.StartRequest(package_id=web_app.CANONICAL_CH1_PACKAGE_ID)
        )
        sid = started["session_id"]
        premature = _post_transition(sid, key="unsettled-key-00")
        assert premature.status_code == 422

        _drive_canonical_terminal(store, sid)
        response = _post_transition(sid, key="unsettled-key-01")
        assert response.status_code == 409
        body = json.loads(response.body)
        assert body["status"] == "settlement_required"
    finally:
        web_app.SESSIONS = original_store
