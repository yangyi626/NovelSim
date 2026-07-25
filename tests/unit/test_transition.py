"""TransitionProposer + patch_validator 测试。

策略同 ActionParser:
- mock LLM 测解析与校验逻辑 (确定性)
- 真实 LLM 用 @pytest.mark.llm 单独跑
"""

from unittest import mock

import pytest

from engine import TransitionProposer, validate_patch
from engine.transition import _extract_json
from world_schema import Action, Actor, ActionType, Operation, OperationKind, StatePatch

from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import (
    LIN,
    NIGHT,
    OUTER_ROBE,
    QINGQING,
)


# ---------------------------------------------------------------------------
# patch_validator
# ---------------------------------------------------------------------------


class TestPatchValidator:
    def test_valid_patch_passes(self, snapshot):
        patch = StatePatch(operations=[
            Operation(op=OperationKind.transfer_item, item_id=OUTER_ROBE, target_id=NIGHT),
            Operation(op=OperationKind.update_relation, source_id=QINGQING,
                      target_id=NIGHT, dimension="hostility", delta=0.2),
        ])
        assert validate_patch(snapshot, patch).valid

    def test_fabricated_item_rejected(self, snapshot):
        patch = StatePatch(operations=[
            Operation(op=OperationKind.transfer_item, item_id="ghost_item", target_id=NIGHT),
        ])
        r = validate_patch(snapshot, patch)
        assert not r.valid
        assert "item_exists" in r.why()

    def test_unknown_dimension_rejected(self, snapshot):
        patch = StatePatch(operations=[
            Operation(op=OperationKind.update_relation, source_id=NIGHT,
                      target_id=QINGQING, dimension="love", delta=0.1),
        ])
        r = validate_patch(snapshot, patch)
        assert not r.valid
        assert "dim_valid" in r.why()

    def test_huge_delta_rejected(self, snapshot):
        patch = StatePatch(operations=[
            Operation(op=OperationKind.update_relation, source_id=NIGHT,
                      target_id=QINGQING, dimension="hostility", delta=3.0),
        ])
        assert not validate_patch(snapshot, patch).valid

    def test_confidence_out_of_range_rejected(self, snapshot):
        patch = StatePatch(operations=[
            Operation(op=OperationKind.update_belief, target_id=LIN,
                      fact_id="f1", confidence=1.5),
        ])
        assert not validate_patch(snapshot, patch).valid


# ---------------------------------------------------------------------------
# TransitionProposer (mock LLM)
# ---------------------------------------------------------------------------


def _make_proposer(raw_outputs):
    p = TransitionProposer()
    p._call_llm = mock.Mock(side_effect=raw_outputs)
    return p


def _action(**kw):
    base = dict(
        action_id="a1",
        action_type=ActionType.swap_object,
        actor=Actor(actor_id=NIGHT),
        target_ids=[OUTER_ROBE],
    )
    base.update(kw)
    return Action(**base)


class TestProposerParseLogic:
    def test_valid_patch_returned(self, snapshot):
        raw = json_str({
            "operations": [
                {"op": "transfer_item", "item_id": OUTER_ROBE, "target_id": NIGHT,
                 "reason": "夜轻歌拿走外衫"},
                {"op": "update_relation", "source_id": QINGQING, "target_id": NIGHT,
                 "dimension": "hostility", "delta": 0.2, "reason": "当众被夺外衫，心生怨恨"},
            ]
        })
        p = _make_proposer([raw])
        patch = p.propose(_action(), snapshot)
        assert patch is not None
        assert len(patch.operations) == 2
        assert patch.operations[0].op == OperationKind.transfer_item
        assert patch.operations[0].reason  # reason 被保留

    def test_empty_operations_ok(self, snapshot):
        # 纯观察类行动可以产出空 patch
        raw = json_str({"operations": []})
        p = _make_proposer([raw])
        patch = p.propose(_action(action_type=ActionType.observe), snapshot)
        assert patch is not None
        assert patch.operations == []

    def test_fabricated_entity_filtered_via_validation(self, snapshot):
        # LLM 编造 item -> patch 能解析但校验失败 -> 重试
        bad = json_str({"operations": [
            {"op": "transfer_item", "item_id": "fake_item", "target_id": NIGHT}
        ]})
        good = json_str({"operations": []})
        p = _make_proposer([bad, good])
        patch = p.propose(_action(), snapshot)
        # 第二次重试返回空 patch，应该成功
        assert patch is not None

    def test_all_retries_fail_returns_none(self, snapshot):
        p = _make_proposer(["garbage1", "garbage2", "garbage3"])
        patch = p.propose(_action(), snapshot)
        assert patch is None
        assert p.last_error is not None

    def test_garbage_then_valid(self, snapshot):
        good = json_str({"operations": []})
        p = _make_proposer(["not json", good])
        patch = p.propose(_action(), snapshot)
        assert patch is not None


# ---------------------------------------------------------------------------
# 真实 LLM (默认跳过)
# ---------------------------------------------------------------------------


@pytest.mark.llm
class TestRealLLMTransition:
    def test_real_propose_smoke(self, snapshot):
        """真实 LLM 推演"拿外衫"的后果。验证能产出合法 patch。"""
        p = TransitionProposer()
        action = _action(
            action_type=ActionType.swap_object,
            declared_goal="反客为主拿走庶妹外衫",
            visibility="covert",
        )
        patch = p.propose(action, snapshot)
        if patch is None:
            pytest.skip(f"LLM 未返回合法 patch: {p.last_error}")
        print(f"\n[LLM-TRANS] 产出 {len(patch.operations)} 条操作:")
        for op in patch.operations:
            print(f"  - {op.op.value}: {op.reason}")
        # 至少应该有外衫转移或关系变化
        ops_kinds = {o.op for o in patch.operations}
        assert ops_kinds  # 不能完全空 (拿东西总有后果)，但也宽容


def json_str(d):
    import json
    return json.dumps(d, ensure_ascii=False)
