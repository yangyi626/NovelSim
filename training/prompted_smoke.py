"""Budgeted, Train/Dev-only real PromptedLLM collection smoke.

The command defaults to a no-cost planning/audit mode.  Real provider calls
require the explicit ``--execute`` flag and configured API credentials.  Test
splits are rejected by construction and successful smoke trajectories are not
merged into SFT automatically.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field, root_validator, validator

from engine import (
    CORE_TOOL_PERMISSIONS,
    AgentExecutionStateMachine,
    GameTrajectory,
    GameTrajectoryRecorder,
    PlannerDecision,
    PlannerFeedback,
    PlannerIntent,
    PlannerUsage,
    PromptedLLMPolicy,
    ToolCall,
    build_game_observation,
    capture_llm_usage,
    create_core_tool_registry,
    replay_game_trajectory,
)
from engine.planner_prompt import (
    PLANNER_PROMPT_VERSION,
    planner_prompt_messages,
)

from .build_split import DataSplit, SplitEntry, SplitManifest, load_split_manifest
from .export_trajectories import write_trajectories_jsonl
from .scenario_generator import GeneratedScenario, evaluate_scenario, generate_scenario


PolicyFactory = Callable[["PromptedSmokeConfig"], PromptedLLMPolicy]


class PromptedSmokeConfig(BaseModel):
    schema_version: str = "prompted_smoke_config.v1"
    config_id: str = "novelsim_prompted_smoke_v1"
    manifest: str
    model_id: str
    prompt_version: str = PLANNER_PROMPT_VERSION
    scenarios_per_family_per_split: int = Field(1, ge=1, le=10)
    allowed_splits: List[str] = Field(default_factory=lambda: ["train", "dev"])
    max_turns_per_episode: int = Field(6, ge=1, le=20)
    max_model_calls: int = Field(24, ge=1)
    max_total_tokens: int = Field(100000, ge=1)
    max_output_tokens_per_call: int = Field(512, ge=32, le=2048)
    temperature: float = Field(0.2, ge=0.0, le=2.0)
    request_timeout_seconds: float = Field(30.0, gt=0.0, le=120.0)
    output_dir: str = "data/trajectories/prompted-smoke-v1"
    report_dir: str = "training/reports/prompted-smoke-v1"

    class Config:
        extra = "forbid"
        allow_mutation = False

    @validator("schema_version")
    def _known_schema(cls, value):
        if value != "prompted_smoke_config.v1":
            raise ValueError("unsupported prompted smoke config schema")
        return value

    @root_validator(skip_on_failure=True)
    def _sealed_splits_are_impossible(cls, values):
        splits = values.get("allowed_splits") or []
        if splits != ["train", "dev"]:
            raise ValueError("Prompted smoke allowed_splits must be train, dev")
        if values.get("prompt_version") != PLANNER_PROMPT_VERSION:
            raise ValueError("Prompted and SFT prompt versions must match")
        return values


class PromptedDecisionAudit(BaseModel):
    turn_index: int = Field(ge=0)
    actor_id: str
    decision_source: str
    model_schema_accepted: bool
    fallback_reason: Optional[str] = None
    tool_name: Optional[str] = None
    provider_call_count: int = Field(0, ge=0)
    provider_failed_call_count: int = Field(0, ge=0)
    provider_model_ids: List[str] = Field(default_factory=list)
    prompt_tokens: int = Field(0, ge=0)
    completion_tokens: int = Field(0, ge=0)
    total_tokens: int = Field(0, ge=0)
    cached_tokens: int = Field(0, ge=0)
    latency_ms: float = Field(0.0, ge=0.0)
    gate_accepted: bool = False
    illegal_proposal: bool = False
    illegal_commit: bool = False
    failure_label: str = "none"

    class Config:
        extra = "forbid"
        allow_mutation = False


class PromptedEpisodeAudit(BaseModel):
    scenario_id: str
    data_split: str
    scenario_family: str
    variant_id: str
    random_seed: int
    model_id: str
    prompt_version: str
    decision_attempt_count: int = Field(0, ge=0)
    provider_call_count: int = Field(0, ge=0)
    provider_failed_call_count: int = Field(0, ge=0)
    provider_model_ids: List[str] = Field(default_factory=list)
    fallback_count: int = Field(0, ge=0)
    gate_accepted_count: int = Field(0, ge=0)
    illegal_proposal_count: int = Field(0, ge=0)
    illegal_commit_count: int = Field(0, ge=0)
    prompt_tokens: int = Field(0, ge=0)
    completion_tokens: int = Field(0, ge=0)
    total_tokens: int = Field(0, ge=0)
    cached_tokens: int = Field(0, ge=0)
    objective_satisfied: bool = False
    replay_consistent: bool = False
    eligible_for_sft_review: bool = False
    termination_reason: str
    decisions: List[PromptedDecisionAudit] = Field(default_factory=list)

    class Config:
        extra = "forbid"
        allow_mutation = False


class PromptedSmokePlan(BaseModel):
    schema_version: str = "prompted_smoke_plan.v1"
    config_id: str
    config_hash: str
    manifest_id: str
    manifest_hash: str
    prompt_version: str
    model_id: str
    allowed_splits: List[str]
    selected_scenarios: List[Dict[str, Any]]
    scenario_count: int
    maximum_model_calls: int
    maximum_total_tokens: int
    estimated_maximum_output_tokens: int
    actor_schedule: str = "deterministic_scripted_actor_only"
    executes_provider_calls: bool = False
    sealed_splits_not_selected: List[str] = Field(
        default_factory=lambda: ["test_id", "test_ood", "adversarial"]
    )

    class Config:
        extra = "forbid"
        allow_mutation = False


class _Budget:
    def __init__(self, max_calls: int, max_tokens: int) -> None:
        self.max_calls = max_calls
        self.max_tokens = max_tokens
        self.decision_attempts = 0
        self.total_tokens = 0

    def can_attempt(self, reserved_tokens: int = 0) -> bool:
        return (
            self.decision_attempts < self.max_calls
            and self.total_tokens + reserved_tokens <= self.max_tokens
        )

    def record(self, total_tokens: int) -> None:
        self.decision_attempts += 1
        self.total_tokens += total_tokens


def load_prompted_smoke_config(config_path) -> PromptedSmokeConfig:
    return PromptedSmokeConfig.parse_raw(
        Path(config_path).read_text(encoding="utf-8")
    )


def select_prompted_smoke_entries(
    manifest: SplitManifest,
    config: PromptedSmokeConfig,
) -> List[SplitEntry]:
    selected: List[SplitEntry] = []
    for split_name in config.allowed_splits:
        split = DataSplit(split_name)
        families = sorted({
            entry.scenario_family
            for entry in manifest.entries
            if entry.split == split
        }, key=lambda item: item.value)
        if not families:
            raise ValueError("manifest contains no scenarios for %s" % split_name)
        for family in families:
            candidates = sorted(
                (
                    entry
                    for entry in manifest.entries
                    if entry.split == split and entry.scenario_family == family
                ),
                key=lambda item: (
                    item.variant_id,
                    item.random_seed,
                    item.scenario_id,
                ),
            )
            if len(candidates) < config.scenarios_per_family_per_split:
                raise ValueError(
                    "not enough %s/%s smoke scenarios"
                    % (split_name, family.value)
                )
            selected.extend(
                candidates[:config.scenarios_per_family_per_split]
            )
    if any(entry.split.value not in config.allowed_splits for entry in selected):
        raise ValueError("sealed split selected for Prompted smoke")
    if len({entry.content_hash for entry in selected}) != len(selected):
        raise ValueError("Prompted smoke plan contains duplicate scenario content")
    return selected


def build_prompted_smoke_plan(
    manifest: SplitManifest,
    config: PromptedSmokeConfig,
) -> PromptedSmokePlan:
    entries = select_prompted_smoke_entries(manifest, config)
    return PromptedSmokePlan(
        config_id=config.config_id,
        config_hash=_config_hash(config),
        manifest_id=manifest.manifest_id,
        manifest_hash=str(manifest.manifest_hash),
        prompt_version=config.prompt_version,
        model_id=config.model_id,
        allowed_splits=list(config.allowed_splits),
        selected_scenarios=[
            {
                "scenario_id": entry.scenario_id,
                "data_split": entry.split.value,
                "scenario_family": entry.scenario_family.value,
                "variant_id": entry.variant_id,
                "random_seed": entry.random_seed,
                "content_hash": entry.content_hash,
                "world_package_id": entry.world_package_id,
            }
            for entry in entries
        ],
        scenario_count=len(entries),
        maximum_model_calls=config.max_model_calls,
        maximum_total_tokens=config.max_total_tokens,
        estimated_maximum_output_tokens=(
            config.max_model_calls * config.max_output_tokens_per_call
        ),
    )


def collect_prompted_episode(
    scenario: GeneratedScenario,
    *,
    data_split: str,
    config: PromptedSmokeConfig,
    budget: Optional[_Budget] = None,
    policy: Optional[PromptedLLMPolicy] = None,
    code_commit: str = "",
) -> Tuple[GameTrajectory, PromptedEpisodeAudit]:
    if data_split not in config.allowed_splits:
        raise ValueError("Prompted collection may only use train/dev")
    tracker = budget or _Budget(config.max_model_calls, config.max_total_tokens)
    prompted = policy or PromptedLLMPolicy(
        model=config.model_id,
        max_tokens=config.max_output_tokens_per_call,
        temperature=config.temperature,
        request_timeout_seconds=config.request_timeout_seconds,
    )
    if prompted.prompt_version != config.prompt_version:
        raise ValueError("Prompted policy prompt version mismatch")
    registry = create_core_tool_registry()
    runtime = AgentExecutionStateMachine(registry, max_retries=0, max_replans=0)
    definitions = tuple(
        registry.get(name)
        for name in registry.names()
        if registry.get(name) is not None
    )
    recorder = GameTrajectoryRecorder(
        scenario.initial_state,
        episode_id="%s:prompted:%s" % (scenario.scenario_id, config.config_id),
        world_package_id=scenario.world_package_id,
        scenario_family=scenario.scenario_family.value,
        variant_id=scenario.variant_id,
        random_seed=scenario.random_seed,
        policy_id="prompt",
        model_id=config.model_id,
        prompt_version=config.prompt_version,
        code_commit=code_commit,
        source_type="prompted_llm",
        metadata={
            "data_split": data_split,
            "scenario_content_hash": scenario.content_hash,
            "prompted_smoke_config_id": config.config_id,
            "eligible_for_sft_review": False,
        },
    )
    state = scenario.initial_state
    previous_decision: Optional[PlannerDecision] = None
    previous_failure = None
    decisions: List[PromptedDecisionAudit] = []
    termination = "max_turns"
    fallback_count = 0

    for turn_index in range(config.max_turns_per_episode):
        if evaluate_scenario(scenario, state) is not None:
            termination = "objective_satisfied"
            break
        if not tracker.can_attempt():
            termination = "budget_exhausted"
            break
        expected_call = scenario.scripted_calls[
            min(turn_index, len(scenario.scripted_calls) - 1)
        ]
        actor_id = expected_call.actor_id
        feedback = None
        if previous_failure is not None and previous_decision is not None:
            feedback = PlannerFeedback(
                previous_decision_id=previous_decision.decision_id,
                tool_name=(
                    previous_decision.tool_call.tool_name
                    if previous_decision.tool_call is not None
                    else None
                ),
                success=False,
                failure_code=previous_failure.code.value,
                summary=previous_failure.message,
                retryable=previous_failure.retryable,
            )
        observation = build_game_observation(
            state,
            actor_id,
            registry,
            world_package_id=scenario.world_package_id,
            scenario_family=scenario.scenario_family.value,
            feedback=feedback,
            metadata={"prompted_smoke_turn": turn_index},
        )
        reserved_tokens = _conservative_token_upper_bound(
            observation,
            config.max_output_tokens_per_call,
        )
        if not tracker.can_attempt(reserved_tokens):
            termination = "budget_exhausted"
            break
        fallback_reason = None
        model_schema_accepted = True
        with capture_llm_usage() as collector:
            try:
                decision = prompted.decide(observation, definitions)
            except Exception as exc:
                model_schema_accepted = False
                fallback_reason = "%s:%s" % (
                    type(exc).__name__,
                    str(exc)[:200],
                )
                fallback_count += 1
                fallback_call = expected_call.copy(
                    update={
                        "call_id": "fallback_%s_%03d"
                        % (scenario.scenario_id, turn_index),
                    },
                    deep=True,
                )
                decision = PlannerDecision.from_tool_call(
                    fallback_call,
                    policy_id="scripted_fallback",
                    reason_summary="provider fallback for smoke continuity",
                ).copy(
                    update={
                        "fallback_reason": fallback_reason,
                        "metadata": {
                            "requested_policy": "prompt",
                            "fallback_policy": "scripted",
                        },
                    },
                    deep=True,
                )
        usage = collector.summary()
        if usage.total_tokens > reserved_tokens:
            raise RuntimeError(
                "provider token usage exceeded conservative reservation"
            )
        tracker.record(usage.total_tokens)
        call_model = (
            collector.calls[-1].model if collector.calls else config.model_id
        )
        audit = PromptedDecisionAudit(
            turn_index=turn_index,
            actor_id=actor_id,
            decision_source=(
                "model" if fallback_reason is None else "scripted_fallback"
            ),
            model_schema_accepted=model_schema_accepted,
            fallback_reason=fallback_reason,
            tool_name=(
                decision.tool_call.tool_name
                if decision.tool_call is not None
                else None
            ),
            provider_call_count=usage.call_count,
            provider_failed_call_count=usage.failed_call_count,
            provider_model_ids=sorted({item.model for item in collector.calls}),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            cached_tokens=usage.cached_tokens,
            latency_ms=usage.latency_ms,
        )
        if decision.tool_call is None:
            decisions.append(audit)
            termination = "model_wait"
            previous_decision = decision
            previous_failure = None
            break

        outcome = asyncio.run(runtime.execute(
            decision.tool_call,
            state,
            permissions=CORE_TOOL_PERMISSIONS,
            metadata={
                "decision_source": audit.decision_source,
                "prompt_version": config.prompt_version,
            },
        ))
        planner_usage = PlannerUsage(
            model_id=call_model,
            prompt_version=config.prompt_version,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            latency_ms=usage.latency_ms,
        )
        step = recorder.record(
            observation,
            decision,
            outcome,
            planner_usage=planner_usage,
        )
        audit = audit.copy(update={
            "gate_accepted": outcome.result.success,
            "illegal_proposal": step.failure.illegal_proposal,
            "illegal_commit": step.failure.illegal_commit,
            "failure_label": step.failure.primary_label.value,
        })
        decisions.append(audit)
        state = outcome.new_state
        previous_decision = decision
        previous_failure = outcome.result.failure
    else:
        if evaluate_scenario(scenario, state) is not None:
            termination = "objective_satisfied"

    objective_satisfied = evaluate_scenario(scenario, state) is not None
    if objective_satisfied:
        termination = "objective_satisfied"
    trajectory = recorder.finish(
        ending_id="success" if objective_satisfied else termination,
        objective_satisfied=objective_satisfied,
    )
    replay = replay_game_trajectory(trajectory)
    eligible = (
        objective_satisfied
        and replay.consistent
        and fallback_count == 0
        and all(not item.illegal_commit for item in decisions)
    )
    trajectory = trajectory.copy(
        update={
            "metadata": {
                **trajectory.metadata,
                "eligible_for_sft_review": eligible,
                "fallback_count": fallback_count,
            }
        },
        deep=True,
    )
    episode = PromptedEpisodeAudit(
        scenario_id=scenario.scenario_id,
        data_split=data_split,
        scenario_family=scenario.scenario_family.value,
        variant_id=scenario.variant_id,
        random_seed=scenario.random_seed,
        model_id=config.model_id,
        prompt_version=config.prompt_version,
        decision_attempt_count=len(decisions),
        provider_call_count=sum(item.provider_call_count for item in decisions),
        provider_failed_call_count=sum(
            item.provider_failed_call_count for item in decisions
        ),
        provider_model_ids=sorted({
            model_id
            for item in decisions
            for model_id in item.provider_model_ids
        }),
        fallback_count=fallback_count,
        gate_accepted_count=sum(item.gate_accepted for item in decisions),
        illegal_proposal_count=sum(item.illegal_proposal for item in decisions),
        illegal_commit_count=sum(item.illegal_commit for item in decisions),
        prompt_tokens=sum(item.prompt_tokens for item in decisions),
        completion_tokens=sum(item.completion_tokens for item in decisions),
        total_tokens=sum(item.total_tokens for item in decisions),
        cached_tokens=sum(item.cached_tokens for item in decisions),
        objective_satisfied=objective_satisfied,
        replay_consistent=replay.consistent,
        eligible_for_sft_review=eligible,
        termination_reason=termination,
        decisions=decisions,
    )
    return trajectory, episode


def execute_prompted_smoke(
    manifest: SplitManifest,
    config: PromptedSmokeConfig,
    *,
    repo_root=None,
    policy_factory: Optional[PolicyFactory] = None,
    code_commit: str = "",
) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    entries = select_prompted_smoke_entries(manifest, config)
    budget = _Budget(config.max_model_calls, config.max_total_tokens)
    trajectories: List[GameTrajectory] = []
    episodes: List[PromptedEpisodeAudit] = []
    for entry in entries:
        if not budget.can_attempt():
            break
        variant_index = int(entry.variant_id.rsplit("_v", 1)[-1])
        scenario = generate_scenario(
            entry.scenario_family,
            variant_index=variant_index,
            seed=entry.random_seed,
        )
        if scenario.content_hash != entry.content_hash:
            raise ValueError("smoke scenario hash mismatch: %s" % entry.scenario_id)
        policy = policy_factory(config) if policy_factory is not None else None
        trajectory, episode = collect_prompted_episode(
            scenario,
            data_split=entry.split.value,
            config=config,
            budget=budget,
            policy=policy,
            code_commit=code_commit,
        )
        trajectories.append(trajectory)
        episodes.append(episode)

    output_dir = _resolve(root, config.output_dir)
    files: Dict[str, Any] = {}
    for split in config.allowed_splits:
        selected = [
            trajectory
            for trajectory in trajectories
            if trajectory.metadata.get("data_split") == split
        ]
        if selected:
            path = write_trajectories_jsonl(
                selected,
                output_dir / ("%s.jsonl" % split),
            )
            files[path.name] = _file_record(path)
    report = _aggregate_report(config, manifest, episodes, files)
    report_dir = _resolve(root, config.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (report_dir / "report.md").write_text(
        _render_report_markdown(report),
        encoding="utf-8",
    )
    return report


def _aggregate_report(
    config: PromptedSmokeConfig,
    manifest: SplitManifest,
    episodes: Sequence[PromptedEpisodeAudit],
    files: Dict[str, Any],
) -> Dict[str, Any]:
    decisions = [decision for episode in episodes for decision in episode.decisions]
    gate_attempts = sum(item.tool_name is not None for item in decisions)
    return {
        "schema_version": "prompted_smoke_report.v1",
        "status": "executed_real_provider_smoke",
        "config": json.loads(config.json()),
        "config_hash": _config_hash(config),
        "manifest_id": manifest.manifest_id,
        "manifest_hash": manifest.manifest_hash,
        "prompt_version": config.prompt_version,
        "requested_model_id": config.model_id,
        "provider_model_distribution": _count_values(
            model_id
            for item in decisions
            for model_id in item.provider_model_ids
        ),
        "episode_count": len(episodes),
        "objective_success_count": sum(item.objective_satisfied for item in episodes),
        "replay_consistent_count": sum(item.replay_consistent for item in episodes),
        "eligible_for_sft_review_count": sum(
            item.eligible_for_sft_review for item in episodes
        ),
        "decision_attempt_count": len(decisions),
        "provider_call_count": sum(item.provider_call_count for item in decisions),
        "provider_failed_call_count": sum(
            item.provider_failed_call_count for item in decisions
        ),
        "model_schema_accepted_count": sum(
            item.model_schema_accepted for item in decisions
        ),
        "fallback_count": sum(
            item.decision_source == "scripted_fallback" for item in decisions
        ),
        "gate_attempt_count": gate_attempts,
        "gate_accepted_count": sum(item.gate_accepted for item in decisions),
        "illegal_proposal_count": sum(item.illegal_proposal for item in decisions),
        "illegal_commit_count": sum(item.illegal_commit for item in decisions),
        "prompt_tokens": sum(item.prompt_tokens for item in decisions),
        "completion_tokens": sum(item.completion_tokens for item in decisions),
        "total_tokens": sum(item.total_tokens for item in decisions),
        "cached_tokens": sum(item.cached_tokens for item in decisions),
        "fallback_rate": _rate(
            sum(item.decision_source == "scripted_fallback" for item in decisions),
            len(decisions),
        ),
        "model_schema_accept_rate": _rate(
            sum(item.model_schema_accepted for item in decisions),
            len(decisions),
        ),
        "gate_accept_rate": _rate(
            sum(item.gate_accepted for item in decisions),
            gate_attempts,
        ),
        "budget": {
            "maximum_model_calls": config.max_model_calls,
            "maximum_total_tokens": config.max_total_tokens,
            "call_budget_respected": len(decisions) <= config.max_model_calls,
            "provider_call_budget_respected": (
                sum(item.provider_call_count for item in decisions)
                <= config.max_model_calls
            ),
            "token_budget_respected": (
                sum(item.total_tokens for item in decisions)
                <= config.max_total_tokens
            ),
        },
        "training_boundary": {
            "collected_splits": sorted({item.data_split for item in episodes}),
            "sealed_splits_not_loaded": ["test_id", "test_ood", "adversarial"],
            "automatic_sft_merge": False,
        },
        "files": files,
        "episodes": [json.loads(item.json()) for item in episodes],
    }


def write_prompted_smoke_plan(plan: PromptedSmokePlan, output_path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json.loads(plan.json()), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _render_report_markdown(report: Dict[str, Any]) -> str:
    return "\n".join([
        "# NovelSim PromptedLLM Smoke Report",
        "",
        "- Status: `%s`" % report["status"],
        "- Requested model: `%s`" % report["requested_model_id"],
        "- Prompt version: `%s`" % report["prompt_version"],
        "- Episodes: `%s`" % report["episode_count"],
        "- Provider calls: `%s`" % report["provider_call_count"],
        "- Total tokens: `%s`" % report["total_tokens"],
        "- Fallback rate: `%s`" % report["fallback_rate"],
        "- Gate accept rate: `%s`" % report["gate_accept_rate"],
        "- Illegal commits: `%s`" % report["illegal_commit_count"],
        "- Objective success: `%s/%s`" % (
            report["objective_success_count"],
            report["episode_count"],
        ),
        "",
        "No trajectory is merged into SFT automatically; eligible trajectories require separate review.",
        "",
    ])


def _config_hash(config: PromptedSmokeConfig) -> str:
    canonical = json.dumps(
        json.loads(config.json()),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _count_values(values: Iterable[str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _conservative_token_upper_bound(
    observation,
    max_output_tokens: int,
) -> int:
    # A token cannot encode less than one byte of the serialized UTF-8 prompt.
    # Extra room covers chat-template control tokens. This deliberately
    # over-reserves so a provider response cannot cross the configured budget.
    messages = planner_prompt_messages(observation)
    prompt_bytes = sum(
        len(message["content"].encode("utf-8")) for message in messages
    )
    return prompt_bytes + max_output_tokens + 256


def _file_record(path: Path) -> Dict[str, Any]:
    payload = path.read_bytes()
    return {
        "path": str(path),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan or execute budgeted Train/Dev PromptedLLM smoke",
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--write-plan")
    parser.add_argument("--code-commit", default="")
    args = parser.parse_args(argv)
    config = load_prompted_smoke_config(args.config)
    root = _repo_root()
    manifest = load_split_manifest(_resolve(root, config.manifest))
    plan = build_prompted_smoke_plan(manifest, config)
    if args.write_plan:
        write_prompted_smoke_plan(plan, _resolve(root, args.write_plan))
    if not args.execute:
        print(json.dumps(json.loads(plan.json()), ensure_ascii=False, sort_keys=True, indent=2))
        return 0

    # Resolve credentials before any scenario begins. Never persist the key.
    from engine.config import get_llm_config

    provider = get_llm_config()
    if provider.model != config.model_id:
        raise RuntimeError(
            "LLM_MODEL %r does not match audited config model_id %r"
            % (provider.model, config.model_id)
        )
    report = execute_prompted_smoke(
        manifest,
        config,
        repo_root=root,
        code_commit=args.code_commit,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
