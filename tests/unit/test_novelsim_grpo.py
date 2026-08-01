import asyncio
import json

import pytest
from pydantic import ValidationError

from engine import PlannerDecision, PlannerIntent, ToolCall
from training.build_grpo_dataset import (
    GRPOPromptSample,
    build_grpo_samples,
    load_grpo_samples,
    write_grpo_samples,
)
from training.novelsim_env import NovelSimEnv, NovelSimEnvSpec
from training.reward_audit import run_reward_audit
from training.rewards import make_trl_reward_function, score_completion_async
from training.rollout_collector import (
    collect_recovery_trajectory,
    collect_scripted_trajectory,
)
from training.scenario_generator import (
    ScenarioFamily,
    generate_scenario,
    scenario_progress,
)
from training.train_grpo import GRPOTrainingConfig, load_grpo_training_config


def _trajectory(family=ScenarioFamily.secret_transport, *, split="train", recovery=False):
    scenario = generate_scenario(family, variant_index=0, seed=11)
    trajectory = (
        collect_recovery_trajectory(scenario)
        if recovery
        else collect_scripted_trajectory(scenario)
    )
    return trajectory.copy(update={
        "metadata": {**trajectory.metadata, "data_split": split},
    })


def _first_sample(trajectory, *, split="train"):
    samples, excluded = build_grpo_samples([trajectory], expected_split=split)
    assert excluded == {"duplicate_content_hash": 0}
    return samples[0]


def _completion(decision):
    return json.dumps(json.loads(decision.json()), ensure_ascii=False)


def test_scenario_progress_is_result_based_not_exact_action_match():
    scenario = generate_scenario(
        ScenarioFamily.rescue_escort,
        variant_index=0,
        seed=11,
    )
    assert scenario_progress(scenario, scenario.initial_state) == 0.0

    trajectory = collect_scripted_trajectory(scenario)
    assert trajectory.objective_satisfied is True
    from engine.event import replay_events

    final_state = replay_events(
        trajectory.initial_state,
        [step.committed_event for step in trajectory.steps if step.committed_event],
    )
    assert scenario_progress(scenario, final_state) == 1.0


def test_grpo_group_resets_to_identical_isolated_authoritative_states():
    sample = _first_sample(_trajectory())
    spec = NovelSimEnvSpec.parse_raw(sample.environment_spec)
    group = NovelSimEnv.reset_group(spec, 4)

    assert {env.initial_state_hash for env in group} == {spec.starting_state_hash}
    group[0].state.flags["local_mutation_probe"] = True
    assert "local_mutation_probe" not in group[1].state.flags


def test_environment_executes_only_through_gate_and_never_commits_aircraft():
    trajectory = _trajectory()
    sample = _first_sample(trajectory)
    spec = NovelSimEnvSpec.parse_raw(sample.environment_spec)
    environment = NovelSimEnv(spec)
    observation = environment.reset()
    illegal = PlannerDecision(
        policy_id="grpo_candidate",
        actor_id=observation.actor_id,
        intent=PlannerIntent.move,
        tool_call=ToolCall(
            actor_id=observation.actor_id,
            tool_name="move_to",
            arguments={"destination_id": "aircraft"},
        ),
    )
    transition = environment.step(illegal)

    assert transition.tool_result.success is False
    assert transition.failure.illegal_proposal is True
    assert transition.failure.illegal_commit is False
    assert transition.committed_event is None
    assert transition.previous_state_hash == transition.next_state_hash
    assert transition.scalar_reward < 0


def test_legal_reference_action_beats_wait_and_fabricated_evidence():
    trajectory = _trajectory()
    sample = _first_sample(trajectory)
    spec = NovelSimEnvSpec.parse_raw(sample.environment_spec)
    reference = trajectory.steps[0].decision
    wait = PlannerDecision(
        policy_id="candidate",
        actor_id=reference.actor_id,
        intent=PlannerIntent.wait,
    )
    fabricated = reference.copy(update={
        "evidence_ids": ("evidence_model_invented",),
    })

    reference_score = asyncio.run(score_completion_async(_completion(reference), spec))
    wait_score = asyncio.run(score_completion_async(_completion(wait), spec))
    fabricated_score = asyncio.run(score_completion_async(_completion(fabricated), spec))

    assert reference_score.gate_accepted is True
    assert reference_score.scalar_reward > wait_score.scalar_reward
    assert fabricated_score.scalar_reward < reference_score.scalar_reward
    assert fabricated_score.failure.primary_label.value == "evidence_mismatch"
    assert all(
        not result.illegal_commit
        for result in (reference_score, wait_score, fabricated_score)
    )


