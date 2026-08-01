"""Build leakage-safe conversational SFT data from verified trajectories.

Only successful, committed planner decisions from the ``train`` and ``dev``
trajectory splits are eligible.  Rejected proposals are useful recovery
context for the following observation, but are never positive SFT targets.
The resulting prompt-completion format is consumed directly by TRL's
``SFTTrainer`` with completion-only loss.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from uuid import uuid4

from pydantic import BaseModel, Field, root_validator

from engine import GameTrajectory, GameTrajectoryStep
from engine.planner_decision import PlannerDecision
from engine.planner_prompt import (
    PLANNER_PROMPT_VERSION,
    PLANNER_SYSTEM_PROMPT,
    compact_observation,
)

from .export_trajectories import load_trajectories_jsonl


SFT_SCHEMA_VERSION = "novelsim_planner_sft_sample.v1"
SFT_DATASET_ID = "novelsim_planner_sft_v5"
PROMPT_VERSION = PLANNER_PROMPT_VERSION
ALLOWED_SPLITS = ("train", "dev")
SEALED_SPLITS = ("test_id", "test_ood", "adversarial")

SYSTEM_PROMPT = PLANNER_SYSTEM_PROMPT


class ChatMessage(BaseModel):
    role: str
    content: str

    class Config:
        extra = "forbid"
        allow_mutation = False

    @root_validator(skip_on_failure=True)
    def _valid_message(cls, values):
        if values.get("role") not in {"system", "user", "assistant"}:
            raise ValueError("unsupported chat role")
        if not str(values.get("content") or "").strip():
            raise ValueError("chat message content cannot be empty")
        return values


class SFTSample(BaseModel):
    schema_version: str = SFT_SCHEMA_VERSION
    sample_id: str
    prompt: List[ChatMessage]
    completion: List[ChatMessage]
    data_split: str
    source_episode_id: str
    source_trajectory_hash: str
    source_step_index: int = Field(ge=0)
    source_type: str
    scenario_family: str
    variant_id: str
    random_seed: int
    recovery_context: bool = False
    prompt_version: str = PROMPT_VERSION
    content_hash: Optional[str] = None

    class Config:
        extra = "forbid"
        allow_mutation = False

    @root_validator(skip_on_failure=True)
    def _validate_contract_and_hash(cls, values):
        split = values.get("data_split")
        if split not in ALLOWED_SPLITS:
            raise ValueError("SFT data may only use train/dev splits")
        prompt = values.get("prompt") or []
        completion = values.get("completion") or []
        if [item.role for item in prompt] != ["system", "user"]:
            raise ValueError("prompt must contain system then user messages")
        if len(completion) != 1 or completion[0].role != "assistant":
            raise ValueError("completion must contain one assistant message")
        supplied = values.get("content_hash")
        values["content_hash"] = None
        calculated = sft_sample_content_hash(values)
        if supplied not in (None, calculated):
            raise ValueError("SFT sample content_hash mismatch")
        values["content_hash"] = calculated
        if not values.get("sample_id"):
            values["sample_id"] = "sft_%s" % calculated[:20]
        return values


def normalized_decision(decision: PlannerDecision) -> Dict[str, Any]:
    call = decision.tool_call
    return _drop_empty({
        "schema_version": decision.schema_version,
        "actor_id": decision.actor_id,
        "intent": decision.intent.value,
        "goal_id": decision.goal_id,
        "tool_call": (
            {
                "actor_id": call.actor_id,
                "tool_name": call.tool_name,
                "arguments": dict(call.arguments),
            }
            if call is not None
            else None
        ),
        "evidence_ids": list(decision.evidence_ids),
        "predicted_preconditions": list(decision.predicted_preconditions),
        "predicted_effects": list(decision.predicted_effects),
        "fallback_intent": decision.fallback_intent.value,
        "confidence": decision.confidence,
        # Collection policy names are provenance, not planner reasoning.  A
        # stable grounded summary prevents the model from learning strings
        # such as "scripted_expert" or "controlled_recovery".
        "reason_summary": (
            "Select the grounded %s action." % call.tool_name
            if call is not None
            else "Wait because no grounded tool action is available."
        ),
        "fallback_reason": decision.fallback_reason,
    })


def positive_target_exclusion_reason(step: GameTrajectoryStep) -> Optional[str]:
    if step.failure.illegal_commit:
        return "illegal_commit"
    if step.failure.illegal_proposal:
        return "illegal_proposal"
    if not step.tool_result.success:
        return "tool_failure"
    if step.decision.tool_call is None:
        return "missing_tool_call"
    if step.committed_event is None:
        return "missing_committed_event"
    return None


def build_sft_sample(
    trajectory: GameTrajectory,
    step: GameTrajectoryStep,
    *,
    expected_split: str,
) -> SFTSample:
    actual_split = str(trajectory.metadata.get("data_split", ""))
    if expected_split not in ALLOWED_SPLITS or actual_split != expected_split:
        raise ValueError(
            "trajectory split %r does not match allowed expected split %r"
            % (actual_split, expected_split)
        )
    exclusion = positive_target_exclusion_reason(step)
    if exclusion is not None:
        raise ValueError("step is not a positive SFT target: %s" % exclusion)
    prompt_payload = compact_observation(step.observation)
    completion_payload = normalized_decision(step.decision)
    sample = SFTSample(
        sample_id="",
        prompt=[
            ChatMessage(role="system", content=SYSTEM_PROMPT),
            ChatMessage(
                role="user",
                content=_canonical_json(prompt_payload),
            ),
        ],
        completion=[
            ChatMessage(
                role="assistant",
                content=_canonical_json(completion_payload),
            )
        ],
        data_split=expected_split,
        source_episode_id=trajectory.episode_id,
        source_trajectory_hash=str(trajectory.content_hash),
        source_step_index=step.step_index,
        source_type=trajectory.source_type,
        scenario_family=trajectory.scenario_family,
        variant_id=trajectory.variant_id,
        random_seed=trajectory.random_seed,
        recovery_context=step.observation.feedback is not None,
    )
    return sample


def build_sft_samples(
    trajectories: Iterable[GameTrajectory],
    *,
    expected_split: str,
) -> Tuple[List[SFTSample], Dict[str, int]]:
    if expected_split not in ALLOWED_SPLITS:
        raise ValueError("SFT data may only use train/dev splits")
    samples: List[SFTSample] = []
    excluded: Dict[str, int] = {}
    seen_hashes = set()
    for trajectory in trajectories:
        actual_split = str(trajectory.metadata.get("data_split", ""))
        if actual_split != expected_split:
            raise ValueError(
                "input contains split %r while building %r"
                % (actual_split, expected_split)
            )
        if not trajectory.objective_satisfied:
            raise ValueError(
                "SFT source trajectory did not satisfy objective: %s"
                % trajectory.episode_id
            )
        for step in trajectory.steps:
            reason = positive_target_exclusion_reason(step)
            if reason is not None:
                excluded[reason] = excluded.get(reason, 0) + 1
                continue
            sample = build_sft_sample(
                trajectory,
                step,
                expected_split=expected_split,
            )
            if sample.content_hash in seen_hashes:
                excluded["duplicate_content_hash"] = (
                    excluded.get("duplicate_content_hash", 0) + 1
                )
                continue
            seen_hashes.add(sample.content_hash)
            samples.append(sample)
    return samples, dict(sorted(excluded.items()))


def load_sft_samples(input_path, *, expected_split: str) -> List[SFTSample]:
    path = Path(input_path)
    samples: List[SFTSample] = []
    seen_ids = set()
    seen_hashes = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                sample = SFTSample.parse_raw(line)
            except Exception as exc:
                raise ValueError(
                    "invalid SFT JSONL line %s: %s" % (line_number, exc)
                ) from exc
            if sample.data_split != expected_split:
                raise ValueError(
                    "SFT line %s has split %r, expected %r"
                    % (line_number, sample.data_split, expected_split)
                )
            if sample.sample_id in seen_ids or sample.content_hash in seen_hashes:
                raise ValueError("duplicate SFT sample at line %s" % line_number)
            seen_ids.add(sample.sample_id)
            seen_hashes.add(sample.content_hash)
            samples.append(sample)
    return samples


def write_sft_samples(samples: Sequence[SFTSample], output_path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".%s.%s.tmp" % (path.name, uuid4().hex))
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for sample in samples:
                handle.write(_canonical_json(json.loads(sample.json())))
                handle.write("\n")
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def build_sft_dataset(
    *,
    train_input,
    dev_input,
    output_dir,
    report_dir=None,
    source_card_path=None,
) -> Dict[str, Any]:
    train_path = Path(train_input)
    dev_path = Path(dev_input)
    _verify_source_card(
        source_card_path,
        {"train.jsonl": train_path, "dev.jsonl": dev_path},
    )
    train_trajectories = load_trajectories_jsonl(train_path)
    dev_trajectories = load_trajectories_jsonl(dev_path)
    train_samples, train_excluded = build_sft_samples(
        train_trajectories,
        expected_split="train",
    )
    dev_samples, dev_excluded = build_sft_samples(
        dev_trajectories,
        expected_split="dev",
    )
    overlap = sorted(
        {item.content_hash for item in train_samples}
        & {item.content_hash for item in dev_samples}
    )
    if overlap:
        raise ValueError("train/dev SFT content leakage detected")

    output = Path(output_dir)
    train_output = write_sft_samples(train_samples, output / "train.jsonl")
    dev_output = write_sft_samples(dev_samples, output / "dev.jsonl")
    card = {
        "schema_version": "novelsim_sft_dataset_card.v1",
        "dataset_id": SFT_DATASET_ID,
        "prompt_version": PROMPT_VERSION,
        "format": "conversational_prompt_completion",
        "loss_scope": "completion_only",
        "source_dataset_card": _source_card_record(source_card_path),
        "source_files": {
            "train.jsonl": _file_record(train_path),
            "dev.jsonl": _file_record(dev_path),
        },
        "files": {
            "train.jsonl": _file_record(train_output),
            "dev.jsonl": _file_record(dev_output),
        },
        "splits": {
            "train": _summarize_samples(
                train_samples,
                source_episode_count=len(train_trajectories),
                source_step_count=sum(len(item.steps) for item in train_trajectories),
                excluded=train_excluded,
            ),
            "dev": _summarize_samples(
                dev_samples,
                source_episode_count=len(dev_trajectories),
                source_step_count=sum(len(item.steps) for item in dev_trajectories),
                excluded=dev_excluded,
            ),
        },
        "leakage_audit": {
            "train_dev_content_hash_overlap": len(overlap),
            "passed": not overlap,
        },
        "training_boundary": {
            "optimization_split": "train",
            "checkpoint_selection_split": "dev",
            "sealed_splits_not_loaded": list(SEALED_SPLITS),
        },
        "target_filter": {
            "requires_objective_satisfied_episode": True,
            "requires_successful_tool_result": True,
            "requires_committed_event": True,
            "excludes_illegal_proposals": True,
            "deduplicates_by_prompt_completion_hash": True,
            "excludes_collection_policy_provenance_from_target": True,
            "preserves_feedback_on_subsequent_recovery_actions": True,
        },
    }
    _write_card(card, output)
    if report_dir is not None:
        _write_card(card, Path(report_dir))
    return card


def sft_sample_content_hash(value: Any) -> str:
    if isinstance(value, BaseModel):
        payload = value.dict()
    else:
        payload = dict(value)
    payload.pop("content_hash", None)
    payload.pop("sample_id", None)
    semantic = {
        "schema_version": payload.get("schema_version"),
        "prompt": _plain(payload.get("prompt", [])),
        "completion": _plain(payload.get("completion", [])),
        "prompt_version": payload.get("prompt_version"),
    }
    return hashlib.sha256(_canonical_json(semantic).encode("utf-8")).hexdigest()


def _summarize_samples(
    samples: Sequence[SFTSample],
    *,
    source_episode_count: int,
    source_step_count: int,
    excluded: Dict[str, int],
) -> Dict[str, Any]:
    prompt_lengths = [sum(len(msg.content) for msg in item.prompt) for item in samples]
    completion_lengths = [
        sum(len(msg.content) for msg in item.completion) for item in samples
    ]
    return {
        "source_episode_count": source_episode_count,
        "source_step_count": source_step_count,
        "sample_count": len(samples),
        "unique_content_hash_count": len({item.content_hash for item in samples}),
        "recovery_context_count": sum(item.recovery_context for item in samples),
        "excluded_step_count": sum(excluded.values()),
        "excluded_step_distribution": excluded,
        "source_distribution": _count(samples, lambda item: item.source_type),
        "family_distribution": _count(samples, lambda item: item.scenario_family),
        "prompt_chars": _length_summary(prompt_lengths),
        "completion_chars": _length_summary(completion_lengths),
    }


def _length_summary(values: Sequence[int]) -> Dict[str, int]:
    if not values:
        return {"min": 0, "p50": 0, "p95": 0, "max": 0}
    ordered = sorted(values)
    return {
        "min": ordered[0],
        "p50": _percentile_nearest_rank(ordered, 0.50),
        "p95": _percentile_nearest_rank(ordered, 0.95),
        "max": ordered[-1],
    }


def _percentile_nearest_rank(ordered: Sequence[int], fraction: float) -> int:
    index = max(0, int(len(ordered) * fraction + 0.999999) - 1)
    return ordered[min(index, len(ordered) - 1)]


def _count(records: Iterable[Any], key_fn) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        key = str(key_fn(record))
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _verify_source_card(source_card_path, files: Dict[str, Path]) -> None:
    if source_card_path is None:
        return
    card = json.loads(Path(source_card_path).read_text(encoding="utf-8"))
    boundary = card.get("training_boundary", {})
    if boundary.get("allowed_splits") != ["train"]:
        raise ValueError("source dataset card has unexpected training boundary")
    if boundary.get("checkpoint_selection_split") != "dev":
        raise ValueError("source dataset card has unexpected dev boundary")
    for name, path in files.items():
        expected = card.get("files", {}).get(name, {}).get("sha256")
        actual = _sha256(path)
        if not expected or expected != actual:
            raise ValueError("source file hash mismatch: %s" % name)


def _source_card_record(source_card_path) -> Optional[Dict[str, Any]]:
    if source_card_path is None:
        return None
    path = Path(source_card_path)
    card = json.loads(path.read_text(encoding="utf-8"))
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "dataset_id": card.get("dataset_id", ""),
        "manifest_id": card.get("manifest_id", ""),
        "manifest_hash": card.get("manifest_hash", ""),
        "code_commits": card.get("code_commits", []),
    }


def _write_card(card: Dict[str, Any], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "dataset-card.json").write_text(
        json.dumps(card, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# NovelSim Planner SFT Dataset v3",
        "",
        "- Format: conversational prompt-completion",
        "- Loss: completion only",
        "- Prompt version: `%s`" % card["prompt_version"],
        "- Train samples: `%s`" % card["splits"]["train"]["sample_count"],
        "- Dev samples: `%s`" % card["splits"]["dev"]["sample_count"],
        "- Train/Dev hash overlap: `%s`" % card["leakage_audit"]["train_dev_content_hash_overlap"],
        "- Sealed and not loaded: `%s`" % ", ".join(card["training_boundary"]["sealed_splits_not_loaded"]),
        "",
        "Rejected/illegal proposals are excluded as targets. Their structured feedback is retained on the next legal recovery action.",
        "",
    ]
    (output / "dataset-card.md").write_text("\n".join(lines), encoding="utf-8")


def _file_record(path: Path) -> Dict[str, Any]:
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": _sha256(path)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _drop_empty(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item is not None and item != "" and item != [] and item != {} and item != ()
    }


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _plain(value.dict())
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _plain(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Build leakage-safe NovelSim Planner SFT train/dev JSONL",
    )
    parser.add_argument("--train-input", required=True)
    parser.add_argument("--dev-input", required=True)
    parser.add_argument("--source-card", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--report-dir")
    args = parser.parse_args(argv)
    card = build_sft_dataset(
        train_input=args.train_input,
        dev_input=args.dev_input,
        output_dir=args.output_dir,
        report_dir=args.report_dir,
        source_card_path=args.source_card,
    )
    print(_canonical_json({
        "dataset_id": card["dataset_id"],
        "train_samples": card["splits"]["train"]["sample_count"],
        "dev_samples": card["splits"]["dev"]["sample_count"],
        "train_dev_hash_overlap": card["leakage_audit"]["train_dev_content_hash_overlap"],
        "output_dir": str(Path(args.output_dir)),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
