"""Action Parser: 自然语言 + WorldState -> 结构化 Action。

对应 docs/plan.md 第七步: "接入 LLM，但只开放受控权限"。
本模块只做"理解" —— LLM 把用户的自然语言转成合法的 Action JSON，
但**不**决定行动是否真的执行。合法性由规则引擎兜底校验。

安全边界:
- LLM 只产出候选 Action (提议权)
- 规则引擎决定是否允许 (执行权)
- 即使 Parser 给出非法 actor_id / target_id，Action 构造时的实体校验
  和后续规则校验都会拦住
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

import openai
from pydantic import ValidationError

from world_schema import Action, WorldState
from world_schema.models import Actor, ActionType

from .config import get_llm_config


# 系统提示词。说明任务、约束、输出格式。
# 故意只给 LLM "它需要知道的" 世界信息，而不是整个 state dump。
SYSTEM_PROMPT = """你是一个游戏行动解析器。玩家用自然语言描述想做的事，你需要把它转成结构化的 Action JSON。

# 任务
根据玩家输入和当前世界状态，输出一个 Action 对象。

# 严格规则
1. 只输出一个 JSON 对象，不要任何解释、前后不要 markdown 代码块标记。
2. actor_id 必须从"可操作角色"里选，通常是玩家当前扮演的角色。
3. target_ids 必须从世界已知的实体 id 里选，不能编造。
4. action_type 只能是: swap_object / move / speak / use_item / investigate / attack / gift / observe 之一。
5. 如果玩家输入含糊或无法解析，action_type 设为 "observe"，parameters 留空。

# 输出格式 (字段说明)
{
  "action_type": "...",
  "actor_id": "...",
  "target_ids": ["..."],
  "parameters": {},          // 方式、工具、目的等
  "declared_goal": "...",    // 玩家想达成的目标 (一句话)
  "visibility": "overt"      // overt 公开 / covert 偷偷 / hidden 隐蔽
}
"""


@dataclass
class ParseError:
    """Parser 失败的描述。携带原始输出便于调试。"""

    reason: str
    raw_output: str = ""


class ActionParser:
    """把自然语言解析成 Action。

    用法:
        parser = ActionParser()
        action = parser.parse("我让夜清清把外衫脱下来", state)
    """

    def __init__(self, model: Optional[str] = None, max_retries: int = 2):
        cfg = get_llm_config()
        self.api_key = cfg.api_key
        self.base_url = cfg.base_url
        self.model = model or cfg.model
        self.max_retries = max_retries
        self._setup_client()

    def _setup_client(self) -> None:
        """配置 openai 0.28 的全局客户端。"""
        openai.api_key = self.api_key
        openai.api_base = self.base_url

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def parse(
        self,
        user_text: str,
        state: WorldState,
        *,
        default_actor_id: Optional[str] = None,
    ) -> Optional[Action]:
        """解析自然语言为 Action。失败返回 None (用 last_error 查原因)。"""
        self.last_error: Optional[ParseError] = None
        context = self._build_context(state, default_actor_id)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"# 当前世界状态\n{context}\n\n# 玩家输入\n{user_text}"},
        ]

        last_raw = ""
        for attempt in range(self.max_retries + 1):
            raw = self._call_llm(messages)
            last_raw = raw
            action = self._try_build_action(raw, state, default_actor_id)
            if action is not None:
                return action
            # 重试时把错误反馈给 LLM
            if attempt < self.max_retries:
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": "你的输出无法解析为合法 Action JSON。请只输出一个 JSON 对象。",
                })

        self.last_error = ParseError("解析失败", last_raw)
        return None

    # ------------------------------------------------------------------
    # 内部: prompt / 调用 / 解析
    # ------------------------------------------------------------------

    def _build_context(self, state: WorldState, default_actor_id: Optional[str]) -> str:
        """把 WorldState 摘要成 LLM 能理解的上下文。

        只暴露"行动需要知道的信息": 角色 id/位置、场景物品、关系。
        不暴露角色 beliefs (那是认知层，Parser 不需要)。
        """
        lines = [f"当前场景: {state.current_scene_id}"]
        lines.append(f"世界时间: {state.world_time}")
        if default_actor_id:
            lines.append(f"玩家当前扮演: {default_actor_id}")

        lines.append("\n## 可操作角色 (character_id | 名字 | 位置 | 状态)")
        for cid, ch in state.characters.items():
            status = "存活" if ch.is_alive else "死亡"
            lines.append(f"- {cid} | {ch.display_name} | {ch.location_id} | {status}")

        if state.items:
            lines.append("\n## 场景物品 (item_id | 名字 | 持有者)")
            for iid, it in state.items.items():
                owner = it.owner_id or f"@{it.location_id}"
                lines.append(f"- {iid} | {it.display_name} | {owner}")

        # 关系只给公开关系，不给数值维度 (那是角色内部状态)
        if state.relations:
            lines.append("\n## 已知关系")
            for rel in state.relations:
                lines.append(f"- {rel.source_id} -> {rel.target_id}: {rel.public_relation}")

        return "\n".join(lines)

    def _call_llm(self, messages: list) -> str:
        """调用 LLM，返回纯文本。"""
        resp = openai.ChatCompletion.create(
            model=self.model,
            messages=messages,
            temperature=0.2,  # 解析任务要确定性，低温
        )
        return resp.choices[0].message.content.strip()

    def _try_build_action(
        self, raw: str, state: WorldState, default_actor_id: Optional[str]
    ) -> Optional[Action]:
        """从 LLM 原始输出提取 JSON 并构造 Action。失败返回 None。"""
        data = self._extract_json(raw)
        if data is None:
            return None

        # 校验 action_type 合法
        at = data.get("action_type")
        try:
            action_type = ActionType(at)
        except ValueError:
            return None

        actor_id = data.get("actor_id") or default_actor_id
        if not actor_id:
            return None

        # 实体校验: actor_id 必须存在于世界 (防 LLM 编造)
        if actor_id not in state.characters:
            return None

        # target_ids 过滤掉不存在的 id
        raw_targets = data.get("target_ids") or []
        target_ids = [t for t in raw_targets if t in state.items or t in state.characters]

        try:
            return Action(
                action_id=f"action_llm_{abs(hash(raw)) % 100000}",
                action_type=action_type,
                actor=Actor(actor_id=actor_id),
                target_ids=target_ids,
                parameters=data.get("parameters") or {},
                declared_goal=data.get("declared_goal") or "",
                visibility=data.get("visibility") or "overt",
            )
        except ValidationError:
            return None

    @staticmethod
    def _extract_json(raw: str) -> Optional[dict]:
        """从可能含 markdown/多余文本的输出里提取 JSON 对象。

        LLM 经常会包 ```json ... ``` 或加注释，这里容错处理。
        """
        if not raw:
            return None
        # 先尝试整体解析
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # 再尝试提取代码块
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        # 最后尝试找第一个 {...} 块
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return None
