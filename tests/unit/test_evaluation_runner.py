import asyncio
import json

from evaluation import (
    EvaluationRunner,
    load_benchmark_cases,
    run_ablation_suite,
)
from evaluation.__main__ import main
from evaluation.report import render_markdown
from evaluation.runner import DEFAULT_CASES


def test_fixed_case_manifest_covers_modes_routes_and_rejections():
    cases = load_benchmark_cases(DEFAULT_CASES)

    assert len(cases) == 9
    assert len({item.case_id for item in cases}) == 9
    assert {item.mode.value for item in cases} == {"free", "script"}
    assert {
        item.player_route
        for item in cases
        if item.player_route is not None
    } == {"destroy_letter", "intercept_letter", "expose_truth"}
    assert {
        code
        for item in cases
        for code in item.expected.rejection_codes
    } == {
        "precondition_failed",
        "target_not_found",
        "cognitive_boundary",
    }


def test_deterministic_runner_computes_objective_safety_and_trace_metrics():
    report = asyncio.run(
        EvaluationRunner().run(
            load_benchmark_cases(),
            include_ablations=False,
        )
    )

    assert report.passed is True
    assert report.deterministic is True
    assert report.case_count == report.run_count == 9
    assert report.metrics.benchmark_pass_rate == 1.0
    assert report.metrics.core_event_completion_rate == 1.0
    assert report.metrics.tool_success_rate == 1.0
    assert report.metrics.expected_rejection_rate == 1.0
    assert report.metrics.replay_consistency_rate == 1.0
    assert report.metrics.propagation_accuracy == 1.0
    assert report.metrics.evidence_chain_completeness == 1.0
    assert report.metrics.illegal_patch_commit_count == 0
    assert report.metrics.unknown_entity_accept_count == 0
    assert report.metrics.causal_violation_count == 0
    assert report.metrics.knowledge_leak_count == 0
    assert report.metrics.llm_usage.call_count == 0
    assert report.metrics.estimated_cost is None
    assert report.mode_comparisons[0].same_final_state_rate == 1.0
    assert report.mode_comparisons[0].same_tool_chain_rate == 1.0
    assert all(record.trace_ids for record in report.records)
    assert all(record.stage_latency_ms for record in report.records)


def test_repeated_runs_keep_stable_ids_state_and_pair_structure():
    cases = load_benchmark_cases()
    first = asyncio.run(
        EvaluationRunner().run(
            cases,
            repetitions=2,
            include_ablations=False,
        )
    )
    second = asyncio.run(
        EvaluationRunner().run(
            cases,
            repetitions=2,
            include_ablations=False,
        )
    )

    assert first.run_id == second.run_id
    assert first.run_count == second.run_count == 18
    assert first.mode_comparisons[0].sample_pairs == 2
    assert [
        (item.case_id, item.repetition, item.final_state_hash)
        for item in first.records
    ] == [
        (item.case_id, item.repetition, item.final_state_hash)
        for item in second.records
    ]


def test_guard_and_memory_ablations_are_isolated_and_show_expected_delta():
    report = asyncio.run(run_ablation_suite())
    accepted = {
        item.profile.profile_id: item.violation_accept_count
        for item in report.guard_profiles
    }
    memory = {
        item.variant_id: item
        for item in report.memory_variants
    }

    assert report.passed is True
    assert report.isolated is True
    assert report.authoritative_store_used is False
    assert report.production_probe_failures == []
    assert accepted == {"G0": 6, "G1": 4, "G2": 1, "G3": 0}
    assert memory["no_memory"].hit_rate == 0.0
    assert memory["no_memory"].mrr == 0.0
    assert memory["sqlite_fts5"].query_count == 50
    assert memory["sqlite_fts5"].hit_rate > 0.0
    assert memory["sqlite_fts5"].mrr > 0.0


def test_markdown_and_cli_write_auditable_reports(tmp_path):
    report = asyncio.run(
        EvaluationRunner().run(
            load_benchmark_cases(),
            include_ablations=True,
        )
    )
    markdown = render_markdown(report)
    assert "Free / Script 配对基线" in markdown
    assert "G0：仅提示词" in markdown
    assert "G3：完整闭环" in markdown
    assert "真实 LLM" in markdown
    assert "未测" in markdown

    json_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"
    exit_code = main(
        [
            "--no-ablations",
            "--json",
            str(json_path),
            "--markdown",
            str(markdown_path),
        ]
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["passed"] is True
    assert payload["ablations"] is None
    assert markdown_path.read_text(encoding="utf-8").startswith(
        "# NovelSim 结构化评测"
    )
