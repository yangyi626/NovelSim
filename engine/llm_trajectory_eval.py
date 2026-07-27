"""真实 LLM 长轨迹质量评分：分块审查，再聚合为发布门禁。"""

from __future__ import annotations

import json
import re
from typing import Callable, Dict, List, Optional

import openai
from pydantic import BaseModel, Field

from world_schema import WorldEvent, WorldState

from .config import get_llm_config
from .persistence import TurnRecord


TRAJECTORY_JUDGE_SYSTEM_PROMPT = """你是互动叙事长轨迹质量审查器。
请只根据提供的事件、玩家输入、叙事输出和角色目标评分，不要续写剧情。

五个维度均为 0 到 1：
- causal_coherence：行动、后果与后续事件是否有清晰因果。
- character_consistency：角色行为是否符合人格、认知和既有立场。
- goal_progression：角色目标/计划是否推进、受阻或合理调整，而非长期停滞。
- world_state_consistency：叙事是否与结构化事件和终态一致。
- repetition_control：是否避免重复台词、重复冲突和无意义原地循环。

issue 必须引用输入中存在的 event_id。没有问题时 issues 返回空数组。
只输出 JSON，不要解释或 markdown。

输出格式：
{
  "causal_coherence": 0.8,
  "character_consistency": 0.8,
  "goal_progression": 0.7,
  "world_state_consistency": 0.9,
  "repetition_control": 0.75,
  "summary": "简短结论",
  "strengths": ["优点"],
  "issues": [
    {
      "category": "goal_progression",
      "severity": "medium",
      "event_ids": ["event_x"],
      "message": "具体问题"
    }
  ]
}
"""


class TrajectoryQualityIssue(BaseModel):
    category: str
    severity: str = "medium"
    event_ids: List[str] = Field(default_factory=list)
    message: str


class TrajectoryQualityScore(BaseModel):
    causal_coherence: float = Field(..., ge=0.0, le=1.0)
    character_consistency: float = Field(..., ge=0.0, le=1.0)
    goal_progression: float = Field(..., ge=0.0, le=1.0)
    world_state_consistency: float = Field(..., ge=0.0, le=1.0)
    repetition_control: float = Field(..., ge=0.0, le=1.0)
    summary: str = ""
    strengths: List[str] = Field(default_factory=list)
    issues: List[TrajectoryQualityIssue] = Field(default_factory=list)

    @property
    def overall(self) -> float:
        return round(
            (
                self.causal_coherence
                + self.character_consistency
                + self.goal_progression
                + self.world_state_consistency
                + self.repetition_control
            )
            / 5.0,
            4,
        )


class LLMTrajectoryReport(BaseModel):
    event_count: int
    chunk_count: int
    overall_score: float = Field(..., ge=0.0, le=1.0)
    passed: bool
    threshold: float
    minimum_dimension: float
    aggregate: TrajectoryQualityScore
    chunks: List[TrajectoryQualityScore] = Field(default_factory=list)


