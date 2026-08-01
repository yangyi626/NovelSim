"""Single-GPU QLoRA entry point for the NovelSim high-level planner.

The module keeps heavyweight training imports lazy so the Python 3.8 game
runtime can validate data/configuration without installing the CUDA stack.
Actual training is intended for a separate Python 3.10+ environment on one
RTX 4090 (or a compatible CUDA GPU).
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
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, root_validator, validator

from .build_sft_dataset import (
    PROMPT_VERSION,
    SFT_DATASET_ID,
    load_sft_samples,
)


class QuantizationConfig(BaseModel):
    load_in_4bit: bool = True
    quant_type: str = "nf4"
    use_double_quant: bool = True
    compute_dtype: str = "bfloat16"

    class Config:
        extra = "forbid"
        allow_mutation = False


class PlannerLoraConfig(BaseModel):
    rank: int = Field(16, ge=1)
    alpha: int = Field(32, ge=1)
    dropout: float = Field(0.05, ge=0.0, lt=1.0)
    target_modules: Union[str, List[str]] = "all-linear"
    bias: str = "none"

    class Config:
        extra = "forbid"
        allow_mutation = False


class SFTTrainingConfig(BaseModel):
    schema_version: str = "novelsim_sft_training_config.v1"
    run_name: str
    model_id: str
    model_revision: str = "main"
    train_file: str
    dev_file: str
    dataset_card: str
    output_dir: str
    max_length: int = Field(2048, ge=512)
    max_steps: int = Field(-1, ge=-1)
    num_train_epochs: float = Field(3.0, gt=0.0)
    micro_batch_size: int = Field(1, ge=1)
    eval_batch_size: int = Field(1, ge=1)
    gradient_accumulation_steps: int = Field(16, ge=1)
    learning_rate: float = Field(2e-4, gt=0.0)
    weight_decay: float = Field(0.0, ge=0.0)
    warmup_ratio: float = Field(0.03, ge=0.0, lt=1.0)
    max_grad_norm: float = Field(0.3, gt=0.0)
    logging_steps: int = Field(5, ge=1)
    eval_steps: int = Field(25, ge=1)
    save_steps: int = Field(25, ge=1)
    save_total_limit: int = Field(2, ge=1)
    dataset_num_proc: int = Field(4, ge=1)
    seed: int = 20260801
    completion_only_loss: bool = True
    packing: bool = False
    gradient_checkpointing: bool = True
    bf16: bool = True
    tf32: bool = True
    attention_implementation: str = "sdpa"
    optimizer: str = "paged_adamw_8bit"
    report_to: List[str] = Field(default_factory=lambda: ["tensorboard"])
    require_cuda: bool = True
    require_single_gpu: bool = True
    minimum_gpu_memory_gib: float = Field(20.0, ge=0.0)
    trust_remote_code: bool = False
    resume_from_checkpoint: Optional[str] = None
    quantization: QuantizationConfig = Field(default_factory=QuantizationConfig)
    lora: PlannerLoraConfig = Field(default_factory=PlannerLoraConfig)

    class Config:
        extra = "forbid"
        allow_mutation = False

    @validator("run_name", "model_id", "train_file", "dev_file", "dataset_card", "output_dir")
    def _non_empty(cls, value):
        if not value.strip():
            raise ValueError("value cannot be empty")
        return value

    @validator("schema_version")
    def _known_schema_version(cls, value):
        if value != "novelsim_sft_training_config.v1":
            raise ValueError("unsupported SFT training config schema")
        return value

    @root_validator(skip_on_failure=True)
    def _enforce_novelsim_qlora_contract(cls, values):
        if values.get("max_steps") == 0:
            raise ValueError("max_steps must be -1 or a positive integer")
        if not values.get("completion_only_loss"):
            raise ValueError("NovelSim SFT requires completion_only_loss=true")
        if values.get("packing"):
            raise ValueError("packing remains disabled until mask equivalence is audited")
        quant = values.get("quantization")
        if quant is not None and not (
            quant.load_in_4bit
            and quant.quant_type == "nf4"
            and quant.use_double_quant
            and quant.compute_dtype == "bfloat16"
        ):
            raise ValueError("NovelSim QLoRA requires 4-bit NF4 double-quant bf16")
        lora = values.get("lora")
        if lora is not None and lora.target_modules != "all-linear":
            raise ValueError("QLoRA must target all linear layers")
        if values.get("save_steps", 1) % values.get("eval_steps", 1) != 0:
            raise ValueError(
                "save_steps must be a multiple of eval_steps when selecting best checkpoint"
            )
        return values


def load_training_config(config_path) -> SFTTrainingConfig:
    path = Path(config_path)
    return SFTTrainingConfig.parse_raw(path.read_text(encoding="utf-8"))


def validate_training_run(
    config: SFTTrainingConfig,
    *,
    repo_root=None,
) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    train_path = _resolve(root, config.train_file)
    dev_path = _resolve(root, config.dev_file)
    card_path = _resolve(root, config.dataset_card)
    for path in (train_path, dev_path, card_path):
        if not path.is_file():
            raise ValueError("required training artifact not found: %s" % path)
    if train_path.resolve() == dev_path.resolve():
        raise ValueError("train and dev files must differ")

    card = json.loads(card_path.read_text(encoding="utf-8"))
    if card.get("dataset_id") != SFT_DATASET_ID:
        raise ValueError("unexpected SFT dataset_id")
    if card.get("prompt_version") != PROMPT_VERSION:
        raise ValueError("unexpected SFT prompt version")
    if card.get("format") != "conversational_prompt_completion":
        raise ValueError("unexpected SFT dataset format")
    if card.get("loss_scope") != "completion_only":
        raise ValueError("dataset card does not require completion-only loss")
    boundary = card.get("training_boundary", {})
    if boundary.get("optimization_split") != "train":
        raise ValueError("dataset card train boundary is invalid")
    if boundary.get("checkpoint_selection_split") != "dev":
        raise ValueError("dataset card dev boundary is invalid")
    sealed = set(boundary.get("sealed_splits_not_loaded", []))
    if not {"test_id", "test_ood"}.issubset(sealed):
        raise ValueError("dataset card does not seal both test splits")

    _verify_card_file(card, "train.jsonl", train_path)
    _verify_card_file(card, "dev.jsonl", dev_path)
    train_samples = load_sft_samples(train_path, expected_split="train")
    dev_samples = load_sft_samples(dev_path, expected_split="dev")
    if len(train_samples) != card["splits"]["train"]["sample_count"]:
        raise ValueError("train sample count does not match dataset card")
    if len(dev_samples) != card["splits"]["dev"]["sample_count"]:
        raise ValueError("dev sample count does not match dataset card")
    train_hashes = {item.content_hash for item in train_samples}
    dev_hashes = {item.content_hash for item in dev_samples}
    overlap = train_hashes & dev_hashes
    if overlap or not card.get("leakage_audit", {}).get("passed"):
        raise ValueError("train/dev content hash leakage detected")
    if any(item.prompt_version != PROMPT_VERSION for item in train_samples + dev_samples):
        raise ValueError("mixed prompt versions are not trainable")

    effective_batch_size = (
        config.micro_batch_size * config.gradient_accumulation_steps
    )
    return {
        "valid": True,
        "run_name": config.run_name,
        "model_id": config.model_id,
        "dataset_id": card["dataset_id"],
        "prompt_version": card["prompt_version"],
        "train_sample_count": len(train_samples),
        "dev_sample_count": len(dev_samples),
        "train_dev_content_hash_overlap": len(overlap),
        "effective_batch_size": effective_batch_size,
        "max_length": config.max_length,
        "max_steps": config.max_steps,
        "num_train_epochs": config.num_train_epochs,
        "train_file_sha256": _sha256(train_path),
        "dev_file_sha256": _sha256(dev_path),
        "dataset_card_sha256": _sha256(card_path),
        "sealed_splits_not_loaded": sorted(sealed),
    }


def run_training(
    config: SFTTrainingConfig,
    *,
    repo_root=None,
    config_path=None,
) -> Dict[str, Any]:
    validation = validate_training_run(config, repo_root=repo_root)
    if sys.version_info < (3, 10):
        raise RuntimeError("actual SFT training requires Python 3.10+")

    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig, prepare_model_for_kbit_training
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
            set_seed,
        )
        from trl import SFTConfig, SFTTrainer
    except ImportError as exc:
        raise RuntimeError(
            "missing SFT dependencies; install training/requirements-sft.txt "
            "inside a Python 3.10+ CUDA environment"
        ) from exc

    _validate_cuda_environment(config, torch)
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats(0)
    root = Path(repo_root) if repo_root is not None else _repo_root()
    train_samples = load_sft_samples(
        _resolve(root, config.train_file),
        expected_split="train",
    )
    dev_samples = load_sft_samples(
        _resolve(root, config.dev_file),
        expected_split="dev",
    )
    train_dataset = Dataset.from_list([_trainer_row(item) for item in train_samples])
    dev_dataset = Dataset.from_list([_trainer_row(item) for item in dev_samples])
    output_dir = _resolve(root, config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    set_seed(config.seed)

    tokenizer = AutoTokenizer.from_pretrained(
        config.model_id,
        revision=config.model_revision,
        trust_remote_code=config.trust_remote_code,
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"
    token_audit = _audit_token_lengths(
        tokenizer,
        train_samples + dev_samples,
        max_length=config.max_length,
    )
    if token_audit["over_limit_count"]:
        raise RuntimeError(
            "%s samples exceed max_length=%s; increase max_length before training"
            % (token_audit["over_limit_count"], config.max_length)
        )

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=config.quantization.load_in_4bit,
        bnb_4bit_quant_type=config.quantization.quant_type,
        bnb_4bit_use_double_quant=config.quantization.use_double_quant,
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    local_rank = int(os.environ.get("LOCAL_RANK", "0"))
    model = AutoModelForCausalLM.from_pretrained(
        config.model_id,
        revision=config.model_revision,
        trust_remote_code=config.trust_remote_code,
        quantization_config=bnb_config,
        device_map={"": local_rank},
        dtype=torch.bfloat16,
        attn_implementation=config.attention_implementation,
    )
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(
        model,
        use_gradient_checkpointing=config.gradient_checkpointing,
    )
    peft_config = LoraConfig(
        r=config.lora.rank,
        lora_alpha=config.lora.alpha,
        lora_dropout=config.lora.dropout,
        target_modules=config.lora.target_modules,
        bias=config.lora.bias,
        task_type="CAUSAL_LM",
    )
    training_args = SFTConfig(
        output_dir=str(output_dir),
        run_name=config.run_name,
        max_length=config.max_length,
        max_steps=config.max_steps,
        num_train_epochs=config.num_train_epochs,
        per_device_train_batch_size=config.micro_batch_size,
        per_device_eval_batch_size=config.eval_batch_size,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        learning_rate=config.learning_rate,
        weight_decay=config.weight_decay,
        warmup_ratio=config.warmup_ratio,
        max_grad_norm=config.max_grad_norm,
        lr_scheduler_type="cosine",
        optim=config.optimizer,
        logging_strategy="steps",
        logging_steps=config.logging_steps,
        eval_strategy="steps",
        eval_steps=config.eval_steps,
        save_strategy="steps",
        save_steps=config.save_steps,
        save_total_limit=config.save_total_limit,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        completion_only_loss=config.completion_only_loss,
        assistant_only_loss=False,
        packing=config.packing,
        eval_packing=False,
        dataset_num_proc=config.dataset_num_proc,
        gradient_checkpointing=config.gradient_checkpointing,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        bf16=config.bf16,
        tf32=config.tf32,
        report_to=config.report_to,
        seed=config.seed,
        data_seed=config.seed,
        remove_unused_columns=True,
    )
    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=dev_dataset,
        processing_class=tokenizer,
        peft_config=peft_config,
    )
    train_result = trainer.train(
        resume_from_checkpoint=(
            _resolve(root, config.resume_from_checkpoint)
            if config.resume_from_checkpoint
            else None
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
        "schema_version": "novelsim_sft_run_manifest.v1",
        "status": "completed",
        "config": json.loads(config.json()),
        "config_path": str(config_path) if config_path is not None else None,
        "config_sha256": (
            _sha256(Path(config_path)) if config_path is not None else None
        ),
        "effective_config_sha256": _canonical_hash(json.loads(config.json())),
        "validation": validation,
        "token_length_audit": token_audit,
        "environment": _environment_record(torch),
        "resource_usage": _resource_usage_record(torch),
        "code_commit": _git_commit(root),
        "metrics": metrics,
        "final_adapter": str(final_dir),
        "adapter_files": adapter_files,
        "adapter_content_hash": adapter_content_hash,
    }
    (output_dir / "run-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _trainer_row(sample) -> Dict[str, Any]:
    return {
        "prompt": [message.dict() for message in sample.prompt],
        "completion": [message.dict() for message in sample.completion],
    }


def _audit_token_lengths(tokenizer, samples, *, max_length: int) -> Dict[str, Any]:
    lengths: List[int] = []
    for sample in samples:
        messages = [
            message.dict() for message in sample.prompt + sample.completion
        ]
        token_ids = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=False,
        )
        lengths.append(len(token_ids))
    ordered = sorted(lengths)
    return {
        "sample_count": len(ordered),
        "min": ordered[0] if ordered else 0,
        "p50": _nearest_rank(ordered, 0.50),
        "p95": _nearest_rank(ordered, 0.95),
        "max": ordered[-1] if ordered else 0,
        "max_length": max_length,
        "over_limit_count": sum(length > max_length for length in ordered),
    }


def _validate_cuda_environment(config: SFTTrainingConfig, torch) -> None:
    if config.require_single_gpu and int(os.environ.get("WORLD_SIZE", "1")) != 1:
        raise RuntimeError("this configuration is intentionally single-GPU")
    if config.require_cuda and not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU is required")
    if not torch.cuda.is_available():
        return
    if config.bf16 and not torch.cuda.is_bf16_supported():
        raise RuntimeError("configured GPU does not support bf16")
    properties = torch.cuda.get_device_properties(0)
    memory_gib = properties.total_memory / (1024 ** 3)
    if memory_gib < config.minimum_gpu_memory_gib:
        raise RuntimeError(
            "GPU memory %.2f GiB is below required %.2f GiB"
            % (memory_gib, config.minimum_gpu_memory_gib)
        )


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
    return result


def _resource_usage_record(torch) -> Dict[str, Any]:
    result = {"cuda_available": torch.cuda.is_available()}
    if not torch.cuda.is_available():
        return result
    divisor = float(1024 ** 3)
    result.update({
        "peak_memory_allocated_gib": round(
            torch.cuda.max_memory_allocated(0) / divisor,
            3,
        ),
        "peak_memory_reserved_gib": round(
            torch.cuda.max_memory_reserved(0) / divisor,
            3,
        ),
    })
    return result


def _verify_card_file(card: Dict[str, Any], name: str, path: Path) -> None:
    expected = card.get("files", {}).get(name, {}).get("sha256")
    if not expected or expected != _sha256(path):
        raise ValueError("SFT file hash mismatch: %s" % name)


def _nearest_rank(ordered: List[int], fraction: float) -> int:
    if not ordered:
        return 0
    index = max(0, int(len(ordered) * fraction + 0.999999) - 1)
    return ordered[min(index, len(ordered) - 1)]


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


def _directory_file_records(path: Path) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        records[item.relative_to(path).as_posix()] = {
            "bytes": item.stat().st_size,
            "sha256": _sha256(item),
        }
    return records


def _canonical_hash(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _git_commit(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            text=True,
        ).strip()
    except Exception:
        return ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate or run NovelSim Planner QLoRA SFT",
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    config_path = Path(args.config)
    config = load_training_config(config_path)
    if args.validate_only:
        report = validate_training_run(config)
    else:
        report = run_training(config, config_path=config_path)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
