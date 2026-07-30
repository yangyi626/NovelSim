import asyncio

from engine import SceneMode, SceneStatus, replay_events
from examples.secret_letter import (
    ALLY,
    FACT_PLOT,
    LETTER,
    PLAYER,
    PLAYER_ROUTE_DESTROY,
    PLAYER_ROUTE_EXPOSE,
    PLAYER_ROUTE_INTERCEPT,
    STEWARD,
    build_snapshot,
    run_autonomous,
    run_secret_letter_scene,
)


def _run(intervention="none"):
    return asyncio.run(run_autonomous(intervention=intervention))


def test_secret_letter_autonomous_chain_reaches_evidence_backed_alliance():
    result = _run()

    assert result.successful is True
    assert result.ending == "defenders_allied"
    assert [outcome.result.tool_name for outcome in result.outcomes] == [
        "pick_up",
        "observe",
        "share_information",
        "share_information",
        "propose_alliance",
    ]
    assert result.state.version == 5
    assert len(result.state.propagation_history) == 2
    assert len(result.state.belief_evidence) == 3
    alliance = next(iter(result.state.alliances.values()))
    assert alliance.member_ids == sorted([STEWARD, ALLY])
    assert alliance.shared_fact_ids == [FACT_PLOT]
    assert alliance.evidence_event_ids
    replayed = replay_events(
        build_snapshot(),
        [outcome.event for outcome in result.outcomes],
    )
    assert replayed == result.state


def test_free_and_script_modes_produce_same_authoritative_chain():
    free = asyncio.run(run_secret_letter_scene(mode=SceneMode.free))
    script = asyncio.run(run_secret_letter_scene(mode=SceneMode.script))

    assert free.ending == script.ending == "defenders_allied"
    assert free.state == script.state
    assert free.summary.tool_sequence == script.summary.tool_sequence
    assert free.summary.event_ids == script.summary.event_ids
    assert {
        step.decision_source for step in free.summary.steps
    } == {"free"}
    assert {
        step.decision_source for step in script.summary.steps
    } == {"script"}


def test_three_player_routes_are_real_tool_events_with_distinct_endings():
    destroyed = asyncio.run(
        run_secret_letter_scene(player_route=PLAYER_ROUTE_DESTROY)
    )
    intercepted = asyncio.run(
        run_secret_letter_scene(player_route=PLAYER_ROUTE_INTERCEPT)
    )
    exposed = asyncio.run(
        run_secret_letter_scene(player_route=PLAYER_ROUTE_EXPOSE)
    )

    assert {
        destroyed.ending,
        intercepted.ending,
        exposed.ending,
    } == {
        "letter_destroyed",
        "player_intercepted",
        "truth_exposed",
    }
    assert destroyed.summary.tool_sequence == ["pick_up", "destroy_item"]
    assert destroyed.state.items[LETTER].attrs["destroyed"] is True
    assert intercepted.summary.tool_sequence == ["pick_up", "move_to"]
    assert intercepted.state.items[LETTER].owner_id == PLAYER
    assert exposed.summary.tool_sequence == [
        "pick_up",
        "observe",
        "share_information",
        "share_information",
        "propose_alliance",
    ]
    assert all(
        outcome.event is not None
        for result in (destroyed, intercepted, exposed)
        for outcome in result.outcomes
    )
    for result in (destroyed, intercepted, exposed):
        replayed = replay_events(
            build_snapshot(),
            [outcome.event for outcome in result.outcomes],
        )
        assert replayed == result.state
        assert result.summary.status == SceneStatus.completed
    assert not destroyed.state.alliances
    assert not intercepted.state.alliances
    assert exposed.state.alliances


def test_secret_letter_replay_is_deterministic():
    first = _run()
    second = _run()

    assert first.ending == second.ending
    assert first.state.dict() == second.state.dict()
    assert [
        outcome.result.call_id for outcome in first.outcomes
    ] == [
        outcome.result.call_id for outcome in second.outcomes
    ]
    assert first.summary.dict() == second.summary.dict()
