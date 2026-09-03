"""动态小说稿件的一致性校验。

校验器只读取已提交事件和提交后的权威状态。自由文本本身不能成为事实依据；
关键变化必须通过 ``FactClaim`` 引用来源事件中的受控 operation。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from world_schema import NarrativeOutput, Operation, OperationKind, WorldEvent, WorldState

from .manuscript import FactClaim, FactClaimKind, ManuscriptRevision
from .narrative_consistency import check_narrative


_INTERNAL_TOKEN_PATTERNS = (
    re.compile(
        r"(?<![A-Za-z0-9_])(?:char|item|loc|fact|plot|arc|event|plan|goal|evidence|propagation)_[A-Za-z0-9_:-]+(?![A-Za-z0-9_])",
        re.IGNORECASE,
    ),
    re.compile(r"(?<![A-Za-z0-9_])ability\s*:\s*[A-Za-z0-9_.:-]+", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9_])canonical\.[A-Za-z0-9_.:-]+", re.IGNORECASE),
    re.compile(
        r"(?<![A-Za-z0-9_])(?:"
        + "|".join(re.escape(kind.value) for kind in OperationKind)
        + r")(?![A-Za-z0-9_])",
        re.IGNORECASE,
    ),
)
_MIN_DUPLICATE_PARAGRAPH_CHARS = 60
_ADJACENT_DUPLICATE_RATIO = 0.92


@dataclass
class ManuscriptViolation:
    severity: str
    rule_id: str
    message: str
    passage_id: str = ""
    claim_id: str = ""


@dataclass
class ManuscriptCheckResult:
    valid: bool
    violations: List[ManuscriptViolation] = field(default_factory=list)

    def errors(self) -> List[ManuscriptViolation]:
        return [item for item in self.violations if item.severity == "error"]

    def why(self) -> str:
        return "; ".join(
            "[%s]%s: %s" % (item.severity, item.rule_id, item.message)
            for item in self.violations
        )


def check_manuscript_revision(
    revision: ManuscriptRevision,
    events: Sequence[WorldEvent],
    state: WorldState,
    *,
    validate_current_state: bool = True,
) -> ManuscriptCheckResult:
    violations: List[ManuscriptViolation] = []
    ordered = list(events)
    event_by_id = {event.event_id: event for event in ordered}

    if not ordered:
        _error(violations, "source_events_required", "稿件校验需要已提交事件")
        return ManuscriptCheckResult(valid=False, violations=violations)
    if len(event_by_id) != len(ordered):
        _error(violations, "source_event_unique", "来源事件 id 不唯一")
    for event in ordered:
        if event.new_version != event.previous_version + 1:
            _error(
                violations,
                "source_event_committed",
                "事件 %s 不是单步提交版本" % event.event_id,
            )
        if event.new_version > state.version:
            _error(
                violations,
                "source_event_not_future",
                "事件 %s 晚于提交后状态" % event.event_id,
            )
    for previous, current in zip(ordered, ordered[1:]):
        if current.previous_version != previous.new_version:
            _error(
                violations,
                "source_event_continuity",
                "事件 %s 与 %s 的版本不连续" % (previous.event_id, current.event_id),
            )

    expected_ids = [event.event_id for event in ordered]
    if revision.source_event_ids != expected_ids:
        _error(
            violations,
            "revision_sources",
            "revision source_event_ids 必须与输入事件顺序一致",
        )

    known_entity_ids = (
        set(state.characters)
        | set(state.items)
        | set(state.locations)
        | set(state.facts)
        | set(state.plot)
    )
    flattened: List[str] = []
    previous_reader_paragraph: Optional[Tuple[str, str]] = None
    for passage in revision.passages:
        flattened.extend(passage.source_event_ids)
        expected_text = "\n\n".join(passage.paragraphs)
        if passage.text != expected_text:
            _error(
                violations,
                "reader_body_unique",
                "paragraphs 必须是完整且唯一的读者正文，text 必须与其严格一致",
                passage_id=passage.passage_id,
            )
        for paragraph in passage.paragraphs:
            token = _find_internal_token(paragraph)
            if token:
                _error(
                    violations,
                    "internal_token_exposed",
                    "读者正文暴露了内部标识: %s" % token,
                    passage_id=passage.passage_id,
                )
            if previous_reader_paragraph is not None and _long_paragraphs_duplicate(
                previous_reader_paragraph[1], paragraph
            ):
                _error(
                    violations,
                    "adjacent_paragraph_duplicate",
                    "相邻长段落内容重复",
                    passage_id=passage.passage_id,
                )
            previous_reader_paragraph = (passage.passage_id, paragraph)
        source_events = [
            event_by_id[event_id]
            for event_id in passage.source_event_ids
            if event_id in event_by_id
        ]
        missing = [
            event_id
            for event_id in passage.source_event_ids
            if event_id not in event_by_id
        ]
        if missing:
            _error(
                violations,
                "passage_sources_exist",
                "passage 引用了不存在的事件: %s" % "、".join(missing),
                passage_id=passage.passage_id,
            )
            continue
        if not source_events:
            _error(
                violations,
                "passage_sources_required",
                "ready passage 没有来源事件",
                passage_id=passage.passage_id,
            )
            continue
        expected_range = (
            source_events[0].new_version,
            source_events[-1].new_version,
        )
        actual_range = (passage.from_world_version, passage.to_world_version)
        if actual_range != expected_range:
            _error(
                violations,
                "passage_version_range",
                "passage 版本范围应为 v%d-v%d，实际为 v%d-v%d"
                % (*expected_range, *actual_range),
                passage_id=passage.passage_id,
            )
        for previous, current in zip(source_events, source_events[1:]):
            if current.previous_version != previous.new_version:
                _error(
                    violations,
                    "passage_source_continuity",
                    "passage 来源事件版本不连续",
                    passage_id=passage.passage_id,
                )

        for entity_id in passage.referenced_entity_ids:
            if entity_id not in known_entity_ids:
                _error(
                    violations,
                    "referenced_entity_exists",
                    "稿件引用了不存在的实体: %s" % entity_id,
                    passage_id=passage.passage_id,
                )

        narrative_result = check_narrative(
            NarrativeOutput(
                narration=passage.text,
                dialogues=passage.dialogues,
                system_hints=passage.system_hints,
                viewpoint=passage.viewpoint,
                grounded_event_ids=list(passage.source_event_ids),
                referenced_entity_ids=list(passage.referenced_entity_ids),
            ),
            source_events[0],
            state,
        )
        for item in narrative_result.violations:
            if not validate_current_state and item.rule_id in {
                "speaker_alive",
                "knowledge_leak",
            }:
                continue
            violations.append(
                ManuscriptViolation(
                    severity=item.severity,
                    rule_id=item.rule_id,
                    message=item.message,
                    passage_id=passage.passage_id,
                )
            )

        for claim in passage.fact_claims:
            _check_claim(
                claim,
                passage.passage_id,
                passage.source_event_ids,
                event_by_id,
                state,
                violations,
                validate_current_state=validate_current_state,
            )

    if flattened != revision.source_event_ids:
        _error(
            violations,
            "passage_source_partition",
            "passages 必须按顺序且不重复地覆盖 revision 来源事件",
        )

    return ManuscriptCheckResult(
        valid=not any(item.severity == "error" for item in violations),
        violations=violations,
    )


def _check_claim(
    claim: FactClaim,
    passage_id: str,
    passage_source_ids: Sequence[str],
    event_by_id: Mapping[str, WorldEvent],
    state: WorldState,
    violations: List[ManuscriptViolation],
    *,
    validate_current_state: bool = True,
) -> None:
    if any(event_id not in passage_source_ids for event_id in claim.source_event_ids):
        _error(
            violations,
            "claim_source_scope",
            "事实声明引用了 passage 之外的事件",
            passage_id=passage_id,
            claim_id=claim.claim_id,
        )
        return
    source_events = [event_by_id.get(event_id) for event_id in claim.source_event_ids]
    if any(event is None for event in source_events):
        _error(
            violations,
            "claim_source_exists",
            "事实声明引用了不存在的事件",
            passage_id=passage_id,
            claim_id=claim.claim_id,
        )
        return
    operations = [
        operation
        for event in source_events
        if event is not None
        for operation in event.patch.operations
    ]
    if not any(_claim_matches_operation(claim, operation) for operation in operations):
        _error(
            violations,
            "claim_operation_grounding",
            "事实声明无法匹配来源事件 operation",
            passage_id=passage_id,
            claim_id=claim.claim_id,
        )
        return
    if validate_current_state and not _claim_matches_state(claim, state):
        _error(
            violations,
            "claim_state_consistency",
            "事实声明与提交后的权威状态冲突",
            passage_id=passage_id,
            claim_id=claim.claim_id,
        )


def _claim_matches_operation(claim: FactClaim, operation: Operation) -> bool:
    if claim.operation_kind is not None and claim.operation_kind != operation.op:
        return False
    expected = _operation_projection(operation)
    actual = {
        "kind": claim.kind,
        "subject_id": claim.subject_id,
        "object_id": claim.object_id,
        "path": claim.path,
        "value": claim.value,
        "delta": claim.delta,
        "dimension": claim.dimension,
    }
    return all(
        _values_equal(actual[key], value)
        for key, value in expected.items()
    )


def _operation_projection(operation: Operation) -> Dict[str, Any]:
    projection: Dict[str, Any] = {
        "kind": FactClaimKind.operation,
        "subject_id": operation.target_id,
        "object_id": None,
        "path": operation.path,
        "value": operation.value,
        "delta": operation.delta,
        "dimension": operation.dimension,
    }
    if operation.op == OperationKind.set_flag:
        projection["kind"] = FactClaimKind.flag_equals
    elif operation.op == OperationKind.set_attr:
        projection["kind"] = FactClaimKind.attr_equals
        projection["subject_id"], projection["path"] = _split_path(operation.path)
    elif operation.op == OperationKind.increment_value:
        projection["kind"] = FactClaimKind.value_delta
    elif operation.op == OperationKind.move_character:
        projection.update(
            kind=FactClaimKind.character_at,
            subject_id=operation.target_id or operation.path,
            object_id=operation.location_id,
        )
    elif operation.op == OperationKind.transfer_item:
        projection.update(
            kind=FactClaimKind.item_owner,
            subject_id=operation.item_id or operation.path,
            object_id=operation.target_id,
        )
    elif operation.op == OperationKind.destroy_item:
        projection.update(
            kind=FactClaimKind.item_destroyed,
            subject_id=operation.item_id or operation.path,
            value=True,
        )
    elif operation.op == OperationKind.update_relation:
        projection.update(
            kind=FactClaimKind.relation_delta,
            subject_id=operation.source_id,
            object_id=operation.target_id,
        )
    elif operation.op == OperationKind.update_belief:
        projection.update(
            kind=FactClaimKind.belief_equals,
            subject_id=operation.target_id,
            object_id=operation.fact_id or operation.path,
            value=operation.belief.value if operation.belief else None,
        )
    elif operation.op in {OperationKind.kill_character, OperationKind.revive_character}:
        projection.update(
            kind=FactClaimKind.character_alive,
            subject_id=operation.target_id or operation.path,
            value=operation.op == OperationKind.revive_character,
        )
    elif operation.op == OperationKind.change_identity:
        projection.update(
            kind=FactClaimKind.identity_tags,
            subject_id=operation.target_id or operation.path,
            value=list(operation.tags or []),
        )
    elif operation.op in {
        OperationKind.start_plot,
        OperationKind.advance_plot,
        OperationKind.complete_plot,
    }:
        value = operation.value
        if operation.op == OperationKind.start_plot:
            value = "active"
        elif operation.op == OperationKind.complete_plot:
            value = "completed"
        projection.update(
            kind=FactClaimKind.plot_stage,
            subject_id=operation.target_id or operation.path,
            value=value,
        )
    elif operation.op == OperationKind.add_fact:
        projection.update(
            kind=FactClaimKind.fact_exists,
            subject_id=operation.fact_id or operation.path,
            value=True,
        )
    return projection


def _claim_matches_state(claim: FactClaim, state: WorldState) -> bool:
    if claim.kind == FactClaimKind.flag_equals:
        return _values_equal(state.flags.get(claim.path), claim.value)
    if claim.kind == FactClaimKind.attr_equals:
        return _values_equal(_entity_attr(state, claim.subject_id, claim.path), claim.value)
    if claim.kind == FactClaimKind.value_delta:
        return True
    if claim.kind == FactClaimKind.character_at:
        character = state.characters.get(claim.subject_id or "")
        return character is not None and character.location_id == claim.object_id
    if claim.kind == FactClaimKind.character_alive:
        character = state.characters.get(claim.subject_id or "")
        return character is not None and character.is_alive is bool(claim.value)
    if claim.kind == FactClaimKind.item_owner:
        item = state.items.get(claim.subject_id or "")
        return item is not None and item.owner_id == claim.object_id
    if claim.kind == FactClaimKind.item_destroyed:
        item = state.items.get(claim.subject_id or "")
        return (
            item is not None
            and item.quantity == 0
            and not item.accessible
            and bool(item.attrs.get("destroyed")) is bool(claim.value)
        )
    if claim.kind == FactClaimKind.relation_delta:
        return any(
            relation.source_id == claim.subject_id
            and relation.target_id == claim.object_id
            for relation in state.relations
        )
    if claim.kind == FactClaimKind.belief_equals:
        return any(
            belief.fact_id == claim.object_id and belief.belief.value == claim.value
            for belief in state.beliefs.get(claim.subject_id or "", [])
        )
    if claim.kind == FactClaimKind.identity_tags:
        character = state.characters.get(claim.subject_id or "")
        return character is not None and character.identity_tags == list(claim.value or [])
    if claim.kind == FactClaimKind.plot_stage:
        arc = state.plot.get(claim.subject_id or "")
        return arc is not None and arc.stage == claim.value
    if claim.kind == FactClaimKind.fact_exists:
        return claim.subject_id in state.facts
    return True


def _entity_attr(state: WorldState, entity_id: Optional[str], path: str) -> Any:
    if not entity_id:
        return state.flags.get(path)
    entity = (
        state.characters.get(entity_id)
        or state.items.get(entity_id)
        or state.locations.get(entity_id)
    )
    if entity is None:
        return None
    return entity.attrs.get(path)


def _split_path(path: str) -> Tuple[str, str]:
    if "." not in path:
        return path, ""
    head, rest = path.split(".", 1)
    return head, rest


def _find_internal_token(text: str) -> str:
    for pattern in _INTERNAL_TOKEN_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return ""


def _long_paragraphs_duplicate(left: str, right: str) -> bool:
    normalized_left = _normalize_paragraph(left)
    normalized_right = _normalize_paragraph(right)
    if min(len(normalized_left), len(normalized_right)) < _MIN_DUPLICATE_PARAGRAPH_CHARS:
        return False
    if normalized_left == normalized_right:
        return True
    length_ratio = min(len(normalized_left), len(normalized_right)) / max(
        len(normalized_left), len(normalized_right)
    )
    if length_ratio < 0.9:
        return False
    return (
        SequenceMatcher(None, normalized_left, normalized_right).ratio()
        >= _ADJACENT_DUPLICATE_RATIO
    )


def _normalize_paragraph(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, float) or isinstance(right, float):
        try:
            return abs(float(left) - float(right)) < 1e-9
        except (TypeError, ValueError):
            return False
    return left == right


def _error(
    violations: List[ManuscriptViolation],
    rule_id: str,
    message: str,
    *,
    passage_id: str = "",
    claim_id: str = "",
) -> None:
    violations.append(
        ManuscriptViolation(
            severity="error",
            rule_id=rule_id,
            message=message,
            passage_id=passage_id,
            claim_id=claim_id,
        )
    )
