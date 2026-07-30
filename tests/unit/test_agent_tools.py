"""ToolRegistry 和四个核心工具的协议/边界测试。"""

import asyncio

import pytest

from engine import (
    CORE_TOOL_PERMISSIONS,
    AgentExecutionStateMachine,
    ToolCall,
    ToolFailureCode,
    create_core_tool_registry,
)
from world_schema import (
    Belief,
    Character,
    CharacterBelief,
    EntityAffordance,
    Item,
    Location,
    WorldFact,
    WorldState,
)


ACTOR = "guard"
TARGET = "steward"
DISTANT = "archivist"
FACT = "fact_secret_letter"
OBSERVABLE_FACT = "fact_seal_is_broken"
UNKNOWN_FACT = "fact_actor_does_not_know"
ITEM = "sealed_letter"


def _world() -> WorldState:
    return WorldState(
        timeline_id="tool_test",
        characters={
            ACTOR: Character(
                character_id=ACTOR,
                display_name="守卫",
                location_id="gate",
            ),
            TARGET: Character(
                character_id=TARGET,
                display_name="管家",
                location_id="gate",
            ),
            DISTANT: Character(
                character_id=DISTANT,
                display_name="档案员",
                location_id="hall",
            ),
        },
        locations={
            "gate": Location(location_id="gate", display_name="侧门"),
            "hall": Location(location_id="hall", display_name="正厅"),
            "vault": Location(
                location_id="vault",
                display_name="密库",
                requires_permission=["trusted"],
            ),
        },
        items={
            ITEM: Item(
                item_id=ITEM,
                display_name="密信",
                location_id="gate",
            ),
            "owned_key": Item(
                item_id="owned_key",
                display_name="钥匙",
                owner_id=TARGET,
            ),
        },
        beliefs={
            ACTOR: [
                CharacterBelief(
                    fact_id=FACT,
                    belief=Belief.believed_true,
                    confidence=0.9,
                    source_type="observation",
                )
            ]
        },
        facts={
            FACT: WorldFact(
                fact_id=FACT,
                statement="密信记录了一项阴谋。",
            ),
            OBSERVABLE_FACT: WorldFact(
                fact_id=OBSERVABLE_FACT,
                statement="信封封印已经破损。",
                item_id=ITEM,
                observable=True,
                base_confidence=1.0,
            ),
            UNKNOWN_FACT: WorldFact(
                fact_id=UNKNOWN_FACT,
                statement="只有管家知道密库口令。",
            ),
        },
        entity_affordances={
            ITEM: [
                EntityAffordance(
                    affordance_id="destroy_test_letter",
                    entity_id=ITEM,
                    action_type="destroy_item",
                )
            ]
        },
    )


def _execute(call: ToolCall, state=None):
    machine = AgentExecutionStateMachine(create_core_tool_registry())
    return asyncio.run(
        machine.execute(
            call,
            state or _world(),
            permissions=CORE_TOOL_PERMISSIONS,
        )
    )


def test_registry_exports_strict_function_tool_json_schemas():
    registry = create_core_tool_registry()

    assert registry.names() == [
        "destroy_item",
        "give_item",
        "move_to",
        "observe",
        "pick_up",
        "propose_alliance",
        "share_information",
        "talk_to",
    ]
    exported = registry.function_tools()
    assert [item["function"]["name"] for item in exported] == registry.names()
    for item in exported:
        function = item["function"]
        schema = function["parameters"]
        assert item["type"] == "function"
        assert function["strict"] is True
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


@pytest.mark.parametrize(
    "call",
    [
        ToolCall(
            actor_id=ACTOR,
            tool_name="move_to",
            arguments={"destination_id": "hall", "invented": True},
        ),
        ToolCall(
            actor_id=ACTOR,
            tool_name="talk_to",
            arguments={"target_character_id": TARGET, "message": "", "tone": "平静"},
        ),
        ToolCall(
            actor_id=ACTOR,
            tool_name="pick_up",
            arguments={},
        ),
        ToolCall(
            actor_id=ACTOR,
            tool_name="share_information",
            arguments={"target_character_id": TARGET},
        ),
        ToolCall(
            actor_id=ACTOR,
            tool_name="observe",
            arguments={},
        ),
        ToolCall(
            actor_id=ACTOR,
            tool_name="give_item",
            arguments={"item_id": ITEM},
        ),
        ToolCall(
            actor_id=ACTOR,
            tool_name="propose_alliance",
            arguments={"target_character_id": TARGET},
        ),
        ToolCall(
            actor_id=ACTOR,
            tool_name="destroy_item",
            arguments={},
        ),
    ],
)
def test_each_core_tool_rejects_invalid_arguments(call):
    outcome = _execute(call)

    assert outcome.result.success is False
    assert outcome.result.failure.code == ToolFailureCode.invalid_arguments
    assert outcome.event is None
    assert outcome.new_state == _world()


