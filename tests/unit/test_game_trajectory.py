import asyncio
import json

import pytest
from pydantic import ValidationError

from engine import (
    CORE_TOOL_PERMISSIONS,
    AgentExecutionStateMachine,
    FailureCategory,
    FailureLabel,
    GameTrajectory,
    GameTrajectoryRecorder,
    PlannerDecision,
    RewardBreakdown,
    ScriptedPolicy,
    ToolCall,
    build_game_observation,
    create_core_tool_registry,
    replay_game_trajectory,
    trajectory_content_hash,
)
from examples.secret_letter import GUARD, build_snapshot, run_secret_letter_scene
from training.export_trajectories import (
    build_secret_letter_v1_trajectory,
    load_trajectories_jsonl,
    load_trajectory_steps_parquet,
    main as export_main,
    summarize_trajectories,
    write_trajectory_steps_parquet,
    write_trajectories_jsonl,
)


def test_secret_letter_trajectory_is_self_contained_and_replayable():
    trajectory = build_secret_letter_v1_trajectory()
    replay = replay_game_trajectory(trajectory)

    assert trajectory.schema_version == "game_trajectory.v1"
    assert len(trajectory.steps) == 5
    assert trajectory.ending_id == "defenders_allied"
    assert trajectory.objective_satisfied is True
    assert replay.consistent is True
    assert replay.event_count == 5
    assert replay.actual_final_state_hash == trajectory.final_state_hash
    assert trajectory.content_hash == trajectory_content_hash(trajectory)
    assert all(step.failure.primary_label == FailureLabel.none for step in trajectory.steps)
    assert all(not step.failure.illegal_commit for step in trajectory.steps)


def test_semantic_content_hash_ignores_volatile_trace_telemetry():
    first = build_secret_letter_v1_trajectory()
    second = build_secret_letter_v1_trajectory()

    assert first.run_id != second.run_id
    assert first.steps[0].execution_trace.trace_id != second.steps[0].execution_trace.trace_id
    assert first.content_hash == second.content_hash

    payload = json.loads(first.json())
    payload["episode_id"] = "rerun_with_different_episode_id"
    for index, step in enumerate(payload["steps"]):
        new_call_id = "randomized_call_%s" % index
        step["decision"]["decision_id"] = "randomized_decision_%s" % index
        step["decision"]["tool_call"]["call_id"] = new_call_id
        step["tool_result"]["call_id"] = new_call_id
    rerun = GameTrajectory.parse_obj(payload)
    assert rerun.content_hash == first.content_hash


def test_failed_illegal_proposal_is_attributed_without_state_commit():
    state = build_snapshot()
    registry = create_core_tool_registry()
    observation = build_game_observation(
        state,
        GUARD,
        registry,
        world_package_id="secret_letter_v1",
        scenario_family="secret_transport",
    )
    policy = ScriptedPolicy(
        lambda obs, tools: ToolCall(
            actor_id=GUARD,
            tool_name="move_to",
            arguments={"destination_id": "aircraft"},
        )
    )
    definitions = tuple(registry.get(name) for name in registry.names())
    decision = policy.decide(observation, definitions)
    outcome = asyncio.run(
        AgentExecutionStateMachine(registry).execute(
            decision.tool_call,
            state,
            permissions=CORE_TOOL_PERMISSIONS,
        )
    )
    recorder = GameTrajectoryRecorder(
        state,
        episode_id="illegal_aircraft_probe",
        world_package_id="secret_letter_v1",
        scenario_family="secret_transport",
        random_seed=1,
        policy_id="scripted",
    )
    step = recorder.record(observation, decision, outcome)
    trajectory = recorder.finish(
        ending_id="rejected",
        objective_satisfied=False,
    )

    assert step.failure.category == FailureCategory.environment_contract
    assert step.failure.primary_label == FailureLabel.unknown_entity
    assert step.failure.illegal_proposal is True
    assert step.failure.illegal_commit is False
    assert step.committed_event is None
    assert step.previous_state_hash == step.next_state_hash
    assert step.reward.total < 0
    assert replay_game_trajectory(trajectory).consistent is True


