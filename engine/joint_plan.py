"""Executable joint plans for long-horizon multi-agent collaboration.

The language model may propose a :class:`JointPlan`, but this module owns all
runtime state: step pointers, explicit waits, deadlock detection, staleness
checks, bounded local replanning and execution through the existing guarded
``AgentExecutionStateMachine``.  No plan step can mutate ``WorldState``
directly.
"""

from __future__ import annotations

import inspect
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)
from uuid import uuid4

from pydantic import BaseModel, Field, root_validator, validator

from world_schema import (
    Belief,
    PlanConditionKind,
    PlanStepCondition,
    WorldEvent,
    WorldState,
)

from .agent_runtime import AgentExecutionOutcome, AgentExecutionStateMachine
from .agent_tools import (
    ToolCall,
    ToolDefinition,
    ToolExecutionError,
    ToolFailure,
    ToolFailureCode,
    ToolRegistry,
)
from .game_observation import PlannerFeedback, build_game_observation
from .plan_progress import condition_holds


class _StrictModel(BaseModel):
    class Config:
        extra = "forbid"


class ActionStep(_StrictModel):
    """A guarded game action.  The payload is a ToolCall, never a StatePatch."""

    kind: Literal["action"] = "action"
    step_id: str
    tool_call: ToolCall

    @validator("step_id")
    def _step_id_is_not_blank(cls, value):
        if not value.strip():
            raise ValueError("step_id cannot be blank")
        return value


class WaitAgentStep(_StrictModel):
    """Block until another actor has completed one named step."""

    kind: Literal["wait_agent"] = "wait_agent"
    step_id: str
    target_actor_id: str
    target_step_id: str

    @validator("step_id", "target_actor_id", "target_step_id")
    def _ids_are_not_blank(cls, value):
        if not value.strip():
            raise ValueError("wait-agent identifiers cannot be blank")
        return value


class WaitStateStep(_StrictModel):
    """Block until an authoritative GOAP/HTN predicate becomes true."""

    kind: Literal["wait_state"] = "wait_state"
    step_id: str
    condition: PlanStepCondition

    @validator("step_id")
    def _step_id_is_not_blank(cls, value):
        if not value.strip():
            raise ValueError("step_id cannot be blank")
        return value


PlanStep = Union[ActionStep, WaitAgentStep, WaitStateStep]