def test_invalid_completion_is_negative_and_state_preserving():
    sample = _first_sample(_trajectory())
    spec = NovelSimEnvSpec.parse_raw(sample.environment_spec)
    result = asyncio.run(score_completion_async("not-json", spec))

    assert result.parsed is False
    assert result.scalar_reward < 0
    assert result.failure.primary_label.value == "invalid_schema"
    assert result.starting_state_hash == result.next_state_hash
    assert result.illegal_proposal is True
    assert result.illegal_commit is False


def test_grpo_dataset_reconstructs_feedback_and_detects_tampering(tmp_path):
    trajectory = _trajectory(recovery=True)
    samples, _ = build_grpo_samples([trajectory], expected_split="train")
    assert len(samples) == len(trajectory.steps)
    assert sum(sample.recovery_context for sample in samples) == 1

    path = write_grpo_samples(samples, tmp_path / "train.jsonl")
    assert load_grpo_samples(path, expected_split="train") == samples
    payload = json.loads(samples[0].json())
    payload["starting_state_hash"] = "0" * 64
    with pytest.raises(ValidationError, match="state hashes differ"):
        GRPOPromptSample.parse_obj(payload)


def test_trl_reward_callable_preserves_group_initial_state():
    trajectory = _trajectory()
    sample = _first_sample(trajectory)
    reference = _completion(trajectory.steps[0].decision)
    reward_func = make_trl_reward_function("mixed")
    rewards = asyncio.run(reward_func(
        completions=[reference, "not-json"],
        environment_spec=[sample.environment_spec, sample.environment_spec],
    ))

    assert reward_func.__name__ == "novelsim_mixed_reward"
    assert rewards[0] > rewards[1]


def test_reward_hacking_audit_passes_all_three_world_families():
    trajectories = [
        _trajectory(family, split="dev") for family in ScenarioFamily
    ]
    samples, _ = build_grpo_samples(trajectories, expected_split="dev")
    report = run_reward_audit(samples, trajectories, group_size=4)

    assert report.passed is True
    assert report.sample_count == 3
    assert report.probe_count == 21
    assert report.group_reset_equal_count == 3
    assert report.illegal_commit_count == 0
    assert all(report.invariant_results.values())


def test_checked_in_grpo_configs_enforce_group_and_single_gpu_contract():
    objective = load_grpo_training_config(
        "training/configs/grpo_qwen3_0.6b_objective_smoke.json"
    )
    mixed = load_grpo_training_config(
        "training/configs/grpo_qwen3_0.6b_mixed_smoke.json"
    )
    debug = load_grpo_training_config(
        "training/configs/grpo_qwen3_1.7b_mixed_debug.json"
    )
    main = load_grpo_training_config(
        "training/configs/grpo_qwen3_4b_mixed_qlora.json"
    )

    assert objective.reward_profile == "objective_only"
    assert mixed.reward_profile == "mixed"
    assert debug.model_id == "Qwen/Qwen3-1.7B"
    assert debug.max_steps == 100
    assert main.use_vllm is True
    assert main.vllm_mode == "colocate"
    assert main.vllm_gpu_memory_utilization == 0.2
    assert main.vllm_enable_sleep_mode is True
    assert all(
        config.micro_batch_size * config.gradient_accumulation_steps
        % config.num_generations == 0
        for config in (objective, mixed, debug, main)
    )

    payload = json.loads(mixed.json())
    payload["gradient_accumulation_steps"] = 3
    with pytest.raises(ValidationError, match="divisible"):
        GRPOTrainingConfig.parse_obj(payload)
