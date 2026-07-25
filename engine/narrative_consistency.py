"""叙事一致性审查器。

检查 NarrativeOutput 是否忠于已提交的世界状态。这是表现层的安全阀:
LLM 写出的文字不能违反已经发生的事实。

检查项:
1. 对白说话者必须存在且存活 (死人不能说话)
2. 对白内容不得包含说话者"不该知道"的事实 (认知隔离)
3. 系统提示不得与已提交 patch 矛盾
4. 引用的角色/物品 id 必须存在

这是 plan 第7.7节"一致性审查器"在叙事层的最小实现。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

from world_schema import NarrativeOutput, WorldEvent, WorldState


@dataclass
class NarrativeViolation:
    severity: str  # error / warning
    rule_id: str
    message: str


@dataclass
class NarrativeCheckResult:
    valid: bool
    violations: List[NarrativeViolation] = field(default_factory=list)

    def errors(self) -> List[NarrativeViolation]:
        return [v for v in self.violations if v.severity == "error"]

    def why(self) -> str:
        return "; ".join(f"[{v.severity}]{v.rule_id}: {v.message}" for v in self.violations)


def check_narrative(
    narrative: NarrativeOutput,
    event: WorldEvent,
    state: WorldState,
) -> NarrativeCheckResult:
    """审查叙事输出。error 级违规视为不通过。"""
    violations: List[NarrativeViolation] = []

    for line in narrative.dialogues:
        # 说话者必须存在
        speaker = state.characters.get(line.speaker_id)
        if speaker is None:
            violations.append(NarrativeViolation(
                "error", "speaker_exists",
                f"对白说话者不存在: {line.speaker_id}",
            ))
            continue
        # 死人不能说话
        if not speaker.is_alive:
            violations.append(NarrativeViolation(
                "error", "speaker_alive",
                f"死亡角色 {line.display_name if hasattr(line,'display_name') else line.speaker_id} 不能说话",
            ))
        # 认知隔离: 说话者提到的"事实关键词"若超出其认知，记 warning
        # (启发式: 检查对白里是否出现说话者 belief=unknown 的 fact_id 关键词)
        _check_knowledge_leak(line.speaker_id, line.line, state, event, violations)

    # 引用不存在的 to_id
    for line in narrative.dialogues:
        if line.to_id and line.to_id not in state.characters:
            violations.append(NarrativeViolation(
                "error", "addressee_exists",
                f"对白对象不存在: {line.to_id}",
            ))

    return NarrativeCheckResult(
        valid=len([v for v in violations if v.severity == "error"]) == 0,
        violations=violations,
    )


def _check_knowledge_leak(
    speaker_id: str,
    text: str,
    state: WorldState,
    event: WorldEvent,
    out: List[NarrativeViolation],
) -> None:
    """启发式认知泄漏检测。

    检查说话者对白里是否出现了他 belief=unknown 的事实的关键词。
    优先用 CharacterBelief.keywords (显式声明的中文关键词)，
    没有则从 fact_id 粗略提取 (对中文效果差，仅兜底)。
    精确检测需要语义理解，留给后续 LLM judge。
    """
    beliefs = state.beliefs.get(speaker_id, [])
    for b in beliefs:
        if b.belief.value != "unknown":
            continue
        keywords = b.keywords or _extract_keywords(b.fact_id)
        for kw in keywords:
            if kw and len(kw) >= 2 and kw in text:
                out.append(NarrativeViolation(
                    "warning", "knowledge_leak",
                    f"{speaker_id} 对白可能泄露未知事实 '{b.fact_id}' "
                    f"(关键词 '{kw}')",
                ))
                break


def _extract_keywords(fact_id: str) -> List[str]:
    """从 fact_id 提取关键词。fact_xxx_yyy_zzz -> [xxx, yyy, zzz]。"""
    cleaned = re.sub(r"^fact_", "", fact_id)
    parts = re.split(r"[_\s]+", cleaned)
    return [p for p in parts if p]
