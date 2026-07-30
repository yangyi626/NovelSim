"""固定 20 局真实 LLM Turn 回归、断点续跑与用量报告。"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Callable, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field, validator

from engine import (
    LLMCallUsage,
    LLMUsageSummary,
    TurnPipeline,
    capture_llm_usage,
    check_narrative,
    evaluate_trajectory,
)
from engine.config import get_llm_config
from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import NIGHT, OUTER_ROBE

from .metrics import estimate_cost, latency_stats, safe_rate
from .models import LatencyStats, PricingConfig


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REAL_CASES = PROJECT_ROOT / "evaluation" / "real_cases.jsonl"


class RealLLMCase(BaseModel):
    schema_version: int = 1
    case_id: str
    user_text: str
    objective: str

    class Config:
        extra = "forbid"

    @validator("schema_version")
    def _version_supported(cls, value):
        if value != 1:
            raise ValueError("only real case schema_version=1 is supported")
        return value

    @validator("objective")
    def _objective_supported(cls, value):
        if value not in {
            "obtain_outer_robe",
            "reach_ye_residence",
            "command_without_forced_transfer",
            "reject_forbidden_concept",
            "reject_unknown_entity",
        }:
            raise ValueError(f"unsupported real objective: {value}")
        return value


class RealLLMRunRecord(BaseModel):
    case_id: str
    user_text: str
    objective: str
    status: str
    objective_passed: bool
    state_committed: bool
    action_type: Optional[str] = None
    operation_types: List[str] = Field(default_factory=list)
    rejection_code: Optional[str] = None
    event_id: Optional[str] = None
    final_version: int = Field(0, ge=0)
    replay_consistent: bool = False
    causal_valid: bool = False
    narrative_present: bool = False
    narrative_grounded: Optional[bool] = None
    narrative_text: str = ""
    dialogue_texts: List[str] = Field(default_factory=list)
    latency_ms: float = Field(..., ge=0.0)
    llm_calls: List[LLMCallUsage] = Field(default_factory=list)
    llm_usage: LLMUsageSummary = Field(default_factory=LLMUsageSummary)
    estimated_cost: Optional[float] = Field(None, ge=0.0)
    cost_currency: Optional[str] = None
    error: str = ""

    class Config:
        extra = "forbid"


class RealLLMReport(BaseModel):
    schema_version: int = 1
    suite_id: str = "novelsim-real-turn-v1"
    run_id: str
    generated_at: str
    model: str
    required_run_count: int = 20
    run_count: int = Field(..., ge=0)
    complete: bool
    passed: bool
    state_commit_rate: Optional[float] = Field(None, ge=0.0, le=1.0)
    objective_success_rate: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
    )
    replay_consistency_rate: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
    )
    causal_valid_rate: Optional[float] = Field(None, ge=0.0, le=1.0)
    narrative_coverage_rate: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
    )
    narrative_grounding_rate: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
    )
    failure_distribution: Dict[str, int] = Field(default_factory=dict)
    total_latency: LatencyStats
    llm_usage: LLMUsageSummary
    estimated_cost: Optional[float] = Field(None, ge=0.0)
    cost_currency: Optional[str] = None
    records: List[RealLLMRunRecord] = Field(default_factory=list)

    class Config:
        extra = "forbid"


PipelineFactory = Callable[[], TurnPipeline]
Checkpoint = Callable[[List[RealLLMRunRecord]], None]


def load_real_cases(
    path: Path = DEFAULT_REAL_CASES,
) -> List[RealLLMCase]:
    cases: List[RealLLMCase] = []
    seen = set()
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        try:
            case = RealLLMCase.parse_obj(json.loads(text))
        except Exception as exc:
            raise ValueError(
                f"invalid real case at {path}:{line_number}: {exc}"
            ) from exc
        if case.case_id in seen:
            raise ValueError(f"duplicate real case_id: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError(f"real LLM suite has no cases: {path}")
    return cases


class RealLLMRunner:
    def __init__(
        self,
        *,
        pricing: Optional[PricingConfig] = None,
        pipeline_factory: Optional[PipelineFactory] = None,
        model: Optional[str] = None,
    ) -> None:
        self.pricing = pricing or PricingConfig()
        self.pipeline_factory = pipeline_factory or TurnPipeline
        self.model = model or get_llm_config().model

    def run(
        self,
        cases: Sequence[RealLLMCase],
        *,
        existing_records: Sequence[RealLLMRunRecord] = (),
        checkpoint: Optional[Checkpoint] = None,
        report_cases: Optional[Sequence[RealLLMCase]] = None,
    ) -> RealLLMReport:
        suite_cases = list(report_cases) if report_cases is not None else list(cases)
        records = [item.copy(deep=True) for item in existing_records]
        done = {item.case_id for item in records}
        for case in cases:
            if case.case_id in done:
                continue
            records.append(self.run_case(case))
            done.add(case.case_id)
            if checkpoint is not None:
                checkpoint([item.copy(deep=True) for item in records])
        return build_real_report(
            records,
            cases=suite_cases,
            model=self.model,
            pricing=self.pricing,
        )

    def run_case(self, case: RealLLMCase) -> RealLLMRunRecord:
        initial = build_snapshot()
        started = perf_counter()
        with capture_llm_usage() as usage:
            try:
                result = self.pipeline_factory().run(
                    case.user_text,
                    initial,
                    default_actor_id=NIGHT,
                    use_llm_proposer=True,
                    use_narrative=True,
                    use_npc_agents=False,
                )
                raised_error = ""
            except Exception as exc:
                result = None
                raised_error = f"{type(exc).__name__}: {exc}"
        elapsed = round((perf_counter() - started) * 1000.0, 3)
        summary = usage.summary()
        if result is None:
            return RealLLMRunRecord(
                case_id=case.case_id,
                user_text=case.user_text,
                objective=case.objective,
                status="exception",
                objective_passed=False,
                state_committed=False,
                latency_ms=elapsed,
                llm_calls=usage.calls,
                llm_usage=summary,
                estimated_cost=estimate_cost(summary, self.pricing),
                cost_currency=(
                    self.pricing.currency
                    if self.pricing.configured
                    else None
                ),
                error=raised_error,
            )

        committed = result.event is not None and result.new_state is not None
        events = [result.event] if result.event is not None else []
        trajectory = (
            evaluate_trajectory(
                initial,
                events,
                expected_final_state=result.new_state,
            )
            if committed
            else None
        )
        narrative_grounded = None
        if result.narrative is not None and committed:
            narrative_grounded = check_narrative(
                result.narrative,
                result.event,
                result.new_state,
            ).valid
        evidence = (
            result.event.patch.causal_evidence
            if result.event is not None
            else None
        )
        causal_valid = bool(
            committed
            and result.action is not None
            and result.event.action_id == result.action.action_id
            and evidence is not None
            and evidence.action_id == result.action.action_id
            and evidence.actor_id == NIGHT
        )
        rejection_code = (
            result.intent_result.reason_code.value
            if result.intent_result is not None
            and result.intent_result.reason_code is not None
            else None
        )
        return RealLLMRunRecord(
            case_id=case.case_id,
            user_text=case.user_text,
            objective=case.objective,
            status=result.status,
            objective_passed=_objective_passed(
                case.objective,
                result,
                initial,
            ),
            state_committed=committed,
            action_type=(
                result.action.action_type.value
                if result.action is not None
                else None
            ),
            operation_types=(
                [
                    operation.op.value
                    for operation in result.event.patch.operations
                ]
                if result.event is not None
                else []
            ),
            rejection_code=rejection_code,
            event_id=result.event.event_id if result.event else None,
            final_version=(
                result.new_state.version if result.new_state else 0
            ),
            replay_consistent=bool(
                trajectory is not None and trajectory.passed
            ),
            causal_valid=causal_valid,
            narrative_present=result.narrative is not None,
            narrative_grounded=narrative_grounded,
            narrative_text=(
                result.narrative.narration
                if result.narrative is not None
                else ""
            ),
            dialogue_texts=(
                [
                    (
                        result.new_state.characters[
                            line.speaker_id
                        ].display_name
                        if line.speaker_id
                        in result.new_state.characters
                        else line.speaker_id
                    )
                    + f"：{line.line}"
                    for line in result.narrative.dialogues
                ]
                if result.narrative is not None
                else []
            ),
            latency_ms=elapsed,
            llm_calls=usage.calls,
            llm_usage=summary,
            estimated_cost=estimate_cost(summary, self.pricing),
            cost_currency=(
                self.pricing.currency
                if self.pricing.configured
                else None
            ),
            error=result.error or "",
        )


def build_real_report(
    records: Sequence[RealLLMRunRecord],
    *,
    cases: Sequence[RealLLMCase],
    model: str,
    pricing: PricingConfig,
) -> RealLLMReport:
    case_ids = [item.case_id for item in cases]
    record_ids = [item.case_id for item in records]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("real LLM suite contains duplicate case_id values")
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("real LLM report contains duplicate case records")
    unknown_records = sorted(set(record_ids) - set(case_ids))
    if unknown_records:
        raise ValueError(
            "real LLM report contains records outside the suite: "
            + ", ".join(unknown_records)
        )
    cases_by_id = {item.case_id: item for item in cases}
    mismatched_objectives = sorted(
        item.case_id
        for item in records
        if item.objective != cases_by_id[item.case_id].objective
    )
    if mismatched_objectives:
        raise ValueError(
            "real LLM report objective mismatch: "
            + ", ".join(mismatched_objectives)
        )

    run_count = len(records)
    committed = sum(item.state_committed for item in records)
    narratives = [item for item in records if item.narrative_present]
    grounded = sum(
        item.narrative_grounded is True for item in narratives
    )
    failures = Counter(
        (
            item.rejection_code
            or item.status
            or "unknown"
        )
        for item in records
        if not item.objective_passed
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
    complete = (
        len(cases) >= 20
        and {item.case_id for item in records}
        >= {item.case_id for item in cases}
        and run_count >= 20
    )
    objective_rate = safe_rate(
        sum(item.objective_passed for item in records),
        run_count,
    )
    replay_rate = safe_rate(
        sum(item.replay_consistent for item in records if item.state_committed),
        committed,
    )
    causal_rate = safe_rate(
        sum(item.causal_valid for item in records if item.state_committed),
        committed,
    )
    narrative_grounding = safe_rate(grounded, len(narratives))
    narrative_coverage = safe_rate(len(narratives), committed)
    return RealLLMReport(
        run_id=_real_run_id(cases, model),
        generated_at=datetime.now(timezone.utc).isoformat(),
        model=model,
        run_count=run_count,
        complete=complete,
        passed=bool(
            complete
            and objective_rate is not None
            and objective_rate >= 0.80
            and replay_rate == 1.0
            and causal_rate == 1.0
            and narrative_coverage == 1.0
            and narrative_grounding == 1.0
        ),
        state_commit_rate=safe_rate(committed, run_count),
        objective_success_rate=objective_rate,
        replay_consistency_rate=replay_rate,
        causal_valid_rate=causal_rate,
        narrative_coverage_rate=narrative_coverage,
        narrative_grounding_rate=narrative_grounding,
        failure_distribution=dict(sorted(failures.items())),
        total_latency=latency_stats(
            item.latency_ms for item in records
        ),
        llm_usage=usage,
        estimated_cost=estimate_cost(usage, pricing),
        cost_currency=pricing.currency if pricing.configured else None,
        records=list(records),
    )


def _objective_passed(objective: str, result, initial_state) -> bool:
    state = result.new_state
    if objective == "obtain_outer_robe":
        if state is None:
            return False
        item = state.items.get(OUTER_ROBE)
        return item is not None and item.owner_id == NIGHT
    if objective == "reach_ye_residence":
        if state is None:
            return False
        actor = state.characters.get(NIGHT)
        return actor is not None and actor.location_id == "loc_yefu"
    if objective == "command_without_forced_transfer":
        original_owner = initial_state.items[OUTER_ROBE].owner_id
        current_owner = (
            state.items[OUTER_ROBE].owner_id
            if state is not None
            else original_owner
        )
        return bool(
            result.status == "committed"
            and result.action is not None
            and result.action.action_type.value == "speak"
            and current_owner == original_owner
        )
    rejection_code = (
        result.intent_result.reason_code.value
        if result.intent_result is not None
        and result.intent_result.reason_code is not None
        else None
    )
    if objective == "reject_forbidden_concept":
        return bool(
            result.status == "rejected"
            and result.event is None
            and rejection_code == "WORLD_CONCEPT_UNAVAILABLE"
        )
    if objective == "reject_unknown_entity":
        return bool(
            result.status == "rejected"
            and result.event is None
            and rejection_code == "ENTITY_NOT_FOUND"
        )
    return False


def _real_run_id(cases: Sequence[RealLLMCase], model: str) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {
                "model": model,
                "cases": [item.dict() for item in cases],
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"real_llm_{digest}"


def _write_report(path: Path, report: RealLLMReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def render_real_markdown(report: RealLLMReport) -> str:
    usage = report.llm_usage
    lines = [
        "# NovelSim 真实 LLM 固定场景回归",
        "",
        f"- Run ID：`{report.run_id}`",
        f"- 模型：`{report.model}`",
        f"- 运行：{report.run_count}/{report.required_run_count}",
        f"- 完整：{'是' if report.complete else '否'}",
        f"- 门禁：{'PASS' if report.passed else 'FAIL'}",
        "",
        "## 核心指标",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 状态提交率 | {_format_rate(report.state_commit_rate)} |",
        f"| 目标成功率 | {_format_rate(report.objective_success_rate)} |",
        f"| 事件回放一致率 | {_format_rate(report.replay_consistency_rate)} |",
        f"| 因果证据有效率 | {_format_rate(report.causal_valid_rate)} |",
        f"| 叙事覆盖率 | {_format_rate(report.narrative_coverage_rate)} |",
        f"| 叙事事件依据率 | {_format_rate(report.narrative_grounding_rate)} |",
        "",
        "## 延迟与模型用量",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 总回合延迟 P50 | {report.total_latency.p50_ms:.3f} ms |",
        f"| 总回合延迟 P95 | {report.total_latency.p95_ms:.3f} ms |",
        f"| 模型调用 | {usage.call_count} |",
        f"| 失败模型调用 | {usage.failed_call_count} |",
        f"| 输入 Token | {usage.prompt_tokens} |",
        f"| 输出 Token | {usage.completion_tokens} |",
        f"| 总 Token | {usage.total_tokens} |",
        "| 估算成本 | "
        + (
            f"{report.estimated_cost:.8f} {report.cost_currency}"
            if report.estimated_cost is not None
            else "未配置单价"
        )
        + " |",
        "",
        "## 逐局结果",
        "",
        "| 案例 | 状态 | 目标 | 回放 | 因果 | 叙事依据 | Token | 延迟 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report.records:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{item.case_id}`",
                    item.status,
                    _yes_no(item.objective_passed),
                    _yes_no(item.replay_consistent),
                    _yes_no(item.causal_valid),
                    (
                        _yes_no(item.narrative_grounded)
                        if item.narrative_grounded is not None
                        else "N/A"
                    ),
                    str(item.llm_usage.total_tokens),
                    f"{item.latency_ms:.3f} ms",
                ]
            )
            + " |"
        )

    failed = [item for item in report.records if not item.objective_passed]
    lines.extend(["", "## 失败样本", ""])
    if not failed:
        lines.append("无。")
    else:
        for item in failed:
            reason = item.rejection_code or item.error or item.status
            lines.append(
                f"- `{item.case_id}`：{reason}；输入：{item.user_text}"
            )
    lines.extend(
        [
            "",
            "> 门禁要求：至少 20 局、目标成功率不低于 80%，且所有已提交事件"
            "的回放、因果证据、叙事覆盖和叙事事件依据均为 100%。",
            "",
        ]
    )
    return "\n".join(lines)


def _format_rate(value: Optional[float]) -> str:
    return f"{value:.2%}" if value is not None else "N/A"


def _yes_no(value: bool) -> str:
    return "是" if value else "否"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_REAL_CASES)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--input-per-million", type=float, default=0.0)
    parser.add_argument(
        "--cached-input-per-million",
        type=float,
        default=0.0,
    )
    parser.add_argument("--output-per-million", type=float, default=0.0)
    args = parser.parse_args(argv)
    suite_cases = load_real_cases(args.cases)
    cases = list(suite_cases)
    if args.limit is not None:
        if args.limit < 1:
            raise SystemExit("--limit must be positive")
        cases = cases[: args.limit]
    pricing = PricingConfig(
        currency=args.currency,
        input_per_million=args.input_per_million,
        cached_input_per_million=args.cached_input_per_million,
        output_per_million=args.output_per_million,
    )
    existing: List[RealLLMRunRecord] = []
    if args.resume and args.output.exists():
        previous = RealLLMReport.parse_obj(
            json.loads(args.output.read_text(encoding="utf-8"))
        )
        expected_run_id = _real_run_id(suite_cases, runner_model := get_llm_config().model)
        if previous.run_id != expected_run_id or previous.model != runner_model:
            raise SystemExit(
                "--resume 报告与当前案例集或模型不匹配；请换输出路径"
            )
        existing = previous.records
    runner = RealLLMRunner(
        pricing=pricing,
        model=(
            runner_model
            if args.resume and args.output.exists()
            else None
        ),
    )

    def checkpoint(records):
        _write_report(
            args.output,
            build_real_report(
                records,
                cases=suite_cases,
                model=runner.model,
                pricing=pricing,
            ),
        )

    report = runner.run(
        cases,
        existing_records=existing,
        checkpoint=checkpoint,
        report_cases=suite_cases,
    )
    _write_report(args.output, report)
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(
            render_real_markdown(report),
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "run_id": report.run_id,
                "run_count": report.run_count,
                "complete": report.complete,
                "passed": report.passed,
                "objective_success_rate": report.objective_success_rate,
                "narrative_grounding_rate": (
                    report.narrative_grounding_rate
                ),
                "total_tokens": report.llm_usage.total_tokens,
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
