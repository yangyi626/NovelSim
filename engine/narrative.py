"""Narrative Generator: WorldEvent + WorldState -> 可读叙事。

对应 plan 第七步任务3: "已提交事件 + 角色状态 -> 剧情文字和对白"。

这是表现层 (Observation) 的入口。它不改变世界状态，只把已发生的事实
翻译成玩家可读的旁白、对白和系统提示。

安全约束:
- 输入是"已提交的事实"，不是用户意图——生成必须忠于 patch 描述的变化
- 生成后过 narrative_consistency 审查: 死人不说话、不泄露未知事实
- 审查不过则反馈给 LLM 重试
"""

from __future__ import annotations

import json
import re
from typing import Optional

import openai
from pydantic import ValidationError

from world_schema import (
    Action,
    DialogueLine,
    NarrativeOutput,
    WorldEvent,
    WorldState,
)

from .config import get_llm_config
from .narrative_consistency import check_narrative, NarrativeCheckResult


SYSTEM_PROMPT = """你是一个小说叙事生成器。系统刚刚在世界里发生了一个事件，你需要把它写成玩家可读的叙事。

# 任务
根据已提交的事件和世界状态，输出一段叙事: 旁白 + 对白 + 系统提示。

# 严格规则
1. 只输出一个 JSON 对象，不要解释、不要 markdown 代码块。
2. 必须忠于已提交的事件——只能描写事件 patch 里描述的变化，不能编造没发生的事。
3. 对白说话者必须是存活的角色，且只能说自己知道的事。
4. 不要让角色说出他不该知道的秘密 (角色认知在"认知"一节列出)。
5. 旁白用第三人称，文笔要有小说感，呼应古风/穿越题材。
6. 对白要符合角色身份和当前情绪 (情绪在角色状态里)。
7. system_hints 用现代游戏提示口吻，告知玩家状态变化 (如"夜清清对你的敌意上升")。

# 输出格式
{
  "narration": "...(旁白，描写发生了什么)...",
  "dialogues": [
    {"speaker_id": "角色id", "line": "...", "tone": "语气", "to_id": "对谁说(可选)"}
  ],
  "system_hints": ["...玩家提示..."],
  "viewpoint": "third_person"
}
"""


class NarrativeGenerator:
    """把已提交的事件转成叙事文本。"""

    def __init__(self, model: Optional[str] = None, max_retries: int = 2):
        cfg = get_llm_config()
        self.api_key = cfg.api_key
        self.base_url = cfg.base_url
        self.model = model or cfg.model
        self.max_retries = max_retries
        openai.api_key = self.api_key
        openai.api_base = self.base_url
        self.last_error: Optional[str] = None

    def generate(
        self,
        event: WorldEvent,
        state: WorldState,
        action: Optional[Action] = None,
    ) -> Optional[NarrativeOutput]:
        """生成叙事。失败返回 None (查 last_error)。"""
        self.last_error = None
        context = self._build_context(event, state, action)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ]

        last_raw = ""
        for attempt in range(self.max_retries + 1):
            raw = self._call_llm(messages)
            last_raw = raw
            narrative = self._try_build(raw)
            if narrative is None:
                if attempt < self.max_retries:
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({
                        "role": "user",
                        "content": "输出无法解析为合法 JSON。请只输出一个 JSON 对象。",
                    })
                continue

            # 一致性审查
            check = check_narrative(narrative, event, state)
            if check.valid:
                return narrative
            # 有 error 级违规，反馈重试
            errs = check.errors()
            if errs and attempt < self.max_retries:
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": f"叙事有违规，请修正:\n{check.why()}",
                })
            elif errs:
                self.last_error = f"consistency failed: {check.why()}"
            else:
                # 只有 warning，放行
                return narrative

        if self.last_error is None:
            self.last_error = f"parse failed, last raw: {last_raw[:200]}"
        return None

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _build_context(
        self, event: WorldEvent, state: WorldState, action: Optional[Action]
    ) -> str:
        lines = [
            "# 世界背景",
            f"场景: {state.current_scene_id} ({self._loc_name(state)})",
            f"时间: {state.world_time}",
        ]

        # 触发行动 (如果有)
        if action:
            lines.append(f"\n# 触发行动\n玩家({action.actor.actor_id})执行了: {action.action_type.value}")
            if action.declared_goal:
                lines.append(f"目标: {action.declared_goal}")

        # 已提交事件: 把 patch 翻译成"发生了什么"的清单
        lines.append("\n# 已发生的事件 (必须忠于这些变化)")
        lines.append(f"事件类型: {event.event_type}")
        if event.patch.operations:
            for op in event.patch.operations:
                desc = self._describe_op(op, state)
                if desc:
                    lines.append(f"- {desc}")
        else:
            lines.append("- (无实质状态变化)")

        # 相关角色的状态与认知 (让 LLM 知道谁在场、知道什么)
        involved = set(event.actor_ids) | set(event.target_ids)
        scene_chars = [c for c in state.characters.values()
                       if c.location_id == state.current_scene_id]
        involved.update(c.character_id for c in scene_chars)

        lines.append("\n## 在场角色")
        for cid in involved:
            ch = state.characters.get(cid)
            if not ch:
                continue
            lines.append(f"- {cid} | {ch.display_name} | 身份:{ch.identity_tags} | "
                         f"存活:{ch.is_alive}")

        # 认知 (关键: 告诉 LLM 每个角色知道什么，防认知泄漏)
        lines.append("\n## 角色认知 (对白绝不能超出各自认知)")
        for cid in involved:
            bs = state.beliefs.get(cid, [])
            known = [f"{b.fact_id}={b.belief.value}" for b in bs[:5]]
            if known:
                lines.append(f"- {cid} 知道: {', '.join(known)}")

        return "\n".join(lines)

    def _describe_op(self, op, state: WorldState) -> str:
        """把一条 Operation 翻译成人话，给 LLM 看。"""
        k = op.op.value
        reason = f" (理由: {op.reason})" if op.reason else ""
        if k == "transfer_item":
            item = state.items.get(op.item_id or "")
            return f"{op.target_id} 获得物品 {op.item_id}({item.display_name if item else '?'}){reason}"
        if k == "update_relation":
            return (f"{op.source_id} 对 {op.target_id} 的 {op.dimension} "
                    f"变化 {op.delta:+.2f}{reason}")
        if k == "set_flag":
            return f"标记 {op.path} = {op.value}{reason}"
        if k == "set_attr":
            return f"{op.path} = {op.value}{reason}"
        if k == "update_belief":
            return f"{op.target_id} 对 {op.fact_id} 的认知变为 {op.belief}{reason}"
        if k == "move_character":
            return f"{op.target_id} 移动到 {op.location_id}{reason}"
        return f"{k}: {op.reason or '无说明'}"

    def _loc_name(self, state: WorldState) -> str:
        loc = state.locations.get(state.current_scene_id or "")
        return loc.display_name if loc else (state.current_scene_id or "?")

    def _call_llm(self, messages: list) -> str:
        resp = openai.ChatCompletion.create(
            model=self.model, messages=messages, temperature=0.7,  # 叙事要创造性，高温
        )
        return resp.choices[0].message.content.strip()

    def _try_build(self, raw: str) -> Optional[NarrativeOutput]:
        data = _extract_json(raw)
        if data is None:
            return None
        try:
            return NarrativeOutput.parse_obj(data)
        except ValidationError:
            return None


def _extract_json(raw: str) -> Optional[dict]:
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
