"""Patch 校验器: 在应用前检查 StatePatch 合法性。

和 apply_patch 的关系:
- apply_patch 在"应用时"会做实体存在性/数值校验，失败抛 PatchError
- patch_validator 在"应用前"做同样的检查，但**不抛异常**，返回违规列表
  这样可以把违规反馈给 LLM 让它重试，而不是半途崩溃

两者共享同一套校验语义 (实体存在、数值范围、维度合法)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from world_schema import (
    Action,
    AllianceState,
    BeliefEvidence,
    OperationKind,
    PropagationRecord,
    StatePatch,
    WorldState,
)
from world_schema.models import Belief


@dataclass
class PatchViolation:
    op_index: int
    rule_id: str
    message: str


@dataclass
class PatchCheckResult:
    valid: bool
    violations: List[PatchViolation] = field(default_factory=list)

    def why(self) -> str:
        return "; ".join(f"op[{v.op_index}] {v.rule_id}: {v.message}" for v in self.violations)


# 合法的 relation 维度 (来自 RelationDimensions)
_VALID_DIMS = {"affection", "trust", "fear", "hostility", "respect", "debt"}
_VALID_BELIEFS = {b.value for b in Belief}


def validate_patch(state: WorldState, patch: StatePatch) -> PatchCheckResult:
    """校验 patch 是否可安全应用。不修改 state。"""
    violations: List[PatchViolation] = []

    for idx, op in enumerate(patch.operations):
        _check_one(state, op, idx, violations)

    evidence_ids = {
        op.evidence_id or op.path
        for op in patch.operations
        if op.op == OperationKind.record_evidence
    }
    known_evidence = set(state.belief_evidence) | evidence_ids
    for idx, op in enumerate(patch.operations):
        if op.op != OperationKind.record_propagation:
            continue
        try:
            record = PropagationRecord.parse_obj(op.value or {})
        except Exception:
            continue
        if record.evidence_id not in known_evidence:
            violations.append(
                PatchViolation(
                    idx,
                    "propagation_evidence_exists",
                    f"unknown propagation evidence: {record.evidence_id}",
                )
            )

    return PatchCheckResult(valid=not violations, violations=violations)


def _check_one(state: WorldState, op, idx: int, out: List[PatchViolation]) -> None:
    def fail(rule_id: str, msg: str):
        out.append(PatchViolation(idx, rule_id, msg))

    k = op.op

    # 通用: 所有引用实体的字段都要存在
    if op.target_id and op.target_id not in state.characters and op.target_id not in state.items:
        # transfer_item 的 target 可以是角色 (owner)，单独放过
        if k.value == "transfer_item" and op.target_id in state.characters:
            pass
        else:
            fail("target_exists", f"unknown target_id: {op.target_id}")

    if op.source_id and op.source_id not in state.characters:
        fail("source_exists", f"unknown source_id: {op.source_id}")

    if k.value in ("move_character", "kill_character", "revive_character",
                   "change_identity") and op.target_id and op.target_id not in state.characters:
        fail("char_exists", f"target is not a character: {op.target_id}")

    if k.value == "move_character":
        if not op.target_id:
            fail("target_present", "move_character needs target_id")
        if not op.location_id:
            fail("location_present", "move_character needs location_id")
        elif op.location_id not in state.locations:
            fail("location_exists", f"unknown location_id: {op.location_id}")

    # update_psyche / advance_plan: 角色必须有 psyche (且非玩家)
    if k.value in ("update_psyche", "advance_plan"):
        if not op.target_id:
            fail("target_present", f"{k.value} needs target_id")
        elif op.target_id not in state.character_psyches:
            fail("psyche_exists", f"{k.value}: no psyche for {op.target_id}")
        else:
            psy = state.character_psyches[op.target_id]
            if psy.is_player:
                fail("not_player", f"{k.value} 不能作用于玩家宿主 {op.target_id}")
        if k.value == "advance_plan" and op.plan_id:
            psy = state.character_psyches.get(op.target_id)
            if psy and not any(p.plan_id == op.plan_id for p in psy.plans):
                fail("plan_exists", f"unknown plan: {op.plan_id}")
        if k.value == "update_psyche":
            if op.intensity is not None and not (0.0 <= op.intensity <= 1.0):
                fail("intensity_range", f"intensity {op.intensity} out of [0,1]")

    # 物品操作: item 必须存在
    if k.value in ("transfer_item", "destroy_item"):
        iid = op.item_id or op.path
        if iid not in state.items:
            fail("item_exists", f"unknown item: {iid}")

    # update_relation: 维度名合法 + source/target 是角色
    if k.value == "update_relation":
        if op.dimension not in _VALID_DIMS:
            fail("dim_valid", f"unknown relation dimension: {op.dimension}")
        if op.delta is None:
            fail("delta_present", "update_relation needs delta")
        elif not (-2.0 <= op.delta <= 2.0):
            # 单次变化幅度限制，防 LLM 暴走
            fail("delta_range", f"delta {op.delta} out of [-2, 2]")

    # update_belief: belief 合法
    if k.value == "update_belief":
        if op.belief is not None and op.belief.value not in _VALID_BELIEFS:
            fail("belief_valid", f"unknown belief: {op.belief}")
        if op.confidence is not None and not (0.0 <= op.confidence <= 1.0):
            fail("confidence_range", f"confidence {op.confidence} out of [0,1]")
        fact_id = op.fact_id or op.path
        if state.facts and fact_id not in state.facts:
            fail("fact_exists", f"unknown fact: {fact_id}")
        if (
            op.source_character_id
            and op.source_character_id not in state.characters
        ):
            fail(
                "belief_source_exists",
                f"unknown belief source: {op.source_character_id}",
            )

    if k.value == "record_evidence":
        try:
            evidence = BeliefEvidence.parse_obj(op.value or {})
        except Exception as exc:
            fail("evidence_schema", f"invalid evidence: {exc}")
        else:
            evidence_id = op.evidence_id or op.path
            if not evidence_id:
                fail("evidence_id_present", "record_evidence needs evidence_id")
            elif evidence_id != evidence.evidence_id:
                fail("evidence_id_matches", "evidence id mismatch")
            elif evidence_id in state.belief_evidence:
                fail("evidence_unique", f"duplicate evidence: {evidence_id}")
            if evidence.holder_id not in state.characters:
                fail(
                    "evidence_holder_exists",
                    f"unknown evidence holder: {evidence.holder_id}",
                )
            if state.facts and evidence.fact_id not in state.facts:
                fail("evidence_fact_exists", f"unknown fact: {evidence.fact_id}")
            if (
                evidence.source_character_id
                and evidence.source_character_id not in state.characters
            ):
                fail(
                    "evidence_source_exists",
                    f"unknown evidence source: {evidence.source_character_id}",
                )

    if k.value == "record_propagation":
        try:
            record = PropagationRecord.parse_obj(op.value or {})
        except Exception as exc:
            fail("propagation_schema", f"invalid propagation: {exc}")
        else:
            propagation_id = op.propagation_id or op.path
            if not propagation_id:
                fail(
                    "propagation_id_present",
                    "record_propagation needs propagation_id",
                )
            elif propagation_id != record.propagation_id:
                fail("propagation_id_matches", "propagation id mismatch")
            elif any(
                item.propagation_id == propagation_id
                for item in state.propagation_history
            ):
                fail(
                    "propagation_unique",
                    f"duplicate propagation: {propagation_id}",
                )
            for character_id in (
                record.source_character_id,
                record.target_character_id,
            ):
                if character_id not in state.characters:
                    fail(
                        "propagation_character_exists",
                        f"unknown propagation character: {character_id}",
                    )
            if state.facts and record.fact_id not in state.facts:
                fail(
                    "propagation_fact_exists",
                    f"unknown fact: {record.fact_id}",
                )

    if k.value == "form_alliance":
        try:
            alliance = AllianceState.parse_obj(op.value or {})
        except Exception as exc:
            fail("alliance_schema", f"invalid alliance: {exc}")
        else:
            alliance_id = op.alliance_id or op.path
            if not alliance_id:
                fail("alliance_id_present", "form_alliance needs alliance_id")
            elif alliance_id != alliance.alliance_id:
                fail("alliance_id_matches", "alliance id mismatch")
            elif alliance_id in state.alliances:
                fail(
                    "alliance_unique",
                    f"alliance already exists: {alliance_id}",
                )
            if len(set(alliance.member_ids)) < 2:
                fail(
                    "alliance_member_count",
                    "alliance requires at least two distinct members",
                )
            for character_id in alliance.member_ids:
                if character_id not in state.characters:
                    fail(
                        "alliance_member_exists",
                        f"unknown alliance member: {character_id}",
                    )

    # set_flag / set_attr: path 非空
    if k.value in ("set_flag", "set_attr") and not op.path:
        fail("path_present", f"{k.value} needs path")

    if k.value == "increment_value" and op.delta is None:
        fail("delta_present", "increment_value needs delta")


def validate_action_patch(
    state: WorldState,
    action: Action,
    patch: StatePatch,
) -> PatchCheckResult:
    """校验 Patch 是否由当前 Action 授权。

    ``validate_patch`` 只证明操作本身结构合法；本函数进一步证明“为什么允许
    这个 Action 产生这些操作”，防止 LLM 借一个普通动作顺带改身份、剧情或
    任意属性。
    """

    base = validate_patch(state, patch)
    violations = list(base.violations)

    def fail(index: int, rule_id: str, message: str) -> None:
        violations.append(PatchViolation(index, rule_id, message))

    evidence = patch.causal_evidence
    if evidence is None:
        fail(-1, "causal_evidence_present", "patch lacks causal evidence")
    else:
        if evidence.action_id != action.action_id:
            fail(
                -1,
                "causal_action_matches",
                f"evidence action {evidence.action_id} != {action.action_id}",
            )
        if evidence.actor_id != action.actor.actor_id:
            fail(
                -1,
                "causal_actor_matches",
                f"evidence actor {evidence.actor_id} != {action.actor.actor_id}",
            )

    policy = state.action_policies.get(action.action_type.value)
    allowed = (
        set(policy.allowed_patch_operations)
        if policy is not None
        else _default_allowed_operations(action.action_type.value)
    )
    target_items = {
        target_id for target_id in action.target_ids if target_id in state.items
    }
    involved_characters = {
        action.actor.actor_id,
        *[
            target_id
            for target_id in action.target_ids
            if target_id in state.characters
        ],
        *[
            state.items[item_id].owner_id
            for item_id in target_items
            if state.items[item_id].owner_id in state.characters
        ],
    }

    for index, op in enumerate(patch.operations):
        op_name = op.op.value
        if op_name not in allowed:
            fail(
                index,
                "patch_not_authorized",
                f"{action.action_type.value} cannot produce {op_name}",
            )
            continue

        if op.op == OperationKind.move_character:
            if op.target_id != action.actor.actor_id:
                fail(
                    index,
                    "move_actor_matches",
                    "move can only relocate the acting character",
                )
            destination_id = action.parameters.get("destination_id")
            expected_location = destination_id
            if destination_id in state.characters:
                expected_location = state.characters[destination_id].location_id
            if expected_location and op.location_id != expected_location:
                fail(
                    index,
                    "move_destination_matches",
                    f"patch destination {op.location_id} != requested {expected_location}",
                )

        elif op.op == OperationKind.transfer_item:
            item_id = op.item_id or op.path
            if target_items and item_id not in target_items:
                fail(
                    index,
                    "transfer_item_matches",
                    f"patch item {item_id} was not targeted by the action",
                )
            if op.target_id not in involved_characters:
                fail(
                    index,
                    "transfer_receiver_matches",
                    f"receiver {op.target_id} is outside the action participants",
                )

        elif op.op in (OperationKind.update_relation, OperationKind.set_relation):
            if (
                op.source_id not in involved_characters
                or op.target_id not in involved_characters
            ):
                fail(
                    index,
                    "relation_participants_match",
                    "relation patch references a non-participant",
                )

        elif op.op == OperationKind.update_belief:
            if op.target_id not in involved_characters:
                fail(
                    index,
                    "belief_target_matches",
                    f"belief target {op.target_id} is outside the action participants",
                )

        elif op.op in (OperationKind.kill_character, OperationKind.change_identity):
            if op.target_id not in set(action.target_ids):
                fail(
                    index,
                    "destructive_target_matches",
                    f"destructive target {op.target_id} was not targeted",
                )

        elif op.op == OperationKind.set_attr:
            scope = op.path.split(".", 1)[0] if op.path else ""
            if scope not in involved_characters and scope not in target_items:
                fail(
                    index,
                    "attribute_scope_matches",
                    f"attribute scope {scope} is outside the action",
                )

    return PatchCheckResult(valid=not violations, violations=violations)


def validate_tool_patch(
    state: WorldState,
    *,
    tool_name: str,
    call_id: str,
    actor_id: str,
    arguments: dict,
    output: dict,
    allowed_operations,
    patch: StatePatch,
) -> PatchCheckResult:
    """校验 ToolCall→ToolResult→Patch 的因果边界。"""

    base = validate_patch(state, patch)
    violations = list(base.violations)

    def fail(index: int, rule_id: str, message: str) -> None:
        violations.append(PatchViolation(index, rule_id, message))

    evidence = patch.causal_evidence
    if evidence is None:
        fail(-1, "causal_evidence_present", "tool patch lacks causal evidence")
    else:
        if evidence.tool_call_id != call_id or evidence.tool_name != tool_name:
            fail(
                -1,
                "tool_evidence_matches",
                "patch evidence does not match the active tool call",
            )
        if evidence.actor_id != actor_id:
            fail(-1, "tool_actor_matches", "patch actor does not match tool caller")

    allowed = {
        value.value if isinstance(value, OperationKind) else str(value)
        for value in allowed_operations
    }
    for index, op in enumerate(patch.operations):
        if op.op.value not in allowed:
            fail(
                index,
                "patch_not_authorized",
                f"{tool_name} cannot produce {op.op.value}",
            )
            continue
        if tool_name == "move_to":
            if op.target_id != actor_id:
                fail(index, "move_actor_matches", "move_to can only move its caller")
            expected = output.get("location_id")
            if expected and op.location_id != expected:
                fail(index, "move_destination_matches", "move_to changed destination")
        elif tool_name == "pick_up":
            if op.item_id != arguments.get("item_id") or op.target_id != actor_id:
                fail(index, "pickup_matches", "pick_up patch changed item or receiver")
        elif tool_name == "give_item":
            if (
                op.item_id != arguments.get("item_id")
                or op.target_id != arguments.get("target_character_id")
            ):
                fail(index, "give_item_matches", "give_item patch changed item or receiver")
        elif tool_name == "destroy_item":
            if op.item_id != arguments.get("item_id"):
                fail(
                    index,
                    "destroy_item_matches",
                    "destroy_item patch changed the requested item",
                )
        elif tool_name == "observe":
            if op.op == OperationKind.update_belief and (
                op.target_id != actor_id
                or op.fact_id != arguments.get("fact_id")
            ):
                fail(index, "observed_fact_matches", "observe patch changed actor or fact")
            elif op.op == OperationKind.record_evidence:
                evidence_value = op.value or {}
                if (
                    evidence_value.get("holder_id") != actor_id
                    or evidence_value.get("fact_id") != arguments.get("fact_id")
                ):
                    fail(
                        index,
                        "observation_evidence_matches",
                        "observe evidence changed actor or fact",
                    )
        elif tool_name == "share_information":
            if op.op == OperationKind.update_belief and (
                op.target_id != arguments.get("target_character_id")
                or op.fact_id != arguments.get("fact_id")
            ):
                fail(
                    index,
                    "shared_fact_matches",
                    "share_information patch changed recipient or fact",
                )
            elif op.op == OperationKind.record_evidence:
                evidence_value = op.value or {}
                if (
                    evidence_value.get("holder_id")
                    != arguments.get("target_character_id")
                    or evidence_value.get("fact_id") != arguments.get("fact_id")
                    or evidence_value.get("source_character_id") != actor_id
                ):
                    fail(
                        index,
                        "shared_evidence_matches",
                        "share_information evidence changed source, recipient, or fact",
                    )
            elif op.op == OperationKind.record_propagation:
                record_value = op.value or {}
                if (
                    record_value.get("source_character_id") != actor_id
                    or record_value.get("target_character_id")
                    != arguments.get("target_character_id")
                    or record_value.get("fact_id") != arguments.get("fact_id")
                ):
                    fail(
                        index,
                        "propagation_record_matches",
                        "propagation record changed source, recipient, or fact",
                    )
        elif tool_name == "propose_alliance":
            alliance_value = op.value or {}
            if (
                set(alliance_value.get("member_ids") or [])
                != {
                    actor_id,
                    arguments.get("target_character_id"),
                }
                or alliance_value.get("goal_key") != arguments.get("goal_key")
                or arguments.get("shared_fact_id")
                not in (alliance_value.get("shared_fact_ids") or [])
            ):
                fail(
                    index,
                    "alliance_matches",
                    "propose_alliance patch changed members, goal, or fact",
                )

    return PatchCheckResult(valid=not violations, violations=violations)


def _default_allowed_operations(action_type: str):
    return {
        "move": {"move_character"},
        "swap_object": {"transfer_item", "update_relation"},
        "speak": {"update_relation", "update_belief"},
        "observe": {"update_belief"},
        "investigate": {"update_belief"},
        "gift": {"transfer_item", "update_relation"},
        "use_item": {"set_attr", "update_belief"},
        "attack": {"set_attr", "update_relation", "kill_character"},
    }.get(action_type, set())
