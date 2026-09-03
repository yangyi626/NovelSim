"""玩家小说投影与原著对照测试。"""

from pathlib import Path

from engine.manuscript import ManuscriptPassage
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


def test_player_view_uses_manifest_chapter_range_and_case():
    state = build_canonical_start_state()

    payload = build_player_view(
        project_root=PROJECT_ROOT,
        package_id="first_crazy_ch5_checkpoint",
        state=state,
        events=[],
        manifest={
            "entry_kind": "canonical_checkpoint",
            "canonical_case_id": "first_crazy_waste_third_lady_ch1_5",
            "chapter_start": 3,
            "chapter_end": 4,
        },
    )

    assert payload["canonical_baseline_available"] is True
    assert [item["chapter"] for item in payload["original_chapters"]] == [3, 4]


def test_player_authority_is_exposed_as_intervention_and_divergence():
    state = build_canonical_start_state()
    state.version = 1

    payload = build_player_view(
        project_root=PROJECT_ROOT,
        package_id=CANONICAL_PACKAGE_ID,
        state=state,
        events=[_take_robe_event("player_action")],
    )

    assert payload["schema_version"] == "player_story_view.v2"
    assert payload["story_beats"][0]["source"] == "player"
    assert payload["player_intervention_count"] == 1
    assert payload["diverged"] is True


def test_player_view_prefers_persisted_narration_and_filters_internal_events():
    state = build_canonical_start_state()
    state.version = 2
    event = _take_robe_event("player_action")
    event.presentation_events = [
        {
            "event_type": "narration",
            "payload": {
                "text": "夜轻歌抬手夺回外衫，院中的喧声霎时一静。",
                "viewpoint": "third_person",
                "grounded_event_ids": [event.event_id],
                "referenced_entity_ids": [NIGHT, QINGQING],
            },
        }
    ]
    internal = WorldEvent(
        event_id="event_dialogue_memory",
        event_type="system.dialogue_perceived",
        actor_ids=[QINGQING],
        previous_version=1,
        new_version=2,
        summary="夜清清记住了这场对话",
    )

    payload = build_player_view(
        project_root=PROJECT_ROOT,
        package_id=CANONICAL_PACKAGE_ID,
        state=state,
        events=[event, internal],
    )

    assert len(payload["story_beats"]) == 1
    assert payload["story_beats"][0]["narrative"].startswith("夜轻歌抬手")
    assert payload["story_beats"][0]["source_event_ids"] == [event.event_id]
    assert [item["kind"] for item in payload["activity_items"]] == [
        "world_change",
        "system",
    ]


def test_player_view_projects_ready_passages_without_removing_legacy_beats():
    state = build_canonical_start_state()
    state.version = 1
    event = _take_robe_event("player_action")
    passage = {
        "passage_id": "passage_001",
        "chapter_number": 2,
        "manuscript_sequence": 1,
        "title": "外衫易手",
        "paragraphs": ["风从院墙上掠过。", "夜轻歌将外衫收回臂弯。"],
        "dialogues": [],
        "system_hints": [],
        "source_event_ids": [event.event_id],
        "from_world_version": 1,
        "to_world_version": 1,
        "generation_kind": "deterministic",
        "generation_status": "ready",
        "current_revision": 1,
    }

    payload = build_player_view(
        project_root=PROJECT_ROOT,
        package_id=CANONICAL_PACKAGE_ID,
        state=state,
        events=[event],
        manuscript={
            "manuscript_id": "manuscript_001",
            "status": "draft",
            "current_revision": 1,
        },
        passages=[passage],
    )

    assert payload["manuscript"]["total_passages"] == 1
    assert payload["novel_passages"][0]["paragraphs"] == passage["paragraphs"]
    assert payload["novel_passages"][0]["generation_status"] == "ready"
    assert payload["novel_passages"][0]["generation_kind"] == "deterministic"
    assert payload["story_beats"]


