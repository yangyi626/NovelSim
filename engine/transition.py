"""State Transition Proposer: Action + WorldState -> 候选 StatePatch。

对应 plan 第七步任务2: "当前状态 + Action + 规则 -> 候选 StatePatch"。

这是整个系统"让世界活起来"的核心。同样的"拿外衫"动作，LLM 能根据
当前关系、情绪、隐蔽性，生成不同的状态变化: 是否被发现、对方是否起疑等。

安全边界 (比 ActionParser 更严):
- LLM 只产**候选** patch，应用前必须过 patch_validator
- Operation 必须从白名单里选，且字段要符合 schema (extra=forbid)
- 数值范围、实体存在性、维度合法性都由 patch_validator 兜底
- 校验失败 -> 反馈违规给 LLM -> 重试
"""

from __future__ import annotations

import json
import re
from typing import List, Optional

import openai
from pydantic import ValidationError

from world_schema import (
    Action,
    CausalEvidence,
    Operation,
    OperationKind,
    StatePatch,
    WorldState,
)

from .config import get_llm_config
from .llm_telemetry import (
    call_openai_compatible,
    chat_generation_options,
)
from .patch_validator import (
    PatchCheckResult,
    validate_action_patch,
    validate_patch,
)


SYSTEM_PROMPT = """你是一个世界状态转移推演器。玩家执行了一个 Action，你需要推演这个行动对世界状态造成的变化，输出一个 StatePatch。

# 任务
根据 Action 和当前世界状态，输出一个 StatePatch JSON，描述应该发生的状态变化。

# 严格规则
1. 只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块。
2. 每条 operation 必须从下面的合法操作里选，字段必须匹配。
3. 不要编造实体 id。actor_id / target_id / item_id 必须来自当前世界状态。
4. 数值变化要合理: 关系维度 delta 通常在 [-0.5, 0.5] 之间，单次剧烈变化不超过 0.8。
5. 每条 operation 必须带 "reason" 字段，解释为什么这个变化会发生。
6. 只输出"这个行动直接导致"的变化，不要推演连锁反应 (那是后续轮次的事)。
7. 必须遵守上下文中的 ActionPolicy，只能使用它授权的 Patch 操作。

# 合法操作 (op 字段的可选值)
- "set_flag": path=开关名(如"plot.shaming_reversed"), value=布尔或值
- "set_attr": path="角色id.属性名", value=值
- "increment_value": path="角色id.属性名" 或开关名, delta=数值
- "move_character": target_id=角色id, location_id=地点id
- "transfer_item": item_id=物品id, target_id=新持有者角色id
- "update_relation": source_id, target_id, dimension(affection/trust/fear/hostility/respect/debt), delta
- "update_belief": target_id=角色, fact_id=事实名, belief(believed_true/suspected_true/unknown/suspected_false/believed_false), confidence(0-1), source_type(observation/hearsay/inference/secret)
- "kill_character" / "revive_character": target_id=角色id
- "change_identity": target_id=角色id, tags=[身份标签列表]
- "start_plot" / "advance_plot" / "complete_plot": target_id=剧情线id

# 输出格式
{
  "operations": [
    {"op": "...", "reason": "...", ...其他字段}
  ],
  "notes": "整体说明 (可选)"
}

# 推演原则
- 行动有隐蔽性 (covert/hidden) 时，是否被发现取决于环境: 在场人数、行动者的能力、目标的警觉度。
- 关系变化要呼应行动性质: 被羞辱则 hostility 上升、被善待则 affection 可能上升。
- 如果行动本身不直接改变世界 (如纯观察、说话未引发实质后果)，operations 可以为空数组。
"""


