import pytest

from engine import (
    CORE_TOOL_PERMISSIONS,
    authoritative_state_hash,
    build_game_observation,
    compact_observation,
    create_core_tool_registry,
)
from examples.secret_letter import FACT_PLOT, GUARD, RIVAL, build_snapshot


def test_observation_is_actor_scoped_read_only_and_deterministic():
    state = build_snapshot()
    registry = create_core_tool_registry()

    first = build_game_observation(
        state,
        GUARD,
        registry,
        world_package_id="secret_letter_v1",
        scenario_family="secret_transport",
    )
    second = build_game_observation(
        state.copy(deep=True),
        GUARD,
        registry,
        world_package_id="secret_letter_v1",
        scenario_family="secret_transport",
    )

    assert first == second
    assert first.authoritative_state_hash == authoritative_state_hash(state)
    assert first.world_version == 0
    assert FACT_PLOT not in {belief.fact_id for belief in first.beliefs}
    assert {fact.fact_id for fact in first.observable_facts} == {FACT_PLOT}
    compact = compact_observation(first)
    observe = next(
        tool for tool in compact["available_tools"]
        if tool["tool_name"] == "observe"
    )
    assert observe["properties"]["fact_id"]["enum"] == [FACT_PLOT]
    assert "statement" not in compact["observable_facts"][0]
    assert "truth" not in compact["observable_facts"][0]
    assert RIVAL not in {
        character.character_id for character in first.visible_characters
    }
    assert "pick_up" in {tool.name for tool in first.available_tools}
    assert first.persona_traits == ("警觉", "守序")

    with pytest.raises(TypeError):
        first.world_version = 99


def test_observation_does_not_alias_mutable_world_state():
    state = build_snapshot()
    observation = build_game_observation(
        state,
        GUARD,
        create_core_tool_registry(),
    )
    original_name = next(
        character.display_name
        for character in observation.visible_characters
        if character.character_id == GUARD
    )

    state.characters[GUARD].display_name = "被外部修改"
    state.character_psyches[GUARD].traits.append("新特质")

    observed_guard = next(
        character
        for character in observation.visible_characters
        if character.character_id == GUARD
    )
    assert observed_guard.display_name == original_name
    assert "新特质" not in observation.persona_traits


def test_observation_rejects_unknown_actor():
    with pytest.raises(ValueError, match="actor not found"):
        build_game_observation(
            build_snapshot(),
            "missing_actor",
            create_core_tool_registry(),
        )
