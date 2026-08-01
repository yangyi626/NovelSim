"""由权威状态变化派生 NPC 计划进度。

Planner 只能选择工具，不能直接提交 ``advance_plan``。核心工具 Patch 先通过
ToolRegistry 因果门禁；Runtime 再用世界作者声明的步骤条件检查提交前后状态，
并把匹配的计划推进与工具效果放进同一个原子事件。
"""

from __future__ import annotations

from typing import Any, Dict, List

from world_schema import (
    Belief,
    Operation,
    OperationKind,
    PlanConditionKind,
    PlanStepCondition,
    WorldState,
)


def derive_plan_progress_operations(
    before: WorldState,
    after_tool_effects: WorldState,
    *,
    actor_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
) -> List[Operation]:
    """返回本次已验证工具效果应原子追加的计划推进操作。"""

    operations: List[Operation] = []
    for character_id in sorted(before.character_psyches):
        psyche = before.character_psyches[character_id]
        for plan in psyche.plans:
            step_index = plan.current_step
            if (
                plan.status != "active"
                or step_index < 0
                or step_index >= len(plan.steps)
                or step_index >= len(plan.step_conditions)
            ):
                continue
            condition = plan.step_conditions[step_index]
            if not _completed_by_transition(
                condition,
                before,
                after_tool_effects,
                actor_id=actor_id,
                tool_name=tool_name,
                arguments=arguments,
            ):
                continue
            operations.append(
                Operation(
                    op=OperationKind.advance_plan,
                    target_id=character_id,
                    plan_id=plan.plan_id,
                    step_delta=1,
                    reason=(
                        "runtime verified plan step %d after %s"
                        % (step_index, tool_name)
                    ),
                )
            )
    return operations


def _completed_by_transition(
    condition: PlanStepCondition,
    before: WorldState,
    after: WorldState,
    *,
    actor_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
) -> bool:
    if condition.kind == PlanConditionKind.tool_committed:
        return (
            condition.tool_name == tool_name
            and (condition.actor_id is None or condition.actor_id == actor_id)
            and all(
                arguments.get(key) == expected
                for key, expected in condition.argument_equals.items()
            )
        )
    return not _condition_holds(condition, before) and _condition_holds(
        condition,
        after,
    )


def _condition_holds(condition: PlanStepCondition, state: WorldState) -> bool:
    if condition.kind == PlanConditionKind.item_owner:
        item = state.items.get(condition.item_id or "")
        return item is not None and item.owner_id == condition.character_id

    if condition.kind == PlanConditionKind.character_at:
        character = state.characters.get(condition.character_id or "")
        return character is not None and character.location_id == condition.location_id

    if condition.kind == PlanConditionKind.belief_known:
        return any(
            belief.fact_id == condition.fact_id
            and belief.belief != Belief.unknown
            and belief.confidence >= condition.min_confidence
            for belief in state.beliefs.get(condition.character_id or "", [])
        )

    if condition.kind == PlanConditionKind.information_propagated:
        return any(
            record.source_character_id == condition.source_character_id
            and record.target_character_id == condition.target_character_id
            and record.fact_id == condition.fact_id
            for record in state.propagation_history
        )

    if condition.kind == PlanConditionKind.alliance_formed:
        expected_members = set(condition.member_ids)
        return any(
            alliance.status == "active"
            and expected_members.issubset(alliance.member_ids)
            and len(set(alliance.member_ids)) >= condition.minimum_member_count
            and (
                condition.goal_key is None
                or alliance.goal_key == condition.goal_key
            )
            and (
                condition.shared_fact_id is None
                or condition.shared_fact_id in alliance.shared_fact_ids
            )
            for alliance in state.alliances.values()
        )

    return False
