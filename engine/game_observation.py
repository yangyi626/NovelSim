"""Planner-facing, actor-scoped observation contract.

The planner never receives a mutable :class:`WorldState`.  This module projects
the authoritative snapshot into the facts that one actor can observe and the
tool schemas it may propose.  Runtime validation remains the authority for
entities, capabilities, affordances, knowledge and causal effects.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from world_schema import WorldState

from .agent_tools import ToolRegistry


class _ReadOnlyContract(BaseModel):
    class Config:
        extra = "forbid"
        allow_mutation = False
        copy_on_model_validation = "deep"


class VisibleCharacter(_ReadOnlyContract):
    character_id: str
    display_name: str
    location_id: Optional[str] = None
    identity_tags: Tuple[str, ...] = ()
    is_alive: bool = True
    relation_from_actor: Dict[str, float] = Field(default_factory=dict)


class VisibleItem(_ReadOnlyContract):
    item_id: str
    display_name: str
    owner_id: Optional[str] = None
    location_id: Optional[str] = None
    quantity: int = 1
    accessible: bool = True


class VisibleLocation(_ReadOnlyContract):
    location_id: str
    display_name: str
    accessible: bool = True


class ObservedBelief(_ReadOnlyContract):
    fact_id: str
    belief: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_type: str
    evidence_ids: Tuple[str, ...] = ()


class ObservableFact(_ReadOnlyContract):
    """A fact handle the actor may inspect without revealing its truth."""

    fact_id: str
    location_id: Optional[str] = None
    item_id: Optional[str] = None


class ObservedGoal(_ReadOnlyContract):
    goal_id: str
    description: str
    priority: float


class ObservedPlan(_ReadOnlyContract):
    plan_id: str
    goal_id: str
    current_step: int
    current_step_text: str = ""
    status: str = "active"


class ObservedMemory(_ReadOnlyContract):
    memory_id: str
    content: str
    source_type: str = "working_memory"


class AvailableTool(_ReadOnlyContract):
    name: str
    description: str
    parameters: Dict[str, Any] = Field(default_factory=dict)


class AvailableAbility(_ReadOnlyContract):
    ability_id: str
    description: str = ""


class PlannerFeedback(_ReadOnlyContract):
    previous_decision_id: Optional[str] = None
    tool_name: Optional[str] = None
    success: bool = False
    failure_code: Optional[str] = None
    summary: str = ""
    retryable: bool = False


class GameObservation(_ReadOnlyContract):
    """A versioned, serializable and actor-scoped planner input."""

    schema_version: str = "game_observation.v3"
    observation_id: str
    actor_id: str
    timeline_id: str
    world_version: int = Field(ge=0)
    authoritative_state_hash: str
    world_package_id: str = ""
    scenario_family: str = ""
    scene_id: Optional[str] = None
    world_time: str = ""
    actor_location_id: Optional[str] = None
    persona_traits: Tuple[str, ...] = ()
    emotion: str = ""
    emotion_intensity: float = Field(0.0, ge=0.0, le=1.0)
    visible_characters: Tuple[VisibleCharacter, ...] = ()
    visible_items: Tuple[VisibleItem, ...] = ()
    visible_locations: Tuple[VisibleLocation, ...] = ()
    beliefs: Tuple[ObservedBelief, ...] = ()
    observable_facts: Tuple[ObservableFact, ...] = ()
    goals: Tuple[ObservedGoal, ...] = ()
    plans: Tuple[ObservedPlan, ...] = ()
    memories: Tuple[ObservedMemory, ...] = ()
    evidence_ids: Tuple[str, ...] = ()
    world_rules: Tuple[str, ...] = ()
    world_constraints: Tuple[str, ...] = ()
    available_concept_ids: Tuple[str, ...] = ()
    unavailable_concept_ids: Tuple[str, ...] = ()
    capability_ids: Tuple[str, ...] = ()
    available_abilities: Tuple[AvailableAbility, ...] = ()
    visible_affordances: Tuple[str, ...] = ()
    available_tools: Tuple[AvailableTool, ...] = ()
    feedback: Optional[PlannerFeedback] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


def build_game_observation(
    state: WorldState,
    actor_id: str,
    registry: ToolRegistry,
    *,
    world_package_id: str = "",
    scenario_family: str = "",
    feedback: Optional[PlannerFeedback] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> GameObservation:
    """Project an authoritative state into one actor's visible planner input.

    Objective facts and other actors' beliefs are deliberately absent.  Public
    rules and accessible location identifiers remain visible so that a planner
    can choose tools without inventing world concepts or destinations.
    """

    actor = state.characters.get(actor_id)
    if actor is None:
        raise ValueError("observation actor not found: %s" % actor_id)

    psyche = state.character_psyches.get(actor_id)
    actor_location = actor.location_id
    visible_character_ids = {
        character.character_id
        for character in state.characters.values()
        if character.character_id == actor_id
        or (
            actor_location is not None
            and character.location_id == actor_location
            and character.is_alive
        )
    }
    relation_by_target: Dict[str, Dict[str, float]] = {}
    for relation in state.relations:
        if relation.source_id != actor_id:
            continue
        relation_by_target[relation.target_id] = {
            "affection": relation.dimensions.affection,
            "trust": relation.dimensions.trust,
            "fear": relation.dimensions.fear,
            "hostility": relation.dimensions.hostility,
            "respect": relation.dimensions.respect,
            "debt": relation.dimensions.debt,
        }

    visible_characters = tuple(
        VisibleCharacter(
            character_id=character.character_id,
            display_name=character.display_name,
            location_id=character.location_id,
            identity_tags=tuple(character.identity_tags),
            is_alive=character.is_alive,
            relation_from_actor=dict(
                relation_by_target.get(character.character_id, {})
            ),
        )
        for character in sorted(
            (
                state.characters[character_id]
                for character_id in visible_character_ids
            ),
            key=lambda value: value.character_id,
        )
    )
    visible_items = tuple(
        VisibleItem(
            item_id=item.item_id,
            display_name=item.display_name,
            owner_id=item.owner_id,
            location_id=item.location_id,
            quantity=item.quantity,
            accessible=item.accessible,
        )
        for item in sorted(state.items.values(), key=lambda value: value.item_id)
        if item.owner_id == actor_id
        or (
            item.owner_id in visible_character_ids
            and item.accessible
            and item.quantity > 0
        )
        or (
            item.owner_id is None
            and actor_location is not None
            and item.location_id == actor_location
        )
    )
    visible_locations = tuple(
        VisibleLocation(
            location_id=location.location_id,
            display_name=location.display_name,
            accessible=_actor_can_enter_location(state, actor_id, location.location_id),
        )
        for location in sorted(
            state.locations.values(),
            key=lambda value: value.location_id,
        )
        if location.accessible or location.location_id == actor_location
    )

    actor_beliefs = state.beliefs.get(actor_id, [])
    evidence_ids = sorted(
        evidence.evidence_id
        for evidence in state.belief_evidence.values()
        if evidence.holder_id == actor_id
    )
    evidence_by_fact: Dict[str, List[str]] = {}
    for evidence in state.belief_evidence.values():
        if evidence.holder_id == actor_id:
            evidence_by_fact.setdefault(evidence.fact_id, []).append(
                evidence.evidence_id
            )
    beliefs = tuple(
        ObservedBelief(
            fact_id=belief.fact_id,
            belief=belief.belief.value,
            confidence=belief.confidence,
            source_type=belief.source_type,
            evidence_ids=tuple(sorted(evidence_by_fact.get(belief.fact_id, []))),
        )
        for belief in sorted(actor_beliefs, key=lambda value: value.fact_id)
    )
    belief_by_fact = {belief.fact_id: belief for belief in actor_beliefs}
    observable_facts = []
    for fact in sorted(state.facts.values(), key=lambda value: value.fact_id):
        if not fact.observable:
            continue
        if fact.location_id and fact.location_id != actor_location:
            continue
        if fact.item_id:
            item = state.items.get(fact.item_id)
            if item is None:
                continue
            actor_can_access_item = (
                item.owner_id == actor_id
                or (
                    item.owner_id is None
                    and item.accessible
                    and item.quantity > 0
                    and item.location_id == actor_location
                )
            )
            if not actor_can_access_item:
                continue
        existing = belief_by_fact.get(fact.fact_id)
        if (
            existing is not None
            and existing.source_type == "observation"
            and existing.belief == fact.truth
            and existing.confidence >= fact.base_confidence
        ):
            continue
        observable_facts.append(ObservableFact(
            fact_id=fact.fact_id,
            location_id=fact.location_id,
            item_id=fact.item_id,
        ))

    goals: List[ObservedGoal] = []
    plans: List[ObservedPlan] = []
    memories: List[ObservedMemory] = []
    if psyche is not None:
        for goal in psyche.goals:
            if goal.achieved or getattr(goal, "status", "active") != "active":
                continue
            goals.append(
                ObservedGoal(
                    goal_id=goal.goal_id,
                    description=goal.description,
                    priority=goal.priority,
                )
            )
        for plan in psyche.plans:
            current_step_text = ""
            if 0 <= plan.current_step < len(plan.steps):
                current_step_text = plan.steps[plan.current_step]
            plans.append(
                ObservedPlan(
                    plan_id=plan.plan_id,
                    goal_id=plan.goal_id,
                    current_step=plan.current_step,
                    current_step_text=current_step_text,
                    status=plan.status,
                )
            )
        for index, content in enumerate(psyche.recent_perceptions):
            digest = hashlib.sha256(
                ("%s:%s:%s" % (actor_id, index, content)).encode("utf-8")
            ).hexdigest()[:16]
            memories.append(
                ObservedMemory(
                    memory_id="working_%s" % digest,
                    content=content,
                )
            )

    ability_specs = state.flags.get("runtime.ability_specs", {})
    enabled_capabilities = {
        capability.capability_id
        for capability in state.character_capabilities.get(actor_id, [])
        if capability.enabled
    }
    ready_ids = set(ready_ability_ids(state, actor_id))
    available_abilities = tuple(
        AvailableAbility(
            ability_id=ability_id,
            description=str(spec.get("summary") or ""),
        )
        for ability_id, spec in sorted(
            ability_specs.items() if isinstance(ability_specs, dict) else []
        )
        if ability_id in enabled_capabilities
        and isinstance(spec, dict)
        and str(spec.get("owner_id") or "") == actor_id
        and ability_id in ready_ids
    )
    available_tools = tuple(
        AvailableTool(
            name=name,
            description=registry.get(name).description,
            parameters=registry.get(name).parameters_schema(),
        )
        for name in registry.names()
        if registry.get(name) is not None
        and (name != "invoke_ability" or available_abilities)
    )
    visible_entity_ids = visible_character_ids | {
        item.item_id for item in visible_items
    }
    visible_affordances = sorted(
        "%s:%s" % (affordance.entity_id, affordance.action_type)
        for entity_id, affordances in state.entity_affordances.items()
        if entity_id in visible_entity_ids
        for affordance in affordances
        if affordance.enabled
    )
    state_hash = authoritative_state_hash(state)
    return GameObservation(
        observation_id="obs_%s_%s_%s" % (
            actor_id,
            state.version,
            state_hash[:16],
        ),
        actor_id=actor_id,
        timeline_id=state.timeline_id,
        world_version=state.version,
        authoritative_state_hash=state_hash,
        world_package_id=world_package_id,
        scenario_family=scenario_family,
        scene_id=state.current_scene_id,
        world_time=state.world_time,
        actor_location_id=actor_location,
        persona_traits=tuple(psyche.traits) if psyche is not None else (),
        emotion=psyche.emotion if psyche is not None else "",
        emotion_intensity=(
            psyche.emotion_intensity if psyche is not None else 0.0
        ),
        visible_characters=visible_characters,
        visible_items=visible_items,
        visible_locations=visible_locations,
        beliefs=beliefs,
        observable_facts=tuple(observable_facts),
        goals=tuple(goals),
        plans=tuple(plans),
        memories=tuple(memories),
        evidence_ids=tuple(evidence_ids),
        world_rules=tuple(rule.statement for rule in state.world_rules),
        world_constraints=tuple(
            constraint.statement for constraint in state.world_constraints
        ),
        available_concept_ids=tuple(sorted(
            concept.concept_id
            for concept in state.world_concepts.values()
            if concept.available
        )),
        unavailable_concept_ids=tuple(sorted(
            concept.concept_id
            for concept in state.world_concepts.values()
            if not concept.available
        )),
        capability_ids=tuple(sorted(
            capability.capability_id
            for capability in state.character_capabilities.get(actor_id, [])
            if capability.enabled
        )),
        available_abilities=available_abilities,
        visible_affordances=tuple(visible_affordances),
        available_tools=available_tools,
        feedback=feedback,
        metadata=dict(metadata or {}),
    )


def _actor_can_enter_location(
    state: WorldState,
    actor_id: str,
    location_id: str,
) -> bool:
    """Expose effective entry availability without leaking hidden gate names."""

    actor = state.characters[actor_id]
    location = state.locations[location_id]
    if not location.accessible:
        return False
    current = state.locations.get(actor.location_id or "")
    if (
        current is not None
        and bool(getattr(current, "blocks_ordinary_exit", False))
        and actor.location_id != location_id
    ):
        return False
    required_flag = str(getattr(location, "requires_flag", "") or "")
    if required_flag and not state.flags.get(required_flag):
        return False
    if set(location.requires_permission) - set(actor.identity_tags):
        return False
    return True


def _ability_ready(state: WorldState, actor_id: str, spec: Dict[str, Any]) -> bool:
    actor = state.characters.get(actor_id)
    target = state.characters.get(str(spec.get("target_character_id") or ""))
    if actor is None or target is None or not target.is_alive:
        return False
    move_actor = bool(spec.get("move_actor", True))
    move_target = bool(spec.get("move_target", True))
    destination_id = str(spec.get("destination_id") or "")
    if (move_actor or move_target) and destination_id not in state.locations:
        return False
    completion_flag = str(spec.get("completion_flag") or "")
    if completion_flag and state.flags.get(completion_flag):
        return False
    required_flags = spec.get("required_flags", {})
    if not isinstance(required_flags, dict) or any(
        state.flags.get(str(key)) != value
        for key, value in required_flags.items()
    ):
        return False
    actor_sources = {str(value) for value in spec.get("actor_source_locations", [])}
    target_sources = {
        str(value) for value in spec.get("target_source_locations", [])
    }
    if actor_sources and actor.location_id not in actor_sources:
        return False
    if target_sources and target.location_id not in target_sources:
        return False
    if spec.get("requires_co_location") and actor.location_id != target.location_id:
        return False
    return True


def ready_ability_ids(state: WorldState, actor_id: str) -> Tuple[str, ...]:
    """Return enabled world abilities whose deterministic gates currently pass."""

    enabled = {
        capability.capability_id
        for capability in state.character_capabilities.get(actor_id, [])
        if capability.enabled
    }
    specs = state.flags.get("runtime.ability_specs", {})
    if not isinstance(specs, dict):
        return ()
    return tuple(
        ability_id
        for ability_id, spec in sorted(specs.items())
        if ability_id in enabled
        and isinstance(spec, dict)
        and str(spec.get("owner_id") or "") == actor_id
        and _ability_ready(state, actor_id, spec)
    )


def authoritative_state_hash(state: WorldState) -> str:
    """Return a stable hash used by observations and future trajectories."""

    payload = json.loads(state.json())
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
