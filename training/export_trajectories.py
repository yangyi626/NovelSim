"""Deterministic JSONL I/O for replayable GameTrajectory episodes."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List
from uuid import uuid4

from engine.game_trajectory import GameTrajectory, replay_game_trajectory
from engine import (
    GameTrajectoryRecorder,
    PlannerDecision,
    build_game_observation,
    create_core_tool_registry,
)


def _verified_unique_trajectories(
    trajectories: Iterable[GameTrajectory],
) -> List[GameTrajectory]:
    records = list(trajectories)
    seen_episode_ids = set()
    seen_content_hashes = set()
    for trajectory in records:
        if trajectory.episode_id in seen_episode_ids:
            raise ValueError(
                "duplicate trajectory episode_id: %s" % trajectory.episode_id
            )
        if trajectory.content_hash in seen_content_hashes:
            raise ValueError(
                "duplicate trajectory content_hash: %s"
                % trajectory.content_hash
            )
        replay = replay_game_trajectory(trajectory)
        if not replay.consistent:
            raise ValueError(
                "trajectory is not replayable: %s: %s"
                % (trajectory.episode_id, "; ".join(replay.errors))
            )
        seen_episode_ids.add(trajectory.episode_id)
        seen_content_hashes.add(trajectory.content_hash)
    return records


def write_trajectories_jsonl(
    trajectories: Iterable[GameTrajectory],
    output_path,
) -> Path:
    """Atomically write canonical, replay-verified JSONL records."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    records = _verified_unique_trajectories(trajectories)
    temporary = path.with_name(
        ".%s.%s.tmp" % (path.name, uuid4().hex)
    )
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for trajectory in records:
                payload = json.loads(trajectory.json())
                handle.write(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                handle.write("\n")
        _replace_with_retry(temporary, path)
    finally:
        if temporary.exists():
            _unlink_with_retry(temporary)
    return path


def _replace_with_retry(source: Path, target: Path, *, attempts: int = 8) -> None:
    """Preserve atomic writes across short-lived Windows file locks."""

    for attempt in range(attempts):
        try:
            os.replace(str(source), str(target))
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.05 * (attempt + 1))


def _unlink_with_retry(path: Path, *, attempts: int = 8) -> None:
    for attempt in range(attempts):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.05 * (attempt + 1))


def load_trajectories_jsonl(input_path) -> List[GameTrajectory]:
    path = Path(input_path)
    trajectories: List[GameTrajectory] = []
    seen = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                trajectory = GameTrajectory.parse_raw(line)
            except Exception as exc:
                raise ValueError(
                    "invalid trajectory JSONL line %s: %s"
                    % (line_number, exc)
                ) from exc
            if trajectory.episode_id in seen:
                raise ValueError(
                    "duplicate trajectory episode_id: %s"
                    % trajectory.episode_id
                )
            replay = replay_game_trajectory(trajectory)
            if not replay.consistent:
                raise ValueError(
                    "trajectory line %s is not replayable: %s"
                    % (line_number, "; ".join(replay.errors))
                )
            trajectories.append(trajectory)
            seen.add(trajectory.episode_id)
    return trajectories


def write_trajectory_steps_parquet(
    trajectories: Iterable[GameTrajectory],
    output_path,
) -> Path:
    """Write one analysis/training row per planner decision step."""

    try:
        import pyarrow as pa
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise RuntimeError(
            "Parquet export requires: pip install -e '.[training]'"
        ) from exc
    records = _verified_unique_trajectories(trajectories)
    rows: List[Dict[str, Any]] = []
    for trajectory in records:
        for step in trajectory.steps:
            call = step.decision.tool_call
            rows.append({
                "run_id": trajectory.run_id,
                "episode_id": trajectory.episode_id,
                "content_hash": trajectory.content_hash,
                "world_package_id": trajectory.world_package_id,
                "scenario_family": trajectory.scenario_family,
                "variant_id": trajectory.variant_id,
                "random_seed": trajectory.random_seed,
                "policy_id": step.decision.policy_id,
                "model_id": trajectory.model_id,
                "prompt_version": trajectory.prompt_version,
                "code_commit": trajectory.code_commit,
                "source_type": trajectory.source_type,
                "data_split": trajectory.metadata.get("data_split", ""),
                "step_index": step.step_index,
                "actor_id": step.decision.actor_id,
                "intent": step.decision.intent.value,
                "tool_name": call.tool_name if call is not None else None,
                "arguments_json": _canonical_json(
                    call.arguments if call is not None else {}
                ),
                "observation_json": _canonical_json(step.observation.dict()),
                "decision_json": _canonical_json(step.decision.dict()),
                "tool_result_json": _canonical_json(step.tool_result.dict()),
                "event_json": _canonical_json(
                    step.committed_event.dict()
                    if step.committed_event is not None
                    else None
                ),
                "previous_state_hash": step.previous_state_hash,
                "next_state_hash": step.next_state_hash,
                "reward_total": step.reward.total,
                "reward_json": _canonical_json(step.reward.dict()),
                "failure_category": step.failure.category.value,
                "failure_label": step.failure.primary_label.value,
                "failure_json": _canonical_json(step.failure.dict()),
                "illegal_proposal": step.failure.illegal_proposal,
                "illegal_commit": step.failure.illegal_commit,
                "ending_id": trajectory.ending_id,
                "objective_satisfied": trajectory.objective_satisfied,
            })
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".%s.%s.tmp" % (path.name, uuid4().hex))
    try:
        table = pa.Table.from_pylist(rows)
        parquet.write_table(table, temporary, compression="zstd")
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def load_trajectory_steps_parquet(input_path) -> List[Dict[str, Any]]:
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise RuntimeError(
            "Parquet import requires: pip install -e '.[training]'"
        ) from exc
    return parquet.read_table(Path(input_path)).to_pylist()


