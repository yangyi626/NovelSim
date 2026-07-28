"""LLM 抽取器：场景文本 -> 实体 / 关系 / 事件 / 世界规则。

对应 plan 第十二步流水线的核心 AI 部分。这是编译器的"脑子"：
把一段小说文本，结构化成可累积进 WorldState 的中间数据。

设计要点 (与 engine 的 LLM 模块一致)：
- 中间数据用独立 schema (RawEntity / RawRelation / RawEvent / RawWorldRule)，
  与 world_schema 解耦——抽取阶段容忍噪声，清洗/消歧后才升级成正式模型。
- 每条抽取结果带 evidence (原文片段) + confidence (0-1)，便于人工审核
  (plan 第十五步"世界编译回归测试"也依赖这些字段)。
- 抽取是"提议"而非"事实"：scene_compiler 会再过一遍实体消歧 + 合法性校验。
- 实体消歧：调用方可传入"已知实体表" (alias -> canonical_id)，让 LLM 复用
  已有 id 而不是重复新建，这是跨章节一致性的关键。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import openai
from pydantic import BaseModel, Field, ValidationError

from engine.config import get_llm_config


# ---------------------------------------------------------------------------
# 中间 schema (抽取产物)
# ---------------------------------------------------------------------------


class RawEntity(BaseModel):
    """一个抽取出的实体 (角色/地点/物品)。

    raw_name 是原文里出现的称呼；canonical_id 由调用方/消歧层决定。
    """

    raw_name: str
    entity_type: str = "character"  # character / location / item
    canonical_id: str = ""  # 消歧后稳定 id；空则由 compiler 生成
    aliases: List[str] = Field(default_factory=list)
    identity_tags: List[str] = Field(default_factory=list)
    # D 阶段：同一灵魂/人物跨时间线、改名或转世时使用稳定身份键。
    # 若只是同名但并非同一人物，应使用不同 global_identity。
    global_identity: str = ""
    incarnation: str = ""
    timeline_id: str = ""
    description: str = ""
    evidence: str = ""  # 原文依据
    confidence: float = Field(0.5, ge=0.0, le=1.0)

    class Config:
        extra = "allow"


class RawRelation(BaseModel):
    """两个角色之间的关系 (单向)。"""

    source_name: str
    target_name: str
    public_relation: str = ""  # 嫡姐-庶妹 / 主仆 / 未婚夫妻
    private_relation: str = ""
    # 关系维度初值 (-1..1)；缺省 0
    dimensions: Dict[str, float] = Field(default_factory=dict)
    evidence: str = ""
    confidence: float = Field(0.5, ge=0.0, le=1.0)

    class Config:
        extra = "allow"


class RawEvent(BaseModel):
    """一段文本里发生的一个事件。

    patch_operations 是"草稿 op" (list[dict])，与 TransitionProposer 同格式，
    后续由 scene_compiler 转成严格 Operation 校验后才能进 WorldEvent。
    """

    summary: str
    event_type: str = "narrative"  # narrative / transfer / conflict / revelation ...
    actor_names: List[str] = Field(default_factory=list)
    target_names: List[str] = Field(default_factory=list)
    patch_operations: List[Dict] = Field(default_factory=list)
    evidence: str = ""
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    order: int = 0  # 文本内出现顺序

    class Config:
        extra = "allow"


class RawWorldRule(BaseModel):
    """世界级规则 (魔法/身份/政治等)。"""

    category: str = "politics"  # magic / death / identity / politics / time
    statement: str = ""
    evidence: str = ""
    confidence: float = Field(0.5, ge=0.0, le=1.0)

    class Config:
        extra = "allow"


class RawCharacterState(BaseModel):
    """角色在当前章节发生的可延续状态变化。"""

    character_name: str
    state_summary: str
    emotion: str = ""
    identity_tags_add: List[str] = Field(default_factory=list)
    attrs_update: Dict = Field(default_factory=dict)
    evidence: str = ""
    confidence: float = Field(0.5, ge=0.0, le=1.0)

    class Config:
        extra = "allow"


class RawForeshadow(BaseModel):
    """伏笔在当前章节中的埋设、强化或回收。"""

    title: str
    description: str = ""
    status: str = "planted"  # planted / reinforced / resolved
    related_names: List[str] = Field(default_factory=list)
    payoff_hint: str = ""
    evidence: str = ""
    confidence: float = Field(0.5, ge=0.0, le=1.0)

    class Config:
        extra = "allow"


class RawGoalEvolution(BaseModel):
    """角色长期目标在当前章节中的建立、推进或终止。"""

    character_name: str
    goal_key: str
    description: str
    status: str = "active"
    priority: float = Field(0.5, ge=0.0, le=1.0)
    target_names: List[str] = Field(default_factory=list)
    # chapter / arc / timeline / world / book；空值交给编译器按证据推断。
    scope: str = ""
    timeline_id: str = ""
    supersedes_goal_keys: List[str] = Field(default_factory=list)
    terminal_reason: str = ""
    evidence: str = ""
    confidence: float = Field(0.5, ge=0.0, le=1.0)

    class Config:
        extra = "allow"


class SceneExtraction(BaseModel):
    """一次场景抽取的完整产物。"""

    scene_id: str
    entities: List[RawEntity] = Field(default_factory=list)
    relations: List[RawRelation] = Field(default_factory=list)
    events: List[RawEvent] = Field(default_factory=list)
    world_rules: List[RawWorldRule] = Field(default_factory=list)
    character_states: List[RawCharacterState] = Field(default_factory=list)
    foreshadows: List[RawForeshadow] = Field(default_factory=list)
    goal_evolutions: List[RawGoalEvolution] = Field(default_factory=list)
    summary: str = ""
    notes: str = ""

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# 系统提示词
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = """你是一个小说世界编译器。你会收到一段小说原文，需要从中抽取结构化的世界数据：人物/地点/物品、角色关系、发生的事件、以及世界级规则。

