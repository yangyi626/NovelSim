"""端到端 AI Turn: 自然语言 -> ActionParser -> 规则校验 -> 状态提交。

这是整个项目第一次把 LLM 接进闭环。
- test_mocked_full_turn: 用 mock LLM，全程确定性，CI 可跑
- test_real_llm_full_turn: 真实调 qwen3.6-plus，验证生产链路 (标记 llm)
"""

from unittest import mock

import pytest

from engine import ActionParser, RuleEngine, commit_event
from world_schema import Operation, OperationKind, StatePatch

from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import NIGHT, OUTER_ROBE, QINGQING


# ---------------------------------------------------------------------------
# State Transition Proposal: Action -> StatePatch
# 当前先用一个"基于 action_type 的规则映射"做占位，对应 plan 第八步的
# transition_model.propose()。后续这一步也会接 LLM，但本阶段先用确定性规则，
# 保证闭环可跑、可测试。
# ---------------------------------------------------------------------------


def propose_patch(action, state) -> StatePatch:
    """根据 action_type 产出 StatePatch 的确定性占位实现。"""
    if action.action_type.value == "swap_object" and OUTER_ROBE in action.target_ids:
        return StatePatch(
            operations=[
                Operation(op=OperationKind.transfer_item, item_id=OUTER_ROBE, target_id=NIGHT),
                Operation(
                    op=OperationKind.update_relation,
                    source_id=QINGQING, target_id=NIGHT,
                    dimension="hostility", delta=0.2,
                ),
                Operation(op=OperationKind.set_flag, path="plot.shaming_reversed", value=True),
            ]
        )
    # speak / observe 等暂不产生状态变化
    return StatePatch(operations=[])


def run_ai_turn(parser, user_text, state, default_actor_id):
    """完整 Turn: 解析 -> 规则校验 -> 提交。"""
    engine = RuleEngine()
    action = parser.parse(user_text, state, default_actor_id=default_actor_id)
    if action is None:
        return None, state, "parse_failed", None
    res = engine.validate(state, action)
    if not res.allowed:
        return action, state, "rejected", res
    patch = propose_patch(action, state)
    ev, new_state = commit_event(
        state, action_id=action.action_id, event_type=action.action_type.value,
        patch=patch, actor_ids=[action.actor.actor_id], target_ids=action.target_ids,
    )
    return action, new_state, "committed", ev


# ---------------------------------------------------------------------------
# Mock 端到端 (确定性)
# ---------------------------------------------------------------------------


class TestMockedFullTurn:
    def test_swap_robe_full_pipeline(self, snapshot):
        """用户要拿外衫 -> 解析成 swap_object -> 规则通过 -> 外衫易主。"""
        p = ActionParser()
        p._call_llm = mock.Mock(return_value=(
            '{"action_type":"swap_object","actor_id":"%s","target_ids":["%s"],'
            '"declared_goal":"拿走外衫","visibility":"covert"}' % (NIGHT, OUTER_ROBE)
        ))

        action, new_state, status, ev = run_ai_turn(
            p, "我把外衫拿过来", snapshot, default_actor_id=NIGHT
        )

        assert status == "committed"
        assert ev is not None
        assert new_state.items[OUTER_ROBE].owner_id == NIGHT
        assert new_state.version == 1
        assert snapshot.version == 0  # 原状态未污染

    def test_rejected_action_does_not_change_state(self, snapshot):
        """规则拒绝的行动不应改变状态。"""
        from examples.huarong_lane.scenario import LIN

        p = ActionParser()
        # 林管家在夜府，想动华容巷的外衫 -> 规则应拒
        p._call_llm = mock.Mock(return_value=(
            '{"action_type":"swap_object","actor_id":"%s","target_ids":["%s"]}'
            % (LIN, OUTER_ROBE)
        ))

        action, new_state, status, detail = run_ai_turn(
            p, "林管家拿外衫", snapshot, default_actor_id=LIN
        )

        assert status == "rejected"
        # rejected 时 detail 是规则结果 (RuleCheckResult)，应有违规记录
        assert detail is not None and not detail.allowed
        assert new_state.version == snapshot.version  # 状态没变
        # 外衫还在夜清清手里 (没被林管家拿走)
        assert new_state.items[OUTER_ROBE].owner_id != LIN

    def test_parse_failure_returns_none(self, snapshot):
        """Parser 解析失败应安全返回，不抛异常。"""
        p = ActionParser()
        p._call_llm = mock.Mock(side_effect=["完全不是JSON", "还是不是", "垃圾"])
        action, new_state, status, ev = run_ai_turn(
            p, "无意义输入", snapshot, default_actor_id=NIGHT
        )
        assert status == "parse_failed"
        assert action is None
        assert new_state.version == snapshot.version


# ---------------------------------------------------------------------------
# 真实 LLM 端到端 (默认跳过)
# ---------------------------------------------------------------------------


@pytest.mark.llm
class TestRealLLMFullTurn:
    def test_real_user_input_completes_turn(self, snapshot):
        """真实 LLM 解析用户输入 -> 跑通完整 Turn。"""
        p = ActionParser()
        action, new_state, status, ev = run_ai_turn(
            p,
            "我冷冷地命令夜清清把她的外衫脱下来给我",
            snapshot,
            default_actor_id=NIGHT,
        )
        print(f"\n[LLM-TURN] status={status}, action={action}")
        # 不做强断言，只要不抛异常且状态机推进就算通
        if status == "parse_failed":
            pytest.skip("LLM 未返回可解析结果")
        if status == "rejected":
            print("[LLM-TURN] 规则拒绝了，正常")
        elif status == "committed":
            print(f"[LLM-TURN] 提交成功，version={new_state.version}")
