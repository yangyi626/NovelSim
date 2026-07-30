"""可回放的轻量场景控制器。

SceneController 负责场景参与者、回合预算、Free/Script 调度和结束摘要；它不拥有
世界事实，也不直接应用 Patch。无论工具调用来自自由策略、脚本节拍还是玩家输入，
都必须经过同一个 AgentExecutionStateMachine 和 ToolRegistry。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from pydantic import BaseModel, Field, root_validator

from world_schema import WorldState

from .agent_runtime import AgentExecutionOutcome, AgentExecutionStateMachine
from .agent_tools import ToolCall, ToolRegistry


class SceneMode(str, Enum):
    free = "free"
    script = "script"


class SceneStatus(str, Enum):
    completed = "completed"
    tool_failed = "tool_failed"
    stalled = "stalled"
    turn_limit = "turn_limit"
    invalid_scene = "invalid_scene"


class ScriptBeat(BaseModel):
    """Script Mode 的高层节拍；执行时仍会走完整工具校验与提交链。"""

    beat_id: str
    actor_id: str
    tool_name: str
    arguments: Dict[str, object] = Field(default_factory=dict)
    objective: str = ""

    class Config:
        extra = "forbid"


class SceneConfig(BaseModel):
    scene_id: str
    mode: SceneMode = SceneMode.free
    location_id: Optional[str] = None
    participant_ids: List[str] = Field(default_factory=list)
    objective: str = ""
    max_turns: int = Field(12, ge=1, le=200)
    random_seed: int = 0
    stop_on_tool_failure: bool = True
    script_beats: List[ScriptBeat] = Field(default_factory=list)

    class Config:
        extra = "forbid"

    @root_validator(skip_on_failure=True)
    def _script_mode_has_beats(cls, values):
        if (
            values.get("mode") == SceneMode.script
            and not values.get("script_beats")
        ):
            raise ValueError("script mode requires at least one script beat")
        return values


class SceneEnding(BaseModel):
    ending_id: str
    objective_satisfied: bool
    reason: str = ""

    class Config:
        extra = "forbid"


class SceneStep(BaseModel):
    turn_index: int
    decision_source: str
    call_id: str
    actor_id: str
    tool_name: str
    success: bool
    failure_code: Optional[str] = None
    event_id: Optional[str] = None
    previous_version: int
    new_version: int

    class Config:
        extra = "forbid"


class SceneSummary(BaseModel):
    scene_id: str
    mode: SceneMode
    status: SceneStatus
    objective: str = ""
    objective_satisfied: bool = False
    ending_id: Optional[str] = None
    ending_reason: str = ""
    participant_ids: List[str] = Field(default_factory=list)
    turns_used: int = 0
    max_turns: int
    initial_version: int
    final_version: int
    event_ids: List[str] = Field(default_factory=list)
    tool_sequence: List[str] = Field(default_factory=list)
    failure_reasons: List[str] = Field(default_factory=list)
    steps: List[SceneStep] = Field(default_factory=list)
    causal_summary: List[str] = Field(default_factory=list)

    class Config:
        extra = "forbid"

    def to_memory_text(self) -> str:
        ending = self.ending_id or self.status.value
        chain = " -> ".join(self.tool_sequence) or "no committed action"
        return (
            f"Scene {self.scene_id} ended as {ending}; "
            f"objective_satisfied={self.objective_satisfied}; "
            f"turns={self.turns_used}; chain={chain}."
        )


@dataclass(frozen=True)
class SceneRun:
    state: WorldState
    outcomes: List[AgentExecutionOutcome]
    summary: SceneSummary


FreeCallSelector = Callable[[WorldState, int], Optional[ToolCall]]
EndingEvaluator = Callable[[WorldState], Optional[SceneEnding]]


class SceneController:
    """通过统一工具状态机运行一个有界场景。"""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        permissions: Iterable[str] = (),
    ) -> None:
        self.registry = registry
        self.permissions = frozenset(permissions)
        self.machine = AgentExecutionStateMachine(registry)

    async def run(
        self,
        state: WorldState,
        config: SceneConfig,
        *,
        free_selector: Optional[FreeCallSelector] = None,
        ending_evaluator: Optional[EndingEvaluator] = None,
        initial_calls: Sequence[ToolCall] = (),
        store: Optional[Any] = None,
        session_id: Optional[str] = None,
    ) -> SceneRun:
        if (store is None) != (session_id is None):
            raise ValueError(
                "store and session_id must be provided together"
            )
        current = state.copy(deep=True)
        initial_version = current.version
        participants, participant_error = _select_participants(current, config)
        outcomes: List[AgentExecutionOutcome] = []
        steps: List[SceneStep] = []
        failures: List[str] = []
        ending = _evaluate_ending(current, ending_evaluator)
        status: Optional[SceneStatus] = (
            SceneStatus.completed if ending is not None else None
        )

        if participant_error:
            failures.append(participant_error)
            status = SceneStatus.invalid_scene

        scheduled: List[tuple] = [
            ("player", call) for call in initial_calls
        ]
        if config.mode == SceneMode.script:
            scheduled.extend(
                (
                    "script",
                    ToolCall(
                        call_id=(
                            f"{config.scene_id}_{index:02d}_"
                            f"{beat.tool_name}"
                        ),
                        actor_id=beat.actor_id,
                        tool_name=beat.tool_name,
                        arguments=dict(beat.arguments),
                    ),
                )
                for index, beat in enumerate(config.script_beats, start=1)
            )

        scheduled_index = 0
        while status is None and len(steps) < config.max_turns:
            if scheduled_index < len(scheduled):
                decision_source, call = scheduled[scheduled_index]
                scheduled_index += 1
            elif config.mode == SceneMode.free:
                if free_selector is None:
                    failures.append("free mode has no call selector")
                    status = SceneStatus.invalid_scene
                    break
                decision_source = "free"
                call = free_selector(current, len(steps) + 1)
                if call is None:
                    ending = _evaluate_ending(current, ending_evaluator)
                    status = (
                        SceneStatus.completed
                        if ending is not None
                        else SceneStatus.stalled
                    )
                    break
            else:
                ending = _evaluate_ending(current, ending_evaluator)
                status = (
                    SceneStatus.completed
                    if ending is not None
                    else SceneStatus.stalled
                )
                break

            if call.actor_id not in participants:
                failures.append(
                    f"actor {call.actor_id} is outside scene participants"
                )
                status = SceneStatus.invalid_scene
                break

            previous_version = current.version
            outcome = await self.machine.execute(
                call,
                current,
                permissions=self.permissions,
                metadata={
                    "decision_source": decision_source,
                    "scene_id": config.scene_id,
                    "scene_mode": config.mode.value,
                    "scene_turn": len(steps) + 1,
                    "random_seed": config.random_seed,
                },
                store=store,
                session_id=session_id,
            )
            outcomes.append(outcome)
            current = outcome.new_state
            failure_code = (
                outcome.result.failure.code.value
                if outcome.result.failure is not None
                else None
            )
            step = SceneStep(
                turn_index=len(steps) + 1,
                decision_source=decision_source,
                call_id=outcome.result.call_id,
                actor_id=call.actor_id,
                tool_name=outcome.result.tool_name,
                success=outcome.result.success,
                failure_code=failure_code,
                event_id=outcome.result.committed_event_id,
                previous_version=previous_version,
                new_version=current.version,
            )
            steps.append(step)

            if not outcome.result.success:
                message = outcome.result.failure.message
                failures.append(
                    f"{call.tool_name}:{failure_code}:{message}"
                )
                if config.stop_on_tool_failure:
                    status = SceneStatus.tool_failed
                    break

            ending = _evaluate_ending(current, ending_evaluator)
            if ending is not None:
                status = SceneStatus.completed
                break

        if status is None:
            ending = _evaluate_ending(current, ending_evaluator)
            status = (
                SceneStatus.completed
                if ending is not None
                else SceneStatus.turn_limit
            )

        summary = SceneSummary(
            scene_id=config.scene_id,
            mode=config.mode,
            status=status,
            objective=config.objective,
            objective_satisfied=(
                ending.objective_satisfied if ending else False
            ),
            ending_id=ending.ending_id if ending else None,
            ending_reason=ending.reason if ending else "",
            participant_ids=participants,
            turns_used=len(steps),
            max_turns=config.max_turns,
            initial_version=initial_version,
            final_version=current.version,
            event_ids=[
                step.event_id for step in steps if step.event_id is not None
            ],
            tool_sequence=[step.tool_name for step in steps],
            failure_reasons=failures,
            steps=steps,
            causal_summary=[_causal_line(step) for step in steps],
        )
        return SceneRun(
            state=current,
            outcomes=outcomes,
            summary=summary,
        )


def _select_participants(
    state: WorldState,
    config: SceneConfig,
) -> tuple:
    location_id = config.location_id or state.current_scene_id
    requested = (
        list(dict.fromkeys(config.participant_ids))
        if config.participant_ids
        else [
            character_id
            for character_id, character in state.characters.items()
            if character.is_alive
            and (location_id is None or character.location_id == location_id)
        ]
    )
    missing = [
        character_id
        for character_id in requested
        if character_id not in state.characters
    ]
    if missing:
        return sorted(requested), (
            f"scene participants not found: {', '.join(sorted(missing))}"
        )
    unavailable = [
        character_id
        for character_id in requested
        if not state.characters[character_id].is_alive
        or (
            location_id is not None
            and state.characters[character_id].location_id != location_id
        )
    ]
    if unavailable:
        return sorted(requested), (
            "scene participants are dead or outside location: "
            + ", ".join(sorted(unavailable))
        )
    return sorted(requested), None


def _evaluate_ending(
    state: WorldState,
    evaluator: Optional[EndingEvaluator],
) -> Optional[SceneEnding]:
    return evaluator(state) if evaluator is not None else None


def _causal_line(step: SceneStep) -> str:
    if step.success:
        return (
            f"turn {step.turn_index}: {step.actor_id} used {step.tool_name}; "
            f"{step.event_id} committed v{step.previous_version}"
            f"->v{step.new_version}"
        )
    return (
        f"turn {step.turn_index}: {step.actor_id} used {step.tool_name}; "
        f"rejected as {step.failure_code}; version stayed "
        f"v{step.new_version}"
    )
