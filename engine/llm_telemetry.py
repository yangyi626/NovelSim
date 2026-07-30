"""OpenAI-compatible 模型调用的上下文级 Telemetry。

业务模块仍获得原始 SDK 响应；本模块只在显式 ``capture_llm_usage`` 上下文中
记录调用次数、Token、耗时和失败，因此不会把评测状态变成全局可变单例，也不会
影响权威世界状态。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from typing import Any, Callable, Dict, Iterator, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class LLMCallUsage(BaseModel):
    """一次文本生成或 Embedding 请求的真实用量记录。"""

    call_id: str = Field(default_factory=lambda: uuid4().hex)
    operation: str
    kind: str = "chat"
    model: str
    prompt_tokens: int = Field(0, ge=0)
    completion_tokens: int = Field(0, ge=0)
    total_tokens: int = Field(0, ge=0)
    cached_tokens: int = Field(0, ge=0)
    latency_ms: float = Field(0.0, ge=0.0)
    success: bool = True
    error_type: Optional[str] = None

    class Config:
        extra = "forbid"


class LLMUsageSummary(BaseModel):
    """一段运行范围内的模型调用聚合。"""

    call_count: int = Field(0, ge=0)
    failed_call_count: int = Field(0, ge=0)
    prompt_tokens: int = Field(0, ge=0)
    completion_tokens: int = Field(0, ge=0)
    total_tokens: int = Field(0, ge=0)
    cached_tokens: int = Field(0, ge=0)
    latency_ms: float = Field(0.0, ge=0.0)

    class Config:
        extra = "forbid"


class LLMUsageCollector:
    """由 ContextVar 绑定的可嵌套调用收集器。"""

    def __init__(self) -> None:
        self._calls: List[LLMCallUsage] = []

    @property
    def calls(self) -> List[LLMCallUsage]:
        return [item.copy(deep=True) for item in self._calls]

    def record(self, usage: LLMCallUsage) -> None:
        self._calls.append(usage.copy(deep=True))

    def summary(self) -> LLMUsageSummary:
        return LLMUsageSummary(
            call_count=len(self._calls),
            failed_call_count=sum(
                1 for item in self._calls if not item.success
            ),
            prompt_tokens=sum(item.prompt_tokens for item in self._calls),
            completion_tokens=sum(
                item.completion_tokens for item in self._calls
            ),
            total_tokens=sum(item.total_tokens for item in self._calls),
            cached_tokens=sum(item.cached_tokens for item in self._calls),
            latency_ms=round(
                sum(item.latency_ms for item in self._calls),
                3,
            ),
        )


_ACTIVE_COLLECTOR: ContextVar[Optional[LLMUsageCollector]] = ContextVar(
    "novelsim_llm_usage_collector",
    default=None,
)


@contextmanager
def capture_llm_usage() -> Iterator[LLMUsageCollector]:
    """在当前异步/线程上下文采集模型调用，不污染相邻评测运行。"""

    collector = LLMUsageCollector()
    token = _ACTIVE_COLLECTOR.set(collector)
    try:
        yield collector
    finally:
        _ACTIVE_COLLECTOR.reset(token)


def call_openai_compatible(
    create: Callable[..., Any],
    *,
    operation: str,
    kind: str = "chat",
    model: str,
    **kwargs: Any,
) -> Any:
    """调用 OpenAI 0.28 风格方法，并从真实响应 ``usage`` 记录用量。"""

    started = perf_counter()
    try:
        response = create(model=model, **kwargs)
    except Exception as exc:
        _record(
            LLMCallUsage(
                operation=operation,
                kind=kind,
                model=model,
                latency_ms=_elapsed_ms(started),
                success=False,
                error_type=type(exc).__name__,
            )
        )
        raise

    usage = _as_mapping(_read(response, "usage", {}))
    prompt_tokens = _as_non_negative_int(
        usage.get("prompt_tokens", usage.get("input_tokens", 0))
    )
    completion_tokens = _as_non_negative_int(
        usage.get("completion_tokens", usage.get("output_tokens", 0))
    )
    total_tokens = _as_non_negative_int(usage.get("total_tokens", 0))
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens
    prompt_details = _as_mapping(
        usage.get("prompt_tokens_details")
        or usage.get("input_tokens_details")
        or {}
    )
    _record(
        LLMCallUsage(
            operation=operation,
            kind=kind,
            model=str(_read(response, "model", model) or model),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_tokens=_as_non_negative_int(
                prompt_details.get("cached_tokens", 0)
            ),
            latency_ms=_elapsed_ms(started),
        )
    )
    return response


def chat_generation_options(
    model: str,
    *,
    max_tokens: int,
    thinking: bool = False,
) -> Dict[str, Any]:
    """返回兼容 OpenAI 0.28 的受控文本生成参数。

    Qwen3 混合思考模型会默认生成较长思维 Token。实时游戏中的 JSON 解析、
    状态 Patch 和短叙事属于低延迟结构化任务，默认显式关闭思考；其它模型只
    接收标准 ``max_tokens``，避免向不支持的提供方发送扩展字段。
    """

    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    options: Dict[str, Any] = {"max_tokens": max_tokens}
    if model.strip().lower().startswith("qwen3"):
        # openai-python 0.28 会把扩展参数原样放进 JSON 请求体；DashScope
        # 的 OpenAI-compatible Chat Completions 正是以顶层字段接收该参数。
        options["enable_thinking"] = thinking
    return options


def _record(usage: LLMCallUsage) -> None:
    collector = _ACTIVE_COLLECTOR.get()
    if collector is not None:
        collector.record(usage)


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000.0, 3)


def _read(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _as_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    if hasattr(value, "to_dict_recursive"):
        result = value.to_dict_recursive()
        return result if isinstance(result, dict) else {}
    try:
        return dict(value)
    except (TypeError, ValueError):
        return {}


def _as_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