def test_player_view_keeps_unknown_chapter_null_and_projects_dialogue_names():
    state = build_canonical_start_state()
    state.flags.pop("canonical.checkpoint_chapter", None)
    event = _take_robe_event("player_action")
    event.presentation_events = [
        {
            "event_type": "dialogue",
            "payload": {
                "speaker_id": "char_unknown_speaker",
                "to_id": "char_unknown_target",
                "line": "别再提 canonical.secret，也别调用 ability:shadow。",
            },
        }
    ]
    passage = {
        "passage_id": "passage_unknown_chapter",
        "chapter_number": None,
        "manuscript_sequence": 1,
        "paragraphs": ["雨声渐近。"],
        "dialogues": [
            {
                "speaker_id": "char_unknown_speaker",
                "to_id": "char_unknown_target",
                "line": "我会留下。",
            }
        ],
        "source_event_ids": [event.event_id],
        "generation_status": "ready",
    }

    payload = build_player_view(
        project_root=PROJECT_ROOT,
        package_id="custom_world",
        state=state,
        events=[event],
        passages=[passage],
    )

    assert payload["checkpoint_chapter"] is None
    assert payload["current_story_chapter"] is None
    assert payload["story_beats"][0]["chapter"] is None
    projected = payload["novel_passages"][0]
    assert projected["chapter"] is None
    assert projected["dialogues"][0]["speaker_name"] == "一名角色"
    assert projected["dialogues"][0]["to_name"] == "对方"
    beat_dialogue = payload["story_beats"][0]["dialogues"][0]
    assert beat_dialogue["speaker_name"] == "一名角色"
    assert beat_dialogue["to_name"] == "对方"
    assert "canonical." not in beat_dialogue["line"]
    assert "ability:" not in beat_dialogue["line"]


def test_player_view_safely_projects_legacy_reader_text_and_quality_issues():
    state = build_canonical_start_state()
    event = _take_robe_event("player_action")
    passage = {
        "passage_id": "passage_legacy",
        "entry_id": "book:chapter:2",
        "entry_revision": 3,
        "chapter_number": 2,
        "manuscript_sequence": 1,
        "title": "canonical.secret",
        "paragraphs": [
            "char_yeqingge 抬眼看向夜色，ability:shadow 在掌心散去。",
            "EVENT event_1 actor=char_yeqingge ability:shadow -> canonical.secret",
        ],
        "dialogues": [],
        "source_event_ids": [event.event_id],
        "generation_kind": "legacy",
        "generation_status": "ready",
        "metadata": {"quality_issues": ["旧稿节奏需要重写"]},
    }

    payload = build_player_view(
        project_root=PROJECT_ROOT,
        package_id="custom_world",
        state=state,
        events=[event],
        passages=[passage],
    )

    projected = payload["novel_passages"][0]
    assert projected["entry_id"] == "book:chapter:2"
    assert projected["entry_revision"] == 3
    assert projected["paragraphs"] == ["夜轻歌 抬眼看向夜色，某个事物 在掌心散去。"]
    assert "EVENT" not in projected["narrative"]
    assert projected["quality_issues"] == [
        "旧稿含内部标识，读者版已安全泛化",
        "旧稿含纯协议记录，已从读者正文隐藏",
        "旧稿节奏需要重写",
    ]
    assert projected["reader_safe"] is False
    assert passage["paragraphs"][0].startswith("char_yeqingge")


def test_player_view_serializes_manuscript_enums_as_contract_values():
    state = build_canonical_start_state()
    state.version = 1
    event = _take_robe_event("player_action")
    passage = ManuscriptPassage(
        passage_id="passage_enum_001",
        manuscript_id="manuscript_001",
        session_id="session_001",
        chapter_number=2,
        manuscript_sequence=1,
        title="外衫易手",
        paragraphs=["夜轻歌将外衫收回臂弯。"],
        source_event_ids=[event.event_id],
        from_world_version=1,
        to_world_version=1,
        current_revision=1,
    )

    payload = build_player_view(
        project_root=PROJECT_ROOT,
        package_id=CANONICAL_PACKAGE_ID,
        state=state,
        events=[event],
        passages=[passage],
    )

    projected = payload["novel_passages"][0]
    assert projected["generation_status"] == "ready"
    assert projected["generation_kind"] == "deterministic"
    assert projected["paragraphs"] == passage.paragraphs