def test_reward_total_is_derived_and_rejects_inconsistent_value():
    reward = RewardBreakdown(
        objective_progress=1.0,
        tool_execution=1.0,
        penalties={"loop": 0.1},
    )
    assert reward.total == 0.4

    with pytest.raises(ValidationError, match="does not match"):
        RewardBreakdown(tool_execution=1.0, total=99)


def test_jsonl_export_is_canonical_atomic_and_replay_verified(tmp_path, capsys):
    trajectory = build_secret_letter_v1_trajectory()
    path = tmp_path / "secret-letter.jsonl"

    write_trajectories_jsonl([trajectory], path)
    first_bytes = path.read_bytes()
    loaded = load_trajectories_jsonl(path)
    write_trajectories_jsonl(loaded, path)

    assert path.read_bytes() == first_bytes
    assert loaded == [trajectory]
    assert path.read_text(encoding="utf-8").count("\n") == 1

    cli_path = tmp_path / "cli.jsonl"
    assert export_main(["--output", str(cli_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["episode_count"] == 1
    assert output["step_count"] == 5
    assert output["replay_consistent"] is True
    assert load_trajectories_jsonl(cli_path)[0].content_hash == output["content_hash"]


def test_parquet_step_export_and_dataset_summary(tmp_path, capsys):
    trajectory = build_secret_letter_v1_trajectory()
    parquet_path = tmp_path / "secret-letter.parquet"

    write_trajectory_steps_parquet([trajectory], parquet_path)
    rows = load_trajectory_steps_parquet(parquet_path)
    summary = summarize_trajectories([trajectory])

    assert len(rows) == 5
    assert [row["step_index"] for row in rows] == list(range(5))
    assert rows[0]["tool_name"] == "pick_up"
    assert rows[-1]["tool_name"] == "propose_alliance"
    assert {row["content_hash"] for row in rows} == {trajectory.content_hash}
    assert all(row["illegal_commit"] is False for row in rows)
    assert summary == {
        "episode_count": 1,
        "step_count": 5,
        "objective_success_count": 1,
        "replay_consistent_count": 1,
        "unique_content_hash_count": 1,
        "illegal_proposal_count": 0,
        "illegal_commit_count": 0,
        "mean_step_reward": 0.3,
        "failure_distribution": {},
    }

    jsonl_path = tmp_path / "cli-with-parquet.jsonl"
    cli_parquet_path = tmp_path / "cli.parquet"
    assert export_main([
        "--output",
        str(jsonl_path),
        "--parquet-output",
        str(cli_parquet_path),
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["parquet_output"] == str(cli_parquet_path)
    assert output["summary"]["illegal_commit_count"] == 0
    assert len(load_trajectory_steps_parquet(cli_parquet_path)) == 5


def test_content_hash_detects_tampering():
    trajectory = build_secret_letter_v1_trajectory()
    payload = json.loads(trajectory.json())
    payload["ending_id"] = "tampered"

    with pytest.raises(ValidationError, match="content_hash mismatch"):
        GameTrajectory.parse_obj(payload)


def test_recorder_rejects_observation_from_wrong_state():
    initial = build_snapshot()
    run = asyncio.run(run_secret_letter_scene(initial_state=initial))
    registry = create_core_tool_registry()
    first_outcome = run.outcomes[0]
    wrong_observation = build_game_observation(
        first_outcome.new_state,
        first_outcome.execution.active_call.actor_id,
        registry,
    )
    decision = PlannerDecision.from_tool_call(
        first_outcome.execution.active_call,
        policy_id="scripted",
    )
    recorder = GameTrajectoryRecorder(
        initial,
        episode_id="wrong_state",
        world_package_id="secret_letter_v1",
        scenario_family="secret_transport",
        random_seed=1,
        policy_id="scripted",
    )

    with pytest.raises(ValueError, match="current state"):
        recorder.record(wrong_observation, decision, first_outcome)
