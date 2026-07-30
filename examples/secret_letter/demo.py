"""运行原创密信场景并打印可读的事件、证据链与结局。"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any, Dict, Optional, Sequence

from engine.scene_controller import SceneMode

from .scenario import (
    PLAYER_ROUTE_DESTROY,
    PLAYER_ROUTE_EXPOSE,
    PLAYER_ROUTE_INTERCEPT,
    run_secret_letter_scene,
)


ROUTES = {
    "none": None,
    PLAYER_ROUTE_DESTROY: PLAYER_ROUTE_DESTROY,
    PLAYER_ROUTE_INTERCEPT: PLAYER_ROUTE_INTERCEPT,
    PLAYER_ROUTE_EXPOSE: PLAYER_ROUTE_EXPOSE,
}


def _result_payload(run) -> Dict[str, Any]:
    events = []
    for outcome in run.outcomes:
        event = outcome.event
        events.append(
            {
                "tool": outcome.result.tool_name,
                "success": outcome.result.success,
                "failure": (
                    outcome.result.failure.code.value
                    if outcome.result.failure
                    else None
                ),
                "event_id": event.event_id if event else None,
                "version": event.new_version if event else run.state.version,
                "summary": event.summary if event else "",
            }
        )
    return {
        "status": run.summary.status.value,
        "ending": run.ending,
        "objective_satisfied": run.summary.objective_satisfied,
        "world_version": run.state.version,
        "tool_sequence": run.summary.tool_sequence,
        "events": events,
        "belief_evidence_count": len(run.state.belief_evidence),
        "propagation_count": len(run.state.propagation_history),
        "alliances": {
            alliance_id: alliance.dict()
            for alliance_id, alliance in run.state.alliances.items()
        },
        "reason": run.summary.ending_reason,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in SceneMode],
        default=SceneMode.free.value,
    )
    parser.add_argument("--route", choices=sorted(ROUTES), default="none")
    args = parser.parse_args(argv)

    run = asyncio.run(
        run_secret_letter_scene(
            mode=SceneMode(args.mode),
            player_route=ROUTES[args.route],
        )
    )
    print(json.dumps(_result_payload(run), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
