"""Single-GPU QLoRA GRPO entry point for the NovelSim planner.

Heavy CUDA imports stay lazy.  ``--validate-only`` verifies dataset hashes,
split boundaries, the deterministic reward audit and the parent SFT adapter
without loading a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, root_validator, validator

from engine import (
    inspect_adapter_checkpoint,
    load_local_adapter_config,
)
from engine.planner_prompt import PLANNER_PROMPT_VERSION

from .build_grpo_dataset import (
    GRPO_DATASET_ID,
    GRPOPromptSample,
    load_grpo_samples,
)
from .export_trajectories import load_trajectories_jsonl
from .reward_audit import RewardAuditReport, run_reward_audit
from .rewards import REWARD_PROFILES, make_trl_reward_function


class GRPOTrainingConfig(BaseModel):
    schema_version: str = "novelsim_grpo_training_config.v1"
    run_name: str
    model_id: str
    model_revision: str = "main"
    sft_policy_config: str
    train_file: str
    dev_file: str
    dataset_card: str
    dev_trajectory_file: str
    reward_audit_report: str
    output_dir: str
    reward_profile: str = "mixed"
    max_prompt_length: int = Field(2048, ge=512)
    max_completion_length: int = Field(512, ge=64, le=2048)
    max_steps: int = Field(50, ge=1)
    num_train_epochs: float = Field(1.0, gt=0.0)
    micro_batch_size: int = Field(1, ge=1)
    eval_batch_size: int = Field(1, ge=1)
    gradient_accumulation_steps: int = Field(4, ge=1)
    num_generations: int = Field(4, ge=2, le=16)
    num_generations_eval: int = Field(4, ge=2, le=16)
    learning_rate: float = Field(1e-5, gt=0.0)
    weight_decay: float = Field(0.0, ge=0.0)
    warmup_ratio: float = Field(0.03, ge=0.0, lt=1.0)
    max_grad_norm: float = Field(0.3, gt=0.0)
    beta: float = Field(0.0, ge=0.0)
    epsilon: float = Field(0.2, gt=0.0, lt=1.0)
    loss_type: str = "dr_grpo"
    scale_rewards: str = "group"
    temperature: float = Field(0.7, gt=0.0, le=2.0)
    top_p: float = Field(0.95, gt=0.0, le=1.0)
    logging_steps: int = Field(1, ge=1)
    eval_steps: int = Field(10, ge=1)
    save_steps: int = Field(10, ge=1)
    save_total_limit: int = Field(2, ge=1)
    seed: int = 20260801
    gradient_checkpointing: bool = True
    bf16: bool = True
    tf32: bool = True
    attention_implementation: str = "sdpa"
    optimizer: str = "paged_adamw_8bit"
    report_to: List[str] = Field(default_factory=lambda: ["tensorboard"])
    use_vllm: bool = False
    vllm_mode: str = "colocate"
    vllm_gpu_memory_utilization: float = Field(0.2, gt=0.0, lt=1.0)
    vllm_enable_sleep_mode: bool = True
    vllm_max_model_length: int = Field(2560, ge=576)
    require_cuda: bool = True
    require_single_gpu: bool = True
    minimum_gpu_memory_gib: float = Field(20.0, ge=0.0)
    trust_remote_code: bool = False
    resume_from_checkpoint: Optional[str] = None

    class Config:
        extra = "forbid"
        allow_mutation = False

    @validator(
        "run_name",
        "model_id",
        "sft_policy_config",
        "train_file",
        "dev_file",
        "dataset_card",
        "dev_trajectory_file",
        "reward_audit_report",
        "output_dir",
    )
    def _non_empty(cls, value):
        if not value.strip():
            raise ValueError("GRPO identity and artifact paths cannot be empty")
        return value

    @validator("schema_version")
    def _known_schema(cls, value):
        if value != "novelsim_grpo_training_config.v1":
            raise ValueError("unsupported GRPO training config schema")
        return value

    @validator("reward_profile")
    def _known_reward_profile(cls, value):
        if value not in REWARD_PROFILES:
            raise ValueError("unknown reward profile")
        return value

    @validator("loss_type")
    def _supported_loss(cls, value):
        if value not in {"dr_grpo", "dapo"}:
            raise ValueError("NovelSim supports dr_grpo or dapo loss")
        return value

    @validator("scale_rewards")
    def _supported_scaling(cls, value):
        if value not in {"group", "none"}:
            raise ValueError("scale_rewards must be group or none")
        return value

    @root_validator(skip_on_failure=True)
    def _single_gpu_group_contract(cls, values):
        effective = values.get("micro_batch_size", 1) * values.get(
            "gradient_accumulation_steps", 1
        )
        generations = values.get("num_generations", 1)
        if effective % generations:
            raise ValueError(
                "effective train batch must be divisible by num_generations"
            )
        if values.get("save_steps", 1) % values.get("eval_steps", 1):
            raise ValueError("save_steps must be a multiple of eval_steps")
        if values.get("vllm_mode") != "colocate":
            raise ValueError("single-GPU NovelSim GRPO only supports colocate vLLM")
        if values.get("trust_remote_code"):
            raise ValueError("NovelSim GRPO forbids trust_remote_code")
        required_length = values.get("max_prompt_length", 0) + values.get(
            "max_completion_length", 0
        )
        if values.get("use_vllm") and values.get("vllm_max_model_length", 0) < required_length:
            raise ValueError("vLLM model length is smaller than prompt plus completion")
        return values


def load_grpo_training_config(config_path) -> GRPOTrainingConfig:
    return GRPOTrainingConfig.parse_raw(
        Path(config_path).read_text(encoding="utf-8")
    )


def validate_grpo_training_run(
    config: GRPOTrainingConfig,
    *,
    repo_root=None,
) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    paths = {
        "train": _resolve(root, config.train_file),
        "dev": _resolve(root, config.dev_file),
        "card": _resolve(root, config.dataset_card),
        "dev_trajectory": _resolve(root, config.dev_trajectory_file),
        "reward_audit": _resolve(root, config.reward_audit_report),
        "sft_policy": _resolve(root, config.sft_policy_config),
    }
    for name, path in paths.items():
        if not path.is_file():
            raise ValueError("required GRPO artifact missing: %s=%s" % (name, path))
    if paths["train"].resolve() == paths["dev"].resolve():
        raise ValueError("GRPO train and dev files must differ")

    card = json.loads(paths["card"].read_text(encoding="utf-8"))
    if card.get("schema_version") != "novelsim_grpo_dataset_card.v1":
        raise ValueError("unexpected GRPO dataset card schema")
    if card.get("dataset_id") != GRPO_DATASET_ID:
        raise ValueError("unexpected GRPO dataset id")
    if card.get("prompt_version") != PLANNER_PROMPT_VERSION:
        raise ValueError("unexpected GRPO prompt version")
    boundary = card.get("training_boundary", {})
    if boundary.get("optimization_split") != "train":
        raise ValueError("GRPO train boundary is invalid")
    if boundary.get("checkpoint_selection_split") != "dev":
        raise ValueError("GRPO dev boundary is invalid")
    sealed = set(boundary.get("sealed_splits_not_loaded", []))
    if not {"test_id", "test_ood"}.issubset(sealed):
        raise ValueError("GRPO card does not seal both test splits")
    _verify_card_file(card, "train.jsonl", paths["train"])
    _verify_card_file(card, "dev.jsonl", paths["dev"])

    train_samples = load_grpo_samples(paths["train"], expected_split="train")
    dev_samples = load_grpo_samples(paths["dev"], expected_split="dev")
    if len(train_samples) != card["splits"]["train"]["sample_count"]:
        raise ValueError("GRPO train sample count differs from card")
    if len(dev_samples) != card["splits"]["dev"]["sample_count"]:
        raise ValueError("GRPO dev sample count differs from card")
    overlap = {item.content_hash for item in train_samples} & {
        item.content_hash for item in dev_samples
    }
    if overlap or not card.get("leakage_audit", {}).get("passed"):
        raise ValueError("GRPO train/dev content leakage detected")

    # Recompute the deterministic Dev audit before consulting the external
    # SFT checkpoint.  A missing parent adapter must not hide stale reward or
    # dataset evidence in local preflight.
    dev_trajectories = load_trajectories_jsonl(paths["dev_trajectory"])
    stored_audit = RewardAuditReport.parse_raw(
        paths["reward_audit"].read_text(encoding="utf-8")
    )
    fresh_audit = run_reward_audit(dev_samples, dev_trajectories)
    if not stored_audit.passed or json.loads(stored_audit.json()) != json.loads(fresh_audit.json()):
        raise ValueError("reward audit is stale, failed, or mismatched")

    sft_config = load_local_adapter_config(paths["sft_policy"])
    if sft_config.policy_kind != "sft":
        raise ValueError("GRPO parent policy must be an SFT adapter")
    if sft_config.model_id != config.model_id:
        raise ValueError("GRPO base model differs from SFT parent")
    sft_evidence = inspect_adapter_checkpoint(sft_config, repo_root=root)
    if not sft_evidence.ready:
        raise ValueError(
            "SFT parent checkpoint failed audit: %s"
            % ", ".join(sft_evidence.errors)
        )

    return {
        "valid": True,
        "run_name": config.run_name,
        "model_id": config.model_id,
        "dataset_id": GRPO_DATASET_ID,
        "prompt_version": PLANNER_PROMPT_VERSION,
        "reward_profile": config.reward_profile,
        "train_sample_count": len(train_samples),
        "dev_sample_count": len(dev_samples),
        "train_dev_content_hash_overlap": len(overlap),
        "effective_batch_size": config.micro_batch_size * config.gradient_accumulation_steps,
        "num_generations": config.num_generations,
        "reward_audit_id": fresh_audit.audit_id,
        "reward_audit_passed": fresh_audit.passed,
        "sft_adapter_content_hash": sft_evidence.adapter_content_hash,
        "sft_run_manifest_sha256": sft_evidence.run_manifest_sha256,
        "train_file_sha256": _sha256(paths["train"]),
        "dev_file_sha256": _sha256(paths["dev"]),
        "dataset_card_sha256": _sha256(paths["card"]),
        "reward_audit_sha256": _sha256(paths["reward_audit"]),
        "sealed_splits_not_loaded": sorted(sealed),
    }


def inspect_grpo_training_run(
    config: GRPOTrainingConfig,
    *,
    repo_root=None,
) -> Dict[str, Any]:
    """Return an honest non-throwing preflight for local/server handoff."""

    try:
        validation = validate_grpo_training_run(config, repo_root=repo_root)
    except Exception as exc:
        return {
            "schema_version": "novelsim_grpo_preflight.v1",
            "ready": False,
            "executes_training": False,
            "run_name": config.run_name,
            "model_id": config.model_id,
            "reward_profile": config.reward_profile,
            "error": "%s: %s" % (type(exc).__name__, str(exc)),
        }
    return {
        "schema_version": "novelsim_grpo_preflight.v1",
        "ready": True,
        "executes_training": False,
        "validation": validation,
    }


def run_grpo_training(
    config: GRPOTrainingConfig,
    *,
    repo_root=None,
    config_path=None,
) -> Dict[str, Any]:
    validation = validate_grpo_training_run(config, repo_root=repo_root)
    if sys.version_info < (3, 10):
        raise RuntimeError("actual GRPO training requires Python 3.10+")
    try:
        import torch
        from datasets import Dataset
        from peft import PeftModel, prepare_model_for_kbit_training
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, set_seed
        from trl import GRPOConfig, GRPOTrainer
    except ImportError as exc:
        raise RuntimeError(
            "missing GRPO dependencies; install training/requirements-sft.txt"
        ) from exc

    _validate_cuda_environment(config, torch)
    root = Path(repo_root) if repo_root is not None else _repo_root()
    train_samples = load_grpo_samples(_resolve(root, config.train_file), expected_split="train")
    dev_samples = load_grpo_samples(_resolve(root, config.dev_file), expected_split="dev")
    train_dataset = Dataset.from_list([_trainer_row(item) for item in train_samples])
    dev_dataset = Dataset.from_list([_trainer_row(item) for item in dev_samples])
    output_dir = _resolve(root, config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    set_seed(config.seed)

    sft_config = load_local_adapter_config(_resolve(root, config.sft_policy_config))
    sft_adapter = _resolve(root, sft_config.adapter_path)
    tokenizer = AutoTokenizer.from_pretrained(
        str(sft_adapter),
        trust_remote_code=False,
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"
    token_audit = _audit_token_lengths(
        tokenizer,
        train_samples + dev_samples,
        max_prompt_length=config.max_prompt_length,
    )
    if token_audit["over_limit_count"]:
        raise RuntimeError(
            "%s GRPO prompts exceed max_prompt_length=%s"
            % (token_audit["over_limit_count"], config.max_prompt_length)
        )

    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    local_rank = int(os.environ.get("LOCAL_RANK", "0"))
    model = AutoModelForCausalLM.from_pretrained(
        config.model_id,
        revision=config.model_revision,
        trust_remote_code=False,
        quantization_config=quantization,
        device_map={"": local_rank},
        dtype=torch.bfloat16,
        attn_implementation=config.attention_implementation,
    )
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(
        model,
        use_gradient_checkpointing=config.gradient_checkpointing,
    )
    model = PeftModel.from_pretrained(model, str(sft_adapter), is_trainable=True)
    reward_func = make_trl_reward_function(config.reward_profile)
    training_args = GRPOConfig(
        output_dir=str(output_dir),
        run_name=config.run_name,
        max_steps=config.max_steps,
        num_train_epochs=config.num_train_epochs,
        per_device_train_batch_size=config.micro_batch_size,
        per_device_eval_batch_size=config.eval_batch_size,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        num_generations=config.num_generations,
        num_generations_eval=config.num_generations_eval,
        max_completion_length=config.max_completion_length,
        learning_rate=config.learning_rate,
        weight_decay=config.weight_decay,
        warmup_ratio=config.warmup_ratio,
        max_grad_norm=config.max_grad_norm,
        beta=config.beta,
        epsilon=config.epsilon,
        loss_type=config.loss_type,
        scale_rewards=config.scale_rewards,
        temperature=config.temperature,
        top_p=config.top_p,
        optim=config.optimizer,
        lr_scheduler_type="cosine",
        logging_strategy="steps",
        logging_steps=config.logging_steps,
        eval_strategy="steps",
        eval_steps=config.eval_steps,
        save_strategy="steps",
        save_steps=config.save_steps,
        save_total_limit=config.save_total_limit,
        load_best_model_at_end=True,
        metric_for_best_model="reward",
        greater_is_better=True,
        gradient_checkpointing=config.gradient_checkpointing,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        bf16=config.bf16,
        tf32=config.tf32,
        report_to=config.report_to,
        seed=config.seed,
        data_seed=config.seed,
        remove_unused_columns=False,
        mask_truncated_completions=True,
        chat_template_kwargs={"enable_thinking": False},
        use_vllm=config.use_vllm,
        vllm_mode=config.vllm_mode,
        vllm_gpu_memory_utilization=config.vllm_gpu_memory_utilization,
        vllm_enable_sleep_mode=config.vllm_enable_sleep_mode,
        vllm_max_model_length=config.vllm_max_model_length,
    )
    trainer = GRPOTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=dev_dataset,
        processing_class=tokenizer,
        reward_funcs=reward_func,
    )
    train_result = trainer.train(
        resume_from_checkpoint=(
            str(_resolve(root, config.resume_from_checkpoint))
            if config.resume_from_checkpoint else None
        )
    )
    final_dir = output_dir / "final_adapter"
    trainer.save_model(str(final_dir))
    tokenizer.save_pretrained(str(final_dir))
    metrics = dict(train_result.metrics)
    trainer.log_metrics("train", metrics)
    trainer.save_metrics("train", metrics)
    trainer.save_state()
    adapter_files = _directory_file_records(final_dir)
    adapter_content_hash = _canonical_hash(adapter_files)
    manifest = {
        "schema_version": "novelsim_grpo_run_manifest.v1",
        "status": "completed",
        "config": json.loads(config.json()),
        "config_path": str(config_path) if config_path is not None else None,
        "config_sha256": _sha256(Path(config_path)) if config_path is not None else None,
        "validation": validation,
        "token_length_audit": token_audit,
        "environment": _environment_record(torch),
        "code_commit": _git_commit(root),
        "metrics": metrics,
        "parent_sft_checkpoint": {
            "adapter_content_hash": validation["sft_adapter_content_hash"],
            "run_manifest_sha256": validation["sft_run_manifest_sha256"],
        },
        "adapter_path": str(final_dir),
        "adapter_files": adapter_files,
        "adapter_content_hash": adapter_content_hash,
    }
    manifest_path = output_dir / "run-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _trainer_row(sample: GRPOPromptSample) -> Dict[str, Any]:
    return {
        "prompt": list(sample.prompt),
        "environment_spec": sample.environment_spec,
        "sample_id": sample.sample_id,
        "scenario_family": sample.scenario_family,
        "starting_state_hash": sample.starting_state_hash,
    }


def _audit_token_lengths(tokenizer, samples, *, max_prompt_length: int) -> Dict[str, Any]:
    lengths: List[int] = []
    for sample in samples:
        token_ids = tokenizer.apply_chat_template(
            sample.prompt,
            tokenize=True,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        lengths.append(len(token_ids))
    ordered = sorted(lengths)
    return {
        "sample_count": len(ordered),
        "min": ordered[0] if ordered else 0,
        "p50": _nearest_rank(ordered, 0.50),
        "p95": _nearest_rank(ordered, 0.95),
        "max": ordered[-1] if ordered else 0,
        "max_prompt_length": max_prompt_length,
        "over_limit_count": sum(length_ > max_prompt_length for length_ in ordered),
    }


def _validate_cuda_environment(config: GRPOTrainingConfig, torch) -> None:
    if config.require_single_gpu and int(os.environ.get("WORLD_SIZE", "1")) != 1:
        raise RuntimeError("this GRPO configuration is intentionally single-GPU")
    if config.require_cuda and not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU is required")
    if not torch.cuda.is_available():
        return
    if config.bf16 and not torch.cuda.is_bf16_supported():
        raise RuntimeError("configured GPU does not support bf16")
    memory_gib = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
    if memory_gib < config.minimum_gpu_memory_gib:
        raise RuntimeError("GPU memory is below the configured minimum")


def _environment_record(torch) -> Dict[str, Any]:
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "torch": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
    }
    if torch.cuda.is_available():
        properties = torch.cuda.get_device_properties(0)
        result.update({
            "gpu_name": properties.name,
            "gpu_memory_gib": round(properties.total_memory / (1024 ** 3), 3),
            "peak_memory_allocated_gib": round(torch.cuda.max_memory_allocated() / (1024 ** 3), 3),
            "peak_memory_reserved_gib": round(torch.cuda.max_memory_reserved() / (1024 ** 3), 3),
            "bf16_supported": torch.cuda.is_bf16_supported(),
        })
    try:
        from importlib.metadata import version
    except ImportError:
        from importlib_metadata import version
    result["packages"] = {
        name: version(name)
        for name in ("transformers", "trl", "peft", "datasets", "accelerate", "bitsandbytes")
    }
    vllm_version = _installed_version("vllm")
    if vllm_version:
        result["packages"]["vllm"] = vllm_version
    return result


def _installed_version(name: str) -> str:
    try:
        from importlib.metadata import version
    except ImportError:
        from importlib_metadata import version
    try:
        return version(name)
    except Exception:
        return ""


def _verify_card_file(card: Dict[str, Any], name: str, path: Path) -> None:
    expected = card.get("files", {}).get(name, {}).get("sha256")
    if not expected or expected != _sha256(path):
        raise ValueError("GRPO file hash mismatch: %s" % name)


def _nearest_rank(ordered: List[int], fraction: float) -> int:
    if not ordered:
        return 0
    index = max(0, int(len(ordered) * fraction + 0.999999) - 1)
    return ordered[min(index, len(ordered) - 1)]


def _directory_file_records(path: Path) -> Dict[str, Dict[str, Any]]:
    return {
        item.relative_to(path).as_posix(): {
            "bytes": item.stat().st_size,
            "sha256": _sha256(item),
        }
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(root), text=True
        ).strip()
    except Exception:
        return ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate or run NovelSim GRPO")
    parser.add_argument("--config", required=True)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args(argv)
    config = load_grpo_training_config(args.config)
    if args.inspect:
        report = inspect_grpo_training_run(config)
    elif args.validate_only:
        report = validate_grpo_training_run(config)
    else:
        report = run_grpo_training(config, config_path=args.config)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
