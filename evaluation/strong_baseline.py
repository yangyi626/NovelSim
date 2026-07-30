"""生成同模型 direct-prompt 叙事基线，并输出可盲测 Pairwise 数据集。"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Callable, List, Optional, Sequence

import openai
from pydantic import BaseModel, Field

from engine.config import get_llm_config
from engine.llm_telemetry import (
    LLMCallUsage,
    LLMUsageSummary,
    call_openai_compatible,
    capture_llm_usage,
    chat_generation_options,
)

from .metrics import estimate_cost
from .models import PricingConfig
from .pairwise import PairwiseCandidate, PairwiseSample, load_pairwise_samples
from .pairwise_dataset import write_pairwise_jsonl


BASELINE_SYSTEM_PROMPT = """你是互动小说叙事器。根据给定世界、玩家输入和权威事件，
写一段 100–180 字的中文小说正文。只输出正文，不要 JSON、标题或解释。
必须保持权威动作和结果，不得引入现代科技、未给出的关键人物或改变事件结果。"""


class StrongBaselineRecord(BaseModel):
    sample_id: str
    text: str
    objective_passed: bool
    latency_ms: float = Field(..., ge=0.0)
    llm_calls: List[LLMCallUsage] = Field(default_factory=list)
    llm_usage: LLMUsageSummary = Field(default_factory=LLMUsageSummary)

    class Config:
        extra = "forbid"


class StrongBaselineReport(BaseModel):
    schema_version: int = 1
    generated_at: str
    model: str
    sample_count: int = Field(..., ge=0)
    objective_pass_count: int = Field(..., ge=0)
    llm_usage: LLMUsageSummary
    estimated_cost: Optional[float] = Field(None, ge=0.0)
    cost_currency: Optional[str] = None
    records: List[StrongBaselineRecord] = Field(default_factory=list)

    class Config:
        extra = "forbid"


BaselineCallable = Callable[[List[dict]], str]


class StrongBaselineGenerator:
    def __init__(
        self,
        *,
        model: Optional[str] = None,
        pricing: Optional[PricingConfig] = None,
        call_llm: Optional[BaselineCallable] = None,
    ) -> None:
        if call_llm is not None:
            self.model = model or "injected-direct-prompt-baseline"
            self.api_key = ""
            self.base_url = ""
        else:
            config = get_llm_config()
            self.model = model or config.model
            self.api_key = config.api_key
            self.base_url = config.base_url
        self.pricing = pricing or PricingConfig()
        self._injected_call = call_llm

    def generate(
        self,
        samples: Sequence[PairwiseSample],
    ) -> tuple:
        records = []
        output_samples = []
        for sample in samples:
            started = perf_counter()
            messages = [
                {"role": "system", "content": BASELINE_SYSTEM_PROMPT},
                {"role": "user", "content": sample.context},
            ]
            with capture_llm_usage() as usage:
                text = self._call_llm(messages).strip()
            record = StrongBaselineRecord(
                sample_id=sample.sample_id,
                text=text,
                objective_passed=_objective_passed(sample, text),
                latency_ms=round(
                    (perf_counter() - started) * 1000.0,
                    3,
                ),
                llm_calls=usage.calls,
                llm_usage=usage.summary(),
            )
            records.append(record)
            output_samples.append(
                sample.copy(
                    update={
                        "candidate_b": PairwiseCandidate(
                            system_id="direct_prompt_llm_baseline",
                            text=text,
                            objective_passed=record.objective_passed,
                        )
                    },
                    deep=True,
                )
            )
        report = _report(records, self.model, self.pricing)
        return output_samples, report

    def _call_llm(self, messages: List[dict]) -> str:
        if self._injected_call is not None:
            return self._injected_call(messages)
        response = call_openai_compatible(
            openai.ChatCompletion.create,
            operation="direct_prompt_narrative_baseline",
            api_key=self.api_key,
            api_base=self.base_url,
            model=self.model,
            messages=messages,
            temperature=0.7,
            **chat_generation_options(
                self.model,
                max_tokens=1024,
                thinking=False,
            ),
        )
        return response.choices[0].message.content


def _objective_passed(sample: PairwiseSample, text: str) -> bool:
    normalized = "".join(text.split())
    if not normalized:
        return False
    forbidden_success = (
        "开飞机",
        "乘飞机",
        "坐飞机",
        "坐直升机",
        "乘直升机",
    )
    if any(term in normalized for term in forbidden_success):
        return False
    if "午前" in sample.context and any(
        term in normalized
        for term in ("夜色", "夜风", "月色", "灯笼摇曳")
    ):
        return False
    if "午前" in sample.context and re.search(
        r"(?:^|[，。；！？])入夜(?:[，。；！？时后])",
        normalized,
    ):
        return False
    actor_present = "夜轻歌" in normalized or "我" in normalized
    if "权威动作：swap_object" in sample.context:
        return actor_present and "外衫" in normalized
    if "权威动作：move" in sample.context:
        unsupported_companion_move = (
            "夜清清" in normalized
            and any(
                term in normalized
                for term in (
                    "跟上",
                    "紧随其后",
                    "亦步亦趋",
                    "一前一后",
                    "她们",
                    "两人同行",
                    "两人并肩",
                )
            )
        )
        return (
            actor_present
            and "夜府" in normalized
            and not unsupported_companion_move
        )
    return False


def _report(
    records: Sequence[StrongBaselineRecord],
    model: str,
    pricing: PricingConfig,
) -> StrongBaselineReport:
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
    return StrongBaselineReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        model=model,
        sample_count=len(records),
        objective_pass_count=sum(
            item.objective_passed for item in records
        ),
        llm_usage=usage,
        estimated_cost=estimate_cost(usage, pricing),
        cost_currency=pricing.currency if pricing.configured else None,
        records=list(records),
    )


def render_strong_baseline_markdown(
    report: StrongBaselineReport,
) -> str:
    usage = report.llm_usage
    lines = [
        "# NovelSim Direct-prompt LLM 叙事基线",
        "",
        f"- 模型：`{report.model}`",
        f"- 样本：{report.sample_count}",
        f"- 核心事件启发式通过：{report.objective_pass_count}/{report.sample_count}",
        f"- 模型调用：{usage.call_count}",
        f"- 总 Token：{usage.total_tokens}",
        "- 估算成本："
        + (
            f"{report.estimated_cost:.8f} {report.cost_currency}"
            if report.estimated_cost is not None
            else "未配置单价"
        ),
        "",
        "> 该基线获得同一权威事件上下文并直接生成 prose，但没有 NovelSim "
        "NarrativeOutput Schema、event_id 引用和运行时一致性审查。核心事件"
        "通过仅是保守关键词检查，不替代权威状态验证。",
        "",
    ]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--output-samples", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
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
    samples, report = StrongBaselineGenerator(
        pricing=pricing,
    ).generate(load_pairwise_samples(args.samples))
    write_pairwise_jsonl(args.output_samples, samples)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(report.dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(
            render_strong_baseline_markdown(report),
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "samples": report.sample_count,
                "objective_pass_count": report.objective_pass_count,
                "total_tokens": report.llm_usage.total_tokens,
                "output_samples": str(args.output_samples.resolve()),
                "json": str(args.json.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.objective_pass_count == report.sample_count else 2


if __name__ == "__main__":
    raise SystemExit(main())
