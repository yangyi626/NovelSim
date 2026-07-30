import pytest

from engine import (
    LLMUsageSummary,
    call_openai_compatible,
    capture_llm_usage,
    chat_generation_options,
)
from evaluation.metrics import estimate_cost
from evaluation.models import PricingConfig


def test_openai_compatible_usage_is_captured_from_real_response_fields():
    def create(**kwargs):
        assert kwargs["model"] == "model-a"
        return {
            "model": "provider-model-a",
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 20},
            },
            "choices": [],
        }

    with capture_llm_usage() as collector:
        response = call_openai_compatible(
            create,
            operation="test_operation",
            model="model-a",
            messages=[],
        )

    assert response["model"] == "provider-model-a"
    assert len(collector.calls) == 1
    call = collector.calls[0]
    assert call.operation == "test_operation"
    assert call.model == "provider-model-a"
    assert call.prompt_tokens == 120
    assert call.completion_tokens == 30
    assert call.total_tokens == 150
    assert call.cached_tokens == 20
    assert call.success is True
    assert collector.summary() == LLMUsageSummary(
        call_count=1,
        prompt_tokens=120,
        completion_tokens=30,
        total_tokens=150,
        cached_tokens=20,
        latency_ms=call.latency_ms,
    )


def test_failed_call_and_nested_collectors_are_context_isolated():
    def fail(**kwargs):
        raise TimeoutError("provider timeout")

    with capture_llm_usage() as outer:
        call_openai_compatible(
            lambda **kwargs: {"usage": {}},
            operation="outer",
            model="model",
        )
        with capture_llm_usage() as inner:
            with pytest.raises(TimeoutError):
                call_openai_compatible(
                    fail,
                    operation="inner",
                    model="model",
                )

    assert [item.operation for item in outer.calls] == ["outer"]
    assert [item.operation for item in inner.calls] == ["inner"]
    assert inner.summary().failed_call_count == 1
    assert inner.calls[0].error_type == "TimeoutError"


def test_qwen_structured_options_disable_thinking_and_cap_output():
    assert chat_generation_options(
        "qwen3.7-plus",
        max_tokens=1024,
    ) == {
        "max_tokens": 1024,
        "enable_thinking": False,
    }
    assert chat_generation_options(
        "another-openai-compatible-model",
        max_tokens=512,
    ) == {"max_tokens": 512}


def test_cost_uses_uncached_cached_and_output_rates():
    usage = LLMUsageSummary(
        call_count=1,
        prompt_tokens=1_000_000,
        completion_tokens=500_000,
        total_tokens=1_500_000,
        cached_tokens=250_000,
    )
    pricing = PricingConfig(
        input_per_million=2.0,
        cached_input_per_million=0.5,
        output_per_million=8.0,
    )

    # 0.75M * 2 + 0.25M * 0.5 + 0.5M * 8 = 5.625
    assert estimate_cost(usage, pricing) == 5.625
    assert estimate_cost(usage, PricingConfig()) is None
