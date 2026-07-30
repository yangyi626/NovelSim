"""从权威 WorldEvent 投影 Unity 可幂等消费的表现命令流。"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel, Field

from world_schema import OperationKind, WorldEvent


COMMANDS_PER_WORLD_VERSION = 1000
MAX_DIRECTIVES_PER_EVENT = COMMANDS_PER_WORLD_VERSION - 1


class PresentationCommand(BaseModel):
    sequence: int = Field(..., ge=1)
    command_id: str
    event_id: str
    world_version: int
    command_type: str
    actor_id: str = ""
    target_id: str = ""
    entity_id: str = ""
    location_id: str = ""
    fact_id: str = ""
    alliance_id: str = ""
    text: str = ""
    tone: str = ""
    member_ids: List[str] = Field(default_factory=list)
    payload: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"


def cursor_after_world_version(version: int) -> int:
    """快照已覆盖到 version 时，可以安全跳过该版本及以前的全部表现命令。"""

    return max(0, int(version)) * COMMANDS_PER_WORLD_VERSION + (
        COMMANDS_PER_WORLD_VERSION - 1
    )


def build_turn_presentation_events(
    event: WorldEvent,
    *,
    action=None,
    narrative=None,
) -> List[Dict[str, Any]]:
    """为普通 TurnPipeline 事件构造持久化表现指令。"""

    directives = _fallback_directives(event)
    if narrative is not None:
        for dialogue in narrative.dialogues:
            directives.append(
                {
                    "event_type": "dialogue",
                    "payload": {
                        "speaker_id": dialogue.speaker_id,
                        "to_id": dialogue.to_id or "",
                        "line": dialogue.line,
                        "tone": dialogue.tone,
                    },
                }
            )
        for hint in narrative.system_hints:
            directives.append(
                {
                    "event_type": "system_hint",
                    "payload": {"text": hint},
                }
            )
    return _deduplicate_directives(directives)


def project_presentation_commands(
    events: Iterable[WorldEvent],
    *,
    after_sequence: int = 0,
    limit: int = 100,
) -> Tuple[List[PresentationCommand], bool]:
    """稳定投影并分页；同一事件日志永远得到相同 command_id/sequence。"""

    safe_after = max(0, int(after_sequence))
    safe_limit = max(1, min(int(limit), 500))
    projected: List[PresentationCommand] = []
    for event in sorted(events, key=lambda item: item.new_version):
        directives = (
            list(event.presentation_events)
            if event.presentation_events
            else _fallback_directives(event)
        )
        if len(directives) > MAX_DIRECTIVES_PER_EVENT:
            raise ValueError(
                f"event {event.event_id} has too many presentation directives"
            )
        for index, raw in enumerate(directives, start=1):
            sequence = (
                event.new_version * COMMANDS_PER_WORLD_VERSION + index
            )
            if sequence <= safe_after:
                continue
            projected.append(
                _command_from_directive(event, index, sequence, raw)
            )
    has_more = len(projected) > safe_limit
    return projected[:safe_limit], has_more


def _command_from_directive(
    event: WorldEvent,
    index: int,
    sequence: int,
    raw: Dict[str, Any],
) -> PresentationCommand:
    event_type = str(raw.get("event_type") or "state_changed")
    payload = dict(raw.get("payload") or {})
    actor_id = _first(
        payload,
        "character_id",
        "speaker_id",
        "from_id",
    ) or (event.actor_ids[0] if event.actor_ids else "")
    target_id = _first(payload, "target_character_id", "to_id")
    entity_id = _first(payload, "item_id", "entity_id")
    return PresentationCommand(
        sequence=sequence,
        command_id=f"{event.event_id}:{index:03d}:{event_type}",
        event_id=event.event_id,
        world_version=event.new_version,
        command_type=event_type,
        actor_id=actor_id,
        target_id=target_id,
        entity_id=entity_id,
        location_id=_first(payload, "location_id", "destination_id"),
        fact_id=_first(payload, "fact_id"),
        alliance_id=_first(payload, "alliance_id"),
        text=_first(payload, "line", "text", "message"),
        tone=_first(payload, "tone"),
        member_ids=list(payload.get("member_ids") or []),
        payload=payload,
    )


def _fallback_directives(event: WorldEvent) -> List[Dict[str, Any]]:
    directives: List[Dict[str, Any]] = []
    for operation in event.patch.operations:
        if operation.op == OperationKind.move_character:
            directives.append(
                _directive(
                    "navigate",
                    character_id=operation.target_id,
                    location_id=operation.location_id,
                )
            )
        elif operation.op == OperationKind.transfer_item:
            directives.append(
                _directive(
                    "item_transferred",
                    item_id=operation.item_id or operation.path,
                    to_id=operation.target_id,
                )
            )
        elif operation.op == OperationKind.destroy_item:
            directives.append(
                _directive(
                    "item_destroyed",
                    item_id=operation.item_id or operation.path,
                    character_id=(
                        event.actor_ids[0] if event.actor_ids else ""
                    ),
                )
            )
        elif operation.op == OperationKind.update_belief:
            directives.append(
                _directive(
                    "knowledge_updated",
                    character_id=operation.target_id,
                    fact_id=operation.fact_id or operation.path,
                    confidence=operation.confidence,
                )
            )
        elif operation.op == OperationKind.form_alliance:
            alliance = dict(operation.value or {})
            directives.append(
                _directive(
                    "alliance_formed",
                    alliance_id=(
                        operation.alliance_id
                        or alliance.get("alliance_id")
                        or ""
                    ),
                    member_ids=list(alliance.get("member_ids") or []),
                    goal_key=str(alliance.get("goal_key") or ""),
                )
            )
    return directives


def _directive(event_type: str, **payload) -> Dict[str, Any]:
    return {
        "event_type": event_type,
        "payload": {
            key: value
            for key, value in payload.items()
            if value is not None
        },
    }


def _first(payload: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value):
            return str(value)
    return ""


def _deduplicate_directives(
    directives: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set()
    for directive in directives:
        payload = dict(directive.get("payload") or {})
        key = (
            str(directive.get("event_type") or ""),
            tuple(sorted((str(k), repr(v)) for k, v in payload.items())),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "event_type": str(
                    directive.get("event_type") or "state_changed"
                ),
                "payload": payload,
            }
        )
    return result
