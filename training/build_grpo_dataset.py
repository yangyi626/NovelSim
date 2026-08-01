"""Build leakage-safe, resettable prompt rows for NovelSim GRPO."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from uuid import uuid4

from pydantic import BaseModel, Field, root_validator, validator

from engine import GameTrajectory
from engine.planner_prompt import PLANNER_PROMPT_VERSION, planner_prompt_messages

from .export_trajectories import load_trajectories_jsonl
from .novelsim_env import NovelSimEnv, NovelSimEnvSpec


GRPO_DATASET_ID = "novelsim_planner_grpo_v1"
ALLOWED_SPLITS = ("train", "dev")
SEALED_SPLITS = ("test_id", "test_ood")


class GRPOPromptSample(BaseModel):
    schema_version: str = "novelsim_grpo_prompt.v1"
    sample_id: str
    prompt: List[Dict[str, str]]
    environment_spec: str
    prompt_version: str = PLANNER_PROMPT_VERSION
    data_split: str
    source_episode_id: str
    source_trajectory_hash: str
    source_step_index: int = Field(ge=0)
    scenario_family: str
    variant_id: str
    random_seed: int
    starting_state_hash: str
    recovery_context: bool = False
    content_hash: Optional[str] = None

    class Config:
        extra = "forbid"
        allow_mutation = False

    @validator("data_split")
    def _allowed_split(cls, value):
        if value not in ALLOWED_SPLITS:
            raise ValueError("GRPO data may only contain train/dev")
        return value

    @validator("prompt_version")
    def _shared_prompt(cls, value):
        if value != PLANNER_PROMPT_VERSION:
            raise ValueError("GRPO prompt version mismatch")
        return value

    @root_validator(skip_on_failure=True)
    def _validate_hash_and_environment(cls, values):
        spec = NovelSimEnvSpec.parse_raw(values.get("environment_spec", ""))
        if spec.starting_state_hash != values.get("starting_state_hash"):
            raise ValueError("sample and environment state hashes differ")
        supplied = values.get("content_hash")
        values["content_hash"] = None
        calculated = grpo_sample_content_hash(values)
        if supplied not in (None, calculated):
            raise ValueError("GRPO sample content_hash mismatch")
        values["content_hash"] = calculated
        return values


def build_grpo_sample(
    trajectory: GameTrajectory,
    step_index: int,
    *,
    expected_split: str,
) -> GRPOPromptSample:
    if expected_split not in ALLOWED_SPLITS:
        raise ValueError("GRPO data may only use train/dev")
    if trajectory.metadata.get("data_split") != expected_split:
        raise ValueError("trajectory split does not match GRPO output split")
    if not 0 <= step_index < len(trajectory.steps):
        raise IndexError("trajectory step index is out of range")
    step = trajectory.steps[step_index]
    scenario_hash = str(trajectory.metadata.get("scenario_content_hash", ""))
    if not scenario_hash:
        raise ValueError("trajectory lacks scenario_content_hash provenance")
    spec = NovelSimEnvSpec(
        scenario_family=trajectory.scenario_family,
        variant_index=_variant_index(trajectory.variant_id),
        random_seed=trajectory.random_seed,
        scenario_content_hash=scenario_hash,
        actor_id=step.observation.actor_id,
        prior_events=tuple(
            prior.committed_event
            for prior in trajectory.steps[:step_index]
            if prior.committed_event is not None
        ),
        starting_state_hash=step.previous_state_hash,
        feedback=step.observation.feedback,
        max_steps=1,
    )
    environment = NovelSimEnv(spec)
    rebuilt_observation = environment.reset()
    prompt = planner_prompt_messages(rebuilt_observation)
    expected_prompt = planner_prompt_messages(step.observation)
    if prompt != expected_prompt:
        raise ValueError("reconstructed GRPO prompt differs from trajectory")
    spec_json = _canonical_json(json.loads(spec.json()))
    identity = "%s:%s:%s" % (
        trajectory.content_hash,
        step_index,
        step.previous_state_hash,
    )
    sample_id = "grpo_%s" % hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return GRPOPromptSample(
        sample_id=sample_id,
        prompt=prompt,
        environment_spec=spec_json,
        data_split=expected_split,
        source_episode_id=trajectory.episode_id,
        source_trajectory_hash=str(trajectory.content_hash),
        source_step_index=step_index,
        scenario_family=trajectory.scenario_family,
        variant_id=trajectory.variant_id,
        random_seed=trajectory.random_seed,
        starting_state_hash=step.previous_state_hash,
        recovery_context=step.observation.feedback is not None,
    )


def build_grpo_samples(
    trajectories: Iterable[GameTrajectory],
    *,
    expected_split: str,
) -> Tuple[List[GRPOPromptSample], Dict[str, int]]:
    if expected_split not in ALLOWED_SPLITS:
        raise ValueError("GRPO data may only use train/dev")
    samples: List[GRPOPromptSample] = []
    seen = set()
    duplicate_count = 0
    for trajectory in trajectories:
        if not trajectory.objective_satisfied:
            raise ValueError("GRPO source episode must satisfy its objective")
        for step_index in range(len(trajectory.steps)):
            sample = build_grpo_sample(
                trajectory,
                step_index,
                expected_split=expected_split,
            )
            if sample.content_hash in seen:
                duplicate_count += 1
                continue
            seen.add(sample.content_hash)
            samples.append(sample)
    return samples, {"duplicate_content_hash": duplicate_count}


def load_grpo_samples(input_path, *, expected_split: str) -> List[GRPOPromptSample]:
    samples: List[GRPOPromptSample] = []
    seen_ids = set()
    seen_hashes = set()
    with Path(input_path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                sample = GRPOPromptSample.parse_raw(line)
            except Exception as exc:
                raise ValueError(
                    "invalid GRPO JSONL line %s: %s" % (line_number, exc)
                ) from exc
            if sample.data_split != expected_split:
                raise ValueError("GRPO sample split mismatch on line %s" % line_number)
            if sample.sample_id in seen_ids or sample.content_hash in seen_hashes:
                raise ValueError("duplicate GRPO sample on line %s" % line_number)
            seen_ids.add(sample.sample_id)
            seen_hashes.add(sample.content_hash)
            samples.append(sample)
    return samples


def write_grpo_samples(samples: Sequence[GRPOPromptSample], output_path) -> Path:
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


def build_grpo_dataset(
    *,
    train_input,
    dev_input,
    output_dir,
    source_card_path,
    report_dir=None,
) -> Dict[str, Any]:
    train_path = Path(train_input)
    dev_path = Path(dev_input)
    card_path = Path(source_card_path)
    _verify_source_card(card_path, {"train.jsonl": train_path, "dev.jsonl": dev_path})
    train_trajectories = load_trajectories_jsonl(train_path)
    dev_trajectories = load_trajectories_jsonl(dev_path)
    train_samples, train_excluded = build_grpo_samples(
        train_trajectories,
        expected_split="train",
    )
    dev_samples, dev_excluded = build_grpo_samples(
        dev_trajectories,
        expected_split="dev",
    )
    overlap = sorted(
        {sample.content_hash for sample in train_samples}
        & {sample.content_hash for sample in dev_samples}
    )
    if overlap:
        raise ValueError("train/dev GRPO content leakage detected")
    output = Path(output_dir)
    train_output = write_grpo_samples(train_samples, output / "train.jsonl")
    dev_output = write_grpo_samples(dev_samples, output / "dev.jsonl")
    card = {
        "schema_version": "novelsim_grpo_dataset_card.v1",
        "dataset_id": GRPO_DATASET_ID,
        "prompt_version": PLANNER_PROMPT_VERSION,
        "format": "conversational_prompt_with_environment_spec",
        "source_dataset_card": _file_record(card_path),
        "source_files": {
            "train.jsonl": _file_record(train_path),
            "dev.jsonl": _file_record(dev_path),
        },
        "files": {
            "train.jsonl": _file_record(train_output),
            "dev.jsonl": _file_record(dev_output),
        },
        "splits": {
            "train": _sample_summary(train_samples, train_excluded),
            "dev": _sample_summary(dev_samples, dev_excluded),
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
        "environment_contract": {
            "reset_source": "scenario parameters plus committed event prefix",
            "group_initial_state_hash_required_equal": True,
            "authoritative_mutation_path": "ToolRegistry->FSM->PatchValidator->WorldEvent",
            "planner_state_patch_forbidden": True,
            "one_decision_per_prompt": True,
        },
    }
    _write_card(card, output)
    if report_dir is not None:
        _write_card(card, Path(report_dir))
    return card


def grpo_sample_content_hash(value: Any) -> str:
    payload = value.dict() if isinstance(value, BaseModel) else dict(value)
    payload.pop("content_hash", None)
    payload.pop("sample_id", None)
    semantic = {
        "schema_version": payload.get("schema_version"),
        "prompt": payload.get("prompt"),
        "environment_spec": payload.get("environment_spec"),
        "prompt_version": payload.get("prompt_version"),
    }
    return hashlib.sha256(_canonical_json(semantic).encode("utf-8")).hexdigest()


def _sample_summary(
    samples: Sequence[GRPOPromptSample],
    excluded: Dict[str, int],
) -> Dict[str, Any]:
    return {
        "sample_count": len(samples),
        "unique_content_hash_count": len({sample.content_hash for sample in samples}),
        "unique_starting_state_hash_count": len({sample.starting_state_hash for sample in samples}),
        "recovery_context_count": sum(sample.recovery_context for sample in samples),
        "excluded": excluded,
        "family_distribution": _count(samples, lambda item: item.scenario_family),
    }


def _verify_source_card(card_path: Path, files: Dict[str, Path]) -> None:
    if not card_path.is_file():
        raise ValueError("source dataset card is missing")
    card = json.loads(card_path.read_text(encoding="utf-8"))
    boundary = card.get("training_boundary", {})
    if boundary.get("allowed_splits") != ["train"]:
        raise ValueError("source dataset card training boundary is invalid")
    if boundary.get("checkpoint_selection_split") != "dev":
        raise ValueError("source dataset card dev boundary is invalid")
    for name, path in files.items():
        expected = card.get("files", {}).get(name, {}).get("sha256")
        if not expected or expected != _sha256(path):
            raise ValueError("source trajectory hash mismatch: %s" % name)


def _variant_index(variant_id: str) -> int:
    try:
        value = int(variant_id.rsplit("_v", 1)[-1])
    except (TypeError, ValueError) as exc:
        raise ValueError("variant_id does not end in _vNNN") from exc
    if value < 0:
        raise ValueError("variant index cannot be negative")
    return value


def _count(records: Iterable[Any], key_fn) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        key = str(key_fn(record))
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _write_card(card: Dict[str, Any], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "dataset-card.json").write_text(
        json.dumps(card, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# NovelSim Planner GRPO Dataset v1",
        "",
        "- Prompt version: `%s`" % card["prompt_version"],
        "- Train prompts: `%s`" % card["splits"]["train"]["sample_count"],
        "- Dev prompts: `%s`" % card["splits"]["dev"]["sample_count"],
        "- Train/Dev overlap: `%s`" % card["leakage_audit"]["train_dev_content_hash_overlap"],
        "- Sealed and not loaded: `%s`" % ", ".join(card["training_boundary"]["sealed_splits_not_loaded"]),
        "",
        "Every completion is executed from an independently rebuilt authoritative state. Model text cannot mutate WorldState directly.",
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


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build resettable NovelSim GRPO prompts")
    parser.add_argument("--train-input", required=True)
    parser.add_argument("--dev-input", required=True)
    parser.add_argument("--source-card", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--report-dir")
    args = parser.parse_args(argv)
    card = build_grpo_dataset(
        train_input=args.train_input,
        dev_input=args.dev_input,
        source_card_path=args.source_card,
        output_dir=args.output_dir,
        report_dir=args.report_dir,
    )
    print(_canonical_json({
        "dataset_id": card["dataset_id"],
        "train_samples": card["splits"]["train"]["sample_count"],
        "dev_samples": card["splits"]["dev"]["sample_count"],
        "train_dev_hash_overlap": card["leakage_audit"]["train_dev_content_hash_overlap"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
