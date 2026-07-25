"""确定性规则引擎。

MVP 阶段不用 LLM 判断成功/失败。这里实现一组硬编码的"动作合法性校验"，
覆盖 plan.md 第七节列出的时空/物品/身份/能力/认知约束。

规则以 Rule 模型承载 (preconditions/effects DSL)，但本阶段为了快速验证，
action_type -> 一组内置校验函数。后续再外挂 YAML 规则。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List

from world_schema import Action, ActionType, WorldState


@dataclass
class RuleViolation:
    rule_id: str
    message: str


@dataclass
class RuleCheckResult:
    allowed: bool
    violations: List[RuleViolation] = field(default_factory=list)
    constraints: dict = field(default_factory=dict)

    def why(self) -> str:
        return "; ".join(f"[{v.rule_id}] {v.message}" for v in self.violations)


def _check_alive(state: WorldState, action: Action, out: List[RuleViolation]) -> None:
    actor_id = action.actor.actor_id
    char = state.characters.get(actor_id)
    if char is None:
        out.append(RuleViolation("actor_exists", f"actor not in world: {actor_id}"))
        return
    if not char.is_alive:
        out.append(RuleViolation("actor_alive", f"dead actor cannot act: {actor_id}"))


def _check_location(
    state: WorldState, action: Action, out: List[RuleViolation]
) -> None:
    """行动者与目标物品/角色必须在同一场景，除非 action 显式声明跨场景。"""
    if action.action_type in (ActionType.move,):
        return
    actor_id = action.actor.actor_id
    actor = state.characters.get(actor_id)
    if actor is None:
        return
    actor_loc = actor.location_id
    # 检查目标物品
    for tid in action.target_ids:
        item = state.items.get(tid)
        if item is not None:
            # 物品在某角色身上: 该角色须与 actor 同场景；物品在某地点: 须与 actor 同场景
            if item.owner_id:
                holder = state.characters.get(item.owner_id)
                holder_loc = holder.location_id if holder else None
                if holder_loc != actor_loc:
                    out.append(
                        RuleViolation(
                            "spatial",
                            f"item {tid} holder {item.owner_id} not in scene {actor_loc}",
                        )
                    )
            elif item.location_id and item.location_id != actor_loc:
                out.append(
                    RuleViolation(
                        "spatial",
                        f"item {tid} at {item.location_id}, actor at {actor_loc}",
                    )
                )
        char = state.characters.get(tid)
        if char is not None and char.location_id != actor_loc:
            out.append(
                RuleViolation(
                    "spatial",
                    f"target {tid} at {char.location_id}, actor at {actor_loc}",
                )
            )


def _check_item_owned(
    state: WorldState, action: Action, out: List[RuleViolation]
) -> None:
    """使用/赠予物品时，行动者须持有该物品，或物品在当前场景可达。"""
    if action.action_type not in (ActionType.use_item, ActionType.gift):
        return
    actor_id = action.actor.actor_id
    actor = state.characters.get(actor_id)
    if actor is None:
        return
    for tid in action.target_ids:
        item = state.items.get(tid)
        if item is None:
            out.append(RuleViolation("item_exists", f"unknown item: {tid}"))
            continue
        if item.owner_id != actor_id and not (
            item.location_id and item.location_id == actor.location_id
        ):
            out.append(
                RuleViolation(
                    "item_owned",
                    f"actor {actor_id} does not hold or reach item {tid}",
                )
            )


def _check_knowledge(
    state: WorldState, action: Action, out: List[RuleViolation]
) -> None:
    """speak/调查等行动若引用某 fact_id，角色须对该 fact 有认知 (belief != unknown)。

    简化版: 只检查 parameters.fact_id。
    """
    if action.action_type not in (ActionType.speak, ActionType.investigate):
        return
    fact_id = action.parameters.get("fact_id")
    if not fact_id:
        return
    actor_id = action.actor.actor_id
    beliefs = state.beliefs.get(actor_id, [])
    known = any(b.fact_id == fact_id and b.belief.value != "unknown" for b in beliefs)
    if not known:
        out.append(
            RuleViolation(
                "knowledge_boundary",
                f"actor {actor_id} must know fact {fact_id} to reference it",
            )
        )


_CHECKS: List[Callable[[WorldState, Action, List[RuleViolation]], None]] = [
    _check_alive,
    _check_location,
    _check_item_owned,
    _check_knowledge,
]


class RuleEngine:
    """规则引擎。validate 不修改 state；apply 由 patch 模块负责。"""

    def validate(self, state: WorldState, action: Action) -> RuleCheckResult:
        violations: List[RuleViolation] = []
        for chk in _CHECKS:
            chk(state, action, violations)
        return RuleCheckResult(allowed=not violations, violations=violations)
