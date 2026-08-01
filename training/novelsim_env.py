"""Resettable authoritative environment for NovelSim planner RL.

The environment never interprets model text as a state patch.  A valid
``PlannerDecision`` may only propose a registered ``ToolCall``; the existing
ToolRegistry, execution state machine, patch validator and event commit path
remain authoritative.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, root_validator, validator

from engine import (
    CORE_TOOL_PERMISSIONS,
    AgentExecutionStateMachine,
    FailureAttribution,
    FailureCategory,
    FailureLabel,
    GameObservation,
    PlannerDecision,
    PlannerFeedback,
    RewardBreakdown,
    ToolResult,
    attribute_failure,
    authoritative_state_hash,
    build_game_observation,
    create_core_tool_registry,
)
from engine.event import replay_events
from world_schema import WorldEvent, WorldState

from .scenario_generator import (
    GeneratedScenario,
    ScenarioFamily,
    evaluate_scenario,
    generate_scenario,
    scenario_progress,
)


class NovelSimEnvSpec(BaseModel):
    """Everything required to reproduce one planner decision state."""

    schema_version: str = "novelsim_env_spec.v1"
    scenario_family: ScenarioFamily
    variant_index: int = Field(ge=0)
    random_seed: int
    scenario_content_hash: str
    actor_id: str
    prior_events: Tuple[WorldEvent, ...] = ()
    starting_state_hash: str
    feedback: Optional[PlannerFeedback] = None
    max_steps: int = Field(1, ge=1, le=100)

    class Config:
        extra = "forbid"
        allow_mutation = False

    @validator(
        "scenario_content_hash",
        "actor_id",
        "starting_state_hash",
    )
    def _non_empty(cls, value):
        if not value.strip():
            raise ValueError("environment identity fields cannot be empty")
        return value

    @validator("schema_version")
    def _known_schema(cls, value):
        if value != "novelsim_env_spec.v1":
            raise ValueError("unsupported NovelSim environment schema")
        return value

    @root_validator(skip_on_failure=True)
    def _event_versions_are_contiguous(cls, values):
        events = list(values.get("prior_events") or [])
        expected = 0
        for event in events:
            if event.previous_version != expected or event.new_version != expected + 1:
                raise ValueError("environment prior event versions are not contiguous")
            expected += 1
        return values


class NovelSimTransition(BaseModel):
    schema_version: str = "novelsim_transition.v1"
    step_index: int = Field(ge=0)
    observation: GameObservation
    decision: PlannerDecision
    tool_result: Optional[ToolResult] = None
    committed_event: Optional[WorldEvent] = None
    previous_state_hash: str
    next_state_hash: str
    objective_progress_before: float = Field(ge=0.0, le=1.0)
    objective_progress_after: float = Field(ge=0.0, le=1.0)
    objective_satisfied: bool = False
    objective_relevant: bool = False
    repeated_action: bool = False
    no_progress: bool = False
    reward: RewardBreakdown
    scalar_reward: float
    reward_profile: str
    failure: FailureAttribution
    terminated: bool = False
    truncated: bool = False

    class Config:
        extra = "forbid"
        allow_mutation = False


class NovelSimEnv:
    """Small stateful environment backed by the production rule engine."""

    def __init__(self, spec: NovelSimEnvSpec, *, reward_profile: str = "mixed"):
        if reward_profile not in {"mixed", "objective_only"}:
            raise ValueError("reward_profile must be mixed or objective_only")
        self.spec = spec
        self.reward_profile = reward_profile
        self.registry = create_core_tool_registry()
        self.runtime = AgentExecutionStateMachine(
            self.registry,
            max_retries=0,
            max_replans=0,
        )
        self.scenario: Optional[GeneratedScenario] = None
        self.state: Optional[WorldState] = None
        self.step_count = 0
        self.done = False
        self._action_history: List[str] = []
        self._initial_hash = ""

    @property
    def initial_state_hash(self) -> str:
        return self._initial_hash

    def reset(self) -> GameObservation:
        scenario = generate_scenario(
            self.spec.scenario_family,
            variant_index=self.spec.variant_index,
            seed=self.spec.random_seed,
        )
        if scenario.content_hash != self.spec.scenario_content_hash:
            raise ValueError("environment scenario content hash mismatch")
        state = replay_events(
            scenario.initial_state,
            list(self.spec.prior_events),
        )
        state_hash = authoritative_state_hash(state)
        if state_hash != self.spec.starting_state_hash:
            raise ValueError("environment starting state hash mismatch")
        if self.spec.actor_id not in state.characters:
            raise ValueError("environment actor does not exist")
        self.scenario = scenario
        self.state = state.copy(deep=True)
        self.step_count = 0
        self.done = False
        self._action_history = []
        self._initial_hash = state_hash
        return self.observe()

    def observe(self) -> GameObservation:
        if self.scenario is None or self.state is None:
            raise RuntimeError("environment must be reset before observe")
        return build_game_observation(
            self.state,
            self.spec.actor_id,
            self.registry,
            world_package_id=self.scenario.world_package_id,
            scenario_family=self.scenario.scenario_family.value,
            feedback=self.spec.feedback if self.step_count == 0 else None,
        )

    async def step_async(self, decision: PlannerDecision) -> NovelSimTransition:
        if self.scenario is None or self.state is None:
            raise RuntimeError("environment must be reset before step")
        if self.done:
            raise RuntimeError("environment episode is already finished")
        observation = self.observe()
        if decision.actor_id != observation.actor_id:
            raise ValueError("decision actor does not match environment actor")
        previous_hash = authoritative_state_hash(self.state)
        progress_before = scenario_progress(self.scenario, self.state)
        fingerprint = _decision_fingerprint(decision)
        repeated = fingerprint in self._action_history
        self._action_history.append(fingerprint)

        outcome = None
        tool_result = None
        committed_event = None
        next_state = self.state.copy(deep=True)
        if decision.tool_call is not None:
            outcome = await self.runtime.execute(
                decision.tool_call,
                self.state,
                permissions=CORE_TOOL_PERMISSIONS,
                metadata={"decision_source": "grpo_environment"},
            )
            tool_result = outcome.result
            committed_event = outcome.event
            next_state = outcome.new_state.copy(deep=True)

        next_hash = authoritative_state_hash(next_state)
        progress_after = scenario_progress(self.scenario, next_state)
        objective_satisfied = evaluate_scenario(self.scenario, next_state) is not None
        objective_relevant = _is_objective_relevant(
            self.scenario,
            decision,
            progress_before=progress_before,
            progress_after=progress_after,
        )
        no_progress = decision.tool_call is None or (
            outcome is not None
            and outcome.result.success
            and next_hash == previous_hash
        )
        failure = _transition_failure(
            observation,
            decision,
            outcome,
            repeated_action=repeated,
            no_progress=no_progress,
            objective_relevant=objective_relevant,
        )
        from .rewards import score_transition_reward

        reward, scalar = score_transition_reward(
            observation=observation,
            decision=decision,
            outcome=outcome,
            failure=failure,
            progress_before=progress_before,
            progress_after=progress_after,
            objective_satisfied=objective_satisfied,
            objective_relevant=objective_relevant,
            repeated_action=repeated,
            no_progress=no_progress,
            reward_profile=self.reward_profile,
        )
        self.state = next_state
        self.step_count += 1
        truncated = self.step_count >= self.spec.max_steps and not objective_satisfied
        self.done = objective_satisfied or truncated
        return NovelSimTransition(
            step_index=self.step_count - 1,
            observation=observation,
            decision=decision,
            tool_result=tool_result,
            committed_event=committed_event,
            previous_state_hash=previous_hash,
            next_state_hash=next_hash,
            objective_progress_before=progress_before,
            objective_progress_after=progress_after,
            objective_satisfied=objective_satisfied,
            objective_relevant=objective_relevant,
            repeated_action=repeated,
            no_progress=no_progress,
            reward=reward,
            scalar_reward=scalar,
            reward_profile=self.reward_profile,
            failure=failure,
            terminated=objective_satisfied,
            truncated=truncated,
        )

    def step(self, decision: PlannerDecision) -> NovelSimTransition:
        """Synchronous convenience API for collectors and audits."""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.step_async(decision))
        raise RuntimeError("use await step_async() inside an active event loop")

    @classmethod
    def reset_group(
        cls,
        spec: NovelSimEnvSpec,
        group_size: int,
        *,
        reward_profile: str = "mixed",
    ) -> Tuple["NovelSimEnv", ...]:
        """Create isolated group members and prove their initial hashes match."""

        if group_size < 2:
            raise ValueError("GRPO group_size must be at least 2")
        environments = tuple(
            cls(spec, reward_profile=reward_profile) for _ in range(group_size)
        )
        hashes = []
        for environment in environments:
            environment.reset()
            hashes.append(environment.initial_state_hash)
        if len(set(hashes)) != 1 or hashes[0] != spec.starting_state_hash:
            raise RuntimeError("GRPO group did not share an identical initial state")
        return environments


def _decision_fingerprint(decision: PlannerDecision) -> str:
    call = decision.tool_call
    payload: Dict[str, Any] = {
        "actor_id": decision.actor_id,
        "intent": decision.intent.value,
        "tool_name": call.tool_name if call is not None else None,
        "arguments": call.arguments if call is not None else {},
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _transition_failure(
    observation: GameObservation,
    decision: PlannerDecision,
    outcome,
    *,
    repeated_action: bool,
    no_progress: bool,
    objective_relevant: bool,
) -> FailureAttribution:
    if outcome is None:
        base = FailureAttribution()
    else:
        base = attribute_failure(decision, outcome)
    labels = list(base.labels)
    evidence_set = set(observation.evidence_ids)
    invalid_evidence = sorted(set(decision.evidence_ids) - evidence_set)
    goal_ids = {goal.goal_id for goal in observation.goals}
    invalid_goal = bool(decision.goal_id and decision.goal_id not in goal_ids)
    if invalid_evidence:
        labels.append(FailureLabel.evidence_mismatch)
    if invalid_goal:
        labels.append(FailureLabel.persona_goal_conflict)
    if repeated_action:
        labels.append(FailureLabel.repeated_loop)
    if no_progress:
        labels.append(FailureLabel.no_progress)
    objective_abandonment = (
        decision.tool_call is not None
        and outcome is not None
        and outcome.result.success
        and not objective_relevant
    )
    if objective_abandonment:
        labels.append(FailureLabel.objective_abandonment)
    labels = list(dict.fromkeys(labels))
    primary = base.primary_label
    category = base.category
    if primary == FailureLabel.none and invalid_evidence:
        primary = FailureLabel.evidence_mismatch
        category = FailureCategory.cognitive_integrity
    elif primary == FailureLabel.none and invalid_goal:
        primary = FailureLabel.persona_goal_conflict
        category = FailureCategory.cognitive_integrity
    elif primary == FailureLabel.none and repeated_action:
        primary = FailureLabel.repeated_loop
        category = FailureCategory.trajectory_regulation
    elif primary == FailureLabel.none and no_progress:
        primary = FailureLabel.no_progress
        category = FailureCategory.trajectory_regulation
    elif primary == FailureLabel.none and objective_abandonment:
        primary = FailureLabel.objective_abandonment
        category = FailureCategory.trajectory_regulation
    evidence = dict(base.evidence)
    evidence.update({
        "invalid_evidence_ids": invalid_evidence,
        "invalid_goal_id": decision.goal_id if invalid_goal else None,
        "repeated_action": repeated_action,
        "no_progress": no_progress,
        "objective_relevant": objective_relevant,
    })
    return base.copy(update={
        "category": category,
        "primary_label": primary,
        "labels": labels,
        "reason": base.reason or (
            "deterministic trajectory/cognitive audit detected a violation"
            if labels else ""
        ),
        "evidence": evidence,
    })


def _is_objective_relevant(
    scenario: GeneratedScenario,
    decision: PlannerDecision,
    *,
    progress_before: float,
    progress_after: float,
) -> bool:
    """Check goal relevance without requiring one exact expert action."""

    if progress_after > progress_before:
        return True
    call = decision.tool_call
    if call is None:
        return False
    relevant_tools = {
        ScenarioFamily.secret_transport: {
            "pick_up", "give_item", "observe", "share_information", "propose_alliance"
        },
        ScenarioFamily.resource_negotiation: {"pick_up", "talk_to", "give_item"},
        ScenarioFamily.rescue_escort: {"pick_up", "move_to", "talk_to", "give_item"},
    }[scenario.scenario_family]
    if call.tool_name not in relevant_tools:
        return False
    relevant_values = set()
    for scripted_call in scenario.scripted_calls:
        relevant_values.update(_scalar_argument_values(scripted_call.arguments))
    for condition in scenario.success_conditions:
        relevant_values.update({
            condition.entity_id,
            condition.expected_id,
            condition.fact_id,
            condition.flag_key,
        })
        relevant_values.update(condition.member_ids)
    relevant_values.discard("")
    return bool(
        _scalar_argument_values(call.arguments) & relevant_values
    )


def _scalar_argument_values(value: Any) -> set:
    if isinstance(value, dict):
        result = set()
        for nested in value.values():
            result.update(_scalar_argument_values(nested))
        return result
    if isinstance(value, (list, tuple)):
        result = set()
        for nested in value:
            result.update(_scalar_argument_values(nested))
        return result
    if isinstance(value, (str, int, float, bool)):
        return {str(value)}
    return set()
