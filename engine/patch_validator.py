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

from world_schema import StatePatch, WorldState
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

    # transfer_item: item 必须存在
    if k.value == "transfer_item":
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

    # set_flag / set_attr: path 非空
    if k.value in ("set_flag", "set_attr") and not op.path:
        fail("path_present", f"{k.value} needs path")

    if k.value == "increment_value" and op.delta is None:
        fail("delta_present", "increment_value needs delta")
