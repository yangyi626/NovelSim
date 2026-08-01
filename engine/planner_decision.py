"""Structured high-level planner output.

Planner decisions may propose a :class:`ToolCall`; they can never carry a
StatePatch or world mutation.  The existing ToolRegistry and execution state
machine remain the only path to authoritative state changes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

from pydantic import BaseModel, Field, root_validator

from .agent_tools import ToolCall


class PlannerIntent(str, Enum):
    investigate = "investigate"
    share = "share"
    protect = "protect"
    negotiate = "negotiate"
    deceive = "deceive"
    ally = "ally"
    move = "move"
    interact = "interact"
    observe = "observe"
    wait = "wait"


_MUTATION_KEYS = {
    "patch",
    "state_patch",
    "expected_patch",
    "candidate_patch",
    "operations",
}


class PlannerDecision(BaseModel):
    schema_version: str = "planner_decision.v1"
    decision_id: str = Field(default_factory=lambda: uuid4().hex)
    policy_id: str
    actor_id: str
    intent: PlannerIntent
    goal_id: Optional[str] = None
    tool_call: Optional[ToolCall] = None
    evidence_ids: Tuple[str, ...] = ()
    predicted_preconditions: Tuple[str, ...] = ()
    predicted_effects: Tuple[str, ...] = ()
    fallback_intent: PlannerIntent = PlannerIntent.observe
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    reason_summary: str = Field("", max_length=500)
    fallback_reason: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"
        allow_mutation = False
        copy_on_model_validation = "deep"

    @root_validator(skip_on_failure=True)
    def _enforce_planner_boundary(cls, values):
        actor_id = values.get("actor_id")
        call = values.get("tool_call")
        intent = values.get("intent")
        if call is not None and call.actor_id != actor_id:
            raise ValueError("tool_call.actor_id must match decision actor_id")
        if intent == PlannerIntent.wait and call is not None:
            raise ValueError("wait decisions cannot contain a tool call")
        if _contains_mutation_payload(values.get("metadata", {})):
            raise ValueError("planner decisions cannot carry StatePatch payloads")
        if call is not None and _contains_mutation_payload(call.arguments):
            raise ValueError("tool arguments cannot carry StatePatch payloads")
        return values

    @classmethod
    def from_tool_call(
        cls,
        call: ToolCall,
        *,
        policy_id: str,
        intent: PlannerIntent = PlannerIntent.interact,
        confidence: float = 1.0,
        reason_summary: str = "",
    ) -> "PlannerDecision":
        return cls(
            decision_id="decision_%s" % call.call_id,
            policy_id=policy_id,
            actor_id=call.actor_id,
            intent=intent,
            tool_call=call,
            confidence=confidence,
            reason_summary=reason_summary,
        )


def _contains_mutation_payload(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in _MUTATION_KEYS:
                return True
            if _contains_mutation_payload(nested):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_mutation_payload(item) for item in value)
    return False

