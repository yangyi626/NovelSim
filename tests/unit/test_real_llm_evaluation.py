from pathlib import Path

import pytest

from engine.llm_telemetry import LLMUsageSummary
from evaluation.models import PricingConfig
from evaluation.real_runner import (
    RealLLMRunRecord,
    RealLLMRunner,
    build_real_report,
    load_real_cases,
    render_real_markdown,
)


def _record(
    case_id: str,
    *,
    objective: str = "obtain_outer_robe",
    narrative: bool = True,
) -> RealLLMRunRecord:
    return RealLLMRunRecord(
        case_id=case_id,
        user_text="固定输入",
        objective=objective,
        status="committed",
        objective_passed=True,
        state_committed=True,
        replay_consistent=True,
        causal_valid=True,
        narrative_present=narrative,
        narrative_grounded=True if narrative else None,
        latency_ms=10.0,
        llm_usage=LLMUsageSummary(call_count=3, total_tokens=30),
    )


def test_real_manifest_has_twenty_unique_fixed_cases():
    cases = load_real_cases()

    assert len(cases) == 20
    assert len({case.case_id for case in cases}) == 20
    assert {case.objective for case in cases} == {
        "obtain_outer_robe",
        "reach_ye_residence",
        "command_without_forced_transfer",
        "reject_forbidden_concept",
        "reject_unknown_entity",
    }


def test_complete_report_passes_all_acceptance_gates():
    cases = load_real_cases()
    records = [
        _record(case.case_id, objective=case.objective)
        for case in cases
    ]

    report = build_real_report(
        records,
        cases=cases,
        model="fake-model",
        pricing=PricingConfig(),
    )

    assert report.complete
    assert report.passed
    assert report.objective_success_rate == 1.0
    assert report.narrative_coverage_rate == 1.0
    assert "20/20" in render_real_markdown(report)


def test_report_fails_when_a_committed_event_has_no_narrative():
    cases = load_real_cases()
    records = [
        _record(case.case_id, objective=case.objective)
        for case in cases
    ]
    records[0] = _record(
        cases[0].case_id,
        objective=cases[0].objective,
        narrative=False,
    )

    report = build_real_report(
        records,
        cases=cases,
        model="fake-model",
        pricing=PricingConfig(),
    )

    assert report.complete
    assert not report.passed
    assert report.narrative_coverage_rate == pytest.approx(0.95)


def test_partial_run_keeps_full_suite_identity_and_checkpoints():
    cases = load_real_cases()
    checkpoints = []

    class RaisingPipeline:
        def run(self, *args, **kwargs):
            raise RuntimeError("synthetic failure")

    runner = RealLLMRunner(
        pipeline_factory=RaisingPipeline,
        model="fake-model",
    )
    report = runner.run(
        cases[:1],
        report_cases=cases,
        checkpoint=lambda records: checkpoints.append(records),
    )

    assert report.run_count == 1
    assert not report.complete
    assert report.records[0].status == "exception"
    assert "synthetic failure" in report.records[0].error
    assert len(checkpoints) == 1


def test_report_rejects_duplicate_or_foreign_records():
    cases = load_real_cases()
    duplicate = [_record(cases[0].case_id), _record(cases[0].case_id)]
    with pytest.raises(ValueError, match="duplicate"):
        build_real_report(
            duplicate,
            cases=cases,
            model="fake-model",
            pricing=PricingConfig(),
        )

    foreign = [_record("not_in_suite")]
    with pytest.raises(ValueError, match="outside the suite"):
        build_real_report(
            foreign,
            cases=cases,
            model="fake-model",
            pricing=PricingConfig(),
        )
