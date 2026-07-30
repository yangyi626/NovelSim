"""评测报告的 JSON 与 Markdown 渲染。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

from .models import EvaluationReport


def render_markdown(report: EvaluationReport) -> str:
    metrics = report.metrics
    lines = [
        f"# NovelSim 结构化评测：{report.suite_id}",
        "",
        f"- Run ID：`{report.run_id}`",
        f"- 生成时间：`{report.generated_at}`",
        f"- 固定案例：{report.case_count}",
        f"- 实际运行：{report.run_count}",
        f"- 确定性运行：{'是' if report.deterministic else '否'}",
        f"- 套件门禁：{'PASS' if report.passed else 'FAIL'}",
        "",
        "## 核心指标",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        _row("案例期望通过率", _rate(metrics.benchmark_pass_rate)),
        _row("目标完成率（全部路线）", _rate(
            metrics.objective_completion_rate
        )),
        _row("核心事件完成率", _rate(
            metrics.core_event_completion_rate
        )),
        _row("工具执行成功率", _rate(metrics.tool_success_rate)),
        _row("规则预期拒绝率", _rate(
            metrics.expected_rejection_rate
        )),
        _row("事件回放一致率", _rate(
            metrics.replay_consistency_rate
        )),
        _row("信息传播准确率", _rate(
            metrics.propagation_accuracy
        )),
        _row("证据链完整率", _rate(
            metrics.evidence_chain_completeness
        )),
        _row("结构化角色一致率", _rate(
            metrics.structural_character_consistency
        )),
        _row("联盟形成率", _rate(
            metrics.alliance_formation_rate
        )),
        "",
        "## 安全与失败",
        "",
        "| 指标 | 数量 |",
        "|---|---:|",
        _row("非法 Patch 提交", metrics.illegal_patch_commit_count),
        _row("未知实体被接受", metrics.unknown_entity_accept_count),
        _row("因果越权提交", metrics.causal_violation_count),
        _row("认知泄漏提交", metrics.knowledge_leak_count),
        _row("非预期拒绝", metrics.unexpected_rejection_count),
        _row("无效循环", metrics.invalid_loop_count),
        "",
        "失败类型："
        + (
            "、".join(
                f"`{key}`={value}"
                for key, value in metrics.failure_distribution.items()
            )
            if metrics.failure_distribution
            else "无"
        ),
        "",
        "## 延迟与模型用量",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        _row("运行延迟 P50", f"{metrics.total_latency.p50_ms:.3f} ms"),
        _row("运行延迟 P95", f"{metrics.total_latency.p95_ms:.3f} ms"),
        _row("模型调用", metrics.llm_usage.call_count),
        _row("失败模型调用", metrics.llm_usage.failed_call_count),
        _row("输入 Token", metrics.llm_usage.prompt_tokens),
        _row("输出 Token", metrics.llm_usage.completion_tokens),
        _row("总 Token", metrics.llm_usage.total_tokens),
        _row(
            "估算成本",
            (
                f"{metrics.estimated_cost:.8f} "
                f"{metrics.cost_currency}"
                if metrics.estimated_cost is not None
                else "未配置单价"
            ),
        ),
        "",
        "## 案例明细",
        "",
        "| Case | Mode | 结局/状态 | 工具 | 版本 | 结果 |",
        "|---|---|---|---:|---:|---|",
    ]
    for record in report.records:
        ending = record.ending_id or record.status.value
        lines.append(
            "| `{case}` | {mode} | `{ending}` | {tools} | "
            "{initial}→{final} | {result} |".format(
                case=record.case_id,
                mode=record.mode.value,
                ending=ending,
                tools=record.tool_call_count,
                initial=record.initial_version,
                final=record.final_version,
                result="PASS" if record.passed else "FAIL",
            )
        )
        if record.assertion_failures:
            lines.append(
                "|  |  | "
                + "<br>".join(
                    _escape_table(item)
                    for item in record.assertion_failures
                )
                + " |  |  |  |"
            )

    if report.mode_comparisons:
        lines.extend(
            [
                "",
                "## Free / Script 配对基线",
                "",
                "| 比较 | 配对数 | 终态相同 | 工具链相同 | "
                "Free P50 | Script P50 |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for item in report.mode_comparisons:
            lines.append(
                "| `{}` | {} | {} | {} | {:.3f} ms | "
                "{:.3f} ms |".format(
                    item.comparison_id,
                    item.sample_pairs,
                    _rate(item.same_final_state_rate),
                    _rate(item.same_tool_chain_rate),
                    item.free_p50_latency_ms,
                    item.script_p50_latency_ms,
                )
            )

    if report.ablations is not None:
        lines.extend(
            [
                "",
                "## 消融结果",
                "",
                "所有禁用门禁的结果均为隔离反事实，不写入权威存档。",
                "",
                "| 世界门禁组 | 启用门禁数 | 违规拒绝率 | 违规被接受 |",
                "|---|---:|---:|---:|",
            ]
        )
        for item in report.ablations.guard_profiles:
            lines.append(
                "| {profile}：{label} | {gates} | {rate} | "
                "{accepted} |".format(
                    profile=item.profile.profile_id,
                    label=item.profile.label,
                    gates=len(item.profile.enabled_gates),
                    rate=_rate(item.rejection_rate),
                    accepted=item.violation_accept_count,
                )
            )
        lines.extend(
            [
                "",
                "| 记忆组 | Hit@4 | MRR | nDCG@4 | P95 |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for item in report.ablations.memory_variants:
            lines.append(
                "| `{}` | {:.2%} | {:.3f} | {:.3f} | "
                "{:.3f} ms |".format(
                    item.variant_id,
                    item.hit_rate,
                    item.mrr,
                    item.ndcg,
                    item.p95_latency_ms,
                )
            )

    lines.extend(
        [
            "",
            "## 验收检查",
            "",
            "| 检查 | 实测 | 门槛 | 本套件要求 | 结果 |",
            "|---|---:|---:|---|---|",
        ]
    )
    for check in report.acceptance_checks:
        lines.append(
            "| `{}` | {} | {} | {} | {} |".format(
                check.check_id,
                _escape_table(check.actual),
                _escape_table(check.threshold),
                "是" if check.required_for_suite else "否",
                (
                    "未测"
                    if not check.measured
                    else ("PASS" if check.passed else "FAIL")
                ),
            )
        )
    lines.extend(
        [
            "",
            "> 未测项目不会被确定性套件伪装为通过；真实 LLM 局数与叙事"
            "依据率由后续真实模型/Pairwise 套件补充。",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(
    report: EvaluationReport,
    *,
    json_path: Optional[Path] = None,
    markdown_path: Optional[Path] = None,
) -> None:
    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(
                report.dict(),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    if markdown_path is not None:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(
            render_markdown(report),
            encoding="utf-8",
        )


def _row(label: str, value) -> str:
    return f"| {label} | {value} |"


def _rate(value: Optional[float]) -> str:
    return "未测" if value is None else f"{value:.2%}"


def _escape_table(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")
