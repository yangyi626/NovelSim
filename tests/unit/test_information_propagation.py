import pytest

from engine.alliance_rules import build_alliance_patch, evaluate_alliance
from engine.information_propagation import (
    PropagationError,
    build_propagation_patch,
)
from engine.patch import apply_patch
from examples.secret_letter import (
    ALLY,
    FACT_PLOT,
    GOAL_PROTECT,
    GUARD,
    STEWARD,
    build_snapshot,
)
from world_schema import Belief, CharacterBelief
from world_schema import BeliefEvidence


def test_propagation_is_deterministic_and_preserves_evidence_lineage():
    state = build_snapshot()
    state.beliefs[GUARD] = [
        CharacterBelief(
            fact_id=FACT_PLOT,
            belief=Belief.believed_true,
            confidence=1.0,
            source_type="observation",
            evidence_event_ids=["event_observe_letter"],
        )
    ]

    first = build_propagation_patch(
        state,
        source_character_id=GUARD,
        target_character_id=STEWARD,
        fact_id=FACT_PLOT,
        causal_event_id="event_share_letter",
    )
    second = build_propagation_patch(
        state,
        source_character_id=GUARD,
        target_character_id=STEWARD,
        fact_id=FACT_PLOT,
        causal_event_id="event_share_letter",
    )

    assert first.patch == second.patch
    assert first.record.resulting_confidence == pytest.approx(0.752)
    assert first.record.resulting_belief == Belief.believed_true
    updated = apply_patch(state, first.patch)
    belief = updated.beliefs[STEWARD][0]
    assert belief.evidence_event_ids == [
        "event_observe_letter",
        "event_share_letter",
    ]
    assert first.evidence.evidence_id in updated.belief_evidence
    assert updated.propagation_history == [first.record]


def test_propagation_rejects_unknown_source_knowledge():
    with pytest.raises(PropagationError) as error:
        build_propagation_patch(
            build_snapshot(),
            source_character_id=GUARD,
            target_character_id=STEWARD,
            fact_id=FACT_PLOT,
        )

    assert error.value.code == "KNOWLEDGE_BOUNDARY_VIOLATION"


def test_independent_evidence_adds_corroboration_bonus():
    state = build_snapshot()
    state.beliefs[GUARD] = [
        CharacterBelief(
            fact_id=FACT_PLOT,
            belief=Belief.believed_true,
            confidence=1.0,
            source_type="observation",
        )
    ]
    state.belief_evidence["evidence_independent"] = BeliefEvidence(
        evidence_id="evidence_independent",
        fact_id=FACT_PLOT,
        holder_id=STEWARD,
        source_type="hearsay",
        source_character_id=ALLY,
    )

    outcome = build_propagation_patch(
        state,
        source_character_id=GUARD,
        target_character_id=STEWARD,
        fact_id=FACT_PLOT,
    )

    assert outcome.record.corroboration_bonus == pytest.approx(0.12)
    assert outcome.record.resulting_confidence == pytest.approx(0.872)


def test_conflicting_belief_applies_penalty_and_keeps_stronger_prior():
    state = build_snapshot()
    state.beliefs[GUARD] = [
        CharacterBelief(
            fact_id=FACT_PLOT,
            belief=Belief.believed_true,
            confidence=1.0,
            source_type="observation",
        )
    ]
    state.beliefs[STEWARD] = [
        CharacterBelief(
            fact_id=FACT_PLOT,
            belief=Belief.believed_false,
            confidence=0.8,
            source_type="observation",
        )
    ]

    outcome = build_propagation_patch(
        state,
        source_character_id=GUARD,
        target_character_id=STEWARD,
        fact_id=FACT_PLOT,
    )

    assert outcome.record.conflict_penalty == pytest.approx(0.2)
    assert outcome.record.resulting_belief == Belief.believed_false
    assert outcome.record.resulting_confidence == pytest.approx(0.8)


def test_alliance_requires_common_goal_trust_belief_and_shared_evidence():
    state = build_snapshot()
    state.beliefs[STEWARD] = [
        CharacterBelief(
            fact_id=FACT_PLOT,
            belief=Belief.suspected_true,
            confidence=0.6,
            source_type="hearsay",
            evidence_event_ids=["event_shared"],
        )
    ]
    state.beliefs[ALLY] = [
        CharacterBelief(
            fact_id=FACT_PLOT,
            belief=Belief.suspected_true,
            confidence=0.45,
            source_type="hearsay",
            evidence_event_ids=["event_shared"],
        )
    ]

    check = evaluate_alliance(
        state,
        proposer_id=STEWARD,
        target_id=ALLY,
        goal_key=GOAL_PROTECT,
        shared_fact_id=FACT_PLOT,
    )
    assert check.allowed is True

    patch = build_alliance_patch(
        state,
        proposer_id=STEWARD,
        target_id=ALLY,
        goal_key=GOAL_PROTECT,
        shared_fact_id=FACT_PLOT,
        causal_event_id="event_alliance",
    )
    updated = apply_patch(state, patch)
    alliance = next(iter(updated.alliances.values()))
    assert alliance.member_ids == sorted([STEWARD, ALLY])
    assert alliance.evidence_event_ids == ["event_shared"]
    assert alliance.formed_event_id == "event_alliance"


def test_alliance_rejects_when_evidence_is_not_shared():
    state = build_snapshot()
    state.beliefs[STEWARD] = [
        CharacterBelief(
            fact_id=FACT_PLOT,
            belief=Belief.suspected_true,
            confidence=0.6,
            evidence_event_ids=["event_a"],
        )
    ]
    state.beliefs[ALLY] = [
        CharacterBelief(
            fact_id=FACT_PLOT,
            belief=Belief.suspected_true,
            confidence=0.6,
            evidence_event_ids=["event_b"],
        )
    ]

    check = evaluate_alliance(
        state,
        proposer_id=STEWARD,
        target_id=ALLY,
        goal_key=GOAL_PROTECT,
        shared_fact_id=FACT_PLOT,
    )

    assert check.allowed is False
    assert "alliance requires shared evidence" in check.violations
