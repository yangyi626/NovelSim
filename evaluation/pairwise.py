"""BOOKWORLD 风格的盲测 Pairwise A/B 与人工一致性统计。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import openai
from pydantic import BaseModel, Field, root_validator, validator

from engine.config import get_llm_config
from engine.llm_telemetry import (
    LLMCallUsage,
    LLMUsageSummary,
    call_openai_compatible,
    capture_llm_usage,
    chat_generation_options,
)
from engine.scene_controller import SceneMode

from .metrics import estimate_cost, safe_rate
from .models import PricingConfig


PROMPT_PATH = Path(__file__).with_name("pairwise_prompt.md")
COMMON_DIMENSIONS = (
    "anthropomorphism",
    "character_fidelity",
    "immersion_setting",
    "writing_quality",
)


class BlindWinner(str, Enum):
    left = "left"
    right = "right"
    tie = "tie"


class OriginalWinner(str, Enum):
    a = "a"
    b = "b"
    tie = "tie"


class PairwiseStatus(str, Enum):
    judged = "judged"
    parse_failed = "parse_failed"
    call_failed = "call_failed"


class PairwiseCandidate(BaseModel):
    system_id: str
    text: str
    objective_passed: bool = True

    class Config:
        extra = "forbid"

    @validator("system_id", "text")
    def _not_blank(cls, value):
        if not value or not value.strip():
            raise ValueError("candidate fields cannot be blank")
        return value.strip()


class PairwiseSample(BaseModel):
    schema_version: int = 1
    sample_id: str
    scenario_id: str
    mode: SceneMode
    context: str
    candidate_a: PairwiseCandidate
    candidate_b: PairwiseCandidate
    human_winner: Optional[OriginalWinner] = None

    class Config:
        extra = "forbid"

    @validator("schema_version")
    def _supported_schema(cls, value):
        if value != 1:
            raise ValueError("only pairwise schema_version=1 is supported")
        return value

    @root_validator(skip_on_failure=True)
    def _systems_are_distinct(cls, values):
        left = values.get("candidate_a")
        right = values.get("candidate_b")
        if left and right and left.system_id == right.system_id:
            raise ValueError("pairwise candidates require distinct system_id")
        return values


class HumanBlindLabel(BaseModel):
    schema_version: int = 1
    sample_id: str
    winner: BlindWinner

    class Config:
        extra = "forbid"

    @validator("schema_version")
    def _supported_schema(cls, value):
        if value != 1:
            raise ValueError("only human label schema_version=1 is supported")
        return value


class DimensionJudgment(BaseModel):
    winner: BlindWinner
    rationale: str = Field(..., min_length=1, max_length=160)

    class Config:
        extra = "forbid"


class JudgePayload(BaseModel):
    anthropomorphism: DimensionJudgment
    character_fidelity: DimensionJudgment
    immersion_setting: DimensionJudgment
    writing_quality: DimensionJudgment
    storyline_quality: Optional[DimensionJudgment] = None
    creativity: Optional[DimensionJudgment] = None
    overall_winner: BlindWinner
    overall_rationale: str = Field(..., min_length=1, max_length=240)

    class Config:
        extra = "forbid"

    def validate_for_mode(self, mode: SceneMode) -> None:
        if mode == SceneMode.script:
            if self.storyline_quality is None or self.creativity is not None:
                raise ValueError(
                    "Script mode requires storyline_quality only"
                )
        else:
            if self.creativity is None or self.storyline_quality is not None:
                raise ValueError("Free mode requires creativity only")

    def dimensions_for_mode(
        self,
        mode: SceneMode,
    ) -> Dict[str, DimensionJudgment]:
        values = {
            name: getattr(self, name)
            for name in COMMON_DIMENSIONS
        }
        key = (
            "storyline_quality"
            if mode == SceneMode.script
            else "creativity"
        )
        values[key] = getattr(self, key)
        return values


class PairwiseRecord(BaseModel):
    sample_id: str
    scenario_id: str
    mode: SceneMode
    status: PairwiseStatus
    blind_left_original: OriginalWinner
    blind_right_original: OriginalWinner
    prompt_hash: str
    judge_winner_blind: Optional[BlindWinner] = None
    judge_winner_original: Optional[OriginalWinner] = None
    effective_winner_original: Optional[OriginalWinner] = None
    objective_override: bool = False
    dimensions: Dict[str, DimensionJudgment] = Field(default_factory=dict)
    overall_rationale: str = ""
    human_winner: Optional[OriginalWinner] = None
    error: Optional[str] = None
    raw_output_excerpt: str = ""
    llm_calls: List[LLMCallUsage] = Field(default_factory=list)
    llm_usage: LLMUsageSummary = Field(default_factory=LLMUsageSummary)

    class Config:
        extra = "forbid"


class PairwiseReport(BaseModel):
    schema_version: int = 1
    sample_count: int = Field(..., ge=0)
    judged_count: int = Field(..., ge=0)
    parse_failure_count: int = Field(..., ge=0)
    call_failure_count: int = Field(..., ge=0)
    objective_override_count: int = Field(..., ge=0)
    original_a_win_count: int = Field(..., ge=0)
    original_b_win_count: int = Field(..., ge=0)
    tie_count: int = Field(..., ge=0)
    per_system_wins: Dict[str, int] = Field(default_factory=dict)
    dimension_wins: Dict[str, Dict[str, int]] = Field(
        default_factory=dict
    )
    human_label_count: int = Field(..., ge=0)
    human_agreement_rate: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
    )
    cohen_kappa: Optional[float] = Field(None, ge=-1.0, le=1.0)
    llm_usage: LLMUsageSummary
    estimated_cost: Optional[float] = Field(None, ge=0.0)
    cost_currency: Optional[str] = None
    records: List[PairwiseRecord] = Field(default_factory=list)

    class Config:
        extra = "forbid"


JudgeCallable = Callable[[List[dict]], str]


class PairwiseEvaluator:
    def __init__(
        self,
        *,
        model: Optional[str] = None,
        random_seed: int = 20260730,
        pricing: Optional[PricingConfig] = None,
        call_llm: Optional[JudgeCallable] = None,
        max_retries: int = 1,
    ) -> None:
        if call_llm is not None:
            self.api_key = ""
            self.base_url = ""
            self.model = model or "injected-pairwise-judge"
        else:
            config = get_llm_config()
            self.api_key = config.api_key
            self.base_url = config.base_url
            self.model = model or config.model
        self.random_seed = random_seed
        self.pricing = pricing or PricingConfig()
        self._injected_call = call_llm
        self.max_retries = max(0, int(max_retries))

    def evaluate(
        self,
        samples: Sequence[PairwiseSample],
    ) -> PairwiseReport:
        records = [self.judge(sample) for sample in samples]
        return _aggregate_pairwise(records, samples, self.pricing)

    def judge(self, sample: PairwiseSample) -> PairwiseRecord:
        left_original, right_original = blind_order(
            sample.sample_id,
            self.random_seed,
        )
        left = (
            sample.candidate_a
            if left_original == OriginalWinner.a
            else sample.candidate_b
        )
        right = (
            sample.candidate_a
            if right_original == OriginalWinner.a
            else sample.candidate_b
        )
        prompt = build_pairwise_prompt(sample, left.text, right.text)
        prompt_hash = hashlib.sha256(
            prompt.encode("utf-8")
        ).hexdigest()
        messages = [
            {
                "role": "system",
                "content": "严格执行盲测评分，只输出指定 JSON。",
            },
            {"role": "user", "content": prompt},
        ]
        raw = ""
        payload = None
        parse_error = None
        with capture_llm_usage() as usage:
            for attempt in range(self.max_retries + 1):
                try:
                    raw = self._call_llm(messages)
                except Exception as exc:
                    return PairwiseRecord(
                        sample_id=sample.sample_id,
                        scenario_id=sample.scenario_id,
                        mode=sample.mode,
                        status=PairwiseStatus.call_failed,
                        blind_left_original=left_original,
                        blind_right_original=right_original,
                        prompt_hash=prompt_hash,
                        human_winner=sample.human_winner,
                        error=f"{type(exc).__name__}: {exc}",
                        llm_calls=usage.calls,
                        llm_usage=usage.summary(),
                    )
                try:
                    parsed = JudgePayload.parse_obj(_extract_json(raw))
                    parsed.validate_for_mode(sample.mode)
                    payload = parsed
                    break
                except Exception as exc:
                    parse_error = exc
                    if attempt < self.max_retries:
                        messages.extend(
                            [
                                {"role": "assistant", "content": raw},
                                {
                                    "role": "user",
                                    "content": (
                                        "输出没有通过严格 Schema。请修正并只输出"
                                        " JSON；Free 只能含 creativity，Script "
                                        "只能含 storyline_quality。错误："
                                        f"{type(exc).__name__}: {exc}"
                                    ),
                                },
                            ]
                        )
            if payload is None:
                return PairwiseRecord(
                    sample_id=sample.sample_id,
                    scenario_id=sample.scenario_id,
                    mode=sample.mode,
                    status=PairwiseStatus.parse_failed,
                    blind_left_original=left_original,
                    blind_right_original=right_original,
                    prompt_hash=prompt_hash,
                    human_winner=sample.human_winner,
                    error=(
                        f"{type(parse_error).__name__}: {parse_error}"
                        if parse_error is not None
                        else "unknown parse error"
                    ),
                    raw_output_excerpt=raw[:1000],
                    llm_calls=usage.calls,
                    llm_usage=usage.summary(),
                )

        judge_original = _to_original(
            payload.overall_winner,
            left_original,
            right_original,
        )
        effective, overridden = _objective_gate(
            sample,
            judge_original,
        )
        return PairwiseRecord(
            sample_id=sample.sample_id,
            scenario_id=sample.scenario_id,
            mode=sample.mode,
            status=PairwiseStatus.judged,
            blind_left_original=left_original,
            blind_right_original=right_original,
            prompt_hash=prompt_hash,
            judge_winner_blind=payload.overall_winner,
            judge_winner_original=judge_original,
            effective_winner_original=effective,
            objective_override=overridden,
            dimensions=payload.dimensions_for_mode(sample.mode),
            overall_rationale=payload.overall_rationale,
            human_winner=sample.human_winner,
            llm_calls=usage.calls,
            llm_usage=usage.summary(),
        )

    def _call_llm(self, messages: List[dict]) -> str:
        if self._injected_call is not None:
            return self._injected_call(messages)
        response = call_openai_compatible(
            openai.ChatCompletion.create,
            operation="pairwise_judge",
            api_key=self.api_key,
            api_base=self.base_url,
            model=self.model,
            messages=messages,
            temperature=0.0,
            **chat_generation_options(
                self.model,
                max_tokens=2048,
                thinking=False,
            ),
        )
        return response.choices[0].message.content.strip()


def blind_order(
    sample_id: str,
    random_seed: int,
) -> Tuple[OriginalWinner, OriginalWinner]:
    digest = hashlib.sha256(
        f"{random_seed}|{sample_id}".encode("utf-8")
    ).digest()
    return (
        (OriginalWinner.b, OriginalWinner.a)
        if digest[0] & 1
        else (OriginalWinner.a, OriginalWinner.b)
    )


def build_pairwise_prompt(
    sample: PairwiseSample,
    left_text: str,
    right_text: str,
) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    if sample.mode == SceneMode.script:
        mode_key = "storyline_quality"
        mode_dimension = (
            "5. storyline_quality：是否形成连贯、可跟随、因果清楚的剧情推进。"
        )
    else:
        mode_key = "creativity"
        mode_dimension = (
            "5. creativity：是否在不破坏世界事实的前提下产生有意义的新变化。"
        )
    return (
        template.replace("{{MODE_DIMENSION}}", mode_dimension)
        .replace("{{MODE_KEY}}", mode_key)
        .replace(
            "{{CONTEXT}}",
            f"模式：{sample.mode.value}\n{sample.context.strip()}",
        )
        .replace("{{LEFT}}", left_text.strip())
        .replace("{{RIGHT}}", right_text.strip())
    )


def _aggregate_pairwise(
    records: Sequence[PairwiseRecord],
    samples: Sequence[PairwiseSample],
    pricing: PricingConfig,
) -> PairwiseReport:
    samples_by_id = {item.sample_id: item for item in samples}
    winners = Counter(
        item.effective_winner_original.value
        for item in records
        if item.effective_winner_original is not None
    )
    system_wins = Counter()
    dimension_wins: Dict[str, Counter] = defaultdict(Counter)
    for record in records:
        sample = samples_by_id[record.sample_id]
        if record.effective_winner_original in (
            OriginalWinner.a,
            OriginalWinner.b,
        ):
            candidate = (
                sample.candidate_a
                if record.effective_winner_original == OriginalWinner.a
                else sample.candidate_b
            )
            system_wins[candidate.system_id] += 1
        for dimension, judgment in record.dimensions.items():
            original = _to_original(
                judgment.winner,
                record.blind_left_original,
                record.blind_right_original,
            )
            if original == OriginalWinner.tie:
                dimension_wins[dimension]["tie"] += 1
            else:
                candidate = (
                    sample.candidate_a
                    if original == OriginalWinner.a
                    else sample.candidate_b
                )
                dimension_wins[dimension][candidate.system_id] += 1

    labeled = [
        item
        for item in records
        if item.human_winner is not None
        and item.effective_winner_original is not None
        and item.status == PairwiseStatus.judged
    ]
    agreement = safe_rate(
        sum(
            item.human_winner == item.effective_winner_original
            for item in labeled
        ),
        len(labeled),
    )
    kappa = _cohen_kappa(
        [
            (
                item.human_winner,
                item.effective_winner_original,
            )
            for item in labeled
        ]
    )
    usage = LLMUsageSummary(
        call_count=sum(item.llm_usage.call_count for item in records),
        failed_call_count=sum(
            item.llm_usage.failed_call_count for item in records
        ),
        prompt_tokens=sum(
            item.llm_usage.prompt_tokens for item in records
        ),
        completion_tokens=sum(
            item.llm_usage.completion_tokens for item in records
        ),
        total_tokens=sum(item.llm_usage.total_tokens for item in records),
        cached_tokens=sum(
            item.llm_usage.cached_tokens for item in records
        ),
        latency_ms=round(
            sum(item.llm_usage.latency_ms for item in records),
            3,
        ),
    )
    return PairwiseReport(
        sample_count=len(records),
        judged_count=sum(
            item.status == PairwiseStatus.judged for item in records
        ),
        parse_failure_count=sum(
            item.status == PairwiseStatus.parse_failed for item in records
        ),
        call_failure_count=sum(
            item.status == PairwiseStatus.call_failed for item in records
        ),
        objective_override_count=sum(
            item.objective_override for item in records
        ),
        original_a_win_count=winners["a"],
        original_b_win_count=winners["b"],
        tie_count=winners["tie"],
        per_system_wins=dict(sorted(system_wins.items())),
        dimension_wins={
            key: dict(sorted(value.items()))
            for key, value in sorted(dimension_wins.items())
        },
        human_label_count=len(labeled),
        human_agreement_rate=agreement,
        cohen_kappa=kappa,
        llm_usage=usage,
        estimated_cost=estimate_cost(usage, pricing),
        cost_currency=pricing.currency if pricing.configured else None,
        records=list(records),
    )


def _objective_gate(
    sample: PairwiseSample,
    judge_winner: OriginalWinner,
) -> Tuple[OriginalWinner, bool]:
    a_passed = sample.candidate_a.objective_passed
    b_passed = sample.candidate_b.objective_passed
    if a_passed and not b_passed:
        return OriginalWinner.a, judge_winner != OriginalWinner.a
    if b_passed and not a_passed:
        return OriginalWinner.b, judge_winner != OriginalWinner.b
    if not a_passed and not b_passed:
        return OriginalWinner.tie, judge_winner != OriginalWinner.tie
    return judge_winner, False


def _to_original(
    winner: BlindWinner,
    left: OriginalWinner,
    right: OriginalWinner,
) -> OriginalWinner:
    if winner == BlindWinner.tie:
        return OriginalWinner.tie
    return left if winner == BlindWinner.left else right


def _extract_json(raw: str) -> dict:
    text = (raw or "").strip()
    try:
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("judge output must be a JSON object")
        return value
    except json.JSONDecodeError:
        match = re.search(
            r"```(?:json)?\s*(\{.*\})\s*```",
            text,
            re.DOTALL,
        )
        if not match:
            raise ValueError("judge output is not valid JSON")
        value = json.loads(match.group(1))
        if not isinstance(value, dict):
            raise ValueError("judge output must be a JSON object")
        return value


def _cohen_kappa(
    labels: Sequence[Tuple[OriginalWinner, OriginalWinner]],
) -> Optional[float]:
    if not labels:
        return None
    categories = list(OriginalWinner)
    observed = sum(left == right for left, right in labels) / len(labels)
    left_counts = Counter(left for left, _ in labels)
    right_counts = Counter(right for _, right in labels)
    expected = sum(
        (left_counts[value] / len(labels))
        * (right_counts[value] / len(labels))
        for value in categories
    )
    if expected >= 1.0:
        return 1.0 if observed >= 1.0 else None
    return round((observed - expected) / (1.0 - expected), 6)


def load_pairwise_samples(path: Path) -> List[PairwiseSample]:
    samples: List[PairwiseSample] = []
    seen = set()
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        try:
            sample = PairwiseSample.parse_obj(json.loads(text))
        except Exception as exc:
            raise ValueError(
                f"invalid pairwise sample at {path}:{line_number}: {exc}"
            ) from exc
        if sample.sample_id in seen:
            raise ValueError(
                f"duplicate pairwise sample_id: {sample.sample_id}"
            )
        seen.add(sample.sample_id)
        samples.append(sample)
    if not samples:
        raise ValueError(f"pairwise file has no samples: {path}")
    return samples


def load_human_labels(path: Path) -> List[HumanBlindLabel]:
    labels: List[HumanBlindLabel] = []
    seen = set()
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        try:
            label = HumanBlindLabel.parse_obj(json.loads(text))
        except Exception as exc:
            raise ValueError(
                f"invalid human label at {path}:{line_number}: {exc}"
            ) from exc
        if label.sample_id in seen:
            raise ValueError(
                f"duplicate human label sample_id: {label.sample_id}"
            )
        seen.add(label.sample_id)
        labels.append(label)
    if not labels:
        raise ValueError(f"human label file has no labels: {path}")
    return labels


def apply_human_labels(
    samples: Sequence[PairwiseSample],
    labels: Sequence[HumanBlindLabel],
    *,
    random_seed: int,
) -> List[PairwiseSample]:
    labels_by_id = {item.sample_id: item for item in labels}
    sample_ids = {item.sample_id for item in samples}
    unknown = sorted(set(labels_by_id) - sample_ids)
    if unknown:
        raise ValueError(
            "human labels reference unknown samples: " + ", ".join(unknown)
        )
    labeled = []
    for sample in samples:
        label = labels_by_id.get(sample.sample_id)
        if label is None:
            labeled.append(sample.copy(deep=True))
            continue
        left_original, right_original = blind_order(
            sample.sample_id,
            random_seed,
        )
        original = _to_original(
            label.winner,
            left_original,
            right_original,
        )
        labeled.append(
            sample.copy(
                update={"human_winner": original},
                deep=True,
            )
        )
    return labeled


def calibrate_pairwise_report(
    report: PairwiseReport,
    labels: Sequence[HumanBlindLabel],
) -> PairwiseReport:
    """把盲标回填到已有 Judge 报告，不再次调用或重新评分 LLM。"""

    labels_by_id = {item.sample_id: item for item in labels}
    records_by_id = {item.sample_id: item for item in report.records}
    unknown = sorted(set(labels_by_id) - set(records_by_id))
    if unknown:
        raise ValueError(
            "human labels reference unknown report samples: "
            + ", ".join(unknown)
        )

    records: List[PairwiseRecord] = []
    for record in report.records:
        label = labels_by_id.get(record.sample_id)
        if label is None:
            records.append(record.copy(deep=True))
            continue
        original = _to_original(
            label.winner,
            record.blind_left_original,
            record.blind_right_original,
        )
        records.append(
            record.copy(
                update={"human_winner": original},
                deep=True,
            )
        )

    comparable = [
        item
        for item in records
        if item.human_winner is not None
        and item.effective_winner_original is not None
        and item.status == PairwiseStatus.judged
    ]
    agreement = safe_rate(
        sum(
            item.human_winner == item.effective_winner_original
            for item in comparable
        ),
        len(comparable),
    )
    kappa = _cohen_kappa(
        [
            (item.human_winner, item.effective_winner_original)
            for item in comparable
        ]
    )
    return report.copy(
        update={
            "human_label_count": len(comparable),
            "human_agreement_rate": agreement,
            "cohen_kappa": kappa,
            "records": records,
        },
        deep=True,
    )


def render_human_packet(
    samples: Sequence[PairwiseSample],
    *,
    random_seed: int,
) -> str:
    lines = [
        "# NovelSim Pairwise 人工盲标包",
        "",
        "逐题只填写 `left`、`right` 或 `tie`。不要查看系统 ID 或 Judge 结果。",
        "标签 JSONL 格式："
        '`{"schema_version":1,"sample_id":"...","winner":"left"}`',
        "",
    ]
    for index, sample in enumerate(samples, start=1):
        left_original, _ = blind_order(sample.sample_id, random_seed)
        left = (
            sample.candidate_a
            if left_original == OriginalWinner.a
            else sample.candidate_b
        )
        right = (
            sample.candidate_b
            if left_original == OriginalWinner.a
            else sample.candidate_a
        )
        lines.extend(
            [
                f"## {index}. {sample.sample_id}",
                "",
                f"模式：`{sample.mode.value}`",
                "",
                sample.context,
                "",
                "### Left",
                "",
                left.text,
                "",
                "### Right",
                "",
                right.text,
                "",
                "人工选择：`_____`",
                "",
            ]
        )
    return "\n".join(lines)


def render_pairwise_markdown(report: PairwiseReport) -> str:
    lines = [
        "# NovelSim BOOKWORLD 风格 Pairwise 盲测",
        "",
        f"- 样本：{report.sample_count}",
        f"- 成功评分：{report.judged_count}",
        f"- 解析失败：{report.parse_failure_count}",
        f"- 调用失败：{report.call_failure_count}",
        f"- 客观门禁覆盖 Judge：{report.objective_override_count}",
        "",
        "| 结果 | 数量 |",
        "|---|---:|",
        f"| 原始候选 A 胜 | {report.original_a_win_count} |",
        f"| 原始候选 B 胜 | {report.original_b_win_count} |",
        f"| 平局 | {report.tie_count} |",
        "",
        "系统胜场："
        + (
            "、".join(
                f"`{key}`={value}"
                for key, value in report.per_system_wins.items()
            )
            if report.per_system_wins
            else "无有效胜场"
        ),
        "",
        f"- 人工标签：{report.human_label_count}",
        "- Judge/人工一致率："
        + (
            f"{report.human_agreement_rate:.2%}"
            if report.human_agreement_rate is not None
            else "未测"
        ),
        "- Cohen's κ："
        + (
            f"{report.cohen_kappa:.3f}"
            if report.cohen_kappa is not None
            else "未测/不可计算"
        ),
        f"- 模型调用：{report.llm_usage.call_count}",
        f"- 总 Token：{report.llm_usage.total_tokens}",
        "- 估算成本："
        + (
            f"{report.estimated_cost:.8f} {report.cost_currency}"
            if report.estimated_cost is not None
            else "未配置单价"
        ),
        "",
        "> 主观胜负不覆盖客观世界规则、事件依据或任务正确性门禁。",
        "",
    ]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--seed", type=int, default=20260730)
    parser.add_argument("--human-labels", type=Path)
    parser.add_argument("--human-packet", type=Path)
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--input-per-million", type=float, default=0.0)
    parser.add_argument(
        "--cached-input-per-million",
        type=float,
        default=0.0,
    )
    parser.add_argument("--output-per-million", type=float, default=0.0)
    args = parser.parse_args(argv)
    pricing = PricingConfig(
        currency=args.currency,
        input_per_million=args.input_per_million,
        cached_input_per_million=args.cached_input_per_million,
        output_per_million=args.output_per_million,
    )
    samples = load_pairwise_samples(args.samples)
    if args.human_packet is not None:
        args.human_packet.parent.mkdir(parents=True, exist_ok=True)
        args.human_packet.write_text(
            render_human_packet(samples, random_seed=args.seed),
            encoding="utf-8",
        )
    if args.human_labels is not None:
        samples = apply_human_labels(
            samples,
            load_human_labels(args.human_labels),
            random_seed=args.seed,
        )
    report = PairwiseEvaluator(
        model=args.model,
        random_seed=args.seed,
        pricing=pricing,
    ).evaluate(samples)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(report.dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(
            render_pairwise_markdown(report),
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "samples": report.sample_count,
                "judged": report.judged_count,
                "parse_failures": report.parse_failure_count,
                "call_failures": report.call_failure_count,
                "json": str(args.json.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return (
        0
        if report.parse_failure_count == 0
        and report.call_failure_count == 0
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
