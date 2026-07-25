"""完整 Turn Pipeline: 用户输入 -> 叙事输出。

把前面三个 LLM 模块串成一条完整的游戏回合:
    用户自然语言
       ↓ ActionParser (LLM 任务1)
    Action
       ↓ RuleEngine (规则校验)
    合法 Action
       ↓ TransitionProposer (LLM 任务2) + patch_validator
    StatePatch
       ↓ commit_event
    WorldEvent + 新 WorldState
       ↓ NarrativeGenerator (LLM 任务3) + 一致性审查
    NarrativeOutput (玩家看到的文字)

对应 plan 第八步 "一次完整 Turn Pipeline" 的最小实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from world_schema import Action, NarrativeOutput, WorldEvent, WorldState

from .action_parser import ActionParser
from .narrative import NarrativeGenerator
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
    error: str = ""


class TurnPipeline:
    """编排一个完整回合。注入式依赖，便于测试和替换组件。"""

    def __init__(
        self,
        parser: Optional[ActionParser] = None,
        proposer: Optional[TransitionProposer] = None,
        narrator: Optional[NarrativeGenerator] = None,
        engine: Optional[RuleEngine] = None,
    ):
        # 延迟初始化: 只在真正需要时才创建 (避免无 key 时 import 就报错)
        self._parser = parser
        self._proposer = proposer
        self._narrator = narrator
        self._engine = engine or RuleEngine()

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

    def run(
        self,
        user_text: str,
        state: WorldState,
        default_actor_id: str,
        *,
        use_llm_proposer: bool = True,
        use_narrative: bool = True,
    ) -> TurnResult:
        """跑一个完整回合。返回 TurnResult。

        use_llm_proposer=False 时用确定性占位 patch (测试用)。
        use_narrative=False 时跳过叙事生成 (省 token，纯状态变更)。
        """
        # 1. 解析
        action = self._get_parser().parse(user_text, state, default_actor_id=default_actor_id)
        if action is None:
            return TurnResult(status="parse_failed", error="无法解析用户输入")

        # 2. 规则校验
        res = self._engine.validate(state, action)
        if not res.allowed:
            return TurnResult(
                status="rejected", action=action, rule_result=res,
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

        # 4. 提交 (乐观锁: expected_version = state.version)
        from .event import commit_event
        ev, new_state = commit_event(
            state, action_id=action.action_id, event_type=action.action_type.value,
            patch=patch, actor_ids=[action.actor.actor_id], target_ids=action.target_ids,
            expected_version=state.version,
        )

        # 5. 叙事生成
        narrative = None
        if use_narrative:
            narrator = self._get_narrator()
            narrative = narrator.generate(ev, new_state, action)
            if narrative is None:
                # 叙事失败不回滚状态 (状态已提交是事实)，只标记
                return TurnResult(
                    status="narrate_failed", action=action, event=ev,
                    new_state=new_state, error=f"叙事失败: {narrator.last_error}",
                )

        return TurnResult(
            status="committed", action=action, event=ev,
            new_state=new_state, narrative=narrative,
        )
