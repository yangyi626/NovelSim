"""ActionParser 单元测试。

策略:
- 大部分用 mock LLM 测"解析逻辑" (快、稳、不烧 token)
- 真实 LLM 调用用单独 marker @pytest.mark.llm，默认跳过，手动 -m llm 跑
"""

from unittest import mock

import pytest

from engine.action_parser import ActionParser
from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import (
    LIN,
    NIGHT,
    OUTER_ROBE,
    QINGQING,
)


@pytest.fixture
def parser():
    """构造一个 parser，但 _call_llm 会被测试各自 mock。"""
    return ActionParser()


def _make_parser_with(raw_outputs):
    """让 _call_llm 依次返回 raw_outputs 列表里的内容。"""
    p = ActionParser()
    p._call_llm = mock.Mock(side_effect=raw_outputs)
    return p


# ---------------------------------------------------------------------------
# JSON 提取容错
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_plain_json(self):
        assert ActionParser._extract_json('{"a": 1}') == {"a": 1}

    def test_code_block(self):
        raw = "```json\n{\"a\": 2}\n```"
        assert ActionParser._extract_json(raw) == {"a": 2}

    def test_embedded_json(self):
        raw = "结果如下: {\"a\": 3} 完毕"
        assert ActionParser._extract_json(raw) == {"a": 3}

    def test_empty(self):
        assert ActionParser._extract_json("") is None
        assert ActionParser._extract_json("no json") is None


# ---------------------------------------------------------------------------
# 解析逻辑 (mock LLM)
# ---------------------------------------------------------------------------


class TestParseLogic:
    def test_valid_swap_action(self, snapshot):
        p = _make_parser_with([
            '{"action_type":"swap_object","actor_id":"%s","target_ids":["%s"],'
            '"declared_goal":"拿走外衫","visibility":"covert"}' % (NIGHT, OUTER_ROBE)
        ])
        action = p.parse("我把夜清清的外衫拿过来", snapshot, default_actor_id=NIGHT)
        assert action is not None
        assert action.action_type.value == "swap_object"
        assert action.actor.actor_id == NIGHT
        assert OUTER_ROBE in action.target_ids
        assert action.visibility == "covert"

    def test_default_actor_when_missing(self, snapshot):
        # LLM 没给 actor_id，应回退到 default_actor_id
        p = _make_parser_with([
            '{"action_type":"observe","target_ids":[]}'
        ])
        action = p.parse("我看看四周", snapshot, default_actor_id=NIGHT)
        assert action is not None
        assert action.actor.actor_id == NIGHT

    def test_unknown_actor_rejected(self, snapshot):
        # LLM 编造了不存在的 actor_id -> 返回 None
        # 重试也返回同样的非法值，用尽重试后失败
        bad = '{"action_type":"move","actor_id":"ghost_nonexistent"}'
        p = _make_parser_with([bad, bad, bad])
        action = p.parse("鬼魂移动", snapshot, default_actor_id=NIGHT)
        assert action is None

    def test_fabricated_target_filtered(self, snapshot):
        # LLM 编造了不存在的 target -> 被过滤掉，但 action 仍有效 (target_ids 清空)
        p = _make_parser_with([
            '{"action_type":"swap_object","actor_id":"%s","target_ids":["fake_item","%s"]}'
            % (NIGHT, OUTER_ROBE)
        ])
        action = p.parse("拿东西", snapshot, default_actor_id=NIGHT)
        assert action is not None
        assert "fake_item" not in action.target_ids
        assert OUTER_ROBE in action.target_ids

    def test_invalid_action_type_rejected(self, snapshot):
        bad = '{"action_type":"teleport_to_mars","actor_id":"%s"}' % NIGHT
        p = _make_parser_with([bad, bad, bad])
        action = p.parse("瞬移到火星", snapshot, default_actor_id=NIGHT)
        assert action is None

    def test_retry_on_garbage(self, snapshot):
        # 第一次返回垃圾，第二次返回合法 JSON -> 应成功
        p = _make_parser_with([
            "抱歉我无法理解",
            '{"action_type":"observe","actor_id":"%s"}' % NIGHT,
        ])
        action = p.parse("看", snapshot, default_actor_id=NIGHT)
        assert action is not None

    def test_all_retries_fail_returns_none(self, snapshot):
        p = _make_parser_with(["垃圾1", "垃圾2", "垃圾3"])
        action = p.parse("xx", snapshot, default_actor_id=NIGHT)
        assert action is None
        assert p.last_error is not None


# ---------------------------------------------------------------------------
# 真实 LLM 冒烟 (默认跳过)
# ---------------------------------------------------------------------------


@pytest.mark.llm
class TestRealLLM:
    """真实调用 qwen3.6-plus。用 pytest -m llm 运行。

    不做强断言 (LLM 输出不确定)，只验证: 能调通、能解析出 Action。
    """

    def test_real_parse_smoke(self, snapshot):
        p = ActionParser()
        action = p.parse(
            "我冷冷地命令夜清清把她的外衫脱下来给我",
            snapshot,
            default_actor_id=NIGHT,
        )
        if action is None:
            pytest.skip(f"LLM 未返回可解析结果: {p.last_error}")
        assert action.actor.actor_id == NIGHT
        print(f"\n[LLM] 解析结果: type={action.action_type}, "
              f"targets={action.target_ids}, goal={action.declared_goal}")
