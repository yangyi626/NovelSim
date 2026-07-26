"""角色 Agent + 调度器测试。

策略与 ActionParser/TransitionProposer 一致:
- mock LLM 测决策/调度/合并逻辑 (确定性、CI 可跑、0 烧 token)
- 真实 LLM 用 @pytest.mark.llm 单独跑

覆盖 plan 第八步: NPC 自主行动 (夜清清反击、林管家盘算)。
"""

import json
from unittest import mock

import pytest

from engine import (
    CharacterAgent,
    CharacterScheduler,
    apply_patch,
    candidate_to_action,
)
from engine.character_agent import _extract_json
from world_schema import Operation, OperationKind, StatePatch

from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import LIN, NIGHT, OUTER_ROBE, QINGQING


# ---------------------------------------------------------------------------
# 基础: psyche 已注入快照
# ---------------------------------------------------------------------------


class TestPsycheWired:
    def test_snapshot_has_psyches(self, snapshot):
        # 3 个 psyche: 夜轻歌(玩家) + 夜清清 + 林管家
        assert NIGHT in snapshot.character_psyches
        assert QINGQING in snapshot.character_psyches
        assert LIN in snapshot.character_psyches
        assert snapshot.character_psyches[NIGHT].is_player is True
        assert snapshot.character_psyches[QINGQING].is_player is False
        # 夜清清有目标和计划
        assert snapshot.character_psyches[QINGQING].goals
        assert snapshot.character_psyches[QINGQING].plans

    def test_player_and_dead_not_scheduled(self, snapshot):
        # 玩家宿主不能被 decide
        ag = CharacterAgent(NIGHT)
        assert ag.decide(snapshot) is None
        assert "player" in (ag.last_error or "")

        # 杀掉夜清清后她不能决策
        dead_state = apply_patch(snapshot, StatePatch(operations=[
            Operation(op=OperationKind.kill_character, target_id=QINGQING)
        ]))
        ag2 = CharacterAgent(QINGQING)
        assert ag2.decide(dead_state) is None
        assert "dead" in (ag2.last_error or "")

    def test_no_psyche_returns_none(self, snapshot):
        ag = CharacterAgent("char_ghost")
        assert ag.decide(snapshot) is None
        assert ag.last_error is not None


# ---------------------------------------------------------------------------
# CharacterAgent 决策逻辑 (mock LLM)
# ---------------------------------------------------------------------------


def _make_agent(cid, raw_outputs):
    ag = CharacterAgent(cid)
    ag._call_llm = mock.Mock(side_effect=raw_outputs)
    return ag


def _json(d):
    return json.dumps(d, ensure_ascii=False)


