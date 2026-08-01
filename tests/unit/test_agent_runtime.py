"""AgentExecutionStateMachine 的闭环、恢复与 Trace 测试。"""

import asyncio

from pydantic import BaseModel

from examples.secret_letter import (
    ALLY,
    FACT_PLOT,
    GOAL_PROTECT,
    GUARD,
    LETTER,
    STEWARD,
    build_snapshot,
)
from engine import (
    CORE_TOOL_PERMISSIONS,
    AgentExecutionState,
    AgentExecutionStateMachine,
    AgentExecutionStatus,
    SQLiteWorldStore,
    ToolCall,
    ToolCandidate,
    ToolDefinition,
    ToolFailureCode,
    ToolRegistry,
    TraceSpanStatus,
    build_game_observation,
    commit_event,
    create_core_tool_registry,
    replay_events,
)
from world_schema import (
    Belief,
    Character,
    CharacterBelief,
    Item,
    Location,
    Operation,
    OperationKind,
    StatePatch,
    WorldState,
)


ACTOR = "guard"
TARGET = "steward"
ITEM = "sealed_letter"
FACT = "fact_secret_letter"


def _world(*, target_location="hall") -> WorldState:
    return WorldState(
        timeline_id="runtime_test",
        characters={
            ACTOR: Character(
                character_id=ACTOR,
                display_name="守卫",
                location_id="gate",
            ),
            TARGET: Character(
                character_id=TARGET,
                display_name="管家",
                location_id=target_location,
            ),
        },
        locations={
            "gate": Location(location_id="gate", display_name="侧门"),
            "hall": Location(location_id="hall", display_name="正厅"),
        },
        items={
            ITEM: Item(
                item_id=ITEM,
                display_name="密信",
                location_id="gate",
            )
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
    )


def _run(machine, call, state, **kwargs):
    return asyncio.run(machine.execute(call, state, **kwargs))


def test_four_core_tools_complete_persisted_backend_loop(tmp_path):
    state = _world()
    store = SQLiteWorldStore(tmp_path / "agent_tools.sqlite3")
    session_id = store.create_session(
        state,
        default_actor_id=ACTOR,
        world_package_id="agent_runtime_test",
    )
    machine = AgentExecutionStateMachine(create_core_tool_registry())
    calls = [
        ToolCall(
            actor_id=ACTOR,
            tool_name="pick_up",
            arguments={"item_id": ITEM},
        ),
        ToolCall(
            actor_id=ACTOR,
            tool_name="move_to",
            arguments={"destination_id": TARGET},
        ),
        ToolCall(
            actor_id=ACTOR,
            tool_name="talk_to",
            arguments={
                "target_character_id": TARGET,
                "message": "我在侧门发现了这封密信。",
                "tone": "谨慎",
            },
        ),
        ToolCall(
            actor_id=ACTOR,
            tool_name="share_information",
            arguments={
                "target_character_id": TARGET,
                "fact_id": FACT,
            },
        ),
    ]

    outcomes = []
    for call in calls:
        outcome = _run(
            machine,
            call,
            state,
            permissions=CORE_TOOL_PERMISSIONS,
            store=store,
            session_id=session_id,
            metadata={"decision_source": "deterministic_test"},
        )
        assert outcome.result.success is True
        state = outcome.new_state
        outcomes.append(outcome)

    restored = store.get_state(session_id)
    assert restored is not None
    assert restored.version == 4
    assert restored.characters[ACTOR].location_id == "hall"
    assert restored.items[ITEM].owner_id == ACTOR
    assert restored.beliefs[TARGET][0].fact_id == FACT
    assert [event.event_type for event in store.list_events(session_id)] == [
        "tool.pick_up",
        "tool.move_to",
        "tool.talk_to",
        "tool.share_information",
    ]
    assert len(store.list_turns(session_id)) == 4
    assert all(item.trace.outcome == "succeeded" for item in outcomes)


def test_success_trace_contains_complete_state_machine_chain():
    machine = AgentExecutionStateMachine(create_core_tool_registry())
    outcome = _run(
        machine,
        ToolCall(
            actor_id=ACTOR,
            tool_name="move_to",
            arguments={"destination_id": "hall"},
        ),
        _world(),
        permissions=CORE_TOOL_PERMISSIONS,
        metadata={"memory_ids": ["memory_1"]},
    )

    assert outcome.execution.status == AgentExecutionStatus.succeeded
    assert outcome.execution.state_history == [
        AgentExecutionState.idle,
        AgentExecutionState.perceive,
        AgentExecutionState.retrieve_memory,
        AgentExecutionState.decide,
        AgentExecutionState.validate_tool,
        AgentExecutionState.navigate,
        AgentExecutionState.execute_tool,
        AgentExecutionState.observe_result,
        AgentExecutionState.reflect,
        AgentExecutionState.idle,
    ]
    assert [span.state for span in outcome.trace.spans] == [
        state.value for state in outcome.execution.state_history
    ]
    assert all(span.ended_at for span in outcome.trace.spans)
    assert all(span.duration_ms >= 0 for span in outcome.trace.spans)
    assert all(span.status == TraceSpanStatus.ok for span in outcome.trace.spans)
    memory_span = next(
        span
        for span in outcome.trace.spans
        if span.state == AgentExecutionState.retrieve_memory.value
    )
    assert memory_span.details["memory_ids"] == ["memory_1"]


def test_authoritative_tool_effects_advance_cross_actor_plans_atomically():
    initial = build_snapshot()
    state = initial
    events = []
    registry = create_core_tool_registry()
    machine = AgentExecutionStateMachine(registry)

    calls = [
        ToolCall(
            actor_id=GUARD,
            tool_name="pick_up",
            arguments={"item_id": LETTER},
        ),
        ToolCall(
            actor_id=GUARD,
            tool_name="observe",
            arguments={"fact_id": FACT_PLOT},
        ),
        ToolCall(
            actor_id=GUARD,
            tool_name="share_information",
            arguments={
                "target_character_id": STEWARD,
                "fact_id": FACT_PLOT,
            },
        ),
        ToolCall(
            actor_id=STEWARD,
            tool_name="share_information",
            arguments={
                "target_character_id": ALLY,
                "fact_id": FACT_PLOT,
            },
        ),
        ToolCall(
            actor_id=STEWARD,
            tool_name="propose_alliance",
            arguments={
                "target_character_id": ALLY,
                "goal_key": GOAL_PROTECT,
                "shared_fact_id": FACT_PLOT,
            },
        ),
    ]

    for index, call in enumerate(calls):
        outcome = _run(
            machine,
            call,
            state,
            permissions=CORE_TOOL_PERMISSIONS,
        )
        assert outcome.result.success is True
        assert outcome.event is not None
        assert any(
            operation.op == OperationKind.advance_plan
            for operation in outcome.event.patch.operations
        )
        state = outcome.new_state
        events.append(outcome.event)

        if index == 0:
            guard_plan = state.character_psyches[GUARD].plans[0]
            assert guard_plan.current_step == 1
            observation = build_game_observation(
                state,
                GUARD,
                registry,
                world_package_id="secret_letter_test",
                scenario_family="secret_transport",
            )
            assert observation.plans[0].current_step_text == "核验内容"
        elif index == 2:
            assert state.character_psyches[GUARD].plans[0].status == "completed"
            assert state.character_psyches[STEWARD].plans[0].current_step == 1

    steward_plan = state.character_psyches[STEWARD].plans[0]
    assert steward_plan.current_step == 3
    assert steward_plan.status == "completed"
    assert replay_events(initial, events).dict() == state.dict()


def test_failed_or_irrelevant_tools_do_not_advance_plan():
    initial = build_snapshot()
    machine = AgentExecutionStateMachine(create_core_tool_registry())

    failed = _run(
        machine,
        ToolCall(
            actor_id=GUARD,
            tool_name="pick_up",
            arguments={"item_id": "missing_letter"},
        ),
        initial,
        permissions=CORE_TOOL_PERMISSIONS,
    )
    assert failed.result.success is False
    assert failed.new_state.character_psyches[GUARD].plans[0].current_step == 0

    irrelevant = _run(
        machine,
        ToolCall(
            actor_id=GUARD,
            tool_name="talk_to",
            arguments={
                "target_character_id": STEWARD,
                "message": "继续值守。",
                "tone": "平静",
            },
        ),
        initial,
        permissions=CORE_TOOL_PERMISSIONS,
    )
    assert irrelevant.result.success is True
    assert irrelevant.event.patch.operations == []
    assert irrelevant.new_state.character_psyches[GUARD].plans[0].current_step == 0
    assert irrelevant.new_state.character_psyches[STEWARD].plans[0].current_step == 0


class _NoArguments(BaseModel):
    class Config:
        extra = "forbid"


def test_timeout_is_retried_once_then_returns_structured_failure():
    attempts = {"count": 0}

    async def slow_handler(context, arguments):
        attempts["count"] += 1
        await asyncio.sleep(0.05)
        return ToolCandidate()

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="slow_tool",
            description="A timeout test tool.",
            arguments_model=_NoArguments,
            handler=slow_handler,
            timeout_seconds=0.001,
        )
    )
    machine = AgentExecutionStateMachine(
        registry,
        max_retries=1,
        max_replans=0,
    )
    outcome = _run(
        machine,
        ToolCall(actor_id=ACTOR, tool_name="slow_tool", arguments={}),
        _world(),
    )

    assert attempts["count"] == 2
    assert outcome.result.success is False
    assert outcome.result.failure.code == ToolFailureCode.timeout
    assert outcome.result.retry_count == 1
    assert outcome.event is None
    assert outcome.new_state.version == 0
    assert outcome.execution.state_history.count(
        AgentExecutionState.recover
    ) == 2
    assert any(
        span.status == TraceSpanStatus.error
        for span in outcome.trace.spans
    )


