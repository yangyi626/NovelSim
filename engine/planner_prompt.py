"""Shared actor-scoped prompt contract for prompted and trained planners."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .game_observation import GameObservation
from .planner_decision import PlannerIntent


PLANNER_PROMPT_VERSION = "novelsim_planner_prompt.v2"
PLANNER_SYSTEM_PROMPT = """You are the high-level NPC planner in NovelSim.
Choose exactly one grounded action from available_tools for the observed actor.
Respect visible entities, rules, constraints, capabilities, affordances, evidence, persona, goals, and feedback.
Return exactly one PlannerDecision JSON object and no other text.
The top-level intent MUST be one value from output_contract.intent_enum; a tool name is never an intent.
For an executable action, tool_call MUST contain exactly actor_id, tool_name, and arguments. Copy tool_name from available_tools[].tool_name and use its required argument keys.
Never invent facts, entities, tools, world mutations, StatePatch, or operations. The authoritative runtime validates and commits all effects.
If no action is grounded, return intent="wait" with tool_call=null."""


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


def extract_json_object(raw: str) -> Optional[Dict[str, Any]]:
    """Parse a planner JSON object from plain or fenced model output."""

    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        try:
            value = json.loads(fenced.group(1))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            pass
    candidate = re.search(r"\{.*\}", raw, re.DOTALL)
    if candidate:
        try:
            value = json.loads(candidate.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            pass
    return None


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
        "output_contract": {
            "schema_version": "planner_decision.v1",
            "format": "one JSON object only",
            "required_top_level_fields": ["actor_id", "intent", "tool_call"],
            "actor_id": observation.actor_id,
            "intent_enum": [intent.value for intent in PlannerIntent],
            "tool_call_contract": {
                "null_only_when_intent_is_wait": True,
                "required_fields": ["actor_id", "tool_name", "arguments"],
                "actor_id": observation.actor_id,
                "tool_name_enum": [tool.name for tool in observation.available_tools],
                "arguments": "JSON object matching the selected available tool schema",
            },
            "optional_top_level_fields": [
                "goal_id",
                "evidence_ids",
                "predicted_preconditions",
                "predicted_effects",
                "confidence",
                "reason_summary",
            ],
        },
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
        "tool_name": name,
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
