"""Player-facing story projection and human-only canon comparison.

This module reads committed WorldEvents and the source-backed evaluation case.
The returned canon text is only served to the player UI; it is never attached
to planner observations or replan feedback.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from evaluation.canonical_alignment import (
    CanonicalEventAnchor,
    align_canonical_events,
)
from world_schema import WorldEvent, WorldState


CANONICAL_PACKAGE_ID = "first_crazy_ch1_checkpoint"
CANONICAL_CASE = Path("evaluation/canonical_cases/first_crazy_ch1_5.json")
CANONICAL_NOVEL = Path("novels/第一狂妃：废柴三小姐.txt")
_CANONICAL_CASE_DIRECTORY = Path("evaluation/canonical_cases")
_CANONICAL_CASE_REGISTRY = {
    "first_crazy_waste_third_lady_ch1_5": CANONICAL_CASE,
    "first_crazy_waste_third_lady_ch6_10": (
        _CANONICAL_CASE_DIRECTORY / "first_crazy_ch6_10.json"
    ),
}
_CHAPTER_PATTERN = re.compile(r"^第\s*(\d+)\s*章\s*(.*)$")
_INTERNAL_STORY_EVENT_TYPES = {
    "system.dialogue_perceived",
    "facts.migrated",
}
_INTERNAL_IDENTIFIER_PATTERN = re.compile(
    r"(?<![\w])(?:char|character|item|loc|location|ability|capability|fact|"
    r"event|rule|plot)[_:.][A-Za-z0-9_.:-]+|(?<![\w])canonical\.[A-Za-z0-9_.:-]+",
    re.IGNORECASE,
)
_PROTOCOL_ASSIGNMENT_PATTERN = re.compile(
    r"(?:^|\s)[A-Za-z_][A-Za-z0-9_.:-]*\s*(?:=|->|:)\s*[^，。！？；]+"
)


def build_player_view(
    *,
    project_root: Path,
    package_id: str,
    state: WorldState,
    events: Sequence[WorldEvent],
    source_chapters: Sequence[Any] = (),
    manifest: Optional[Mapping[str, Any]] = None,
    manuscript: Optional[Any] = None,
    passages: Sequence[Any] = (),
) -> Dict[str, Any]:
    """Project one session into novel beats and an optional canon baseline."""

    package_manifest = dict(manifest or {})
    entry_kind = str(package_manifest.get("entry_kind") or "")
    case_id = str(
        package_manifest.get("canonical_case_id")
        or package_manifest.get("evaluation_case_id")
        or ""
    )
    canonical = (
        entry_kind == "canonical_checkpoint"
        or bool(case_id)
        or package_id == CANONICAL_PACKAGE_ID
    )
    anchors: List[CanonicalEventAnchor] = []
    case_payload: Dict[str, Any] = {}
    if canonical:
        if not case_id and package_id == CANONICAL_PACKAGE_ID:
            case_path = CANONICAL_CASE
        else:
            case_path = _CANONICAL_CASE_REGISTRY.get(case_id)
            if case_path is None:
                raise ValueError(f"未注册的 canonical case: {case_id}")
        case_payload = json.loads(
            (project_root / case_path).read_text(encoding="utf-8")
        )
        anchors = [
            CanonicalEventAnchor.parse_obj(item)
            for item in case_payload.get("canonical_events", [])
        ]

    alignment = align_canonical_events(
        anchors,
        events,
        final_state=state,
    )
    anchor_by_id = {item.event_id: item for item in anchors}
    event_alignment = {
        item.simulated_event_id: item
        for item in alignment.alignments
        if item.matched and item.simulated_event_id
    }
    checkpoint_chapter = _optional_chapter(
        state.flags.get("canonical.checkpoint_chapter")
    )
    current_chapter = checkpoint_chapter
    story_beats = []
    event_chapters: Dict[str, Optional[int]] = {}
    for event in events:
        matched = event_alignment.get(event.event_id)
        anchor = (
            anchor_by_id.get(matched.canonical_event_id)
            if matched is not None
            else None
        )
        if anchor is not None:
            current_chapter = max(current_chapter or anchor.chapter, anchor.chapter)
        event_chapters[event.event_id] = current_chapter
        if event.event_type in _INTERNAL_STORY_EVENT_TYPES:
            continue
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
    chapter_start = int(package_manifest.get("chapter_start") or 1)
    chapter_end = int(
        package_manifest.get("chapter_end")
        or package_manifest.get("checkpoint_chapter")
        or (chapter_start + max(len(source_chapters) - 1, 0))
    )
    chapters = (
        _load_original_chapters(
            project_root / CANONICAL_NOVEL,
            start=chapter_start,
            end=chapter_end,
        )
        if canonical
        else _source_chapter_projection(source_chapters)
    )
    novel_passages = [
        _novel_passage_projection(item, event_alignment, state=state)
        for item in passages
    ]
    activity_items = [
        _activity_item(
            event,
            state,
            chapter=event_chapters.get(event.event_id, checkpoint_chapter),
        )
        for event in events
    ]
    manuscript_payload = _manuscript_projection(
        manuscript,
        total_passages=len(novel_passages),
    )
    return {
        "status": "ok",
        "schema_version": "player_story_view.v2",
        "world_package_id": package_id,
        "canonical_baseline_available": canonical,
        "canon_is_human_only": canonical,
        "case_id": case_payload.get("case_id", ""),
        "checkpoint_chapter": checkpoint_chapter,
        "current_story_chapter": current_chapter,
        "manuscript": manuscript_payload,
        "novel_passages": novel_passages,
        "activity_items": activity_items,
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
    chapter: Optional[int],
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
    narration = ""
    viewpoint = "third_person"
    grounded_event_ids = [event.event_id]
    referenced_entity_ids: List[str] = []
    for presentation in event.presentation_events:
        payload = presentation.get("payload", {})
        if presentation.get("event_type") == "narration":
            narration = str(payload.get("text") or "").strip()
            viewpoint = str(payload.get("viewpoint") or "third_person")
            grounded_event_ids = [
                str(item)
                for item in payload.get("grounded_event_ids", [])
                if str(item)
            ] or [event.event_id]
            referenced_entity_ids = [
                str(item)
                for item in payload.get("referenced_entity_ids", [])
                if str(item)
            ]
        elif presentation.get("event_type") == "dialogue":
            dialogues.append(
                _dialogue_projection(payload, state)
            )
        elif presentation.get("event_type") == "system_hint":
            text = str(payload.get("text") or "").strip()
            if text:
                hints.append(text)

    authority = evidence.authority if evidence is not None else ""
    source = (
        "player"
        if authority.startswith("player_action")
        else "agent"
        if event.event_type.startswith("tool.")
        else "environment"
    )
    raw_narrative = narration or _event_narrative(
        tool_name, actor_id, event, state
    )
    narrative = (
        "世界线继续向前推进。"
        if _looks_like_protocol_log(raw_narrative)
        else _reader_safe_text(raw_narrative, state)
    )
    return {
        "beat_id": f"beat_{event.new_version:06d}",
        "event_id": event.event_id,
        "world_version": event.new_version,
        "chapter": chapter,
        "title": _reader_safe_text(
            _beat_title(tool_name, actor_id, event, state), state
        ),
        "narrative": narrative,
        "paragraphs": [narrative] if narrative else [],
        "dialogues": dialogues,
        "system_hints": [
            safe_hint
            for hint in hints
            if (safe_hint := _reader_safe_text(hint, state))
        ],
        "source": source,
        "tool_name": tool_name,
        "actor_ids": list(event.actor_ids),
        "target_ids": list(event.target_ids),
        "actor_names": [_entity_name(state, item) for item in event.actor_ids],
        "target_names": [_entity_name(state, item) for item in event.target_ids],
        "alignment": dict(alignment or {}),
        "alignment_status": "matched" if alignment else "new",
        "viewpoint": viewpoint,
        "source_event_ids": grounded_event_ids,
        "referenced_entity_ids": referenced_entity_ids,
    }


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _manuscript_projection(
    manuscript: Optional[Any], *, total_passages: int
) -> Dict[str, Any]:
    if manuscript is None:
        return {
            "manuscript_id": "",
            "status": "empty" if total_passages == 0 else "draft",
            "current_revision": 0,
            "total_passages": total_passages,
        }
    return {
        "manuscript_id": str(_value(manuscript, "manuscript_id", "")),
        "status": str(_value(manuscript, "status", "draft")),
        "current_revision": int(
            _value(manuscript, "current_revision", 0) or 0
        ),
        "total_passages": total_passages,
    }


def _novel_passage_projection(
    passage: Any,
    event_alignment: Mapping[str, Any],
    *,
    state: WorldState,
) -> Dict[str, Any]:
    source_event_ids = [
        str(item)
        for item in (_value(passage, "source_event_ids", []) or [])
        if str(item)
    ]
    alignments = [
        event_alignment[event_id].dict()
        for event_id in source_event_ids
        if event_id in event_alignment
    ]
    raw_paragraphs = [
        str(item).strip()
        for item in (_value(passage, "paragraphs", []) or [])
        if str(item).strip()
    ]
    generation_kind = _enum_value(
        _value(passage, "generation_kind", "deterministic")
    )
    paragraphs, quality_issues = _reader_paragraphs(
        raw_paragraphs,
        state,
        legacy=generation_kind == "legacy",
    )
    metadata = _value(passage, "metadata", {}) or {}
    declared_quality_issues = (
        metadata.get("quality_issues", [])
        if isinstance(metadata, Mapping)
        else []
    )
    quality_issues = list(
        dict.fromkeys(
            [
                *quality_issues,
                *[
                    str(item).strip()
                    for item in declared_quality_issues
                    if str(item).strip()
                ],
            ]
        )
    )
    dialogues = [
        _dialogue_projection(item, state)
        for item in (_value(passage, "dialogues", []) or [])
    ]
    hints = [
        safe_hint
        for item in (_value(passage, "system_hints", []) or [])
        if (safe_hint := _reader_safe_text(str(item), state))
    ]
    return {
        "passage_id": str(_value(passage, "passage_id", "")),
        "entry_id": str(_value(passage, "entry_id", "")),
        "entry_revision": int(_value(passage, "entry_revision", 0) or 0),
        "chapter": _optional_chapter(
            _value(passage, "chapter_number", None)
        ),
        "order": int(_value(passage, "manuscript_sequence", 0) or 0),
        "title": _reader_safe_text(str(_value(passage, "title", "")), state),
        "paragraphs": paragraphs,
        "narrative": "\n\n".join(paragraphs),
        "dialogues": dialogues,
        "system_hints": hints,
        "quality_issues": quality_issues,
        "reader_safe": not quality_issues,
        "source_event_ids": source_event_ids,
        "from_world_version": int(
            _value(passage, "from_world_version", 0) or 0
        ),
        "to_world_version": int(
            _value(passage, "to_world_version", 0) or 0
        ),
        "generation_kind": generation_kind,
        "generation_status": _enum_value(
            _value(passage, "generation_status", "ready")
        ),
        "revision": int(_value(passage, "current_revision", 0) or 0),
        "viewpoint": str(_value(passage, "viewpoint", "third_person")),
        "referenced_entity_ids": list(
            _value(passage, "referenced_entity_ids", []) or []
        ),
        "alignment": alignments,
        "alignment_status": "matched" if alignments else "new",
    }


def _activity_item(
    event: WorldEvent,
    state: WorldState,
    *,
    chapter: Optional[int],
) -> Dict[str, Any]:
    beat = _story_beat(event, state, chapter=chapter, alignment=None)
    if event.event_type in _INTERNAL_STORY_EVENT_TYPES or event.event_type.startswith("system."):
        kind = "system"
    elif beat["source"] == "agent":
        kind = "npc_action"
    else:
        kind = "world_change"
    return {
        "activity_id": f"activity_{event.event_id}",
        "event_id": event.event_id,
        "world_version": event.new_version,
        "chapter": chapter,
        "kind": kind,
        "summary": beat["narrative"],
        "source": beat["source"],
        "tool_name": beat["tool_name"],
        "actor_ids": beat["actor_ids"],
        "target_ids": beat["target_ids"],
        "actor_names": beat["actor_names"],
        "target_names": beat["target_names"],
        "dialogues": beat["dialogues"],
        "system_hints": beat["system_hints"],
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


def _optional_chapter(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        chapter = int(value)
    except (TypeError, ValueError):
        return None
    return chapter if chapter > 0 else None


def _entity_name(state: WorldState, entity_id: Any) -> str:
    key = str(entity_id or "")
    if key in state.characters:
        return state.characters[key].display_name
    if key in state.items:
        return state.items[key].display_name
    if key in state.locations:
        return state.locations[key].display_name
    return ""


def _safe_entity_name(
    state: WorldState,
    entity_id: Any,
    *,
    fallback: str,
) -> str:
    return _entity_name(state, entity_id) or fallback


def _dialogue_projection(
    dialogue: Any,
    state: WorldState,
) -> Dict[str, str]:
    speaker_id = str(_value(dialogue, "speaker_id", "") or "")
    to_id = str(_value(dialogue, "to_id", "") or "")
    speaker_name = _safe_entity_name(
        state,
        speaker_id,
        fallback="一名角色",
    )
    to_name = (
        _safe_entity_name(state, to_id, fallback="对方") if to_id else ""
    )
    return {
        "speaker_id": speaker_id,
        "speaker_name": speaker_name,
        "speaker": speaker_name,
        "to_id": to_id,
        "to_name": to_name,
        "to": to_name,
        "line": _reader_safe_text(str(_value(dialogue, "line", "")), state),
        "tone": _reader_safe_text(str(_value(dialogue, "tone", "")), state),
    }


def _reader_safe_text(text: str, state: WorldState) -> str:
    result = _replace_entity_ids(str(text or ""), state).strip()
    result = _INTERNAL_IDENTIFIER_PATTERN.sub("某个事物", result)
    return result.strip()


def _looks_like_protocol_log(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    identifier_count = len(_INTERNAL_IDENTIFIER_PATTERN.findall(stripped))
    assignment_count = len(_PROTOCOL_ASSIGNMENT_PATTERN.findall(stripped))
    punctuation_count = sum(stripped.count(mark) for mark in "，。！？；“”")
    return (
        (identifier_count >= 2 and punctuation_count == 0)
        or (assignment_count >= 2 and punctuation_count == 0)
        or stripped.startswith(("EVENT ", "PATCH ", "STATE ", "TOOL "))
    )


def _reader_paragraphs(
    paragraphs: Sequence[str],
    state: WorldState,
    *,
    legacy: bool,
) -> Tuple[List[str], List[str]]:
    projected: List[str] = []
    quality_issues: List[str] = []
    for paragraph in paragraphs:
        if _looks_like_protocol_log(paragraph):
            quality_issues.append("旧稿含纯协议记录，已从读者正文隐藏")
            continue
        if _INTERNAL_IDENTIFIER_PATTERN.search(paragraph):
            quality_issues.append("旧稿含内部标识，读者版已安全泛化")
        safe = _reader_safe_text(paragraph, state)
        if safe:
            projected.append(safe)
    if legacy and not projected and paragraphs:
        quality_issues.append("旧稿缺少可安全展示的文学正文")
    return projected, list(dict.fromkeys(quality_issues))


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


def _load_original_chapters(
    path: Path,
    *,
    start: int,
    end: int,
) -> List[Dict[str, Any]]:
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
            if number > end:
                current = None
                break
            current = (
                {
                    "chapter": number,
                    "title": f"第{number}章 {match.group(2).strip()}",
                    "lines": [],
                }
                if number >= start
                else None
            )
        elif current is not None and line:
            current["lines"].append(line)
    if current is not None:
        chapters.append(_finalize_chapter(current))
    return chapters


def _finalize_chapter(chapter: Dict[str, Any]) -> Dict[str, Any]:
    content = "\n\n".join(chapter.pop("lines", []))
    return {
        **chapter,
        "excerpt": content,
        "truncated": False,
    }


def _source_chapter_projection(
    source_chapters: Sequence[Any],
) -> List[Dict[str, Any]]:
    chapters: List[Dict[str, Any]] = []
    for index, value in enumerate(source_chapters):
        if isinstance(value, Mapping):
            paragraphs = [
                str(item).strip()
                for item in value.get("paragraphs", [])
                if str(item).strip()
            ]
            content = str(value.get("content") or "").strip()
            excerpt = "\n\n".join(paragraphs) or content
            chapter = int(
                value.get("index")
                or value.get("chapter")
                or index + 1
            )
            title = str(
                value.get("heading")
                or value.get("title")
                or f"第 {chapter} 章"
            )
            chapters.append(
                {
                    "chapter": chapter,
                    "title": title,
                    "excerpt": excerpt or "该世界未配置原著正文对照。",
                    "truncated": False,
                }
            )
            continue
        chapters.append(
            {
                "chapter": index + 1,
                "title": str(value),
                "excerpt": "该世界未配置原著正文对照。",
                "truncated": False,
            }
        )
    return chapters


def _chapter_placeholders(source_chapters: Sequence[Any]) -> List[Dict[str, Any]]:
    return _source_chapter_projection(source_chapters)


__all__ = ["CANONICAL_PACKAGE_ID", "build_player_view"]
