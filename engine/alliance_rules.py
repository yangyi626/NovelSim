"""联盟成立的确定性规则。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional

from world_schema import (
    AllianceState,
    Belief,
    Operation,
    OperationKind,
    StatePatch,
    WorldState,
)

from .information_propagation import get_belief, relation_dimension


@dataclass(frozen=True)
class AllianceCheckResult:
    allowed: bool
    violations: List[str] = field(default_factory=list)
    shared_evidence_event_ids: List[str] = field(default_factory=list)

    def why(self) -> str:
        return "; ".join(self.violations)


def evaluate_alliance(
    state: WorldState,
    *,
    proposer_id: str,
    target_id: str,
    goal_key: str,
    shared_fact_id: str,
    trust_threshold: float = 0.55,
    hostility_threshold: float = 0.35,
    belief_threshold: float = 0.35,
) -> AllianceCheckResult:
    violations: List[str] = []
    proposer = state.characters.get(proposer_id)
    target = state.characters.get(target_id)
    if proposer is None or target is None:
        return AllianceCheckResult(False, ["alliance character not found"])
    if proposer_id == target_id:
        violations.append("cannot form alliance with self")
    if not proposer.is_alive or not target.is_alive:
        violations.append("alliance members must be alive")
    if (
        proposer.location_id is None
        or proposer.location_id != target.location_id
    ):
        violations.append("alliance members must be co-located")

    for source_id, other_id in (
        (proposer_id, target_id),
        (target_id, proposer_id),
    ):
        trust = relation_dimension(
            state,
            source_id,
            other_id,
            "trust",
        )
        hostility = relation_dimension(
            state,
            source_id,
            other_id,
            "hostility",
        )
        if trust < trust_threshold:
            violations.append(
                f"{source_id} trust {trust:.2f} < {trust_threshold:.2f}"
            )
        if hostility > hostility_threshold:
            violations.append(
                f"{source_id} hostility {hostility:.2f} > "
                f"{hostility_threshold:.2f}"
            )

    for character_id in (proposer_id, target_id):
        if not _has_active_goal(state, character_id, goal_key):
            violations.append(
                f"{character_id} lacks active common goal {goal_key}"
            )

    beliefs = [
        get_belief(state, character_id, shared_fact_id)
        for character_id in (proposer_id, target_id)
    ]
    for character_id, belief in zip((proposer_id, target_id), beliefs):
        if (
            belief is None
            or belief.belief == Belief.unknown
            or belief.confidence < belief_threshold
        ):
            violations.append(
                f"{character_id} belief confidence is below "
                f"{belief_threshold:.2f} for {shared_fact_id}"
            )

    shared_evidence = []
    if all(beliefs):
        shared_evidence = sorted(
            set(beliefs[0].evidence_event_ids)
            & set(beliefs[1].evidence_event_ids)
        )
    if not shared_evidence:
        violations.append("alliance requires shared evidence")

    return AllianceCheckResult(
        allowed=not violations,
        violations=violations,
        shared_evidence_event_ids=shared_evidence,
    )


def build_alliance_patch(
    state: WorldState,
    *,
    proposer_id: str,
    target_id: str,
    goal_key: str,
    shared_fact_id: str,
    causal_event_id: Optional[str] = None,
    alliance_id: Optional[str] = None,
) -> StatePatch:
    check = evaluate_alliance(
        state,
        proposer_id=proposer_id,
        target_id=target_id,
        goal_key=goal_key,
        shared_fact_id=shared_fact_id,
    )
    if not check.allowed:
        raise ValueError(check.why())
    if alliance_id is None:
        digest = hashlib.sha256(
            (
                f"{state.timeline_id}|{goal_key}|"
                f"{'|'.join(sorted([proposer_id, target_id]))}"
            ).encode("utf-8")
        ).hexdigest()[:12]
        alliance_id = f"alliance_{digest}"
    if alliance_id in state.alliances:
        raise ValueError(f"alliance already exists: {alliance_id}")

    alliance = AllianceState(
        alliance_id=alliance_id,
        member_ids=sorted([proposer_id, target_id]),
        goal_key=goal_key,
        shared_fact_ids=[shared_fact_id],
        evidence_event_ids=check.shared_evidence_event_ids,
        formed_event_id=causal_event_id,
    )
    return StatePatch(
        operations=[
            Operation(
                op=OperationKind.form_alliance,
                alliance_id=alliance_id,
                value=alliance.dict(),
                reason=(
                    f"{proposer_id} and {target_id} share goal "
                    f"{goal_key}"
                ),
            )
        ],
        notes=f"alliance formed: {alliance_id}",
    )


def _has_active_goal(
    state: WorldState,
    character_id: str,
    goal_key: str,
) -> bool:
    psyche = state.character_psyches.get(character_id)
    if psyche is None:
        return False
    return any(
        goal.status == "active"
        and not goal.achieved
        and (goal.goal_key == goal_key or goal.goal_id == goal_key)
        for goal in psyche.goals
    )
