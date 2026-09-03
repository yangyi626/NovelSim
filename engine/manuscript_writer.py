"""稿件写作协议和确定性/可选 LLM 实现。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

import openai

from world_schema import DialogueLine, NarrativeOutput, Operation, OperationKind, WorldEvent, WorldState

from .config import get_llm_config
from .llm_telemetry import call_openai_compatible, chat_generation_options
from .manuscript import (
    FactClaim,
    FactClaimKind,
    ManuscriptPassage,
    ManuscriptRevision,
    ManuscriptSource,
)
from .planner_prompt import extract_json_object


_INTERNAL_PROSE_EVENT_TYPES = {
    "system.dialogue_perceived",
    "facts.migrated",
}
_INTERNAL_PROSE_OPERATIONS = {
    OperationKind.set_flag,
    OperationKind.increment_value,
    OperationKind.update_relation,
    OperationKind.set_relation,
    OperationKind.update_belief,
    OperationKind.change_identity,
    OperationKind.start_plot,
    OperationKind.advance_plot,
    OperationKind.complete_plot,
    OperationKind.update_psyche,
    OperationKind.advance_plan,
    OperationKind.record_evidence,
    OperationKind.add_fact,
    OperationKind.record_propagation,
    OperationKind.form_alliance,
}
_LLM_PRESENTATION_FIELDS = {
    "title",
    "paragraphs",
    "text",
    "referenced_entity_ids",
    "dialogues",
    "system_hints",
    "viewpoint",
    "continuity_summary",
}


class ManuscriptWriter(Protocol):
    """将一段连续、已提交的事件投影成稿件修订。"""

    def write(
        self,
        events: Sequence[WorldEvent],
        state: WorldState,
        chapter_number: Optional[int] = None,
        previous_passage: Optional[ManuscriptPassage] = None,
    ) -> ManuscriptRevision:
        ...


class ManuscriptWriterError(RuntimeError):
    """稿件输入或模型输出无效。"""


class DeterministicManuscriptWriter:
    """不调用模型，只把已提交 operations 写成连续中文正文。"""

    def __init__(
        self,
        *,
        manuscript_id: Optional[str] = None,
        revision_number: int = 1,
        parent_revision_id: Optional[str] = None,
        events_per_passage: int = 2,
    ) -> None:
        if revision_number < 1:
            raise ValueError("revision_number must be positive")
        if events_per_passage < 1:
            raise ValueError("events_per_passage must be positive")
        self.manuscript_id = manuscript_id
        self.revision_number = revision_number
        self.parent_revision_id = parent_revision_id
        self.events_per_passage = events_per_passage

    def write(
        self,
        events: Sequence[WorldEvent],
        state: WorldState,
        chapter_number: Optional[int] = None,
        previous_passage: Optional[ManuscriptPassage] = None,
    ) -> ManuscriptRevision:
        ordered = _validated_events(events, state)
        manuscript_id = self.manuscript_id or "manuscript_%s" % state.timeline_id
        passages: List[ManuscriptPassage] = []
        for index in range(0, len(ordered), self.events_per_passage):
            batch = ordered[index : index + self.events_per_passage]
            passages.append(
                _deterministic_passage(
                    batch,
                    state,
                    passage_number=len(passages) + 1,
                    continues=previous_passage is not None or bool(passages),
                )
            )
        source_ids = [event.event_id for event in ordered]
        revision_id = _revision_id(manuscript_id, self.revision_number, source_ids)
        metadata: Dict[str, Any] = {
            "writer": "deterministic",
            "first_world_version": ordered[0].previous_version,
            "last_world_version": ordered[-1].new_version,
        }
        if chapter_number is not None:
            metadata["chapter_number"] = chapter_number
        if previous_passage is not None:
            metadata["previous_passage_id"] = previous_passage.passage_id
        return ManuscriptRevision(
            revision_id=revision_id,
            manuscript_id=manuscript_id,
            timeline_id=state.timeline_id,
            revision_number=self.revision_number,
            parent_revision_id=self.parent_revision_id,
            source=ManuscriptSource.deterministic,
            passages=passages,
            source_event_ids=source_ids,
            metadata=metadata,
        )

    def from_narrative_output(
        self,
        narrative: NarrativeOutput,
        events: Sequence[WorldEvent],
        state: WorldState,
        chapter_number: Optional[int] = None,
        previous_passage: Optional[ManuscriptPassage] = None,
    ) -> ManuscriptRevision:
        """复用已有 NarrativeOutput；此路径不会触发任何模型调用。"""

        return narrative_output_to_revision(
            narrative,
            events,
            state,
            chapter_number=chapter_number,
            previous_passage=previous_passage,
            manuscript_id=self.manuscript_id,
            revision_number=self.revision_number,
            parent_revision_id=self.parent_revision_id,
        )


ManuscriptResponseGenerator = Callable[
    [Sequence[WorldEvent], WorldState, Optional[int], Optional[ManuscriptPassage]],
    Mapping[str, Any],
]
ManuscriptRepairGenerator = Callable[
    [
        Sequence[WorldEvent],
        WorldState,
        Optional[int],
        Optional[ManuscriptPassage],
        Mapping[str, Any],
        str,
    ],
    Mapping[str, Any],
]


class LLMManuscriptWriter:
    """可选模型 writer。

    测试可注入 ``generator``；未注入时才沿用项目的 OpenAI-compatible 调用
    包装。类的构造与导入均不会发起真实请求。
    """

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        manuscript_id: Optional[str] = None,
        revision_number: int = 1,
        parent_revision_id: Optional[str] = None,
        generator: Optional[ManuscriptResponseGenerator] = None,
        repair_generator: Optional[ManuscriptRepairGenerator] = None,
        repair_attempts: int = 1,
        max_tokens: int = 2048,
        temperature: float = 0.4,
    ) -> None:
        if revision_number < 1:
            raise ValueError("revision_number must be positive")
        if repair_attempts < 0 or repair_attempts > 2:
            raise ValueError("repair_attempts must be between 0 and 2")
        self.model = model
        self.manuscript_id = manuscript_id
        self.revision_number = revision_number
        self.parent_revision_id = parent_revision_id
        self.generator = generator
        self.repair_generator = repair_generator
        self.repair_attempts = repair_attempts
        self.max_tokens = max_tokens
        self.temperature = temperature

    def write(
        self,
        events: Sequence[WorldEvent],
        state: WorldState,
        chapter_number: Optional[int] = None,
        previous_passage: Optional[ManuscriptPassage] = None,
    ) -> ManuscriptRevision:
        ordered = _validated_events(events, state)
        if self.generator is not None:
            payload = self.generator(ordered, state, chapter_number, previous_passage)
        else:
            payload = self._call_provider(ordered, state, chapter_number, previous_passage)

        last_error = ""
        for attempt in range(self.repair_attempts + 1):
            try:
                return self._revision_from_payload(
                    payload,
                    ordered,
                    state,
                    chapter_number,
                    previous_passage,
                )
            except (ManuscriptWriterError, TypeError, ValueError) as exc:
                last_error = str(exc)
                if attempt >= self.repair_attempts:
                    break
                payload = self._repair_payload(
                    ordered,
                    state,
                    chapter_number,
                    previous_passage,
                    payload,
                    last_error,
                )
        raise ManuscriptWriterError(
            "LLM manuscript response remained invalid after %d repair attempt(s): %s"
            % (self.repair_attempts, last_error)
        )

    def _revision_from_payload(
        self,
        payload: Mapping[str, Any],
        events: Sequence[WorldEvent],
        state: WorldState,
        chapter_number: Optional[int],
        previous_passage: Optional[ManuscriptPassage],
    ) -> ManuscriptRevision:
        passage_payload = _single_llm_passage(payload)
        source_ids = [event.event_id for event in events]
        manuscript_id = self.manuscript_id or "manuscript_%s" % state.timeline_id
        passage = _llm_passage_from_payload(
            passage_payload,
            events,
            state,
            manuscript_id=manuscript_id,
            revision_number=self.revision_number,
            chapter_number=chapter_number,
        )
        metadata: Dict[str, Any] = {"writer": "llm"}
        if chapter_number is not None:
            metadata["chapter_number"] = chapter_number
        if previous_passage is not None:
            metadata["previous_passage_id"] = previous_passage.passage_id
        return ManuscriptRevision(
            revision_id=_revision_id(manuscript_id, self.revision_number, source_ids),
            manuscript_id=manuscript_id,
            timeline_id=state.timeline_id,
            revision_number=self.revision_number,
            parent_revision_id=self.parent_revision_id,
            source=ManuscriptSource.llm,
            passages=[passage],
            source_event_ids=source_ids,
            writer_version="llm.v2",
            input_hash=_input_hash(events, state),
            metadata=metadata,
        )

    def _repair_payload(
        self,
        events: Sequence[WorldEvent],
        state: WorldState,
        chapter_number: Optional[int],
        previous_passage: Optional[ManuscriptPassage],
        payload: Mapping[str, Any],
        error: str,
    ) -> Mapping[str, Any]:
        if self.repair_generator is not None:
            return self.repair_generator(
                events,
                state,
                chapter_number,
                previous_passage,
                payload,
                error,
            )
        if self.generator is not None:
            return self.generator(events, state, chapter_number, previous_passage)
        return self._call_provider(
            events,
            state,
            chapter_number,
            previous_passage,
            repair_payload=payload,
            repair_error=error,
        )

    def _call_provider(
        self,
        events: Sequence[WorldEvent],
        state: WorldState,
        chapter_number: Optional[int],
        previous_passage: Optional[ManuscriptPassage],
        *,
        repair_payload: Optional[Mapping[str, Any]] = None,
        repair_error: str = "",
    ) -> Mapping[str, Any]:
        cfg = get_llm_config()
        openai.api_key = cfg.api_key
        openai.api_base = cfg.base_url
        model = self.model or cfg.model
        response = call_openai_compatible(
            openai.ChatCompletion.create,
            operation="manuscript_write",
            model=model,
            messages=_llm_messages(
                events,
                state,
                chapter_number,
                previous_passage,
                repair_payload=repair_payload,
                repair_error=repair_error,
            ),
            temperature=self.temperature,
            response_format={"type": "json_object"},
            **chat_generation_options(
                model,
                max_tokens=self.max_tokens,
                thinking=False,
            ),
        )
        raw = str(response.choices[0].message.content or "").strip()
        payload = extract_json_object(raw)
        if payload is None:
            raise ManuscriptWriterError("provider response is not a JSON object")
        return payload


def narrative_output_to_revision(
    narrative: NarrativeOutput,
    events: Sequence[WorldEvent],
    state: WorldState,
    *,
    chapter_number: Optional[int] = None,
    previous_passage: Optional[ManuscriptPassage] = None,
    manuscript_id: Optional[str] = None,
    revision_number: int = 1,
    parent_revision_id: Optional[str] = None,
) -> ManuscriptRevision:
    """把已生成的表现层输出直接包装成 revision，不进行第二次模型调用。"""

    ordered = _validated_events(events, state)
    available_ids = {event.event_id for event in ordered}
    grounded = list(narrative.grounded_event_ids) or [event.event_id for event in ordered]
    if any(event_id not in available_ids for event_id in grounded):
        raise ManuscriptWriterError("NarrativeOutput references unavailable source event")
    event_by_id = {event.event_id: event for event in ordered}
    grounded_events = [event_by_id[event_id] for event_id in grounded]
    paragraphs = _narrative_paragraphs(narrative, state)
    text = "\n\n".join(paragraphs)
    entity_ids = list(
        dict.fromkeys(
            [
                *narrative.referenced_entity_ids,
                *[line.speaker_id for line in narrative.dialogues],
                *[line.to_id for line in narrative.dialogues if line.to_id],
            ]
        )
    )
    passage = ManuscriptPassage(
        passage_id="passage_%s" % grounded[0],
        paragraphs=paragraphs,
        text=text,
        source_event_ids=grounded,
        from_world_version=grounded_events[0].new_version,
        to_world_version=grounded_events[-1].new_version,
        referenced_entity_ids=entity_ids,
        dialogues=list(narrative.dialogues),
        system_hints=list(narrative.system_hints),
        viewpoint=narrative.viewpoint,
        fact_claims=_claims_for_events(grounded_events, state),
        continuity_summary=text[-240:],
        writer_version="narrative_output.v1",
        input_hash=_input_hash(grounded_events, state),
        generation_kind=ManuscriptSource.narrative_output,
        current_revision=revision_number,
        metadata={"source": "narrative_output"},
    )
    actual_manuscript_id = manuscript_id or "manuscript_%s" % state.timeline_id
    metadata: Dict[str, Any] = {"writer": "narrative_output"}
    if narrative.system_hints:
        metadata["system_hints"] = list(narrative.system_hints)
    if chapter_number is not None:
        metadata["chapter_number"] = chapter_number
    if previous_passage is not None:
        metadata["previous_passage_id"] = previous_passage.passage_id
    return ManuscriptRevision(
        revision_id=_revision_id(actual_manuscript_id, revision_number, grounded),
        manuscript_id=actual_manuscript_id,
        timeline_id=state.timeline_id,
        revision_number=revision_number,
        parent_revision_id=parent_revision_id,
        source=ManuscriptSource.narrative_output,
        passages=[passage],
        source_event_ids=grounded,
        metadata=metadata,
    )


def _validated_events(events: Sequence[WorldEvent], state: WorldState) -> List[WorldEvent]:
    ordered = list(events)
    if not ordered:
        raise ManuscriptWriterError("manuscript writer requires committed events")
    if len({event.event_id for event in ordered}) != len(ordered):
        raise ManuscriptWriterError("source event ids must be unique")
    for event in ordered:
        if event.new_version != event.previous_version + 1:
            raise ManuscriptWriterError("source event is not a committed version transition")
        if event.new_version > state.version:
            raise ManuscriptWriterError("source event is newer than supplied state")
    for previous, current in zip(ordered, ordered[1:]):
        if current.previous_version != previous.new_version:
            raise ManuscriptWriterError("source events are not continuous")
    return ordered


def _deterministic_passage(
    events: Sequence[WorldEvent],
    state: WorldState,
    *,
    passage_number: int,
    continues: bool,
) -> ManuscriptPassage:
    paragraphs: List[str] = []
    dialogues: List[DialogueLine] = []
    referenced: List[str] = []
    for event in events:
        if event.event_type in _INTERNAL_PROSE_EVENT_TYPES:
            continue
        event_paragraphs, event_dialogues, event_entities = _event_prose(event, state)
        paragraphs.extend(event_paragraphs)
        dialogues.extend(event_dialogues)
        referenced.extend(event_entities)
    if not paragraphs:
        paragraphs.append("四周一时沉静，局势却已悄然改变。")
    text = "\n\n".join(paragraphs)
    return ManuscriptPassage(
        passage_id="passage_%s_%s" % (events[0].event_id, passage_number),
        paragraphs=paragraphs,
        text=text,
        source_event_ids=[event.event_id for event in events],
        from_world_version=events[0].new_version,
        to_world_version=events[-1].new_version,
        referenced_entity_ids=list(dict.fromkeys(referenced)),
        dialogues=dialogues,
        viewpoint="third_person",
        fact_claims=_claims_for_events(events, state),
        continuity_summary=text[-240:],
        writer_version="deterministic.v1",
        input_hash=_input_hash(events, state),
        generation_kind=ManuscriptSource.deterministic,
        current_revision=1,
        metadata={
            "first_world_version": events[0].new_version,
            "last_world_version": events[-1].new_version,
        },
    )


def _event_prose(
    event: WorldEvent,
    state: WorldState,
) -> Tuple[List[str], List[DialogueLine], List[str]]:
    paragraphs: List[str] = []
    dialogues: List[DialogueLine] = []
    referenced = _event_entity_ids(event, state)

    # 已持久化的表现事件是读者正文的首选来源；patch 只负责权威事实和 claims。
    for raw in event.presentation_events:
        event_type = str(raw.get("event_type") or "")
        payload = raw.get("payload") or {}
        if not isinstance(payload, Mapping):
            continue
        if event_type == "narration":
            narration = _sanitize_reader_text(str(payload.get("text") or ""), state)
            if narration:
                paragraphs.extend(_split_reader_paragraphs(narration))
            continue
        if event_type != "dialogue":
            continue
        try:
            line = DialogueLine.parse_obj(payload)
        except Exception:
            continue
        safe_line = line.copy(
            update={"line": _sanitize_reader_text(line.line, state)}
        )
        if not safe_line.line:
            continue
        dialogues.append(safe_line)
        referenced.extend(
            [safe_line.speaker_id, safe_line.to_id]
            if safe_line.to_id
            else [safe_line.speaker_id]
        )
        paragraphs.append(_dialogue_paragraph(safe_line, state))

    if not paragraphs:
        visible_sentences = [
            sentence
            for operation in event.patch.operations
            for sentence in [_operation_sentence(operation, state)]
            if sentence
        ]
        if visible_sentences:
            paragraphs.append("".join(visible_sentences))
        else:
            summary = _sanitize_reader_text(event.summary, state)
            if summary:
                paragraphs.extend(_split_reader_paragraphs(_ensure_sentence(summary)))
            else:
                actor_names = [
                    _entity_name(state, entity_id, kind="character")
                    for entity_id in event.actor_ids
                ]
                subject = "、".join(actor_names) or "四周"
                paragraphs.append("%s一时沉静，局势却已悄然改变。" % subject)
    return paragraphs, dialogues, referenced


def _operation_sentence(operation: Operation, state: WorldState) -> str:
    kind = operation.op
    if kind in _INTERNAL_PROSE_OPERATIONS:
        return ""
    if kind == OperationKind.move_character:
        return "%s来到%s。" % (
            _entity_name(state, operation.target_id or operation.path, kind="character"),
            _entity_name(state, operation.location_id, kind="location"),
        )
    if kind == OperationKind.transfer_item:
        item_name = _entity_name(state, operation.item_id or operation.path, kind="item")
        if operation.target_id:
            return "%s转由%s持有。" % (
                item_name,
                _entity_name(state, operation.target_id, kind="character"),
            )
        return "%s已不再由任何角色持有。" % item_name
    if kind == OperationKind.destroy_item:
        return "%s被毁去。" % _entity_name(
            state, operation.item_id or operation.path, kind="item"
        )
    if kind == OperationKind.kill_character:
        return "%s死去。" % _entity_name(
            state, operation.target_id or operation.path, kind="character"
        )
    if kind == OperationKind.revive_character:
        return "%s恢复了生命。" % _entity_name(
            state, operation.target_id or operation.path, kind="character"
        )
    if kind == OperationKind.update_relation:
        direction = "上升" if (operation.delta or 0) >= 0 else "下降"
        return "%s对%s的%s%s了。" % (
            _entity_name(state, operation.source_id),
            _entity_name(state, operation.target_id),
            _dimension_name(operation.dimension),
            direction,
        )
    if kind == OperationKind.update_belief:
        return "%s对“%s”的认知变为%s。" % (
            _entity_name(state, operation.target_id),
            _fact_name(state, operation.fact_id or operation.path),
            _belief_name(operation.belief.value if operation.belief else "unknown"),
        )
    if kind == OperationKind.change_identity:
        tags = "、".join(operation.tags or []) or "无"
        return "%s的身份标记变为%s。" % (
            _entity_name(state, operation.target_id or operation.path),
            tags,
        )
    if kind in {OperationKind.start_plot, OperationKind.advance_plot, OperationKind.complete_plot}:
        arc_id = operation.target_id or operation.path
        arc = state.plot.get(arc_id)
        title = arc.title if arc is not None else "一段未明的线索"
        if kind == OperationKind.start_plot:
            return "“%s”剧情线由此展开。" % title
        if kind == OperationKind.complete_plot:
            return "“%s”剧情线至此完成。" % title
        return "“%s”剧情线推进至%s。" % (title, operation.value)
    if kind == OperationKind.set_attr:
        entity_id, _ = _split_path(operation.path)
        if entity_id in _known_entity_ids(state) and _safe_display_scalar(operation.value):
            return "%s的状态有了变化。" % _entity_name(state, entity_id)
        return ""
    return ""


def _claims_for_events(events: Sequence[WorldEvent], state: WorldState) -> List[FactClaim]:
    claims: List[FactClaim] = []
    for event in events:
        for index, operation in enumerate(event.patch.operations, start=1):
            claims.append(_claim_for_operation(event, operation, index, state))
    return claims


def _claim_for_operation(
    event: WorldEvent,
    operation: Operation,
    index: int,
    state: WorldState,
) -> FactClaim:
    kind = FactClaimKind.operation
    subject_id: Optional[str] = operation.target_id
    object_id: Optional[str] = None
    path = operation.path
    value = operation.value
    if operation.op == OperationKind.set_flag:
        kind = FactClaimKind.flag_equals
    elif operation.op == OperationKind.set_attr:
        kind = FactClaimKind.attr_equals
        subject_id, path = _split_path(operation.path)
    elif operation.op == OperationKind.increment_value:
        kind = FactClaimKind.value_delta
    elif operation.op == OperationKind.move_character:
        kind = FactClaimKind.character_at
        subject_id = operation.target_id or operation.path
        object_id = operation.location_id
    elif operation.op == OperationKind.transfer_item:
        kind = FactClaimKind.item_owner
        subject_id = operation.item_id or operation.path
        object_id = operation.target_id
    elif operation.op == OperationKind.destroy_item:
        kind = FactClaimKind.item_destroyed
        subject_id = operation.item_id or operation.path
        value = True
    elif operation.op == OperationKind.update_relation:
        kind = FactClaimKind.relation_delta
        subject_id = operation.source_id
        object_id = operation.target_id
    elif operation.op == OperationKind.update_belief:
        kind = FactClaimKind.belief_equals
        subject_id = operation.target_id
        object_id = operation.fact_id or operation.path
        value = operation.belief.value if operation.belief else None
    elif operation.op in {OperationKind.kill_character, OperationKind.revive_character}:
        kind = FactClaimKind.character_alive
        subject_id = operation.target_id or operation.path
        value = operation.op == OperationKind.revive_character
    elif operation.op == OperationKind.change_identity:
        kind = FactClaimKind.identity_tags
        subject_id = operation.target_id or operation.path
        value = list(operation.tags or [])
    elif operation.op in {OperationKind.start_plot, OperationKind.advance_plot, OperationKind.complete_plot}:
        kind = FactClaimKind.plot_stage
        subject_id = operation.target_id or operation.path
        if operation.op == OperationKind.start_plot:
            value = "active"
        elif operation.op == OperationKind.complete_plot:
            value = "completed"
    elif operation.op == OperationKind.add_fact:
        kind = FactClaimKind.fact_exists
        subject_id = operation.fact_id or operation.path
        value = True
    return FactClaim(
        claim_id="claim_%s_%02d" % (event.event_id, index),
        kind=kind,
        source_event_ids=[event.event_id],
        subject_id=subject_id,
        object_id=object_id,
        path=path,
        value=value,
        delta=operation.delta,
        dimension=operation.dimension,
        operation_kind=operation.op,
        statement=_operation_sentence(operation, state),
    )


def _event_entity_ids(event: WorldEvent, state: WorldState) -> List[str]:
    known = _known_entity_ids(state)
    values: List[Optional[str]] = [*event.actor_ids, *event.target_ids]
    for operation in event.patch.operations:
        values.extend(
            [
                operation.target_id,
                operation.source_id,
                operation.item_id,
                operation.location_id,
            ]
        )
        head, _ = _split_path(operation.path)
        values.append(head)
    return list(dict.fromkeys(value for value in values if value and value in known))


def _known_entity_ids(state: WorldState) -> set:
    return set(state.characters) | set(state.items) | set(state.locations)


def _entity_name(
    state: WorldState,
    entity_id: Optional[str],
    *,
    kind: str = "entity",
) -> str:
    generic = {
        "character": "那人",
        "item": "那件东西",
        "location": "那处地方",
        "entity": "那个对象",
    }.get(kind, "那个对象")
    if not entity_id:
        return generic
    entity = (
        state.characters.get(entity_id)
        or state.items.get(entity_id)
        or state.locations.get(entity_id)
    )
    return entity.display_name if entity is not None else generic


def _fact_name(state: WorldState, fact_id: Optional[str]) -> str:
    if not fact_id:
        return "一件未明之事"
    fact = state.facts.get(fact_id)
    return fact.statement if fact is not None else "一件未明之事"


def _dimension_name(value: Optional[str]) -> str:
    return {
        "affection": "好感",
        "trust": "信任",
        "fear": "恐惧",
        "hostility": "敌意",
        "respect": "敬意",
        "debt": "恩怨",
    }.get(value or "", value or "关系")


def _belief_name(value: str) -> str:
    return {
        "believed_true": "确信为真",
        "suspected_true": "怀疑为真",
        "unknown": "未知",
        "suspected_false": "怀疑为假",
        "believed_false": "确信为假",
    }.get(value, value)


def _safe_display_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _split_path(path: str) -> Tuple[str, str]:
    if "." not in path:
        return path, ""
    return tuple(path.split(".", 1))  # type: ignore[return-value]


def _replace_entity_ids(text: str, state: WorldState) -> str:
    result = text
    for entity_id in sorted(_known_entity_ids(state), key=len, reverse=True):
        result = result.replace(entity_id, _entity_name(state, entity_id))
    return result


def _sanitize_reader_text(text: str, state: WorldState) -> str:
    result = _replace_entity_ids(str(text or "").strip(), state)
    # summary/presentation 中偶尔混入调试理由；正文宁可泛化，也不暴露内部 token。
    result = re.sub(
        r"(?i)\b(?:char|item|loc|fact|plot|arc|event|plan|goal|evidence|propagation)_[a-z0-9_:-]+\b",
        "那个对象",
        result,
    )
    result = re.sub(r"(?i)\b(?:ability|canonical)\s*[:.]\s*[a-z0-9_.:-]+\b", "", result)
    result = re.sub(r"\s{2,}", " ", result).strip(" ,，;；")
    return result


def _split_reader_paragraphs(text: str) -> List[str]:
    return [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]


def _dialogue_paragraph(line: DialogueLine, state: WorldState) -> str:
    speaker = _entity_name(state, line.speaker_id, kind="character")
    return "%s说：“%s”" % (speaker, _ensure_sentence(line.line))


def _narrative_paragraphs(
    narrative: NarrativeOutput,
    state: WorldState,
) -> List[str]:
    paragraphs = _split_reader_paragraphs(
        _sanitize_reader_text(narrative.narration, state)
    )
    for line in narrative.dialogues:
        safe_line = line.copy(update={"line": _sanitize_reader_text(line.line, state)})
        if safe_line.line:
            paragraphs.append(_dialogue_paragraph(safe_line, state))
    return paragraphs or ["四周一时沉静，局势却已悄然改变。"]


def _ensure_sentence(text: str) -> str:
    return text if text.endswith(("。", "！", "？")) else text + "。"


def _revision_id(manuscript_id: str, revision_number: int, event_ids: Sequence[str]) -> str:
    digest = hashlib.sha256("|".join(event_ids).encode("utf-8")).hexdigest()[:12]
    return "%s_r%d_%s" % (manuscript_id, revision_number, digest)


def _input_hash(events: Sequence[WorldEvent], state: WorldState) -> str:
    payload = {
        "timeline_id": state.timeline_id,
        "state_version": state.version,
        "events": [event.dict() for event in events],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _single_llm_passage(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ManuscriptWriterError("LLM manuscript response must be a JSON object")
    if set(payload) != {"passage"}:
        raise ManuscriptWriterError("LLM response must contain exactly one passage field")
    passage = payload.get("passage")
    if not isinstance(passage, Mapping):
        raise ManuscriptWriterError("LLM passage must be a JSON object")
    forbidden = sorted(set(passage) - _LLM_PRESENTATION_FIELDS)
    if forbidden:
        raise ManuscriptWriterError(
            "LLM passage contains server-owned fields: %s" % ", ".join(forbidden)
        )
    if "paragraphs" not in passage and "text" not in passage:
        raise ManuscriptWriterError("LLM passage requires paragraphs or text")
    return passage


def _llm_passage_from_payload(
    payload: Mapping[str, Any],
    events: Sequence[WorldEvent],
    state: WorldState,
    *,
    manuscript_id: str,
    revision_number: int,
    chapter_number: Optional[int],
) -> ManuscriptPassage:
    source_ids = [event.event_id for event in events]
    paragraphs = payload.get("paragraphs")
    text = payload.get("text")
    if paragraphs is not None:
        if not isinstance(paragraphs, list) or not all(
            isinstance(item, str) for item in paragraphs
        ):
            raise ManuscriptWriterError("LLM paragraphs must be a string list")
        normalized_paragraphs = [item.strip() for item in paragraphs if item.strip()]
    else:
        normalized_paragraphs = _split_reader_paragraphs(str(text or ""))
    normalized_text = "\n\n".join(normalized_paragraphs)
    if not normalized_text:
        raise ManuscriptWriterError("LLM passage body cannot be blank")
    if text is not None and str(text).strip() != normalized_text:
        raise ManuscriptWriterError("LLM paragraphs and text must describe one identical body")

    dialogues_payload = payload.get("dialogues") or []
    if not isinstance(dialogues_payload, list):
        raise ManuscriptWriterError("LLM dialogues must be a list")
    dialogues = [DialogueLine.parse_obj(item) for item in dialogues_payload]
    referenced = list(payload.get("referenced_entity_ids") or [])
    if not isinstance(referenced, list) or not all(
        isinstance(item, str) for item in referenced
    ):
        raise ManuscriptWriterError("LLM referenced_entity_ids must be a string list")
    referenced = list(
        dict.fromkeys(
            [
                *referenced,
                *[line.speaker_id for line in dialogues],
                *[line.to_id for line in dialogues if line.to_id],
            ]
        )
    )
    input_hash = _input_hash(events, state)
    return ManuscriptPassage(
        passage_id="passage_%s" % source_ids[0],
        manuscript_id=manuscript_id,
        chapter_number=chapter_number,
        title=str(payload.get("title") or ""),
        paragraphs=normalized_paragraphs,
        text=normalized_text,
        source_event_ids=source_ids,
        source_fingerprint=input_hash,
        from_world_version=events[0].new_version,
        to_world_version=events[-1].new_version,
        referenced_entity_ids=referenced,
        dialogues=dialogues,
        system_hints=list(payload.get("system_hints") or []),
        viewpoint=str(payload.get("viewpoint") or "third_person"),
        fact_claims=_claims_for_events(events, state),
        continuity_summary=str(payload.get("continuity_summary") or normalized_text[-240:]),
        writer_version="llm.v2",
        input_hash=input_hash,
        generation_kind=ManuscriptSource.llm,
        current_revision=revision_number,
        metadata={},
    )


def _llm_messages(
    events: Sequence[WorldEvent],
    state: WorldState,
    chapter_number: Optional[int],
    previous_passage: Optional[ManuscriptPassage],
    *,
    repair_payload: Optional[Mapping[str, Any]] = None,
    repair_error: str = "",
) -> List[Dict[str, str]]:
    entities = {
        entity_id: entity.display_name
        for collection in (state.characters, state.items, state.locations)
        for entity_id, entity in collection.items()
    }
    payload = {
        "chapter_number": chapter_number,
        "previous_passage_text": previous_passage.text if previous_passage else None,
        "entities": entities,
        "alive_character_ids": [
            entity_id for entity_id, character in state.characters.items() if character.is_alive
        ],
        "events": [event.dict() for event in events],
        "allowed_passage_fields": sorted(_LLM_PRESENTATION_FIELDS),
        "output_shape": {"passage": {"paragraphs": ["完整读者正文"]}},
    }
    if repair_payload is not None:
        payload["invalid_previous_output"] = repair_payload
        payload["repair_error"] = repair_error
    return [
        {
            "role": "system",
            "content": (
                "你是动态小说稿件编辑。只能输出表现层字段，并且必须恰好输出一个 "
                "{\"passage\": {...}} JSON。paragraphs 是完整且唯一的读者正文；如同时"
                "输出 text，它必须严格等于 paragraphs 以双换行连接的结果。dialogues "
                "只作结构化证据，对白文字也必须已经写入 paragraphs。不得输出 passage_id、"
                "manuscript_id、source_event_ids、版本、claims、hash、writer/source 等服务端"
                "字段；不得把 operation、path、reason、原始 dict/list 或内部 ID 写入正文。"
                "只能忠于已提交事件，实体使用给定 display_name。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
    ]