class ActorActionChain(_StrictModel):
    actor_id: str
    steps: List[PlanStep] = Field(default_factory=list)

    @root_validator(skip_on_failure=True)
    def _chain_is_well_formed(cls, values):
        actor_id = (values.get("actor_id") or "").strip()
        if not actor_id:
            raise ValueError("actor_id cannot be blank")
        steps = values.get("steps") or []
        ids = [step.step_id for step in steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step_id must be unique within an actor chain")
        for step in steps:
            if isinstance(step, ActionStep) and step.tool_call.actor_id != actor_id:
                raise ValueError("ToolCall actor_id must match chain actor_id")
        return values


class JointPlan(_StrictModel):
    schema_version: str = "joint_plan.v1"
    plan_id: str = Field(default_factory=lambda: uuid4().hex)
    goal_id: str
    base_world_version: int = Field(ge=0)
    actor_chains: Dict[str, ActorActionChain]
    revision: int = Field(0, ge=0)
    parent_plan_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @root_validator(skip_on_failure=True)
    def _plan_is_well_formed(cls, values):
        plan_id = (values.get("plan_id") or "").strip()
        goal_id = (values.get("goal_id") or "").strip()
        chains = values.get("actor_chains") or {}
        if not plan_id or not goal_id:
            raise ValueError("plan_id and goal_id cannot be blank")
        if not chains:
            raise ValueError("joint plan requires at least one actor chain")
        for actor_id, chain in chains.items():
            if actor_id != chain.actor_id:
                raise ValueError("actor_chains key must match chain.actor_id")
        for actor_id, chain in chains.items():
            for step in chain.steps:
                if not isinstance(step, WaitAgentStep):
                    continue
                target = chains.get(step.target_actor_id)
                if target is None:
                    raise ValueError(
                        "wait_agent target actor is outside the joint plan: %s"
                        % step.target_actor_id
                    )
                if step.target_step_id not in {item.step_id for item in target.steps}:
                    raise ValueError(
                        "wait_agent target step does not exist: %s/%s"
                        % (step.target_actor_id, step.target_step_id)
                    )
                if (
                    step.target_actor_id == actor_id
                    and step.target_step_id == step.step_id
                ):
                    raise ValueError("a wait step cannot wait on itself")
        return values


class PlanDependency(_StrictModel):
    actor_ids: Set[str] = Field(default_factory=set)
    item_ids: Set[str] = Field(default_factory=set)
    location_ids: Set[str] = Field(default_factory=set)
    fact_ids: Set[str] = Field(default_factory=set)
    relation_edges: Set[Tuple[str, str]] = Field(default_factory=set)
    conditions: List[PlanStepCondition] = Field(default_factory=list)


class PlanRuntimeStatus(str, Enum):
    active = "active"
    completed = "completed"
    stale = "stale"
    deadlocked = "deadlocked"
    aborted = "aborted"


class PlanRuntimeState(_StrictModel):
    schema_version: str = "joint_plan_runtime.v1"
    plan_id: str
    base_world_version: int = Field(ge=0)
    observed_world_version: int = Field(ge=0)
    dependencies: PlanDependency = Field(default_factory=PlanDependency)
    actor_step_pointers: Dict[str, int]
    completed_steps: Dict[str, List[str]] = Field(default_factory=dict)
    blocked_reasons: Dict[str, str] = Field(default_factory=dict)
    status: PlanRuntimeStatus = PlanRuntimeStatus.active
    stale_reasons: List[str] = Field(default_factory=list)
    deadlock_cycle: List[str] = Field(default_factory=list)
    replan_count: int = Field(0, ge=0)
    max_replans: int = Field(2, ge=0, le=20)
    last_trigger: str = ""

    @root_validator(skip_on_failure=True)
    def _runtime_has_valid_pointers(cls, values):
        pointers = values.get("actor_step_pointers") or {}
        if any(pointer < 0 for pointer in pointers.values()):
            raise ValueError("actor step pointers cannot be negative")
        return values


class StateDiff(_StrictModel):
    from_version: int = Field(ge=0)
    to_version: int = Field(ge=0)
    moved_characters: Dict[str, Tuple[Optional[str], Optional[str]]] = Field(
        default_factory=dict
    )
    changed_items: Dict[str, Tuple[Optional[str], Optional[str]]] = Field(
        default_factory=dict
    )
    changed_beliefs: Dict[str, List[str]] = Field(default_factory=dict)
    changed_relations: List[Tuple[str, str]] = Field(default_factory=list)


class PlanValidityStatus(str, Enum):
    valid = "valid"
    stale = "stale"
    deadlocked = "deadlocked"


class PlanValidityResult(_StrictModel):
    status: PlanValidityStatus = PlanValidityStatus.valid
    reasons: List[str] = Field(default_factory=list)
    relevant_diff: Optional[StateDiff] = None
    deadlock_cycle: List[str] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.status == PlanValidityStatus.valid


class ChainAdvanceKind(str, Enum):
    dispatch = "dispatch"
    blocked = "blocked"
    completed = "completed"


class ChainAdvance(_StrictModel):
    actor_id: str
    kind: ChainAdvanceKind
    step_id: Optional[str] = None
    tool_call: Optional[ToolCall] = None
    reason: str = ""


class JointPlanTrigger(_StrictModel):
    code: str
    reasons: List[str] = Field(default_factory=list)
    affected_actor_ids: List[str] = Field(default_factory=list)
    deadlock_cycle: List[str] = Field(default_factory=list)
    relevant_diff: Optional[StateDiff] = None


class ReplanRequest(_StrictModel):
    original_plan_id: str
    goal_id: str
    revision: int = Field(ge=0)
    world_version: int = Field(ge=0)
    affected_actor_ids: List[str]
    completed_steps: Dict[str, List[str]]
    remaining_chains: Dict[str, ActorActionChain]
    failure_codes: List[str] = Field(default_factory=list)
    relevant_diff: Optional[StateDiff] = None


class JointPlanTickResult(_StrictModel):
    plan: JointPlan
    runtime: PlanRuntimeState
    state: WorldState
    advances: List[ChainAdvance] = Field(default_factory=list)
    outcomes: List[AgentExecutionOutcome] = Field(default_factory=list)
    events: List[WorldEvent] = Field(default_factory=list)
    replanned: bool = False
    replan_trigger: Optional[JointPlanTrigger] = None


JointReplanCallback = Callable[
    [ReplanRequest, WorldState],
    Union[Optional[JointPlan], Awaitable[Optional[JointPlan]]],
]


def create_plan_runtime(
    plan: JointPlan,
    *,
    max_replans: int = 2,
) -> PlanRuntimeState:
    return PlanRuntimeState(
        plan_id=plan.plan_id,
        base_world_version=plan.base_world_version,
        observed_world_version=plan.base_world_version,
        dependencies=extract_plan_dependencies(plan),
        actor_step_pointers={actor_id: 0 for actor_id in plan.actor_chains},
        completed_steps={actor_id: [] for actor_id in plan.actor_chains},
        max_replans=max_replans,
    )


def validate_joint_plan(
    plan: JointPlan,
    state: WorldState,
    registry: ToolRegistry,
    *,
    permissions_by_actor: Optional[Mapping[str, Iterable[str]]] = None,
    enforce_shared_scope: bool = True,
) -> None:
    """Validate plan structure, tool schemas and collaboration scope."""

    permissions = permissions_by_actor or {}
    missing = sorted(set(plan.actor_chains) - set(state.characters))
    if missing:
        raise ValueError("joint plan references missing actors: %s" % ", ".join(missing))
    dead = sorted(
        actor_id
        for actor_id in plan.actor_chains
        if not state.characters[actor_id].is_alive
    )
    if dead:
        raise ValueError("joint plan references dead actors: %s" % ", ".join(dead))
    if enforce_shared_scope and len(plan.actor_chains) > 1:
        _validate_shared_scope(plan, state)
    for actor_id, chain in plan.actor_chains.items():
        for step in chain.steps:
            if isinstance(step, ActionStep):
                registry.prepare(
                    step.tool_call,
                    state,
                    permissions=permissions.get(actor_id, ()),
                )


def extract_plan_dependencies(
    plan: JointPlan,
    runtime: Optional[PlanRuntimeState] = None,
) -> PlanDependency:
    dependencies = PlanDependency()
    for actor_id, chain in plan.actor_chains.items():
        start = 0 if runtime is None else runtime.actor_step_pointers.get(actor_id, 0)
        dependencies.actor_ids.add(actor_id)
        for step in chain.steps[start:]:
            if isinstance(step, ActionStep):
                _collect_argument_dependencies(step.tool_call.arguments, dependencies)
            elif isinstance(step, WaitAgentStep):
                dependencies.actor_ids.add(step.target_actor_id)
            elif isinstance(step, WaitStateStep):
                dependencies.conditions.append(step.condition)
                _collect_condition_dependencies(step.condition, dependencies)
    return dependencies


def build_relevant_diff(
    before: WorldState,
    after: WorldState,
    dependencies: PlanDependency,
) -> StateDiff:
    moved = {}
    for actor_id in sorted(dependencies.actor_ids):
        old = before.characters.get(actor_id)
        new = after.characters.get(actor_id)
        old_location = old.location_id if old is not None else None
        new_location = new.location_id if new is not None else None
        if old_location != new_location:
            moved[actor_id] = (old_location, new_location)
    changed_items = {}
    for item_id in sorted(dependencies.item_ids):
        old = before.items.get(item_id)
        new = after.items.get(item_id)
        old_holder = _item_holder(old)
        new_holder = _item_holder(new)
        if old_holder != new_holder:
            changed_items[item_id] = (old_holder, new_holder)
    changed_beliefs: Dict[str, List[str]] = {}
    for actor_id in sorted(dependencies.actor_ids):
        old = _belief_signature(before, actor_id, dependencies.fact_ids)
        new = _belief_signature(after, actor_id, dependencies.fact_ids)
        changed = sorted(key for key in set(old) | set(new) if old.get(key) != new.get(key))
        if changed:
            changed_beliefs[actor_id] = changed
    changed_relations = []
    before_relations = _relation_signatures(before)
    after_relations = _relation_signatures(after)
    for edge in sorted(dependencies.relation_edges):
        if before_relations.get(edge) != after_relations.get(edge):
            changed_relations.append(edge)
    return StateDiff(
        from_version=before.version,
        to_version=after.version,
        moved_characters=moved,
        changed_items=changed_items,
        changed_beliefs=changed_beliefs,
        changed_relations=changed_relations,
    )


def build_wait_graph(
    plan: JointPlan,
    runtime: PlanRuntimeState,
) -> Dict[str, Set[str]]:
    graph: Dict[str, Set[str]] = {actor_id: set() for actor_id in plan.actor_chains}
    for actor_id, chain in plan.actor_chains.items():
        pointer = runtime.actor_step_pointers.get(actor_id, 0)
        if pointer >= len(chain.steps):
            continue
        step = chain.steps[pointer]
        if isinstance(step, WaitAgentStep) and not _is_step_completed(
            runtime,
            step.target_actor_id,
            step.target_step_id,
        ):
            graph[actor_id].add(step.target_actor_id)
    return graph


def find_deadlock(graph: Mapping[str, Iterable[str]]) -> List[str]:
    """Return one deterministic directed cycle, including its closing node."""

    visiting: Set[str] = set()
    visited: Set[str] = set()

    def dfs(node: str, path: List[str]) -> List[str]:
        if node in visiting:
            start = path.index(node)
            return path[start:] + [node]
        if node in visited:
            return []
        visiting.add(node)
        path.append(node)
        for target in sorted(graph.get(node, ())):
            cycle = dfs(target, path)
            if cycle:
                return cycle
        path.pop()
        visiting.remove(node)
        visited.add(node)
        return []

    for node in sorted(graph):
        cycle = dfs(node, [])
        if cycle:
            return cycle
    return []


def check_plan_validity(
    plan: JointPlan,
    runtime: PlanRuntimeState,
    state: WorldState,
    registry: ToolRegistry,
    *,
    permissions_by_actor: Optional[Mapping[str, Iterable[str]]] = None,
    relevant_diff: Optional[StateDiff] = None,
) -> PlanValidityResult:
    cycle = find_deadlock(build_wait_graph(plan, runtime))
    if cycle:
        return PlanValidityResult(
            status=PlanValidityStatus.deadlocked,
            reasons=["PLAN_DEADLOCK"],
            relevant_diff=relevant_diff,
            deadlock_cycle=cycle,
        )
    permissions = permissions_by_actor or {}
    reasons: List[str] = []
    for actor_id, chain in plan.actor_chains.items():
        pointer = runtime.actor_step_pointers.get(actor_id, 0)
        for step in chain.steps[pointer:]:
            if isinstance(step, ActionStep):
                reasons.extend(
                    _permanent_action_failures(
                        step,
                        state,
                        registry,
                        permissions.get(actor_id, ()),
                    )
                )
            elif isinstance(step, WaitStateStep):
                reason = _unsatisfiable_condition_reason(step.condition, state)
                if reason:
                    reasons.append(reason)
    reasons = list(dict.fromkeys(reasons))
    return PlanValidityResult(
        status=(PlanValidityStatus.stale if reasons else PlanValidityStatus.valid),
        reasons=reasons,
        relevant_diff=relevant_diff,
    )


def reconcile_runtime(
    plan: JointPlan,
    runtime: PlanRuntimeState,
    committed_events: Sequence[WorldEvent],
) -> PlanRuntimeState:
    """Advance persisted pointers for action IDs already present in the event log.

    This closes the small crash window between authoritative event commit and
    plan-runtime persistence, preventing a tool call from being executed twice.
    """

    updated = runtime.copy(deep=True)
    committed_call_ids = {
        event.action_id for event in committed_events if event.action_id
    }
    for actor_id, chain in plan.actor_chains.items():
        while True:
            pointer = updated.actor_step_pointers.get(actor_id, 0)
            if pointer >= len(chain.steps):
                break
            step = chain.steps[pointer]
            if not isinstance(step, ActionStep):
                break
            if step.tool_call.call_id not in committed_call_ids:
                break
            _complete_current_step(plan, updated, actor_id)
    updated.observed_world_version = max(
        [updated.observed_world_version]
        + [event.new_version for event in committed_events]
    )
    if _all_chains_completed(plan, updated):
        updated.status = PlanRuntimeStatus.completed
    return updated


class JointPlanExecutor:
    """Run one action per ready actor and one bounded replan per trigger."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        execution_machine: Optional[AgentExecutionStateMachine] = None,
    ) -> None:
        self.registry = registry
        self.execution_machine = execution_machine or AgentExecutionStateMachine(
            registry,
            max_replans=0,
        )

    async def tick(
        self,
        plan: JointPlan,
        runtime: PlanRuntimeState,
        state: WorldState,
        *,
        permissions_by_actor: Optional[Mapping[str, Iterable[str]]] = None,
        replan: Optional[JointReplanCallback] = None,
        store: Optional[Any] = None,
        session_id: Optional[str] = None,
        previous_state: Optional[WorldState] = None,
    ) -> JointPlanTickResult:
        if runtime.plan_id != plan.plan_id:
            raise ValueError("runtime plan_id does not match JointPlan")
        if (store is None) != (session_id is None):
            raise ValueError("store and session_id must be provided together")
        permissions = permissions_by_actor or {}
        active_runtime = runtime.copy(deep=True)
        active_plan = plan.copy(deep=True)
        if store is not None:
            active_runtime = reconcile_runtime(
                active_plan,
                active_runtime,
                store.list_events(session_id),
            )
        if active_runtime.status == PlanRuntimeStatus.completed:
            return JointPlanTickResult(
                plan=active_plan,
                runtime=active_runtime,
                state=state,
            )
        active_runtime.status = PlanRuntimeStatus.active
        active_runtime.blocked_reasons = {}
        active_runtime.stale_reasons = []
        active_runtime.deadlock_cycle = []

        advances = []
        dispatches: List[ChainAdvance] = []
        for actor_id in sorted(active_plan.actor_chains):
            advance = _advance_waits(active_plan, active_runtime, actor_id, state)
            advances.append(advance)
            if advance.kind == ChainAdvanceKind.dispatch:
                dispatches.append(advance)

        relevant_diff = None
        if previous_state is not None:
            relevant_diff = build_relevant_diff(
                previous_state,
                state,
                active_runtime.dependencies,
            )
        validity = check_plan_validity(
            active_plan,
            active_runtime,
            state,
            self.registry,
            permissions_by_actor=permissions,
            relevant_diff=relevant_diff,
        )
        if not validity.valid:
            trigger = JointPlanTrigger(
                code=(
                    "PLAN_DEADLOCK"
                    if validity.status == PlanValidityStatus.deadlocked
                    else "PLAN_STALE"
                ),
                reasons=validity.reasons,
                affected_actor_ids=_affected_actors(
                    active_plan,
                    validity.reasons,
                    validity.deadlock_cycle,
                ),
                deadlock_cycle=validity.deadlock_cycle,
                relevant_diff=relevant_diff,
            )
            return await self._handle_trigger(
                active_plan,
                active_runtime,
                state,
                trigger,
                replan,
                advances=advances,
                store=store,
                session_id=session_id,
                permissions_by_actor=permissions,
            )

        current_state = state
        outcomes: List[AgentExecutionOutcome] = []
        events: List[WorldEvent] = []
        for dispatch in dispatches:
            actor_id = dispatch.actor_id
            call = dispatch.tool_call
            if call is None:
                continue
            outcome = await self.execution_machine.execute(
                call,
                current_state,
                permissions=permissions.get(actor_id, ()),
                metadata={
                    "decision_source": "joint_plan",
                    "joint_plan_id": active_plan.plan_id,
                    "joint_plan_revision": active_plan.revision,
                    "joint_step_id": dispatch.step_id,
                },
                store=store,
                session_id=session_id,
            )
            outcomes.append(outcome)
            if not outcome.result.success:
                failure = outcome.result.failure
                reason = (
                    failure.code.value if failure is not None else "execution_error"
                )
                trigger = JointPlanTrigger(
                    code="PLAN_ACTION_FAILED",
                    reasons=[reason],
                    affected_actor_ids=[actor_id],
                )
                return await self._handle_trigger(
                    active_plan,
                    active_runtime,
                    current_state,
                    trigger,
                    replan,
                    advances=advances,
                    outcomes=outcomes,
                    events=events,
                    store=store,
                    session_id=session_id,
                    permissions_by_actor=permissions,
                )
            current_state = outcome.new_state
            if outcome.event is not None:
                events.append(outcome.event)
            _complete_current_step(active_plan, active_runtime, actor_id)
            active_runtime.observed_world_version = current_state.version
            active_runtime.dependencies = extract_plan_dependencies(
                active_plan,
                active_runtime,
            )
            _save_runtime(store, session_id, active_plan, active_runtime)

        if _all_chains_completed(active_plan, active_runtime):
            active_runtime.status = PlanRuntimeStatus.completed
        active_runtime.observed_world_version = current_state.version
        _save_runtime(store, session_id, active_plan, active_runtime)
        return JointPlanTickResult(
            plan=active_plan,
            runtime=active_runtime,
            state=current_state,
            advances=advances,
            outcomes=outcomes,
            events=events,
        )

    async def _handle_trigger(
        self,
        plan: JointPlan,
        runtime: PlanRuntimeState,
        state: WorldState,
        trigger: JointPlanTrigger,
        callback: Optional[JointReplanCallback],
        *,
        advances: Optional[List[ChainAdvance]] = None,
        outcomes: Optional[List[AgentExecutionOutcome]] = None,
        events: Optional[List[WorldEvent]] = None,
        store: Optional[Any] = None,
        session_id: Optional[str] = None,
        permissions_by_actor: Optional[Mapping[str, Iterable[str]]] = None,
    ) -> JointPlanTickResult:
        runtime.status = (
            PlanRuntimeStatus.deadlocked
            if trigger.code == "PLAN_DEADLOCK"
            else PlanRuntimeStatus.stale
        )
        runtime.stale_reasons = list(trigger.reasons)
        runtime.deadlock_cycle = list(trigger.deadlock_cycle)
        runtime.last_trigger = trigger.code
        if callback is None or runtime.replan_count >= runtime.max_replans:
            _save_runtime(store, session_id, plan, runtime)
            return JointPlanTickResult(
                plan=plan,
                runtime=runtime,
                state=state,
                advances=list(advances or []),
                outcomes=list(outcomes or []),
                events=list(events or []),
                replan_trigger=trigger,
            )
        request = build_replan_request(plan, runtime, state, trigger)
        replacement = callback(request, state.copy(deep=True))
        if inspect.isawaitable(replacement):
            replacement = await replacement
        if replacement is None:
            runtime.status = PlanRuntimeStatus.aborted
            _save_runtime(store, session_id, plan, runtime)
            return JointPlanTickResult(
                plan=plan,
                runtime=runtime,
                state=state,
                advances=list(advances or []),
                outcomes=list(outcomes or []),
                events=list(events or []),
                replan_trigger=trigger,
            )
        validate_joint_plan(
            replacement,
            state,
            self.registry,
            permissions_by_actor=permissions_by_actor,
            enforce_shared_scope=False,
        )
        revised_plan, revised_runtime = splice_remaining_chains(
            plan,
            runtime,
            replacement,
            trigger.affected_actor_ids,
            world_version=state.version,
        )
        _save_runtime(store, session_id, revised_plan, revised_runtime)
        return JointPlanTickResult(
            plan=revised_plan,
            runtime=revised_runtime,
            state=state,
            advances=list(advances or []),
            outcomes=list(outcomes or []),
            events=list(events or []),
            replanned=True,
            replan_trigger=trigger,
        )


class PolicyJointReplanner:
    """Adapt existing actor-scoped ReAct policies to local joint-plan repair."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy_by_actor: Mapping[str, Any],
        *,
        world_package_id: str = "",
        scenario_family: str = "",
    ) -> None:
        self.registry = registry
        self.policy_by_actor = dict(policy_by_actor)
        self.world_package_id = world_package_id
        self.scenario_family = scenario_family

    def __call__(
        self,
        request: ReplanRequest,
        state: WorldState,
    ) -> JointPlan:
        chains: Dict[str, ActorActionChain] = {}
        definitions = tuple(
            definition
            for name in self.registry.names()
            for definition in [self.registry.get(name)]
            if definition is not None
        )
        for actor_id in request.affected_actor_ids:
            policy = self.policy_by_actor.get(actor_id)
            if policy is None:
                raise ValueError("no replan policy registered for %s" % actor_id)
            feedback = PlannerFeedback(
                success=False,
                failure_code=(request.failure_codes[0] if request.failure_codes else None),
                summary="; ".join(request.failure_codes),
            )
            observation = build_game_observation(
                state,
                actor_id,
                self.registry,
                world_package_id=self.world_package_id,
                scenario_family=self.scenario_family,
                feedback=feedback,
                metadata={
                    "replan_of": request.original_plan_id,
                    "replan_revision": request.revision,
                },
            )
            actor_definitions = tuple(
                definition
                for definition in definitions
                if definition.name in {tool.name for tool in observation.available_tools}
            )
            replan_method = getattr(policy, "replan", None)
            if callable(replan_method):
                decision = replan_method(observation, actor_definitions, feedback)
            else:
                decision = policy.decide(observation, actor_definitions)
            if decision.tool_call is None:
                chains[actor_id] = ActorActionChain(actor_id=actor_id, steps=[])
            else:
                chains[actor_id] = ActorActionChain(
                    actor_id=actor_id,
                    steps=[
                        ActionStep(
                            step_id="replan_%s_%s"
                            % (request.revision + 1, actor_id),
                            tool_call=decision.tool_call,
                        )
                    ],
                )
        return JointPlan(
            plan_id=uuid4().hex,
            goal_id=request.goal_id,
            base_world_version=state.version,
            actor_chains=chains,
            revision=request.revision + 1,
            parent_plan_id=request.original_plan_id,
            metadata={"source": "actor_scoped_policy_replan"},
        )


