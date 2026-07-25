"""端到端场景测试: 一个完整 Turn 的闭环。

对应 docs/plan.md 第四节"验收标准":
  1. 加载世界快照
  2. 提交结构化 Action
  3. 检查前置条件 (规则)
  4. 生成 StatePatch
  5. 提交 WorldEvent
  6. 更新世界状态
  7. 回放并恢复相同结果 (状态 hash 一致)
  8. 乐观锁: 并发版本冲突被拒绝
"""

import pytest

from engine import RuleEngine, commit_event, replay_events
from engine.event import CommitError, state_hash
from world_schema import Actor, Operation, OperationKind, StatePatch

from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import NIGHT, OUTER_ROBE, QINGQING


def _turn(state, action, *, event_type, patch, summary=""):
    """极简 Turn: 规则校验 -> 提交事件。"""
    engine = RuleEngine()
    res = engine.validate(state, action)
    if not res.allowed:
        return None, state, res
    ev, new_state = commit_event(
        state,
        action_id=action.action_id,
        event_type=event_type,
        patch=patch,
        actor_ids=[action.actor.actor_id],
        target_ids=action.target_ids,
        expected_version=state.version,
        summary=summary,
    )
    return ev, new_state, res


class TestMinimalLoop:
    def test_full_turn_swaps_robe_and_updates_relation(self, snapshot):
        from world_schema import Action, ActionType

        # 用户(夜轻歌)让夜清清脱外衫——原著第2章的核心行动
        action = Action(
            action_id="act_demand_robe",
            action_type=ActionType.swap_object,
            actor=Actor(actor_id=NIGHT),
            target_ids=[OUTER_ROBE],
            declared_goal="反客为主: 拿走庶妹外衫",
        )
        patch = StatePatch(
            operations=[
                Operation(
                    op=OperationKind.transfer_item,
                    item_id=OUTER_ROBE,
                    target_id=NIGHT,
                ),
                Operation(
                    op=OperationKind.update_relation,
                    source_id=QINGQING,
                    target_id=NIGHT,
                    dimension="hostility",
                    delta=0.2,
                ),
                Operation(
                    op=OperationKind.set_flag,
                    path="plot.shaming_reversed",
                    value=True,
                ),
            ]
        )

        ev, new_state, res = _turn(
            snapshot, action, event_type="cup_robe_taken", patch=patch
        )
        assert res.allowed, res.why()
        assert ev is not None
        assert new_state.version == snapshot.version + 1
        # 物品易主
        assert new_state.items[OUTER_ROBE].owner_id == NIGHT
        # 关系变化
        new_hostility = next(
            r for r in new_state.relations
            if r.source_id == QINGQING and r.target_id == NIGHT
        ).dimensions.hostility
        old_hostility = next(
            r for r in snapshot.relations
            if r.source_id == QINGQING and r.target_id == NIGHT
        ).dimensions.hostility
        assert new_hostility > old_hostility
        # 原状态未被污染
        assert snapshot.version == 0

    def test_replay_restores_identical_state(self, snapshot):
        """验收第7条: 回放相同事件得到相同状态 hash。"""
        from world_schema import Action, ActionType

        action = Action(
            action_id="act_demand_robe",
            action_type=ActionType.swap_object,
            actor=Actor(actor_id=NIGHT),
            target_ids=[OUTER_ROBE],
        )
        patch = StatePatch(
            operations=[
                Operation(
                    op=OperationKind.transfer_item,
                    item_id=OUTER_ROBE,
                    target_id=NIGHT,
                ),
            ]
        )
        ev, final, _ = _turn(snapshot, action, event_type="robe_taken", patch=patch)

        # 从同一快照重放
        replayed = replay_events(snapshot, [ev])
        assert state_hash(replayed) == state_hash(final)

    def test_replay_multi_turn_consistency(self, snapshot):
        """连续两个事件，回放仍一致。"""
        from world_schema import Action, ActionType

        # Turn 1: 拿外衫
        a1 = Action(
            action_id="act1",
            action_type=ActionType.swap_object,
            actor=Actor(actor_id=NIGHT),
            target_ids=[OUTER_ROBE],
        )
        p1 = StatePatch(
            operations=[
                Operation(
                    op=OperationKind.transfer_item,
                    item_id=OUTER_ROBE,
                    target_id=NIGHT,
                )
            ]
        )
        ev1, s1, res = _turn(snapshot, a1, event_type="robe_taken", patch=p1)
        assert res.allowed

        # Turn 2: 夜清清敌意上升
        a2 = Action(
            action_id="act2",
            action_type=ActionType.observe,
            actor=Actor(actor_id=QINGQING),
        )
        p2 = StatePatch(
            operations=[
                Operation(
                    op=OperationKind.update_relation,
                    source_id=QINGQING,
                    target_id=NIGHT,
                    dimension="hostility",
                    delta=0.3,
                )
            ]
        )
        ev2, s2, res = _turn(s1, a2, event_type="grudge_grows", patch=p2)
        assert res.allowed
        assert s2.version == 2

        # 从 v0 快照重放两个事件
        replayed = replay_events(snapshot, [ev1, ev2])
        assert state_hash(replayed) == state_hash(s2)


class TestOptimisticLock:
    def test_version_conflict_rejected(self, snapshot):
        from world_schema import Action, ActionType

        action = Action(
            action_id="act_x",
            action_type=ActionType.swap_object,
            actor=Actor(actor_id=NIGHT),
            target_ids=[OUTER_ROBE],
        )
        patch = StatePatch(
            operations=[
                Operation(
                    op=OperationKind.transfer_item,
                    item_id=OUTER_ROBE,
                    target_id=NIGHT,
                )
            ]
        )
        # 故意传错 expected_version
        with pytest.raises(CommitError):
            commit_event(
                snapshot,
                action_id=action.action_id,
                event_type="x",
                patch=patch,
                expected_version=999,
            )


class TestIllegalAction:
    def test_offscene_action_blocked_by_rules(self, snapshot):
        from world_schema import Action, ActionType

        from examples.huarong_lane.scenario import LIN

        # 林管家在夜府，试图动华容巷的外衫——应被规则拒绝
        action = Action(
            action_id="act_bad",
            action_type=ActionType.swap_object,
            actor=Actor(actor_id=LIN),
            target_ids=[OUTER_ROBE],
        )
        patch = StatePatch(operations=[])
        ev, new_state, res = _turn(snapshot, action, event_type="x", patch=patch)
        assert not res.allowed
        assert ev is None
        # 状态没变
        assert new_state.version == snapshot.version
