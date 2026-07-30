"""确定性规则引擎。

MVP 阶段不用 LLM 判断成功/失败。这里实现一组硬编码的"动作合法性校验"，
覆盖 plan.md 第七节列出的时空/物品/身份/能力/认知约束。

规则以 Rule 模型承载 (preconditions/effects DSL)，但本阶段为了快速验证，
action_type -> 一组内置校验函数。后续再外挂 YAML 规则。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Set

from world_schema import Action, ActionPolicy, ActionType, WorldState


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


def _action_policy(state: WorldState, action: Action) -> Optional[ActionPolicy]:
    return state.action_policies.get(action.action_type.value)


def _check_action_policy(
    state: WorldState,
    action: Action,
    out: List[RuleViolation],
) -> None:
    """检查 Action 形状；尤其禁止无目的地的 move 被当作成功移动。"""

    policy = _action_policy(state, action)
    if policy is not None:
        for name in policy.required_parameters:
            value = action.parameters.get(name)
            if value is None or value == "" or value == []:
                out.append(
                    RuleViolation(
                        "required_parameter",
                        f"{action.action_type.value} requires parameter {name}",
                    )
                )
        if policy.requires_target and not action.target_ids:
            out.append(
                RuleViolation(
                    "target_required",
                    f"{action.action_type.value} requires at least one target",
                )
            )

    if action.action_type == ActionType.move:
        destination_id = action.parameters.get("destination_id")
        if not destination_id:
            if policy is None:
                out.append(
                    RuleViolation(
                        "destination_required",
                        "move requires parameters.destination_id",
                    )
                )
            return
        if (
            destination_id not in state.locations
            and destination_id not in state.characters
        ):
            out.append(
                RuleViolation(
                    "destination_exists",
                    f"unknown move destination: {destination_id}",
                )
            )


def _concept_ids(action: Action) -> List[str]:
    raw = action.parameters.get("concept_ids") or []
    if isinstance(raw, str):
        return [raw]
    return [str(value) for value in raw]


def _check_world_concepts(
    state: WorldState,
    action: Action,
    out: List[RuleViolation],
) -> None:
    """检查 Action 显式引用的世界概念。"""

    concept_ids = _concept_ids(action)
    forbidden = {
        concept_id
        for constraint in state.world_constraints
        for concept_id in constraint.forbidden_concept_ids
    }
    for concept_id in concept_ids:
        concept = state.world_concepts.get(concept_id)
        if concept is None:
            out.append(
                RuleViolation(
                    "world_concept_exists",
                    f"unknown world concept: {concept_id}",
                )
            )
            continue
        if not concept.available or concept_id in forbidden:
            out.append(
                RuleViolation(
                    "world_concept_unavailable",
                    f"world concept is unavailable: {concept_id}",
                )
            )

    for constraint in state.world_constraints:
        if not constraint.strict_allowlist or not concept_ids:
            continue
        allowed = set(constraint.allowed_concept_ids)
        for concept_id in concept_ids:
            concept = state.world_concepts.get(concept_id)
            if concept and concept.category == constraint.category and concept_id not in allowed:
                out.append(
                    RuleViolation(
                        "world_concept_not_allowed",
                        f"{concept_id} is not allowed by {constraint.constraint_id}",
                    )
                )


def _enabled_capabilities(state: WorldState, actor_id: str) -> Set[str]:
    return {
        capability.capability_id
        for capability in state.character_capabilities.get(actor_id, [])
        if capability.enabled
    }


def _check_capability_and_affordance(
    state: WorldState,
    action: Action,
    out: List[RuleViolation],
) -> None:
    """检查角色能力，以及被操作实体是否公开当前动作。"""

    required: Set[str] = set()
    policy = _action_policy(state, action)
    if policy is not None:
        required.update(policy.required_capability_ids)

    requested_capability = action.parameters.get("capability_id")
    if requested_capability:
        required.add(str(requested_capability))

    for concept_id in _concept_ids(action):
        concept = state.world_concepts.get(concept_id)
        if concept is not None:
            required.update(concept.required_capability_ids)

    entity_id = None
    if policy is not None and policy.affordance_parameter:
        entity_id = action.parameters.get(policy.affordance_parameter)
    if (
        entity_id is None
        and policy is not None
        and policy.affordance_from_target
        and action.target_ids
    ):
        entity_id = action.target_ids[0]
    if entity_id is None:
        entity_id = (
            action.parameters.get("transport_entity_id")
            or action.parameters.get("entity_id")
        )
    if entity_id:
        if (
            entity_id not in state.items
            and entity_id not in state.characters
            and entity_id not in state.locations
        ):
            out.append(
                RuleViolation(
                    "affordance_entity_exists",
                    f"affordance entity does not exist: {entity_id}",
                )
            )
        else:
            matching = [
                affordance
                for affordance in state.entity_affordances.get(str(entity_id), [])
                if affordance.enabled
                and affordance.action_type == action.action_type.value
            ]
            if not matching:
                out.append(
                    RuleViolation(
                        "affordance_missing",
                        f"{entity_id} does not afford {action.action_type.value}",
                    )
                )
            else:
                for affordance in matching:
                    if (
                        affordance.concept_id
                        and _concept_ids(action)
                        and affordance.concept_id not in _concept_ids(action)
                    ):
                        out.append(
                            RuleViolation(
                                "affordance_concept_matches",
                                f"{entity_id} does not implement requested concept",
                            )
                        )
                    required.update(affordance.required_capability_ids)

    available = _enabled_capabilities(state, action.actor.actor_id)
    missing = sorted(required - available)
    if missing:
        out.append(
            RuleViolation(
                "capability_missing",
                f"actor {action.actor.actor_id} lacks capabilities: {', '.join(missing)}",
            )
        )


_CHECKS: List[Callable[[WorldState, Action, List[RuleViolation]], None]] = [
    _check_alive,
    _check_action_policy,
    _check_world_concepts,
    _check_capability_and_affordance,
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
