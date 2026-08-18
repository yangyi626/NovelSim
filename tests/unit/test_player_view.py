"""玩家小说投影与原著对照测试。"""

from pathlib import Path

from examples.huarong_lane.canonical_case import build_canonical_start_state
from examples.huarong_lane.scenario import NIGHT, QINGQING
from web.player_view import CANONICAL_PACKAGE_ID, build_player_view
from world_schema import CausalEvidence, StatePatch, WorldEvent


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _take_robe_event(authority: str = "runtime") -> WorldEvent:
    return WorldEvent(
        event_id="event_take_robe",
        event_type="tool.take_item",
        actor_ids=[NIGHT],
        target_ids=["item_qingqing_outer_robe", QINGQING],
        previous_version=0,
        new_version=1,
        summary=(
            "char_yeqingge took item_qingqing_outer_robe "
            "from char_yeqingqing"
        ),
        patch=StatePatch(
            causal_evidence=CausalEvidence(
                tool_name="take_item",
                actor_id=NIGHT,
                authority=authority,
            )
        ),
    )


def test_player_view_projects_story_and_human_only_canon():
    state = build_canonical_start_state()
    state.version = 1

    payload = build_player_view(
        project_root=PROJECT_ROOT,
        package_id=CANONICAL_PACKAGE_ID,
        state=state,
        events=[_take_robe_event()],
        source_chapters=[],
    )

    assert payload["canon_is_human_only"] is True
    assert payload["current_story_chapter"] == 2
    assert payload["comparison"][0]["status"] == "matched"
    assert payload["metrics"]["matched_event_count"] == 1
    assert payload["original_chapters"][0]["title"].startswith("第1章")
    narrative = payload["story_beats"][0]["narrative"]
    assert "夜轻歌" in narrative
    assert "夜清清" in narrative
    assert "夜清清的外衫" in narrative
    assert "从夜清清手中" in narrative


def test_player_authority_is_exposed_as_intervention_and_divergence():
    state = build_canonical_start_state()
    state.version = 1

    payload = build_player_view(
        project_root=PROJECT_ROOT,
        package_id=CANONICAL_PACKAGE_ID,
        state=state,
        events=[_take_robe_event("player_action_with_npc_reactions")],
    )

    assert payload["story_beats"][0]["source"] == "player"
    assert payload["player_intervention_count"] == 1
    assert payload["diverged"] is True
