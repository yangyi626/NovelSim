import asyncio

from engine import (
    CORE_TOOL_PERMISSIONS,
    SceneConfig,
    SceneController,
    SceneMode,
    SceneStatus,
    ScriptBeat,
    create_core_tool_registry,
)
from examples.secret_letter import (
    ALLY,
    GUARD,
    PLAYER,
    STEWARD,
    build_snapshot,
    evaluate_ending,
    next_autonomous_call,
)


def _controller():
    return SceneController(
        create_core_tool_registry(),
        permissions=CORE_TOOL_PERMISSIONS,
    )


def test_free_scene_without_selector_is_invalid_and_does_not_mutate_state():
    initial = build_snapshot()
    result = asyncio.run(
        _controller().run(
            initial,
            SceneConfig(
                scene_id="missing_selector",
                mode=SceneMode.free,
                participant_ids=[PLAYER, GUARD, STEWARD, ALLY],
            ),
            ending_evaluator=evaluate_ending,
        )
    )

    assert result.summary.status == SceneStatus.invalid_scene
    assert result.summary.turns_used == 0
    assert result.state == initial


def test_script_beat_cannot_bypass_tool_preconditions():
    initial = build_snapshot()
    result = asyncio.run(
        _controller().run(
            initial,
            SceneConfig(
                scene_id="invalid_script",
                mode=SceneMode.script,
                participant_ids=[PLAYER, GUARD, STEWARD, ALLY],
                script_beats=[
                    ScriptBeat(
                        beat_id="declare_alliance_without_evidence",
                        actor_id=STEWARD,
                        tool_name="propose_alliance",
                        arguments={
                            "target_character_id": ALLY,
                            "goal_key": "protect_estate",
                            "shared_fact_id": "fact_regent_plot",
                        },
                    )
                ],
            ),
            ending_evaluator=evaluate_ending,
        )
    )

    assert result.summary.status == SceneStatus.tool_failed
    assert result.summary.steps[0].success is False
    assert result.summary.steps[0].failure_code == "precondition_failed"
    assert result.state.version == 0
    assert not result.state.alliances


def test_participant_scope_is_checked_before_scene_execution():
    initial = build_snapshot()
    result = asyncio.run(
        _controller().run(
            initial,
            SceneConfig(
                scene_id="bad_participant",
                mode=SceneMode.free,
                participant_ids=["missing_character"],
            ),
            free_selector=next_autonomous_call,
            ending_evaluator=evaluate_ending,
        )
    )

    assert result.summary.status == SceneStatus.invalid_scene
    assert "not found" in result.summary.failure_reasons[0]
    assert result.state == initial


def test_scene_turn_limit_is_explicit_and_summary_is_memory_ready():
    result = asyncio.run(
        _controller().run(
            build_snapshot(),
            SceneConfig(
                scene_id="short_scene",
                mode=SceneMode.free,
                participant_ids=[PLAYER, GUARD, STEWARD, ALLY],
                max_turns=2,
            ),
            free_selector=next_autonomous_call,
            ending_evaluator=evaluate_ending,
        )
    )

    assert result.summary.status == SceneStatus.turn_limit
    assert result.summary.turns_used == 2
    assert result.summary.final_version == 2
    assert result.summary.event_ids == ["event_000001", "event_000002"]
    assert "turns=2" in result.summary.to_memory_text()