def summarize_trajectories(
    trajectories: Iterable[GameTrajectory],
) -> Dict[str, Any]:
    records = _verified_unique_trajectories(trajectories)
    failure_distribution: Dict[str, int] = {}
    step_count = 0
    illegal_proposal_count = 0
    illegal_commit_count = 0
    reward_total = 0.0
    for trajectory in records:
        for step in trajectory.steps:
            step_count += 1
            reward_total += step.reward.total or 0.0
            illegal_proposal_count += int(step.failure.illegal_proposal)
            illegal_commit_count += int(step.failure.illegal_commit)
            label = step.failure.primary_label.value
            if label != "none":
                failure_distribution[label] = (
                    failure_distribution.get(label, 0) + 1
                )
    return {
        "episode_count": len(records),
        "step_count": step_count,
        "objective_success_count": sum(
            int(trajectory.objective_satisfied) for trajectory in records
        ),
        "replay_consistent_count": len(records),
        "unique_content_hash_count": len(records),
        "illegal_proposal_count": illegal_proposal_count,
        "illegal_commit_count": illegal_commit_count,
        "mean_step_reward": (
            round(reward_total / step_count, 6) if step_count else 0.0
        ),
        "failure_distribution": dict(sorted(failure_distribution.items())),
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def build_secret_letter_v1_trajectory() -> GameTrajectory:
    """Export the existing deterministic benchmark through the V2 contract."""

    from examples.secret_letter import (
        build_snapshot,
        run_secret_letter_scene,
    )

    initial_state = build_snapshot()
    run = asyncio.run(run_secret_letter_scene(initial_state=initial_state))
    registry = create_core_tool_registry()
    recorder = GameTrajectoryRecorder(
        initial_state,
        episode_id="secret_letter_v1:scripted:20260729",
        world_package_id="secret_letter_v1",
        scenario_family="secret_transport",
        random_seed=20260729,
        policy_id="scripted",
        variant_id="base",
        source_type="deterministic_benchmark",
    )
    current = initial_state
    for outcome in run.outcomes:
        call = outcome.execution.active_call
        observation = build_game_observation(
            current,
            call.actor_id,
            registry,
            world_package_id="secret_letter_v1",
            scenario_family="secret_transport",
        )
        decision = PlannerDecision.from_tool_call(
            call,
            policy_id="scripted",
            reason_summary="exported from deterministic secret-letter benchmark",
        )
        recorder.record(observation, decision, outcome)
        current = outcome.new_state
    return recorder.finish(
        ending_id=run.ending,
        objective_satisfied=run.summary.objective_satisfied,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Export replay-verified NovelSim GameTrajectory JSONL",
    )
    parser.add_argument(
        "--source",
        choices=["secret-letter-v1"],
        default="secret-letter-v1",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--parquet-output")
    args = parser.parse_args(argv)
    if args.source != "secret-letter-v1":
        parser.error("unsupported source")
    trajectory = build_secret_letter_v1_trajectory()
    path = write_trajectories_jsonl([trajectory], args.output)
    parquet_path = None
    if args.parquet_output:
        parquet_path = write_trajectory_steps_parquet(
            [trajectory],
            args.parquet_output,
        )
    summary = summarize_trajectories([trajectory])
    print(json.dumps({
        "output": str(path),
        "parquet_output": str(parquet_path) if parquet_path else None,
        "episode_count": 1,
        "step_count": len(trajectory.steps),
        "content_hash": trajectory.content_hash,
        "replay_consistent": replay_game_trajectory(trajectory).consistent,
        "summary": summary,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
