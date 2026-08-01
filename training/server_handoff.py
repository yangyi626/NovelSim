"""Audited data handoff and one-command SFT smoke pipeline for a 4090 server.

The Git repository intentionally excludes generated JSONL datasets and model
weights.  This module packages only the frozen Train/Dev artifacts required by
the 0.6B smoke, verifies them on the server, installs without overwriting
different files, and executes SFT -> checkpoint Runtime smoke with an auditable
report.  Heavy CUDA imports remain inside ``training.train_sft.run_training``.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import subprocess
import tarfile
from time import perf_counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field, root_validator, validator

from engine import inspect_adapter_checkpoint, load_local_adapter_config

from .checkpoint_smoke import (
    execute_checkpoint_smoke,
    inspect_checkpoint_smoke,
    load_checkpoint_smoke_config,
)
from .train_sft import load_training_config, run_training, validate_training_run


HANDOFF_MANIFEST_NAME = "handoff-manifest.json"


class ServerSFTPipelineConfig(BaseModel):
    schema_version: str = "novelsim_server_sft_pipeline_config.v1"
    handoff_id: str
    required_git_branch: str
    sft_config: str
    checkpoint_smoke_config: str
    requirements_file: str = "training/requirements-sft.txt"
    archive_output: str
    manifest_output: str
    pipeline_report_output: str
    model_card_output: str
    install_receipt_output: str

    class Config:
        extra = "forbid"
        allow_mutation = False

    @validator(
        "handoff_id",
        "required_git_branch",
        "sft_config",
        "checkpoint_smoke_config",
        "requirements_file",
        "archive_output",
        "manifest_output",
        "pipeline_report_output",
        "model_card_output",
        "install_receipt_output",
    )
    def _non_empty(cls, value):
        if not value.strip():
            raise ValueError("server handoff fields cannot be empty")
        return value

    @validator("schema_version")
    def _known_schema(cls, value):
        if value != "novelsim_server_sft_pipeline_config.v1":
            raise ValueError("unsupported server pipeline config schema")
        return value

    @root_validator(skip_on_failure=True)
    def _separate_outputs(cls, values):
        outputs = [
            values.get("archive_output"),
            values.get("manifest_output"),
            values.get("pipeline_report_output"),
            values.get("model_card_output"),
            values.get("install_receipt_output"),
        ]
        if len(set(outputs)) != len(outputs):
            raise ValueError("server pipeline outputs must use distinct paths")
        return values


def load_server_pipeline_config(config_path) -> ServerSFTPipelineConfig:
    return ServerSFTPipelineConfig.parse_raw(
        Path(config_path).read_text(encoding="utf-8")
    )


def build_handoff_archive(
    config: ServerSFTPipelineConfig,
    *,
    repo_root=None,
    config_path=None,
    require_clean_worktree: bool = True,
) -> Dict[str, Any]:
    root = _root(repo_root)
    if require_clean_worktree and not _git_worktree_clean(root):
        raise ValueError("refusing to build handoff from a dirty Git worktree")
    sft_path = _resolve(root, config.sft_config)
    checkpoint_path = _resolve(root, config.checkpoint_smoke_config)
    requirements_path = _resolve(root, config.requirements_file)
    pipeline_config_path = (
        Path(config_path) if config_path is not None else None
    )
    if pipeline_config_path is not None and not pipeline_config_path.is_absolute():
        pipeline_config_path = root / pipeline_config_path

    sft_config = load_training_config(sft_path)
    validation = validate_training_run(sft_config, repo_root=root)
    checkpoint_config = load_checkpoint_smoke_config(checkpoint_path)
    if checkpoint_config.data_split != "dev" or checkpoint_config.policy_kind != "sft":
        raise ValueError("server SFT handoff requires an SFT Dev-only checkpoint smoke")
    local_policy_path = _resolve(root, checkpoint_config.local_policy_config)
    scenario_manifest_path = _resolve(root, checkpoint_config.scenario_manifest)
    local_policy = load_local_adapter_config(local_policy_path)
    if local_policy.model_id != sft_config.model_id:
        raise ValueError("training and Runtime smoke model IDs differ")
    expected_adapter = _resolve(root, sft_config.output_dir) / "final_adapter"
    expected_run_manifest = _resolve(root, sft_config.output_dir) / "run-manifest.json"
    if _resolve(root, local_policy.adapter_path).resolve() != expected_adapter.resolve():
        raise ValueError("local policy adapter path does not match SFT output")
    if _resolve(root, local_policy.run_manifest_path).resolve() != expected_run_manifest.resolve():
        raise ValueError("local policy run manifest does not match SFT output")

    source_paths = [
        sft_path,
        _resolve(root, sft_config.train_file),
        _resolve(root, sft_config.dev_file),
        _resolve(root, sft_config.dataset_card),
        checkpoint_path,
        local_policy_path,
        scenario_manifest_path,
        requirements_path,
    ]
    if pipeline_config_path is not None:
        source_paths.append(pipeline_config_path)
    source_paths = _unique_paths(source_paths)
    records = [_file_record(path, root=root) for path in source_paths]
    relative_names = {record["path"] for record in records}
    if any("test_id" in name.lower() or "test_ood" in name.lower() for name in relative_names):
        raise ValueError("sealed test artifacts cannot enter the server handoff")
    if not {
        _relative_name(root, _resolve(root, sft_config.train_file)),
        _relative_name(root, _resolve(root, sft_config.dev_file)),
    }.issubset(relative_names):
        raise ValueError("handoff is missing Train/Dev data")

    manifest = {
        "schema_version": "novelsim_server_handoff_manifest.v1",
        "handoff_id": config.handoff_id,
        "required_git_branch": config.required_git_branch,
        "code_commit": _git_value(root, ["rev-parse", "HEAD"]),
        "dataset_validation": validation,
        "training_config": config.sft_config,
        "checkpoint_smoke_config": config.checkpoint_smoke_config,
        "sealed_splits_included": [],
        "files": records,
    }
    manifest_bytes = _json_bytes(manifest)
    archive_path = _resolve(root, config.archive_output)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                _add_bytes(archive, HANDOFF_MANIFEST_NAME, manifest_bytes)
                for path, record in zip(source_paths, records):
                    _add_file(archive, path, record["path"])

    result = dict(manifest)
    result["archive"] = _absolute_file_record(archive_path)
    manifest_path = _resolve(root, config.manifest_output)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(_json_bytes(result))
    return result


def verify_handoff_archive(
    archive_path,
    *,
    repo_root=None,
    require_current_commit: bool = False,
) -> Dict[str, Any]:
    root = _root(repo_root)
    path = _resolve(root, str(archive_path))
    if not path.is_file():
        raise ValueError("handoff archive is missing: %s" % path)
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        _validate_archive_names(names)
        if names.count(HANDOFF_MANIFEST_NAME) != 1:
            raise ValueError("handoff archive must contain one manifest")
        manifest = json.loads(
            _read_member(archive, HANDOFF_MANIFEST_NAME).decode("utf-8")
        )
        if manifest.get("schema_version") != "novelsim_server_handoff_manifest.v1":
            raise ValueError("unsupported handoff manifest schema")
        file_records = manifest.get("files", [])
        expected = {record["path"]: record for record in file_records}
        if len(expected) != len(file_records):
            raise ValueError("handoff manifest contains duplicate file records")
        if set(names) != set(expected) | {HANDOFF_MANIFEST_NAME}:
            raise ValueError("archive members do not match the handoff manifest")
        for name, record in expected.items():
            payload = _read_member(archive, name)
            if len(payload) != record.get("bytes"):
                raise ValueError("handoff file size mismatch: %s" % name)
            if hashlib.sha256(payload).hexdigest() != record.get("sha256"):
                raise ValueError("handoff file hash mismatch: %s" % name)
    if manifest.get("sealed_splits_included"):
        raise ValueError("handoff unexpectedly contains sealed splits")
    current_commit = _git_value(root, ["rev-parse", "HEAD"], required=False)
    if require_current_commit and current_commit != manifest.get("code_commit"):
        raise ValueError("server checkout commit does not match handoff manifest")
    result = dict(manifest)
    result.update({
        "valid": True,
        "current_code_commit": current_commit,
        "code_commit_matches": current_commit == manifest.get("code_commit"),
        "archive": _absolute_file_record(path),
    })
    return result


def install_handoff_archive(
    archive_path,
    *,
    repo_root=None,
    require_current_commit: bool = True,
    receipt_path=None,
) -> Dict[str, Any]:
    root = _root(repo_root)
    verification = verify_handoff_archive(
        archive_path,
        repo_root=root,
        require_current_commit=require_current_commit,
    )
    installed: List[str] = []
    reused: List[str] = []
    path = _resolve(root, str(archive_path))
    with tarfile.open(path, mode="r:gz") as archive:
        for record in verification["files"]:
            name = record["path"]
            target = root / Path(name)
            if target.is_file():
                if _sha256(target) != record["sha256"]:
                    raise ValueError("refusing to overwrite different file: %s" % name)
                reused.append(name)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".handoff.tmp")
            temporary.write_bytes(_read_member(archive, name))
            if _sha256(temporary) != record["sha256"]:
                temporary.unlink(missing_ok=True)
                raise RuntimeError("installed handoff file failed hash verification")
            temporary.replace(target)
            installed.append(name)
    result = {
        "schema_version": "novelsim_server_handoff_install.v1",
        "handoff_id": verification["handoff_id"],
        "valid": True,
        "installed": installed,
        "reused": reused,
        "code_commit": verification["code_commit"],
        "archive": verification["archive"],
    }
    if receipt_path is not None:
        output = _resolve(root, str(receipt_path))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(_json_bytes(result))
    return result


def run_sft_smoke_pipeline(
    config: ServerSFTPipelineConfig,
    *,
    repo_root=None,
    execute: bool = False,
    config_path=None,
) -> Dict[str, Any]:
    root = _root(repo_root)
    started = perf_counter()
    sft_path = _resolve(root, config.sft_config)
    checkpoint_path = _resolve(root, config.checkpoint_smoke_config)
    sft_config = load_training_config(sft_path)
    checkpoint_config = load_checkpoint_smoke_config(checkpoint_path)
    report: Dict[str, Any] = {
        "schema_version": "novelsim_server_sft_pipeline_report.v1",
        "handoff_id": config.handoff_id,
        "executes_training": execute,
        "code_commit": _git_value(root, ["rev-parse", "HEAD"], required=False),
        "git_branch": _git_value(
            root, ["rev-parse", "--abbrev-ref", "HEAD"], required=False
        ),
        "required_git_branch": config.required_git_branch,
        "stages": [],
        "passed": False,
    }
    report_path = _resolve(root, config.pipeline_report_output)
    try:
        validation = validate_training_run(sft_config, repo_root=root)
        report["dataset_validation"] = validation
        report["stages"].append({"stage": "dataset_preflight", "status": "passed"})
        before = inspect_checkpoint_smoke(checkpoint_config, repo_root=root)
        report["checkpoint_preflight_before"] = before
        if not execute:
            report.update({
                "status": "validated_no_training",
                "passed": True,
                "next_command": (
                    "python -m training.server_handoff run --config %s --execute"
                    % (str(config_path) if config_path is not None else "<config>")
                ),
            })
            report["stages"].append({"stage": "cuda_training", "status": "not_executed"})
            report["wall_time_seconds"] = round(perf_counter() - started, 3)
            return _write_pipeline_report(report_path, report)

        if report["git_branch"] != config.required_git_branch:
            raise RuntimeError("server checkout is on the wrong Git branch")
        if not _git_worktree_clean(root):
            raise RuntimeError("server Git worktree must be clean before training")
        receipt_path = _resolve(root, config.install_receipt_output)
        if not receipt_path.is_file():
            raise RuntimeError("verified handoff install receipt is missing")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("handoff_id") != config.handoff_id:
            raise RuntimeError("handoff install receipt identity mismatch")
        if receipt.get("code_commit") != report["code_commit"]:
            raise RuntimeError("handoff install receipt commit mismatch")
        report["install_receipt"] = _absolute_file_record(receipt_path)
        local_policy = load_local_adapter_config(
            _resolve(root, checkpoint_config.local_policy_config)
        )
        existing = inspect_adapter_checkpoint(local_policy, repo_root=root)
        if existing.ready:
            report["stages"].append({
                "stage": "sft_training",
                "status": "reused_completed_checkpoint",
                "adapter_content_hash": existing.adapter_content_hash,
            })
        else:
            resume_path = _latest_checkpoint(_resolve(root, sft_config.output_dir))
            effective = sft_config
            if resume_path is not None:
                effective = sft_config.copy(update={
                    "resume_from_checkpoint": str(resume_path),
                })
            training_manifest = run_training(
                effective,
                repo_root=root,
                config_path=sft_path,
            )
            report["training_manifest"] = _training_summary(training_manifest)
            report["stages"].append({
                "stage": "sft_training",
                "status": "passed",
                "resumed_from": str(resume_path) if resume_path is not None else None,
            })

        after = inspect_checkpoint_smoke(checkpoint_config, repo_root=root)
        report["checkpoint_preflight_after"] = after
        if not after["ready"]:
            raise RuntimeError(
                "trained checkpoint failed Runtime preflight: %s"
                % ", ".join(after["errors"])
            )
        runtime_report = execute_checkpoint_smoke(
            checkpoint_config,
            repo_root=root,
        )
        report["runtime_smoke"] = runtime_report
        manifest_path = _resolve(root, local_policy.run_manifest_path)
        completed_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        model_card_path = _resolve(root, config.model_card_output)
        _write_model_card(model_card_path, completed_manifest, runtime_report)
        report["model_card"] = _absolute_file_record(model_card_path)
        report["stages"].append({
            "stage": "checkpoint_runtime_smoke",
            "status": "passed" if runtime_report["passed"] else "failed",
        })
        report["passed"] = bool(runtime_report["passed"])
        report["status"] = "completed" if report["passed"] else "failed"
    except Exception as exc:
        report.update({
            "status": "failed",
            "passed": False,
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:1000]),
        })
    report["wall_time_seconds"] = round(perf_counter() - started, 3)
    return _write_pipeline_report(report_path, report)


def _training_summary(manifest: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": manifest.get("schema_version"),
        "status": manifest.get("status"),
        "code_commit": manifest.get("code_commit"),
        "adapter_content_hash": manifest.get("adapter_content_hash"),
        "token_length_audit": manifest.get("token_length_audit"),
        "environment": manifest.get("environment"),
        "resource_usage": manifest.get("resource_usage"),
        "metrics": manifest.get("metrics"),
    }


def _write_model_card(
    path: Path,
    manifest: Dict[str, Any],
    runtime_report: Dict[str, Any],
) -> None:
    validation = manifest.get("validation", {})
    environment = manifest.get("environment", {})
    resource = manifest.get("resource_usage", {})
    metrics = manifest.get("metrics", {})
    token_audit = manifest.get("token_length_audit", {})
    config = manifest.get("config", {})
    text = """# NovelSim Qwen3-0.6B Planner SFT Smoke Model Card