def test_retry_recovers_from_transient_handler_error():
    attempts = {"count": 0}

    async def flaky_handler(context, arguments):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("temporary navigation service failure")
        return ToolCandidate(
            patch=StatePatch(
                operations=[
                    Operation(
                        op=OperationKind.set_flag,
                        path="test.flaky_recovered",
                        value=True,
                    )
                ]
            ),
            summary="flaky tool recovered",
        )

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="flaky_tool",
            description="A retry test tool.",
            arguments_model=_NoArguments,
            handler=flaky_handler,
            allowed_patch_operations=frozenset(
                {OperationKind.set_flag}
            ),
        )
    )
    outcome = _run(
        AgentExecutionStateMachine(registry, max_retries=1),
        ToolCall(actor_id=ACTOR, tool_name="flaky_tool", arguments={}),
        _world(),
    )

    assert attempts["count"] == 2
    assert outcome.result.success is True
    assert outcome.result.retry_count == 1
    assert outcome.new_state.flags["test.flaky_recovered"] is True


def test_replan_callback_can_replace_failed_tool_call():
    machine = AgentExecutionStateMachine(
        create_core_tool_registry(),
        max_retries=0,
        max_replans=1,
    )
    initial = ToolCall(
        actor_id=ACTOR,
        tool_name="move_to",
        arguments={"destination_id": "gate"},
    )

    def replan(failure, execution, state):
        assert failure.code == ToolFailureCode.precondition_failed
        return ToolCall(
            actor_id=ACTOR,
            tool_name="move_to",
            arguments={"destination_id": "hall"},
        )

    outcome = _run(
        machine,
        initial,
        _world(),
        permissions=CORE_TOOL_PERMISSIONS,
        replan=replan,
    )

    assert outcome.result.success is True
    assert outcome.execution.replan_count == 1
    assert outcome.new_state.characters[ACTOR].location_id == "hall"
    assert len(outcome.trace.call_ids) == 2
    assert outcome.trace.call_ids[0] == initial.call_id


