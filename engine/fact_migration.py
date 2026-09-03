"""受控的旧存档权威事实补录。

事实补录不是玩家行动，也不经过普通 Action/Tool 权限链；它只能由系统迁移
调用，并通过一条 append-only WorldEvent 原子推进会话版本。
"""

from __future__ import annotations

from typing import Mapping, Optional

from world_schema import Operation, OperationKind, StatePatch, WorldEvent, WorldFact

from .event import commit_event
from .persistence import PersistenceError, VersionConflict
from .storage import WorldStore


FACT_MIGRATION_EVENT_TYPE = "facts.migrated"


def migrate_world_facts(
    store: WorldStore,
    session_id: str,
    facts: Mapping[str, WorldFact],
    *,
    migration_id: str,
    summary: str = "补录旧存档缺失的权威世界事实",
) -> Optional[WorldEvent]:
    """幂等补录一组系统事实，返回新建或已存在的迁移事件。

    ``facts`` 必须由受信世界包提供；函数不接受任意 JSON。相同 migration_id
    重复执行不会产生第二条事件，并发提交在版本冲突后会重新观察已提交事件。
    """

    if not migration_id.strip():
        raise PersistenceError("事实迁移缺少 migration_id")
    normalized = dict(facts)
    for fact_id, fact in normalized.items():
        if not isinstance(fact, WorldFact):
            raise PersistenceError(f"事实迁移包含无效 WorldFact: {fact_id}")
        if not fact_id or fact.fact_id != fact_id:
            raise PersistenceError(f"事实迁移 fact_id 不一致: {fact_id}")

    existing_events = store.list_events(session_id)
    event_id = f"{FACT_MIGRATION_EVENT_TYPE}_{migration_id}"
    existing = next((event for event in existing_events if event.event_id == event_id), None)
    if existing is not None:
        return existing

    state = store.get_state(session_id)
    if state is None:
        raise PersistenceError(f"会话不存在: {session_id}")
    missing = {
        fact_id: fact
        for fact_id, fact in normalized.items()
        if fact_id not in state.facts
    }
    if not missing:
        return None

    patch = StatePatch(
        operations=[
            Operation(
                op=OperationKind.add_fact,
                path=fact_id,
                fact_id=fact_id,
                value=fact.dict(),
                reason="系统修复旧章节快照",
            )
            for fact_id, fact in missing.items()
        ],
        notes=summary,
        causal_evidence={"authority": "system_migration"},
    )
    event, new_state = commit_event(
        state,
        action_id=f"system_fact_migration:{migration_id}",
        event_type=FACT_MIGRATION_EVENT_TYPE,
        patch=patch,
        actor_ids=["system"],
        expected_version=state.version,
        event_id=event_id,
        summary=summary,
    )
    try:
        store.commit_turn(
            session_id,
            expected_version=state.version,
            new_state=new_state,
            event=event,
            player_input="系统快照迁移",
            turn_payload={
                "status": "system_migration",
                "event_type": FACT_MIGRATION_EVENT_TYPE,
                "migration_id": migration_id,
                "fact_ids": sorted(missing),
            },
        )
    except (VersionConflict, PersistenceError):
        raced = next(
            (item for item in store.list_events(session_id) if item.event_id == event_id),
            None,
        )
        if raced is not None:
            return raced
        raise
    return event