## Status

- Artifact status: `{status}`
- Base model: `{model_id}`
- Training method: single-GPU 4-bit NF4 QLoRA, completion-only loss
- Dataset: `{dataset_id}` (`{train_count}` Train / `{dev_count}` Dev)
- Prompt contract: `{prompt_version}`
- Code commit: `{code_commit}`
- Adapter content hash: `{adapter_hash}`

## Training evidence

- GPU: `{gpu_name}` ({gpu_memory} GiB)
- Peak CUDA allocated/reserved: `{peak_allocated}` / `{peak_reserved}` GiB
- Token audit: {token_count} samples, P50/P95/max `{token_p50}/{token_p95}/{token_max}`, over limit `{over_limit}`
- Train loss: `{train_loss}`

## Runtime smoke

- Dev scenario: `{scenario_id}`
- Passed: `{runtime_passed}`
- Decisions / Schema accepted / Gate accepted: `{decisions}/{schema_accepted}/{gate_accepted}`
- Objective satisfied / Replay consistent: `{objective}` / `{replay}`
- Illegal proposals / illegal commits: `{illegal_proposals}` / `{illegal_commits}`

## Intended use and limitations

This adapter is a 100-step pipeline smoke for NovelSim high-level structured NPC
planning. It is not the final 4B model, has not been evaluated on sealed Test-ID
or Test-OOD splits, and must not be presented as evidence that SFT or GRPO
improves the final benchmark. World mutation remains authoritative in the
ToolRegistry/FSM/Gate Runtime; model output cannot directly patch state.
""".format(
        status=manifest.get("status", "unknown"),
        model_id=config.get("model_id", "unknown"),
        dataset_id=validation.get("dataset_id", "unknown"),
        train_count=validation.get("train_sample_count", "unknown"),
        dev_count=validation.get("dev_sample_count", "unknown"),
        prompt_version=validation.get("prompt_version", "unknown"),
        code_commit=manifest.get("code_commit", "unknown"),
        adapter_hash=manifest.get("adapter_content_hash", "unknown"),
        gpu_name=environment.get("gpu_name", "unknown"),
        gpu_memory=environment.get("gpu_memory_gib", "unknown"),
        peak_allocated=resource.get("peak_memory_allocated_gib", "unknown"),
        peak_reserved=resource.get("peak_memory_reserved_gib", "unknown"),
        token_count=token_audit.get("sample_count", "unknown"),
        token_p50=token_audit.get("p50", "unknown"),
        token_p95=token_audit.get("p95", "unknown"),
        token_max=token_audit.get("max", "unknown"),
        over_limit=token_audit.get("over_limit_count", "unknown"),
        train_loss=metrics.get("train_loss", "unknown"),
        scenario_id=runtime_report.get("scenario_id", "unknown"),
        runtime_passed=runtime_report.get("passed", False),
        decisions=runtime_report.get("decision_count", 0),
        schema_accepted=runtime_report.get("schema_accepted_count", 0),
        gate_accepted=runtime_report.get("gate_accepted_count", 0),
        objective=runtime_report.get("objective_satisfied", False),
        replay=runtime_report.get("replay_consistent", False),
        illegal_proposals=runtime_report.get("illegal_proposal_count", 0),
        illegal_commits=runtime_report.get("illegal_commit_count", 0),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _latest_checkpoint(output_dir: Path) -> Optional[Path]:
    if not output_dir.is_dir():
        return None
    candidates = []
    for path in output_dir.glob("checkpoint-*"):
        if not path.is_dir():
            continue
        try:
            step = int(path.name.rsplit("-", 1)[1])
        except (IndexError, ValueError):
            continue
        candidates.append((step, path))
    return max(candidates, default=(0, None), key=lambda item: item[0])[1]


def _write_pipeline_report(path: Path, report: Dict[str, Any]) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(report))
    return report


def _unique_paths(paths: Sequence[Path]) -> List[Path]:
    result: List[Path] = []
    seen = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        if not resolved.is_file():
            raise ValueError("required handoff artifact missing: %s" % path)
        seen.add(resolved)
        result.append(resolved)
    return result


def _file_record(path: Path, *, root: Path) -> Dict[str, Any]:
    return {
        "path": _relative_name(root, path),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _absolute_file_record(path: Path) -> Dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _relative_name(root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("handoff artifact must stay inside the repository") from exc
    return relative.as_posix()


def _validate_archive_names(names: Sequence[str]) -> None:
    if len(names) != len(set(names)):
        raise ValueError("handoff archive contains duplicate members")
    for name in names:
        candidate = Path(name)
        if candidate.is_absolute() or ".." in candidate.parts or "\\" in name:
            raise ValueError("unsafe handoff archive member: %s" % name)


def _add_bytes(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(payload)
    info.mtime = 0
    info.mode = 0o644
    archive.addfile(info, io.BytesIO(payload))


def _add_file(archive: tarfile.TarFile, path: Path, name: str) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = path.stat().st_size
    info.mtime = 0
    info.mode = 0o644
    with path.open("rb") as source:
        archive.addfile(info, source)


def _read_member(archive: tarfile.TarFile, name: str) -> bytes:
    member = archive.getmember(name)
    if not member.isfile():
        raise ValueError("handoff member is not a regular file: %s" % name)
    source = archive.extractfile(member)
    if source is None:
        raise ValueError("cannot read handoff member: %s" % name)
    return source.read()


def _json_bytes(value: Dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_value(root: Path, args: Sequence[str], *, required: bool = True) -> str:
    try:
        return subprocess.check_output(
            ["git"] + list(args),
            cwd=str(root),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        if required:
            raise ValueError("Git metadata is required for server handoff")
        return ""


def _git_worktree_clean(root: Path) -> bool:
    try:
        output = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("Git worktree status is required for server handoff") from exc
    return not output.strip()


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _root(repo_root=None) -> Path:
    return Path(repo_root).resolve() if repo_root is not None else Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build and run the 4090 SFT smoke handoff")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "verify", "install", "run"):
        command = subparsers.add_parser(name)
        command.add_argument("--config", required=True)
        if name in {"verify", "install"}:
            command.add_argument("--archive")
        if name == "install":
            command.add_argument("--allow-commit-mismatch", action="store_true")
        if name == "run":
            command.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    config = load_server_pipeline_config(args.config)
    if args.command == "build":
        result = build_handoff_archive(config, config_path=args.config)
    elif args.command == "verify":
        result = verify_handoff_archive(args.archive or config.archive_output)
    elif args.command == "install":
        result = install_handoff_archive(
            args.archive or config.archive_output,
            require_current_commit=not args.allow_commit_mismatch,
            receipt_path=config.install_receipt_output,
        )
    else:
        result = run_sft_smoke_pipeline(
            config,
            execute=args.execute,
            config_path=args.config,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    if args.command == "run" and not result.get("passed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
