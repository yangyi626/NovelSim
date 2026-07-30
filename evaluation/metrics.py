"""从逐局明细计算可审计指标与验收门槛。"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Sequence

from engine.llm_telemetry import LLMUsageSummary

from .models import (
    AcceptanceCheck,
    AggregateMetrics,
    CaseRunRecord,
    LatencyStats,
    PricingConfig,
)


def safe_rate(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def latency_stats(values: Iterable[float]) -> LatencyStats:
    samples = sorted(max(0.0, float(value)) for value in values)
    if not samples:
        return LatencyStats()
    return LatencyStats(
        sample_count=len(samples),
        mean_ms=round(sum(samples) / len(samples), 3),
        p50_ms=round(_percentile(samples, 0.50), 3),
        p95_ms=round(_percentile(samples, 0.95), 3),
        max_ms=round(samples[-1], 3),
    )


def estimate_cost(
    usage: LLMUsageSummary,
    pricing: PricingConfig,
) -> Optional[float]:
    if not pricing.configured:
        return None
    cached = min(usage.cached_tokens, usage.prompt_tokens)
    uncached = max(0, usage.prompt_tokens - cached)
    cost = (
        uncached * pricing.input_per_million
        + cached * pricing.cached_input_per_million
        + usage.completion_tokens * pricing.output_per_million
    ) / 1_000_000.0
    return round(cost, 8)


def aggregate_metrics(
    records: Sequence[CaseRunRecord],
    pricing: PricingConfig,
) -> AggregateMetrics:
    run_count = len(records)
    calls = sum(item.tool_call_count for item in records)
    expected_rejections = sum(
        item.expected_rejection_count for item in records
    )
    matched_rejections = sum(
        item.matched_expected_rejection_count for item in records
    )
    eligible_calls = max(0, calls - expected_rejections)
    successful_calls = sum(
        item.successful_tool_calls for item in records
    )
    propagation_samples = sum(item.propagation_count for item in records)
    valid_propagations = sum(
        item.valid_propagation_count for item in records
    )
    evidence_samples = sum(
        item.evidence_chain_sample_count for item in records
    )
    complete_evidence = sum(
        item.complete_evidence_chain_count for item in records
    )
    core = [item for item in records if "core_objective" in item.tags]
    alliance_cases = [
        item for item in records if "alliance_expected" in item.tags
    ]
    structural_violations = sum(
        item.structural_character_violation_count for item in records
    )

    stage_samples: Dict[str, List[float]] = defaultdict(list)
    for item in records:
        for stage, duration in item.stage_latency_ms.items():
            stage_samples[stage].append(duration)

    failure_distribution: Counter = Counter()
    for item in records:
        failure_distribution.update(item.failure_distribution)

    llm_usage = LLMUsageSummary(
        call_count=sum(item.llm_usage.call_count for item in records),
        failed_call_count=sum(
            item.llm_usage.failed_call_count for item in records
        ),
        prompt_tokens=sum(item.llm_usage.prompt_tokens for item in records),
        completion_tokens=sum(
            item.llm_usage.completion_tokens for item in records
        ),
        total_tokens=sum(item.llm_usage.total_tokens for item in records),
        cached_tokens=sum(item.llm_usage.cached_tokens for item in records),
        latency_ms=round(
            sum(item.llm_usage.latency_ms for item in records),
            3,
        ),
    )
    return AggregateMetrics(
        run_count=run_count,
        benchmark_pass_rate=(
            safe_rate(sum(item.passed for item in records), run_count)
            or 0.0
        ),
        objective_completion_rate=(
            safe_rate(
                sum(item.objective_satisfied for item in records),
                run_count,
            )
            or 0.0
        ),
        core_event_completion_rate=safe_rate(
            sum(item.objective_satisfied for item in core),
            len(core),
        ),
        tool_success_rate=safe_rate(successful_calls, eligible_calls),
        expected_rejection_rate=safe_rate(
            matched_rejections,
            expected_rejections,
        ),
        replay_consistency_rate=safe_rate(
            sum(item.replay_consistent for item in records),
            run_count,
        ),
        propagation_accuracy=safe_rate(
            valid_propagations,
            propagation_samples,
        ),
        evidence_chain_completeness=safe_rate(
            complete_evidence,
            evidence_samples,
        ),
        structural_character_consistency=safe_rate(
            max(0, calls - structural_violations),
            calls,
        ),
        alliance_formation_rate=safe_rate(
            sum(item.alliance_count > 0 for item in alliance_cases),
            len(alliance_cases),
        ),
        illegal_patch_commit_count=sum(
            item.illegal_patch_commit_count for item in records
        ),
        unknown_entity_accept_count=sum(
            item.unknown_entity_accept_count for item in records
        ),
        causal_violation_count=sum(
            item.causal_violation_count for item in records
        ),
        knowledge_leak_count=sum(
            item.knowledge_leak_count for item in records
        ),
        unexpected_rejection_count=sum(
            item.unexpected_rejection_count for item in records
        ),
        invalid_loop_count=sum(
            item.invalid_loop_count for item in records
        ),
        failure_distribution=dict(sorted(failure_distribution.items())),
        total_latency=latency_stats(item.latency_ms for item in records),
        stage_latency={
            stage: latency_stats(values)
            for stage, values in sorted(stage_samples.items())
        },
        llm_usage=llm_usage,
        estimated_cost=estimate_cost(llm_usage, pricing),
        cost_currency=pricing.currency if pricing.configured else None,
    )


def build_acceptance_checks(
    metrics: AggregateMetrics,
    *,
    real_llm_run_count: int = 0,
    narrative_sample_count: int = 0,
    narrative_grounded_count: int = 0,
) -> List[AcceptanceCheck]:
    checks = [
        _zero_check(
            "illegal_patch_commits",
            metrics.illegal_patch_commit_count,
            "StatePatch 校验与事件回放",
        ),
        _zero_check(
            "unknown_entity_accepts",
            metrics.unknown_entity_accept_count,
            "事件目标与权威实体表",
        ),
        _zero_check(
            "causal_violation_commits",
            metrics.causal_violation_count,
            "ToolCall/事件/Patch causal_evidence",
        ),
        _zero_check(
            "knowledge_leaks",
            metrics.knowledge_leak_count,
            "传播事件提交前的来源角色认知",
        ),
        _rate_check(
            "replay_consistency",
            metrics.replay_consistency_rate,
            1.0,
            "逐事件重放与终态哈希",
        ),
        _rate_check(
            "benchmark_expectations",
            metrics.benchmark_pass_rate,
            1.0,
            "固定案例断言",
        ),
        _rate_check(
            "core_event_completion",
            metrics.core_event_completion_rate,
            0.80,
            "标记 core_objective 的固定案例",
        ),
        _rate_check(
            "tool_execution_success",
            metrics.tool_success_rate,
            0.95,
            "排除规则预期拒绝后的 ToolResult",
        ),
        _rate_check(
            "expected_rejections",
            metrics.expected_rejection_rate,
            1.0,
            "非法案例的结构化失败码",
        ),
        _rate_check(
            "propagation_accuracy",
            metrics.propagation_accuracy,
            1.0,
            "传播记录、目标认知和证据三方一致",
        ),
        _rate_check(
            "evidence_chain_completeness",
            metrics.evidence_chain_completeness,
            1.0,
            "传播证据及 parent_evidence_ids",
        ),
    ]
    narrative_rate = safe_rate(
        narrative_grounded_count,
        narrative_sample_count,
    )
    checks.append(
        AcceptanceCheck(
            check_id="narrative_event_grounding",
            passed=narrative_rate == 1.0,
            measured=narrative_rate is not None,
            required_for_suite=False,
            actual=(
                _format_rate(narrative_rate)
                if narrative_rate is not None
                else "not measured"
            ),
            threshold="100%",
            evidence=(
                "确定性 Scene 工具套件不生成 Narrative；"
                "由真实 Turn/Pairwise 套件补充"
            ),
        )
    )
    checks.append(
        AcceptanceCheck(
            check_id="real_llm_regression_runs",
            passed=real_llm_run_count >= 20,
            measured=real_llm_run_count > 0,
            required_for_suite=False,
            actual=str(real_llm_run_count),
            threshold=">=20",
            evidence="真实模型回归单独运行，不能用确定性 Mock 代替",
        )
    )
    return checks


def _zero_check(
    check_id: str,
    actual: int,
    evidence: str,
) -> AcceptanceCheck:
    return AcceptanceCheck(
        check_id=check_id,
        passed=actual == 0,
        actual=str(actual),
        threshold="0",
        evidence=evidence,
    )


def _rate_check(
    check_id: str,
    actual: Optional[float],
    threshold: float,
    evidence: str,
) -> AcceptanceCheck:
    return AcceptanceCheck(
        check_id=check_id,
        passed=actual is not None and actual >= threshold,
        measured=actual is not None,
        actual=(
            _format_rate(actual) if actual is not None else "not measured"
        ),
        threshold=f">={threshold:.0%}",
        evidence=evidence,
    )


def _format_rate(value: float) -> str:
    return f"{value:.2%}"


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    position = (len(values) - 1) * quantile
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return float(values[lower])
    fraction = position - lower
    return float(
        values[lower] + (values[upper] - values[lower]) * fraction
    )
