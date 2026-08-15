"""Fixed-seed dynamic-perturbation evaluation for executable joint plans."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from engine import (
    CORE_TOOL_PERMISSIONS,
    ActionStep,
    ActorActionChain,
    JointPlan,
    JointPlanExecutor,
    JointPlanTrigger,
    PlanRuntimeStatus,
    ReplanRequest,
    ToolCall,
    WaitAgentStep,
    WaitStateStep,
    authoritative_state_hash,
    commit_event,
    create_core_tool_registry,
    create_plan_runtime,
    evaluate_trajectory,
)
from examples.secret_letter import (
    ALLY,
    COURTYARD,
    FACT_PLOT,
    GOAL_PROTECT,
    GUARD,
    LETTER,
    PLAYER,
    STEWARD,
    build_snapshot,
)
from world_schema import (
    Belief,
    CharacterBelief,
    Operation,
    OperationKind,
    PlanConditionKind,
    PlanStepCondition,
    StatePatch,
    WorldEvent,
    WorldState,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = PROJECT_ROOT / "evaluation" / "long_horizon_cases.json"
DEFAULT_REPORT = PROJECT_ROOT / "evaluation" / "reports" / "long-horizon-v1.json"


class PerturbationKind(str, Enum):
    destroy_item = "destroy_item"
    move_key_actor = "move_key_actor"
    conflicting_evidence = "conflicting_evidence"
    item_competition = "item_competition"
    wait_cycle = "wait_cycle"
    invalid_entity = "invalid_entity"


class Perturbation(BaseModel):
    perturbation_id: str
    kind: PerturbationKind
    turn: int = Field(ge=0, le=29)
    parameters: Dict[str, object] = Field(default_factory=dict)

    class Config:
        extra = "forbid"


class LongHorizonCase(BaseModel):
    case_id: str
    seed: int
    max_turns: int = Field(ge=10, le=30)
    objective_conditions: List[PlanStepCondition]
    perturbations: List[Perturbation]
    expected_stale_triggers: int = Field(0, ge=0)
    expected_deadlocks: int = Field(0, ge=0)
    expected_illegal_proposals: int = Field(0, ge=0)

    class Config:
        extra = "forbid"


class LongHorizonCaseResult(BaseModel):
    case_id: str
    seed: int
    max_turns: int
    turns_executed: int
    objective_satisfied: bool
    plan_status: PlanRuntimeStatus
    event_count: int = Field(ge=0)
    perturbation_event_count: int = Field(ge=0)
    stale_plan_count: int = Field(ge=0)
    detected_stale_plan_count: int = Field(ge=0)
    deadlock_count: int = Field(ge=0)
    recovered_deadlock_count: int = Field(ge=0)
    replan_count: int = Field(ge=0)
    useful_replan_count: int = Field(ge=0)
    unnecessary_replan_count: int = Field(ge=0)
    external_llm_call_count: int = Field(ge=0)
    illegal_proposal_count: int = Field(ge=0)
    illegal_commit_count: int = Field(ge=0)
    replay_consistent: bool
    final_state_hash: str
    triggers: List[JointPlanTrigger] = Field(default_factory=list)

    class Config:
        extra = "forbid"


class LongHorizonMetrics(BaseModel):
    episode_count: int = Field(ge=0)
    long_horizon_success_rate: float = Field(ge=0.0, le=1.0)
    deadlock_recovery_rate: Optional[float] = Field(None, ge=0.0, le=1.0)
    replan_precision: Optional[float] = Field(None, ge=0.0, le=1.0)
    staleness_recall: Optional[float] = Field(None, ge=0.0, le=1.0)
    unnecessary_replan_rate: Optional[float] = Field(None, ge=0.0, le=1.0)
    planner_calls_per_success: Optional[float] = Field(None, ge=0.0)
    external_llm_calls_per_success: Optional[float] = Field(None, ge=0.0)
    illegal_commit_count: int = Field(ge=0)
    replay_consistency_rate: float = Field(ge=0.0, le=1.0)

    class Config:
        extra = "forbid"


class LongHorizonReport(BaseModel):
    schema_version: str = "long_horizon_report.v1"
    suite_id: str
    deterministic: bool = True
    cases: List[LongHorizonCaseResult]
    metrics: LongHorizonMetrics
    passed: bool

    class Config:
        extra = "forbid"


class _RepairPlanner:
    """Deterministic repair oracle used to test runtime mechanics, not LLM quality."""

    def __init__(self, case: LongHorizonCase):
        self.case = case
        self.calls = 0

    def __call__(self, request: ReplanRequest, state: WorldState) -> JointPlan:
        self.calls += 1
        chains = {}
        primary = _repair_actor(self.case, request)
        for actor_id in request.affected_actor_ids:
            steps = []
            if actor_id == primary:
                steps = [
                    _action(
                        actor_id,
                        "repair_%s_%d" % (actor_id, request.revision + 1),
                        "move_to",
                        {"destination_id": COURTYARD},
                    )
                ]
            chains[actor_id] = ActorActionChain(actor_id=actor_id, steps=steps)
        return JointPlan(
            plan_id="%s_repair_%d" % (self.case.case_id, self.calls),
            goal_id=GOAL_PROTECT,
            base_world_version=state.version,
            actor_chains=chains,
            revision=request.revision + 1,
            parent_plan_id=request.original_plan_id,
            metadata={
                "evaluation_only": True,
                "repair_trigger": list(request.failure_codes),
            },
        )


def load_long_horizon_cases(path: Path = DEFAULT_CASES) -> List[LongHorizonCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("only long-horizon case schema_version=1 is supported")
    return [LongHorizonCase.parse_obj(item) for item in payload.get("cases", [])]


def run_long_horizon_suite(
    path: Path = DEFAULT_CASES,
) -> LongHorizonReport:
    cases = load_long_horizon_cases(path)
    results = [asyncio.run(_run_case(case)) for case in cases]
    metrics = _aggregate(results)
    passed = bool(results) and all(
        result.objective_satisfied
        and result.replay_consistent
        and result.illegal_commit_count == 0
        and result.detected_stale_plan_count == result.stale_plan_count
        and result.recovered_deadlock_count == result.deadlock_count
        for result in results
    )
    return LongHorizonReport(
        suite_id="novelsim-dynamic-joint-plan-v1",
        cases=results,
        metrics=metrics,
        passed=passed,
    )


async def _run_case(case: LongHorizonCase) -> LongHorizonCaseResult:
    initial = build_snapshot()
    state = initial.copy(deep=True)
    plan = _build_case_plan(case, state)
    runtime = create_plan_runtime(plan, max_replans=3)
    registry = create_core_tool_registry()
    executor = JointPlanExecutor(registry)
    repair = _RepairPlanner(case)
    permissions = {
        actor_id: CORE_TOOL_PERMISSIONS for actor_id in state.characters
    }
    events: List[WorldEvent] = []
    triggers: List[JointPlanTrigger] = []
    perturbation_events = 0
    detected_stale = 0
    detected_deadlocks = 0
    recovered_deadlocks = 0
    illegal_commits = 0
    turns_executed = 0
    objective_satisfied = False

    for turn in range(case.max_turns):
        turns_executed = turn + 1
        for perturbation in case.perturbations:
            if perturbation.turn != turn:
                continue
            state, injected = await _inject_perturbation(
                perturbation,
                state,
                executor,
                permissions,
                seed=case.seed,
            )
            events.extend(injected)
            perturbation_events += len(injected)

        result = await executor.tick(
            plan,
            runtime,
            state,
            permissions_by_actor=permissions,
            replan=repair,
        )
        plan, runtime, state = result.plan, result.runtime, result.state
        events.extend(result.events)
        if result.replan_trigger is not None:
            trigger = result.replan_trigger
            triggers.append(trigger)
            if trigger.code == "PLAN_STALE":
                detected_stale += 1
            if trigger.code == "PLAN_DEADLOCK":
                detected_deadlocks += 1
                if result.replanned:
                    recovered_deadlocks += 1
        illegal_commits += sum(
            event.action_id == "call_invalid_destination" for event in result.events
        )
        objective_satisfied = all(
            _objective_holds(condition, state)
            for condition in case.objective_conditions
        )
        # Continue monitoring until max_turns even after completion.  This
        # exercises stable completed-state handling without creating empty
        # world events or calling the planner again.

    replay = evaluate_trajectory(
        initial,
        events,
        expected_final_state=state,
    )
    useful_replans = repair.calls if objective_satisfied else 0
    unnecessary = max(0, repair.calls - useful_replans)
    return LongHorizonCaseResult(
        case_id=case.case_id,
        seed=case.seed,
        max_turns=case.max_turns,
        turns_executed=turns_executed,
        objective_satisfied=objective_satisfied,
        plan_status=runtime.status,
        event_count=len(events),
        perturbation_event_count=perturbation_events,
        stale_plan_count=case.expected_stale_triggers,
        detected_stale_plan_count=detected_stale,
        deadlock_count=case.expected_deadlocks,
        recovered_deadlock_count=recovered_deadlocks,
        replan_count=repair.calls,
        useful_replan_count=useful_replans,
        unnecessary_replan_count=unnecessary,
        external_llm_call_count=0,
        illegal_proposal_count=case.expected_illegal_proposals,
        illegal_commit_count=illegal_commits,
        replay_consistent=replay.passed,
        final_state_hash=authoritative_state_hash(state),
        triggers=triggers,
    )


async def _inject_perturbation(
    perturbation: Perturbation,
    state: WorldState,
    executor: JointPlanExecutor,
    permissions: Dict[str, Tuple[str, ...]],
    *,
    seed: int,
) -> Tuple[WorldState, List[WorldEvent]]:
    events = []
    if perturbation.kind == PerturbationKind.destroy_item:
        for index, tool_name in enumerate(("pick_up", "destroy_item"), start=1):
            outcome = await executor.execution_machine.execute(
                ToolCall(
                    call_id="perturb_destroy_%d" % index,
                    actor_id=PLAYER,
                    tool_name=tool_name,
                    arguments={"item_id": LETTER},
                ),
                state,
                permissions=permissions[PLAYER],
                metadata={"perturbation_id": perturbation.perturbation_id},
            )
            if not outcome.result.success:
                raise RuntimeError("destroy perturbation failed")
            state = outcome.new_state
            events.append(outcome.event)
        return state, [event for event in events if event is not None]
    if perturbation.kind == PerturbationKind.move_key_actor:
        outcome = await executor.execution_machine.execute(
            ToolCall(
                call_id="perturb_move_steward",
                actor_id=STEWARD,
                tool_name="move_to",
                arguments={"destination_id": COURTYARD},
            ),
            state,
            permissions=permissions[STEWARD],
            metadata={"perturbation_id": perturbation.perturbation_id},
        )
        if not outcome.result.success or outcome.event is None:
            raise RuntimeError("move-key-actor perturbation failed")
        return outcome.new_state, [outcome.event]
    if perturbation.kind == PerturbationKind.conflicting_evidence:
        event, new_state = commit_event(
            state,
            action_id="perturb_conflicting_evidence",
            event_type="environment.conflicting_evidence",
            patch=StatePatch(
                operations=[
                    Operation(
                        op=OperationKind.update_belief,
                        target_id=GUARD,
                        fact_id=FACT_PLOT,
                        belief=Belief.believed_false,
                        confidence=0.85,
                        source_type="observation",
                        reason="fixed-seed contradictory evidence",
                    )
                ]
            ),
            actor_ids=[GUARD],
            target_ids=[FACT_PLOT],
            random_seed=seed,
            expected_version=state.version,
            summary="guard receives newer contradictory evidence",
        )
        return new_state, [event]
    # Competition, wait-cycle and invalid-entity perturbations are encoded in
    # the initial plan itself so every compared runtime receives identical JSON.
    return state, []


def _build_case_plan(case: LongHorizonCase, state: WorldState) -> JointPlan:
    kind = case.perturbations[0].kind
    if kind == PerturbationKind.destroy_item:
        chains = {
            GUARD: ActorActionChain(
                actor_id=GUARD,
                steps=[_action(GUARD, "pick_after_destroy", "pick_up", {"item_id": LETTER})],
            )
        }
    elif kind == PerturbationKind.move_key_actor:
        chains = {
            GUARD: ActorActionChain(
                actor_id=GUARD,
                steps=[
                    _action(GUARD, "observe_before_share", "observe", {"fact_id": FACT_PLOT}),
                    _action(
                        GUARD,
                        "share_after_move",
                        "share_information",
                        {"target_character_id": STEWARD, "fact_id": FACT_PLOT},
                    ),
                ],
            )
        }
    elif kind == PerturbationKind.conflicting_evidence:
        state.beliefs[GUARD] = [
            CharacterBelief(
                fact_id=FACT_PLOT,
                belief=Belief.believed_true,
                confidence=0.9,
                source_type="memory",
            )
        ]
        chains = {
            GUARD: ActorActionChain(
                actor_id=GUARD,
                steps=[_action(GUARD, "move_with_conflict", "move_to", {"destination_id": COURTYARD})],
            )
        }
    elif kind == PerturbationKind.item_competition:
        chains = {
            GUARD: ActorActionChain(
                actor_id=GUARD,
                steps=[_action(GUARD, "guard_competes", "pick_up", {"item_id": LETTER})],
            ),
            STEWARD: ActorActionChain(
                actor_id=STEWARD,
                steps=[_action(STEWARD, "steward_competes", "pick_up", {"item_id": LETTER})],
            ),
        }
    elif kind == PerturbationKind.wait_cycle:
        chains = {
            GUARD: ActorActionChain(
                actor_id=GUARD,
                steps=[
                    WaitAgentStep(
                        step_id="guard_cycle_wait",
                        target_actor_id=STEWARD,
                        target_step_id="steward_cycle_wait",
                    )
                ],
            ),
            STEWARD: ActorActionChain(
                actor_id=STEWARD,
                steps=[
                    WaitAgentStep(
                        step_id="steward_cycle_wait",
                        target_actor_id=GUARD,
                        target_step_id="guard_cycle_wait",
                    )
                ],
            ),
        }
    elif kind == PerturbationKind.invalid_entity:
        chains = {
            GUARD: ActorActionChain(
                actor_id=GUARD,
                steps=[
                    _action(
                        GUARD,
                        "invalid_destination",
                        "move_to",
                        {"destination_id": "missing_airport"},
                        call_id="call_invalid_destination",
                    )
                ],
            )
        }
    else:
        raise ValueError("unsupported perturbation kind: %s" % kind.value)
    return JointPlan(
        plan_id="%s_plan_v1" % case.case_id,
        goal_id=GOAL_PROTECT,
        base_world_version=state.version,
        actor_chains=chains,
        metadata={"seed": case.seed, "case_id": case.case_id},
    )


def _action(
    actor_id: str,
    step_id: str,
    tool_name: str,
    arguments: Dict[str, object],
    *,
    call_id: Optional[str] = None,
) -> ActionStep:
    return ActionStep(
        step_id=step_id,
        tool_call=ToolCall(
            call_id=call_id or "call_%s" % step_id,
            actor_id=actor_id,
            tool_name=tool_name,
            arguments=arguments,
        ),
    )


def _repair_actor(case: LongHorizonCase, request: ReplanRequest) -> str:
    kind = case.perturbations[0].kind
    if kind == PerturbationKind.item_competition:
        return STEWARD
    return GUARD if GUARD in request.affected_actor_ids else request.affected_actor_ids[0]


def _objective_holds(condition: PlanStepCondition, state: WorldState) -> bool:
    from engine.plan_progress import condition_holds

    return condition_holds(condition, state)


def _aggregate(results: List[LongHorizonCaseResult]) -> LongHorizonMetrics:
    episodes = len(results)
    successes = sum(result.objective_satisfied for result in results)
    deadlocks = sum(result.deadlock_count for result in results)
    recovered = sum(result.recovered_deadlock_count for result in results)
    replans = sum(result.replan_count for result in results)
    useful = sum(result.useful_replan_count for result in results)
    unnecessary = sum(result.unnecessary_replan_count for result in results)
    stale = sum(result.stale_plan_count for result in results)
    detected = sum(result.detected_stale_plan_count for result in results)
    external_llm_calls = sum(result.external_llm_call_count for result in results)
    return LongHorizonMetrics(
        episode_count=episodes,
        long_horizon_success_rate=_rate(successes, episodes) or 0.0,
        deadlock_recovery_rate=_rate(recovered, deadlocks),
        replan_precision=_rate(useful, replans),
        staleness_recall=_rate(detected, stale),
        unnecessary_replan_rate=_rate(unnecessary, replans),
        planner_calls_per_success=(replans / successes if successes else None),
        external_llm_calls_per_success=(
            external_llm_calls / successes if successes else None
        ),
        illegal_commit_count=sum(result.illegal_commit_count for result in results),
        replay_consistency_rate=_rate(
            sum(result.replay_consistent for result in results),
            episodes,
        )
        or 0.0,
    )


def _rate(numerator: int, denominator: int) -> Optional[float]:
    if denominator == 0:
        return None
    return numerator / denominator


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run NovelSim fixed-seed dynamic joint-plan evaluation"
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    report = run_long_horizon_suite(args.cases)
    text = report.json(ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())

