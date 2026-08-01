import json
from pathlib import Path

import pytest

from engine import (
    GRPOPolicy,
    LocalAdapterConfig,
    LocalGeneration,
    PlannerIntent,
    PlannerUsage,
    SFTPolicy,
    build_game_observation,
    create_core_tool_registry,
    inspect_adapter_checkpoint,
    planner_prompt_messages,
)
from examples.secret_letter import GUARD, LETTER, build_snapshot


class FakeBackend:
    def __init__(self, text):
        self.text = text
        self.messages = None

    def generate(self, messages):
        self.messages = list(messages)
        return LocalGeneration(
            text=self.text,
            usage=PlannerUsage(
                model_id="Qwen/Qwen3-0.6B",
                prompt_version="novelsim_planner_prompt.v3",
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                latency_ms=12.5,
            ),
        )


def _config(**updates):
    payload = {
        "model_id": "Qwen/Qwen3-0.6B",
        "adapter_path": "training/outputs/fake/final_adapter",
        "run_manifest_path": "training/outputs/fake/run-manifest.json",
    }
    payload.update(updates)
    return LocalAdapterConfig(**payload)


def _observation_and_tools():
    registry = create_core_tool_registry()
    observation = build_game_observation(build_snapshot(), GUARD, registry)
    tools = tuple(
        registry.get(name)
        for name in registry.names()
        if registry.get(name) is not None
    )
    return observation, tools


def test_sft_policy_generates_structured_decision_with_auditable_usage():
    observation, tools = _observation_and_tools()
    backend = FakeBackend(json.dumps({
        "intent": "interact",
        "tool_call": {
            "actor_id": GUARD,
            "tool_name": "pick_up",
            "arguments": {"item_id": LETTER},
        },
        "confidence": 0.9,
        "reason_summary": "grounded item is visible",
    }))
    policy = SFTPolicy(_config(), backend=backend)

    result = policy.decide_with_usage(observation, tools)

    assert result.decision.policy_id == "sft"
    assert result.decision.tool_call.tool_name == "pick_up"
    assert result.usage.total_tokens == 120
    assert result.usage.model_id == "Qwen/Qwen3-0.6B"
    assert backend.messages == planner_prompt_messages(observation)


def test_sft_policy_supports_grounded_wait_without_tool_call():
    observation, tools = _observation_and_tools()
    backend = FakeBackend(json.dumps({
        "intent": "wait",
        "tool_call": None,
        "confidence": 0.5,
        "reason_summary": "no grounded action",
    }))

    decision = SFTPolicy(_config(), backend=backend).decide(
        observation,
        tools,
    )

    assert decision.intent == PlannerIntent.wait
    assert decision.tool_call is None


def test_sft_and_grpo_policy_kinds_cannot_be_mislabeled():
    observation, tools = _observation_and_tools()
    backend = FakeBackend('{"intent":"wait","tool_call":null}')

    with pytest.raises(ValueError, match="policy_kind=sft"):
        SFTPolicy(_config(policy_kind="grpo"), backend=backend)

    grpo = GRPOPolicy(
        _config(policy_kind="grpo"),
        backend=backend,
    )
    assert grpo.decide(observation, tools).policy_id == "grpo"


def test_checkpoint_inspection_requires_manifest_and_exact_file_hashes(tmp_path):
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
    (adapter / "adapter_model.safetensors").write_bytes(b"fake-adapter")
    manifest_path = tmp_path / "run-manifest.json"
    config = _config(
        adapter_path=str(adapter),
        run_manifest_path=str(manifest_path),
    )

    before = inspect_adapter_checkpoint(config, repo_root=tmp_path)
    assert before.ready is False
    assert "run_manifest_missing" in before.errors

    manifest_path.write_text(json.dumps({
        "schema_version": "novelsim_sft_run_manifest.v1",
        "status": "completed",
        "config": {"model_id": "Qwen/Qwen3-0.6B"},
        "validation": {
            "prompt_version": "novelsim_planner_prompt.v3",
            "dataset_id": "novelsim_planner_sft_v3",
        },
        "code_commit": "abc1234",
        "adapter_files": before.adapter_files,
        "adapter_content_hash": before.adapter_content_hash,
    }), encoding="utf-8")

    ready = inspect_adapter_checkpoint(config, repo_root=tmp_path)
    assert ready.ready is True
    assert ready.errors == []
    assert ready.training_code_commit == "abc1234"
    assert ready.dataset_id == "novelsim_planner_sft_v3"

    (adapter / "adapter_model.safetensors").write_bytes(b"tampered")
    tampered = inspect_adapter_checkpoint(config, repo_root=tmp_path)
    assert tampered.ready is False
    assert "adapter_file_hash_mismatch" in tampered.errors
    assert "adapter_content_hash_mismatch" in tampered.errors


def test_grpo_checkpoint_requires_grpo_manifest_and_parent_sft_hashes(tmp_path):
    adapter = tmp_path / "grpo_adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text(json.dumps({
        "base_model_name_or_path": "Qwen/Qwen3-0.6B",
        "peft_type": "LORA",
        "task_type": "CAUSAL_LM",
    }), encoding="utf-8")
    (adapter / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (adapter / "adapter_model.safetensors").write_bytes(b"fake-grpo")
    manifest_path = tmp_path / "grpo-run-manifest.json"
    config = _config(
        policy_kind="grpo",
        adapter_path=str(adapter),
        run_manifest_path=str(manifest_path),
    )
    evidence = inspect_adapter_checkpoint(config, repo_root=tmp_path)
    manifest_path.write_text(json.dumps({
        "schema_version": "novelsim_grpo_run_manifest.v1",
        "status": "completed",
        "config": {"model_id": "Qwen/Qwen3-0.6B"},
        "validation": {
            "prompt_version": "novelsim_planner_prompt.v3",
            "dataset_id": "novelsim_planner_grpo_v3",
        },
        "parent_sft_checkpoint": {
            "adapter_content_hash": "parent-adapter-hash",
            "run_manifest_sha256": "parent-manifest-hash",
        },
        "code_commit": "def5678",
        "adapter_files": evidence.adapter_files,
        "adapter_content_hash": evidence.adapter_content_hash,
    }), encoding="utf-8")

    ready = inspect_adapter_checkpoint(config, repo_root=tmp_path)
    assert ready.ready is True
    assert ready.dataset_id == "novelsim_planner_grpo_v3"

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["parent_sft_checkpoint"] = {}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    missing_parent = inspect_adapter_checkpoint(config, repo_root=tmp_path)
    assert missing_parent.ready is False
    assert "grpo_parent_adapter_hash_missing" in missing_parent.errors
    assert "grpo_parent_manifest_hash_missing" in missing_parent.errors


@pytest.mark.parametrize(
    "updates,match",
    [
        ({"adapter_path": ""}, "cannot be empty"),
        ({"temperature": 0.2}, "temperature=0"),
        ({"trust_remote_code": True}, "trust_remote_code"),
        ({"require_cuda": False}, "CUDA path"),
    ],
)
def test_local_adapter_config_rejects_unaudited_runtime_modes(updates, match):
    payload = {
        "model_id": "Qwen/Qwen3-0.6B",
        "adapter_path": "adapter",
        "run_manifest_path": "run-manifest.json",
    }
    payload.update(updates)

    with pytest.raises(ValueError, match=match):
        LocalAdapterConfig(**payload)