def build_replan_request(
    plan: JointPlan,
    runtime: PlanRuntimeState,
    state: WorldState,
    trigger: JointPlanTrigger,
) -> ReplanRequest:
    remaining = {}
    for actor_id in trigger.affected_actor_ids:
        chain = plan.actor_chains[actor_id]
        pointer = runtime.actor_step_pointers.get(actor_id, 0)
        remaining[actor_id] = ActorActionChain(
            actor_id=actor_id,
            steps=chain.steps[pointer:],
        )
    return ReplanRequest(
        original_plan_id=plan.plan_id,
        goal_id=plan.goal_id,
        revision=plan.revision,
        world_version=state.version,
        affected_actor_ids=list(trigger.affected_actor_ids),
        completed_steps={
            actor_id: list(steps)
            for actor_id, steps in runtime.completed_steps.items()
        },
        remaining_chains=remaining,
        failure_codes=list(trigger.reasons),
        relevant_diff=trigger.relevant_diff,
    )


def splice_remaining_chains(
    original: JointPlan,
    runtime: PlanRuntimeState,
    replacement: JointPlan,
    affected_actor_ids: Sequence[str],
    *,
    world_version: int,
) -> Tuple[JointPlan, PlanRuntimeState]:
    affected = set(affected_actor_ids)
    if set(replacement.actor_chains) != affected:
        raise ValueError("replacement must contain exactly the affected actor chains")
    chains = {
        actor_id: chain.copy(deep=True)
        for actor_id, chain in original.actor_chains.items()
    }
    for actor_id in affected:
        chains[actor_id] = replacement.actor_chains[actor_id].copy(deep=True)
    revised = JointPlan(
        plan_id=replacement.plan_id,
        goal_id=original.goal_id,
        base_world_version=world_version,
        actor_chains=chains,
        revision=max(original.revision + 1, replacement.revision),
        parent_plan_id=original.plan_id,
        metadata={
            **original.metadata,
            **replacement.metadata,
            "replanned_actor_ids": sorted(affected),
        },
    )
    pointers = {
        actor_id: (
            0 if actor_id in affected else runtime.actor_step_pointers.get(actor_id, 0)
        )
        for actor_id in chains
    }
    revised_runtime = PlanRuntimeState(
        plan_id=revised.plan_id,
        base_world_version=world_version,
        observed_world_version=world_version,
        dependencies=PlanDependency(),
        actor_step_pointers=pointers,
        completed_steps={
            actor_id: list(runtime.completed_steps.get(actor_id, []))
            for actor_id in chains
        },
        status=PlanRuntimeStatus.active,
        replan_count=runtime.replan_count + 1,
        max_replans=runtime.max_replans,
        last_trigger=runtime.last_trigger,
    )
    revised_runtime.dependencies = extract_plan_dependencies(
        revised,
        revised_runtime,
    )
    return revised, revised_runtime


