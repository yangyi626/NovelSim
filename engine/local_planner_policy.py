"""Local Transformers + PEFT adapter policies for trained planners."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence

from pydantic import BaseModel, Field, root_validator, validator

from .agent_tools import ToolDefinition
from .game_observation import GameObservation
from .game_trajectory import PlannerUsage
from .planner_decision import PlannerDecision
from .planner_policy import PlannerPolicyError, coerce_planner_decision
from .planner_prompt import (
    PLANNER_PROMPT_VERSION,
    extract_json_object,
    planner_prompt_messages,
)


class LocalAdapterConfig(BaseModel):
    schema_version: str = "local_adapter_planner_config.v1"
    policy_kind: str = "sft"
    model_id: str
    adapter_path: str
    run_manifest_path: str
    prompt_version: str = PLANNER_PROMPT_VERSION
    load_in_4bit: bool = True
    quant_type: str = "nf4"
    use_double_quant: bool = True
    compute_dtype: str = "bfloat16"
    device_index: int = Field(0, ge=0)
    max_new_tokens: int = Field(512, ge=32, le=2048)
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    trust_remote_code: bool = False
    require_cuda: bool = True

    class Config:
        extra = "forbid"
        allow_mutation = False

    @validator("schema_version")
    def _known_schema(cls, value):
        if value != "local_adapter_planner_config.v1":
            raise ValueError("unsupported local adapter config schema")
        return value

    @validator("policy_kind")
    def _known_policy(cls, value):
        if value not in {"sft", "grpo"}:
            raise ValueError("policy_kind must be sft or grpo")
        return value

    @validator("model_id", "adapter_path", "run_manifest_path")
    def _non_empty_identity_or_path(cls, value):
        if not value.strip():
            raise ValueError("model and checkpoint paths cannot be empty")
        return value

    @root_validator(skip_on_failure=True)
    def _fixed_inference_contract(cls, values):
        if values.get("prompt_version") != PLANNER_PROMPT_VERSION:
            raise ValueError("local policy prompt version mismatch")
        if not (
            values.get("load_in_4bit")
            and values.get("quant_type") == "nf4"
            and values.get("use_double_quant")
            and values.get("compute_dtype") == "bfloat16"
        ):
            raise ValueError("local adapter requires 4-bit NF4 double-quant bf16")
        if values.get("temperature") != 0.0:
            raise ValueError("checkpoint evaluation requires deterministic temperature=0")
        if values.get("trust_remote_code"):
            raise ValueError("checkpoint evaluation forbids trust_remote_code")
        if not values.get("require_cuda"):
            raise ValueError("checkpoint evaluation requires the audited CUDA path")
        return values


class AdapterCheckpointEvidence(BaseModel):
    ready: bool
    model_id: str
    policy_kind: str
    prompt_version: str
    adapter_path: str
    run_manifest_path: str
    run_manifest_sha256: str = ""
    adapter_content_hash: str = ""
    adapter_files: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    training_code_commit: str = ""
    dataset_id: str = ""
    errors: List[str] = Field(default_factory=list)

    class Config:
        extra = "forbid"
        allow_mutation = False


class LocalGeneration(BaseModel):
    text: str
    usage: PlannerUsage

    class Config:
        extra = "forbid"
        allow_mutation = False


class PlannerDecisionWithUsage(BaseModel):
    decision: PlannerDecision
    usage: PlannerUsage

    class Config:
        extra = "forbid"
        allow_mutation = False


class LocalPlannerBackend(Protocol):
    def generate(
        self,
        messages: Sequence[Mapping[str, str]],
    ) -> LocalGeneration:
        ...


def load_local_adapter_config(config_path) -> LocalAdapterConfig:
    return LocalAdapterConfig.parse_raw(
        Path(config_path).read_text(encoding="utf-8")
    )


def inspect_adapter_checkpoint(
    config: LocalAdapterConfig,
    *,
    repo_root=None,
) -> AdapterCheckpointEvidence:
    root = Path(repo_root) if repo_root is not None else Path.cwd()
    adapter_path = _resolve(root, config.adapter_path)
    manifest_path = _resolve(root, config.run_manifest_path)
    errors: List[str] = []
    manifest: Dict[str, Any] = {}
    adapter_files: Dict[str, Dict[str, Any]] = {}
    manifest_sha = ""
    content_hash = ""

    if not manifest_path.is_file():
        errors.append("run_manifest_missing")
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_sha = _sha256(manifest_path)
        except Exception:
            errors.append("run_manifest_invalid_json")
    if adapter_path.resolve() == root.resolve():
        errors.append("adapter_directory_cannot_be_repo_root")
    elif not adapter_path.is_dir():
        errors.append("adapter_directory_missing")
    else:
        for required in ("adapter_config.json", "tokenizer_config.json"):
            if not (adapter_path / required).is_file():
                errors.append("adapter_file_missing:%s" % required)
        weights = [
            path
            for name in ("adapter_model.safetensors", "adapter_model.bin")
            if (path := adapter_path / name).is_file()
        ]
        if not weights:
            errors.append("adapter_weights_missing")
        adapter_files = _directory_file_records(adapter_path)
        content_hash = _adapter_content_hash(adapter_files)

    if manifest:
        expected_manifest_schema = (
            "novelsim_sft_run_manifest.v1"
            if config.policy_kind == "sft"
            else "novelsim_grpo_run_manifest.v1"
        )
        expected_dataset_id = (
            "novelsim_planner_sft_v2"
            if config.policy_kind == "sft"
            else "novelsim_planner_grpo_v2"
        )
        if manifest.get("schema_version") != expected_manifest_schema:
            errors.append("run_manifest_schema_mismatch")
        if manifest.get("status") != "completed":
            errors.append("run_manifest_not_completed")
        training_config = manifest.get("config", {})
        if training_config.get("model_id") != config.model_id:
            errors.append("run_manifest_model_mismatch")
        validation = manifest.get("validation", {})
        if validation.get("prompt_version") != config.prompt_version:
            errors.append("run_manifest_prompt_mismatch")
        if validation.get("dataset_id") != expected_dataset_id:
            errors.append("run_manifest_dataset_mismatch")
        if config.policy_kind == "grpo":
            parent = manifest.get("parent_sft_checkpoint", {})
            if not parent.get("adapter_content_hash"):
                errors.append("grpo_parent_adapter_hash_missing")
            if not parent.get("run_manifest_sha256"):
                errors.append("grpo_parent_manifest_hash_missing")
        recorded_files = manifest.get("adapter_files", {})
        if not recorded_files:
            errors.append("run_manifest_adapter_hashes_missing")
        elif recorded_files != adapter_files:
            errors.append("adapter_file_hash_mismatch")
        if manifest.get("adapter_content_hash") != content_hash:
            errors.append("adapter_content_hash_mismatch")
        if not manifest.get("code_commit"):
            errors.append("run_manifest_code_commit_missing")
    if adapter_path.is_dir() and (adapter_path / "adapter_config.json").is_file():
        try:
            adapter_config = json.loads(
                (adapter_path / "adapter_config.json").read_text(encoding="utf-8")
            )
            if adapter_config.get("base_model_name_or_path") != config.model_id:
                errors.append("adapter_base_model_mismatch")
            if str(adapter_config.get("peft_type", "")).upper() != "LORA":
                errors.append("adapter_peft_type_mismatch")
            if str(adapter_config.get("task_type", "")).upper() != "CAUSAL_LM":
                errors.append("adapter_task_type_mismatch")
        except Exception:
            errors.append("adapter_config_invalid_json")

    return AdapterCheckpointEvidence(
        ready=not errors,
        model_id=config.model_id,
        policy_kind=config.policy_kind,
        prompt_version=config.prompt_version,
        adapter_path=str(adapter_path),
        run_manifest_path=str(manifest_path),
        run_manifest_sha256=manifest_sha,
        adapter_content_hash=content_hash,
        adapter_files=adapter_files,
        training_code_commit=str(manifest.get("code_commit", "")),
        dataset_id=str(manifest.get("validation", {}).get("dataset_id", "")),
        errors=errors,
    )


class TransformersPeftBackend:
    """Lazy single-GPU backend; importing the game engine stays lightweight."""

    def __init__(
        self,
        config: LocalAdapterConfig,
        *,
        repo_root=None,
    ) -> None:
        self.config = config
        self.repo_root = Path(repo_root) if repo_root is not None else Path.cwd()
        self._model = None
        self._tokenizer = None
        self._torch = None
        self._lock = threading.Lock()

    def generate(
        self,
        messages: Sequence[Mapping[str, str]],
    ) -> LocalGeneration:
        with self._lock:
            self._ensure_loaded()
            started = perf_counter()
            inputs = self._tokenizer.apply_chat_template(
                list(messages),
                tokenize=True,
                add_generation_prompt=True,
                return_tensors="pt",
                return_dict=True,
                enable_thinking=False,
            )
            inputs = {
                key: value.to(self._model.device)
                for key, value in inputs.items()
            }
            prompt_tokens = int(inputs["input_ids"].shape[-1])
            generation_kwargs = {
                "max_new_tokens": self.config.max_new_tokens,
                "do_sample": self.config.temperature > 0.0,
                "pad_token_id": self._tokenizer.pad_token_id,
                "eos_token_id": self._tokenizer.eos_token_id,
            }
            if self.config.temperature > 0.0:
                generation_kwargs["temperature"] = self.config.temperature
            with self._torch.inference_mode():
                output = self._model.generate(**inputs, **generation_kwargs)
            completion_ids = output[0, prompt_tokens:]
            completion_tokens = int(completion_ids.shape[-1])
            text = self._tokenizer.decode(
                completion_ids,
                skip_special_tokens=True,
            ).strip()
            return LocalGeneration(
                text=text,
                usage=PlannerUsage(
                    model_id=self.config.model_id,
                    prompt_version=self.config.prompt_version,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                    latency_ms=round((perf_counter() - started) * 1000.0, 3),
                ),
            )

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        evidence = inspect_adapter_checkpoint(
            self.config,
            repo_root=self.repo_root,
        )
        if not evidence.ready:
            raise PlannerPolicyError(
                "adapter checkpoint failed validation: %s"
                % ", ".join(evidence.errors)
            )
        try:
            import torch
            from peft import PeftModel
            from transformers import (
                AutoModelForCausalLM,
                AutoTokenizer,
                BitsAndBytesConfig,
            )
        except ImportError as exc:
            raise PlannerPolicyError(
                "local adapter inference requires the SFT environment"
            ) from exc
        if self.config.require_cuda and not torch.cuda.is_available():
            raise PlannerPolicyError("local adapter inference requires CUDA")
        adapter_path = _resolve(self.repo_root, self.config.adapter_path)
        quantization = BitsAndBytesConfig(
            load_in_4bit=self.config.load_in_4bit,
            bnb_4bit_quant_type=self.config.quant_type,
            bnb_4bit_use_double_quant=self.config.use_double_quant,
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
        tokenizer = AutoTokenizer.from_pretrained(
            str(adapter_path),
            trust_remote_code=self.config.trust_remote_code,
        )
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        model = AutoModelForCausalLM.from_pretrained(
            self.config.model_id,
            quantization_config=quantization,
            device_map={"": self.config.device_index},
            dtype=torch.bfloat16,
            attn_implementation="sdpa",
            trust_remote_code=self.config.trust_remote_code,
        )
        model = PeftModel.from_pretrained(model, str(adapter_path))
        model.eval()
        self._torch = torch
        self._tokenizer = tokenizer
        self._model = model


class AdapterPlannerPolicy:
    def __init__(
        self,
        config: LocalAdapterConfig,
        *,
        policy_id: Optional[str] = None,
        backend: Optional[LocalPlannerBackend] = None,
        repo_root=None,
    ) -> None:
        self.config = config
        self.policy_id = policy_id or config.policy_kind
        self.prompt_version = config.prompt_version
        self._backend = backend or TransformersPeftBackend(
            config,
            repo_root=repo_root,
        )

    def decide(
        self,
        observation: GameObservation,
        available_tools: Sequence[ToolDefinition],
    ) -> PlannerDecision:
        return self.decide_with_usage(observation, available_tools).decision

    def decide_with_usage(
        self,
        observation: GameObservation,
        available_tools: Sequence[ToolDefinition],
    ) -> PlannerDecisionWithUsage:
        generation = self._backend.generate(planner_prompt_messages(observation))
        payload = extract_json_object(generation.text)
        if payload is None:
            raise PlannerPolicyError("local planner response is not a JSON object")
        decision = coerce_planner_decision(
            payload,
            observation=observation,
            policy_id=self.policy_id,
        )
        if generation.usage.prompt_version != self.prompt_version:
            raise PlannerPolicyError("local generation prompt version mismatch")
        if generation.usage.model_id != self.config.model_id:
            raise PlannerPolicyError("local generation model identity mismatch")
        return PlannerDecisionWithUsage(
            decision=decision,
            usage=generation.usage,
        )


class SFTPolicy(AdapterPlannerPolicy):
    def __init__(self, config: LocalAdapterConfig, **kwargs) -> None:
        if config.policy_kind != "sft":
            raise ValueError("SFTPolicy requires policy_kind=sft")
        super().__init__(config, policy_id="sft", **kwargs)


class GRPOPolicy(AdapterPlannerPolicy):
    def __init__(self, config: LocalAdapterConfig, **kwargs) -> None:
        if config.policy_kind != "grpo":
            raise ValueError("GRPOPolicy requires policy_kind=grpo")
        super().__init__(config, policy_id="grpo", **kwargs)


def _directory_file_records(path: Path) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        relative = item.relative_to(path).as_posix()
        records[relative] = {
            "bytes": item.stat().st_size,
            "sha256": _sha256(item),
        }
    return records


def _adapter_content_hash(records: Dict[str, Dict[str, Any]]) -> str:
    canonical = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
