import asyncio

from engine import evaluate_trajectory
from examples.secret_letter import build_snapshot, run_secret_letter_scene


def test_fact_ids_are_valid_event_targets_in_trajectory_replay():
    initial = build_snapshot()
    run = asyncio.run(run_secret_letter_scene())
    events = [
        outcome.event
        for outcome in run.outcomes
        if outcome.event is not None
    ]

    report = evaluate_trajectory(
        initial,
        events,
        expected_final_state=run.state,
    )

    assert report.passed is True
    assert not {
        item.code
        for item in report.violations
        if item.code == "unknown_target"
    }
