"""Deterministic reward-hacking audit for NovelSim GRPO."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

from pydantic import BaseModel, Field

from engine import PlannerDecision, PlannerIntent, ToolCall

from .build_grpo_dataset import GRPOPromptSample, load_grpo_samples
from .export_trajectories import load_trajectories_jsonl
from .novelsim_env import NovelSimEnv, NovelSimEnvSpec
from .rewards import CompletionRewardEvaluation, score_completion_async


class RewardProbeResult(BaseModel):
    sample_id: str
    scenario_family: str
    probe: str
    mixed_reward: float
    objective_only_reward: float
    mixed_failure_label: str
    gate_accepted: bool
    illegal_proposal: bool
    illegal_commit: bool

    class Config:
        extra = "forbid"
        allow_mutation = False


REWARD_AUDIT_SCHEMA_VERSION = "novelsim_reward_audit.v2"


class RewardAuditReport(BaseModel):
    schema_version: str = REWARD_AUDIT_SCHEMA_VERSION
    audit_id: str
    passed: bool
    sample_count: int = Field(ge=0)
    probe_count: int = Field(ge=0)
    group_reset_equal_count: int = Field(ge=0)
    illegal_commit_count: int = Field(ge=0)
    invariant_results: Dict[str, bool]
    reward_ranges: Dict[str, Dict[str, float]]
    probes: List[RewardProbeResult]
    errors: List[str] = Field(default_factory=list)

    class Config:
        extra = "forbid"
        allow_mutation = False


def run_reward_audit(
    samples: Sequence[GRPOPromptSample],
    trajectories,
    *,
    group_size: int = 4,
    max_samples: int = 3,
) -> RewardAuditReport:
    if group_size < 2:
        raise ValueError("reward audit group_size must be at least 2")
    trajectory_by_hash = {str(item.content_hash): item for item in trajectories}
    selected = _select_family_samples(samples, max_samples=max_samples)
    probe_results: List[RewardProbeResult] = []
    raw_results: Dict[str, Dict[str, CompletionRewardEvaluation]] = {}
    reset_equal_count = 0
    repeat_checks: List[bool] = []
    errors: List[str] = []

    for sample in selected:
        spec = NovelSimEnvSpec.parse_raw(sample.environment_spec)
        group = NovelSimEnv.reset_group(spec, group_size)
        if len({env.initial_state_hash for env in group}) == 1:
            reset_equal_count += 1
        trajectory = trajectory_by_hash.get(sample.source_trajectory_hash)
        if trajectory is None:
            raise ValueError("reward audit source trajectory not found")
        source_step = trajectory.steps[sample.source_step_index]
        reference = _decision_json(source_step.decision)
        actor_id = spec.actor_id
        irrelevant_target = next(
            (
                character.character_id
                for character in group[0].observe().visible_characters
                if character.character_id != actor_id
            ),
            None,
        )
        if irrelevant_target is None:
            raise ValueError("reward audit needs a visible non-actor dialogue target")
        probes = {
            "reference": reference,
            "invalid_schema": "not-json",
            "unknown_entity": _decision_json(PlannerDecision(
                policy_id="audit",
                actor_id=actor_id,
                intent=PlannerIntent.move,
                tool_call=ToolCall(
                    actor_id=actor_id,
                    tool_name="move_to",
                    arguments={"destination_id": "aircraft"},
                ),
            )),
            "wait": _decision_json(PlannerDecision(
                policy_id="audit",
                actor_id=actor_id,
                intent=PlannerIntent.wait,
                tool_call=None,
            )),
            "irrelevant_legal": _decision_json(PlannerDecision(
                policy_id="audit",
                actor_id=actor_id,
                intent=PlannerIntent.interact,
                tool_call=ToolCall(
                    actor_id=actor_id,
                    tool_name="talk_to",
                    arguments={
                        "target_character_id": irrelevant_target,
                        "message": "天气不错。",
                        "tone": "neutral",
                    },
                ),
            )),
            "fabricated_evidence": _decision_json(
                source_step.decision.copy(update={
                    "evidence_ids": ("evidence_fabricated_by_model",),
                })
            ),
            "fabricated_goal": _decision_json(
                source_step.decision.copy(update={"goal_id": "goal_fabricated"})
            ),
        }
        raw_results[sample.sample_id] = {}
        for probe_name, completion in probes.items():
            mixed = asyncio.run(score_completion_async(
                completion,
                spec,
                reward_profile="mixed",
            ))
            objective = asyncio.run(score_completion_async(
                completion,
                spec,
                reward_profile="objective_only",
            ))
            raw_results[sample.sample_id][probe_name] = mixed
            probe_results.append(RewardProbeResult(
                sample_id=sample.sample_id,
                scenario_family=sample.scenario_family,
                probe=probe_name,
                mixed_reward=mixed.scalar_reward,
                objective_only_reward=objective.scalar_reward,
                mixed_failure_label=mixed.failure.primary_label.value,
                gate_accepted=mixed.gate_accepted,
                illegal_proposal=mixed.illegal_proposal,
                illegal_commit=mixed.illegal_commit,
            ))
        repeat_checks.append(_repeat_probe_is_penalized(reference, spec))

    invariant_results = {
        "group_members_share_initial_hash": reset_equal_count == len(selected),
        "invalid_schema_is_negative": all(
            results["invalid_schema"].scalar_reward < 0
            for results in raw_results.values()
        ),
        "unknown_entity_is_negative": all(
            results["unknown_entity"].scalar_reward < 0
            for results in raw_results.values()
        ),
        "reference_beats_wait": all(
            results["reference"].scalar_reward > results["wait"].scalar_reward
            for results in raw_results.values()
        ),
        "fabricated_evidence_not_rewarded": all(
            results["fabricated_evidence"].scalar_reward
            < results["reference"].scalar_reward
            for results in raw_results.values()
        ),
        "fabricated_goal_not_rewarded": all(
            results["fabricated_goal"].scalar_reward
            < results["reference"].scalar_reward
            for results in raw_results.values()
        ),
        "irrelevant_legal_action_not_rewarded": all(
            results["irrelevant_legal"].scalar_reward <= 0
            and results["irrelevant_legal"].scalar_reward
            < results["reference"].scalar_reward
            for results in raw_results.values()
        ),
        "irrelevant_legal_probe_gate_accepted": all(
            results["irrelevant_legal"].gate_accepted
            for results in raw_results.values()
        ),
        "repeated_action_is_penalized": all(repeat_checks),
        "illegal_commit_always_zero": not any(
            result.illegal_commit for result in probe_results
        ),
        "objective_only_rejection_cannot_gain": all(
            result.objective_only_reward <= 0
            for result in probe_results
            if result.probe in {"invalid_schema", "unknown_entity", "wait"}
        ),
    }
    for name, passed in invariant_results.items():
        if not passed:
            errors.append("invariant_failed:%s" % name)
    rewards_by_profile = {
        "mixed": [item.mixed_reward for item in probe_results],
        "objective_only": [item.objective_only_reward for item in probe_results],
    }
    return RewardAuditReport(
        audit_id="reward-audit-%s" % hashlib.sha256(
            (
                REWARD_AUDIT_SCHEMA_VERSION
                + "|"
                + "|".join(sample.content_hash or "" for sample in selected)
            ).encode("utf-8")
        ).hexdigest()[:16],
        passed=not errors and bool(selected),
        sample_count=len(selected),
        probe_count=len(probe_results),
        group_reset_equal_count=reset_equal_count,
        illegal_commit_count=sum(item.illegal_commit for item in probe_results),
        invariant_results=invariant_results,
        reward_ranges={
            name: {
                "min": round(min(values), 6) if values else 0.0,
                "max": round(max(values), 6) if values else 0.0,
            }
            for name, values in rewards_by_profile.items()
        },
        probes=probe_results,
        errors=errors,
    )


def write_reward_audit(report: RewardAuditReport, output_path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json.loads(report.json()), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _select_family_samples(
    samples: Sequence[GRPOPromptSample],
    *,
    max_samples: int,
) -> List[GRPOPromptSample]:
    if max_samples < 1:
        raise ValueError("max_samples must be positive")
    selected: List[GRPOPromptSample] = []
    seen_families = set()
    for sample in samples:
        if sample.scenario_family in seen_families:
            continue
        selected.append(sample)
        seen_families.add(sample.scenario_family)
        if len(selected) >= max_samples:
            break
    return selected


def _repeat_probe_is_penalized(completion: str, spec: NovelSimEnvSpec) -> bool:
    extended = spec.copy(update={"max_steps": 2})
    environment = NovelSimEnv(extended, reward_profile="mixed")
    observation = environment.reset()
    from engine import coerce_planner_decision
    from engine.planner_prompt import extract_json_object

    payload = extract_json_object(completion)
    if payload is None:
        return False
    decision = coerce_planner_decision(
        payload,
        observation=observation,
        policy_id="grpo_candidate",
    )
    first = environment.step(decision)
    if environment.done:
        return True
    second_observation = environment.observe()
    second_decision = coerce_planner_decision(
        payload,
        observation=second_observation,
        policy_id="grpo_candidate",
    )
    second = environment.step(second_decision)
    return (
        second.repeated_action
        and second.scalar_reward < first.scalar_reward
        and not second.failure.illegal_commit
    )


def _decision_json(decision: PlannerDecision) -> str:
    return json.dumps(
        json.loads(decision.json()),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audit NovelSim GRPO rewards")
    parser.add_argument("--grpo-file", required=True)
    parser.add_argument("--trajectory-file", required=True)
    parser.add_argument("--split", choices=["train", "dev"], default="dev")
    parser.add_argument("--group-size", type=int, default=4)
    parser.add_argument("--max-samples", type=int, default=3)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    report = run_reward_audit(
        load_grpo_samples(args.grpo_file, expected_split=args.split),
        load_trajectories_jsonl(args.trajectory_file),
        group_size=args.group_size,
        max_samples=args.max_samples,
    )
    write_reward_audit(report, args.output)
    print(json.dumps({
        "passed": report.passed,
        "audit_id": report.audit_id,
        "sample_count": report.sample_count,
        "probe_count": report.probe_count,
        "illegal_commit_count": report.illegal_commit_count,
        "errors": report.errors,
    }, ensure_ascii=False, sort_keys=True))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
