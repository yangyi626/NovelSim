"""Deterministic alignment between simulated events and hidden novel canon."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from pydantic import BaseModel, Field, validator

from world_schema import WorldEvent


class _StrictModel(BaseModel):
    class Config:
        extra = "forbid"


class CanonicalEventAnchor(_StrictModel):
    """One source-backed event kept out of planner prompts."""

    event_id: str
    chapter: int = Field(ge=1)
    order: int = Field(ge=0)
    summary: str
    actor_ids: Set[str] = Field(default_factory=set)
    target_ids: Set[str] = Field(default_factory=set)
    required_target_ids: Set[str] = Field(default_factory=set)
    accepted_tools: Set[str] = Field(default_factory=set)
    accepted_event_types: Set[str] = Field(default_factory=set)
    keywords: Set[str] = Field(default_factory=set)
    required_completion_flags: Dict[str, bool] = Field(default_factory=dict)
    source_evidence_sha256: str
    weight: float = Field(1.0, gt=0.0)

    @validator("event_id", "summary", "source_evidence_sha256")
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("canonical event fields cannot be blank")
        return value


class EventAlignment(_StrictModel):
    canonical_event_id: str
    simulated_event_id: Optional[str] = None
    score: float = Field(0.0, ge=0.0, le=1.0)
    matched: bool = False
    tool_match: bool = False
    actor_match: bool = False
    target_match: bool = False
    keyword_match_rate: float = Field(0.0, ge=0.0, le=1.0)


class CanonicalAlignmentMetrics(_StrictModel):
    canonical_event_count: int = Field(ge=0)
    matched_event_count: int = Field(ge=0)
    weighted_event_recall: float = Field(ge=0.0, le=1.0)
    event_order_accuracy: float = Field(ge=0.0, le=1.0)
    actor_consistency: float = Field(ge=0.0, le=1.0)
    target_consistency: float = Field(ge=0.0, le=1.0)


class CanonicalAlignmentReport(_StrictModel):
    schema_version: str = "canonical_alignment.v1"
    alignments: List[EventAlignment]
    unmatched_simulated_event_ids: List[str]
    metrics: CanonicalAlignmentMetrics


def align_canonical_events(
    canonical: Sequence[CanonicalEventAnchor],
    simulated: Sequence[WorldEvent],
    *,
    threshold: float = 0.55,
    final_state: Optional[Any] = None,
) -> CanonicalAlignmentReport:
    """Independent weighted matching; hidden canon never reaches the planner.

    Event identity is matched without order constraints.  Ordering is scored
    afterwards, so ``event_order_accuracy`` is an actual measured outcome
    rather than a property guaranteed by the matcher.  Anchors may pin a
    server-trusted completion flag: when ``final_state`` is provided and the
    flag is absent, the anchor cannot match no matter how similar the shape.
    """

    ordered_canon = sorted(canonical, key=lambda item: (item.chapter, item.order))
    alignments: List[EventAlignment] = []
    used: Set[int] = set()
    matched_indices: List[int] = []
    for anchor in ordered_canon:
        flag_satisfied = _completion_flags_satisfied(anchor, final_state)
        best: Optional[Tuple[float, int, Dict[str, object]]] = None
        for index in range(len(simulated)):
            if index in used:
                continue
            score, detail = _event_score(anchor, simulated[index])
            if not (
                detail["tool_match"]
                and detail["actor_match"]
                and detail["target_match"]
            ):
                continue
            if not flag_satisfied:
                break
            if best is None or score > best[0]:
                best = (score, index, detail)
        if best is None or best[0] < threshold:
            alignments.append(
                EventAlignment(
                    canonical_event_id=anchor.event_id,
                    score=round(best[0], 4) if best is not None else 0.0,
                    **(best[2] if best is not None else {}),
                )
            )
            continue
        score, index, detail = best
        used.add(index)
        matched_indices.append(index)
        alignments.append(
            EventAlignment(
                canonical_event_id=anchor.event_id,
                simulated_event_id=simulated[index].event_id,
                score=round(score, 4),
                matched=True,
                **detail,
            )
        )
    matched = [item for item in alignments if item.matched]
    anchor_by_id = {item.event_id: item for item in ordered_canon}
    total_weight = sum(item.weight for item in ordered_canon)
    matched_weight = sum(
        anchor_by_id[item.canonical_event_id].weight for item in matched
    )
    actor_checks = [item.actor_match for item in matched]
    target_checks = [item.target_match for item in matched]
    order_accuracy = 1.0
    if len(matched_indices) > 1:
        correct_pairs = sum(
            left < right
            for left, right in zip(matched_indices, matched_indices[1:])
        )
        order_accuracy = correct_pairs / (len(matched_indices) - 1)
    unmatched_ids = [
        event.event_id for index, event in enumerate(simulated) if index not in used
    ]
    return CanonicalAlignmentReport(
        alignments=alignments,
        unmatched_simulated_event_ids=unmatched_ids,
        metrics=CanonicalAlignmentMetrics(
            canonical_event_count=len(ordered_canon),
            matched_event_count=len(matched),
            weighted_event_recall=_rate(matched_weight, total_weight),
            event_order_accuracy=round(order_accuracy, 4),
            actor_consistency=_rate(sum(actor_checks), len(actor_checks)),
            target_consistency=_rate(sum(target_checks), len(target_checks)),
        ),
    )


def _completion_flags_satisfied(
    anchor: CanonicalEventAnchor,
    final_state: Optional[Any],
) -> bool:
    if not anchor.required_completion_flags or final_state is None:
        return True
    flags = getattr(final_state, "flags", None)
    if not isinstance(flags, dict):
        return False
    return all(
        bool(flags.get(path)) == expected
        for path, expected in anchor.required_completion_flags.items()
    )


def _event_score(
    anchor: CanonicalEventAnchor,
    event: WorldEvent,
) -> Tuple[float, Dict[str, object]]:
    tool_name = _tool_name(event)
    accepted_mechanisms = bool(
        anchor.accepted_tools or anchor.accepted_event_types
    )
    tool_match = (
        not accepted_mechanisms
        or tool_name in anchor.accepted_tools
        or event.event_type in anchor.accepted_event_types
    )
    actors = set(event.actor_ids)
    targets = _event_target_ids(event)
    actor_match = not anchor.actor_ids or bool(anchor.actor_ids & actors)
    target_match = (
        (not anchor.target_ids or bool(anchor.target_ids & targets))
        and anchor.required_target_ids.issubset(targets)
    )
    text = (event.summary or "").lower()
    keyword_hits = sum(keyword.lower() in text for keyword in anchor.keywords)
    keyword_rate = _rate(keyword_hits, len(anchor.keywords))
    score = (
        0.45 * float(tool_match)
        + 0.25 * float(actor_match)
        + 0.15 * float(target_match)
        + 0.15 * keyword_rate
    )
    return score, {
        "tool_match": tool_match,
        "actor_match": actor_match,
        "target_match": target_match,
        "keyword_match_rate": keyword_rate,
    }


def _tool_name(event: WorldEvent) -> str:
    if event.event_type.startswith("tool."):
        return event.event_type.split(".", 1)[1]
    evidence = event.patch.causal_evidence
    return evidence.tool_name if evidence is not None else ""


def _event_target_ids(event: WorldEvent) -> Set[str]:
    targets = set(event.target_ids)
    if _tool_name(event) == "move_to" and " moved to " in event.summary:
        resolved = event.summary.rsplit(" moved to ", 1)[-1].strip()
        if resolved:
            targets.add(resolved)
    return targets


def _rate(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 1.0
    return round(float(numerator) / float(denominator), 4)
