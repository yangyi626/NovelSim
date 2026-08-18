"""Player-facing story projection and human-only canon comparison.

This module reads committed WorldEvents and the source-backed evaluation case.
The returned canon text is only served to the player UI; it is never attached
to planner observations or replan feedback.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from evaluation.canonical_alignment import (
    CanonicalEventAnchor,
    align_canonical_events,
)
from world_schema import WorldEvent, WorldState


CANONICAL_PACKAGE_ID = "first_crazy_ch1_checkpoint"
CANONICAL_CASE = Path("evaluation/canonical_cases/first_crazy_ch1_5.json")
CANONICAL_NOVEL = Path("novels/第一狂妃：废柴三小姐.txt")
_CHAPTER_PATTERN = re.compile(r"^第\s*(\d+)\s*章\s*(.*)$")


def build_player_view(
    *,
    project_root: Path,
    package_id: str,
    state: WorldState,
    events: Sequence[WorldEvent],
    source_chapters: Sequence[Any] = (),
) -> Dict[str, Any]:
    """Project one session into novel beats and an optional canon baseline."""

    canonical = package_id == CANONICAL_PACKAGE_ID
    anchors: List[CanonicalEventAnchor] = []
    case_payload: Dict[str, Any] = {}
    if canonical:
        case_payload = json.loads(
            (project_root / CANONICAL_CASE).read_text(encoding="utf-8")
        )
        anchors = [
            CanonicalEventAnchor.parse_obj(item)
            for item in case_payload.get("canonical_events", [])
        ]

    alignment = align_canonical_events(anchors, events)
    anchor_by_id = {item.event_id: item for item in anchors}
    event_alignment = {
        item.simulated_event_id: item
        for item in alignment.alignments
        if item.matched and item.simulated_event_id
    }
    checkpoint_chapter = int(
        state.flags.get("canonical.checkpoint_chapter") or 1
    )
    current_chapter = checkpoint_chapter
    story_beats = []
    for event in events:
        matched = event_alignment.get(event.event_id)
        anchor = (
            anchor_by_id.get(matched.canonical_event_id)
            if matched is not None
            else None
        )
        if anchor is not None:
            current_chapter = max(current_chapter, anchor.chapter)
        beat = _story_beat(
            event,
            state,
            chapter=current_chapter,
            alignment=matched.dict() if matched is not None else None,
        )
        story_beats.append(beat)

    comparison = []
    beat_by_event = {item["event_id"]: item for item in story_beats}
    for anchor, matched in zip(
        sorted(anchors, key=lambda item: (item.chapter, item.order)),
        alignment.alignments,
    ):
        simulated = (
            beat_by_event.get(matched.simulated_event_id)
            if matched.simulated_event_id
            else None
        )
        comparison.append(
            {
                "canonical_event_id": anchor.event_id,
                "chapter": anchor.chapter,
                "order": anchor.order,
                "canonical_summary": anchor.summary,
                "weight": anchor.weight,
                "status": "matched" if matched.matched else "pending",
                "score": matched.score,
                "simulated_event_id": matched.simulated_event_id,
                "simulated_narrative": (
                    simulated.get("narrative", "") if simulated else ""
                ),
                "actor_match": matched.actor_match,
                "target_match": matched.target_match,
                "tool_match": matched.tool_match,
            }
        )

    unmatched = [
        item
        for item in story_beats
        if item["event_id"] in alignment.unmatched_simulated_event_ids
    ]
    player_interventions = [
        item for item in story_beats if item["source"] == "player"
    ]
    chapters = (
        _load_original_chapters(project_root / CANONICAL_NOVEL, limit=5)
        if canonical
        else _chapter_placeholders(source_chapters)
    )
    return {
        "status": "ok",
        "schema_version": "player_story_view.v1",
        "world_package_id": package_id,
        "canonical_baseline_available": canonical,
        "canon_is_human_only": canonical,
        "case_id": case_payload.get("case_id", ""),
        "checkpoint_chapter": checkpoint_chapter,
        "current_story_chapter": current_chapter,
        "story_beats": story_beats,
        "original_chapters": chapters,
        "comparison": comparison,
        "unmatched_beats": unmatched,
        "player_intervention_count": len(player_interventions),
        "diverged": bool(player_interventions or unmatched),
        "metrics": alignment.metrics.dict(),
    }


def _story_beat(
    event: WorldEvent,
    state: WorldState,
    *,
    chapter: int,
    alignment: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    evidence = event.patch.causal_evidence
    tool_name = (
        evidence.tool_name
        if evidence is not None and evidence.tool_name
        else (
            event.event_type.split(".", 1)[1]
            if event.event_type.startswith("tool.")
            else event.event_type
        )
    )
    actor_id = (
        evidence.actor_id
        if evidence is not None and evidence.actor_id
        else (event.actor_ids[0] if event.actor_ids else "")
    )
    dialogues = []
    hints = []
    for presentation in event.presentation_events:
        payload = presentation.get("payload", {})
        if presentation.get("event_type") == "dialogue":
            dialogues.append(
                {
                    "speaker_id": str(payload.get("speaker_id") or ""),
                    "speaker": _entity_name(state, payload.get("speaker_id")),
                    "to_id": str(payload.get("to_id") or ""),
                    "to": _entity_name(state, payload.get("to_id")),
                    "line": str(payload.get("line") or ""),
                    "tone": str(payload.get("tone") or ""),
                }
            )
        elif presentation.get("event_type") == "system_hint":
            text = str(payload.get("text") or "").strip()
            if text:
                hints.append(text)

    source = (
        "player"
        if evidence is not None
        and evidence.authority == "player_action_with_npc_reactions"
        else "agent"
        if event.event_type.startswith("tool.")
        else "environment"
    )
    return {
        "beat_id": f"beat_{event.new_version:06d}",
        "event_id": event.event_id,
        "world_version": event.new_version,
        "chapter": chapter,
        "title": _beat_title(tool_name, actor_id, event, state),
        "narrative": _event_narrative(tool_name, actor_id, event, state),
        "dialogues": dialogues,
        "system_hints": hints,
        "source": source,
        "tool_name": tool_name,
        "actor_ids": list(event.actor_ids),
        "target_ids": list(event.target_ids),
        "actor_names": [_entity_name(state, item) for item in event.actor_ids],
        "target_names": [_entity_name(state, item) for item in event.target_ids],
        "alignment": dict(alignment or {}),
        "alignment_status": "matched" if alignment else "new",
    }


def _beat_title(
    tool_name: str,
    actor_id: str,
    event: WorldEvent,
    state: WorldState,
) -> str:
    actor = _entity_name(state, actor_id)
    labels = {
        "talk_to": "一句话改变局势",
        "take_item": "取回主动权",
        "give_item": "物品易手",
        "move_to": "场景转移",
        "invoke_ability": "能力发动",
        "share_information": "消息开始传播",
        "propose_alliance": "新的联盟",
        "speak": "华容巷中的交锋",
    }
    return f"{actor} · {labels.get(tool_name, '世界继续演化')}" if actor else labels.get(tool_name, "世界继续演化")


def _event_narrative(
    tool_name: str,
    actor_id: str,
    event: WorldEvent,
    state: WorldState,
) -> str:
    actor = _entity_name(state, actor_id) or "有人"
    targets = [_entity_name(state, item) for item in event.target_ids]
    target = next(
        (
            _entity_name(state, target_id)
            for target_id in event.target_ids
            if target_id in state.characters
        ),
        next((item for item in targets if item), "眼前的人"),
    )
    item = next(
        (
            _entity_name(state, item_id)
            for item_id in event.target_ids
            if item_id in state.items
        ),
        "物品",
    )
    location = next(
        (
            _entity_name(state, location_id)
            for location_id in event.target_ids
            if location_id in state.locations
        ),
        "",
    )
    if tool_name == "take_item":
        return f"{actor}没有再退让，当众从{target}手中取走了{item}，局势的主动权随之发生变化。"
    if tool_name == "give_item":
        return f"{actor}将{item}交给了{target}。这个选择已经写入当前世界线，并将影响之后的角色判断。"
    if tool_name == "move_to":
        return f"{actor}结束了眼前的停留，动身前往{location or target}。故事的舞台随之转移。"
    if tool_name == "talk_to" or tool_name == "speak":
        return f"{actor}打破沉默，向{target}表明了自己的态度。周围角色也在依据这番交锋重新判断局势。"
    if tool_name == "invoke_ability":
        return f"{actor}发动了自身能力，世界状态随之出现了无法由普通行动造成的变化。"
    if tool_name == "share_information":
        return f"{actor}把掌握的消息传给了{target}，信息开始沿角色关系向外扩散。"
    if tool_name == "propose_alliance":
        return f"{actor}向{target}提出合作，新的关系分支由此形成。"
    summary = _replace_entity_ids(event.summary, state).strip()
    return summary or f"{actor}采取了行动，世界线推进至 v{event.new_version}。"


def _entity_name(state: WorldState, entity_id: Any) -> str:
    key = str(entity_id or "")
    if key in state.characters:
        return state.characters[key].display_name
    if key in state.items:
        return state.items[key].display_name
    if key in state.locations:
        return state.locations[key].display_name
    return key


def _replace_entity_ids(text: str, state: WorldState) -> str:
    result = str(text or "")
    entities: Iterable[Any] = (
        list(state.characters.values())
        + list(state.items.values())
        + list(state.locations.values())
    )
    for entity in entities:
        entity_id = getattr(entity, "character_id", None) or getattr(
            entity, "item_id", None
        ) or getattr(entity, "location_id", None)
        result = result.replace(str(entity_id), str(entity.display_name))
    return result


def _load_original_chapters(path: Path, *, limit: int) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    chapters: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = _CHAPTER_PATTERN.match(line)
        if match:
            if current is not None:
                chapters.append(_finalize_chapter(current))
            number = int(match.group(1))
            if number > limit:
                current = None
                break
            current = {
                "chapter": number,
                "title": f"第{number}章 {match.group(2).strip()}",
                "lines": [],
            }
        elif current is not None and line:
            current["lines"].append(line)
    if current is not None and len(chapters) < limit:
        chapters.append(_finalize_chapter(current))
    return chapters


def _finalize_chapter(chapter: Dict[str, Any]) -> Dict[str, Any]:
    content = "\n\n".join(chapter.pop("lines", []))
    return {
        **chapter,
        "excerpt": content,
        "truncated": False,
    }


def _chapter_placeholders(source_chapters: Sequence[Any]) -> List[Dict[str, Any]]:
    return [
        {
            "chapter": index + 1,
            "title": str(value),
            "excerpt": "该世界未配置原著正文对照。",
            "truncated": False,
        }
        for index, value in enumerate(source_chapters)
    ]


__all__ = ["CANONICAL_PACKAGE_ID", "build_player_view"]
