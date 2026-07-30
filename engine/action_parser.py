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

from world_schema import (
    Action,
    IntentParseResult,
    IntentRejectionCode,
    IntentStatus,
    WorldState,
)
from world_schema.models import Actor, ActionType

from .config import get_llm_config
from .llm_telemetry import (
    call_openai_compatible,
    chat_generation_options,
)


_CONTROL_OVERRIDE_PATTERNS = (
    re.compile(r"忽略.{0,12}(世界|系统|游戏).{0,8}(规则|约束)", re.IGNORECASE),
    re.compile(r"(直接|强制).{0,8}(修改|改写).{0,8}(剧情|结局|世界状态)", re.IGNORECASE),
)


# 系统提示词。说明任务、约束、输出格式。
# 故意只给 LLM "它需要知道的" 世界信息，而不是整个 state dump。
SYSTEM_PROMPT = """你是一个游戏行动解析器。玩家用自然语言描述想做的事，你需要把它转成结构化的行动意图。玩家描述的是意图，不是已经发生的世界事实。

# 任务
根据玩家输入和当前世界状态，输出一个 Action 对象。

# 严格规则
1. 只输出一个 JSON 对象，不要任何解释、前后不要 markdown 代码块标记。
2. actor_id 必须从"可操作角色"里选，通常是玩家当前扮演的角色。
3. target_ids 必须从世界已知的实体 id 里选，不能编造。
4. action_type 只能是: swap_object / move / speak / use_item / investigate / attack / gift / observe 之一。
5. 不得把含糊、不可能或引用未知实体的输入降级成 observe；必须显式 rejected。
6. move 必须给出 parameters.destination_id；使用交通工具时还要给出 transport_entity_id、concept_ids 和 capability_id。
7. 玩家提到但世界中没有对应 id 的具体人物、物品、地点或工具，放入 unresolved_references 并拒绝。
8. 对已知角色发出命令、请求、询问或威胁属于 speak；这只表示说话已经发生，
   不表示对方已经照做。把话语原文或意图放进 parameters.message。
9. 行动者亲自拿、夺、取一个已知且可接触的物品属于 swap_object，target_ids
   放该物品 id；不要因为没有 steal 这个枚举而拒绝。

# 接受时的输出格式
{
  "status": "accepted",
  "action_type": "...",
  "actor_id": "...",
  "target_ids": ["..."],
  "parameters": {
    "destination_id": "地点或角色id(仅move)",
    "transport_entity_id": "交通工具实体id(可选)",
    "capability_id": "所需能力id(可选)",
    "concept_ids": ["引用的世界概念id"]
  },
  "declared_goal": "...",    // 玩家想达成的目标 (一句话)
  "visibility": "overt",     // overt 公开 / covert 偷偷 / hidden 隐蔽
  "unresolved_references": []
}

# 拒绝时的输出格式
{
  "status": "rejected",
  "reason_code": "AMBIGUOUS_INTENT / ENTITY_NOT_FOUND / WORLD_CONCEPT_UNAVAILABLE / INVALID_ACTION",
  "message": "简洁说明",
  "unresolved_references": ["无法映射到世界实体的原词"]
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

    def parse_result(
        self,
        user_text: str,
        state: WorldState,
        *,
        default_actor_id: Optional[str] = None,
    ) -> IntentParseResult:
        """严格解析入口。

        与兼容接口 :meth:`parse` 不同，本方法保留 rejected/parse_failed，
        不会把非法输入伪装成合法 Action。权威 Turn Pipeline 必须使用它。
        """

        self.last_error = None
        precheck = self._precheck_world_concepts(user_text, state)
        if precheck is not None:
            return precheck

        context = self._build_context(state, default_actor_id)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"# 当前世界状态\n{context}\n\n# 玩家输入\n{user_text}",
            },
        ]

        last_raw = ""
        for attempt in range(self.max_retries + 1):
            raw = self._call_llm(messages)
            last_raw = raw
            result = self._try_build_intent_result(
                raw,
                user_text,
                state,
                default_actor_id,
            )
            if result is not None:
                return result
            if attempt < self.max_retries:
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "输出无法解析。请按 accepted 或 rejected 的 JSON "
                            "格式重新输出，不要解释。"
                        ),
                    }
                )

        self.last_error = ParseError("解析失败", last_raw)
        return IntentParseResult(
            status=IntentStatus.parse_failed,
            message="无法解析用户输入",
            raw_input=user_text,
            details={"raw_output": last_raw[:500]},
        )

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

        if state.locations:
            lines.append("\n## 可用地点 (location_id | 名字 | 是否可达)")
            for lid, loc in state.locations.items():
                lines.append(f"- {lid} | {loc.display_name} | {loc.accessible}")

        if state.world_concepts:
            lines.append("\n## 世界概念 (concept_id | 名称 | 可用性)")
            for concept_id, concept in state.world_concepts.items():
                availability = "可用" if concept.available else "不可用"
                lines.append(
                    f"- {concept_id} | {concept.display_name} | "
                    f"{concept.category} | {availability}"
                )

        if state.world_constraints:
            lines.append("\n## 权威世界约束")
            for constraint in state.world_constraints:
                lines.append(f"- {constraint.constraint_id}: {constraint.statement}")

        if default_actor_id:
            capabilities = [
                cap.capability_id
                for cap in state.character_capabilities.get(default_actor_id, [])
                if cap.enabled
            ]
            lines.append(
                "\n## 当前角色能力\n"
                f"- {default_actor_id}: {capabilities or ['无已注册能力']}"
            )

        # 关系只给公开关系，不给数值维度 (那是角色内部状态)
        if state.relations:
            lines.append("\n## 已知关系")
            for rel in state.relations:
                lines.append(f"- {rel.source_id} -> {rel.target_id}: {rel.public_relation}")

        return "\n".join(lines)

    def _call_llm(self, messages: list) -> str:
        """调用 LLM，返回纯文本。"""
        resp = call_openai_compatible(
            openai.ChatCompletion.create,
            operation="action_parser",
            model=self.model,
            messages=messages,
            temperature=0.2,  # 解析任务要确定性，低温
            **chat_generation_options(
                self.model,
                max_tokens=1024,
                thinking=False,
            ),
        )
        return resp.choices[0].message.content.strip()

    def _try_build_action(
        self, raw: str, state: WorldState, default_actor_id: Optional[str]
    ) -> Optional[Action]:
        """从 LLM 原始输出提取 JSON 并构造 Action。失败返回 None。"""
        data = self._extract_json(raw)
        if data is None:
            return None
        return self._build_action_from_data(
            data,
            raw,
            state,
            default_actor_id,
            strict_targets=False,
        )

    def _try_build_intent_result(
        self,
        raw: str,
        user_text: str,
        state: WorldState,
        default_actor_id: Optional[str],
    ) -> Optional[IntentParseResult]:
        data = self._extract_json(raw)
        if data is None:
            return None

        status = str(data.get("status") or "accepted").lower()
        unresolved = [
            str(value)
            for value in (data.get("unresolved_references") or [])
            if str(value).strip()
        ]
        if status == "rejected" or unresolved:
            raw_code = (
                data.get("reason_code")
                or (
                    IntentRejectionCode.entity_not_found.value
                    if unresolved
                    else IntentRejectionCode.invalid_action.value
                )
            )
            try:
                code = IntentRejectionCode(str(raw_code))
            except ValueError:
                code = IntentRejectionCode.invalid_action
            return IntentParseResult(
                status=IntentStatus.rejected,
                reason_code=code,
                message=(
                    str(data.get("message") or "").strip()
                    or "行动不符合当前世界或缺少可解析实体"
                ),
                raw_input=user_text,
                details={"unresolved_references": unresolved},
            )
        if status not in {"accepted", ""}:
            return None

        actor_id = data.get("actor_id") or default_actor_id
        if not actor_id or actor_id not in state.characters:
            return IntentParseResult(
                status=IntentStatus.rejected,
                reason_code=IntentRejectionCode.entity_not_found,
                message=f"行动者不存在: {actor_id or '<missing>'}",
                raw_input=user_text,
                details={"actor_id": actor_id},
            )
        if default_actor_id and actor_id != default_actor_id:
            return IntentParseResult(
                status=IntentStatus.rejected,
                reason_code=IntentRejectionCode.permission_denied,
                message=(
                    f"当前玩家只能控制 {default_actor_id}，不能直接控制 "
                    f"{actor_id}"
                ),
                raw_input=user_text,
                details={
                    "default_actor_id": default_actor_id,
                    "requested_actor_id": actor_id,
                },
            )

        raw_targets = data.get("target_ids") or []
        unknown_targets = [
            target
            for target in raw_targets
            if target not in state.items
            and target not in state.characters
            and target not in state.locations
        ]
        if unknown_targets:
            return IntentParseResult(
                status=IntentStatus.rejected,
                reason_code=IntentRejectionCode.entity_not_found,
                message=f"目标实体不存在: {', '.join(map(str, unknown_targets))}",
                raw_input=user_text,
                details={"unknown_target_ids": unknown_targets},
            )

        action = self._build_action_from_data(
            data,
            raw,
            state,
            default_actor_id,
            strict_targets=True,
        )
        if action is None:
            return None
        return IntentParseResult(
            status=IntentStatus.accepted,
            action=action,
            raw_input=user_text,
        )

    def _build_action_from_data(
        self,
        data: dict,
        raw: str,
        state: WorldState,
        default_actor_id: Optional[str],
        *,
        strict_targets: bool,
    ) -> Optional[Action]:
        """从已解析字典构造 Action；strict_targets 时不静默丢弃未知目标。"""

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

        # 兼容接口仍过滤不存在的 id；权威入口 strict_targets 会在上游拒绝。
        raw_targets = data.get("target_ids") or []
        if strict_targets:
            target_ids = list(raw_targets)
        else:
            target_ids = [
                target
                for target in raw_targets
                if target in state.items or target in state.characters
            ]

        parameters = dict(data.get("parameters") or {})
        if data.get("concept_ids") and "concept_ids" not in parameters:
            parameters["concept_ids"] = list(data.get("concept_ids") or [])

        try:
            return Action(
                action_id=f"action_llm_{abs(hash(raw)) % 100000}",
                action_type=action_type,
                actor=Actor(actor_id=actor_id),
                target_ids=target_ids,
                parameters=parameters,
                declared_goal=data.get("declared_goal") or "",
                visibility=data.get("visibility") or "overt",
            )
        except ValidationError:
            return None

    @staticmethod
    def _precheck_world_concepts(
        user_text: str,
        state: WorldState,
    ) -> Optional[IntentParseResult]:
        """用世界包声明的概念别名阻断明确不可用概念。

        引擎本身不知道“飞机”或“瞬移”是什么；是否不可用完全来自当前
        ``WorldState``，因此现代世界可以把同一概念配置为 available=True。
        """

        for pattern in _CONTROL_OVERRIDE_PATTERNS:
            if pattern.search(user_text):
                return IntentParseResult(
                    status=IntentStatus.rejected,
                    reason_code=IntentRejectionCode.permission_denied,
                    message="玩家输入不能覆盖世界规则或直接修改剧情状态",
                    raw_input=user_text,
                    details={"guard": "control_override"},
                )

        normalized = user_text.casefold()
        explicitly_forbidden = {
            concept_id
            for constraint in state.world_constraints
            for concept_id in constraint.forbidden_concept_ids
        }
        for concept_id, concept in state.world_concepts.items():
            terms = [
                term
                for term in [concept.display_name, *concept.aliases]
                if term and len(str(term)) >= 2
            ]
            matched = next(
                (
                    term
                    for term in terms
                    if term and str(term).casefold() in normalized
                ),
                None,
            )
            if matched is None:
                for pattern in concept.mention_patterns:
                    match = re.search(pattern, user_text, re.IGNORECASE)
                    if match:
                        matched = match.group(0)
                        break
            if not matched:
                continue
            if not concept.available or concept_id in explicitly_forbidden:
                return IntentParseResult(
                    status=IntentStatus.rejected,
                    reason_code=IntentRejectionCode.world_concept_unavailable,
                    message=f"当前世界不可使用“{concept.display_name}”",
                    raw_input=user_text,
                    details={
                        "concept_id": concept_id,
                        "matched_term": matched,
                    },
                )
            if concept.requires_entity:
                registered = any(
                    affordance.enabled
                    and affordance.concept_id == concept_id
                    and entity_id in (
                        set(state.items)
                        | set(state.characters)
                        | set(state.locations)
                    )
                    for entity_id, affordances in state.entity_affordances.items()
                    for affordance in affordances
                )
                if not registered:
                    return IntentParseResult(
                        status=IntentStatus.rejected,
                        reason_code=IntentRejectionCode.entity_not_found,
                        message=(
                            f"当前世界没有可使用的“{concept.display_name}”实体"
                        ),
                        raw_input=user_text,
                        details={
                            "concept_id": concept_id,
                            "matched_term": matched,
                        },
                    )
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
