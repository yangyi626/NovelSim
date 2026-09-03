"""旧章节快照的权威事实补录测试。"""

import pytest

from engine import commit_event, migrate_world_facts
from engine.event import CommitError
from engine.patch import apply_patch, PatchError
from engine.patch_validator import validate_action_patch
from examples.huarong_lane.canonical_ch6_10 import build_canonical_ch5_start_state
from examples.huarong_lane.scenario import NIGHT
from world_schema import (
    Action,
    Actor,
    CausalEvidence,
    Operation,
    OperationKind,
    StatePatch,
)
from world_schema.models import ActionType
from engine.persistence import SQLiteWorldStore


def _legacy_state():
    state = build_canonical_ch5_start_state()
    state.facts = {}
    return state


def test_add_fact_applies_only_new_authoritative_fact():
    state = _legacy_state()
    source = build_canonical_ch5_start_state().facts["fact_qingqing_poisoned_tea"]
    updated = apply_patch(
        state,
        StatePatch(
            operations=[
                Operation(
                    op=OperationKind.add_fact,
                    path=source.fact_id,
                    fact_id=source.fact_id,
                    value=source.dict(),
                )
            ]
        ),
    )
    assert updated.facts[source.fact_id] == source
    with pytest.raises(PatchError, match="duplicate fact"):
        apply_patch(
            updated,
            StatePatch(
                operations=[
                    Operation(
                        op=OperationKind.add_fact,
                        path=source.fact_id,
                        fact_id=source.fact_id,
                        value=source.dict(),
                    )
                ]
            ),
        )


def test_add_fact_commit_requires_system_migration_authority():
    state = _legacy_state()
    source = build_canonical_ch5_start_state().facts["fact_qingqing_poisoned_tea"]
    patch = StatePatch(
        operations=[
            Operation(
                op=OperationKind.add_fact,
                path=source.fact_id,
                fact_id=source.fact_id,
                value=source.dict(),
            )
        ],
        causal_evidence={"authority": "player_action"},
    )
    with pytest.raises(CommitError, match="system_migration"):
        commit_event(state, "bad", "facts.migrated", patch, expected_version=0)


def test_ordinary_action_cannot_inject_fact(snapshot):
    action = Action(
        action_id="speak_inject_fact",
        action_type=ActionType.speak,
        actor=Actor(actor_id=NIGHT),
        parameters={"message": "凭空登记事实"},
    )
    source = build_canonical_ch5_start_state().facts["fact_qingqing_poisoned_tea"]
    result = validate_action_patch(
        snapshot,
        action,
        StatePatch(
            operations=[
                Operation(
                    op=OperationKind.add_fact,
                    path=source.fact_id,
                    fact_id=source.fact_id,
                    value=source.dict(),
                )
            ],
            causal_evidence=CausalEvidence(
                action_id=action.action_id,
                actor_id=NIGHT,
                authority="player_action",
            ),
        ),
    )
    assert not result.valid
    assert "system_only_operation" in result.why()


def test_legacy_fact_migration_is_audited_and_idempotent(tmp_path):
    store = SQLiteWorldStore(tmp_path / "world.sqlite3")
    state = _legacy_state()
    session_id = store.create_session(
        state,
        default_actor_id=NIGHT,
        world_package_id="first_crazy_ch5_checkpoint",
    )
    source_facts = build_canonical_ch5_start_state().facts
    facts = {
        fact_id: source_facts[fact_id]
        for fact_id in (
            "fact_qingqing_poisoned_tea",
            "fact_self_framing_sister",
            "fact_self_in_huarong_lane",
        )
    }

    first = migrate_world_facts(
        store,
        session_id,
        facts,
        migration_id="canonical_ch6_10_facts_v1",
    )
    second = migrate_world_facts(
        store,
        session_id,
        facts,
        migration_id="canonical_ch6_10_facts_v1",
    )

    repaired = store.get_state(session_id)
    events = store.list_events(session_id)
    assert first is not None
    assert second is not None
    assert second.event_id == first.event_id
    assert repaired is not None
    assert set(repaired.facts) == set(facts)
    assert repaired.version == 1
    assert len(events) == 1
    assert events[0].event_type == "facts.migrated"
    assert events[0].patch.causal_evidence.authority == "system_migration"
    assert store.list_turns(session_id)[0].result["status"] == "system_migration"
