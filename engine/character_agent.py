"""角色 Agent: 让 NPC 有自主行动。

对应 plan 第八步。一个 NPC 不再只对玩家行动做"反应式 patch"，而是有:
    身份 / 人格 / 目标 / 计划 / 情绪 / 关系 / 记忆 / 认知

决策流程 (plan 第八节):
    读取角色可见事实 (基于 beliefs，认知不越权)
    -> 检索角色相关记忆 (recent_perceptions)
    -> 生成候选动作 (LLM，产出 AgentDecision)
    -> 规则过滤 + patch_validator 兜底 (安全边界)
    -> Utility 评分 (确定性后处理，叠加 LLM 自评)
    -> 选择动作
    -> 产出 NPC Action + 候选 StatePatch (供 scheduler 合并)

安全边界 (与 TransitionProposer 一致):
- LLM 只产**候选**动作 + 候选 patch，最终合法性由 patch_validator 兜底
- 角色**只能依据自己 beliefs 行动**——prompt 里只给该角色的认知
- 玩家宿主 (is_player=True) 永不被调度，由人操控
- 死人不决策
"""

from __future__ import annotations

import json
import re
from typing import List, Optional

import openai
from pydantic import ValidationError

from world_schema import (
    Action,
    AgentCandidateAction,
    AgentDecision,
    Operation,
    OperationKind,
    StatePatch,
    WorldState,
)
from world_schema.models import Actor, ActionType

from .config import get_llm_config
from .llm_telemetry import call_openai_compatible
from .patch_validator import validate_patch


SYSTEM_PROMPT = """你是一个小说世界里的角色。你正在"做你自己"，根据你的人设、目标、情绪和对局势的认知，决定你接下来会做什么。你不是叙述者，你就是这个角色本人在思考与行动。

# 你的决策原则
1. 只依据"你知道的事实"行动 (认知一节列出)。你不知道的事不能拿来决策。
2. 行动要服务于你当前的**目标**和**计划**，受你的**人格**和**情绪**驱动。
3. 角色之间有**身份尊卑/利害关系**——庶妹对嫡姐要顾忌，管家对主子要恭顺，但若有利害冲突也可能暗中使坏。
4. 可以选择**按兵不动** (decided=false)，尤其在形势不明、或隐忍更符合人设时。不要每轮都强行行动。
5. 输出的 expected_patch 只能描述"你这个动作直接造成的"变化，不要替别人做决定，不要推演连锁反应。

# 合法 action_type
speak / attack / move / investigate / use_item / gift / swap_object / observe

# 合法 patch 操作 (expected_patch 数组里每条的 op 字段)
- set_flag: path, value
- set_attr: path("角色id.属性名"), value
- increment_value: path, delta
- move_character: target_id, location_id
- transfer_item: item_id, target_id
- update_relation: source_id, target_id, dimension(affection/trust/fear/hostility/respect/debt), delta
- update_belief: target_id, fact_id, belief, confidence, source_type
- update_psyche: target_id(你自己), emotion, intensity(0-1), perception(一句话感受)
- advance_plan: target_id(你自己), plan_id
- change_identity / kill_character / start_plot / advance_plot / complete_plot

# 输出格式 (只输出一个 JSON 对象)
{
  "decided": true,
  "action": {
    "action_type": "speak",
    "intent": "当众反讽夜轻歌不自量力",
    "target_ids": ["char_yeqingge"],
    "dialogue": "姐姐好大的威风，连亲妹妹的外衫也要抢么？",
    "tone": "委屈含讽",
    "expected_patch": [
      {"op": "update_relation", "source_id": "char_yeqingqing", "target_id": "char_yeqingge", "dimension": "hostility", "delta": 0.15, "reason": "被夺外衫心生怨恨"},
      {"op": "update_psyche", "target_id": "char_yeqingqing", "emotion": "屈辱隐忍", "intensity": 0.7, "perception": "嫡姐当众夺我外衫，威压惊人"}
    ],
    "utility": 0.6,
    "rationale": "受辱当众，必须反击保住面子，但又不能硬碰嫡系身份，故以言语含讽试探对方虚实"
  },
  "emotion_update": "屈辱隐忍",
  "emotion_intensity": 0.7,
  "perception_summary": "嫡姐判若两人，废柴之名或有蹊跷"
}

若选择按兵不动:
{"decided": false, "emotion_update": "...", "emotion_intensity": 0.4, "perception_summary": "..."}
"""


