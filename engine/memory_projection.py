"""从权威事件与回合历史生成可检索角色记忆。"""

from __future__ import annotations

from typing import List

from world_schema import WorldEvent, WorldState

from .persistence import PersistenceError
from .storage import WorldStore


def event_memory_content(
    event: WorldEvent,
    *,
    player_input: str,
    narration: str = "",
) -> str:
    """把事件整理为稳定、可检索的人类可读摘要。"""

    parts = []
    cleaned_input = " ".join((player_input or "").split())
    if cleaned_input:
        parts.append(f"行动：{cleaned_input}")
    cleaned_narration = " ".join((narration or "").split())
    if cleaned_narration:
        parts.append(f"结果：{cleaned_narration}")
    elif event.summary:
        parts.append(f"结果：{' '.join(event.summary.split())}")
    else:
        reasons = [
            operation.reason
            for operation in event.patch.operations
            if operation.reason
        ]
        if reasons:
            parts.append("结果：" + "；".join(reasons[:4]))
    if not parts:
        parts.append(
            f"发生事件：{event.event_type}（世界版本 {event.new_version}）"
        )
    return " ".join(parts)


def event_memory_characters(
    event: WorldEvent,
    state: WorldState,
) -> List[str]:
    """只让事件参与角色获得该情景记忆。"""

    return list(
        dict.fromkeys(
            character_id
            for character_id in (
                list(event.actor_ids) + list(event.target_ids)
            )
            if character_id in state.characters
        )
    )


def record_event_memory(
    store: WorldStore,
    session_id: str,
    state: WorldState,
    event: WorldEvent,
    *,
    player_input: str,
    narration: str = "",
    max_episodic_records: int = 500,
) -> int:
    """幂等写入单个事件的参与角色记忆。"""

    character_ids = event_memory_characters(event, state)
    if not character_ids:
        return 0
    store.record_character_memories(
        session_id,
        character_ids,
        source_event_id=event.event_id,
        world_version=event.new_version,
        content=event_memory_content(
            event,
            player_input=player_input,
            narration=narration,
        ),
        importance=0.8 if event.target_ids else 0.65,
        memory_type="episodic",
    )
    for character_id in character_ids:
        store.prune_character_memories(
            session_id,
            character_id,
            memory_type="episodic",
            max_records=max_episodic_records,
        )
    return len(character_ids)


def rebuild_session_memories(
    store: WorldStore,
    session_id: str,
) -> int:
    """从事件链和剧情历史重建一条世界线的情景记忆。"""

    state = store.get_state(session_id)
    if state is None:
        raise PersistenceError(f"会话不存在: {session_id}")
    events = store.list_events(session_id)
    turns = store.list_turns(session_id)
    turns_by_version = {}
    for turn in turns:
        status = str(turn.result.get("status") or "")
        if status in {"committed", "narrate_failed"}:
            turns_by_version[turn.world_version] = turn

    written = 0
    for event in events:
        turn = turns_by_version.get(event.new_version)
        player_input = turn.player_input if turn else ""
        narration = ""
        if turn:
            narrative = turn.result.get("narrative") or {}
            narration = str(narrative.get("narration") or "")
        written += record_event_memory(
            store,
            session_id,
            state,
            event,
            player_input=player_input,
            narration=narration,
        )
    return written