def test_handler_cannot_mutate_authoritative_state_on_failure():
    def mutating_handler(context, arguments):
        context.state.flags["illegal_mutation"] = True
        raise RuntimeError("handler crashed after mutating its copy")

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="mutating_tool",
            description="A snapshot isolation test tool.",
            arguments_model=_NoArguments,
            handler=mutating_handler,
        )
    )
    original = _world()
    outcome = _run(
        AgentExecutionStateMachine(
            registry,
            max_retries=0,
            max_replans=0,
        ),
        ToolCall(actor_id=ACTOR, tool_name="mutating_tool", arguments={}),
        original,
    )

    assert outcome.result.success is False
    assert "illegal_mutation" not in original.flags
    assert "illegal_mutation" not in outcome.new_state.flags
    assert outcome.event is None


def test_invalid_candidate_patch_is_rejected_without_commit():
    def invalid_patch_handler(context, arguments):
        return ToolCandidate(
            patch=StatePatch(
                operations=[
                    Operation(
                        op=OperationKind.move_character,
                        target_id="invented_character",
                        location_id="hall",
                    )
                ]
            )
        )

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="invalid_patch_tool",
            description="A patch validation test tool.",
            arguments_model=_NoArguments,
            handler=invalid_patch_handler,
            allowed_patch_operations=frozenset(
                {OperationKind.move_character}
            ),
        )
    )
    outcome = _run(
        AgentExecutionStateMachine(registry, max_retries=0),
        ToolCall(
            actor_id=ACTOR,
            tool_name="invalid_patch_tool",
            arguments={},
        ),
        _world(),
    )

    assert outcome.result.success is False
    assert outcome.result.failure.code == ToolFailureCode.patch_rejected
    assert outcome.result.candidate_patch is not None
    assert outcome.event is None
    assert outcome.new_state.version == 0


def test_stale_persistent_tool_commit_requests_replan_without_overwrite(
    tmp_path,
):
    stale_state = _world()
    store = SQLiteWorldStore(tmp_path / "conflict.sqlite3")
    session_id = store.create_session(
        stale_state,
        default_actor_id=ACTOR,
        world_package_id="runtime_conflict",
    )
    external_event, external_state = commit_event(
        stale_state,
        action_id="external_action",
        event_type="external",
        patch=StatePatch(
            operations=[
                Operation(
                    op=OperationKind.set_flag,
                    path="external.committed",
                    value=True,
                )
            ]
        ),
        expected_version=0,
    )
    store.commit_turn(
        session_id,
        expected_version=0,
        new_state=external_state,
        event=external_event,
    )

    outcome = _run(
        AgentExecutionStateMachine(create_core_tool_registry()),
        ToolCall(
            actor_id=ACTOR,
            tool_name="move_to",
            arguments={"destination_id": "hall"},
        ),
        stale_state,
        permissions=CORE_TOOL_PERMISSIONS,
        store=store,
        session_id=session_id,
    )

    restored = store.get_state(session_id)
    assert outcome.result.success is False
    assert outcome.result.failure.code == ToolFailureCode.version_conflict
    assert outcome.result.replan_required is True
    assert outcome.event is None
    assert outcome.new_state.version == 0
    assert restored.version == 1
    assert restored.flags["external.committed"] is True
    assert restored.characters[ACTOR].location_id == "gate"