class CharacterAgent:
    """单个 NPC 的自主决策单元。

    用法:
        agent = CharacterAgent("char_yeqingqing")
        decision = agent.decide(state)
        # decision.action -> 候选 AgentCandidateAction
    """

    def __init__(
        self,
        character_id: str,
        model: Optional[str] = None,
        max_retries: int = 2,
    ):
        cfg = get_llm_config()
        self.api_key = cfg.api_key
        self.base_url = cfg.base_url
        self.model = model or cfg.model
        self.max_retries = max_retries
        openai.api_key = self.api_key
        openai.api_base = self.base_url
        self.character_id = character_id
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def decide(
        self,
        state: WorldState,
        long_term_memories: Optional[List[str]] = None,
    ) -> Optional[AgentDecision]:
        """让该角色做一次自主决策。失败返回 None (查 last_error)。

        返回的 AgentDecision 里:
        - decided=False: 角色选择按兵不动 (仍可能带情绪/感知更新)
        - decided=True: action 字段含候选动作 + expected_patch (已过 patch_validator)
        """
        self.last_error = None

        psy = state.character_psyches.get(self.character_id)
        char = state.characters.get(self.character_id)
        if psy is None or char is None:
            self.last_error = f"no character/psyche: {self.character_id}"
            return None
        if psy.is_player:
            self.last_error = "player host is not auto-driven"
            return None
        if not char.is_alive:
            self.last_error = "dead characters do not act"
            return None

        context = self._build_context(
            state,
            psy,
            char,
            long_term_memories=long_term_memories,
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ]

        last_raw = ""
        for attempt in range(self.max_retries + 1):
            raw = self._call_llm(messages)
            last_raw = raw
            decision = self._try_build(raw)
            if decision is None:
                if attempt < self.max_retries:
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({
                        "role": "user",
                        "content": "输出无法解析为合法 JSON。请只输出一个 JSON 对象。",
                    })
                continue

            # 不行动: 直接接受 (但确保情绪/感知字段已填)
            if not decision.decided or decision.action is None:
                return self._finalize_noop(decision, psy)

            # 行动: 把 expected_patch 草稿转成 StatePatch 并校验
            patch = self._patch_from_candidate(decision.action, psy)
            check = validate_patch(state, patch)
            if check.valid:
                # 顺便校验 action 本身的实体合法性 (target_ids 过滤)
                self._sanitize_action(decision.action, state)
                return decision

            if attempt < self.max_retries:
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": f"你的动作 patch 有违规，请修正:\n{check.why()}",
                })
            else:
                self.last_error = f"patch validation failed: {check.why()}"

        if self.last_error is None:
            self.last_error = f"parse failed, last raw: {last_raw[:200]}"
        return None

    # ------------------------------------------------------------------
    # 上下文构造 (只暴露该角色"该知道的")
    # ------------------------------------------------------------------

    def _build_context(
        self,
        state: WorldState,
        psy,
        char,
        *,
        long_term_memories: Optional[List[str]] = None,
    ) -> str:
        cid = self.character_id
        lines = [
            "# 你的身份",
            f"id: {cid} | {char.display_name} | 身份:{char.identity_tags} | "
            f"位置:{char.location_id}",
            f"人格特质: {', '.join(psy.traits) or '(未设定)'}",
            f"当前情绪: {psy.emotion or '(平静)'} (强度 {psy.emotion_intensity})",
        ]

        # 目标与计划
        if psy.goals:
            lines.append("\n# 你的目标")
            current_timeline = str(
                state.flags.get("compiler.current_timeline")
                or state.timeline_id
            )
            for g in psy.goals:
                status = getattr(g, "status", "active")
                timeline_id = getattr(g, "timeline_id", "")
                if (
                    g.achieved
                    or status != "active"
                    or (
                        timeline_id
                        and timeline_id != current_timeline
                    )
                ):
                    continue
                tag = "[活跃]"
                lines.append(f"- {tag} {g.goal_id} (优先级{g.priority}): {g.description}")
        if psy.plans:
            lines.append("\n# 你的计划")
            for p in psy.plans:
                idx = p.current_step
                step = p.steps[idx] if 0 <= idx < len(p.steps) else "(无当前步骤)"
                lines.append(f"- [{p.status}] {p.plan_id}: 当前步骤 -> {step}")

        # 认知: 只给这个角色自己的 beliefs (认知不越权)
        beliefs = state.beliefs.get(cid, [])
        if beliefs:
            lines.append("\n# 你知道的事 (只能基于这些决策)")
            for b in beliefs[:8]:
                lines.append(f"- {b.fact_id}: {b.belief.value} (置信度{b.confidence})")

        # 短期记忆
        if psy.recent_perceptions:
            lines.append("\n# 你最近感知到的")
            for s in psy.recent_perceptions[-5:]:
                lines.append(f"- {s}")

        # 长期记忆由持久化检索层按会话和角色隔离后注入。它是相关历史的
        # 召回结果，不覆盖当前权威世界状态。
        if long_term_memories:
            lines.append("\n# 与当前局势相关的长期记忆")
            lines.append("以下记忆可能不完整；若与当前世界状态冲突，以当前状态为准。")
            for memory in long_term_memories[:5]:
                lines.append(f"- {memory}")

        # 在场角色 (能看到的人) + 与他们的关系
        scene_chars = [c for c in state.characters.values()
                       if c.location_id == char.location_id and c.character_id != cid]
        if scene_chars:
            lines.append("\n# 在场的人")
            for c in scene_chars:
                rel = _find_relation(state, cid, c.character_id)
                rd = ""
                if rel is not None:
                    rd = f" | 关系:{rel.public_relation or rel.private_relation} | " \
                         f"好感{rel.dimensions.affection:.1f} 敌意{rel.dimensions.hostility:.1f}"
                lines.append(f"- {c.character_id} | {c.display_name} | "
                             f"身份:{c.identity_tags} | 存活:{c.is_alive}{rd}")

        # 世界规则 (高层，帮角色判断利害)
        if state.world_rules:
            lines.append("\n# 你熟知的世界规矩")
            for wr in state.world_rules:
                lines.append(f"- {wr.statement}")

        # 当前剧情线
        active_arcs = [a for a in state.plot.values() if a.stage == "active"]
        if active_arcs:
            lines.append("\n# 正在发生的事")
            for a in active_arcs:
                lines.append(f"- {a.title} ({a.kind})")

        lines.append("\n# 请决策: 你接下来会做什么？(可按兵不动)")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 调用与解析
    # ------------------------------------------------------------------

    def _call_llm(self, messages: list) -> str:
        resp = call_openai_compatible(
            openai.ChatCompletion.create,
            operation="character_agent",
            model=self.model, messages=messages, temperature=0.6,
        )
        return resp.choices[0].message.content.strip()

    def _try_build(self, raw: str) -> Optional[AgentDecision]:
        data = _extract_json(raw)
        if data is None:
            return None
        # 强制 character_id 一致 (防 LLM 跑偏)
        data["character_id"] = self.character_id
        try:
            return AgentDecision.parse_obj(data)
        except ValidationError:
            return None

    # ------------------------------------------------------------------
    # 后处理: 候选动作 -> 可提交的 Action / StatePatch
    # ------------------------------------------------------------------

    def _patch_from_candidate(self, cand: AgentCandidateAction, psy) -> StatePatch:
        """把 LLM 草稿 expected_patch (list[dict]) 转成严格 StatePatch。

        每条草稿带上 reason (若缺则用 cand.rationale)。
        草稿里若 update_psyche 没指定 target_id，补成自己。
        """
        ops: List[Operation] = []
        for raw_op in cand.expected_patch:
            if not isinstance(raw_op, dict):
                continue
            try:
                op_val = raw_op.get("op")
                kind = OperationKind(op_val)
            except (ValueError, KeyError):
                continue
            # 复制一份再清整，避免污染原 dict
            d = {k: v for k, v in raw_op.items() if k != "op"}
            d.setdefault("reason", cand.rationale or cand.intent)
            # update_psyche 默认作用于自己
            if kind == OperationKind.update_psyche and not d.get("target_id"):
                d["target_id"] = self.character_id
            if kind == OperationKind.advance_plan and not d.get("target_id"):
                d["target_id"] = self.character_id
            try:
                ops.append(Operation(op=kind, **_filtered_op_fields(d)))
            except (ValidationError, TypeError):
                continue
        return StatePatch(operations=ops)

    def _sanitize_action(self, cand: AgentCandidateAction, state: WorldState) -> None:
        """过滤掉候选动作里不存在的 target_ids (防编造)。原地修改。"""
        cand.target_ids = [
            t for t in cand.target_ids if t in state.characters or t in state.items
        ]

    def _finalize_noop(self, decision: AgentDecision, psy) -> AgentDecision:
        """按兵不动分支: 保证情绪/感知字段回落到当前值，避免空更新。"""
        if not decision.emotion_update:
            decision.emotion_update = psy.emotion
        if decision.emotion_intensity is None:
            decision.emotion_intensity = psy.emotion_intensity
        return decision


