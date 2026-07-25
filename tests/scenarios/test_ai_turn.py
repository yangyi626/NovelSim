"""端到端 AI Turn: 自然语言 -> ActionParser -> 规则校验 -> 状态提交。

闭环两套状态转移源:
- 确定性占位 propose_patch (mock 测试用，CI 可跑)
- TransitionProposer (LLM 驱动，标记 llm 的测试用)

安全链路: LLM 产候选 patch -> patch_validator 校验 -> 规则引擎校验 action -> commit
"""

from unittest import mock

import pytest

from engine import ActionParser, RuleEngine, TransitionProposer, commit_event
from world_schema import Operation, OperationKind, StatePatch

from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import NIGHT, OUTER_ROBE, QINGQING


# ---------------------------------------------------------------------------
# 确定性占位 propose_patch (mock 测试用)
# ---------------------------------------------------------------------------


def propose_patch(action, state) -> StatePatch:
    """根据 action_type 产出 StatePatch 的确定性占位实现。

    真实系统用 TransitionProposer (LLM)；这里保留硬编码版用于确定性测试对照。
    """
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


def run_ai_turn(parser, user_text, state, default_actor_id, *, proposer=None):
    """完整 Turn: 解析 -> 规则校验 -> (LLM/占位) 推演 -> 校验 patch -> 提交。

    proposer=None 时用确定性占位；传入 TransitionProposer 实例则用 LLM 推演。
    """
    engine = RuleEngine()
    action = parser.parse(user_text, state, default_actor_id=default_actor_id)
    if action is None:
        return None, state, "parse_failed", None
    res = engine.validate(state, action)
    if not res.allowed:
        return action, state, "rejected", res
    # 状态推演: proposer 优先 (LLM)，否则确定性占位
    if proposer is not None:
        patch = proposer.propose(action, state)
        if patch is None:
            return action, state, "propose_failed", proposer.last_error
    else:
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
        if status == "parse_failed":
            pytest.skip("LLM 未返回可解析结果")
        if status == "rejected":
            print("[LLM-TURN] 规则拒绝了，正常")
        elif status == "committed":
            print(f"[LLM-TURN] 提交成功，version={new_state.version}")

    def test_llm_proposes_rich_patch(self, snapshot):
        """LLM 驱动的状态推演: 同一个"拿外衫"，应产出比硬编码更丰富的后果。

        这是 TransitionProposer 的核心价值——不再依赖 if/else 规则映射，
        而是根据场景上下文 (在场人数、隐蔽性、关系) 智能推演。
        """
        parser = ActionParser()
        proposer = TransitionProposer()
        action, new_state, status, detail = run_ai_turn(
            parser,
            "趁着围观人群的注意力被分散，我悄悄把夜清清的外衫拿过来披在身上",
            snapshot,
            default_actor_id=NIGHT,
            proposer=proposer,
        )
        print(f"\n[LLM-PROP] status={status}")
        if status == "parse_failed":
            pytest.skip("Parser 失败")
        if status == "propose_failed":
            pytest.skip(f"Proposer 失败: {detail}")
        if status != "committed":
            print(f"[LLM-PROP] 未提交 (status={status})，可能是规则拒绝")
            return
        # 打印 LLM 推演出的所有操作
        print(f"[LLM-PROP] 提交 {len(detail.patch.operations)} 条操作:")
        for op in detail.patch.operations:
            print(f"  - {op.op.value}: {op.reason}")
        assert new_state.version == snapshot.version + 1