def _advance_waits(
    plan: JointPlan,
    runtime: PlanRuntimeState,
    actor_id: str,
    state: WorldState,
) -> ChainAdvance:
    chain = plan.actor_chains[actor_id]
    while True:
        pointer = runtime.actor_step_pointers.get(actor_id, 0)
        if pointer >= len(chain.steps):
            runtime.blocked_reasons.pop(actor_id, None)
            return ChainAdvance(actor_id=actor_id, kind=ChainAdvanceKind.completed)
        step = chain.steps[pointer]
        if isinstance(step, ActionStep):
            runtime.blocked_reasons.pop(actor_id, None)
            return ChainAdvance(
                actor_id=actor_id,
                kind=ChainAdvanceKind.dispatch,
                step_id=step.step_id,
                tool_call=step.tool_call,
            )
        if isinstance(step, WaitAgentStep):
            if _is_step_completed(
                runtime,
                step.target_actor_id,
                step.target_step_id,
            ):
                _complete_current_step(plan, runtime, actor_id)
                continue
            reason = "wait_agent:%s/%s" % (
                step.target_actor_id,
                step.target_step_id,
            )
        else:
            if condition_holds(step.condition, state):
                _complete_current_step(plan, runtime, actor_id)
                continue
            reason = "wait_state:%s" % step.condition.kind.value
        runtime.blocked_reasons[actor_id] = reason
        return ChainAdvance(
            actor_id=actor_id,
            kind=ChainAdvanceKind.blocked,
            step_id=step.step_id,
            reason=reason,
        )


