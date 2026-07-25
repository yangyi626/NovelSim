"""事件提交与回放 (内存版，乐观锁)。

commit_event:
    检查 expected_version == state.version
    -> apply_patch
    -> 生成 WorldEvent (previous/new version)
    -> state.version = new_version
    -> 返回 (event, new_state)

replay_events:
    从某快照开始重放事件序列，应得到相同的最终状态 hash。
    这是"事件溯源一致性"测试的核心。
"""

from __future__ import annotations

import hashlib
import json
from typing import List, Tuple

from world_schema import StatePatch, WorldEvent, WorldState

from .patch import apply_patch


class CommitError(RuntimeError):
    """乐观锁冲突或提交失败。"""


def state_hash(state: WorldState) -> str:
    """状态的稳定哈希。用于回放一致性断言。

    排除 version (version 在回放中必然递增)，只哈希"世界事实"。
    """

    payload = state.dict()
    payload.pop("version", None)
    # 排序保证 dict 顺序不影响 hash
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def commit_event(
    state: WorldState,
    action_id: str,
    event_type: str,
    patch: StatePatch,
    *,
    actor_ids: List[str] | None = None,
    target_ids: List[str] | None = None,
    random_seed: int | None = None,
    expected_version: int | None = None,
    event_id: str | None = None,
    summary: str = "",
) -> Tuple[WorldEvent, WorldState]:
    """提交一个事件。乐观锁: expected_version 不匹配则抛 CommitError。"""

    if expected_version is not None and expected_version != state.version:
        raise CommitError(
            f"version conflict: expected {expected_version}, got {state.version}"
        )

    new_state = apply_patch(state, patch)
    new_version = state.version + 1
    new_state.version = new_version

    event = WorldEvent(
        event_id=event_id or f"event_{new_version:06d}",
        event_type=event_type,
        actor_ids=actor_ids or [],
        target_ids=target_ids or [],
        action_id=action_id,
        patch=patch,
        random_seed=random_seed,
        previous_version=state.version,
        new_version=new_version,
        summary=summary,
    )
    return event, new_state


def replay_events(
    base_snapshot: WorldState, events: List[WorldEvent]
) -> WorldState:
    """从快照重放事件序列，返回最终状态。

    约束: events 必须 previous_version 连续且 == 当前 state.version。
    """
    state = base_snapshot.copy(deep=True)
    for ev in events:
        if ev.previous_version != state.version:
            raise CommitError(
                f"replay gap: event {ev.event_id} expects prev={ev.previous_version}, "
                f"state.version={state.version}"
            )
        state = apply_patch(state, ev.patch)
        state.version = ev.new_version
    return state
