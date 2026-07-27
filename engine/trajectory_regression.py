"""版本化长轨迹回归库运行器。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import NIGHT, QINGQING
from world_schema import Operation, OperationKind, StatePatch, WorldEvent, WorldState

from .event import commit_event
from .trajectory_eval import evaluate_trajectory


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SUITE = PROJECT_ROOT / "benchmarks" / "trajectories" / "v1.json"


def _flag_sequence(
    initial_state: WorldState,
    count: int,
) -> Tuple[List[WorldEvent], WorldState]:
    state = initial_state
    events = []
    for index in range(1, count + 1):
        event, state = commit_event(
            state,
            action_id=f"regression_{index}",
            event_type="trajectory_regression",
            patch=StatePatch(
                operations=[
                    Operation(
                        op=OperationKind.set_flag,
                        path=f"regression.step_{index}",
                        value=True,
                    )
                ]
            ),
            actor_ids=[NIGHT],
            expected_version=state.version,
        )
        events.append(event)
    return events, state


def _build_case(
    case: Dict[str, Any],
    initial_state: WorldState,
) -> Tuple[List[WorldEvent], WorldState]:
    kind = case["kind"]
    count = int(case.get("event_count") or 0)
    if kind in {"flag_sequence", "broken_version"}:
        events, final_state = _flag_sequence(initial_state, count)
        if kind == "broken_version":
            events[1].previous_version += 7
        return events, final_state
    if kind == "dead_actor":
        killed, dead_state = commit_event(
            initial_state,
            action_id="regression_kill",
            event_type="attack",
            patch=StatePatch(
                operations=[
                    Operation(
                        op=OperationKind.kill_character,
                        target_id=QINGQING,
                    )
                ]
            ),
            actor_ids=[NIGHT],
            target_ids=[QINGQING],
            expected_version=initial_state.version,
        )
        ghost, final_state = commit_event(
            dead_state,
            action_id="regression_ghost",
            event_type="speak",
            patch=StatePatch(),
            actor_ids=[QINGQING],
            expected_version=dead_state.version,
        )
        return [killed, ghost], final_state
    if kind == "unknown_target":
        event, final_state = commit_event(
            initial_state,
            action_id="regression_unknown_target",
            event_type="observe",
            patch=StatePatch(),
            actor_ids=[NIGHT],
            target_ids=["missing_entity"],
            expected_version=initial_state.version,
        )
        return [event], final_state
    raise ValueError(f"未知长轨迹回归类型: {kind}")


def run_suite(path: Path = DEFAULT_SUITE) -> Dict[str, Any]:
    suite = json.loads(path.read_text(encoding="utf-8"))
    if suite.get("schema_version") != 1:
        raise ValueError("只支持 schema_version=1 的长轨迹回归库")
    results = []
    for case in suite.get("cases") or []:
        initial_state = build_snapshot()
        events, final_state = _build_case(case, initial_state)
        report = evaluate_trajectory(
            initial_state,
            events,
            expected_final_state=(
                final_state if case["expected_passed"] else None
            ),
        )
        codes = sorted({item.code for item in report.violations})
        expected_codes = sorted(case.get("expected_codes") or [])
        matched = (
            report.passed is bool(case["expected_passed"])
            and all(code in codes for code in expected_codes)
        )
        results.append(
            {
                "case_id": case["case_id"],
                "event_count": len(events),
                "passed": report.passed,
                "violation_codes": codes,
                "expected_passed": case["expected_passed"],
                "expected_codes": expected_codes,
                "matched": matched,
                "summary": report.summary(),
            }
        )
    return {
        "suite": suite["suite"],
        "case_count": len(results),
        "passed": all(result["matched"] for result in results),
        "results": results,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="运行版本化长轨迹回归库")
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = run_suite(args.suite)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
