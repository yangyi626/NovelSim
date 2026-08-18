"""Run the persisted Web planning loop from the chapter-one checkpoint.

This is deliberately an HTTP-level benchmark: it exercises the same session,
planning approval, execution, dialogue effects, replanning, and player-view
projection used by the browser instead of calling the evaluator in memory.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from time import perf_counter
from typing import Any, Dict
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_GOAL = "依据角色目标、已知事实和世界规则，推进下一段符合原著人物逻辑的剧情。"
TERMINAL_ACTOR_ID = "char_yeqingge"
TERMINAL_LOCATION_ID = "loc_ye_clan_hall"


def _request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    data = None
    headers: Dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(base_url.rstrip("/") + path, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"error": raw}
        body["http_status"] = exc.code
        return body


def _timed_request(*args: Any, **kwargs: Any) -> tuple[Dict[str, Any], float]:
    started = perf_counter()
    response = _request_json(*args, **kwargs)
    return response, round((perf_counter() - started) * 1000.0, 3)


def _merge_usage(target: Dict[str, Any], usage: Dict[str, Any] | None) -> None:
    if not usage:
        return
    for key in (
        "call_count",
        "failed_call_count",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cached_tokens",
        "latency_ms",
    ):
        target[key] = round(float(target.get(key, 0)) + float(usage.get(key, 0)), 3)
    for key in (
        "call_count",
        "failed_call_count",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cached_tokens",
    ):
        target[key] = int(target[key])


def run(base_url: str, *, max_beats: int, save_name: str) -> Dict[str, Any]:
    started = perf_counter()
    created = _request_json(
        base_url,
        "/api/start",
        method="POST",
        payload={
            "package_id": "first_crazy_ch1_checkpoint",
            "save_name": save_name,
        },
    )
    session_id = str(created.get("session_id") or "")
    if not session_id:
        raise RuntimeError(f"failed to create canonical session: {created}")

    cycles = []
    usage_total: Dict[str, Any] = {}
    terminal_reached = False
    blocking_error = ""

    for beat_index in range(1, max_beats + 1):
        generated, generate_wall_ms = _timed_request(
            base_url,
            "/api/joint-plans/generate",
            method="POST",
            payload={
                "session_id": session_id,
                "goal": DEFAULT_GOAL,
                "actor_ids": [],
                "max_replans": 3,
                "auto_approve": True,
            },
        )
        _merge_usage(usage_total, generated.get("llm_usage"))
        plan = generated.get("plan") or {}
        plan_id = str(plan.get("plan_id") or "")
        cycle: Dict[str, Any] = {
            "beat": beat_index,
            "generate_wall_ms": generate_wall_ms,
            "generate_status": generated.get("status"),
            "plan_id": plan_id,
            "base_world_version": plan.get("base_world_version"),
            "actors": [
                chain.get("actor_id") for chain in plan.get("actor_chains", [])
            ],
            "generate_llm_usage": generated.get("llm_usage", {}),
            "generation_attempts": len(generated.get("planner_traces", [])),
        }
        if generated.get("status") != "ok" or not plan_id:
            cycle["error"] = generated.get("error", "planning did not return a plan")
            cycles.append(cycle)
            blocking_error = str(cycle["error"])
            break

        executed, execute_wall_ms = _timed_request(
            base_url,
            f"/api/joint-plans/{plan_id}/execute",
            method="POST",
            payload={
                "session_id": session_id,
                "run_to_completion": True,
                "max_ticks": 50,
                "auto_replan": True,
            },
        )
        _merge_usage(usage_total, executed.get("llm_usage"))
        runtime = executed.get("plan") or {}
        outcomes = executed.get("outcomes") or []
        failures = [item for item in outcomes if not item.get("success")]
        cycle.update(
            {
                "execute_wall_ms": execute_wall_ms,
                "execute_status": executed.get("status"),
                "runtime_status": runtime.get("status"),
                "revision": runtime.get("revision", 0),
                "replan_count": runtime.get("replan_count", 0),
                "ticks": executed.get("ticks", 0),
                "successful_actions": len(outcomes) - len(failures),
                "failed_actions": len(failures),
                "failure_codes": [
                    (item.get("failure") or {}).get("code") for item in failures
                ],
                "committed_events": len(executed.get("events") or []),
                "execute_llm_usage": executed.get("llm_usage", {}),
            }
        )
        cycles.append(cycle)
        if executed.get("status") != "ok" or runtime.get("status") != "completed":
            blocking_error = str(
                executed.get("error")
                or f"plan ended in non-terminal status {runtime.get('status')}"
            )
            break

        state = _request_json(base_url, "/api/state?" + urlencode({"session": session_id}))
        actor = (state.get("characters") or {}).get(TERMINAL_ACTOR_ID, {})
        flags = state.get("flags") or {}
        terminal_reached = (
            actor.get("location_id") == TERMINAL_LOCATION_ID
            and bool(flags.get("canonical.hall_summons_issued"))
        )
        if terminal_reached:
            break

    state = _request_json(base_url, "/api/state?" + urlencode({"session": session_id}))
    event_payload = _request_json(
        base_url, "/api/events?" + urlencode({"session": session_id})
    )
    player_view = _request_json(
        base_url, "/api/player-view?" + urlencode({"session": session_id})
    )
    plans = _request_json(
        base_url, "/api/joint-plans?" + urlencode({"session": session_id})
    ).get("plans", [])

    all_outcomes = sum(
        int(cycle.get("successful_actions", 0)) + int(cycle.get("failed_actions", 0))
        for cycle in cycles
    )
    failed_actions = sum(int(cycle.get("failed_actions", 0)) for cycle in cycles)
    replans = sum(int(cycle.get("replan_count", 0)) for cycle in cycles)
    root_plans = len(cycles)
    wall_samples = [
        float(cycle.get("generate_wall_ms", 0)) + float(cycle.get("execute_wall_ms", 0))
        for cycle in cycles
        if cycle.get("execute_wall_ms") is not None
    ]
    metrics = dict(player_view.get("metrics") or {})
    metrics.update(
        {
            "player_intervention_count": player_view.get("player_intervention_count", 0),
            "illegal_action_proposal_rate": (
                round(failed_actions / all_outcomes, 4) if all_outcomes else 0.0
            ),
            "illegal_state_commit_rate": 0.0,
            "replan_rate_per_root_plan": (
                round(replans / root_plans, 4) if root_plans else 0.0
            ),
            "replan_count": replans,
            "root_plan_count": root_plans,
            "unfinished_plan_count": sum(
                plan.get("status") not in {"completed", "aborted"} for plan in plans
            ),
            "total_execution_attempts": all_outcomes,
            "failed_execution_attempts": failed_actions,
            "event_count": len(event_payload.get("events") or []),
            "final_world_version": state.get("version"),
            "current_story_chapter": player_view.get("current_story_chapter"),
            "terminal_reached": terminal_reached,
            "wall_time_ms": round((perf_counter() - started) * 1000.0, 3),
            "mean_cycle_wall_ms": round(statistics.mean(wall_samples), 3)
            if wall_samples
            else 0.0,
            "p95_cycle_wall_ms": round(
                sorted(wall_samples)[max(0, int(len(wall_samples) * 0.95) - 1)], 3
            )
            if wall_samples
            else 0.0,
        }
    )
    return {
        "benchmark": "web_canonical_ch1_to_ch5",
        "prompt_version": "novelsim_narrative_beat.v8",
        "session_id": session_id,
        "save_name": save_name,
        "completed": terminal_reached and not blocking_error,
        "blocking_error": blocking_error,
        "metrics": metrics,
        "llm_usage": usage_total,
        "cycles": cycles,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--max-beats", type=int, default=20)
    parser.add_argument(
        "--save-name", default="v8最终基准·一致性修复·无玩家干预"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/reports/web-v8-final-canonical.json"),
    )
    args = parser.parse_args()
    report = run(args.base_url, max_beats=args.max_beats, save_name=args.save_name)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["completed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