class TestAgentDecideLogic:
    def test_long_term_memory_is_injected_with_authority_warning(
        self, snapshot
    ):
        ag = CharacterAgent(QINGQING)
        context = ag._build_context(
            snapshot,
            snapshot.character_psyches[QINGQING],
            snapshot.characters[QINGQING],
            long_term_memories=["三日前在旧书店发现过一本秘密账本"],
        )

        assert "# 与当前局势相关的长期记忆" in context
        assert "秘密账本" in context
        assert "以当前状态为准" in context

    def test_decide_with_action_returns_valid_patch(self, snapshot):
        """夜清清被夺外衫后，LLM 让她含讽反击。"""
        raw = _json({
            "decided": True,
            "action": {
                "action_type": "speak",
                "intent": "当众含讽反击夜轻歌",
                "target_ids": [NIGHT],
                "dialogue": "姐姐好大的威风，连亲妹妹的外衫也要抢么？",
                "tone": "委屈含讽",
                "expected_patch": [
                    {"op": "update_relation", "source_id": QINGQING,
                     "target_id": NIGHT, "dimension": "hostility",
                     "delta": 0.15, "reason": "受辱心生怨恨"},
                    {"op": "update_psyche", "target_id": QINGQING,
                     "emotion": "屈辱隐忍", "intensity": 0.7,
                     "perception": "嫡姐当众夺我外衫"},
                ],
                "utility": 0.6,
                "rationale": "受辱必须反击但顾忌嫡庶身份",
            },
            "emotion_update": "屈辱隐忍",
            "emotion_intensity": 0.7,
            "perception_summary": "嫡姐判若两人",
        })
        ag = _make_agent(QINGQING, [raw])
        decision = ag.decide(snapshot)
        assert decision is not None
        assert decision.decided
        assert decision.action.action_type == "speak"
        assert decision.action.dialogue  # 有台词

    def test_decide_noop_still_records_emotion(self, snapshot):
        """夜清清选择按兵不动，仍记录情绪/感知。"""
        raw = _json({
            "decided": False,
            "emotion_update": "隐忍观望",
            "emotion_intensity": 0.5,
            "perception_summary": "形势未明，先看清再说",
        })
        ag = _make_agent(QINGQING, [raw])
        decision = ag.decide(snapshot)
        assert decision is not None
        assert decision.decided is False
        assert decision.emotion_update == "隐忍观望"

    def test_invalid_patch_retries_then_succeeds(self, snapshot):
        """LLM 第一轮编造实体 -> 校验失败 -> 第二轮给出合法 patch。"""
        bad = _json({
            "decided": True,
            "action": {
                "action_type": "speak",
                "intent": "x",
                "target_ids": [NIGHT],
                "dialogue": "...",
                "expected_patch": [
                    {"op": "transfer_item", "item_id": "ghost_item",
                     "target_id": QINGQING}
                ],
                "utility": 0.5,
            },
            "emotion_update": "怒",
        })
        good = _json({
            "decided": True,
            "action": {
                "action_type": "observe",
                "intent": "冷眼旁观",
                "target_ids": [],
                "expected_patch": [],
                "utility": 0.3,
            },
            "emotion_update": "冷眼",
        })
        ag = _make_agent(QINGQING, [bad, good])
        decision = ag.decide(snapshot)
        assert decision is not None
        assert decision.action.action_type == "observe"

    def test_all_retries_fail_returns_none(self, snapshot):
        ag = _make_agent(QINGQING, ["garbage1", "garbage2", "garbage3"])
        assert ag.decide(snapshot) is None
        assert ag.last_error is not None

    def test_garbage_then_valid(self, snapshot):
        good = _json({"decided": False, "emotion_update": "平静"})
        ag = _make_agent(QINGQING, ["not json", good])
        d = ag.decide(snapshot)
        assert d is not None and d.decided is False

    def test_fabricated_target_filtered(self, snapshot):
        """候选动作里编造的 target_id 应被过滤掉。"""
        raw = _json({
            "decided": True,
            "action": {
                "action_type": "speak",
                "intent": "x",
                "target_ids": [NIGHT, "ghost_char"],
                "dialogue": "...",
                "expected_patch": [],
                "utility": 0.5,
            },
        })
        ag = _make_agent(QINGQING, [raw])
        d = ag.decide(snapshot)
        assert d is not None
        assert "ghost_char" not in d.action.target_ids
        assert NIGHT in d.action.target_ids


class TestCandidateToAction:
    def test_convert_to_action(self, snapshot):
        from engine.character_agent import AgentCandidateAction
        from world_schema.models import ActionType
        cand = AgentCandidateAction(
            action_type="speak",
            intent="嘲讽",
            target_ids=[NIGHT],
            dialogue="哼",
            tone="冷",
            utility=0.5,
        )
        act = candidate_to_action(cand, snapshot, QINGQING, "a_npc_1")
        assert act is not None
        assert act.action_type == ActionType.speak
        assert act.actor.actor_id == QINGQING
        assert NIGHT in act.target_ids

    def test_invalid_action_type_falls_back(self, snapshot):
        from engine.character_agent import AgentCandidateAction
        from world_schema.models import ActionType
        cand = AgentCandidateAction(action_type="dance", intent="跳舞")
        act = candidate_to_action(cand, snapshot, QINGQING, "a_npc_2")
        assert act is not None
        assert act.action_type == ActionType.observe  # 非法类型回落