def _complete_current_step(
    plan: JointPlan,
    runtime: PlanRuntimeState,
    actor_id: str,
) -> None:
    chain = plan.actor_chains[actor_id]
    pointer = runtime.actor_step_pointers.get(actor_id, 0)
    if pointer >= len(chain.steps):
        return
    step_id = chain.steps[pointer].step_id
    completed = runtime.completed_steps.setdefault(actor_id, [])
    if step_id not in completed:
        completed.append(step_id)
    runtime.actor_step_pointers[actor_id] = pointer + 1
    runtime.blocked_reasons.pop(actor_id, None)


def _is_step_completed(
    runtime: PlanRuntimeState,
    actor_id: str,
    step_id: str,
) -> bool:
    return step_id in runtime.completed_steps.get(actor_id, [])


def _all_chains_completed(plan: JointPlan, runtime: PlanRuntimeState) -> bool:
    return all(
        runtime.actor_step_pointers.get(actor_id, 0) >= len(chain.steps)
        for actor_id, chain in plan.actor_chains.items()
    )


def _affected_actors(
    plan: JointPlan,
    reasons: Sequence[str],
    deadlock_cycle: Sequence[str],
) -> List[str]:
    if deadlock_cycle:
        return list(dict.fromkeys(deadlock_cycle[:-1]))
    referenced_ids = {
        reason.split(":", 1)[1]
        for reason in reasons
        if ":" in reason and reason.split(":", 1)[1]
    }
    affected = []
    for actor_id, chain in sorted(plan.actor_chains.items()):
        if actor_id in referenced_ids or any(actor_id in reason for reason in reasons):
            affected.append(actor_id)
            continue
        serialized_steps = "\n".join(step.json() for step in chain.steps)
        if any(entity_id in serialized_steps for entity_id in referenced_ids):
            affected.append(actor_id)
    affected_set = set(affected or sorted(plan.actor_chains))
    # A chain that waits on a changed actor is also affected.  Repeat to a
    # fixed point so transitive coordination dependencies are never left
    # dangling after a local splice.
    changed = True
    while changed:
        changed = False
        for actor_id, chain in plan.actor_chains.items():
            if actor_id in affected_set:
                continue
            if any(
                isinstance(step, WaitAgentStep)
                and step.target_actor_id in affected_set
                for step in chain.steps
            ):
                affected_set.add(actor_id)
                changed = True
    return sorted(affected_set)


