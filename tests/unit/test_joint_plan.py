"""Joint action chains, explicit waits, repair and recovery tests."""

import asyncio

import pytest
from pydantic import ValidationError

from engine import (
    CORE_TOOL_PERMISSIONS,
    ActionStep,
    ActorActionChain,
    ChainAdvanceKind,
    JointPlan,
    JointPlanExecutor,
    PlanRuntimeStatus,
    PlanValidityStatus,
    PlannerDecision,
    PlannerFeedback,
    PolicyJointReplanner,
    ReplanRequest,
    SQLiteWorldStore,
    ToolCall,
    WaitAgentStep,
    WaitStateStep,
    build_relevant_diff,
    build_wait_graph,
    check_plan_validity,
    create_core_tool_registry,
    create_plan_runtime,
    find_deadlock,
    reconcile_runtime,
    validate_joint_plan,
)
from examples.secret_letter import (
    ALLY,
    FACT_PLOT,
    GOAL_PROTECT,
    GUARD,
    LETTER,
    PLAYER,
    STEWARD,
    build_snapshot,
)
from world_schema import Belief, CharacterBelief, PlanConditionKind, PlanStepCondition


def _call(actor_id, step_id, tool_name, arguments):
    return ActionStep(
        step_id=step_id,
        tool_call=ToolCall(
            call_id="call_%s" % step_id,
            actor_id=actor_id,
            tool_name=tool_name,
            arguments=arguments,
        ),
    )


def _cooperation_plan(state=None):
    state = state or build_snapshot()
    return JointPlan(
        plan_id="protect_estate_joint_v1",
        goal_id=GOAL_PROTECT,
        base_world_version=state.version,
        actor_chains={
            GUARD: ActorActionChain(
                actor_id=GUARD,
                steps=[
                    _call(GUARD, "guard_pick", "pick_up", {"item_id": LETTER}),
                    _call(GUARD, "guard_read", "observe", {"fact_id": FACT_PLOT}),
                    _call(
                        GUARD,
                        "guard_report",
                        "share_information",
                        {"target_character_id": STEWARD, "fact_id": FACT_PLOT},
                    ),
                ],
            ),
            STEWARD: ActorActionChain(
                actor_id=STEWARD,
                steps=[
                    WaitAgentStep(
                        step_id="steward_wait_report",
                        target_actor_id=GUARD,
                        target_step_id="guard_report",
                    ),
                    WaitStateStep(
                        step_id="steward_wait_knowledge",
                        condition=PlanStepCondition(
                            kind=PlanConditionKind.belief_known,
                            character_id=STEWARD,
                            fact_id=FACT_PLOT,
                        ),
                    ),
                    _call(
                        STEWARD,
                        "steward_share",
                        "share_information",
                        {"target_character_id": ALLY, "fact_id": FACT_PLOT},
                    ),
                    _call(
                        STEWARD,
                        "steward_ally",
                        "propose_alliance",
                        {
                            "target_character_id": ALLY,
                            "goal_key": GOAL_PROTECT,
                            "shared_fact_id": FACT_PLOT,
                        },
                    ),
                ],
            ),
            ALLY: ActorActionChain(
                actor_id=ALLY,
                steps=[
                    WaitAgentStep(
                        step_id="ally_wait_share",
                        target_actor_id=STEWARD,
                        target_step_id="steward_share",
                    ),
                    WaitStateStep(
                        step_id="ally_wait_knowledge",
                        condition=PlanStepCondition(
                            kind=PlanConditionKind.belief_known,
                            character_id=ALLY,
                            fact_id=FACT_PLOT,
                        ),
                    ),
                ],
            ),
        },
    )


def _run_tick(executor, plan, runtime, state, **kwargs):
    return asyncio.run(executor.tick(plan, runtime, state, **kwargs))


