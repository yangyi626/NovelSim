"""Agent 工具执行 Trace。

Trace 是一次执行的可序列化证据链。它记录状态机阶段、耗时、世界版本和
结构化失败信息，但不参与权威状态写入，因此可安全地作为日志或评测输入。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from time import perf_counter
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class TraceStage(str, Enum):
    """Agent 执行链路中的可观测阶段。"""

    lifecycle = "lifecycle"
    perception = "perception"
    memory_retrieval = "memory_retrieval"
    decision = "decision"
    validation = "validation"
    navigation = "navigation"
    execution = "execution"
    observation = "observation"
    recovery = "recovery"
    reflection = "reflection"


class TraceSpanStatus(str, Enum):
    running = "running"
    ok = "ok"
    error = "error"
    cancelled = "cancelled"


class AgentTraceSpan(BaseModel):
    """一个状态阶段的起止时间和结构化上下文。"""

    span_id: str = Field(default_factory=lambda: uuid4().hex)
    stage: TraceStage
    state: str
    started_at: str
    ended_at: Optional[str] = None
    duration_ms: float = 0.0
    status: TraceSpanStatus = TraceSpanStatus.running
    details: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"


class AgentTrace(BaseModel):
    """一次状态机执行的完整、可 JSON 序列化 Trace。"""

    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    execution_id: str
    actor_id: str
    initial_call_id: str
    call_ids: List[str] = Field(default_factory=list)
    initial_tool_name: str
    started_at: str
    ended_at: Optional[str] = None
    duration_ms: float = 0.0
    initial_world_version: int
    final_world_version: Optional[int] = None
    outcome: str = "running"
    failure: Optional[Dict[str, Any]] = None
    spans: List[AgentTraceSpan] = Field(default_factory=list)

    class Config:
        extra = "forbid"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentTraceRecorder:
    """状态机内部使用的单活动 Span 记录器。

    状态转换会自动关闭前一个 Span，从而保证每个阶段都有开始、结束和耗时。
    """

    def __init__(
        self,
        *,
        execution_id: str,
        actor_id: str,
        call_id: str,
        tool_name: str,
        initial_world_version: int,
        trace_id: Optional[str] = None,
    ) -> None:
        self._started = perf_counter()
        self._active_started: Optional[float] = None
        self._active: Optional[AgentTraceSpan] = None
        self.trace = AgentTrace(
            trace_id=trace_id or uuid4().hex,
            execution_id=execution_id,
            actor_id=actor_id,
            initial_call_id=call_id,
            call_ids=[call_id],
            initial_tool_name=tool_name,
            started_at=_utc_now(),
            initial_world_version=initial_world_version,
        )

    def add_call(self, call_id: str) -> None:
        if call_id not in self.trace.call_ids:
            self.trace.call_ids.append(call_id)

    def transition(
        self,
        *,
        stage: TraceStage,
        state: str,
        details: Optional[Dict[str, Any]] = None,
        previous_status: TraceSpanStatus = TraceSpanStatus.ok,
        previous_details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._close_active(previous_status, previous_details)
        self._active_started = perf_counter()
        self._active = AgentTraceSpan(
            stage=stage,
            state=state,
            started_at=_utc_now(),
            details=dict(details or {}),
        )
        self.trace.spans.append(self._active)

    def finish(
        self,
        *,
        outcome: str,
        final_world_version: int,
        failure: Optional[Dict[str, Any]] = None,
        current_status: TraceSpanStatus = TraceSpanStatus.ok,
        current_details: Optional[Dict[str, Any]] = None,
    ) -> AgentTrace:
        self._close_active(current_status, current_details)
        self.trace.ended_at = _utc_now()
        self.trace.duration_ms = round((perf_counter() - self._started) * 1000, 3)
        self.trace.final_world_version = final_world_version
        self.trace.outcome = outcome
        self.trace.failure = failure
        return self.trace.copy(deep=True)

    def _close_active(
        self,
        status: TraceSpanStatus,
        details: Optional[Dict[str, Any]],
    ) -> None:
        if self._active is None or self._active_started is None:
            return
        if details:
            self._active.details.update(details)
        self._active.ended_at = _utc_now()
        self._active.duration_ms = round(
            (perf_counter() - self._active_started) * 1000,
            3,
        )
        self._active.status = status
        self._active = None
        self._active_started = None
