import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from training.train_sft import (
    SFTTrainingConfig,
    load_training_config,
    validate_training_run,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_checked_in_qwen3_configs_encode_single_gpu_qlora_contract():
    smoke = load_training_config(
        REPO_ROOT / "training/configs/sft_qwen3_0.6b_smoke.json"
    )
    debug = load_training_config(
        REPO_ROOT / "training/configs/sft_qwen3_1.7b_debug.json"
    )
    main = load_training_config(
        REPO_ROOT / "training/configs/sft_qwen3_4b_qlora.json"
    )

    assert smoke.model_id == "Qwen/Qwen3-0.6B"
    assert smoke.max_steps == 100
    assert debug.model_id == "Qwen/Qwen3-1.7B"
    assert debug.max_steps == 200
    assert main.model_id == "Qwen/Qwen3-4B-Instruct-2507"
    assert main.max_steps == -1
    assert main.num_train_epochs == 3.0
    assert main.micro_batch_size == 1
    assert main.gradient_accumulation_steps == 16
    assert main.quantization.dict() == {
        "load_in_4bit": True,
        "quant_type": "nf4",
        "use_double_quant": True,
        "compute_dtype": "bfloat16",
    }
    assert main.lora.target_modules == "all-linear"
    assert main.completion_only_loss is True
    assert main.packing is False
    assert main.require_single_gpu is True


@pytest.mark.parametrize(
    "updates,match",
    [
        ({"completion_only_loss": False}, "completion_only_loss"),
        ({"packing": True}, "packing remains disabled"),
        ({"max_steps": 0}, "max_steps"),
        ({"save_steps": 25, "eval_steps": 10}, "multiple of eval_steps"),
        (
            {
                "quantization": {
                    "load_in_4bit": True,
                    "quant_type": "fp4",
                    "use_double_quant": True,
                    "compute_dtype": "bfloat16",
                }
            },
            "4-bit NF4",
        ),
    ],
)
def test_training_config_rejects_unsafe_or_unaudited_variants(updates, match):
    payload = json.loads(
        (
            REPO_ROOT / "training/configs/sft_qwen3_0.6b_smoke.json"
        ).read_text(encoding="utf-8")
    )
    payload.update(updates)

    with pytest.raises(ValidationError, match=match):
        SFTTrainingConfig.parse_obj(payload)


def test_formal_smoke_config_validates_dataset_hashes_and_boundaries():
    config = load_training_config(
        REPO_ROOT / "training/configs/sft_qwen3_0.6b_smoke.json"
    )

    report = validate_training_run(config, repo_root=REPO_ROOT)

    assert report["valid"] is True
    assert report["train_sample_count"] == 3060
    assert report["dev_sample_count"] == 340
    assert report["train_dev_content_hash_overlap"] == 0
    assert report["effective_batch_size"] == 8
    assert {"test_id", "test_ood"}.issubset(
        report["sealed_splits_not_loaded"]
    )

    debug = validate_training_run(
        load_training_config(
            REPO_ROOT / "training/configs/sft_qwen3_1.7b_debug.json"
        ),
        repo_root=REPO_ROOT,
    )
    assert debug["valid"] is True
    assert debug["model_id"] == "Qwen/Qwen3-1.7B"


def test_training_validation_rejects_file_not_frozen_by_data_card(tmp_path):
    config = load_training_config(
        REPO_ROOT / "training/configs/sft_qwen3_0.6b_smoke.json"
    )
    tampered = tmp_path / "train.jsonl"
    tampered.write_text("{}\n", encoding="utf-8")
    changed = config.copy(
        update={"train_file": str(tampered)},
        deep=True,
    )

    with pytest.raises(ValueError, match="file hash mismatch"):
        validate_training_run(changed, repo_root=REPO_ROOT)
