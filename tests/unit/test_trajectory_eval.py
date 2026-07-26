"""20/60 回合长轨迹确定性发布门禁测试。"""

from engine import commit_event, evaluate_trajectory
from examples.huarong_lane.scenario import NIGHT, OUTER_ROBE, QINGQING
from world_schema import Operation, OperationKind, StatePatch


def _build_flag_trajectory(initial_state, count):
    state = initial_state
    events = []
    for index in range(1, count + 1):
        event, state = commit_event(
            state,
            action_id=f"trajectory_{index}",
            event_type="trajectory_test",
            patch=StatePatch(
                operations=[
                    Operation(
                        op=OperationKind.set_flag,
                        path=f"trajectory.step_{index}",
                        value=True,
                    )
                ]
            ),
            actor_ids=[NIGHT],
            expected_version=state.version,
        )
        events.append(event)
    return events, state


def test_twenty_turn_trajectory_passes_release_gate(snapshot):
    events, final_state = _build_flag_trajectory(snapshot, 20)

    report = evaluate_trajectory(
        snapshot,
        events,
        expected_final_state=final_state,
    )

    assert report.passed
    assert report.event_count == 20
    assert report.final_version == 20
    assert report.summary().startswith("PASS")


def test_sixty_turn_trajectory_remains_schema_valid(snapshot):
    events, final_state = _build_flag_trajectory(snapshot, 60)

    report = evaluate_trajectory(
        snapshot,
        events,
        expected_final_state=final_state,
    )

    assert report.passed
    assert report.final_version == 60
    assert len(final_state.flags) >= 60


def test_trajectory_gate_detects_broken_version_chain(snapshot):
    events, _ = _build_flag_trajectory(snapshot, 3)
    events[1].previous_version = 9

    report = evaluate_trajectory(snapshot, events)

    assert not report.passed
    assert report.violations[0].code == "version_chain"


def test_trajectory_gate_detects_dead_actor(snapshot):
    kill_event, dead_state = commit_event(
        snapshot,
        action_id="kill_qingqing",
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
        expected_version=0,
    )
    ghost_event, _ = commit_event(
        dead_state,
        action_id="dead_character_acts",
        event_type="speak",
        patch=StatePatch(),
        actor_ids=[QINGQING],
        expected_version=1,
    )

    report = evaluate_trajectory(
        snapshot,
        [kill_event, ghost_event],
    )

    assert not report.passed
    assert any(
        violation.code == "dead_actor"
        for violation in report.violations
    )


def test_item_target_is_a_known_world_entity(snapshot):
    event, final_state = commit_event(
        snapshot,
        action_id="observe_item",
        event_type="observe",
        patch=StatePatch(),
        actor_ids=[NIGHT],
        target_ids=[OUTER_ROBE],
        expected_version=0,
    )

    report = evaluate_trajectory(
        snapshot,
        [event],
        expected_final_state=final_state,
    )

    assert report.passed