# 抽取原则
1. 只抽取"这段文本里真正出现或能直接推断"的内容，不要用你对该小说的预设立场编造。
2. 每条结果都要给 evidence (原文里的依据片段，尽量照抄原话) 和 confidence (0-1)。
3. raw_name 用原文里出现的称呼 (如"夜轻歌"、"三小姐"、"林管家")；canonical_id 若"已知实体"里给了对应，就填那个 id；否则留空。
4. 事件 patch_operations 只描述"这段文本里直接发生"的状态变化，每条 op 从合法集合里选。不要推演未来。
5. 关系维度只填原文有依据的 (affection/trust/fear/hostility/respect/debt)，缺省不写。
6. 若某类内容这段文本里没有，对应数组留空即可。
7. character_states 只记录能延续到后续章节的身份、伤势、情绪或处境变化。
8. foreshadows 用稳定 title 识别同一伏笔，status 仅可为 planted/reinforced/resolved。
9. goal_evolutions 用稳定 goal_key 识别同一角色目标。status 仅可为 active/achieved/abandoned/superseded/expired；scope 仅可为 chapter/arc/timeline/world/book。一次性调查或报答通常是 arc，随当前世界存在的是 world，跨世界长期使命才是 book。目标完成、放弃、被替代或因世界切换失效时必须给 terminal_reason；新目标替代旧目标时把旧 goal_key 写入 supersedes_goal_keys。
10. 同一人物跨时间线、改名或转世时，global_identity 使用稳定身份键；incarnation 标记当前肉身/身份，timeline_id 标记明确出现的时间线。无法判断时留空，不要猜测。

# 合法 patch op (事件 patch_operations 数组里每条的 op 字段)
- set_flag: path, value
- set_attr: path("角色id.属性名"), value
- increment_value: path, delta
- move_character: target_id, location_id
- transfer_item: item_id, target_id
- update_relation: source_id, target_id, dimension, delta
- update_belief: target_id, fact_id, belief(believed_true/suspected_true/unknown/suspected_false/believed_false), confidence, source_type
- kill_character / revive_character: target_id
- change_identity: target_id, tags
- start_plot / advance_plot / complete_plot: target_id