def test_joint_plan_schema_rejects_actor_mismatch_and_missing_wait_target():
    with pytest.raises(ValidationError, match="actor_id must match"):
        ActorActionChain(
            actor_id=GUARD,
            steps=[
                _call(STEWARD, "wrong_actor", "observe", {"fact_id": FACT_PLOT})
            ],
        )

    with pytest.raises(ValidationError, match="target actor is outside"):
        JointPlan(
            plan_id="bad_wait",
            goal_id=GOAL_PROTECT,
            base_world_version=0,
            actor_chains={
                GUARD: ActorActionChain(
                    actor_id=GUARD,
                    steps=[
                        WaitAgentStep(
                            step_id="wait_missing",
                            target_actor_id="missing_actor",
                            target_step_id="missing_step",
                        )
                    ],
                )
            },
        )


def test_joint_plan_requires_shared_goal_or_alliance():
    state = build_snapshot()
    plan = _cooperation_plan(state)
    registry = create_core_tool_registry()
    validate_joint_plan(
        plan,
        state,
        registry,
        permissions_by_actor={
            actor_id: CORE_TOOL_PERMISSIONS for actor_id in plan.actor_chains
        },
    )

    state.character_psyches[ALLY].goals = []
    with pytest.raises(ValueError, match="share an active goal"):
        validate_joint_plan(
            plan,
            state,
            registry,
            permissions_by_actor={
                actor_id: CORE_TOOL_PERMISSIONS for actor_id in plan.actor_chains
            },
        )


def test_explicit_waits_execute_only_ready_actors_without_empty_events():
    state = build_snapshot()
    plan = _cooperation_plan(state)
    runtime = create_plan_runtime(plan)
    executor = JointPlanExecutor(create_core_tool_registry())
    permissions = {
        actor_id: CORE_TOOL_PERMISSIONS for actor_id in plan.actor_chains
    }
    all_events = []
    saw_blocked = False

    for _ in range(8):
        result = _run_tick(
            executor,
            plan,
            runtime,
            state,
            permissions_by_actor=permissions,
        )
        saw_blocked = saw_blocked or any(
            advance.kind == ChainAdvanceKind.blocked
            for advance in result.advances
        )
        assert all(event.event_type.startswith("tool.") for event in result.events)
        all_events.extend(result.events)
        plan, runtime, state = result.plan, result.runtime, result.state
        if runtime.status == PlanRuntimeStatus.completed:
            break

    assert saw_blocked is True
    assert runtime.status == PlanRuntimeStatus.completed
    assert [event.event_type for event in all_events] == [
        "tool.pick_up",
        "tool.observe",
        "tool.share_information",
        "tool.share_information",
        "tool.propose_alliance",
    ]
    assert len(all_events) == 5
    assert state.version == 5
    assert state.alliances
    assert runtime.blocked_reasons == {}


def test_wait_graph_finds_deterministic_cycle():
    state = build_snapshot()
    plan = JointPlan(
        plan_id="deadlock",
        goal_id=GOAL_PROTECT,
        base_world_version=0,
        actor_chains={
            GUARD: ActorActionChain(
                actor_id=GUARD,
                steps=[
                    WaitAgentStep(
                        step_id="guard_wait",
                        target_actor_id=STEWARD,
                        target_step_id="steward_wait",
                    )
                ],
            ),
            STEWARD: ActorActionChain(
                actor_id=STEWARD,
                steps=[
                    WaitAgentStep(
                        step_id="steward_wait",
                        target_actor_id=GUARD,
                        target_step_id="guard_wait",
                    )
                ],
            ),
        },
    )
    runtime = create_plan_runtime(plan)
    graph = build_wait_graph(plan, runtime)

    assert find_deadlock(graph) == [GUARD, STEWARD, GUARD]
    validity = check_plan_validity(
        plan,
        runtime,
        state,
        create_core_tool_registry(),
    )
    assert validity.status == PlanValidityStatus.deadlocked
    assert validity.deadlock_cycle == [GUARD, STEWARD, GUARD]


def test_destroyed_dependency_marks_plan_stale_before_dispatch():
    state = build_snapshot()
    plan = _cooperation_plan(state)
    runtime = create_plan_runtime(plan)
    state.items[LETTER].accessible = False
    state.items[LETTER].quantity = 0

    validity = check_plan_validity(
        plan,
        runtime,
        state,
        create_core_tool_registry(),
        permissions_by_actor={GUARD: CORE_TOOL_PERMISSIONS},
    )

    assert validity.status == PlanValidityStatus.stale
    assert "missing_or_destroyed_item:%s" % LETTER in validity.reasons
    result = _run_tick(
        JointPlanExecutor(create_core_tool_registry()),
        plan,
        runtime,
        state,
        permissions_by_actor={
            actor_id: CORE_TOOL_PERMISSIONS for actor_id in plan.actor_chains
        },
    )
    assert result.events == []
    assert result.state.version == 0
    assert result.runtime.status == PlanRuntimeStatus.stale


