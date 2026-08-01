"""Replayable training/evaluation trajectories for high-level planners."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, root_validator, validator

from world_schema import WorldEvent, WorldState

from .agent_runtime import AgentExecutionOutcome
from .agent_tools import ToolFailureCode, ToolResult
from .agent_trace import AgentTrace
from .event import replay_events
from .game_observation import GameObservation, authoritative_state_hash
from .planner_decision import PlannerDecision


class FailureCategory(str, Enum):
    none = "none"
    environment_contract = "environment_contract"
    action_realization = "action_realization"
    trajectory_regulation = "trajectory_regulation"
    cognitive_integrity = "cognitive_integrity"
    runtime_internal = "runtime_internal"


class FailureLabel(str, Enum):
    none = "none"
    invalid_schema = "invalid_schema"
    unknown_entity = "unknown_entity"
    unavailable_world_concept = "unavailable_world_concept"
    missing_capability = "missing_capability"
    missing_affordance = "missing_affordance"
    navigation_failed = "navigation_failed"
    tool_precondition_failed = "tool_precondition_failed"
    tool_timeout = "tool_timeout"
    patch_rejected = "patch_rejected"
    version_conflict = "version_conflict"
    repeated_loop = "repeated_loop"
    no_progress = "no_progress"
    retry_exhausted = "retry_exhausted"
    premature_termination = "premature_termination"
    objective_abandonment = "objective_abandonment"
    knowledge_leak = "knowledge_leak"
    unsupported_belief = "unsupported_belief"
    evidence_mismatch = "evidence_mismatch"
    persona_goal_conflict = "persona_goal_conflict"
    execution_error = "execution_error"


class RewardBreakdown(BaseModel):
    """Auditable first-version reward described in the V2 plan."""

    objective_progress: float = Field(0.0, ge=-1.0, le=1.0)
    tool_execution: float = Field(0.0, ge=-1.0, le=1.0)
    causal_grounding: float = Field(0.0, ge=-1.0, le=1.0)
    character_consistency: float = Field(0.0, ge=-1.0, le=1.0)
    information_integrity: float = Field(0.0, ge=-1.0, le=1.0)
    recovery_quality: float = Field(0.0, ge=-1.0, le=1.0)
    action_efficiency: float = Field(0.0, ge=-1.0, le=1.0)
    terminal_outcome: float = Field(0.0, ge=-1.0, le=1.0)
    penalties: Dict[str, float] = Field(default_factory=dict)
    total: Optional[float] = None

    class Config:
        extra = "forbid"
        allow_mutation = False

    @validator("penalties")
    def _penalties_are_non_negative(cls, value):
        if any(amount < 0 for amount in value.values()):
            raise ValueError("reward penalties must be non-negative")
        return value

    @root_validator(skip_on_failure=True)
    def _total_matches_components(cls, values):
        calculated = round(
            0.35 * values.get("objective_progress", 0.0)
            + 0.15 * values.get("tool_execution", 0.0)
            + 0.15 * values.get("causal_grounding", 0.0)
            + 0.10 * values.get("character_consistency", 0.0)
            + 0.10 * values.get("information_integrity", 0.0)
            + 0.05 * values.get("recovery_quality", 0.0)
            + 0.05 * values.get("action_efficiency", 0.0)
            + 0.05 * values.get("terminal_outcome", 0.0)
            - sum(values.get("penalties", {}).values()),
            6,
        )
        supplied = values.get("total")
        if supplied is not None and abs(supplied - calculated) > 1e-6:
            raise ValueError("reward total does not match reward components")
        values["total"] = calculated
        return values


class FailureAttribution(BaseModel):
    category: FailureCategory = FailureCategory.none
    primary_label: FailureLabel = FailureLabel.none
    labels: List[FailureLabel] = Field(default_factory=list)
    stage: str = ""
    source_failure_code: Optional[str] = None
    reason: str = ""
    retryable: bool = False
    illegal_proposal: bool = False
    illegal_commit: bool = False
    evidence: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"
        allow_mutation = False

    @root_validator(skip_on_failure=True)
    def _primary_label_is_in_labels(cls, values):
        primary = values.get("primary_label")
        labels = list(values.get("labels") or [])
        if primary != FailureLabel.none and primary not in labels:
            labels.insert(0, primary)
        if primary == FailureLabel.none and labels:
            values["primary_label"] = labels[0]
        values["labels"] = list(dict.fromkeys(labels))
        return values


class ValidationRecord(BaseModel):
    accepted: bool
    stage: str = ""
    code: Optional[str] = None
    message: str = ""
    details: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"
        allow_mutation = False


class PlannerUsage(BaseModel):
    model_id: str = ""
    prompt_version: str = ""
    prompt_tokens: int = Field(0, ge=0)
    completion_tokens: int = Field(0, ge=0)
    total_tokens: int = Field(0, ge=0)
    latency_ms: float = Field(0.0, ge=0.0)

    class Config:
        extra = "forbid"
        allow_mutation = False

    @root_validator(skip_on_failure=True)
    def _usage_total_is_consistent(cls, values):
        expected = values.get("prompt_tokens", 0) + values.get(
            "completion_tokens", 0
        )
        supplied = values.get("total_tokens", 0)
        if supplied not in (0, expected):
            raise ValueError("total_tokens must equal prompt + completion")
        values["total_tokens"] = expected
        return values


class GameTrajectoryStep(BaseModel):
    schema_version: str = "game_trajectory_step.v1"
    step_id: str
    step_index: int = Field(ge=0)
    observation: GameObservation
    decision: PlannerDecision
    schema_validation: ValidationRecord
    world_gate_result: ValidationRecord
    tool_result: ToolResult
    execution_trace: AgentTrace
    committed_event: Optional[WorldEvent] = None
    previous_state_hash: str
    next_state_hash: str
    reward: RewardBreakdown = Field(default_factory=RewardBreakdown)
    failure: FailureAttribution = Field(default_factory=FailureAttribution)
    planner_usage: PlannerUsage = Field(default_factory=PlannerUsage)

    class Config:
        extra = "forbid"
        allow_mutation = False

    @root_validator(skip_on_failure=True)
    def _step_evidence_is_consistent(cls, values):
        observation = values.get("observation")
        decision = values.get("decision")
        result = values.get("tool_result")
        event = values.get("committed_event")
        previous_hash = values.get("previous_state_hash")
        next_hash = values.get("next_state_hash")
        if observation is not None:
            if observation.authoritative_state_hash != previous_hash:
                raise ValueError("observation hash does not match previous state")
        if observation is not None and decision is not None:
            if observation.actor_id != decision.actor_id:
                raise ValueError("decision actor does not match observation actor")
        if decision is not None and result is not None:
            if decision.tool_call is None:
                raise ValueError("recorded execution requires a decision ToolCall")
            if decision.tool_call.call_id != result.call_id:
                raise ValueError("decision and ToolResult call_id mismatch")
        if event is None:
            if result is not None and result.committed_event_id is not None:
                raise ValueError("ToolResult references a missing committed event")
            if previous_hash != next_hash:
                raise ValueError("state changed without a committed event")
        else:
            if result is None or result.committed_event_id != event.event_id:
                raise ValueError("ToolResult and committed event mismatch")
        return values


class GameTrajectory(BaseModel):
    schema_version: str = "game_trajectory.v1"
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    episode_id: str
    world_package_id: str
    scenario_family: str
    variant_id: str = "default"
    random_seed: int
    policy_id: str
    model_id: str = ""
    prompt_version: str = ""
    code_commit: str = ""
    source_type: str = "runtime_rollout"
    generator_version: str = "game_trajectory.v1"
    initial_state: WorldState
    initial_state_hash: str
    steps: List[GameTrajectoryStep] = Field(default_factory=list)
    final_state_hash: str
    ending_id: str = ""
    objective_satisfied: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)
    content_hash: Optional[str] = None

    class Config:
        extra = "forbid"
        allow_mutation = False

    @root_validator(skip_on_failure=True)
    def _trajectory_chain_is_consistent(cls, values):
        initial = values.get("initial_state")
        initial_hash = values.get("initial_state_hash")
        steps = values.get("steps") or []
        final_hash = values.get("final_state_hash")
        if initial is not None and authoritative_state_hash(initial) != initial_hash:
            raise ValueError("initial_state_hash does not match initial_state")
        expected_hash = initial_hash
        expected_version = initial.version if initial is not None else None
        for index, step in enumerate(steps):
            if step.step_index != index:
                raise ValueError("trajectory step_index must be contiguous")
            if step.previous_state_hash != expected_hash:
                raise ValueError("trajectory state hash chain is broken")
            if expected_version is not None:
                if step.observation.world_version != expected_version:
                    raise ValueError("trajectory world version chain is broken")
                if step.committed_event is not None:
                    if step.committed_event.previous_version != expected_version:
                        raise ValueError("event previous_version is not contiguous")
                    expected_version = step.committed_event.new_version
            expected_hash = step.next_state_hash
        if expected_hash != final_hash:
            raise ValueError("final_state_hash does not match last step")
        supplied_content_hash = values.get("content_hash")
        values["content_hash"] = None
        calculated = _trajectory_hash_from_values(values)
        if supplied_content_hash not in (None, calculated):
            raise ValueError("trajectory content_hash mismatch")
        values["content_hash"] = calculated
        return values


class TrajectoryReplayReport(BaseModel):
    episode_id: str
    consistent: bool
    event_count: int = Field(ge=0)
    expected_final_state_hash: str
    actual_final_state_hash: str
    errors: List[str] = Field(default_factory=list)

    class Config:
        extra = "forbid"


class GameTrajectoryRecorder:
    """Collect immutable runtime evidence into one self-contained episode."""

    def __init__(
        self,
        initial_state: WorldState,
        *,
        episode_id: str,
        world_package_id: str,
        scenario_family: str,
        random_seed: int,
        policy_id: str,
        variant_id: str = "default",
        run_id: Optional[str] = None,
        model_id: str = "",
        prompt_version: str = "",
        code_commit: str = "",
        source_type: str = "runtime_rollout",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.initial_state = initial_state.copy(deep=True)
        self.current_state = initial_state.copy(deep=True)
        self.episode_id = episode_id
        self.world_package_id = world_package_id
        self.scenario_family = scenario_family
        self.random_seed = random_seed
        self.policy_id = policy_id
        self.variant_id = variant_id
        self.run_id = run_id or uuid4().hex
        self.model_id = model_id
        self.prompt_version = prompt_version
        self.code_commit = code_commit
        self.source_type = source_type
        self.metadata = dict(metadata or {})
        self.steps: List[GameTrajectoryStep] = []

    def record(
        self,
        observation: GameObservation,
        decision: PlannerDecision,
        outcome: AgentExecutionOutcome,
        *,
        reward: Optional[RewardBreakdown] = None,
        failure: Optional[FailureAttribution] = None,
        planner_usage: Optional[PlannerUsage] = None,
    ) -> GameTrajectoryStep:
        previous_hash = authoritative_state_hash(self.current_state)
        if observation.authoritative_state_hash != previous_hash:
            raise ValueError("observation does not describe recorder current state")
        if outcome.execution.active_call.call_id != decision.tool_call.call_id:
            raise ValueError("execution does not match planner decision")
        next_hash = authoritative_state_hash(outcome.new_state)
        attribution = failure or attribute_failure(decision, outcome)
        validation = validation_record(outcome.result)
        step = GameTrajectoryStep(
            step_id="%s:%06d" % (self.episode_id, len(self.steps)),
            step_index=len(self.steps),
            observation=observation,
            decision=decision,
            schema_validation=validation,
            world_gate_result=validation,
            tool_result=outcome.result,
            execution_trace=outcome.trace,
            committed_event=outcome.event,
            previous_state_hash=previous_hash,
            next_state_hash=next_hash,
            reward=reward or default_reward(outcome, attribution),
            failure=attribution,
            planner_usage=planner_usage or PlannerUsage(
                latency_ms=outcome.trace.duration_ms
            ),
        )
        self.steps.append(step)
        self.current_state = outcome.new_state.copy(deep=True)
        return step

    def finish(
        self,
        *,
        ending_id: str,
        objective_satisfied: bool,
    ) -> GameTrajectory:
        return GameTrajectory(
            run_id=self.run_id,
            episode_id=self.episode_id,
            world_package_id=self.world_package_id,
            scenario_family=self.scenario_family,
            variant_id=self.variant_id,
            random_seed=self.random_seed,
            policy_id=self.policy_id,
            model_id=self.model_id,
            prompt_version=self.prompt_version,
            code_commit=self.code_commit,
            source_type=self.source_type,
            initial_state=self.initial_state,
            initial_state_hash=authoritative_state_hash(self.initial_state),
            steps=list(self.steps),
            final_state_hash=authoritative_state_hash(self.current_state),
            ending_id=ending_id,
            objective_satisfied=objective_satisfied,
            metadata=dict(self.metadata),
        )


def attribute_failure(
    decision: PlannerDecision,
    outcome: AgentExecutionOutcome,
) -> FailureAttribution:
    failure = outcome.result.failure
    illegal_commit = failure is not None and outcome.event is not None
    if failure is None:
        return FailureAttribution(illegal_commit=illegal_commit)
    category, label = _FAILURE_MAP.get(
        failure.code,
        (FailureCategory.runtime_internal, FailureLabel.execution_error),
    )
    illegal_proposal = failure.code in _ILLEGAL_PROPOSAL_CODES
    return FailureAttribution(
        category=category,
        primary_label=label,
        labels=[label],
        stage=failure.stage,
        source_failure_code=failure.code.value,
        reason=failure.message,
        retryable=failure.retryable,
        illegal_proposal=illegal_proposal,
        illegal_commit=illegal_commit,
        evidence={
            "decision_id": decision.decision_id,
            "call_id": outcome.result.call_id,
            "world_version": outcome.new_state.version,
            "details": dict(failure.details),
        },
    )


def validation_record(result: ToolResult) -> ValidationRecord:
    if result.success:
        return ValidationRecord(accepted=True, stage="commit")
    failure = result.failure
    return ValidationRecord(
        accepted=False,
        stage=failure.stage if failure is not None else "unknown",
        code=failure.code.value if failure is not None else None,
        message=failure.message if failure is not None else "",
        details=dict(failure.details) if failure is not None else {},
    )


def default_reward(
    outcome: AgentExecutionOutcome,
    attribution: Optional[FailureAttribution] = None,
) -> RewardBreakdown:
    failure = attribution or FailureAttribution()
    penalties: Dict[str, float] = {}
    if failure.illegal_proposal:
        penalties["illegal_proposal"] = 0.25
    if failure.illegal_commit:
        penalties["illegal_commit"] = 1.0
    return RewardBreakdown(
        tool_execution=1.0 if outcome.result.success else -1.0,
        causal_grounding=1.0 if outcome.event is not None else 0.0,
        information_integrity=(
            -1.0
            if failure.primary_label == FailureLabel.knowledge_leak
            else 0.0
        ),
        penalties=penalties,
    )


def replay_game_trajectory(
    trajectory: GameTrajectory,
) -> TrajectoryReplayReport:
    events = [
        step.committed_event
        for step in trajectory.steps
        if step.committed_event is not None
    ]
    errors: List[str] = []
    try:
        final_state = replay_events(trajectory.initial_state, events)
        actual_hash = authoritative_state_hash(final_state)
    except Exception as exc:
        actual_hash = ""
        errors.append("%s: %s" % (type(exc).__name__, str(exc)))
    if actual_hash != trajectory.final_state_hash:
        errors.append("replayed final state hash does not match trajectory")
    return TrajectoryReplayReport(
        episode_id=trajectory.episode_id,
        consistent=not errors,
        event_count=len(events),
        expected_final_state_hash=trajectory.final_state_hash,
        actual_final_state_hash=actual_hash,
        errors=errors,
    )


def trajectory_content_hash(trajectory: GameTrajectory) -> str:
    return _canonical_hash(_semantic_content_payload(trajectory.dict()))


def _trajectory_hash_from_values(values: Dict[str, Any]) -> str:
    return _canonical_hash(_semantic_content_payload(values))


def _semantic_content_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Drop volatile run/trace telemetry from the dataset deduplication hash."""

    data = _to_plain(payload)
    return {
        "schema_version": data.get("schema_version"),
        "world_package_id": data.get("world_package_id"),
        "scenario_family": data.get("scenario_family"),
        "variant_id": data.get("variant_id"),
        "random_seed": data.get("random_seed"),
        "initial_state": data.get("initial_state"),
        "initial_state_hash": data.get("initial_state_hash"),
        "steps": [
            {
                "step_index": step.get("step_index"),
                "observation": _without_keys(
                    step.get("observation", {}),
                    {"observation_id"},
                ),
                "decision": _semantic_decision(step.get("decision", {})),
                "schema_validation": step.get("schema_validation"),
                "world_gate_result": step.get("world_gate_result"),
                "tool_result": _without_keys(
                    step.get("tool_result", {}),
                    {"call_id", "latency_ms"},
                ),
                "committed_event": _without_keys(
                    step.get("committed_event") or {},
                    {"event_id", "action_id"},
                ),
                "previous_state_hash": step.get("previous_state_hash"),
                "next_state_hash": step.get("next_state_hash"),
                "reward": step.get("reward"),
                "failure": _semantic_failure(step.get("failure", {})),
            }
            for step in data.get("steps", [])
        ],
        "final_state_hash": data.get("final_state_hash"),
        "ending_id": data.get("ending_id"),
        "objective_satisfied": data.get("objective_satisfied"),
    }