class LLMTrajectoryEvaluator:
    """按事件窗口评分，避免长轨迹一次性塞入模型导致证据丢失。"""

    def __init__(
        self,
        model: Optional[str] = None,
        *,
        chunk_size: int = 12,
        threshold: float = 0.72,
        minimum_dimension: float = 0.6,
        before_llm_call: Optional[Callable[[], None]] = None,
    ):
        config = get_llm_config()
        self.api_key = config.api_key
        self.base_url = config.base_url
        self.model = model or config.model
        self.chunk_size = max(4, int(chunk_size))
        self.threshold = float(threshold)
        self.minimum_dimension = float(minimum_dimension)
        self.before_llm_call = before_llm_call
        self.last_error = ""

    def evaluate(
        self,
        events: List[WorldEvent],
        *,
        final_state: WorldState,
        turns: Optional[List[TurnRecord]] = None,
    ) -> LLMTrajectoryReport:
        if not events:
            raise ValueError("真实 LLM 长轨迹评分至少需要一个事件")
        self.last_error = ""
        turns_by_version = {
            turn.world_version: turn
            for turn in (turns or [])
        }
        chunk_scores: List[TrajectoryQualityScore] = []
        for start in range(0, len(events), self.chunk_size):
            chunk = events[start:start + self.chunk_size]
            payload = {
                "scope": {
                    "start_version": chunk[0].previous_version,
                    "end_version": chunk[-1].new_version,
                },
                "events": [
                    self._event_payload(event, turns_by_version)
                    for event in chunk
                ],
                "final_character_goals": self._goal_payload(final_state),
            }
            chunk_scores.append(
                self._score_payload(
                    payload,
                    instruction="请审查这个连续轨迹窗口。",
                )
            )

        if len(chunk_scores) == 1:
            aggregate = chunk_scores[0]
        else:
            aggregate = self._score_payload(
                {
                    "event_count": len(events),
                    "chunk_scores": [
                        {
                            **score.dict(),
                            "overall": score.overall,
                        }
                        for score in chunk_scores
                    ],
                    "final_character_goals": self._goal_payload(final_state),
                },
                instruction=(
                    "请聚合各窗口评分。不得简单忽略低分窗口；"
                    "重复出现的问题应提高严重度。"
                ),
            )

        dimensions = [
            aggregate.causal_coherence,
            aggregate.character_consistency,
            aggregate.goal_progression,
            aggregate.world_state_consistency,
            aggregate.repetition_control,
        ]
        passed = (
            aggregate.overall >= self.threshold
            and min(dimensions) >= self.minimum_dimension
        )
        return LLMTrajectoryReport(
            event_count=len(events),
            chunk_count=len(chunk_scores),
            overall_score=aggregate.overall,
            passed=passed,
            threshold=self.threshold,
            minimum_dimension=self.minimum_dimension,
            aggregate=aggregate,
            chunks=chunk_scores,
        )

    def _score_payload(
        self,
        payload: Dict,
        *,
        instruction: str,
    ) -> TrajectoryQualityScore:
        messages = [
            {
                "role": "system",
                "content": TRAJECTORY_JUDGE_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    f"{instruction}\n"
                    + json.dumps(payload, ensure_ascii=False)
                ),
            },
        ]
        try:
            raw = self._call_llm(messages)
            return TrajectoryQualityScore.parse_obj(_extract_json(raw))
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            raise RuntimeError(
                f"真实 LLM 长轨迹评分失败: {exc}"
            ) from exc

    def _call_llm(self, messages: list) -> str:
        if self.before_llm_call is not None:
            self.before_llm_call()
        response = openai.ChatCompletion.create(
            api_key=self.api_key,
            api_base=self.base_url,
            model=self.model,
            messages=messages,
            temperature=0.0,
        )
        return response.choices[0].message.content.strip()

    @staticmethod
    def _event_payload(
        event: WorldEvent,
        turns_by_version: Dict[int, TurnRecord],
    ) -> Dict:
        turn = turns_by_version.get(event.new_version)
        result = turn.result if turn else {}
        narrative = result.get("narrative") or {}
        return {
            "event_id": event.event_id,
            "version": event.new_version,
            "event_type": event.event_type,
            "summary": event.summary,
            "actors": list(event.actor_ids),
            "targets": list(event.target_ids),
            "operations": [
                operation.dict(exclude_none=True)
                for operation in event.patch.operations
            ],
            "player_input": turn.player_input if turn else "",
            "narration": narrative.get("narration", ""),
            "dialogues": narrative.get("dialogues", []),
            "npc_reactions": result.get("npc_reactions", []),
        }

    @staticmethod
    def _goal_payload(state: WorldState) -> List[Dict]:
        payload = []
        for character_id, psyche in state.character_psyches.items():
            payload.append(
                {
                    "character_id": character_id,
                    "traits": list(psyche.traits),
                    "emotion": psyche.emotion,
                    "goals": [goal.dict() for goal in psyche.goals],
                    "plans": [plan.dict() for plan in psyche.plans],
                }
            )
        return payload


def _extract_json(raw: str) -> Dict:
    if not raw:
        raise ValueError("轨迹评分模型返回为空")
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
    raise ValueError("轨迹评分模型未返回 JSON 对象")
