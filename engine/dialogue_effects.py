"""Trusted post-dialogue effects shared by evaluation and production runtimes."""

from __future__ import annotations

from typing import Iterable, List, Optional, TYPE_CHECKING

from world_schema import Operation, OperationKind, StatePatch, WorldEvent, WorldState

from .event import commit_event

if TYPE_CHECKING:
    from .storage import WorldStore


def commit_dialogue_perceptions(
    state: WorldState,
    events: Iterable[WorldEvent],
    *,
    store: Optional["WorldStore"] = None,
    session_id: Optional[str] = None,
) -> tuple[WorldState, List[WorldEvent]]:
    """Persist dialogue memory and world-package gates after real dialogue.

    Tool events remain the source of truth.  World packages may declare a
    small, trusted ``runtime.dialogue_effects`` table that turns a matching
    speaker/receiver/location interaction into an explicit flag.  When a
    store is provided, every derived perception event is atomically committed
    to the same authoritative session.
    """

    if (store is None) != (session_id is None):
        raise ValueError("store and session_id must be provided together")
    committed: List[WorldEvent] = []
    current = state
    for source_event in events:
        for presentation in source_event.presentation_events:
            if presentation.get("event_type") != "dialogue":
                continue
            payload = presentation.get("payload") or {}
            target_id = str(payload.get("to_id") or "")
            speaker_id = str(payload.get("speaker_id") or "")
            line = str(payload.get("line") or "").strip()
            if target_id not in current.character_psyches or not line:
                continue
            operations = [
                Operation(
                    op=OperationKind.update_psyche,
                    target_id=target_id,
                    perception="%s对我说：%s" % (speaker_id, line),
                    reason="对话进入角色工作记忆",
                )
            ]
            operations.extend(
                _dialogue_effect_operations(
                    current,
                    speaker_id=speaker_id,
                    target_id=target_id,
                )
            )
            expected_version = current.version
            event, updated = commit_event(
                current,
                action_id="perception_%s" % source_event.event_id,
                event_type="system.dialogue_perceived",
                patch=StatePatch(operations=operations),
                actor_ids=[target_id],
                target_ids=[speaker_id],
                expected_version=expected_version,
                summary="%s记住了%s的对话" % (target_id, speaker_id),
            )
            if store is not None and session_id is not None:
                store.commit_turn(
                    session_id,
                    expected_version=expected_version,
                    new_state=updated,
                    event=event,
                )
            current = updated
            committed.append(event)
    return current, committed


def _dialogue_effect_operations(
    state: WorldState,
    *,
    speaker_id: str,
    target_id: str,
) -> List[Operation]:
    effects = state.flags.get("runtime.dialogue_effects", [])
    if not isinstance(effects, list):
        return []
    speaker = state.characters.get(speaker_id)
    if speaker is None:
        return []
    operations: List[Operation] = []
    for effect in effects:
        if not isinstance(effect, dict):
            continue
        if str(effect.get("speaker_id") or "") != speaker_id:
            continue
        if str(effect.get("target_character_id") or "") != target_id:
            continue
        if str(effect.get("location_id") or "") != speaker.location_id:
            continue
        required = effect.get("required_flags", {})
        if not isinstance(required, dict) or any(
            state.flags.get(str(key)) != value for key, value in required.items()
        ):
            continue
        completion_flag = str(effect.get("completion_flag") or "")
        if completion_flag and not state.flags.get(completion_flag):
            operations.append(
                Operation(
                    op=OperationKind.set_flag,
                    path=completion_flag,
                    value=True,
                    reason="dialogue_effect:%s:%s" % (speaker_id, target_id),
                )
            )
    return operations