def _canonical_hash(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(
        json.loads(
            json.dumps(
                payload,
                ensure_ascii=False,
                default=_json_default,
            )
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _to_plain(value: Any):
    if isinstance(value, BaseModel):
        return _to_plain(value.dict())
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(item) for item in value]
    return value


def _without_keys(value: Dict[str, Any], ignored):
    return {
        key: item
        for key, item in _to_plain(value).items()
        if key not in ignored
    }


def _semantic_decision(value: Dict[str, Any]) -> Dict[str, Any]:
    decision = _without_keys(value, {"decision_id", "policy_id"})
    tool_call = decision.get("tool_call")
    if isinstance(tool_call, dict):
        decision["tool_call"] = _without_keys(
            tool_call,
            {"call_id", "parent_trace_id"},
        )
    return decision


def _semantic_failure(value: Dict[str, Any]) -> Dict[str, Any]:
    failure = _to_plain(value)
    evidence = failure.get("evidence")
    if isinstance(evidence, dict):
        failure["evidence"] = _without_keys(
            evidence,
            {"decision_id", "call_id"},
        )
    return failure


def _json_default(value: Any):
    if isinstance(value, BaseModel):
        return value.dict()
    if isinstance(value, Enum):
        return value.value
    raise TypeError("not JSON serializable: %s" % type(value).__name__)


_FAILURE_MAP = {
    ToolFailureCode.unknown_tool: (
        FailureCategory.environment_contract,
        FailureLabel.invalid_schema,
    ),
    ToolFailureCode.invalid_arguments: (
        FailureCategory.environment_contract,
        FailureLabel.invalid_schema,
    ),
    ToolFailureCode.actor_not_found: (
        FailureCategory.environment_contract,
        FailureLabel.unknown_entity,
    ),
    ToolFailureCode.actor_dead: (
        FailureCategory.action_realization,
        FailureLabel.tool_precondition_failed,
    ),
    ToolFailureCode.target_not_found: (
        FailureCategory.environment_contract,
        FailureLabel.unknown_entity,
    ),
    ToolFailureCode.permission_denied: (
        FailureCategory.environment_contract,
        FailureLabel.missing_capability,
    ),
    ToolFailureCode.precondition_failed: (
        FailureCategory.action_realization,
        FailureLabel.tool_precondition_failed,
    ),
    ToolFailureCode.spatial_constraint: (
        FailureCategory.action_realization,
        FailureLabel.navigation_failed,
    ),
    ToolFailureCode.cognitive_boundary: (
        FailureCategory.cognitive_integrity,
        FailureLabel.knowledge_leak,
    ),
    ToolFailureCode.patch_rejected: (
        FailureCategory.action_realization,
        FailureLabel.patch_rejected,
    ),
    ToolFailureCode.version_conflict: (
        FailureCategory.action_realization,
        FailureLabel.version_conflict,
    ),
    ToolFailureCode.timeout: (
        FailureCategory.action_realization,
        FailureLabel.tool_timeout,
    ),
    ToolFailureCode.execution_error: (
        FailureCategory.runtime_internal,
        FailureLabel.execution_error,
    ),
    ToolFailureCode.retry_exhausted: (
        FailureCategory.trajectory_regulation,
        FailureLabel.retry_exhausted,
    ),
}


_ILLEGAL_PROPOSAL_CODES = {
    ToolFailureCode.unknown_tool,
    ToolFailureCode.invalid_arguments,
    ToolFailureCode.actor_not_found,
    ToolFailureCode.actor_dead,
    ToolFailureCode.target_not_found,
    ToolFailureCode.permission_denied,
    ToolFailureCode.precondition_failed,
    ToolFailureCode.spatial_constraint,
    ToolFailureCode.cognitive_boundary,
    ToolFailureCode.patch_rejected,
}