def _permanent_action_failures(
    step: ActionStep,
    state: WorldState,
    registry: ToolRegistry,
    permissions: Iterable[str],
) -> List[str]:
    call = step.tool_call
    reasons = []
    try:
        registry.prepare(call, state, permissions=permissions)
    except ToolExecutionError as exc:
        if exc.failure.code in {
            ToolFailureCode.unknown_tool,
            ToolFailureCode.invalid_arguments,
            ToolFailureCode.actor_not_found,
            ToolFailureCode.actor_dead,
            ToolFailureCode.permission_denied,
        }:
            reasons.append("%s:%s" % (exc.failure.code.value, call.actor_id))
    arguments = call.arguments
    for key, value in arguments.items():
        if not isinstance(value, str):
            continue
        if key in {"target_character_id", "character_id"}:
            target = state.characters.get(value)
            if target is None:
                reasons.append("missing_character:%s" % value)
            elif not target.is_alive:
                reasons.append("dead_character:%s" % value)
        elif key == "item_id":
            item = state.items.get(value)
            if item is None or item.quantity <= 0 or not item.accessible:
                reasons.append("missing_or_destroyed_item:%s" % value)
        elif key in {"destination_id", "location_id"}:
            if value not in state.locations and value not in state.characters:
                reasons.append("missing_destination:%s" % value)
        elif key == "fact_id" and value not in state.facts:
            reasons.append("missing_fact:%s" % value)
    return reasons