# ---------------------------------------------------------------------------
# CharacterScheduler 调度与合并 (mock LLM)
# ---------------------------------------------------------------------------


def _scheduler_with(agent_outputs: dict):
    """构造调度器，每个 cid 用固定 raw 输出。"""
    def factory(cid):
        ag = CharacterAgent(cid)
        outs = agent_outputs.get(cid, [_json({"decided": False, "emotion_update": "观望"})])
        ag._call_llm = mock.Mock(side_effect=outs)
        return ag
    return CharacterScheduler(agent_factory=factory)


class TestScheduler:
    def test_player_not_scheduled(self, snapshot):
        # 只给夜清清动作；夜轻歌是玩家不应出现
        out = {
            QINGQING: [_json({
                "decided": True,
                "action": {
                    "action_type": "speak", "intent": "反击",
                    "target_ids": [NIGHT], "dialogue": "哼",
                    "expected_patch": [
                        {"op": "update_psyche", "target_id": QINGQING,
                         "emotion": "怒", "intensity": 0.6}
                    ],
                    "utility": 0.5,
                },
            })],
        }
        sched = _scheduler_with(out)
        result = sched.react(snapshot)
        assert NIGHT not in result.order
        assert QINGQING in result.order
        # combined patch 含夜清清的情绪更新
        kinds = {op.op for op in result.combined_patch.operations}
        assert OperationKind.update_psyche in kinds

    def test_no_psyches_world_skips(self, snapshot):
        # 把 psyches 清空，调度应直接返回空
        snap = snapshot.copy(deep=True)
        snap.character_psyches = {}
        sched = _scheduler_with({})
        result = sched.react(snap)
        assert result.order == []
        assert result.combined_patch.operations == []

    def test_two_npcs_both_act_merge(self, snapshot):
        # 夜清清 + 林管家 都在场有 psyche，都行动
        out = {
            QINGQING: [_json({
                "decided": True,
                "action": {
                    "action_type": "speak", "intent": "含讽",
                    "target_ids": [NIGHT], "dialogue": "姐姐好威风",
                    "expected_patch": [
                        {"op": "update_relation", "source_id": QINGQING,
                         "target_id": NIGHT, "dimension": "hostility",
                         "delta": 0.2, "reason": "受辱"}
                    ], "utility": 0.6,
                },
            })],
            LIN: [_json({
                "decided": True,
                "action": {
                    "action_type": "observe", "intent": "冷眼盘算",
                    "target_ids": [],
                    "expected_patch": [
                        {"op": "update_psyche", "target_id": LIN,
                         "emotion": "盘算", "intensity": 0.5,
                         "perception": "局势有变，要重新站队"}
                    ], "utility": 0.4,
                },
            })],
        }
        sched = _scheduler_with(out)
        result = sched.react(snapshot)
        assert set(result.order) == {QINGQING, LIN}
        # 两个 NPC 的 ops 都进了合并 patch
        ops = result.combined_patch.operations
        assert any(op.op == OperationKind.update_relation for op in ops)
        assert any(op.op == OperationKind.update_psyche for op in ops)

    def test_noop_npc_only_emotion_patch(self, snapshot):
        out = {QINGQING: [_json({"decided": False, "emotion_update": "隐忍",
                                  "intensity": 0.4, "perception_summary": "看不清"})]}
        sched = _scheduler_with(out)
        result = sched.react(snapshot)
        assert QINGQING in result.order
        ops = result.combined_patch.operations
        # 按兵不动 -> 至少有一条情绪更新
        assert ops and ops[0].op == OperationKind.update_psyche


# ---------------------------------------------------------------------------
# TurnPipeline 接入 NPC Agent
# ---------------------------------------------------------------------------


