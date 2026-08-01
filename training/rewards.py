"""Deterministic, outcome-grounded rewards for NovelSim GRPO."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from engine import (
    FailureAttribution,
    FailureCategory,
    FailureLabel,
    GameObservation,
    PlannerDecision,
    PlannerPolicyError,
    RewardBreakdown,
    coerce_planner_decision,
)
from engine.planner_prompt import extract_json_object

from .novelsim_env import NovelSimEnv, NovelSimEnvSpec, NovelSimTransition


REWARD_PROFILES = ("objective_only", "mixed")


class CompletionRewardEvaluation(BaseModel):
    schema_version: str = "novelsim_completion_reward.v1"
    completion_sha256: str
    parsed: bool = False
    scalar_reward: float
    reward_profile: str
    reward: RewardBreakdown
    failure: FailureAttribution
    starting_state_hash: str
    next_state_hash: str
    gate_accepted: bool = False
    objective_satisfied: bool = False
    illegal_proposal: bool = False
    illegal_commit: bool = False
    transition: Optional[NovelSimTransition] = None

    class Config:
        extra = "forbid"
        allow_mutation = False


def score_transition_reward(
    *,
    observation: GameObservation,
    decision: PlannerDecision,
    outcome,
    failure: FailureAttribution,
    progress_before: float,
    progress_after: float,
    objective_satisfied: bool,
    repeated_action: bool,
    no_progress: bool,
    objective_relevant: bool,
    reward_profile: str,
) -> Tuple[RewardBreakdown, float]:
    if reward_profile not in REWARD_PROFILES:
        raise ValueError("unknown NovelSim reward profile")
    success = outcome is not None and outcome.result.success
    committed = outcome is not None and outcome.event is not None
    evidence_ids = set(observation.evidence_ids)
    invalid_evidence = bool(set(decision.evidence_ids) - evidence_ids)
    goal_ids = {goal.goal_id for goal in observation.goals}
    invalid_goal = bool(decision.goal_id and decision.goal_id not in goal_ids)
    penalties: Dict[str, float] = {}
    label_penalty = {
        FailureLabel.invalid_schema: 0.35,
        FailureLabel.unknown_entity: 0.30,
        FailureLabel.unavailable_world_concept: 0.30,
        FailureLabel.missing_capability: 0.30,
        FailureLabel.missing_affordance: 0.30,
        FailureLabel.navigation_failed: 0.15,
        FailureLabel.tool_precondition_failed: 0.20,
        FailureLabel.tool_timeout: 0.20,
        FailureLabel.patch_rejected: 1.00,
        FailureLabel.version_conflict: 0.20,
        FailureLabel.retry_exhausted: 0.20,
        FailureLabel.execution_error: 0.50,
    }
    if failure.primary_label in label_penalty:
        penalties[failure.primary_label.value] = label_penalty[failure.primary_label]
    if invalid_evidence:
        penalties[FailureLabel.evidence_mismatch.value] = 0.20
    if invalid_goal:
        penalties[FailureLabel.persona_goal_conflict.value] = 0.20
    if repeated_action:
        penalties[FailureLabel.repeated_loop.value] = 0.20
    if no_progress:
        penalties[FailureLabel.no_progress.value] = 0.05
    if decision.tool_call is not None and not objective_relevant:
        penalties[FailureLabel.objective_abandonment.value] = 0.30
    if failure.illegal_commit:
        penalties["illegal_commit"] = 1.00

    progress_delta = max(-1.0, min(1.0, progress_after - progress_before))
    reward = RewardBreakdown(
        objective_progress=progress_delta,
        tool_execution=(1.0 if success else (-1.0 if outcome is not None else 0.0)),
        causal_grounding=(
            1.0 if committed else (-1.0 if decision.predicted_effects else 0.0)
        ),
        character_consistency=(-1.0 if invalid_goal else (1.0 if decision.goal_id else 0.0)),
        information_integrity=(
            -1.0 if invalid_evidence else (1.0 if decision.evidence_ids else 0.0)
        ),
        recovery_quality=(
            (1.0 if success else -1.0) if observation.feedback is not None else 0.0
        ),
        action_efficiency=(
            -1.0
            if repeated_action or (decision.tool_call is not None and not objective_relevant)
            else (1.0 if progress_delta > 0 else 0.0)
        ),
        terminal_outcome=1.0 if objective_satisfied else 0.0,
        penalties=penalties,
    )
    scalar = progress_delta if reward_profile == "objective_only" else float(reward.total)
    return reward, round(scalar, 6)


async def score_completion_async(
    completion: Any,
    environment_spec: NovelSimEnvSpec,
    *,
    reward_profile: str = "mixed",
) -> CompletionRewardEvaluation:
    text = completion_text(completion)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    environment = NovelSimEnv(environment_spec, reward_profile=reward_profile)
    observation = environment.reset()
    payload = extract_json_object(text)
    if payload is None:
        return _invalid_completion_evaluation(
            digest,
            environment_spec,
            reward_profile,
            reason="completion is not one JSON object",
        )
    try:
        decision = coerce_planner_decision(
            payload,
            observation=observation,
            policy_id="grpo_candidate",
        )
    except (PlannerPolicyError, TypeError, ValueError) as exc:
        return _invalid_completion_evaluation(
            digest,
            environment_spec,
            reward_profile,
            reason="%s: %s" % (type(exc).__name__, str(exc)[:300]),
        )
    transition = await environment.step_async(decision)
    result = transition.tool_result
    return CompletionRewardEvaluation(
        completion_sha256=digest,
        parsed=True,
        scalar_reward=transition.scalar_reward,
        reward_profile=reward_profile,
        reward=transition.reward,
        failure=transition.failure,
        starting_state_hash=transition.previous_state_hash,
        next_state_hash=transition.next_state_hash,
        gate_accepted=bool(result is not None and result.success),
        objective_satisfied=transition.objective_satisfied,
        illegal_proposal=transition.failure.illegal_proposal,
        illegal_commit=transition.failure.illegal_commit,
        transition=transition,
    )


def completion_text(completion: Any) -> str:
    """Normalize standard or conversational TRL completion payloads."""

    if isinstance(completion, str):
        return completion.strip()
    if isinstance(completion, dict):
        return str(completion.get("content", "")).strip()
    if isinstance(completion, (list, tuple)):
        if not completion:
            return ""
        last = completion[-1]
        if isinstance(last, dict):
            return str(last.get("content", "")).strip()
        return str(last).strip()
    return str(completion or "").strip()


def make_trl_reward_function(reward_profile: str):
    """Build a TRL-compatible async reward callable.

    ``environment_spec`` is retained as an extra dataset column because
    ``GRPOConfig.remove_unused_columns`` is fixed to ``False``.
    """

    if reward_profile not in REWARD_PROFILES:
        raise ValueError("unknown NovelSim reward profile")

    async def novelsim_reward_func(
        completions: Sequence[Any],
        environment_spec: Sequence[str],
        **kwargs,
    ) -> List[float]:
        if len(completions) != len(environment_spec):
            raise ValueError("completion/environment_spec batch mismatch")
        evaluations = await asyncio.gather(*[
            score_completion_async(
                completion,
                NovelSimEnvSpec.parse_raw(spec),
                reward_profile=reward_profile,
            )
            for completion, spec in zip(completions, environment_spec)
        ])
        return [item.scalar_reward for item in evaluations]

    novelsim_reward_func.__name__ = "novelsim_%s_reward" % reward_profile
    return novelsim_reward_func


def _invalid_completion_evaluation(
    digest: str,
    spec: NovelSimEnvSpec,
    reward_profile: str,
    *,
    reason: str,
) -> CompletionRewardEvaluation:
    failure = FailureAttribution(
        category=FailureCategory.environment_contract,
        primary_label=FailureLabel.invalid_schema,
        labels=[FailureLabel.invalid_schema],
        stage="planner_parse",
        reason=reason,
        illegal_proposal=True,
        illegal_commit=False,
    )
    reward = RewardBreakdown(
        tool_execution=-1.0,
        penalties={FailureLabel.invalid_schema.value: 0.35},
    )
    scalar = 0.0 if reward_profile == "objective_only" else float(reward.total)
    return CompletionRewardEvaluation(
        completion_sha256=digest,
        parsed=False,
        scalar_reward=round(scalar, 6),
        reward_profile=reward_profile,
        reward=reward,
        failure=failure,
        starting_state_hash=spec.starting_state_hash,
        next_state_hash=spec.starting_state_hash,
        gate_accepted=False,
        objective_satisfied=False,
        illegal_proposal=True,
        illegal_commit=False,
    )
