"""从多条情景记忆提炼带证据链的 NPC 反思/语义记忆。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import openai
from pydantic import BaseModel, Field, validator

from world_schema import Belief, CharacterBelief, WorldState

from .config import get_llm_config
from .persistence import MemoryRecord, PersistenceError
from .storage import WorldStore


REFLECTION_SYSTEM_PROMPT = """你是 NPC 长期记忆整理器。请从同一角色亲历的多条情景记忆中，提炼少量稳定、可复用的推断。

严格规则：
1. 只输出 JSON，不要解释或 markdown。
2. 每条反思必须由至少两个输入事件共同支持，evidence_event_ids 只能使用输入中给出的 ID。
3. 不得把猜测写成客观事实；证据不足时使用 suspected_true / suspected_false。
4. 不得编造人物、物品、地点或事件，不得覆盖“当前认知”中明确相反的事实。
5. fact_id 使用稳定英文/数字标识，表达一个可重复更新的主张。
6. content 使用角色第一人称或贴近角色视角的简洁中文，不超过 120 字。
7. 最多输出 3 条反思；没有可靠的跨事件规律时返回空数组。
8. “当前认知”未列出某个主张不代表冲突；允许从多条经历形成新的 suspected_true / suspected_false 推断。
9. 若至少两条不同经历共同指向同一人物特征、风险、动机或规律，应提炼至少一条反思，而不是逐条复述经历。

示例：一条经历看到某人徒手折断兵器，另一条经历看到护卫畏惧此人，可以推断
“我怀疑此人一直隐藏实力”，belief 使用 suspected_true，并同时列出两条事件 ID。

输出格式：
{
  "reflections": [
    {
      "content": "...",
      "fact_id": "inference_xxx",
      "belief": "suspected_true",
      "confidence": 0.7,
      "evidence_event_ids": ["event_a", "event_b"],
      "related_entity_ids": ["char_xxx"],
      "keywords": ["关键词1", "关键词2"]
    }
  ]
}
"""

REFLECTION_JUDGE_SYSTEM_PROMPT = """你是反思记忆的证据一致性审查器。
你的任务不是润色或补充推断，而是判断“候选反思”是否被给出的情景证据直接支持。

评分规则：
1. 只使用输入证据，不使用常识补全未出现的幕后动机、身份或因果。
2. 至少两条不同证据必须分别为主张提供有效信息；只是一条证据的改写应判不通过。
3. 若证据与主张冲突，contradicted=true。
4. evidence_coverage 表示被引用证据中真正支持主张的比例。
5. semantic_score 综合衡量直接蕴含、跨证据支持和无过度推断程度。
6. 只输出 JSON，不要解释或 markdown。