def test_local_replan_expands_to_dependent_chains_and_keeps_history():
    state = build_snapshot()
    plan = _cooperation_plan(state)
    runtime = create_plan_runtime(plan)
    state.items[LETTER].accessible = False
    state.items[LETTER].quantity = 0
    seen = {}

    def replan(request, current_state):
        seen["request"] = request
        return JointPlan(
            plan_id="protect_estate_joint_v2",
            goal_id=GOAL_PROTECT,
            base_world_version=current_state.version,
            actor_chains={
                GUARD: ActorActionChain(
                    actor_id=GUARD,
                    steps=[
                        _call(
                            GUARD,
                            "guard_fallback_move",
                            "move_to",
                            {"destination_id": STEWARD},
                        )
                    ],
                ),
                STEWARD: ActorActionChain(
                    actor_id=STEWARD,
                    steps=[
                        _call(
                            STEWARD,
                            "steward_fallback_move",
                            "move_to",
                            {"destination_id": ALLY},
                        )
                    ],
                ),
                ALLY: ActorActionChain(actor_id=ALLY, steps=[]),
            },
            revision=1,
            parent_plan_id=request.original_plan_id,
        )

    result = _run_tick(
        JointPlanExecutor(create_core_tool_registry()),
        plan,
        runtime,
        state,
        permissions_by_actor={
            actor_id: CORE_TOOL_PERMISSIONS for actor_id in plan.actor_chains
        },
        replan=replan,
    )

    assert result.replanned is True
    assert seen["request"].affected_actor_ids == [ALLY, GUARD, STEWARD]
    assert result.plan.actor_chains[GUARD].steps[0].step_id == "guard_fallback_move"
    assert result.plan.actor_chains[STEWARD].steps[0].step_id == "steward_fallback_move"
    assert result.runtime.replan_count == 1
    assert result.runtime.status == PlanRuntimeStatus.active


def test_persisted_replan_aborts_superseded_parent(tmp_path):
    state = build_snapshot()
    plan = _cooperation_plan(state)
    runtime = create_plan_runtime(plan)
    store = SQLiteWorldStore(tmp_path / "world.sqlite3")
    session_id = store.create_session(
        state,
        default_actor_id=GUARD,
        world_package_id="secret_letter_v1",
    )
    store.save_joint_plan_runtime(session_id, plan, runtime)
    state.items[LETTER].accessible = False
    state.items[LETTER].quantity = 0

    def replan(request, current_state):
        return JointPlan(
            plan_id="replacement_plan",
            goal_id=request.goal_id,
            base_world_version=current_state.version,
            actor_chains={
                actor_id: ActorActionChain(actor_id=actor_id, steps=[])
                for actor_id in request.affected_actor_ids
            },
            revision=request.revision + 1,
            parent_plan_id=request.original_plan_id,
        )

    result = _run_tick(
        JointPlanExecutor(create_core_tool_registry()),
        plan,
        runtime,
        state,
        permissions_by_actor={
            actor_id: CORE_TOOL_PERMISSIONS for actor_id in plan.actor_chains
        },
        replan=replan,
        store=store,
        session_id=session_id,
    )

    assert result.replanned is True
    stored = {
        saved_plan.plan_id: saved_runtime
        for saved_plan, saved_runtime in store.list_joint_plan_runtimes(session_id)
    }
    assert stored[plan.plan_id].status == PlanRuntimeStatus.aborted
    assert stored[plan.plan_id].last_trigger.startswith("SUPERSEDED_BY_REPLAN:")
    assert stored[result.plan.plan_id].status == PlanRuntimeStatus.active


