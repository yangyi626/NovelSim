"""显式 Agent 工具执行状态机。

状态机接收已经由高层规划器生成的 :class:`ToolCall`，依次完成感知快照、
决策接纳、Schema/权限/前置条件校验、执行、Patch 校验、事件提交、观察和
反思。只有 ``commit_event`` 与可选 ``WorldStore.commit_turn`` 能推进权威
世界版本。
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from enum import Enum
from time import perf_counter
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Union,
)
from uuid import uuid4

from pydantic import BaseModel, Field

from world_schema import WorldEvent, WorldState

from .agent_tools import (
    ToolCall,
    ToolCandidate,
    ToolExecutionError,
    ToolFailure,
    ToolFailureCode,
    ToolRegistry,
    ToolResult,
)
from .agent_trace import (
    AgentTrace,
    AgentTraceRecorder,
    TraceSpanStatus,
    TraceStage,
)
from .event import CommitError, commit_event
from .patch import PatchError, apply_patch
from .patch_validator import validate_patch, validate_tool_patch
from .plan_progress import derive_plan_progress_operations
from .persistence import PersistenceError, VersionConflict
from world_schema import CausalEvidence


class AgentExecutionState(str, Enum):
    idle = "idle"
    perceive = "perceive"
    retrieve_memory = "retrieve_memory"
    decide = "decide"
    validate_tool = "validate_tool"
    navigate = "navigate"
    execute_tool = "execute_tool"
    observe_result = "observe_result"
    reflect = "reflect"
    recover = "recover"
    aborted = "aborted"


class AgentExecutionStatus(str, Enum):
    running = "running"
    succeeded = "succeeded"
    aborted = "aborted"
    replan_required = "replan_required"


class AgentExecution(BaseModel):
    """一次状态机运行的可序列化快照。"""

    execution_id: str = Field(default_factory=lambda: uuid4().hex)
    trace_id: str
    active_call: ToolCall
    current_state: AgentExecutionState = AgentExecutionState.idle
    state_history: List[AgentExecutionState] = Field(
        default_factory=lambda: [AgentExecutionState.idle]
    )
    status: AgentExecutionStatus = AgentExecutionStatus.running
    retry_count: int = 0
    replan_count: int = 0
    max_retries: int = 1
    max_replans: int = 1
    waiting_for: Optional[str] = None
    termination_reason: Optional[str] = None

    class Config:
        extra = "forbid"


class AgentExecutionOutcome(BaseModel):
    """状态机执行结果；失败时 ``new_state`` 保持输入快照。"""

    execution: AgentExecution
    result: ToolResult
    new_state: WorldState
    event: Optional[WorldEvent] = None
    trace: AgentTrace

    class Config:
        extra = "forbid"


ReplanCallback = Callable[
    [ToolFailure, AgentExecution, WorldState],
    Union[Optional[ToolCall], Awaitable[Optional[ToolCall]]],
]


_STATE_STAGE = {
    AgentExecutionState.idle: TraceStage.lifecycle,
    AgentExecutionState.perceive: TraceStage.perception,
    AgentExecutionState.retrieve_memory: TraceStage.memory_retrieval,
    AgentExecutionState.decide: TraceStage.decision,
    AgentExecutionState.validate_tool: TraceStage.validation,
    AgentExecutionState.navigate: TraceStage.navigation,
    AgentExecutionState.execute_tool: TraceStage.execution,
    AgentExecutionState.observe_result: TraceStage.observation,
    AgentExecutionState.reflect: TraceStage.reflection,
    AgentExecutionState.recover: TraceStage.recovery,
    AgentExecutionState.aborted: TraceStage.lifecycle,
}

_REPLAN_CODES = {
    ToolFailureCode.unknown_tool,
    ToolFailureCode.invalid_arguments,
    ToolFailureCode.target_not_found,
    ToolFailureCode.precondition_failed,
    ToolFailureCode.spatial_constraint,
    ToolFailureCode.cognitive_boundary,
    ToolFailureCode.patch_rejected,
    ToolFailureCode.version_conflict,
    ToolFailureCode.timeout,
    ToolFailureCode.execution_error,
    ToolFailureCode.retry_exhausted,
}


class AgentExecutionStateMachine:
    """执行受控工具调用，支持超时、一次重试、重新规划和终止。"""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        max_retries: int = 1,
        max_replans: int = 1,
    ) -> None:
        if max_retries < 0 or max_replans < 0:
            raise ValueError("retry and replan limits cannot be negative")
        self.registry = registry
        self.max_retries = max_retries
        self.max_replans = max_replans

    async def execute(
        self,
        call: ToolCall,
        state: WorldState,
        *,
        permissions: Iterable[str] = (),
        metadata: Optional[Dict[str, Any]] = None,
        store: Optional[Any] = None,
        session_id: Optional[str] = None,
        replan: Optional[ReplanCallback] = None,
    ) -> AgentExecutionOutcome:
        """运行一次工具执行链路。

        ``store`` 与 ``session_id`` 同时提供时，状态和事件会通过存储层的乐观
        锁事务原子持久化。任何失败都返回结构化 ``ToolResult``，不抛出业务
        异常，也不修改输入 ``state``。
        """

        started = perf_counter()
        granted_permissions = frozenset(permissions)
        run_metadata = dict(metadata or {})
        execution_id = uuid4().hex
        trace_id = call.parent_trace_id or uuid4().hex
        execution = AgentExecution(
            execution_id=execution_id,
            trace_id=trace_id,
            active_call=call,
            max_retries=self.max_retries,
            max_replans=self.max_replans,
        )
        recorder = AgentTraceRecorder(
            execution_id=execution_id,
            actor_id=call.actor_id,
            call_id=call.call_id,
            tool_name=call.tool_name,
            initial_world_version=state.version,
            trace_id=trace_id,
        )
        recorder.transition(
            stage=TraceStage.lifecycle,
            state=AgentExecutionState.idle.value,
            details={"world_version": state.version},
        )

        if (store is None) != (session_id is None):
            failure = ToolFailure(
                code=ToolFailureCode.execution_error,
                message="store and session_id must be provided together",
                stage=AgentExecutionState.idle.value,
                details={},
            )
            return self._finish_failure(
                execution,
                recorder,
                call,
                state,
                failure,
                started,
                candidate=None,
            )

        self._transition(
            execution,
            recorder,
            AgentExecutionState.perceive,
            details={
                "actor_id": call.actor_id,
                "actor_location_id": _actor_location(state, call.actor_id),
                "world_version": state.version,
            },
        )
        self._transition(
            execution,
            recorder,
            AgentExecutionState.retrieve_memory,
            details={
                "memory_ids": list(run_metadata.get("memory_ids") or []),
            },
        )
        self._transition(
            execution,
            recorder,
            AgentExecutionState.decide,
            details={
                "call_id": call.call_id,
                "tool_name": call.tool_name,
                "decision_source": run_metadata.get(
                    "decision_source",
                    "external_planner",
                ),
            },
        )

        active_call = call
        retries_for_call = 0
        candidate: Optional[ToolCandidate] = None

        while True:
            try:
                self._transition(
                    execution,
                    recorder,
                    AgentExecutionState.validate_tool,
                    details={
                        "call_id": active_call.call_id,
                        "tool_name": active_call.tool_name,
                    },
                )
                prepared = self.registry.prepare(
                    active_call,
                    state,
                    permissions=granted_permissions,
                )

                if prepared.definition.requires_navigation_state:
                    execution.waiting_for = str(
                        active_call.arguments.get("destination_id") or ""
                    )
                    self._transition(
                        execution,
                        recorder,
                        AgentExecutionState.navigate,
                        details={"waiting_for": execution.waiting_for},
                    )

                self._transition(
                    execution,
                    recorder,
                    AgentExecutionState.execute_tool,
                    details={
                        "timeout_seconds": prepared.definition.timeout_seconds,
                        "attempt": retries_for_call + 1,
                    },
                )
                candidate = await asyncio.wait_for(
                    self.registry.invoke(
                        prepared,
                        active_call,
                        state,
                        permissions=granted_permissions,
                        metadata={
                            **run_metadata,
                            "tool_call_id": active_call.call_id,
                            "tool_name": active_call.tool_name,
                        },
                    ),
                    timeout=prepared.definition.timeout_seconds,
                )

                self._transition(
                    execution,
                    recorder,
                    AgentExecutionState.observe_result,
                    details={
                        "candidate_operation_count": len(
                            candidate.patch.operations
                        ),
                        "presentation_event_count": len(
                            candidate.presentation_events
                        ),
                    },
                )
                candidate = candidate.copy(deep=True)
                candidate.patch.causal_evidence = CausalEvidence(
                    action_id=active_call.call_id,
                    tool_call_id=active_call.call_id,
                    tool_name=active_call.tool_name,
                    actor_id=active_call.actor_id,
                    authority="tool_registry",
                )
                check = validate_tool_patch(
                    state,
                    tool_name=active_call.tool_name,
                    call_id=active_call.call_id,
                    actor_id=active_call.actor_id,
                    arguments=prepared.arguments.dict(),
                    output=candidate.output,
                    allowed_operations=(
                        prepared.definition.allowed_patch_operations
                    ),
                    patch=candidate.patch,
                )
                if not check.valid:
                    raise ToolExecutionError(
                        ToolFailure(
                            code=ToolFailureCode.patch_rejected,
                            message=f"candidate patch rejected: {check.why()}",
                            stage=AgentExecutionState.observe_result.value,
                            details={
                                "violations": [
                                    {
                                        "op_index": item.op_index,
                                        "rule_id": item.rule_id,
                                        "message": item.message,
                                    }
                                    for item in check.violations
                                ]
                            },
                        )
                    )

                projected_state = apply_patch(state, candidate.patch)
                plan_operations = derive_plan_progress_operations(
                    state,
                    projected_state,
                    actor_id=active_call.actor_id,
                    tool_name=active_call.tool_name,
                    arguments=prepared.arguments.dict(),
                )
                if plan_operations:
                    candidate.patch.operations.extend(plan_operations)
                    augmented_check = validate_patch(state, candidate.patch)
                    if not augmented_check.valid:
                        raise ToolExecutionError(
                            ToolFailure(
                                code=ToolFailureCode.patch_rejected,
                                message=(
                                    "runtime plan progress rejected: "
                                    f"{augmented_check.why()}"
                                ),
                                stage=AgentExecutionState.observe_result.value,
                                details={
                                    "violations": [
                                        {
                                            "op_index": item.op_index,
                                            "rule_id": item.rule_id,
                                            "message": item.message,
                                        }
                                        for item in augmented_check.violations
                                    ]
                                },
                            )
                        )

                event, new_state = commit_event(
                    state,
                    action_id=active_call.call_id,
                    event_type=f"tool.{active_call.tool_name}",
                    patch=candidate.patch,
                    actor_ids=[active_call.actor_id],
                    target_ids=list(candidate.target_ids),
                    expected_version=state.version,
                    summary=candidate.summary,
                    presentation_events=[
                        item.dict()
                        for item in candidate.presentation_events
                    ],
                )

                if store is not None:
                    await _commit_to_store(
                        store,
                        session_id,
                        expected_version=state.version,
                        new_state=new_state,
                        event=event,
                        call=active_call,
                        trace_id=trace_id,
                    )

                execution.waiting_for = None
                self._transition(
                    execution,
                    recorder,
                    AgentExecutionState.reflect,
                    details={
                        "success": True,
                        "event_id": event.event_id,
                        "world_version": new_state.version,
                    },
                )
                execution.status = AgentExecutionStatus.succeeded
                execution.termination_reason = "success"
                self._transition(
                    execution,
                    recorder,
                    AgentExecutionState.idle,
                    details={"completed": True},
                )
                trace = recorder.finish(
                    outcome=execution.status.value,
                    final_world_version=new_state.version,
                )
                result = ToolResult(
                    call_id=active_call.call_id,
                    tool_name=active_call.tool_name,
                    success=True,
                    output=dict(candidate.output),
                    latency_ms=_elapsed_ms(started),
                    candidate_patch=candidate.patch,
                    presentation_events=list(candidate.presentation_events),
                    committed_event_id=event.event_id,
                    world_version=new_state.version,
                    retry_count=execution.retry_count,
                )
                return AgentExecutionOutcome(
                    execution=execution,
                    result=result,
                    new_state=new_state,
                    event=event,
                    trace=trace,
                )

            except ToolExecutionError as exc:
                failure = exc.failure
            except asyncio.TimeoutError:
                failure = ToolFailure(
                    code=ToolFailureCode.timeout,
                    message=f"tool timed out: {active_call.tool_name}",
                    stage=AgentExecutionState.execute_tool.value,
                    retryable=True,
                    details={},
                )
            except VersionConflict as exc:
                failure = ToolFailure(
                    code=ToolFailureCode.version_conflict,
                    message=str(exc),
                    stage=AgentExecutionState.observe_result.value,
                    details={"expected_version": state.version},
                )
            except (PatchError, CommitError) as exc:
                failure = ToolFailure(
                    code=ToolFailureCode.patch_rejected,
                    message=str(exc),
                    stage=AgentExecutionState.observe_result.value,
                    details={},
                )
            except PersistenceError as exc:
                failure = ToolFailure(
                    code=ToolFailureCode.execution_error,
                    message=f"persistence failed: {exc}",
                    stage=AgentExecutionState.observe_result.value,
                    retryable=True,
                    details={},
                )
            except Exception as exc:  # noqa: BLE001 - 统一为结构化失败
                failure = ToolFailure(
                    code=ToolFailureCode.execution_error,
                    message=f"{type(exc).__name__}: {exc}",
                    stage=execution.current_state.value,
                    retryable=True,
                    details={},
                )

            self._transition(
                execution,
                recorder,
                AgentExecutionState.recover,
                details={
                    "failure_code": failure.code.value,
                    "retryable": failure.retryable,
                },
                previous_status=TraceSpanStatus.error,
                previous_details={"failure": failure.dict()},
            )

            if failure.retryable and retries_for_call < self.max_retries:
                retries_for_call += 1
                execution.retry_count += 1
                continue

            try:
                replanned_call = await self._try_replan(
                    replan,
                    failure,
                    execution,
                    state,
                )
            except Exception as exc:  # noqa: BLE001 - 规划器也必须结构化失败
                failure = ToolFailure(
                    code=ToolFailureCode.execution_error,
                    message=f"replan callback failed: {type(exc).__name__}: {exc}",
                    stage=AgentExecutionState.recover.value,
                    details={},
                )
                replanned_call = None
            if replanned_call is not None:
                active_call = replanned_call
                execution.active_call = replanned_call
                execution.replan_count += 1
                retries_for_call = 0
                candidate = None
                recorder.add_call(replanned_call.call_id)
                self._transition(
                    execution,
                    recorder,
                    AgentExecutionState.decide,
                    details={
                        "call_id": replanned_call.call_id,
                        "tool_name": replanned_call.tool_name,
                        "decision_source": "replan_callback",
                    },
                )
                continue

            return self._finish_failure(
                execution,
                recorder,
                active_call,
                state,
                failure,
                started,
                candidate=candidate,
            )

    async def _try_replan(
        self,
        callback: Optional[ReplanCallback],
        failure: ToolFailure,
        execution: AgentExecution,
        state: WorldState,
    ) -> Optional[ToolCall]:
        if (
            callback is None
            or execution.replan_count >= self.max_replans
            or failure.code not in _REPLAN_CODES
        ):
            return None
        callback_execution = execution.copy(deep=True)
        callback_state = state.copy(deep=True)
        if inspect.iscoroutinefunction(callback):
            value = await callback(failure, callback_execution, callback_state)
        else:
            loop = asyncio.get_running_loop()
            value = await loop.run_in_executor(
                None,
                functools.partial(
                    callback,
                    failure,
                    callback_execution,
                    callback_state,
                ),
            )
        if inspect.isawaitable(value):
            value = await value
        if value is None:
            return None
        if not isinstance(value, ToolCall):
            raise TypeError("replan callback must return ToolCall or None")
        if value.actor_id != execution.active_call.actor_id:
            return None
        return value

    def _finish_failure(
        self,
        execution: AgentExecution,
        recorder: AgentTraceRecorder,
        call: ToolCall,
        state: WorldState,
        failure: ToolFailure,
        started: float,
        *,
        candidate: Optional[ToolCandidate],
    ) -> AgentExecutionOutcome:
        replan_required = failure.code in _REPLAN_CODES
        execution.status = (
            AgentExecutionStatus.replan_required
            if replan_required
            else AgentExecutionStatus.aborted
        )
        execution.termination_reason = failure.code.value
        execution.waiting_for = None
        self._transition(
            execution,
            recorder,
            AgentExecutionState.aborted,
            details={
                "failure_code": failure.code.value,
                "replan_required": replan_required,
            },
        )
        failure_payload = failure.dict()
        trace = recorder.finish(
            outcome=execution.status.value,
            final_world_version=state.version,
            failure=failure_payload,
        )
        result = ToolResult(
            call_id=call.call_id,
            tool_name=call.tool_name,
            success=False,
            failure=failure,
            latency_ms=_elapsed_ms(started),
            candidate_patch=candidate.patch if candidate is not None else None,
            presentation_events=(
                list(candidate.presentation_events)
                if candidate is not None
                else []
            ),
            world_version=state.version,
            retry_count=execution.retry_count,
            replan_required=replan_required,
        )
        return AgentExecutionOutcome(
            execution=execution,
            result=result,
            new_state=state.copy(deep=True),
            event=None,
            trace=trace,
        )

    @staticmethod
    def _transition(
        execution: AgentExecution,
        recorder: AgentTraceRecorder,
        state: AgentExecutionState,
        *,
        details: Optional[Dict[str, Any]] = None,
        previous_status: TraceSpanStatus = TraceSpanStatus.ok,
        previous_details: Optional[Dict[str, Any]] = None,
    ) -> None:
        execution.current_state = state
        execution.state_history.append(state)
        recorder.transition(
            stage=_STATE_STAGE[state],
            state=state.value,
            details=details,
            previous_status=previous_status,
            previous_details=previous_details,
        )


def _actor_location(state: WorldState, actor_id: str) -> Optional[str]:
    actor = state.characters.get(actor_id)
    return actor.location_id if actor is not None else None


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)


async def _commit_to_store(
    store: Any,
    session_id: str,
    *,
    expected_version: int,
    new_state: WorldState,
    event: WorldEvent,
    call: ToolCall,
    trace_id: str,
) -> None:
    """在线程池中执行当前同步 WorldStore 契约，避免阻塞 async 运行时。"""

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        functools.partial(
            store.commit_turn,
            session_id,
            expected_version=expected_version,
            new_state=new_state,
            event=event,
            player_input=f"tool:{call.tool_name}",
            turn_payload={
                "status": "committed",
                "tool_call": call.dict(),
                "trace_id": trace_id,
                "narrative": {
                    "narration": event.summary,
                    "dialogues": [],
                    "system_hints": [
                        "联合计划中的受限工具已通过规则校验并提交。"
                    ],
                },
            },
        ),
    )