class TransitionProposer:
    """把 Action 推演成候选 StatePatch。"""

    def __init__(self, model: Optional[str] = None, max_retries: int = 2):
        cfg = get_llm_config()
        self.api_key = cfg.api_key
        self.base_url = cfg.base_url
        self.model = model or cfg.model
        self.max_retries = max_retries
        openai.api_key = self.api_key
        openai.api_base = self.base_url
        self.last_error: Optional[str] = None

    def propose(
        self, action: Action, state: WorldState
    ) -> Optional[StatePatch]:
        """推演出候选 patch。失败返回 None (查 last_error)。"""
        self.last_error = None
        context = self._build_context(action, state)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ]

        last_raw = ""
        for attempt in range(self.max_retries + 1):
            raw = self._call_llm(messages)
            last_raw = raw
            patch = self._try_build_patch(raw)
            if patch is None:
                if attempt < self.max_retries:
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({
                        "role": "user",
                        "content": "你的输出无法解析为合法 StatePatch JSON。请只输出一个 JSON 对象。",
                    })
                continue

            # patch 解析成功，再做语义校验
            check = validate_patch(state, patch)
            if check.valid:
                authorized = patch.copy(deep=True)
                authorized.causal_evidence = CausalEvidence(
                    action_id=action.action_id,
                    actor_id=action.actor.actor_id,
                    authority="candidate_validation",
                )
                check = validate_action_patch(
                    state,
                    action,
                    authorized,
                )
                if check.valid:
                    return patch
                if attempt >= self.max_retries:
                    # 最后一份候选仍越权时交给 TurnPipeline 的独立权威门禁，
                    # 让调用方获得明确 PATCH_NOT_AUTHORIZED，而非模糊失败。
                    self.last_error = (
                        "action patch validation failed: "
                        f"{check.why()}"
                    )
                    return patch

            # 结构或动作授权失败，带具体违规反馈给 LLM 重试。
            if attempt < self.max_retries:
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": (
                        "你的 patch 有违规，请删除越权变化并修正，只保留当前"
                        f"行动直接授权的操作:\n{check.why()}"
                    ),
                })
            else:
                self.last_error = f"patch validation failed: {check.why()}"

        if self.last_error is None:
            self.last_error = f"parse failed, last raw: {last_raw[:200]}"
        return None

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _build_context(self, action: Action, state: WorldState) -> str:
        """构造推演上下文。比 Parser 给更多: 关系数值、情绪、隐蔽性。"""
        lines = ["# 当前世界状态", f"场景: {state.current_scene_id}", f"时间: {state.world_time}"]

        lines.append("\n## 相关角色 (含情绪与状态)")
        # 只给与 action 相关的角色，减少噪声
        involved = {action.actor.actor_id}
        involved.update(action.target_ids)
        for cid in involved:
            ch = state.characters.get(cid)
            if not ch:
                continue
            lines.append(f"- {cid} | {ch.display_name} | 位置:{ch.location_id} | "
                         f"身份:{ch.identity_tags} | 存活:{ch.is_alive}")
            if ch.attrs:
                lines.append(f"  属性: {json.dumps(ch.attrs, ensure_ascii=False)}")

        # 给出涉及角色之间的关系数值
        rels = [r for r in state.relations
                if r.source_id in involved and r.target_id in involved]
        if rels:
            lines.append("\n## 相关关系 (含数值)")
            for r in rels:
                d = r.dimensions.dict() if hasattr(r.dimensions, 'dict') else dict(r.dimensions)
                lines.append(f"- {r.source_id} -> {r.target_id} | 公开:{r.public_relation} | "
                             f"数值:{json.dumps(d, ensure_ascii=False)}")

        # 当前相关 beliefs (让 LLM 知道角色知道什么)
        for cid in involved:
            bs = state.beliefs.get(cid, [])
            if bs:
                lines.append(f"\n## {cid} 的认知")
                for b in bs[:5]:  # 限制数量
                    lines.append(f"- {b.fact_id}: {b.belief.value} (置信度{b.confidence})")

        if state.world_constraints:
            lines.append("\n## 权威世界约束")
            for constraint in state.world_constraints:
                lines.append(
                    f"- {constraint.constraint_id}: {constraint.statement}"
                )

        policy = state.action_policies.get(action.action_type.value)
        if policy is not None:
            lines.append("\n## 当前 ActionPolicy（不得越权）")
            lines.append(
                "- allowed_patch_operations: "
                f"{policy.allowed_patch_operations}"
            )

        # 场景内所有人 (判断行动是否被目击)
        scene_chars = [c for c in state.characters.values() if c.location_id == state.current_scene_id]
        lines.append(f"\n## 场景内在场角色 ({len(scene_chars)}人)")
        for c in scene_chars:
            lines.append(f"- {c.character_id} | {c.display_name}")

        # Action 本身
        lines.append("\n# 待推演的 Action")
        lines.append(f"type: {action.action_type.value}")
        lines.append(f"actor: {action.actor.actor_id}")
        lines.append(f"targets: {action.target_ids}")
        lines.append(f"parameters: {json.dumps(action.parameters, ensure_ascii=False)}")
        lines.append(f"declared_goal: {action.declared_goal}")
        lines.append(f"visibility: {action.visibility}")

        return "\n".join(lines)

    def _call_llm(self, messages: list) -> str:
        resp = call_openai_compatible(
            openai.ChatCompletion.create,
            operation="transition",
            model=self.model,
            messages=messages,
            temperature=0.4,
            **chat_generation_options(
                self.model,
                max_tokens=1536,
                thinking=False,
            ),
        )
        return resp.choices[0].message.content.strip()

    def _try_build_patch(self, raw: str) -> Optional[StatePatch]:
        data = _extract_json(raw)
        if data is None:
            return None
        try:
            return StatePatch.parse_obj(data)
        except ValidationError:
            return None


def _extract_json(raw: str) -> Optional[dict]:
    """和 ActionParser 共享的 JSON 提取逻辑。"""
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
