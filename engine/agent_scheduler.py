"""角色 Agent 调度器: 决定"哪些 NPC、按什么顺序、在何时自主行动"。

对应 plan 第八节 "事件驱动调度"：
    当前场景中的角色
    + 被事件直接影响的角色
    + 有未完成计划的角色

一次玩家 Turn 后，scheduler 决定:
    1. 哪些 NPC 该被唤醒 (调度)
    2. 依次让每个 NPC 决策 (CharacterAgent.decide)
    3. 把每个 NPC 的候选 patch 校验后合并成一个综合 StatePatch
    4. (可选) 把 NPC 对白收集起来，供叙事层渲染

安全边界:
- 每个 NPC 一轮最多 1 个动作 (防止 NPC 之间无限连锁行动)
- 玩家宿主、死人、无 psyche 的角色不调度
- NPC 的 patch 同样过 patch_validator (CharacterAgent 内已做，这里 merge 后再校验一次)
- merge 冲突时后写者胜，但 update_relation 这类增量可叠加

调度顺序 (确定性，便于回放):
    按 "被事件影响 > 在场 > 有活跃计划 > 字典序" 排序
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from world_schema import (
    AgentDecision,
    Operation,
    OperationKind,
    StatePatch,
    WorldEvent,
    WorldState,
)

from .character_agent import CharacterAgent, candidate_to_action
from .patch_validator import validate_patch


@dataclass
class NPCReaction:
    """单个 NPC 的一次反应记录 (供叙事层 + 调试用)。"""

    character_id: str
    action_type: str = ""
    intent: str = ""
    dialogue: str = ""
    tone: str = ""
    patch: StatePatch = field(default_factory=StatePatch)
    decided: bool = False
    perception_summary: str = ""


@dataclass
class AgentScheduleResult:
    """一轮 NPC 调度的综合产物。"""

    reactions: List[NPCReaction] = field(default_factory=list)
    combined_patch: StatePatch = field(default_factory=StatePatch)
    order: List[str] = field(default_factory=list)  # 实际被唤醒的顺序
    errors: Dict[str, str] = field(default_factory=dict)  # character_id -> 失败原因


class CharacterScheduler:
    """NPC 自主行动调度器。注入式依赖，便于测试时替换 agent factory。"""

    def __init__(
        self,
        agent_factory=None,
        max_npcs_per_turn: int = 4,
        max_narrative_per_npc: int = 1,
    ):
        # agent_factory(character_id) -> CharacterAgent；默认直接 new
        self._agent_factory = agent_factory or (lambda cid: CharacterAgent(cid))
        self.max_npcs_per_turn = max_npcs_per_turn
        self.max_narrative_per_npc = max_narrative_per_npc

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def react(
        self,
        state: WorldState,
        trigger_event: Optional[WorldEvent] = None,
    ) -> AgentScheduleResult:
        """在一轮玩家行动后，调度相关 NPC 做出反应。

        state 视为"已应用玩家 patch 后的新状态"。
        trigger_event 可为空 (主动 tick，如时间推进)。
        """
        result = AgentScheduleResult()

        # 1. 候选 NPC: 谁该被唤醒
        candidates = self._select_candidates(state, trigger_event)
        if not candidates:
            return result
        # 截断，防一次 turn 醒太多
        candidates = candidates[: self.max_npcs_per_turn]

        # 2. 依次决策 (确定性顺序)
        combined_ops: List[Operation] = []
        for cid in candidates:
            reaction = self._run_one(cid, state, trigger_event)
            result.reactions.append(reaction)
            # 无论是否行动，NPC 的情绪/感知更新都计入合并 patch
            # (按兵不动也是世界状态的演化: 情绪、认知在工作记忆里沉淀)
            combined_ops.extend(reaction.patch.operations)
            result.order.append(cid)

        # 3. 合并校验: 把所有 NPC 的 ops 合成一个 patch 再过一次校验
        combined = StatePatch(operations=combined_ops,
                              notes=f"NPC reactions from {len(result.order)} agents")
        check = validate_patch(state, combined)
        if not check.valid:
            # 有冲突: 退化为只保留合法的 ops，丢掉违规的 (保留可提交部分)
            keep = self._strip_violating(state, combined_ops, check)
            combined = StatePatch(operations=keep,
                                  notes=f"NPC reactions (filtered): {check.why()}")
        result.combined_patch = combined
        return result

    # ------------------------------------------------------------------
    # 调度选择
    # ------------------------------------------------------------------

    def _select_candidates(
        self, state: WorldState, trigger_event: Optional[WorldEvent]
    ) -> List[str]:
        """谁该被唤醒。优先级:
        1. 被事件直接影响的 NPC (actor/target)
        2. 与玩家同场景的 NPC
        3. 有活跃计划的 NPC

        剔除: 玩家宿主 / 死人 / 无 psyche。
        排序稳定 (同优先级按字典序)，便于回放。
        """
        psyches = state.character_psyches
        if not psyches:
            return []

        # 玩家宿主 id (不调度)
        player_ids = {cid for cid, p in psyches.items() if p.is_player}

        affected: set = set()
        if trigger_event is not None:
            # 事件 actor/target 里的角色都"被波及"
            for tid in list(trigger_event.actor_ids) + list(trigger_event.target_ids):
                if tid in psyches and tid not in player_ids:
                    affected.add(tid)

        # 同场景: 找玩家所在场景
        scene_chars: set = set()
        if player_ids:
            pid = next(iter(player_ids))
            pchar = state.characters.get(pid)
            if pchar is not None:
                for c in state.characters.values():
                    if (c.location_id == pchar.location_id
                            and c.character_id in psyches
                            and c.character_id not in player_ids
                            and c.is_alive):
                        scene_chars.add(c.character_id)

        # 有活跃计划者
        planners: set = set()
        for cid, psy in psyches.items():
            if cid in player_ids:
                continue
            if any(p.status == "active" for p in psy.plans):
                planners.add(cid)

        # 按优先级分组 (数值越小越先)，组内字典序
        def bucket(cid: str) -> Tuple[int, str]:
            if cid in affected:
                return 0, cid
            if cid in scene_chars:
                return 1, cid
            if cid in planners:
                return 2, cid
            return 3, cid

        all_ids = set(affected) | scene_chars | planners
        return sorted(all_ids, key=bucket)

    # ------------------------------------------------------------------
    # 单个 NPC 决策
    # ------------------------------------------------------------------

    def _run_one(
        self, cid: str, state: WorldState, trigger_event: Optional[WorldEvent]
    ) -> NPCReaction:
        agent = self._agent_factory(cid)
        decision = agent.decide(state)

        if decision is None:
            return NPCReaction(
                character_id=cid,
                decided=False,
                perception_summary=f"[ERROR] {agent.last_error or ''}",
            )

        rx = NPCReaction(
            character_id=cid,
            decided=decision.decided,
            perception_summary=decision.perception_summary,
        )
        if decision.action is not None:
            rx.action_type = decision.action.action_type
            rx.intent = decision.action.intent
            rx.dialogue = decision.action.dialogue
            rx.tone = decision.action.tone

        if not decision.decided or decision.action is None:
            # 按兵不动: 仍记录情绪/感知更新
            rx.patch = self._noop_patch(cid, decision)
            return rx

        # 行动: 提取候选 patch (与 CharacterAgent 内部一致的转换)
        patch = agent._patch_from_candidate(decision.action, state.character_psyches[cid])
        # 补一条情绪/感知更新 (LLM 可能在顶层给了 emotion_update 而 expected_patch 没含)
        extra = self._emotion_patch(cid, decision)
        if extra.operations:
            patch = StatePatch(operations=list(patch.operations) + list(extra.operations))
        rx.patch = patch
        return rx

    # ------------------------------------------------------------------
    # patch 辅助
    # ------------------------------------------------------------------

    def _noop_patch(self, cid: str, decision: AgentDecision) -> StatePatch:
        """按兵不动时的最小更新: 情绪 + 感知 (若有)。"""
        return self._emotion_patch(cid, decision)

    def _emotion_patch(self, cid: str, decision: AgentDecision) -> StatePatch:
        ops: List[Operation] = []
        if decision.emotion_update:
            ops.append(Operation(
                op=OperationKind.update_psyche, target_id=cid,
                emotion=decision.emotion_update,
                intensity=decision.emotion_intensity,
                perception=decision.perception_summary,
                reason="agent emotion update",
            ))
        return StatePatch(operations=ops)

    def _strip_violating(
        self, state: WorldState, ops: List[Operation], check
    ) -> List[Operation]:
        """把校验结果里标记违规的 op 索引丢弃。"""
        bad = {v.op_index for v in check.violations}
        kept = [op for i, op in enumerate(ops) if i not in bad]
        # 再次校验剩余 (update_psyche 等可能仍因为依赖被丢的 op 而失效)
        recheck = validate_patch(state, StatePatch(operations=kept))
        if recheck.valid:
            return kept
        # 递归剥离 (最多两层，防死循环)
        if len(kept) < len(ops):
            return self._strip_violating(state, kept, recheck)
        return []


def merge_patches(*patches: StatePatch) -> StatePatch:
    """合并多个 StatePatch 为一个。

    增量类 op (update_relation/increment_value/update_psyche) 可叠加；
    覆盖类 op (set_flag/set_attr) 后写者胜——交给 apply_patch 自然处理。
    """
    ops: List[Operation] = []
    notes_parts: List[str] = []
    for p in patches:
        ops.extend(p.operations)
        if p.notes:
            notes_parts.append(p.notes)
    return StatePatch(operations=ops, notes=" | ".join(notes_parts))