输出格式：
{
  "fact_id": "与候选相同",
  "entailed": true,
  "contradicted": false,
  "evidence_coverage": 0.9,
  "semantic_score": 0.85,
  "reason": "简短中文理由"
}
"""

_FACT_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{3,120}$")
_TRUE_BELIEFS = {Belief.believed_true, Belief.suspected_true}
_FALSE_BELIEFS = {Belief.believed_false, Belief.suspected_false}


class ReflectionCandidate(BaseModel):
    content: str = Field(..., min_length=4, max_length=120)
    fact_id: str = Field(..., min_length=3, max_length=120)
    belief: Belief
    confidence: float = Field(..., ge=0.5, le=0.95)
    evidence_event_ids: List[str] = Field(
        ...,
        min_items=2,
        max_items=12,
    )
    related_entity_ids: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list, max_items=8)

    @validator("belief")
    def belief_must_be_an_inference(cls, value):
        if value == Belief.unknown:
            raise ValueError("反思不能输出 unknown")
        return value

    @validator("fact_id")
    def fact_id_must_be_stable(cls, value):
        if not _FACT_ID_RE.match(value):
            raise ValueError("fact_id 只能包含英文、数字和 ._:-")
        return value

    @validator("evidence_event_ids", "related_entity_ids", "keywords")
    def values_must_be_unique(cls, value):
        cleaned = list(
            dict.fromkeys(str(item).strip() for item in value if str(item).strip())
        )
        if len(cleaned) != len(value):
            raise ValueError("列表值不能为空或重复")
        return cleaned


class ReflectionPayload(BaseModel):
    reflections: List[ReflectionCandidate] = Field(
        default_factory=list,
        max_items=3,
    )


class ReflectionSemanticScore(BaseModel):
    fact_id: str = Field(..., min_length=3, max_length=120)
    entailed: bool
    contradicted: bool = False
    evidence_coverage: float = Field(..., ge=0.0, le=1.0)
    semantic_score: float = Field(..., ge=0.0, le=1.0)
    reason: str = Field("", max_length=240)


@dataclass
class ReflectionReport:
    character_id: str
    episodic_count: int = 0
    eligible_count: int = 0
    generated_count: int = 0
    written_count: int = 0
    rejected_count: int = 0
    skipped_reason: str = ""
    rejection_reasons: List[str] = field(default_factory=list)
    semantic_scores: Dict[str, float] = field(default_factory=dict)


class ReflectionGenerator:
    """低温 LLM 反思生成器；输出仍需确定性证据与冲突校验。"""

    def __init__(
        self,
        model: Optional[str] = None,
        *,
        max_retries: int = 1,
    ):
        config = get_llm_config()
        self.api_key = config.api_key
        self.base_url = config.base_url
        self.model = model or config.model
        self.max_retries = max(0, int(max_retries))
        self.last_error = ""

    def generate(
        self,
        character_id: str,
        state: WorldState,
        episodes: List[MemoryRecord],
    ) -> List[ReflectionCandidate]:
        self.last_error = ""
        messages = [
            {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": self._build_context(
                    character_id,
                    state,
                    episodes,
                ),
            },
        ]
        retries = max(0, int(getattr(self, "max_retries", 1)))
        for attempt in range(retries + 1):
            try:
                raw = self._call_llm(messages)
                payload = ReflectionPayload.parse_obj(_extract_json(raw))
                self.last_error = ""
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                if attempt >= retries:
                    return []
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "上一次输出无法解析。请严格只输出约定的 JSON 对象，"
                            "并逐项遵守证据与实体约束。"
                        ),
                    }
                )
                continue

            if payload.reflections or attempt >= retries:
                return payload.reflections

            messages.extend(
                [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            "你返回了空数组。请再检查一次：是否至少有两条不同经历共同指向"
                            "同一个人物特征、能力、动机或风险模式。若有，本次只能生成"
                            "一条证据最直接、最强的 suspected_true 反思，并列出这些"
                            " event_id；只有确实不存在交叉证据时才保持空数组。不要把"
                            "单条经历升级为结论，也不要推测经历中没有出现的幕后安排。"
                        ),
                    },
                ]
            )
        return []

    def _call_llm(self, messages: list) -> str:
        response = openai.ChatCompletion.create(
            api_key=self.api_key,
            api_base=self.base_url,
            model=self.model,
            messages=messages,
            temperature=0.2,
        )
        return response.choices[0].message.content.strip()

    @staticmethod
    def _build_context(
        character_id: str,
        state: WorldState,
        episodes: List[MemoryRecord],
    ) -> str:
        character = state.characters.get(character_id)
        lines = [
            f"# 角色\n{character_id} | "
            f"{character.display_name if character else character_id}",
            "\n# 当前认知（不得与其明确冲突）",
        ]
        beliefs = state.beliefs.get(character_id, [])
        if beliefs:
            for item in beliefs:
                lines.append(
                    f"- {item.fact_id}: {item.belief.value} "
                    f"(置信度 {item.confidence:.2f})"
                )
        else:
            lines.append("- 暂无结构化认知")
        lines.append("\n# 可用实体 ID")
        entity_collections = (
            state.characters,
            state.items,
            state.locations,
            state.plot,
        )
        for collection in entity_collections:
            for entity_id, entity in collection.items():
                display_name = getattr(
                    entity,
                    "display_name",
                    getattr(entity, "name", entity_id),
                )
                lines.append(f"- {entity_id}: {display_name}")
        lines.append("\n# 待整理的情景记忆")
        for episode in episodes:
            lines.append(
                f"- [{episode.source_event_id}] "
                f"v{episode.world_version}: {episode.content}"
            )
        return "\n".join(lines)


class ReflectionSemanticJudge:
    """用独立低温 LLM 对候选反思做证据蕴含评分。"""

    def __init__(self, model: Optional[str] = None):
        config = get_llm_config()
        self.api_key = config.api_key
        self.base_url = config.base_url
        self.model = model or config.model
        self.last_error = ""

    def score(
        self,
        candidate: ReflectionCandidate,
        episodes: List[MemoryRecord],
    ) -> Optional[ReflectionSemanticScore]:
        self.last_error = ""
        evidence_by_id = {
            episode.source_event_id: episode.content
            for episode in episodes
        }
        evidence = [
            {
                "event_id": event_id,
                "content": evidence_by_id.get(event_id, ""),
            }
            for event_id in candidate.evidence_event_ids
        ]
        payload = {
            "candidate": candidate.dict(),
            "evidence": evidence,
        }
        messages = [
            {"role": "system", "content": REFLECTION_JUDGE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ]
        try:
            raw = self._call_llm(messages)
            score = ReflectionSemanticScore.parse_obj(_extract_json(raw))
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return None
        if score.fact_id != candidate.fact_id:
            self.last_error = "语义审查返回了不匹配的 fact_id"
            return None
        return score

    def _call_llm(self, messages: list) -> str:
        response = openai.ChatCompletion.create(
            api_key=self.api_key,
            api_base=self.base_url,
            model=self.model,
            messages=messages,
            temperature=0.0,
        )
        return response.choices[0].message.content.strip()


def reflect_character_memories(
    store: WorldStore,
    session_id: str,
    state: WorldState,
    character_id: str,
    *,
    generator: Optional[ReflectionGenerator] = None,
    semantic_judge: Optional[ReflectionSemanticJudge] = None,
    semantic_threshold: float = 0.72,
    min_new_episodes: int = 3,
    max_episode_window: int = 12,
    max_reflections: int = 100,
) -> ReflectionReport:
    """把尚未被反思消费的情景记忆整理为幂等语义记忆。"""

    report = ReflectionReport(character_id=character_id)
    episodes = store.list_character_memories(
        session_id,
        character_id=character_id,
        memory_type="episodic",
    )
    reflections = store.list_character_memories(
        session_id,
        character_id=character_id,
        memory_type="reflection",
    )
    report.episodic_count = len(episodes)
    consumed = {
        event_id
        for reflection in reflections
        for event_id in reflection.evidence_event_ids
    }
    eligible = [
        episode
        for episode in episodes
        if episode.source_event_id not in consumed
    ]
    report.eligible_count = len(eligible)
    if len(eligible) < min_new_episodes:
        report.skipped_reason = (
            f"需要至少 {min_new_episodes} 条未反思情景记忆"
        )
        return report
    window = eligible[-max_episode_window:]
    active_generator = generator or ReflectionGenerator()
    candidates = active_generator.generate(character_id, state, window)
    if not candidates and active_generator.last_error:
        raise PersistenceError(
            f"角色反思生成失败: {active_generator.last_error}"
        )
    report.generated_count = len(candidates)
    episode_by_event = {
        item.source_event_id: item
        for item in window
    }
    existing_by_fact = {
        item.claim_fact_id: item
        for item in reflections
        if item.claim_fact_id
    }
    known_entities = (
        set(state.characters)
        | set(state.items)
        | set(state.locations)
        | set(state.plot)
    )
    for candidate in candidates:
        candidate_semantic_score = 0.0
        reason = _candidate_rejection_reason(
            candidate,
            character_id,
            state,
            episode_by_event,
            known_entities,
        )
        if reason:
            report.rejected_count += 1
            report.rejection_reasons.append(
                f"{candidate.fact_id}: {reason}"
            )
            continue
        if semantic_judge is not None:
            semantic_result = semantic_judge.score(candidate, window)
            if semantic_result is None:
                report.rejected_count += 1
                report.rejection_reasons.append(
                    f"{candidate.fact_id}: 语义一致性审查失败"
                    f"（{semantic_judge.last_error or '未知错误'}）"
                )
                continue
            candidate_semantic_score = semantic_result.semantic_score
            report.semantic_scores[candidate.fact_id] = (
                candidate_semantic_score
            )
            semantic_reason = _semantic_rejection_reason(
                semantic_result,
                semantic_threshold,
            )
            if semantic_reason:
                report.rejected_count += 1
                report.rejection_reasons.append(
                    f"{candidate.fact_id}: {semantic_reason}"
                )
                continue
        existing = existing_by_fact.get(candidate.fact_id)
        evidence = list(candidate.evidence_event_ids)
        if existing is not None:
            evidence = list(
                dict.fromkeys(
                    list(existing.evidence_event_ids) + evidence
                )
            )
        source_id = reflection_source_id(
            character_id,
            candidate.fact_id,
        )
        store.record_character_memories(
            session_id,
            [character_id],
            source_event_id=source_id,
            world_version=state.version,
            content=candidate.content,
            importance=min(
                0.95,
                max(0.65, candidate.confidence),
            ),
            memory_type="reflection",
            evidence_event_ids=evidence,
            claim_fact_id=candidate.fact_id,
            claim_belief=candidate.belief.value,
            claim_confidence=candidate.confidence,
            semantic_score=candidate_semantic_score,
        )
        report.written_count += 1
    if report.written_count:
        store.prune_character_memories(
            session_id,
            character_id,
            memory_type="reflection",
            max_records=max_reflections,
        )
    return report


def _semantic_rejection_reason(
    score: ReflectionSemanticScore,
    threshold: float,
) -> str:
    if score.contradicted:
        return f"证据与反思矛盾：{score.reason}"
    if not score.entailed:
        return f"证据不能直接支持反思：{score.reason}"
    if score.evidence_coverage < 0.6:
        return (
            f"有效证据覆盖率过低 {score.evidence_coverage:.2f}："
            f"{score.reason}"
        )
    if score.semantic_score < threshold:
        return (
            f"语义一致性分 {score.semantic_score:.2f} "
            f"低于门槛 {threshold:.2f}：{score.reason}"
        )
    return ""


def reflection_source_id(character_id: str, fact_id: str) -> str:
    digest = hashlib.sha256(
        f"{character_id}:{fact_id}".encode("utf-8")
    ).hexdigest()[:24]
    return f"reflection:{digest}"


def memory_conflicts_with_state(
    memory: MemoryRecord,
    state: WorldState,
    character_id: str,
) -> bool:
    """权威认知已明确相反或 unknown 时，不把该反思注入 Agent。"""

    if memory.memory_type != "reflection" or not memory.claim_fact_id:
        return False
    try:
        proposed = Belief(memory.claim_belief)
    except ValueError:
        return True
    conflict = _belief_conflict_reason(
        state.beliefs.get(character_id, []),
        memory.claim_fact_id,
        proposed,
        memory.claim_confidence,
    )
    return bool(conflict)


def filter_compatible_memories(
    memories: List[MemoryRecord],
    state: WorldState,
    character_id: str,
) -> List[MemoryRecord]:
    return [
        memory
        for memory in memories
        if not memory_conflicts_with_state(
            memory,
            state,
            character_id,
        )
    ]


def _candidate_rejection_reason(
    candidate: ReflectionCandidate,
    character_id: str,
    state: WorldState,
    episode_by_event: dict,
    known_entities: set,
) -> str:
    unknown_evidence = [
        item
        for item in candidate.evidence_event_ids
        if item not in episode_by_event
    ]
    if unknown_evidence:
        return f"引用不在输入窗口的证据 {unknown_evidence}"
    if not set(candidate.related_entity_ids).issubset(known_entities):
        return "引用未知世界实体"
    return _belief_conflict_reason(
        state.beliefs.get(character_id, []),
        candidate.fact_id,
        candidate.belief,
        candidate.confidence,
    )


def _belief_conflict_reason(
    beliefs: List[CharacterBelief],
    fact_id: str,
    proposed: Belief,
    confidence: float,
) -> str:
    current = next(
        (item for item in beliefs if item.fact_id == fact_id),
        None,
    )
    if current is None:
        return ""
    if current.belief == Belief.unknown:
        return "权威角色认知仍为 unknown"
    opposite = (
        (current.belief in _TRUE_BELIEFS and proposed in _FALSE_BELIEFS)
        or (
            current.belief in _FALSE_BELIEFS
            and proposed in _TRUE_BELIEFS
        )
    )
    if opposite:
        return "与当前权威角色认知相反"
    if (
        current.belief != proposed
        and current.confidence > confidence
    ):
        return "弱置信度反思不得覆盖更强的当前认知"
    return ""


def _extract_json(raw: str) -> dict:
    if not raw:
        raise ValueError("反思模型返回为空")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        raw,
        re.DOTALL,
    )
    if fenced:
        return json.loads(fenced.group(1))
    matched = re.search(r"\{.*\}", raw, re.DOTALL)
    if matched:
        return json.loads(matched.group(0))
    raise ValueError("反思模型未返回 JSON 对象")
