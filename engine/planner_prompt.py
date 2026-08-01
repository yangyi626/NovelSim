"""Shared actor-scoped prompt contract for prompted and trained planners."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .game_observation import GameObservation


PLANNER_PROMPT_VERSION = "novelsim_planner_prompt.v1"
PLANNER_SYSTEM_PROMPT = """You are the high-level NPC planner in NovelSim.
Choose exactly one grounded action from available_tools for the observed actor.
Respect visible entities, rules, constraints, capabilities, affordances, evidence, persona, goals, and feedback.
Return one PlannerDecision JSON object only. Never invent facts, entities, tools, world mutations, StatePatch, or operations. The authoritative runtime validates and commits all effects.
If no action is grounded, return intent=wait with tool_call=null."""


def planner_prompt_messages(observation: GameObservation) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                compact_observation(observation),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    ]


def compact_observation(observation: GameObservation) -> Dict[str, Any]:
    """Return only actor-visible facts required to ground one tool action."""

    payload: Dict[str, Any] = {
        "world": {
            "package_id": observation.world_package_id,
            "scenario_family": observation.scenario_family,
            "timeline_id": observation.timeline_id,
            "version": observation.world_version,
            "scene_id": observation.scene_id,
            "time": observation.world_time,
        },
        "actor": {
            "actor_id": observation.actor_id,
            "location_id": observation.actor_location_id,
            "persona_traits": list(observation.persona_traits),
            "emotion": observation.emotion,
            "emotion_intensity": observation.emotion_intensity,
        },
        "visible_characters": [
            _drop_empty({
                "id": item.character_id,
                "name": item.display_name,
                "location_id": item.location_id,
                "identity_tags": list(item.identity_tags),
                "is_alive": item.is_alive,
                "relation": dict(item.relation_from_actor),
            })
            for item in observation.visible_characters
        ],
        "visible_items": [
            _drop_empty({
                "id": item.item_id,
                "name": item.display_name,
                "owner_id": item.owner_id,
                "location_id": item.location_id,
                "quantity": item.quantity,
                "accessible": item.accessible,
            })
            for item in observation.visible_items
        ],
        "visible_locations": [
            _drop_empty({
                "id": item.location_id,
                "name": item.display_name,
                "accessible": item.accessible,
            })
            for item in observation.visible_locations
        ],
        "beliefs": [item.dict() for item in observation.beliefs],
        "goals": [item.dict() for item in observation.goals],
        "plans": [item.dict() for item in observation.plans],
        "memories": [item.dict() for item in observation.memories],
        "evidence_ids": list(observation.evidence_ids),
        "rules": list(observation.world_rules),
        "constraints": list(observation.world_constraints),
        "concepts": {
            "available": list(observation.available_concept_ids),
            "unavailable": list(observation.unavailable_concept_ids),
        },
        "capability_ids": list(observation.capability_ids),
        "visible_affordances": list(observation.visible_affordances),
        "available_tools": [
            compact_tool_schema(tool.name, tool.description, tool.parameters)
            for tool in observation.available_tools
        ],
    }
    if observation.feedback is not None:
        payload["feedback"] = _drop_empty(observation.feedback.dict())
    return _drop_empty(payload)


def compact_tool_schema(
    name: str,
    description: str,
    parameters: Dict[str, Any],
) -> Dict[str, Any]:
    properties = parameters.get("properties", {})
    compact_properties: Dict[str, Any] = {}
    for field_name, schema in sorted(properties.items()):
        kept = {
            key: schema[key]
            for key in (
                "type",
                "enum",
                "minimum",
                "maximum",
                "minLength",
                "maxLength",
            )
            if key in schema
        }
        compact_properties[field_name] = kept or {"type": "string"}
    return {
        "name": name,
        "description": description,
        "required": list(parameters.get("required", [])),
        "properties": compact_properties,
    }


def _drop_empty(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item is not None and item != "" and item != [] and item != {} and item != ()
    }
