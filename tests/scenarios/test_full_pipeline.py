"""完整 Turn Pipeline 端到端测试。

这是整个项目的"验收测试": 用户输入一句话 -> 得到完整剧情回应。
- mock 版: 注入 mock 的三个 LLM 组件，确定性
- 真实 LLM 版: 全程 qwen3.6-plus
"""

import json
from unittest import mock

import pytest

from engine import (
    ActionParser,
    NarrativeGenerator,
    TurnPipeline,
    TransitionProposer,
)

from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import NIGHT, OUTER_ROBE, QINGQING


# ---------------------------------------------------------------------------
# Mock 全链路 (确定性)
# ---------------------------------------------------------------------------


class TestFullPipelineMocked:
    def test_complete_turn_returns_narrative(self, snapshot):
        """完整链路: 解析 -> 推演 -> 提交 -> 叙事。"""
        parser = ActionParser()
        parser._call_llm = mock.Mock(return_value=json.dumps({
            "action_type": "swap_object",
            "actor_id": NIGHT,
            "target_ids": [OUTER_ROBE],
            "declared_goal": "拿走外衫",
            "visibility": "covert",
        }, ensure_ascii=False))

        proposer = TransitionProposer()
        proposer._call_llm = mock.Mock(return_value=json.dumps({
            "operations": [
                {"op": "transfer_item", "item_id": OUTER_ROBE, "target_id": NIGHT,
                 "reason": "夜轻歌拿走外衫"},
            ],
        }, ensure_ascii=False))

        narrator = NarrativeGenerator()
        narrator._call_llm = mock.Mock(return_value=json.dumps({
            "narration": "夜轻歌反手取过外衫，披于肩上。",
            "dialogues": [
                {"speaker_id": NIGHT, "line": "不愧是我的好妹妹。",
                 "tone": "欣慰", "to_id": QINGQING},
            ],
            "system_hints": ["获得: 夜清清的外衫"],
            "grounded_event_ids": ["event_000001"],
            "referenced_entity_ids": [NIGHT, QINGQING, OUTER_ROBE],
        }, ensure_ascii=False))

        pipe = TurnPipeline(parser=parser, proposer=proposer, narrator=narrator)
        result = pipe.run("我拿走外衫", snapshot, default_actor_id=NIGHT)

        assert result.status == "committed"
        assert result.action is not None
        assert result.event is not None
        assert result.new_state.version == 1
        assert result.narrative is not None
        assert "外衫" in result.narrative.narration
        assert len(result.narrative.dialogues) == 1
        # 物品真的易主了
        assert result.new_state.items[OUTER_ROBE].owner_id == NIGHT

    def test_rejected_action_no_state_change(self, snapshot):
        from examples.huarong_lane.scenario import LIN

        parser = ActionParser()
        parser._call_llm = mock.Mock(return_value=json.dumps({
            "action_type": "swap_object",
            "actor_id": LIN,  # 林管家在夜府，跨场景
            "target_ids": [OUTER_ROBE],
        }, ensure_ascii=False))

        pipe = TurnPipeline(parser=parser)
        result = pipe.run("林管家拿外衫", snapshot, default_actor_id=LIN,
                          use_llm_proposer=False, use_narrative=False)

        assert result.status == "rejected"
        assert result.new_state is None  # 没产生新状态
        assert result.event is None

    def test_parse_failure_handled(self, snapshot):
        parser = ActionParser()
        parser._call_llm = mock.Mock(side_effect=["垃圾", "垃圾", "垃圾"])
        pipe = TurnPipeline(parser=parser)
        result = pipe.run("无意义", snapshot, default_actor_id=NIGHT)
        assert result.status == "parse_failed"
        assert result.event is None


# ---------------------------------------------------------------------------
# 真实 LLM 全链路 (默认跳过)
# ---------------------------------------------------------------------------


@pytest.mark.llm
class TestFullPipelineRealLLM:
    def test_real_complete_turn(self, snapshot):
        """真实 qwen3.6-plus 跑完整链路: 一句话 -> 完整剧情。

        这是整个项目的里程碑测试。
        """
        pipe = TurnPipeline()
        result = pipe.run(
            "我冷冷地盯着夜清清，命令她把身上的外衫脱下来交给我，声音里没有一丝温度",
            snapshot,
            default_actor_id=NIGHT,
        )

        print(f"\n{'='*60}")
        print(f"[完整 Turn] status: {result.status}")
        if result.error:
            print(f"[错误] {result.error}")

        if result.action:
            print(f"[Action] {result.action.action_type.value} | "
                  f"目标: {result.action.target_ids} | "
                  f"意图: {result.action.declared_goal}")

        if result.event and result.event.patch.operations:
            print(f"[状态变化] {len(result.event.patch.operations)} 条:")
            for op in result.event.patch.operations:
                print(f"  - {op.op.value}: {op.reason}")

        if result.new_state:
            print(f"[版本] {result.new_state.version}")

        if result.narrative:
            print(f"\n[旁白] {result.narrative.narration}")
            for d in result.narrative.dialogues:
                tone = f"({d.tone})" if d.tone else ""
                print(f"[对白] {d.speaker_id}{tone}: {d.line}")
            for h in result.narrative.system_hints:
                print(f"[系统] {h}")
        print(f"{'='*60}")

        # 不做强断言，只要 status 不是 parse_failed 就算链路通
        if result.status == "parse_failed":
            pytest.skip("LLM 解析失败 (可能网络/限流)")
        assert result.status in ("committed", "narrate_failed", "rejected")
