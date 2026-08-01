import io
import json
import tarfile
from pathlib import Path

import pytest

from training.server_handoff import (
    HANDOFF_MANIFEST_NAME,
    ServerSFTPipelineConfig,
    _latest_checkpoint,
    _write_model_card,
    build_handoff_archive,
    install_handoff_archive,
    load_server_pipeline_config,
    run_sft_smoke_pipeline,
    verify_handoff_archive,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "training/configs/server_sft_qwen3_0.6b_smoke.json"


def _temporary_config(tmp_path):
    checked_in = load_server_pipeline_config(CONFIG_PATH)
    return checked_in.copy(update={
        "archive_output": str(tmp_path / "handoff.tar.gz"),
        "manifest_output": str(tmp_path / "handoff-manifest.json"),
        "pipeline_report_output": str(tmp_path / "pipeline-report.json"),
        "model_card_output": str(tmp_path / "model-card.md"),
        "install_receipt_output": str(tmp_path / "handoff-install.json"),
    })


def test_handoff_archive_is_hash_verified_and_excludes_sealed_data(tmp_path):
    config = _temporary_config(tmp_path)

    built = build_handoff_archive(
        config,
        repo_root=REPO_ROOT,
        config_path=CONFIG_PATH,
        require_clean_worktree=False,
    )
    verified = verify_handoff_archive(config.archive_output, repo_root=REPO_ROOT)

    assert built["handoff_id"] == verified["handoff_id"]
    assert verified["valid"] is True
    assert verified["sealed_splits_included"] == []
    assert verified["dataset_validation"]["train_sample_count"] == 3060
    assert verified["dataset_validation"]["dev_sample_count"] == 340
    assert verified["dataset_validation"]["train_dev_content_hash_overlap"] == 0
    names = {item["path"] for item in verified["files"]}
    assert "data/sft/novelsim-planner-v5/train.jsonl" in names
    assert "data/sft/novelsim-planner-v5/dev.jsonl" in names
    assert not any("test_id" in name or "test_ood" in name for name in names)
    assert Path(config.manifest_output).is_file()


def test_handoff_builder_refuses_dirty_worktree(monkeypatch, tmp_path):
    config = _temporary_config(tmp_path)
    monkeypatch.setattr(
        "training.server_handoff._git_worktree_clean",
        lambda root: False,
    )

    with pytest.raises(ValueError, match="dirty Git worktree"):
        build_handoff_archive(
            config,
            repo_root=REPO_ROOT,
            config_path=CONFIG_PATH,
        )


def test_handoff_verifier_rejects_tampered_member(tmp_path):
    config = _temporary_config(tmp_path)
    build_handoff_archive(
        config,
        repo_root=REPO_ROOT,
        config_path=CONFIG_PATH,
        require_clean_worktree=False,
    )
    original = Path(config.archive_output)
    tampered = tmp_path / "tampered.tar.gz"
    with tarfile.open(original, "r:gz") as source, tarfile.open(tampered, "w:gz") as target:
        for member in source.getmembers():
            payload = source.extractfile(member).read()
            if member.name.endswith("dev.jsonl"):
                payload += b"tamper"
                member.size = len(payload)
            target.addfile(member, io.BytesIO(payload))

    with pytest.raises(ValueError, match="(size|hash) mismatch"):
        verify_handoff_archive(tampered, repo_root=REPO_ROOT)


def test_handoff_install_writes_then_reuses_and_refuses_different_file(tmp_path):
    config = _temporary_config(tmp_path)
    build_handoff_archive(
        config,
        repo_root=REPO_ROOT,
        config_path=CONFIG_PATH,
        require_clean_worktree=False,
    )
    target = tmp_path / "server-repo"
    target.mkdir()

    installed = install_handoff_archive(
        config.archive_output,
        repo_root=target,
        require_current_commit=False,
        receipt_path=config.install_receipt_output,
    )
    reused = install_handoff_archive(
        config.archive_output,
        repo_root=target,
        require_current_commit=False,
    )

    assert "data/sft/novelsim-planner-v5/train.jsonl" in installed["installed"]
    assert installed["reused"] == []
    assert (target / config.install_receipt_output).is_file()
    assert reused["valid"] is True
    assert "data/sft/novelsim-planner-v5/train.jsonl" in reused["reused"]
    assert reused["installed"] == []
    dev_path = target / "data/sft/novelsim-planner-v5/dev.jsonl"
    dev_path.write_text("different", encoding="utf-8")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        install_handoff_archive(
            config.archive_output,
            repo_root=target,
            require_current_commit=False,
        )


def test_server_pipeline_dry_run_validates_without_training(tmp_path):
    config = _temporary_config(tmp_path)

    report = run_sft_smoke_pipeline(
        config,
        repo_root=REPO_ROOT,
        execute=False,
        config_path=CONFIG_PATH,
    )

    assert report["passed"] is True
    assert report["status"] == "validated_no_training"
    assert report["executes_training"] is False
    assert report["dataset_validation"]["dataset_id"] == "novelsim_planner_sft_v5"
    assert report["checkpoint_preflight_before"]["ready"] is False
    assert json.loads(Path(config.pipeline_report_output).read_text(encoding="utf-8")) == report


def test_execute_refuses_to_train_without_verified_install_receipt(
    monkeypatch,
    tmp_path,
):
    config = _temporary_config(tmp_path)
    monkeypatch.setattr(
        "training.server_handoff._git_worktree_clean",
        lambda root: True,
    )

    report = run_sft_smoke_pipeline(
        config,
        repo_root=REPO_ROOT,
        execute=True,
        config_path=CONFIG_PATH,
    )

    assert report["passed"] is False
    assert report["status"] == "failed"
    assert "install receipt is missing" in report["error"]
    assert not (tmp_path / "model-card.md").exists()


def test_latest_checkpoint_uses_highest_numeric_step(tmp_path):
    for name in ("checkpoint-25", "checkpoint-100", "checkpoint-bad"):
        (tmp_path / name).mkdir()

    assert _latest_checkpoint(tmp_path).name == "checkpoint-100"


def test_model_card_marks_smoke_scope_and_runtime_evidence(tmp_path):
    path = tmp_path / "model-card.md"
    _write_model_card(
        path,
        {
            "status": "completed",
            "config": {"model_id": "Qwen/Qwen3-0.6B"},
            "validation": {
                "dataset_id": "novelsim_planner_sft_v5",
                "train_sample_count": 3060,
                "dev_sample_count": 340,
                "prompt_version": "novelsim_planner_prompt.v4",
            },
            "environment": {"gpu_name": "NVIDIA GeForce RTX 4090", "gpu_memory_gib": 23.99},
            "resource_usage": {"peak_memory_allocated_gib": 4.2, "peak_memory_reserved_gib": 5.1},
            "token_length_audit": {"sample_count": 3400, "p50": 900, "p95": 1100, "max": 1200, "over_limit_count": 0},
            "metrics": {"train_loss": 0.5},
            "code_commit": "abc123",
            "adapter_content_hash": "adapter-hash",
        },
        {
            "scenario_id": "resource_negotiation_v005_s000001",
            "passed": True,
            "decision_count": 3,
            "schema_accepted_count": 3,
            "gate_accepted_count": 3,
            "objective_satisfied": True,
            "replay_consistent": True,
            "illegal_proposal_count": 0,
            "illegal_commit_count": 0,
        },
    )

    content = path.read_text(encoding="utf-8")
    assert "NVIDIA GeForce RTX 4090" in content
    assert "3/3/3" in content
    assert "not the final 4B model" in content


def test_pipeline_config_rejects_aliased_outputs():
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    payload["manifest_output"] = payload["archive_output"]

    with pytest.raises(ValueError, match="distinct paths"):
        ServerSFTPipelineConfig.parse_obj(payload)