@pytest.mark.parametrize(
    "call",
    [
        ToolCall(
            actor_id=ACTOR,
            tool_name="move_to",
            arguments={"destination_id": "missing_place"},
        ),
        ToolCall(
            actor_id=ACTOR,
            tool_name="talk_to",
            arguments={
                "target_character_id": "missing_character",
                "message": "有人吗？",
                "tone": "试探",
            },
        ),
        ToolCall(
            actor_id=ACTOR,
            tool_name="pick_up",
            arguments={"item_id": "missing_item"},
        ),
        ToolCall(
            actor_id=ACTOR,
            tool_name="share_information",
            arguments={
                "target_character_id": "missing_character",
                "fact_id": FACT,
            },
        ),
        ToolCall(
            actor_id=ACTOR,
            tool_name="observe",
            arguments={"fact_id": "missing_fact"},
        ),
        ToolCall(
            actor_id=ACTOR,
            tool_name="give_item",
            arguments={
                "item_id": "missing_item",
                "target_character_id": TARGET,
            },
        ),
        ToolCall(
            actor_id=ACTOR,
            tool_name="propose_alliance",
            arguments={
                "target_character_id": "missing_character",
                "goal_key": "protect_estate",
                "shared_fact_id": FACT,
            },
        ),
        ToolCall(
            actor_id=ACTOR,
            tool_name="destroy_item",
            arguments={"item_id": "missing_item"},
        ),
    ],
)
def test_each_core_tool_rejects_missing_entities(call):
    outcome = _execute(call)

    assert outcome.result.success is False
    assert outcome.result.failure.code == ToolFailureCode.target_not_found
    assert outcome.event is None
    assert outcome.new_state.version == 0


@pytest.mark.parametrize(
    ("call", "expected_code"),
    [
            (
                ToolCall(
                actor_id=ACTOR,
                tool_name="move_to",
                arguments={"destination_id": "gate"},
            ),
            ToolFailureCode.precondition_failed,
        ),
        (
            ToolCall(
                actor_id=ACTOR,
                tool_name="talk_to",
                arguments={
                    "target_character_id": DISTANT,
                    "message": "请留步。",
                    "tone": "严肃",
                },
            ),
            ToolFailureCode.spatial_constraint,
        ),
        (
            ToolCall(
                actor_id=ACTOR,
                tool_name="pick_up",
                arguments={"item_id": "owned_key"},
            ),
            ToolFailureCode.precondition_failed,
        ),
        (
            ToolCall(
                actor_id=ACTOR,
                tool_name="share_information",
                arguments={
                    "target_character_id": TARGET,
                    "fact_id": UNKNOWN_FACT,
                },
            ),
                ToolFailureCode.cognitive_boundary,
            ),
            (
                ToolCall(
                    actor_id=ACTOR,
                    tool_name="destroy_item",
                    arguments={"item_id": ITEM},
                ),
                ToolFailureCode.precondition_failed,
            ),
    ],
)
def test_each_core_tool_rejects_failed_preconditions(call, expected_code):
    outcome = _execute(call)

    assert outcome.result.success is False
    assert outcome.result.failure.code == expected_code
    assert outcome.event is None
    assert outcome.new_state.version == 0


def test_move_to_success_produces_and_commits_move_patch():
    outcome = _execute(
        ToolCall(
            actor_id=ACTOR,
            tool_name="move_to",
            arguments={"destination_id": "hall"},
        )
    )

    assert outcome.result.success is True
    assert outcome.event.event_type == "tool.move_to"
    assert outcome.new_state.characters[ACTOR].location_id == "hall"
    assert outcome.new_state.version == 1
    assert outcome.result.presentation_events[0].event_type == "navigate"


def test_talk_to_success_commits_dialogue_event_without_world_mutation():
    before = _world()
    outcome = _execute(
        ToolCall(
            actor_id=ACTOR,
            tool_name="talk_to",
            arguments={
                "target_character_id": TARGET,
                "message": "密信的事，我们单独谈。",
                "tone": "低声",
            },
        ),
        before,
    )

    assert outcome.result.success is True
    assert outcome.event.event_type == "tool.talk_to"
    assert outcome.event.patch.operations == []
    assert outcome.new_state.version == 1
    assert before.version == 0
    assert outcome.result.presentation_events[0].event_type == "dialogue"


