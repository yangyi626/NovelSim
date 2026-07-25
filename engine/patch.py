"""apply_patch: 把 StatePatch 应用到 WorldState，返回新状态。

纯函数: 不修改输入 state，返回一份深拷贝后的新状态。
这是整个系统的"状态转移原语"，规则引擎、事件提交、回放都依赖它的语义。
"""

from __future__ import annotations

import copy
from typing import Any

from world_schema import (
    Character,
    CharacterBelief,
    CharacterRelation,
    Item,
    Location,
    Operation,
    OperationKind,
    PlotArc,
    RelationDimensions,
    StatePatch,
    WorldState,
)
from world_schema.models import Belief


class PatchError(ValueError):
    """Patch 应用失败。携带 op 索引和原因，便于调试。"""


def _get_char(state: WorldState, cid: str) -> Character:
    char = state.characters.get(cid)
    if char is None:
        raise PatchError(f"unknown character: {cid}")
    return char


def _get_or_create_relation(
    state: WorldState, source_id: str, target_id: str
) -> CharacterRelation:
    for rel in state.relations:
        if rel.source_id == source_id and rel.target_id == target_id:
            return rel
    rel = CharacterRelation(source_id=source_id, target_id=target_id)
    state.relations.append(rel)
    return rel


def _get_or_create_belief(
    state: WorldState, cid: str, fact_id: str
) -> CharacterBelief:
    bucket = state.beliefs.setdefault(cid, [])
    for b in bucket:
        if b.fact_id == fact_id:
            return b
    b = CharacterBelief(fact_id=fact_id)
    bucket.append(b)
    return b


# flags 是扁平 dict: key 即完整点号串 'plot.poisoning_prevented'。
# 设计取舍: flags 本质是"自由布尔/数值开关"，扁平化让 set/get 天然一致，
# 无需处理中间节点缺失。嵌套结构留给实体 attrs。
def _set_flat(d: dict, path: str, value: Any) -> None:
    if not path:
        raise PatchError("empty path for set_flag")
    d[path] = value