# ---------------------------------------------------------------------------
# 公共工具
# ---------------------------------------------------------------------------


def _find_relation(state: WorldState, source_id: str, target_id: str):
    for r in state.relations:
        if r.source_id == source_id and r.target_id == target_id:
            return r
    return None


def _filtered_op_fields(d: dict) -> dict:
    """只保留 Operation 模型认识的字段，丢弃 LLM 夹带的杂项。"""
    allowed = {
        "path", "value", "target_id", "source_id", "dimension", "delta",
        "item_id", "location_id", "belief", "confidence", "source_type",
        "tags", "fact_id", "reason", "emotion", "intensity", "perception",
        "plan_id", "step_delta",
    }
    return {k: v for k, v in d.items() if k in allowed}


def _extract_json(raw: str) -> Optional[dict]:
    """与 ActionParser/TransitionProposer 共享的 JSON 容错提取。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# 候选决策 -> 可执行 Action 的转换 (scheduler 用)
# ---------------------------------------------------------------------------


def candidate_to_action(
    cand: AgentCandidateAction,
    state: WorldState,
    actor_id: str,
    action_id: str,
) -> Optional[Action]:
    """把 AgentCandidateAction 转成正式 Action (供规则引擎校验)。

    action_type 非法时回落到 observe。target_ids 已经过滤。
    """
    try:
        at = ActionType(cand.action_type)
    except ValueError:
        at = ActionType.observe
    try:
        return Action(
            action_id=action_id,
            action_type=at,
            actor=Actor(actor_id=actor_id),
            target_ids=list(cand.target_ids),
            parameters={"intent": cand.intent, "dialogue": cand.dialogue,
                        "tone": cand.tone},
            declared_goal=cand.intent or cand.rationale,
            visibility="overt",  # NPC 自主行动默认公开
        )
    except ValidationError:
        return None
