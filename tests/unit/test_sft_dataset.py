import json

import pytest
from pydantic import ValidationError

from training.build_sft_dataset import (
    SFTSample,
    build_sft_dataset,
    build_sft_samples,
    load_sft_samples,
)
from training.export_trajectories import write_trajectories_jsonl
from training.rollout_collector import (
    collect_recovery_trajectory,
    collect_scripted_trajectory,
)
from training.scenario_generator import ScenarioFamily, generate_scenario


def _with_split(trajectory, split):
    return trajectory.copy(
        update={
            "metadata": {
                **trajectory.metadata,
                "data_split": split,
            }
        },
        deep=True,
    )


def test_sft_builder_excludes_rejection_and_keeps_recovery_feedback():
    trajectory = _with_split(
        collect_recovery_trajectory(
            generate_scenario(
                ScenarioFamily.secret_transport,
                variant_index=0,
                seed=11,
            )
        ),
        "train",
    )

    samples, excluded = build_sft_samples(
        [trajectory],
        expected_split="train",
    )

    assert len(samples) == len(trajectory.steps) - 1
    assert excluded == {"illegal_proposal": 1}
    assert sum(item.recovery_context for item in samples) == 1
    assert all(item.data_split == "train" for item in samples)
    assert all(item.completion[0].role == "assistant" for item in samples)
    assert all(item.prompt[0].role == "system" for item in samples)
    recovery = next(item for item in samples if item.recovery_context)
    prompt_payload = json.loads(recovery.prompt[1].content)
    assert prompt_payload["feedback"]["success"] is False
    assert prompt_payload["feedback"]["failure_code"]


def test_sft_completion_is_normalized_and_prompt_is_actor_scoped():
    trajectory = _with_split(
        collect_scripted_trajectory(
            generate_scenario(
                ScenarioFamily.resource_negotiation,
                variant_index=0,
                seed=11,
            )
        ),
        "train",
    )
    samples, _ = build_sft_samples([trajectory], expected_split="train")
    sample = samples[0]
    decision = json.loads(sample.completion[0].content)
    observation = json.loads(sample.prompt[1].content)

    assert "decision_id" not in decision
    assert "policy_id" not in decision
    assert "call_id" not in decision["tool_call"]
    assert "parent_trace_id" not in decision["tool_call"]
    assert "metadata" not in decision
    assert decision["reason_summary"] == "Select the grounded pick_up action."
    assert "scripted_expert" not in sample.completion[0].content
    assert "authoritative_state_hash" not in observation
    assert "observation_id" not in observation
    assert "initial_state" not in observation
    assert "available_tools" in observation
    assert "title" not in json.dumps(observation["available_tools"])


def test_sft_builder_rejects_sealed_or_mixed_splits():
    base = collect_scripted_trajectory(
        generate_scenario(
            ScenarioFamily.secret_transport,
            variant_index=0,
            seed=11,
        )
    )
    test_trajectory = _with_split(base, "test_id")

    with pytest.raises(ValueError, match="train/dev"):
        build_sft_samples([test_trajectory], expected_split="test_id")
    with pytest.raises(ValueError, match="input contains split"):
        build_sft_samples([test_trajectory], expected_split="train")


def test_sft_dataset_writes_train_dev_and_audits_hashes(tmp_path):
    train = _with_split(
        collect_recovery_trajectory(
            generate_scenario(
                ScenarioFamily.secret_transport,
                variant_index=0,
                seed=11,
            )
        ),
        "train",
    )
    dev = _with_split(
        collect_recovery_trajectory(
            generate_scenario(
                ScenarioFamily.resource_negotiation,
                variant_index=1,
                seed=23,
            )
        ),
        "dev",
    )
    source = tmp_path / "source"
    train_path = write_trajectories_jsonl([train], source / "train.jsonl")
    dev_path = write_trajectories_jsonl([dev], source / "dev.jsonl")

    card = build_sft_dataset(
        train_input=train_path,
        dev_input=dev_path,
        output_dir=tmp_path / "sft",
        report_dir=tmp_path / "report",
    )

    assert card["splits"]["train"]["excluded_step_distribution"] == {
        "illegal_proposal": 1
    }
    assert card["splits"]["dev"]["excluded_step_distribution"] == {
        "illegal_proposal": 1
    }
    assert card["leakage_audit"] == {
        "train_dev_content_hash_overlap": 0,
        "passed": True,
    }
    assert card["training_boundary"]["sealed_splits_not_loaded"] == [
        "test_id",
        "test_ood",
        "adversarial",
    ]
    assert load_sft_samples(
        tmp_path / "sft" / "train.jsonl",
        expected_split="train",
    )
    assert (tmp_path / "report" / "dataset-card.json").exists()


def test_sft_sample_hash_rejects_tampering():
    trajectory = _with_split(
        collect_scripted_trajectory(
            generate_scenario(
                ScenarioFamily.secret_transport,
                variant_index=0,
                seed=11,
            )
        ),
        "train",
    )
    sample = build_sft_samples([trajectory], expected_split="train")[0][0]
    payload = json.loads(sample.json())
    payload["completion"][0]["content"] = '{"intent":"wait"}'

    with pytest.raises(ValidationError, match="content_hash mismatch"):
        SFTSample.parse_obj(payload)


def test_sft_semantic_hash_ignores_split_and_provenance_metadata():
    trajectory = collect_scripted_trajectory(
        generate_scenario(
            ScenarioFamily.secret_transport,
            variant_index=0,
            seed=11,
        )
    )
    train_sample = build_sft_samples(
        [_with_split(trajectory, "train")],
        expected_split="train",
    )[0][0]
    dev_sample = build_sft_samples(
        [_with_split(trajectory, "dev")],
        expected_split="dev",
    )[0][0]

    assert train_sample.data_split != dev_sample.data_split
    assert train_sample.content_hash == dev_sample.content_hash
    assert train_sample.sample_id == dev_sample.sample_id
