import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from engine import (
    LocalAdapterConfig,
    LocalGeneration,
    PlannerUsage,
    SFTPolicy,
    inspect_adapter_checkpoint,
)
from training.checkpoint_smoke import (
    CheckpointSmokeConfig,
    execute_checkpoint_smoke,
    inspect_checkpoint_smoke,
    load_checkpoint_smoke_config,
)
from training.scenario_generator import ScenarioFamily, generate_scenario


REPO_ROOT = Path(__file__).resolve().parents[2]


class ScriptedLocalBackend:
    def __init__(self, calls):
        self.calls = list(calls)
        self.index = 0

    def generate(self, messages):
        call = self.calls[self.index]
        self.index += 1
        return LocalGeneration(
            text=json.dumps({
                "intent": "interact",
                "tool_call": call.dict(),
                "confidence": 0.9,
                "reason_summary": "fake checkpoint grounded action",
            }),
            usage=PlannerUsage(
                model_id="Qwen/Qwen3-0.6B",
                prompt_version="novelsim_planner_prompt.v1",
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                latency_ms=10.0,
            ),
        )


def _write_fake_checkpoint(tmp_path):
    adapter = tmp_path / "final_adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text(json.dumps({
        "base_model_name_or_path": "Qwen/Qwen3-0.6B",
        "peft_type": "LORA",
        "task_type": "CAUSAL_LM",
    }), encoding="utf-8")
    (adapter / "tokenizer_config.json").write_text(
        json.dumps({"model_max_length": 32768}),
        encoding="utf-8",
    )
    (adapter / "adapter_model.safetensors").write_bytes(b"fake")
    manifest_path = tmp_path / "run-manifest.json"
    local_config = LocalAdapterConfig(
        model_id="Qwen/Qwen3-0.6B",
        adapter_path=str(adapter),
        run_manifest_path=str(manifest_path),
    )
    evidence = inspect_adapter_checkpoint(local_config, repo_root=tmp_path)
    manifest_path.write_text(json.dumps({
        "schema_version": "novelsim_sft_run_manifest.v1",
        "status": "completed",
        "config": {"model_id": "Qwen/Qwen3-0.6B"},
        "validation": {
            "prompt_version": "novelsim_planner_prompt.v1",
            "dataset_id": "novelsim_planner_sft_v1",
        },
        "code_commit": "fake-training-commit",
        "adapter_files": evidence.adapter_files,
        "adapter_content_hash": evidence.adapter_content_hash,
    }), encoding="utf-8")
    config_path = tmp_path / "local-policy.json"
    config_path.write_text(local_config.json(), encoding="utf-8")
    assert inspect_adapter_checkpoint(local_config, repo_root=tmp_path).ready
    return local_config, config_path


def test_checked_in_checkpoint_smoke_preflight_is_honest_before_training():
    config = load_checkpoint_smoke_config(
        REPO_ROOT / "training/configs/checkpoint_smoke_qwen3_0.6b.json"
    )

    report = inspect_checkpoint_smoke(config, repo_root=REPO_ROOT)

    assert report["ready"] is False
    assert report["executes_model"] is False
    assert report["data_split"] == "dev"
    assert report["scenario_content_hash"]
    assert "checkpoint:run_manifest_missing" in report["errors"]
    assert "checkpoint:adapter_directory_missing" in report["errors"]


def test_checkpoint_smoke_config_forbids_train_and_test_splits():
    payload = json.loads(
        (
            REPO_ROOT / "training/configs/checkpoint_smoke_qwen3_0.6b.json"
        ).read_text(encoding="utf-8")
    )
    for split in ("train", "test_id", "test_ood"):
        payload["data_split"] = split
        with pytest.raises(ValidationError, match="dev split"):
            CheckpointSmokeConfig.parse_obj(payload)


def test_fake_checkpoint_runs_inference_gate_and_replay_on_dev(tmp_path):
    local_config, local_config_path = _write_fake_checkpoint(tmp_path)
    scenario = generate_scenario(
        ScenarioFamily.resource_negotiation,
        variant_index=5,
        seed=1,
    )
    policy = SFTPolicy(
        local_config,
        backend=ScriptedLocalBackend(scenario.scripted_calls),
    )
    config = CheckpointSmokeConfig(
        config_id="fake-checkpoint-smoke",
        local_policy_config=str(local_config_path),
        scenario_manifest=str(
            REPO_ROOT / "training/manifests/scenario-split-v1.json"
        ),
        scenario_id=scenario.scenario_id,
        max_turns=6,
        trajectory_output=str(tmp_path / "trajectory.jsonl"),
        report_output=str(tmp_path / "report.json"),
    )

    report = execute_checkpoint_smoke(
        config,
        repo_root=REPO_ROOT,
        policy=policy,
    )

    assert report["passed"] is True
    assert report["data_split"] == "dev"
    assert report["decision_count"] == 3
    assert report["schema_accepted_count"] == 3
    assert report["gate_accepted_count"] == 3
    assert report["generation_error_count"] == 0
    assert report["illegal_proposal_count"] == 0
    assert report["illegal_commit_count"] == 0
    assert report["objective_satisfied"] is True
    assert report["replay_consistent"] is True
    assert report["total_tokens"] == 360
    assert (tmp_path / "trajectory.jsonl").exists()
    assert (tmp_path / "report.json").exists()