# 输出格式 (只输出一个 JSON 对象，不要解释、不要 markdown)
{
  "summary": "这段场景讲了什么 (1-2句)",
  "entities": [
    {"raw_name": "夜轻歌", "entity_type": "character", "aliases": ["三小姐"], "identity_tags": ["嫡系","废柴"], "global_identity": "soul_yeqingge", "incarnation": "夜家三小姐", "timeline_id": "", "description": "...", "evidence": "原文...", "confidence": 0.9}
  ],
  "relations": [
    {"source_name": "夜清清", "target_name": "夜轻歌", "public_relation": "庶妹-嫡姐", "private_relation": "嫉妒陷害", "dimensions": {"hostility": 0.7, "affection": -0.5}, "evidence": "...", "confidence": 0.8}
  ],
  "events": [
    {"summary": "夜轻歌被当众羞辱", "event_type": "conflict", "actor_names": ["夜清清"], "target_names": ["夜轻歌"], "patch_operations": [{"op": "set_flag", "path": "plot.shaming_happened", "value": true}], "evidence": "...", "confidence": 0.85, "order": 1}
  ],
  "world_rules": [
    {"category": "politics", "statement": "庶出不可忤逆嫡系", "evidence": "...", "confidence": 0.7}
  ],
  "character_states": [
    {"character_name": "夜轻歌", "state_summary": "从受辱转为主动反击", "emotion": "冷静", "identity_tags_add": ["觉醒"], "attrs_update": {}, "evidence": "...", "confidence": 0.8}
  ],
  "foreshadows": [
    {"title": "毒茶真相", "description": "毒茶来源尚未查明", "status": "planted", "related_names": ["夜轻歌","夜清清"], "payoff_hint": "后续查出下毒者", "evidence": "...", "confidence": 0.75}
  ],
  "goal_evolutions": [
    {"character_name": "夜轻歌", "goal_key": "clear_name", "description": "查清陷害并洗刷污名", "status": "active", "scope": "arc", "timeline_id": "", "priority": 0.9, "target_names": ["夜清清"], "supersedes_goal_keys": [], "terminal_reason": "", "evidence": "...", "confidence": 0.8}
  ]
}
"""


# ---------------------------------------------------------------------------
# 抽取器
# ---------------------------------------------------------------------------


class EntityExtractor:
    """把一段场景文本抽取成 SceneExtraction。

    用法:
        ext = EntityExtractor()
        result = ext.extract(scene, known_entities={"三小姐": "char_yeqingge"})
    """

    def __init__(
        self,
        model: Optional[str] = None,
        max_retries: int = 2,
        temperature: float = 0.3,
        before_llm_call: Optional[Callable[[], None]] = None,
    ):
        cfg = get_llm_config()
        self.api_key = cfg.api_key
        self.base_url = cfg.base_url
        self.model = model or cfg.model
        self.max_retries = max_retries
        self.temperature = temperature
        self.before_llm_call = before_llm_call
        openai.api_key = self.api_key
        openai.api_base = self.base_url
        self.last_error: Optional[str] = None

    def extract(
        self,
        scene_text: str,
        *,
        scene_id: str = "scene",
        known_entities: Optional[Dict[str, str]] = None,
        chapter_hint: str = "",
    ) -> Optional[SceneExtraction]:
        """抽取一个场景。失败返回 None (查 last_error)。

        known_entities: {别名/称呼: canonical_id}，用于实体消歧 (复用已有 id)。
        """
        self.last_error = None
        context = self._build_context(scene_text, scene_id, known_entities, chapter_hint)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ]

        last_raw = ""
        for attempt in range(self.max_retries + 1):
            raw = self._call_llm(messages)
            last_raw = raw
            extraction = self._try_build(raw, scene_id)
            if extraction is not None:
                # 把已知实体 id 回填进 entities / events
                self._reconcile_known(extraction, known_entities or {})
                return extraction
            if attempt < self.max_retries:
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": "输出无法解析为合法 JSON。请只输出一个 JSON 对象。",
                })

        self.last_error = f"parse failed, last raw: {last_raw[:200]}"
        return None

    # ------------------------------------------------------------------
    # 上下文构造
    # ------------------------------------------------------------------

    def _build_context(
        self,
        scene_text: str,
        scene_id: str,
        known_entities: Optional[Dict[str, str]],
        chapter_hint: str,
    ) -> str:
        lines = [f"# 场景 id: {scene_id}"]
        if chapter_hint:
            lines.append(f"出处: {chapter_hint}")
        if known_entities:
            lines.append("\n# 已知实体 (raw_name 命中这些称呼时，canonical_id 填对应值)")
            for alias, cid in known_entities.items():
                lines.append(f"- {alias} -> {cid}")
        lines.append("\n# 原文")
        lines.append(scene_text)
        lines.append("\n# 请抽取")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 调用与解析
    # ------------------------------------------------------------------

    def _call_llm(self, messages: list) -> str:
        if self.before_llm_call is not None:
            self.before_llm_call()
        resp = openai.ChatCompletion.create(
            model=self.model, messages=messages, temperature=self.temperature,
        )
        return resp.choices[0].message.content.strip()

    def _try_build(self, raw: str, scene_id: str) -> Optional[SceneExtraction]:
        data = _extract_json(raw)
        if data is None:
            return None
        data["scene_id"] = scene_id
        try:
            return SceneExtraction.parse_obj(data)
        except ValidationError:
            return None

    # ------------------------------------------------------------------
    # 实体消歧后处理
    # ------------------------------------------------------------------

    def _reconcile_known(
        self, extraction: SceneExtraction, known: Dict[str, str]
    ) -> None:
        """若 LLM 没填 canonical_id 但 raw_name/aliases 命中已知表，回填。"""
        if not known:
            return
        for ent in extraction.entities:
            if ent.canonical_id:
                continue
            for name in [ent.raw_name, *ent.aliases]:
                if name and name in known:
                    ent.canonical_id = known[name]
                    break


# ---------------------------------------------------------------------------
# 公共工具
# ---------------------------------------------------------------------------


def _extract_json(raw: str) -> Optional[dict]:
    """与其它 LLM 模块共享的 JSON 容错提取。"""
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