def _unsatisfiable_condition_reason(
    condition: PlanStepCondition,
    state: WorldState,
) -> Optional[str]:
    kind = condition.kind
    if condition.character_id:
        actor = state.characters.get(condition.character_id)
        if actor is None:
            return "missing_character:%s" % condition.character_id
        if not actor.is_alive:
            return "dead_character:%s" % condition.character_id
    if condition.source_character_id:
        actor = state.characters.get(condition.source_character_id)
        if actor is None or not actor.is_alive:
            return "missing_or_dead_character:%s" % condition.source_character_id
    if condition.target_character_id:
        actor = state.characters.get(condition.target_character_id)
        if actor is None or not actor.is_alive:
            return "missing_or_dead_character:%s" % condition.target_character_id
    if condition.item_id:
        item = state.items.get(condition.item_id)
        if item is None or item.quantity <= 0 or not item.accessible:
            return "missing_or_destroyed_item:%s" % condition.item_id
    if condition.location_id and condition.location_id not in state.locations:
        return "missing_location:%s" % condition.location_id
    if condition.fact_id and condition.fact_id not in state.facts:
        return "missing_fact:%s" % condition.fact_id
    if kind == PlanConditionKind.alliance_formed:
        for member_id in condition.member_ids:
            member = state.characters.get(member_id)
            if member is None or not member.is_alive:
                return "missing_or_dead_character:%s" % member_id
    return None


