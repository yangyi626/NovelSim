"""带来源证据链的确定性信息传播。

LLM 只决定“是否分享、分享给谁”；置信度、冲突处理和证据记录由本模块计算。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Optional

from world_schema import (
    Belief,
    BeliefEvidence,
    CharacterBelief,
    Operation,
    OperationKind,
    PropagationRecord,
    StatePatch,
    WorldState,
)


_SOURCE_RELIABILITY: Dict[str, float] = {
    "observation": 1.0,
    "secret": 0.95,
    "inference": 0.8,
    "hearsay": 0.7,
    "unknown": 0.5,
}


class PropagationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PropagationOutcome:
    patch: StatePatch
    record: PropagationRecord
    evidence: BeliefEvidence
    previous_belief: Optional[CharacterBelief]


def get_belief(
    state: WorldState,
    character_id: str,
    fact_id: str,
) -> Optional[CharacterBelief]:
    return next(
        (
            belief
            for belief in state.beliefs.get(character_id, [])
            if belief.fact_id == fact_id
        ),
        None,
    )


def relation_dimension(
    state: WorldState,
    source_id: str,
    target_id: str,
    dimension: str,
) -> float:
    relation = next(
        (
            value
            for value in state.relations
            if value.source_id == source_id
            and value.target_id == target_id
        ),
        None,
    )
    if relation is None:
        return 0.0
    return float(getattr(relation.dimensions, dimension, 0.0) or 0.0)


def build_propagation_patch(
    state: WorldState,
    *,
    source_character_id: str,
    target_character_id: str,
    fact_id: str,
    causal_event_id: Optional[str] = None,
) -> PropagationOutcome:
    """计算一次 hearsay 传播并返回候选 Patch，不修改状态。"""

    source = state.characters.get(source_character_id)
    target = state.characters.get(target_character_id)
    if source is None or target is None:
        raise PropagationError("ENTITY_NOT_FOUND", "传播角色不存在")
    if not source.is_alive or not target.is_alive:
        raise PropagationError("PRECONDITION_FAILED", "死亡角色不能传播或接收信息")
    if source_character_id == target_character_id:
        raise PropagationError("PRECONDITION_FAILED", "不能向自己传播信息")
    if (
        source.location_id is None
        or source.location_id != target.location_id
    ):
        raise PropagationError("SPATIAL_PRECONDITION_FAILED", "传播双方不在同一地点")
    if state.facts and fact_id not in state.facts:
        raise PropagationError("ENTITY_NOT_FOUND", f"未知事实: {fact_id}")

    source_belief = get_belief(state, source_character_id, fact_id)
    if (
        source_belief is None
        or source_belief.belief == Belief.unknown
        or source_belief.confidence <= 0
    ):
        raise PropagationError(
            "KNOWLEDGE_BOUNDARY_VIOLATION",
            f"{source_character_id} 不知道事实 {fact_id}",
        )

    target_belief = get_belief(state, target_character_id, fact_id)
    source_reliability = _SOURCE_RELIABILITY.get(
        source_belief.source_type,
        0.5,
    )
    # 接收者对传播者的单向信任 [-1,1] -> [0.4,1.0]。
    trust = relation_dimension(
        state,
        target_character_id,
        source_character_id,
        "trust",
    )
    trust_normalized = max(0.0, min(1.0, (trust + 1.0) / 2.0))
    trust_factor = 0.4 + 0.6 * trust_normalized
    channel_decay = 0.8

    independent_sources = {
        evidence.source_character_id or evidence.evidence_id
        for evidence in state.belief_evidence.values()
        if evidence.holder_id == target_character_id
        and evidence.fact_id == fact_id
        and evidence.source_character_id != source_character_id
    }
    corroboration_bonus = min(0.24, 0.12 * len(independent_sources))
    conflict = bool(
        target_belief
        and target_belief.belief != Belief.unknown
        and _belief_polarity(target_belief.belief)
        != _belief_polarity(source_belief.belief)
    )
    conflict_penalty = 0.2 if conflict else 0.0
    transmitted = max(
        0.0,
        min(
            1.0,
            (
                source_belief.confidence
                * source_reliability
                * trust_factor
                * channel_decay
            )
            + corroboration_bonus
            - conflict_penalty,
        ),
    )

    resulting_belief = _belief_for_confidence(
        source_belief.belief,
        transmitted,
    )
    resulting_confidence = transmitted
    if target_belief is not None:
        if conflict and target_belief.confidence >= transmitted:
            resulting_belief = target_belief.belief
            resulting_confidence = target_belief.confidence
        elif not conflict and _belief_polarity(target_belief.belief) == _belief_polarity(
            source_belief.belief
        ):
            resulting_confidence = max(
                target_belief.confidence,
                transmitted,
            )
            resulting_belief = _belief_for_confidence(
                source_belief.belief,
                resulting_confidence,
            )

    sequence = len(state.propagation_history) + 1
    digest = hashlib.sha256(
        (
            f"{state.timeline_id}|{state.version}|{sequence}|{fact_id}|"
            f"{source_character_id}|{target_character_id}"
        ).encode("utf-8")
    ).hexdigest()[:12]
    propagation_id = f"propagation_{digest}"
    evidence_id = f"evidence_{digest}"
    event_id = causal_event_id or propagation_id
    parent_evidence_ids = [
        evidence.evidence_id
        for evidence in state.belief_evidence.values()
        if evidence.holder_id == source_character_id
        and evidence.fact_id == fact_id
    ]
    evidence = BeliefEvidence(
        evidence_id=evidence_id,
        fact_id=fact_id,
        holder_id=target_character_id,
        source_type="hearsay",
        source_character_id=source_character_id,
        source_event_id=event_id,
        parent_evidence_ids=parent_evidence_ids,
        reliability=source_reliability * channel_decay,
    )
    record = PropagationRecord(
        propagation_id=propagation_id,
        fact_id=fact_id,
        source_character_id=source_character_id,
        target_character_id=target_character_id,
        source_confidence=source_belief.confidence,
        source_reliability=source_reliability,
        trust_factor=trust_factor,
        channel_decay=channel_decay,
        corroboration_bonus=corroboration_bonus,
        conflict_penalty=conflict_penalty,
        resulting_confidence=resulting_confidence,
        resulting_belief=resulting_belief,
        evidence_id=evidence_id,
    )
    evidence_events = list(
        dict.fromkeys(
            [
                *source_belief.evidence_event_ids,
                event_id,
            ]
        )
    )
    patch = StatePatch(
        operations=[
            Operation(
                op=OperationKind.update_belief,
                target_id=target_character_id,
                fact_id=fact_id,
                belief=resulting_belief,
                confidence=round(resulting_confidence, 4),
                source_type="hearsay",
                source_character_id=source_character_id,
                source_event_id=event_id,
                evidence_event_ids=evidence_events,
                reason=f"information shared by {source_character_id}",
            ),
            Operation(
                op=OperationKind.record_evidence,
                evidence_id=evidence_id,
                value=evidence.dict(),
                reason=f"evidence for {fact_id}",
            ),
            Operation(
                op=OperationKind.record_propagation,
                propagation_id=propagation_id,
                value=record.dict(),
                reason=f"{source_character_id} -> {target_character_id}",
            ),
        ],
        notes=(
            f"{source_character_id} shares {fact_id} "
            f"with {target_character_id}"
        ),
    )
    return PropagationOutcome(
        patch=patch,
        record=record,
        evidence=evidence,
        previous_belief=target_belief,
    )


def _belief_polarity(belief: Belief) -> int:
    if belief in (Belief.believed_true, Belief.suspected_true):
        return 1
    if belief in (Belief.believed_false, Belief.suspected_false):
        return -1
    return 0


def _belief_for_confidence(source: Belief, confidence: float) -> Belief:
    polarity = _belief_polarity(source)
    if polarity > 0:
        return (
            Belief.believed_true
            if confidence >= 0.75
            else Belief.suspected_true
        )
    if polarity < 0:
        return (
            Belief.believed_false
            if confidence >= 0.75
            else Belief.suspected_false
        )
    return Belief.unknown
