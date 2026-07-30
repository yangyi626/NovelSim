"""完整 Turn Pipeline: 用户输入 -> 叙事输出。

把前面三个 LLM 模块串成一条完整的游戏回合:
    用户自然语言
       ↓ ActionParser (LLM 任务1)
    Action
       ↓ RuleEngine (规则校验)
    合法 Action
       ↓ TransitionProposer (LLM 任务2) + patch_validator
    StatePatch
       ↓ CharacterScheduler (角色 Agent，plan 第八步)
    合并后的 StatePatch (含 NPC 自主反应)
       ↓ commit_event
    WorldEvent + 新 WorldState
       ↓ NarrativeGenerator (LLM 任务3) + 一致性审查
    NarrativeOutput (玩家看到的文字)

对应 plan 第八步 "一次完整 Turn Pipeline" 的最小实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from world_schema import (
    Action,
    CausalEvidence,
    IntentParseResult,
    IntentRejectionCode,
    IntentStatus,
    NarrativeOutput,
    WorldEvent,
    WorldState,
)

from .action_parser import ActionParser
from .agent_scheduler import AgentScheduleResult, CharacterScheduler
from .narrative import NarrativeGenerator
from .patch_validator import validate_action_patch
from .rules import RuleCheckResult, RuleEngine
from .transition import TransitionProposer


@dataclass
class TurnResult:
    """一个 Turn 的完整结果。"""

    status: str  # committed / rejected / parse_failed / propose_failed / narrate_failed
    action: Optional[Action] = None
    event: Optional[WorldEvent] = None
    new_state: Optional[WorldState] = None
    narrative: Optional[NarrativeOutput] = None
    rule_result: Optional[RuleCheckResult] = None
    intent_result: Optional[IntentParseResult] = None
    npc_reactions: List[str] = field(default_factory=list)  # 参与反应的 NPC id
    error: str = ""


class TurnPipeline:
    """编排一个完整回合。注入式依赖，便于测试和替换组件。"""

    def __init__(
        self,
        parser: Optional[ActionParser] = None,
        proposer: Optional[TransitionProposer] = None,
        narrator: Optional[NarrativeGenerator] = None,
        engine: Optional[RuleEngine] = None,
        scheduler: Optional[CharacterScheduler] = None,
    ):
        # 延迟初始化: 只在真正需要时才创建 (避免无 key 时 import 就报错)
        self._parser = parser
        self._proposer = proposer
        self._narrator = narrator
        self._engine = engine or RuleEngine()
        self._scheduler = scheduler

    def _get_parser(self) -> ActionParser:
        if self._parser is None:
            self._parser = ActionParser()
        return self._parser

    def _get_proposer(self) -> TransitionProposer:
        if self._proposer is None:
            self._proposer = TransitionProposer()
        return self._proposer

    def _get_narrator(self) -> NarrativeGenerator:
        if self._narrator is None:
            self._narrator = NarrativeGenerator()
        return self._narrator

    def _get_scheduler(self) -> CharacterScheduler:
        if self._scheduler is None:
            self._scheduler = CharacterScheduler()
        return self._scheduler

    def run(
        self,
        user_text: str,
        state: WorldState,
        default_actor_id: str,
        *,
        use_llm_proposer: bool = True,
        use_narrative: bool = True,
        use_npc_agents: bool = False,
        npc_memory_context: Optional[Dict[str, List[str]]] = None,
    ) -> TurnResult:
        """跑一个完整回合。返回 TurnResult。

        use_llm_proposer=False 时用确定性占位 patch (测试用)。
        use_narrative=False 时跳过叙事生成 (省 token，纯状态变更)。
        use_npc_agents=True 时，玩家行动提交后唤醒相关 NPC 自主反应
            (plan 第八步角色 Agent)。默认关闭以保持向后兼容；启用 NPC 的
            调用方需显式传入。世界未配置 character_psyches 时自动跳过。
        """
        # 1. 解析。权威入口保留 rejected，不把不可能行动降级成 observe。
        parser = self._get_parser()
        if hasattr(parser, "parse_result"):
            intent_result = parser.parse_result(
                user_text,
                state,
                default_actor_id=default_actor_id,
            )
            if intent_result.status == IntentStatus.rejected:
                return TurnResult(
                    status="rejected",
                    intent_result=intent_result,
                    error=(
                        f"{intent_result.reason_code.value}: "
                        f"{intent_result.message}"
                    ),
                )
            if intent_result.status == IntentStatus.parse_failed:
                return TurnResult(
                    status="parse_failed",
                    intent_result=intent_result,
                    error=intent_result.message or "无法解析用户输入",
                )
            action = intent_result.action
        else:
            # 兼容测试和外部注入的旧 Parser。
            action = parser.parse(
                user_text,
                state,
                default_actor_id=default_actor_id,
            )
            intent_result = None
        if action is None:
            return TurnResult(
                status="parse_failed",
                intent_result=intent_result,
                error="无法解析用户输入",
            )

        # 2. 规则校验
        res = self._engine.validate(state, action)
        if not res.allowed:
            rejected_intent = _intent_rejection_from_rules(user_text, res)
            return TurnResult(
                status="rejected",
                action=action,
                rule_result=res,
                intent_result=rejected_intent,
                error=f"规则拒绝: {res.why()}",
            )

        # 3. 状态推演
        if use_llm_proposer:
            proposer = self._get_proposer()
            patch = proposer.propose(action, state)
            if patch is None:
                return TurnResult(
                    status="propose_failed", action=action,
                    error=f"推演失败: {proposer.last_error}",
                )
        else:
            from ._deterministic_patch import deterministic_patch
            patch = deterministic_patch(action)

        patch = patch.copy(deep=True)
        patch.causal_evidence = CausalEvidence(
            action_id=action.action_id,
            actor_id=action.actor.actor_id,
            authority="player_action",
        )
        causal_check = validate_action_patch(state, action, patch)
        if not causal_check.valid:
            rejected_intent = IntentParseResult(
                status=IntentStatus.rejected,
                reason_code=IntentRejectionCode.patch_not_authorized,
                message=causal_check.why(),
                raw_input=user_text,
            )
            return TurnResult(
                status="rejected",
                action=action,
                intent_result=rejected_intent,
                error=f"PATCH_NOT_AUTHORIZED: {causal_check.why()}",
            )

        # 4. 角色 Agent: 在玩家 patch 之上叠加 NPC 自主反应
        npc_reactions: List[str] = []
        npc_schedule: Optional[AgentScheduleResult] = None
        if use_npc_agents and state.character_psyches:
            scheduler = self._get_scheduler()
            # 在"假设玩家 patch 已应用"的临时状态上调度 NPC
            from .patch import apply_patch
            hypothetical = apply_patch(state, patch)
            hypothetical.version = state.version + 1
            npc_schedule = scheduler.react(
                hypothetical,
                trigger_event=None,
                memory_context=npc_memory_context,
            )
            npc_reactions = list(npc_schedule.order)
            if npc_schedule.combined_patch.operations:
                patch = merge_player_and_npc(patch, npc_schedule.combined_patch)
                patch.causal_evidence = CausalEvidence(
                    action_id=action.action_id,
                    actor_id=action.actor.actor_id,
                    authority="player_action_with_npc_reactions",
                )

        # 5. 提交 (乐观锁: expected_version = state.version)
        from .event import commit_event
        ev, new_state = commit_event(
            state, action_id=action.action_id, event_type=action.action_type.value,
            patch=patch, actor_ids=[action.actor.actor_id], target_ids=action.target_ids,
            expected_version=state.version,
        )
        # 把 NPC id 也记到事件里 (便于回放/追溯谁参与了反应)
        if npc_reactions:
            ev.actor_ids = list(ev.actor_ids) + npc_reactions

        # 6. 叙事生成
        narrative = None
        if use_narrative:
            narrator = self._get_narrator()
            # 把 NPC 反应信息喂给叙事器，让旁白/对白能体现 NPC 行动
            narrative = self._generate_narrative(narrator, ev, new_state, action, npc_schedule)
            if narrative is None:
                # 叙事失败不回滚状态 (状态已提交是事实)，只标记
                return TurnResult(
                    status="narrate_failed", action=action, event=ev,
                    new_state=new_state, intent_result=intent_result,
                    npc_reactions=npc_reactions,
                    error=f"叙事失败: {narrator.last_error}",
                )

        from .presentation_stream import build_turn_presentation_events
        ev.presentation_events = build_turn_presentation_events(
            ev,
            action=action,
            narrative=narrative,
        )
        return TurnResult(
            status="committed", action=action, event=ev,
            new_state=new_state, narrative=narrative,
            intent_result=intent_result,
            npc_reactions=npc_reactions,
        )

    def _generate_narrative(
        self, narrator, ev, new_state, action, npc_schedule
    ) -> Optional[NarrativeOutput]:
        """生成叙事。若有 NPC 反应，先把它们的对白/动作摘要注入到事件的 patch 摘要。"""
        if npc_schedule is None or not npc_schedule.reactions:
            return narrator.generate(ev, new_state, action)
        # 给叙事器一个"NPC 也做了这些事"的提示: 通过扩展 action.parameters 传递
        extra = []
        for rx in npc_schedule.reactions:
            if rx.decided and (rx.dialogue or rx.intent):
                d = rx.dialogue or ""
                extra.append(f"{rx.character_id}({rx.action_type}): {rx.intent}"
                             + (f" 「{d}」" if d else ""))
        if extra:
            action = action.copy(deep=True) if hasattr(action, "copy") else action
            action.parameters = dict(action.parameters)
            action.parameters["npc_reactions"] = extra
        return narrator.generate(ev, new_state, action)


def merge_player_and_npc(player_patch, npc_patch):
    """把玩家 patch 和 NPC 综合 patch 合并。NPC 的 ops 追加在玩家 ops 之后。"""
    from world_schema import StatePatch
    ops = list(player_patch.operations) + list(npc_patch.operations)
    notes = player_patch.notes
    if npc_patch.notes:
        notes = (notes + " | " + npc_patch.notes) if notes else npc_patch.notes
    return StatePatch(
        operations=ops,
        notes=notes,
        causal_evidence=player_patch.causal_evidence,
    )


def _intent_rejection_from_rules(
    user_text: str,
    result: RuleCheckResult,
) -> IntentParseResult:
    ids = {violation.rule_id for violation in result.violations}
    if ids & {"world_concept_unavailable", "world_concept_not_allowed"}:
        code = IntentRejectionCode.world_concept_unavailable
    elif ids & {
        "world_concept_exists",
        "destination_exists",
        "affordance_entity_exists",
    }:
        code = IntentRejectionCode.entity_not_found
    elif "capability_missing" in ids:
        code = IntentRejectionCode.capability_missing
    elif "affordance_missing" in ids:
        code = IntentRejectionCode.affordance_missing
    elif "knowledge_boundary" in ids:
        code = IntentRejectionCode.knowledge_boundary_violation
    elif ids & {"spatial", "destination_required"}:
        code = IntentRejectionCode.spatial_precondition_failed
    else:
        code = IntentRejectionCode.invalid_action
    return IntentParseResult(
        status=IntentStatus.rejected,
        reason_code=code,
        message=result.why(),
        raw_input=user_text,
        details={"rule_ids": sorted(ids)},
    )