def _validate_shared_scope(plan: JointPlan, state: WorldState) -> None:
    actor_ids = set(plan.actor_chains)
    for alliance in state.alliances.values():
        if (
            alliance.status == "active"
            and alliance.goal_key == plan.goal_id
            and actor_ids.issubset(set(alliance.member_ids))
        ):
            return
    for actor_id in actor_ids:
        psyche = state.character_psyches.get(actor_id)
        if psyche is None or not any(
            goal.status == "active"
            and not goal.achieved
            and plan.goal_id in {goal.goal_id, goal.goal_key}
            for goal in psyche.goals
        ):
            raise ValueError(
                "joint-plan actors must share an active goal or alliance: %s"
                % plan.goal_id
            )


def _collect_argument_dependencies(
    arguments: Mapping[str, Any],
    dependencies: PlanDependency,
) -> None:
    for key, value in arguments.items():
        if not isinstance(value, str):
            continue
        if key in {"target_character_id", "character_id"}:
            dependencies.actor_ids.add(value)
        elif key == "item_id":
            dependencies.item_ids.add(value)
        elif key in {"location_id", "destination_id"}:
            dependencies.location_ids.add(value)
        elif key in {"fact_id", "shared_fact_id"}:
            dependencies.fact_ids.add(value)


def _collect_condition_dependencies(
    condition: PlanStepCondition,
    dependencies: PlanDependency,
) -> None:
    for actor_id in (
        condition.actor_id,
        condition.character_id,
        condition.source_character_id,
        condition.target_character_id,
    ):
        if actor_id:
            dependencies.actor_ids.add(actor_id)
    if condition.item_id:
        dependencies.item_ids.add(condition.item_id)
    if condition.location_id:
        dependencies.location_ids.add(condition.location_id)
    if condition.fact_id:
        dependencies.fact_ids.add(condition.fact_id)
    if condition.shared_fact_id:
        dependencies.fact_ids.add(condition.shared_fact_id)


def _item_holder(item: Any) -> Optional[str]:
    if item is None:
        return None
    return item.owner_id or item.location_id or "destroyed"


def _belief_signature(
    state: WorldState,
    actor_id: str,
    fact_ids: Set[str],
) -> Dict[str, Tuple[str, float]]:
    return {
        belief.fact_id: (belief.belief.value, belief.confidence)
        for belief in state.beliefs.get(actor_id, [])
        if not fact_ids or belief.fact_id in fact_ids
    }


def _relation_signatures(state: WorldState) -> Dict[Tuple[str, str], Dict[str, Any]]:
    return {
        (relation.source_id, relation.target_id): relation.dimensions.dict()
        for relation in state.relations
    }


def _save_runtime(
    store: Optional[Any],
    session_id: Optional[str],
    plan: JointPlan,
    runtime: PlanRuntimeState,
) -> None:
    if store is None:
        return
    save = getattr(store, "save_joint_plan_runtime", None)
    if not callable(save):
        raise TypeError("store does not support joint-plan runtime persistence")
    save(session_id, plan, runtime)