def test_pick_up_success_transfers_authoritative_ownership():
    outcome = _execute(
        ToolCall(
            actor_id=ACTOR,
            tool_name="pick_up",
            arguments={"item_id": ITEM},
        )
    )

    assert outcome.result.success is True
    assert outcome.new_state.items[ITEM].owner_id == ACTOR
    assert outcome.new_state.items[ITEM].location_id is None
    assert ITEM in outcome.new_state.characters[ACTOR].inventory


def test_observe_success_records_belief_and_evidence():
    outcome = _execute(
        ToolCall(
            actor_id=ACTOR,
            tool_name="observe",
            arguments={"fact_id": OBSERVABLE_FACT},
        )
    )

    assert outcome.result.success is True
    observed = next(
        belief
        for belief in outcome.new_state.beliefs[ACTOR]
        if belief.fact_id == OBSERVABLE_FACT
    )
    assert observed.belief == Belief.believed_true
    assert observed.confidence == 1.0
    assert observed.source_type == "observation"
    assert observed.evidence_event_ids == [outcome.result.call_id]
    assert len(outcome.new_state.belief_evidence) == 1


def test_give_item_success_requires_and_transfers_actor_ownership():
    picked_up = _execute(
        ToolCall(
            actor_id=ACTOR,
            tool_name="pick_up",
            arguments={"item_id": ITEM},
        )
    )
    outcome = _execute(
        ToolCall(
            actor_id=ACTOR,
            tool_name="give_item",
            arguments={
                "item_id": ITEM,
                "target_character_id": TARGET,
            },
        ),
        picked_up.new_state,
    )

    assert outcome.result.success is True
    assert outcome.new_state.items[ITEM].owner_id == TARGET
    assert ITEM not in outcome.new_state.characters[ACTOR].inventory
    assert ITEM in outcome.new_state.characters[TARGET].inventory


def test_destroy_item_success_requires_ownership_and_affordance():
    picked_up = _execute(
        ToolCall(
            actor_id=ACTOR,
            tool_name="pick_up",
            arguments={"item_id": ITEM},
        )
    )
    outcome = _execute(
        ToolCall(
            actor_id=ACTOR,
            tool_name="destroy_item",
            arguments={"item_id": ITEM},
        ),
        picked_up.new_state,
    )

    assert outcome.result.success is True
    destroyed = outcome.new_state.items[ITEM]
    assert destroyed.quantity == 0
    assert destroyed.accessible is False
    assert destroyed.owner_id is None
    assert destroyed.attrs["destroyed"] is True
    assert ITEM not in outcome.new_state.characters[ACTOR].inventory


def test_share_information_success_updates_only_target_belief():
    before = _world()
    outcome = _execute(
        ToolCall(
            actor_id=ACTOR,
            tool_name="share_information",
            arguments={"target_character_id": TARGET, "fact_id": FACT},
        ),
        before,
    )

    assert outcome.result.success is True
    target_belief = outcome.new_state.beliefs[TARGET][0]
    assert target_belief.fact_id == FACT
    assert target_belief.belief == Belief.suspected_true
    assert target_belief.confidence == pytest.approx(0.504)
    assert target_belief.source_type == "hearsay"
    assert target_belief.source_character_id == ACTOR
    assert len(outcome.new_state.belief_evidence) == 1
    assert len(outcome.new_state.propagation_history) == 1
    assert TARGET not in before.beliefs


def test_tool_permission_is_required_before_handler_runs():
    machine = AgentExecutionStateMachine(create_core_tool_registry())
    outcome = asyncio.run(
        machine.execute(
            ToolCall(
                actor_id=ACTOR,
                tool_name="move_to",
                arguments={"destination_id": "hall"},
            ),
            _world(),
            permissions=[],
        )
    )

    assert outcome.result.success is False
    assert outcome.result.failure.code == ToolFailureCode.permission_denied
    assert outcome.new_state.version == 0


def test_location_identity_permission_is_enforced():
    outcome = _execute(
        ToolCall(
            actor_id=ACTOR,
            tool_name="move_to",
            arguments={"destination_id": "vault"},
        )
    )

    assert outcome.result.success is False
    assert outcome.result.failure.code == ToolFailureCode.permission_denied