def _apply_one(state: WorldState, op: Operation) -> None:
    k = op.op

    if k == OperationKind.set_flag:
        _set_flat(state.flags, op.path, op.value)
        return

    if k == OperationKind.set_attr:
        # path 形如 "<character_id>.cultivation_level" 或 flags 路径
        # 简化: 若首段是已知角色/物品 id，写到它的 attrs；否则按全局 flags 路径写
        head = op.path.split(".", 1)[0] if op.path else ""
        rest = op.path.split(".", 1)[1] if "." in op.path else ""
        if head in state.characters:
            state.characters[head].attrs[rest] = op.value
        elif head in state.items:
            state.items[head].attrs[rest] = op.value
        elif head in state.locations:
            state.locations[head].attrs[rest] = op.value
        else:
            _set_flat(state.flags, op.path, op.value)
        return

    if k == OperationKind.increment_value:
        head = op.path.split(".", 1)[0] if op.path else ""
        rest = op.path.split(".", 1)[1] if "." in op.path else ""
        if op.delta is None:
            raise PatchError(f"increment requires delta: {op.path}")
        cur = 0
        if head in state.characters and rest in state.characters[head].attrs:
            cur = state.characters[head].attrs.get(rest, 0) or 0
            state.characters[head].attrs[rest] = cur + op.delta
        else:
            cur = state.flags.get(op.path, 0) or 0
            state.flags[op.path] = cur + op.delta
        return

    if k == OperationKind.move_character:
        cid = op.target_id or op.path
        if cid not in state.characters:
            raise PatchError(f"move unknown character: {cid}")
        state.characters[cid].location_id = op.location_id
        return

    if k == OperationKind.transfer_item:
        item_id = op.item_id or op.path
        if item_id not in state.items:
            raise PatchError(f"transfer unknown item: {item_id}")
        item = state.items[item_id]
        # 从原 owner 的 inventory 移除
        if item.owner_id and item.owner_id in state.characters:
            inv = state.characters[item.owner_id].inventory
            if item_id in inv:
                inv.remove(item_id)
        item.owner_id = op.target_id
        item.location_id = None
        if op.target_id and op.target_id in state.characters:
            if item_id not in state.characters[op.target_id].inventory:
                state.characters[op.target_id].inventory.append(item_id)
        return

    if k == OperationKind.update_relation:
        src = op.source_id or ""
        tgt = op.target_id or ""
        if not src or not tgt:
            raise PatchError("update_relation needs source_id & target_id")
        if op.dimension is None or op.delta is None:
            raise PatchError("update_relation needs dimension & delta")
        rel = _get_or_create_relation(state, src, tgt)
        old = getattr(rel.dimensions, op.dimension, 0.0) or 0.0
        # 数值规则: 钳制到合法区间
        lo, hi = _dim_bounds(op.dimension)
        new = max(lo, min(hi, old + op.delta))
        setattr(rel.dimensions, op.dimension, new)
        return

    if k == OperationKind.set_relation:
        src = op.source_id or ""
        tgt = op.target_id or ""
        rel = _get_or_create_relation(state, src, tgt)
        if op.value and isinstance(op.value, dict):
            for kk, vv in op.value.items():
                if hasattr(rel.dimensions, kk):
                    setattr(rel.dimensions, kk, vv)
                else:
                    setattr(rel, kk, vv)  # public_relation 等
        return

    if k == OperationKind.update_belief:
        cid = op.target_id or ""
        fact_id = op.fact_id or op.path
        if not cid or not fact_id:
            raise PatchError("update_belief needs target_id & fact_id")
        b = _get_or_create_belief(state, cid, fact_id)
        if op.belief is not None:
            b.belief = op.belief
        if op.confidence is not None:
            b.confidence = max(0.0, min(1.0, op.confidence))
        if op.source_type and op.source_type != "unknown":
            b.source_type = op.source_type
        return

    if k == OperationKind.kill_character:
        cid = op.target_id or op.path
        char = _get_char(state, cid)
        char.is_alive = False
        return

    if k == OperationKind.revive_character:
        cid = op.target_id or op.path
        char = _get_char(state, cid)
        char.is_alive = True
        return

    if k == OperationKind.change_identity:
        cid = op.target_id or op.path
        char = _get_char(state, cid)
        if op.tags is not None:
            char.identity_tags = list(op.tags)
        return

    if k == OperationKind.start_plot:
        arc_id = op.target_id or op.path
        arc = state.plot.get(arc_id) or PlotArc(arc_id=arc_id, title=arc_id)
        arc.stage = "active"
        arc.completed = False
        state.plot[arc_id] = arc
        return

    if k == OperationKind.advance_plot:
        arc_id = op.target_id or op.path
        arc = state.plot.get(arc_id)
        if arc is None:
            raise PatchError(f"advance unknown plot: {arc_id}")
        arc.stage = str(op.value) if op.value is not None else arc.stage
        return

    if k == OperationKind.complete_plot:
        arc_id = op.target_id or op.path
        arc = state.plot.get(arc_id)
        if arc is None:
            raise PatchError(f"complete unknown plot: {arc_id}")
        arc.completed = True
        arc.stage = "completed"
        return

    if k == OperationKind.update_psyche:
        # target_id = 角色；更新情绪/情绪强度/新增感知
        cid = op.target_id or ""
        if not cid:
            raise PatchError("update_psyche needs target_id")
        psy = state.character_psyches.get(cid)
        if psy is None:
            raise PatchError(f"update_psyche: no psyche for {cid}")
        if op.emotion:
            psy.emotion = op.emotion
        if op.intensity is not None:
            psy.emotion_intensity = max(0.0, min(1.0, op.intensity))
        if op.perception:
            # 工作记忆只保留最近 N 条，防无限增长
            psy.recent_perceptions.append(op.perception)
            del psy.recent_perceptions[:-10]
        return

    if k == OperationKind.advance_plan:
        # target_id = 角色；plan_id 指定计划，推进 current_step
        cid = op.target_id or ""
        if not cid:
            raise PatchError("advance_plan needs target_id")
        psy = state.character_psyches.get(cid)
        if psy is None:
            raise PatchError(f"advance_plan: no psyche for {cid}")
        if not psy.plans:
            return
        plan = None
        if op.plan_id:
            for p in psy.plans:
                if p.plan_id == op.plan_id:
                    plan = p
                    break
        if plan is None:
            # 没指定就推第一个 active 计划
            plan = next((p for p in psy.plans if p.status == "active"), psy.plans[0])
        step = op.step_delta if op.step_delta is not None else 1
        plan.current_step = max(0, min(len(plan.steps), plan.current_step + step))
        if plan.steps and plan.current_step >= len(plan.steps):
            plan.status = "completed"
        return

    raise PatchError(f"unsupported operation: {k}")


def _dim_bounds(dimension: str):
    """关系维度的合法区间。affection/trust/respect/debt 可负，fear/hostility 非负。"""
    non_negative = {"fear", "hostility"}
    if dimension in non_negative:
        return 0.0, 1.0
    return -1.0, 1.0


def apply_patch(state: WorldState, patch: StatePatch) -> WorldState:
    """纯函数: 返回应用 patch 后的新 state，不修改输入。

    逐条应用 operations；任一失败则抛 PatchError，输入 state 保持不变
    (因为我们在副本上操作)。
    """
    new_state: WorldState = copy.deepcopy(state)
    for idx, op in enumerate(patch.operations):
        try:
            _apply_one(new_state, op)
        except PatchError:
            raise
        except Exception as e:  # noqa: BLE001
            raise PatchError(f"op[{idx}] {op.op} failed: {e}") from e
    new_state.version = state.version  # version 由 commit_event 推动，这里不动
    return new_state