def test_runtime_persistence_and_event_reconciliation_prevent_duplicate_action(
    tmp_path,
):
    state = build_snapshot()
    plan = JointPlan(
        plan_id="persistent_plan",
        goal_id=GOAL_PROTECT,
        base_world_version=state.version,
        actor_chains={
            GUARD: ActorActionChain(
                actor_id=GUARD,
                steps=[
                    _call(GUARD, "persistent_pick", "pick_up", {"item_id": LETTER})
                ],
            )
        },
    )
    runtime = create_plan_runtime(plan)
    store = SQLiteWorldStore(tmp_path / "joint_plan.sqlite3")
    session_id = store.create_session(
        state,
        default_actor_id=PLAYER,
        world_package_id="secret_letter",
    )
    result = _run_tick(
        JointPlanExecutor(create_core_tool_registry()),
        plan,
        runtime,
        state,
        permissions_by_actor={GUARD: CORE_TOOL_PERMISSIONS},
        store=store,
        session_id=session_id,
    )
    loaded = store.get_joint_plan_runtime(session_id, plan.plan_id)

    assert loaded is not None
    loaded_plan, loaded_runtime = loaded
    assert loaded_runtime.status == PlanRuntimeStatus.completed
    assert loaded_runtime.actor_step_pointers[GUARD] == 1
    assert len(store.list_events(session_id)) == 1

    # Simulate a crash window: restore the pre-commit pointer, then reconcile
    # it against the authoritative event log before a new dispatch.
    stale_runtime = create_plan_runtime(loaded_plan)
    reconciled = reconcile_runtime(
        loaded_plan,
        stale_runtime,
        store.list_events(session_id),
    )
    assert reconciled.status == PlanRuntimeStatus.completed
    again = _run_tick(
        JointPlanExecutor(create_core_tool_registry()),
        loaded_plan,
        stale_runtime,
        store.get_state(session_id),
        permissions_by_actor={GUARD: CORE_TOOL_PERMISSIONS},
        store=store,
        session_id=session_id,
    )
    assert again.events == []
    assert len(store.list_events(session_id)) == 1


def test_relevant_diff_only_contains_plan_dependencies():
    before = build_snapshot()
    after = before.copy(deep=True)
    after.version = 2
    after.characters[GUARD].location_id = after.characters[STEWARD].location_id
    after.characters[PLAYER].location_id = "loc_courtyard"
    plan = _cooperation_plan(before)
    runtime = create_plan_runtime(plan)

    diff = build_relevant_diff(before, after, runtime.dependencies)

    assert GUARD not in diff.moved_characters  # same location in fixture already
    assert PLAYER not in diff.moved_characters
    assert diff.from_version == 0
    assert diff.to_version == 2


def test_policy_joint_replanner_keeps_actor_observations_isolated():
    state = build_snapshot()
    state.beliefs[GUARD] = [
        CharacterBelief(
            fact_id=FACT_PLOT,
            belief=Belief.believed_true,
            confidence=0.9,
            source_type="secret",
        )
    ]
    captured = {}

    class _Policy:
        def __init__(self, actor_id):
            self.actor_id = actor_id

        def replan(self, observation, available_tools, feedback):
            captured[self.actor_id] = observation
            return PlannerDecision.from_tool_call(
                ToolCall(
                    actor_id=self.actor_id,
                    tool_name="move_to",
                    arguments={"destination_id": "loc_courtyard"},
                ),
                policy_id="isolated_replan",
            )

    request = ReplanRequest(
        original_plan_id="old",
        goal_id=GOAL_PROTECT,
        revision=0,
        world_version=state.version,
        affected_actor_ids=[GUARD, STEWARD],
        completed_steps={},
        remaining_chains={
            GUARD: ActorActionChain(actor_id=GUARD, steps=[]),
            STEWARD: ActorActionChain(actor_id=STEWARD, steps=[]),
        },
        failure_codes=["spatial_constraint"],
    )
    replacement = PolicyJointReplanner(
        create_core_tool_registry(),
        {GUARD: _Policy(GUARD), STEWARD: _Policy(STEWARD)},
    )(request, state)

    assert set(replacement.actor_chains) == {GUARD, STEWARD}
    assert [item.fact_id for item in captured[GUARD].beliefs] == [FACT_PLOT]
    assert captured[STEWARD].beliefs == ()
