"""固定秘密信件场景的可复现客观评测运行器。"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from engine import (
    CORE_TOOL_PERMISSIONS,
    SceneConfig,
    SceneController,
    SceneMode,
    ScriptBeat,
    ToolCall,
    apply_patch,
    capture_llm_usage,
    create_core_tool_registry,
    evaluate_trajectory,
)
from engine.event import state_hash
from engine.information_propagation import get_belief
from world_schema import OperationKind, WorldEvent, WorldState

from examples.secret_letter import (
    ALLY,
    GATEHOUSE,
    GUARD,
    PLAYER,
    STEWARD,
    build_script_beats,
    build_snapshot,
    evaluate_ending,
    next_autonomous_call,
    player_intervention_calls,
)

from .metrics import (
    aggregate_metrics,
    build_acceptance_checks,
    estimate_cost,
    latency_stats,
    safe_rate,
)
from .models import (
    AcceptanceCheck,
    BenchmarkCase,
    CaseRunRecord,
    EvaluationReport,
    ModeComparison,
    PricingConfig,
)
from .ablation import run_ablation_suite


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = (
    PROJECT_ROOT / "evaluation" / "benchmark_cases.jsonl"
)
SUITE_ID = "novelsim-secret-letter-objective-v1"


def load_benchmark_cases(path: Path = DEFAULT_CASES) -> List[BenchmarkCase]:
    """读取 JSONL；空行和 ``#`` 注释不会进入样本数。"""

    cases: List[BenchmarkCase] = []
    seen = set()
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        try:
            case = BenchmarkCase.parse_obj(json.loads(text))
        except Exception as exc:
            raise ValueError(
                f"invalid benchmark case at {path}:{line_number}: {exc}"
            ) from exc
        if case.case_id in seen:
            raise ValueError(f"duplicate benchmark case_id: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError(f"benchmark has no cases: {path}")
    return cases


class EvaluationRunner:
    """每局使用全新内存快照，永不连接生产存档。"""

    def __init__(
        self,
        *,
        pricing: Optional[PricingConfig] = None,
    ) -> None:
        self.pricing = pricing or PricingConfig()

    async def run(
        self,
        cases: Sequence[BenchmarkCase],
        *,
        repetitions: Optional[int] = None,
        include_ablations: bool = True,
    ) -> EvaluationReport:
        if not cases:
            raise ValueError("at least one benchmark case is required")
        records: List[CaseRunRecord] = []
        for case in cases:
            repeat_count = repetitions or case.repetitions
            if repeat_count < 1 or repeat_count > 100:
                raise ValueError("repetitions must be between 1 and 100")
            for repetition in range(1, repeat_count + 1):
                records.append(
                    await self.run_case(case, repetition=repetition)
                )

        metrics = aggregate_metrics(records, self.pricing)
        checks = build_acceptance_checks(metrics)
        mode_comparisons = _mode_comparisons(records)
        for comparison in mode_comparisons:
            checks.append(
                AcceptanceCheck(
                    check_id=(
                        f"{comparison.comparison_id}_authoritative_equivalence"
                    ),
                    passed=(
                        comparison.same_final_state_rate == 1.0
                        and comparison.same_tool_chain_rate == 1.0
                    ),
                    measured=comparison.sample_pairs > 0,
                    actual=(
                        f"state={comparison.same_final_state_rate:.2%}, "
                        f"chain={comparison.same_tool_chain_rate:.2%}"
                        if comparison.sample_pairs > 0
                        else "not measured"
                    ),
                    threshold="state=100%, chain=100%",
                    evidence="Free/Script 固定种子逐重复配对",
                )
            )
        ablations = (
            await run_ablation_suite()
            if include_ablations
            else None
        )
        if ablations is not None:
            full = next(
                item
                for item in ablations.guard_profiles
                if item.profile.profile_id == "G3"
            )
            enabled_memory = next(
                item
                for item in ablations.memory_variants
                if item.memory_enabled
            )
            disabled_memory = next(
                item
                for item in ablations.memory_variants
                if not item.memory_enabled
            )
            checks.extend(
                [
                    AcceptanceCheck(
                        check_id="ablation_isolation",
                        passed=(
                            ablations.isolated
                            and not ablations.authoritative_store_used
                        ),
                        actual=(
                            "temporary/in-memory"
                            if ablations.isolated
                            else "not isolated"
                        ),
                        threshold="no authoritative store",
                        evidence="AblationReport authoritative_store_used",
                    ),
                    AcceptanceCheck(
                        check_id="full_guard_violation_accepts",
                        passed=full.violation_accept_count == 0,
                        actual=str(full.violation_accept_count),
                        threshold="0",
                        evidence="G3 production validator probes",
                    ),
                    AcceptanceCheck(
                        check_id="memory_ablation_effect",
                        passed=(
                            enabled_memory.hit_rate
                            > disabled_memory.hit_rate
                            and enabled_memory.mrr
                            > disabled_memory.mrr
                        ),
                        actual=(
                            f"Hit {disabled_memory.hit_rate:.3f}"
                            f"->{enabled_memory.hit_rate:.3f}, "
                            f"MRR {disabled_memory.mrr:.3f}"
                            f"->{enabled_memory.mrr:.3f}"
                        ),
                        threshold="enabled > no_memory",
                        evidence="50-query scoped Chinese memory benchmark",
                    ),
                ]
            )
        run_id = _stable_run_id(
            cases,
            repetitions,
            self.pricing,
            include_ablations,
        )
        required_checks = [
            item for item in checks if item.required_for_suite
        ]
        return EvaluationReport(
            suite_id=SUITE_ID,
            run_id=run_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
            deterministic=metrics.llm_usage.call_count == 0,
            random_seed=_suite_seed(cases),
            pricing=self.pricing,
            case_count=len(cases),
            run_count=len(records),
            records=records,
            metrics=metrics,
            mode_comparisons=mode_comparisons,
            ablations=ablations,
            acceptance_checks=checks,
            passed=(
                all(item.passed for item in records)
                and all(item.passed for item in required_checks)
                and (ablations is None or ablations.passed)
            ),
        )

    async def run_case(
        self,
        case: BenchmarkCase,
        *,
        repetition: int = 1,
    ) -> CaseRunRecord:
        if case.scenario_id != "secret_letter":
            raise ValueError(
                f"unsupported scenario_id: {case.scenario_id}"
            )
        initial = build_snapshot()
        config = SceneConfig(
            scene_id="scene_secret_letter",
            mode=case.mode,
            location_id=GATEHOUSE,
            participant_ids=[PLAYER, GUARD, STEWARD, ALLY],
            objective="阻止密信中的阴谋并形成可信联盟",
            max_turns=case.max_turns,
            random_seed=case.random_seed,
            script_beats=(
                build_script_beats()
                if case.mode == SceneMode.script
                else []
            ),
        )
        initial_calls = _initial_calls(case)
        controller = SceneController(
            create_core_tool_registry(),
            permissions=CORE_TOOL_PERMISSIONS,
        )
        started = perf_counter()
        with capture_llm_usage() as usage:
            run = await controller.run(
                initial,
                config,
                free_selector=(
                    next_autonomous_call
                    if case.mode == SceneMode.free
                    else None
                ),
                ending_evaluator=evaluate_ending,
                initial_calls=initial_calls,
            )
        latency_ms = round((perf_counter() - started) * 1000.0, 3)
        usage_summary = usage.summary()
        events = [
            outcome.event
            for outcome in run.outcomes
            if outcome.event is not None
        ]
        trajectory = evaluate_trajectory(
            initial,
            events,
            expected_final_state=run.state,
        )
        failure_codes = [
            outcome.result.failure.code.value
            for outcome in run.outcomes
            if outcome.result.failure is not None
        ]
        observed = Counter(failure_codes)
        expected = Counter(case.expected.rejection_codes)
        matched = sum((observed & expected).values())
        unexpected = sum((observed - expected).values())
        causal_violations = _causal_violations(run.outcomes)
        knowledge_leaks = _knowledge_leaks(initial, events)
        valid_propagations, complete_evidence = (
            _information_integrity(run.state)
        )
        structural_violations = sum(
            1
            for item in trajectory.violations
            if item.code in {"unknown_character", "dead_actor"}
        )
        assertion_failures = _assert_case(
            case,
            run.summary,
            failure_codes,
            len(run.state.propagation_history),
            len(run.state.alliances),
        )
        if not trajectory.passed:
            assertion_failures.append(
                "trajectory: "
                + ", ".join(
                    sorted({item.code for item in trajectory.violations})
                )
            )
        if causal_violations:
            assertion_failures.append(
                f"causal_violation_count: {causal_violations}"
            )
        if knowledge_leaks:
            assertion_failures.append(
                f"knowledge_leak_count: {knowledge_leaks}"
            )
        stage_latency = _stage_latency(run.outcomes)
        record = CaseRunRecord(
            case_id=case.case_id,
            repetition=repetition,
            scenario_id=case.scenario_id,
            mode=case.mode,
            tags=list(case.tags),
            random_seed=case.random_seed,
            passed=not assertion_failures,
            assertion_failures=assertion_failures,
            status=run.summary.status,
            ending_id=run.summary.ending_id,
            objective_satisfied=run.summary.objective_satisfied,
            turns_used=run.summary.turns_used,
            initial_version=initial.version,
            final_version=run.state.version,
            initial_state_hash=state_hash(initial),
            final_state_hash=state_hash(run.state),
            tool_sequence=list(run.summary.tool_sequence),
            tool_call_count=len(run.outcomes),
            successful_tool_calls=sum(
                outcome.result.success for outcome in run.outcomes
            ),
            expected_rejection_count=sum(expected.values()),
            matched_expected_rejection_count=matched,
            unexpected_rejection_count=unexpected,
            failure_codes=failure_codes,
            failure_distribution=dict(sorted(observed.items())),
            trace_ids=[
                outcome.trace.trace_id for outcome in run.outcomes
            ],
            event_ids=[event.event_id for event in events],
            replay_consistent=trajectory.passed,
            illegal_patch_commit_count=(
                sum(
                    outcome.event is not None
                    and not outcome.result.success
                    for outcome in run.outcomes
                )
                + sum(
                    item.code in {"invalid_patch", "state_apply"}
                    for item in trajectory.violations
                )
            ),
            unknown_entity_accept_count=sum(
                item.code in {"unknown_character", "unknown_target"}
                for item in trajectory.violations
            ),
            causal_violation_count=causal_violations,
            knowledge_leak_count=knowledge_leaks,
            propagation_count=len(run.state.propagation_history),
            valid_propagation_count=valid_propagations,
            evidence_chain_sample_count=len(
                run.state.propagation_history
            ),
            complete_evidence_chain_count=complete_evidence,
            alliance_count=len(run.state.alliances),
            invalid_loop_count=_invalid_loops(run.outcomes),
            structural_character_violation_count=structural_violations,
            latency_ms=latency_ms,
            stage_latency_ms=stage_latency,
            llm_calls=usage.calls,
            llm_usage=usage_summary,
            estimated_cost=estimate_cost(
                usage_summary,
                self.pricing,
            ),
            cost_currency=(
                self.pricing.currency
                if self.pricing.configured
                else None
            ),
        )
        return record


def _initial_calls(case: BenchmarkCase) -> List[ToolCall]:
    if case.player_route:
        return player_intervention_calls(case.player_route)
    return [
        ToolCall(
            call_id=(
                spec.call_id
                or f"evaluation_{case.case_id}_{index:02d}"
            ),
            actor_id=spec.actor_id,
            tool_name=spec.tool_name,
            arguments=dict(spec.arguments),
        )
        for index, spec in enumerate(case.initial_calls, start=1)
    ]


def _assert_case(
    case: BenchmarkCase,
    summary,
    failure_codes: List[str],
    propagation_count: int,
    alliance_count: int,
) -> List[str]:
    expected = case.expected
    failures: List[str] = []
    comparisons = [
        ("status", summary.status, expected.status),
        ("ending_id", summary.ending_id, expected.ending_id),
        (
            "objective_satisfied",
            summary.objective_satisfied,
            expected.objective_satisfied,
        ),
        (
            "tool_sequence",
            list(summary.tool_sequence),
            expected.tool_sequence,
        ),
        ("rejection_codes", failure_codes, expected.rejection_codes),
        ("final_version", summary.final_version, expected.final_version),
        ("propagation_count", propagation_count, expected.propagation_count),
        ("alliance_count", alliance_count, expected.alliance_count),
    ]
    for label, actual, wanted in comparisons:
        if actual != wanted:
            failures.append(
                f"{label}: expected {wanted!r}, got {actual!r}"
            )
    return failures


def _causal_violations(outcomes) -> int:
    violations = 0
    for outcome in outcomes:
        if not outcome.result.success:
            if outcome.event is not None:
                violations += 1
            continue
        event = outcome.event
        evidence = event.patch.causal_evidence if event else None
        call = outcome.execution.active_call
        if (
            event is None
            or evidence is None
            or event.action_id != call.call_id
            or evidence.action_id != call.call_id
            or evidence.tool_call_id != call.call_id
            or evidence.tool_name != call.tool_name
            or evidence.actor_id != call.actor_id
            or evidence.authority != "tool_registry"
        ):
            violations += 1
    return violations


def _knowledge_leaks(
    initial: WorldState,
    events: Sequence[WorldEvent],
) -> int:
    state = initial.copy(deep=True)
    leaks = 0
    for event in events:
        for operation in event.patch.operations:
            if operation.op != OperationKind.record_propagation:
                continue
            payload = operation.value or {}
            source_id = payload.get("source_character_id")
            fact_id = payload.get("fact_id")
            if (
                not source_id
                or not fact_id
                or get_belief(state, source_id, fact_id) is None
            ):
                leaks += 1
        state = apply_patch(state, event.patch)
        state.version = event.new_version
    return leaks


def _information_integrity(state: WorldState) -> Tuple[int, int]:
    valid = 0
    complete = 0
    evidence_ids = set(state.belief_evidence)
    for record in state.propagation_history:
        evidence = state.belief_evidence.get(record.evidence_id)
        belief = get_belief(
            state,
            record.target_character_id,
            record.fact_id,
        )
        record_valid = (
            record.source_character_id in state.characters
            and record.target_character_id in state.characters
            and record.fact_id in state.facts
            and evidence is not None
            and evidence.fact_id == record.fact_id
            and evidence.holder_id == record.target_character_id
            and evidence.source_character_id
            == record.source_character_id
            and belief is not None
            and belief.source_character_id
            == record.source_character_id
            and abs(
                belief.confidence - record.resulting_confidence
            )
            < 0.00011
        )
        if record_valid:
            valid += 1
        if (
            record_valid
            and evidence is not None
            and all(
                parent_id in evidence_ids
                for parent_id in evidence.parent_evidence_ids
            )
        ):
            complete += 1
    return valid, complete


def _stage_latency(outcomes) -> Dict[str, float]:
    totals: Dict[str, float] = {}
    for outcome in outcomes:
        for span in outcome.trace.spans:
            key = span.stage.value
            totals[key] = totals.get(key, 0.0) + span.duration_ms
    return {
        key: round(value, 3)
        for key, value in sorted(totals.items())
    }


def _invalid_loops(outcomes) -> int:
    signatures: List[str] = []
    for outcome in outcomes:
        call = outcome.execution.active_call
        signatures.append(
            json.dumps(
                [
                    call.actor_id,
                    call.tool_name,
                    call.arguments,
                    outcome.new_state.version,
                ],
                sort_keys=True,
                ensure_ascii=False,
            )
        )
    loops = 0
    for index in range(2, len(signatures)):
        if (
            signatures[index]
            == signatures[index - 1]
            == signatures[index - 2]
        ):
            loops += 1
    return loops


def _stable_run_id(
    cases: Sequence[BenchmarkCase],
    repetitions: Optional[int],
    pricing: PricingConfig,
    include_ablations: bool,
) -> str:
    payload = {
        "suite_id": SUITE_ID,
        "cases": [case.dict() for case in cases],
        "repetitions": repetitions,
        "pricing": pricing.dict(),
        "include_ablations": include_ablations,
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"evaluation_{digest}"


def _suite_seed(cases: Iterable[BenchmarkCase]) -> int:
    seeds = {case.random_seed for case in cases}
    return next(iter(seeds)) if len(seeds) == 1 else 0


def _mode_comparisons(
    records: Sequence[CaseRunRecord],
) -> List[ModeComparison]:
    free = {
        item.repetition: item
        for item in records
        if item.case_id == "free_defenders_allied"
    }
    script = {
        item.repetition: item
        for item in records
        if item.case_id == "script_defenders_allied"
    }
    repetitions = sorted(set(free) & set(script))
    if not repetitions:
        return []
    free_records = [free[index] for index in repetitions]
    script_records = [script[index] for index in repetitions]
    free_latency = latency_stats(
        item.latency_ms for item in free_records
    )
    script_latency = latency_stats(
        item.latency_ms for item in script_records
    )
    return [
        ModeComparison(
            comparison_id="free_vs_script_canonical",
            sample_pairs=len(repetitions),
            free_pass_rate=safe_rate(
                sum(item.passed for item in free_records),
                len(free_records),
            ),
            script_pass_rate=safe_rate(
                sum(item.passed for item in script_records),
                len(script_records),
            ),
            same_final_state_rate=safe_rate(
                sum(
                    free[index].final_state_hash
                    == script[index].final_state_hash
                    for index in repetitions
                ),
                len(repetitions),
            ),
            same_tool_chain_rate=safe_rate(
                sum(
                    free[index].tool_sequence
                    == script[index].tool_sequence
                    for index in repetitions
                ),
                len(repetitions),
            ),
            free_p50_latency_ms=free_latency.p50_ms,
            script_p50_latency_ms=script_latency.p50_ms,
        )
    ]