class TestPipelineWithNPC:
    def test_npc_reactions_appear_when_enabled(self, snapshot):
        """use_npc_agents=True 时，玩家行动后 NPC 被唤醒。

        用一个返回"按兵不动"的 mock scheduler，验证：
        - 状态仍正确提交 (version +1)
        - npc_reactions 列出参与的 NPC
        """
        from engine import TurnPipeline
        from engine.agent_scheduler import AgentScheduleResult, NPCReaction

        def always_noop_factory(cid):
            ag = CharacterAgent(cid)
            ag._call_llm = mock.Mock(return_value=_json({
                "decided": False, "emotion_update": "观望", "intensity": 0.3,
            }))
            return ag
        sched = CharacterScheduler(agent_factory=always_noop_factory)

        # 用确定性 patch (绕过 LLM proposer)
        pipe = TurnPipeline(
            parser=_FakeParser(),
            scheduler=sched,
        )
        result = pipe.run(
            "我命令夜清清把外衫脱下来", snapshot, NIGHT,
            use_llm_proposer=False, use_narrative=False, use_npc_agents=True,
        )
        assert result.status == "committed"
        assert result.new_state.version == snapshot.version + 1
        # 夜清清在场，应被唤醒
        assert QINGQING in result.npc_reactions

    def test_npc_agents_disabled_skips(self, snapshot):
        """use_npc_agents=False 时完全不调度 NPC。"""
        from engine import TurnPipeline
        called = []

        def spy_factory(cid):
            called.append(cid)
            ag = CharacterAgent(cid)
            ag._call_llm = mock.Mock(return_value=_json({"decided": False}))
            return ag
        sched = CharacterScheduler(agent_factory=spy_factory)

        pipe = TurnPipeline(parser=_FakeParser(), scheduler=sched)
        result = pipe.run(
            "观察", snapshot, NIGHT,
            use_llm_proposer=False, use_narrative=False, use_npc_agents=False,
        )
        assert result.status == "committed"
        assert called == []  # 没有任何 NPC 被调度
        assert result.npc_reactions == []


class _FakeParser:
    """绕过真实 ActionParser 的桩: 固定产出一个合法 action。"""

    def parse(self, user_text, state, *, default_actor_id=None):
        from world_schema import Action, Actor
        from world_schema.models import ActionType
        return Action(
            action_id="act_fake",
            action_type=ActionType.swap_object,
            actor=Actor(actor_id=default_actor_id or NIGHT),
            target_ids=[OUTER_ROBE],
            declared_goal=user_text[:30],
            visibility="overt",
        )


# ---------------------------------------------------------------------------
# 真实 LLM (默认跳过)
# ---------------------------------------------------------------------------


@pytest.mark.llm
class TestRealLLMAgent:
    def test_real_qingqing_counters(self, snapshot):
        """夜清清在受辱场景被唤醒，真实 LLM 应让她做出符合人设的反应。"""
        ag = CharacterAgent(QINGQING)
        decision = ag.decide(snapshot)
        if decision is None:
            pytest.skip(f"LLM 未返回合法决策: {ag.last_error}")
        print(f"\n[LLM-AGENT] 夜清清 decided={decision.decided}")
        if decision.action:
            print(f"  action: {decision.action.action_type} | {decision.action.intent}")
            if decision.action.dialogue:
                print(f"  台词({decision.action.tone}): {decision.action.dialogue}")
        print(f"  情绪: {decision.emotion_update}({decision.emotion_intensity})")
        print(f"  感知: {decision.perception_summary}")
        # 至少有情绪更新或动作
        assert decision.decided or decision.emotion_update

    def test_real_scheduler_runs(self, snapshot):
        """调度器真实唤醒夜清清+林管家，产出可提交的合并 patch。"""
        sched = CharacterScheduler()
        result = sched.react(snapshot)
        print(f"\n[LLM-SCHED] 唤醒顺序: {result.order}")
        for rx in result.reactions:
            tag = "行动" if rx.decided else "按兵"
            print(f"  - {rx.character_id} [{tag}] {rx.intent}"
                  + (f" 「{rx.dialogue}」" if rx.dialogue else ""))
        assert len(result.order) >= 1
        # 合并 patch 能成功应用到状态 (不抛错)
        new_state = apply_patch(snapshot, result.combined_patch)
        assert new_state is not None
