"""规则引擎测试: 时空/物品/认知边界。"""

import pytest

from engine import RuleEngine
from world_schema import Action, Actor, ActionType

from examples.huarong_lane.scenario import (
    LIN,
    NIGHT,
    OUTER_ROBE,
    QINGQING,
    SCENE_ID,
)


@pytest.fixture
def engine():
    return RuleEngine()


class TestActor:
    def test_unknown_actor_rejected(self, snapshot, engine):
        act = Action(
            action_id="a1",
            action_type=ActionType.speak,
            actor=Actor(actor_id="ghost"),
        )
        res = engine.validate(snapshot, act)
        assert not res.allowed
        assert any(v.rule_id == "actor_exists" for v in res.violations)


class TestSpatial:
    def test_swap_robe_same_scene_ok(self, snapshot, engine):
        # 夜轻歌与夜清清同在华容巷，交换外衫合法
        act = Action(
            action_id="a1",
            action_type=ActionType.swap_object,
            actor=Actor(actor_id=NIGHT),
            target_ids=[OUTER_ROBE],
        )
        assert engine.validate(snapshot, act).allowed

    def test_lin_cannot_swap_from_yefu(self, snapshot, engine):
        # 林管家在夜府，不在华容巷，不能动华容巷里的外衫
        act = Action(
            action_id="a2",
            action_type=ActionType.swap_object,
            actor=Actor(actor_id=LIN),
            target_ids=[OUTER_ROBE],
        )
        res = engine.validate(snapshot, act)
        assert not res.allowed
        assert any(v.rule_id == "spatial" for v in res.violations)


class TestItemOwned:
    def test_use_unheld_item_rejected(self, snapshot, engine):
        # 夜轻歌不持有"解药"这种东西
        act = Action(
            action_id="a3",
            action_type=ActionType.use_item,
            actor=Actor(actor_id=NIGHT),
            target_ids=["item_antidote"],
        )
        res = engine.validate(snapshot, act)
        assert not res.allowed


class TestKnowledge:
    def test_speak_unknown_fact_rejected(self, snapshot, engine):
        # 夜清清不能"说出"自己不该知道的事实 (此处用她不知的 fact)
        act = Action(
            action_id="a4",
            action_type=ActionType.speak,
            actor=Actor(actor_id=QINGQING),
            parameters={"fact_id": "fact_night_is_transmigrator"},
        )
        res = engine.validate(snapshot, act)
        assert not res.allowed
        assert any(v.rule_id == "knowledge_boundary" for v in res.violations)

    def test_speak_known_fact_ok(self, snapshot, engine):
        # 夜清清知道下毒的事 (她自己干的)，可以提
        act = Action(
            action_id="a5",
            action_type=ActionType.speak,
            actor=Actor(actor_id=QINGQING),
            parameters={"fact_id": "fact_qingqing_poisoned_tea"},
        )
        assert engine.validate(snapshot, act).allowed
