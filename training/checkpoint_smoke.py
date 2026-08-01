"""Dev-only checkpoint inference -> Runtime Gate -> replay smoke."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, root_validator, validator

from engine import (
    CORE_TOOL_PERMISSIONS,
    AgentExecutionStateMachine,
    GameTrajectory,
    GameTrajectoryRecorder,
    PlannerFeedback,
    SFTPolicy,
    build_game_observation,
    create_core_tool_registry,
    inspect_adapter_checkpoint,
    load_local_adapter_config,
    replay_game_trajectory,
)

from .build_split import DataSplit, SplitEntry, SplitManifest, load_split_manifest
from .export_trajectories import write_trajectories_jsonl
from .scenario_generator import evaluate_scenario, generate_scenario


class CheckpointSmokeConfig(BaseModel):
    schema_version: str = "checkpoint_runtime_smoke_config.v1"
    config_id: str
    local_policy_config: str
    scenario_manifest: str
    scenario_id: str
    data_split: str = "dev"
    max_turns: int = Field(6, ge=1, le=20)
    trajectory_output: str
    report_output: str

    class Config:
        extra = "forbid"
        allow_mutation = False

    @validator("schema_version")
    def _known_schema(cls, value):
        if value != "checkpoint_runtime_smoke_config.v1":
            raise ValueError("unsupported checkpoint smoke config schema")
        return value

    @root_validator(skip_on_failure=True)
    def _dev_only(cls, values):
        if values.get("data_split") != "dev":
            raise ValueError("checkpoint smoke must use dev split")
        return values


class CheckpointTurnAudit(BaseModel):
    turn_index: int = Field(ge=0)
    actor_id: str
    schema_accepted: bool = False
    generation_error: Optional[str] = None
    tool_name: Optional[str] = None
    gate_accepted: bool = False
    illegal_proposal: bool = False
    illegal_commit: bool = False
    failure_label: str = "none"
    prompt_tokens: int = Field(0, ge=0)
    completion_tokens: int = Field(0, ge=0)
    total_tokens: int = Field(0, ge=0)
    latency_ms: float = Field(0.0, ge=0.0)

    class Config:
        extra = "forbid"
        allow_mutation = False


def load_checkpoint_smoke_config(config_path) -> CheckpointSmokeConfig:
    return CheckpointSmokeConfig.parse_raw(
        Path(config_path).read_text(encoding="utf-8")
    )


def resolve_checkpoint_smoke_scenario(
    manifest: SplitManifest,
    config: CheckpointSmokeConfig,
) -> SplitEntry:
    matches = [
        entry
        for entry in manifest.entries
        if entry.scenario_id == config.scenario_id
    ]
    if len(matches) != 1:
        raise ValueError("checkpoint smoke scenario_id is not unique in manifest")
    entry = matches[0]
    if entry.split != DataSplit.dev or config.data_split != "dev":
        raise ValueError("checkpoint smoke scenario must belong to dev")
    return entry


def inspect_checkpoint_smoke(
    config: CheckpointSmokeConfig,
    *,
    repo_root=None,
) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    policy_config_path = _resolve(root, config.local_policy_config)
    manifest_path = _resolve(root, config.scenario_manifest)
    errors: List[str] = []
    policy_config = None
    checkpoint = None
    entry = None
    if not policy_config_path.is_file():
        errors.append("local_policy_config_missing")
    else:
        try:
            policy_config = load_local_adapter_config(policy_config_path)
            if policy_config.policy_kind != "sft":
                errors.append("local_policy_is_not_sft")
            checkpoint = inspect_adapter_checkpoint(
                policy_config,
                repo_root=root,
            )
            errors.extend(
                "checkpoint:%s" % error for error in checkpoint.errors
            )
        except Exception as exc:
            errors.append("local_policy_config_invalid:%s" % type(exc).__name__)
    if not manifest_path.is_file():
        errors.append("scenario_manifest_missing")
    else:
        try:
            manifest = load_split_manifest(manifest_path)
            entry = resolve_checkpoint_smoke_scenario(manifest, config)
        except Exception as exc:
            errors.append("scenario_invalid:%s" % str(exc))
    return {
        "schema_version": "checkpoint_runtime_smoke_preflight.v1",
        "config_id": config.config_id,
        "ready": not errors,
        "executes_model": False,
        "data_split": config.data_split,
        "scenario_id": config.scenario_id,
        "scenario_content_hash": entry.content_hash if entry is not None else "",
        "checkpoint": (
            json.loads(checkpoint.json()) if checkpoint is not None else None
        ),
        "errors": errors,
    }


def execute_checkpoint_smoke(
    config: CheckpointSmokeConfig,
    *,
    repo_root=None,
    policy: Optional[SFTPolicy] = None,
) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    preflight = inspect_checkpoint_smoke(config, repo_root=root)
    if not preflight["ready"]:
        raise RuntimeError(
            "checkpoint smoke preflight failed: %s"
            % ", ".join(preflight["errors"])
        )
    policy_config = load_local_adapter_config(
        _resolve(root, config.local_policy_config)
    )
    active_policy = policy or SFTPolicy(policy_config, repo_root=root)
    manifest = load_split_manifest(_resolve(root, config.scenario_manifest))
    entry = resolve_checkpoint_smoke_scenario(manifest, config)
    variant_index = int(entry.variant_id.rsplit("_v", 1)[-1])
    scenario = generate_scenario(
        entry.scenario_family,
        variant_index=variant_index,
        seed=entry.random_seed,
    )
    if scenario.content_hash != entry.content_hash:
        raise RuntimeError("checkpoint smoke scenario hash mismatch")

    registry = create_core_tool_registry()
    runtime = AgentExecutionStateMachine(registry, max_retries=0, max_replans=0)
    definitions = tuple(
        registry.get(name)
        for name in registry.names()
        if registry.get(name) is not None
    )
    checkpoint = preflight["checkpoint"]
    recorder = GameTrajectoryRecorder(
        scenario.initial_state,
        episode_id="%s:%s" % (scenario.scenario_id, config.config_id),
        world_package_id=scenario.world_package_id,
        scenario_family=scenario.scenario_family.value,
        variant_id=scenario.variant_id,
        random_seed=scenario.random_seed,
        policy_id="sft",
        model_id=policy_config.model_id,
        prompt_version=policy_config.prompt_version,
        code_commit=checkpoint["training_code_commit"],
        source_type="sft_checkpoint_smoke",
        metadata={
            "data_split": "dev",
            "checkpoint_adapter_content_hash": checkpoint[
                "adapter_content_hash"
            ],
            "checkpoint_run_manifest_sha256": checkpoint[
                "run_manifest_sha256"
            ],
            "scenario_content_hash": scenario.content_hash,
        },
    )
    state = scenario.initial_state
    previous_decision = None
    previous_failure = None
    turns: List[CheckpointTurnAudit] = []
    termination = "max_turns"
    for turn_index in range(config.max_turns):
        if evaluate_scenario(scenario, state) is not None:
            termination = "objective_satisfied"
            break
        expected_call = scenario.scripted_calls[
            min(turn_index, len(scenario.scripted_calls) - 1)
        ]
        feedback = None
        if previous_decision is not None and previous_failure is not None:
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
            expected_call.actor_id,
            registry,
            world_package_id=scenario.world_package_id,
            scenario_family=scenario.scenario_family.value,
            feedback=feedback,
            metadata={"checkpoint_smoke_turn": turn_index},
        )
        try:
            generated = active_policy.decide_with_usage(
                observation,
                definitions,
            )
        except Exception as exc:
            turns.append(CheckpointTurnAudit(
                turn_index=turn_index,
                actor_id=expected_call.actor_id,
                generation_error="%s:%s" % (
                    type(exc).__name__,
                    str(exc)[:300],
                ),
            ))
            termination = "generation_error"
            break
        decision = generated.decision
        audit = CheckpointTurnAudit(
            turn_index=turn_index,
            actor_id=expected_call.actor_id,
            schema_accepted=True,
            tool_name=(
                decision.tool_call.tool_name
                if decision.tool_call is not None
                else None
            ),
            prompt_tokens=generated.usage.prompt_tokens,
            completion_tokens=generated.usage.completion_tokens,
            total_tokens=generated.usage.total_tokens,
            latency_ms=generated.usage.latency_ms,
        )
        if decision.tool_call is None:
            turns.append(audit)
            termination = "model_wait"
            previous_decision = decision
            previous_failure = None
            break
        outcome = asyncio.run(runtime.execute(
            decision.tool_call,
            state,
            permissions=CORE_TOOL_PERMISSIONS,
            metadata={
                "decision_source": "sft_checkpoint",
                "prompt_version": policy_config.prompt_version,
            },
        ))
        step = recorder.record(
            observation,
            decision,
            outcome,
            planner_usage=generated.usage,
        )
        turns.append(audit.copy(update={
            "gate_accepted": outcome.result.success,
            "illegal_proposal": step.failure.illegal_proposal,
            "illegal_commit": step.failure.illegal_commit,
            "failure_label": step.failure.primary_label.value,
        }))
        state = outcome.new_state
        previous_decision = decision
        previous_failure = outcome.result.failure
    objective = evaluate_scenario(scenario, state) is not None
    if objective:
        termination = "objective_satisfied"
    trajectory = recorder.finish(
        ending_id="success" if objective else termination,
        objective_satisfied=objective,
    )
    replay = replay_game_trajectory(trajectory)
    trajectory_path = write_trajectories_jsonl(
        [trajectory],
        _resolve(root, config.trajectory_output),
    )
    generation_error_count = sum(item.generation_error is not None for item in turns)
    report = {
        "schema_version": "checkpoint_runtime_smoke_report.v1",
        "config_id": config.config_id,
        "status": "executed_checkpoint_runtime_smoke",
        "passed": (
            objective
            and replay.consistent
            and generation_error_count == 0
            and all(item.schema_accepted for item in turns)
            and all(not item.illegal_commit for item in turns)
        ),
        "preflight": preflight,
        "scenario_id": scenario.scenario_id,
        "data_split": "dev",
        "model_id": policy_config.model_id,
        "prompt_version": policy_config.prompt_version,
        "decision_count": len(turns),
        "schema_accepted_count": sum(item.schema_accepted for item in turns),
        "generation_error_count": generation_error_count,
        "gate_accepted_count": sum(item.gate_accepted for item in turns),
        "illegal_proposal_count": sum(item.illegal_proposal for item in turns),
        "illegal_commit_count": sum(item.illegal_commit for item in turns),
        "objective_satisfied": objective,
        "replay_consistent": replay.consistent,
        "termination_reason": termination,
        "prompt_tokens": sum(item.prompt_tokens for item in turns),
        "completion_tokens": sum(item.completion_tokens for item in turns),
        "total_tokens": sum(item.total_tokens for item in turns),
        "latency_ms": round(sum(item.latency_ms for item in turns), 3),
        "trajectory_file": _file_record(trajectory_path),
        "turns": [json.loads(item.json()) for item in turns],
    }
    report_path = _resolve(root, config.report_output)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def _file_record(path: Path) -> Dict[str, Any]:
    import hashlib

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
        description="Inspect or execute a Dev-only SFT checkpoint Runtime smoke",
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    config = load_checkpoint_smoke_config(args.config)
    if args.execute:
        report = execute_checkpoint_smoke(config)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if report["passed"] else 1
    preflight = inspect_checkpoint_smoke(config)
    print(json.dumps(preflight, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
