"""Collect, verify and package the formal Phase-2 expert trajectory dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from engine import GameTrajectory

from .build_split import DataSplit, SplitManifest, load_split_manifest
from .export_trajectories import (
    summarize_trajectories,
    write_trajectory_steps_parquet,
    write_trajectories_jsonl,
)
from .filter_trajectories import filter_trajectories
from .filter_trajectories import TrajectoryFilterConfig
from .rollout_collector import collect_expert_trajectories
from .scenario_generator import generate_scenario


def collect_manifest_dataset(
    manifest: SplitManifest,
    *,
    code_commit: str = "",
    limit: int = 0,
) -> List[GameTrajectory]:
    entries = manifest.entries[:limit] if limit > 0 else manifest.entries
    trajectories: List[GameTrajectory] = []
    for entry in entries:
        variant_index = int(entry.variant_id.rsplit("_v", 1)[-1])
        scenario = generate_scenario(
            entry.scenario_family,
            variant_index=variant_index,
            seed=entry.random_seed,
        )
        if scenario.content_hash != entry.content_hash:
            raise ValueError(
                "manifest scenario hash mismatch: %s" % entry.scenario_id
            )
        collected = collect_expert_trajectories(
            [scenario],
            code_commit=code_commit,
            include_recovery=True,
        )
        trajectories.extend(
            trajectory.copy(
                update={
                    "metadata": {
                        **trajectory.metadata,
                        "data_split": entry.split.value,
                        "manifest_id": manifest.manifest_id,
                    }
                },
                deep=True,
            )
            for trajectory in collected
        )
    filtered = filter_trajectories(
        trajectories,
        TrajectoryFilterConfig(max_illegal_proposals=1),
    )
    if filtered.rejected:
        raise ValueError(
            "dataset contains rejected trajectories: %s"
            % ", ".join(item.episode_id for item in filtered.rejected[:10])
        )
    content_hashes = [item.content_hash for item in filtered.accepted]
    if len(content_hashes) != len(set(content_hashes)):
        raise ValueError("dataset contains duplicate semantic trajectories")
    return filtered.accepted


def write_dataset(
    trajectories: Iterable[GameTrajectory],
    output_dir,
    *,
    manifest: SplitManifest,
    write_parquet: bool = True,
) -> Dict[str, Any]:
    records = list(trajectories)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    files: Dict[str, Dict[str, Any]] = {}
    per_split: Dict[str, Dict[str, Any]] = {}
    for split in (
        DataSplit.train,
        DataSplit.dev,
        DataSplit.test_id,
        DataSplit.test_ood,
        DataSplit.adversarial,
    ):
        selected = [
            trajectory
            for trajectory in records
            if trajectory.metadata.get("data_split") == split.value
        ]
        if not selected:
            continue
        jsonl_path = write_trajectories_jsonl(
            selected,
            output / ("%s.jsonl" % split.value),
        )
        files[jsonl_path.name] = _file_record(jsonl_path)
        if write_parquet:
            parquet_path = write_trajectory_steps_parquet(
                selected,
                output / ("%s.parquet" % split.value),
            )
            files[parquet_path.name] = _file_record(parquet_path)
        summary = summarize_trajectories(selected)
        summary["sealed_for_training"] = split in {
            DataSplit.test_id,
            DataSplit.test_ood,
        }
        per_split[split.value] = summary

    overall = summarize_trajectories(records)
    source_distribution = _count(records, lambda item: item.source_type)
    family_distribution = _count(records, lambda item: item.scenario_family)
    source_step_distribution = _sum_steps(records, lambda item: item.source_type)
    family_step_distribution = _sum_steps(
        records,
        lambda item: item.scenario_family,
    )
    card = {
        "schema_version": "novelsim_dataset_card.v1",
        "dataset_id": "novelsim_planner_expert_v2",
        "manifest_id": manifest.manifest_id,
        "manifest_hash": manifest.manifest_hash,
        "generator_version": manifest.generator_version,
        "license_spdx": "CC-BY-4.0",
        "content_origin": "original_for_novelsim_v2",
        "code_commits": sorted({item.code_commit for item in records}),
        "policies": sorted(source_distribution),
        "scenario_families": sorted(family_distribution),
        "overall": overall,
        "per_split": per_split,
        "episode_source_distribution": source_distribution,
        "step_source_distribution": source_step_distribution,
        "episode_family_distribution": family_distribution,
        "step_family_distribution": family_step_distribution,
        "controlled_recovery": {
            "episode_count": source_distribution.get(
                "controlled_recovery",
                0,
            ),
            "episode_rate": round(
                source_distribution.get("controlled_recovery", 0)
                / overall["episode_count"],
                6,
            ) if overall["episode_count"] else 0.0,
            "required_minimum_rate": 0.2,
            "meets_requirement": (
                source_distribution.get("controlled_recovery", 0)
                / overall["episode_count"] >= 0.2
            ) if overall["episode_count"] else False,
        },
        "files": files,
        "training_boundary": {
            "allowed_splits": ["train"],
            "checkpoint_selection_split": "dev",
            "sealed_splits": ["test_id", "test_ood"],
        },
    }
    _write_card_files(card, output)
    return card


def _write_card_files(card: Dict[str, Any], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    card_path = output / "dataset-card.json"
    card_path.write_text(
        json.dumps(card, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path = output / "dataset-card.md"
    markdown_path.write_text(_render_card_markdown(card), encoding="utf-8")


def _count(records, key_fn) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for record in records:
        key = key_fn(record)
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items()))


def _sum_steps(records, key_fn) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for record in records:
        key = key_fn(record)
        result[key] = result.get(key, 0) + len(record.steps)
    return dict(sorted(result.items()))


def _file_record(path: Path) -> Dict[str, Any]:
    payload = path.read_bytes()
    return {
        "path": path.name,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _render_card_markdown(card: Dict[str, Any]) -> str:
    overall = card["overall"]
    lines = [
        "# NovelSim Planner Expert Dataset v2",
        "",
        "- Manifest: `%s`" % card["manifest_id"],
        "- Code commit: `%s`" % ", ".join(card["code_commits"]),
        "- Episodes: `%s`" % overall["episode_count"],
        "- Decision steps: `%s`" % overall["step_count"],
        "- Objective success: `%s/%s`" % (
            overall["objective_success_count"],
            overall["episode_count"],
        ),
        "- Illegal proposals: `%s`" % overall["illegal_proposal_count"],
        "- Illegal commits: `%s`" % overall["illegal_commit_count"],
        "- Replay consistent: `%s/%s`" % (
            overall["replay_consistent_count"],
            overall["episode_count"],
        ),
        "",
        "| Split | Episodes | Steps | Training access |",
        "|---|---:|---:|---|",
    ]
    for split, summary in card["per_split"].items():
        lines.append("| %s | %s | %s | %s |" % (
            split,
            summary["episode_count"],
            summary["step_count"],
            "sealed" if summary["sealed_for_training"] else "allowed",
        ))
    lines.extend([
        "",
        "Only `train` may be consumed by SFT/GRPO training. `dev` is for "
        "checkpoint/reward selection; `test_id` and `test_ood` remain sealed.",
        "",
    ])
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect the NovelSim Phase-2 expert trajectory dataset",
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--report-dir")
    parser.add_argument("--code-commit", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-parquet", action="store_true")
    args = parser.parse_args(argv)
    manifest = load_split_manifest(args.manifest)
    trajectories = collect_manifest_dataset(
        manifest,
        code_commit=args.code_commit,
        limit=args.limit,
    )
    card = write_dataset(
        trajectories,
        args.output_dir,
        manifest=manifest,
        write_parquet=not args.no_parquet,
    )
    if args.report_dir:
        _write_card_files(card, Path(args.report_dir))
    print(json.dumps({
        "dataset_id": card["dataset_id"],
        "manifest_id": card["manifest_id"],
        "episode_count": card["overall"]["episode_count"],
        "step_count": card["overall"]["step_count"],
        "illegal_proposal_count": card["overall"]["illegal_proposal_count"],
        "illegal_commit_count": card["overall"]["illegal_commit_count"],
        "output_dir": str(Path(args.output_dir)),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
