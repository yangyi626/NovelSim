import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from engine import (
    CORE_TOOL_PERMISSIONS,
    AgentExecutionStateMachine,
    PlannerDecision,
    PlannerFeedback,
    PlannerIntent,
    PlannerPolicyConfig,
    PlannerPolicyKind,
    PlannerPolicyRouter,
    PromptedLLMPolicy,
    ReActPolicy,
    ScriptedPolicy,
    ToolCall,
    build_game_observation,
    create_core_tool_registry,
    call_openai_compatible,
    capture_llm_usage,
    planner_prompt_messages,
)
from examples.secret_letter import GUARD, LETTER, build_snapshot


def _definitions(registry):
    return tuple(registry.get(name) for name in registry.names())


def _observation():
    registry = create_core_tool_registry()
    return (
        build_game_observation(build_snapshot(), GUARD, registry),
        registry,
    )


def _pick_up_call(suffix="scripted"):
    return ToolCall(
        call_id="policy_%s_pick_up" % suffix,
        actor_id=GUARD,
        tool_name="pick_up",
        arguments={"item_id": LETTER},
    )


def test_planner_decision_forbids_patch_payloads_and_actor_spoofing():
    with pytest.raises(ValidationError, match="StatePatch"):
        PlannerDecision(
            policy_id="bad",
            actor_id=GUARD,
            intent=PlannerIntent.interact,
            tool_call=ToolCall(
                actor_id=GUARD,
                tool_name="pick_up",
                arguments={
                    "item_id": LETTER,
                    "expected_patch": {"operations": []},
                },
            ),
        )

    with pytest.raises(ValidationError, match="must match"):
        PlannerDecision(
            policy_id="bad",
            actor_id=GUARD,
            intent=PlannerIntent.interact,
            tool_call=ToolCall(
                actor_id="char_player",
                tool_name="pick_up",
                arguments={"item_id": LETTER},
            ),
        )


def test_prompt_and_react_adapters_normalize_structured_decisions():
    observation, registry = _observation()
    definitions = _definitions(registry)
    prompt = PromptedLLMPolicy(
        lambda obs, tools: {
            "intent": "interact",
            "tool_call": _pick_up_call("prompt").dict(),
            "confidence": 0.8,
            "reason_summary": "密信在守卫所在门房且可拾取",
        }
    )
    prompt_decision = prompt.decide(observation, definitions)

    feedback_seen = []

    def react_generator(obs, tools):
        feedback_seen.append(obs.feedback)
        return _pick_up_call("react")

    react = ReActPolicy(react_generator)
    react_decision = react.replan(
        observation,
        definitions,
        PlannerFeedback(
            previous_decision_id=prompt_decision.decision_id,
            tool_name="observe",
            success=False,
            failure_code="precondition_failed",
            summary="需要先取得密信",
            retryable=True,
        ),
    )

    assert prompt_decision.policy_id == "prompt"
    assert prompt_decision.tool_call.tool_name == "pick_up"
    assert react_decision.policy_id == "react"
    assert feedback_seen[0].failure_code == "precondition_failed"


@pytest.mark.parametrize(
    "active_policy,expected_call_suffix",
    [
        (PlannerPolicyKind.scripted, "scripted"),
        (PlannerPolicyKind.prompt, "prompt"),
        (PlannerPolicyKind.react, "react"),
    ],
)
def test_policy_router_switches_by_config(active_policy, expected_call_suffix):
    observation, registry = _observation()
    policies = {
        PlannerPolicyKind.scripted: ScriptedPolicy(
            lambda obs, tools: _pick_up_call("scripted")
        ),
        PlannerPolicyKind.prompt: PromptedLLMPolicy(
            lambda obs, tools: _pick_up_call("prompt")
        ),
        PlannerPolicyKind.react: ReActPolicy(
            lambda obs, tools: _pick_up_call("react")
        ),
    }
    router = PlannerPolicyRouter(
        policies,
        config=PlannerPolicyConfig(active_policy=active_policy),
    )
    try:
        decision = router.decide(observation, _definitions(registry))
    finally:
        router.close()

    assert decision.policy_id == active_policy.value
    assert decision.tool_call.call_id == "policy_%s_pick_up" % expected_call_suffix
    assert decision.fallback_reason is None


def test_planner_policy_config_reads_environment(monkeypatch):
    monkeypatch.setenv("NOVELSIM_PLANNER_POLICY", "react")
    monkeypatch.setenv("NOVELSIM_PLANNER_FALLBACK", "scripted")
    monkeypatch.setenv("NOVELSIM_PLANNER_TIMEOUT_SECONDS", "1.25")

    config = PlannerPolicyConfig.from_env()

    assert config.active_policy == PlannerPolicyKind.react
    assert config.fallback_policy == PlannerPolicyKind.scripted
    assert config.timeout_seconds == 1.25


def test_policy_router_uses_scripted_fallback_on_error_and_timeout():
    observation, registry = _observation()
    fallback = ScriptedPolicy(lambda obs, tools: _pick_up_call("fallback"))

    def broken(obs, tools):
        raise RuntimeError("provider unavailable")

    error_router = PlannerPolicyRouter(
        {
            PlannerPolicyKind.scripted: fallback,
            PlannerPolicyKind.prompt: PromptedLLMPolicy(broken),
        },
        config=PlannerPolicyConfig(active_policy=PlannerPolicyKind.prompt),
    )
    try:
        error_decision = error_router.decide(
            observation,
            _definitions(registry),
        )
    finally:
        error_router.close()

    def slow(obs, tools):
        time.sleep(0.05)
        return _pick_up_call("too_late")

    timeout_router = PlannerPolicyRouter(
        {
            PlannerPolicyKind.scripted: fallback,
            PlannerPolicyKind.react: ReActPolicy(slow),
        },
        config=PlannerPolicyConfig(
            active_policy=PlannerPolicyKind.react,
            timeout_seconds=0.005,
        ),
    )
    try:
        timeout_decision = timeout_router.decide(
            observation,
            _definitions(registry),
        )
    finally:
        timeout_router.close()

    assert error_decision.policy_id == "scripted"
    assert error_decision.fallback_reason.startswith("RuntimeError")
    assert timeout_decision.policy_id == "scripted"
    assert timeout_decision.fallback_reason == "timeout:react"


def test_illegal_planner_proposal_cannot_commit_world_state():
    state = build_snapshot()
    registry = create_core_tool_registry()
    observation = build_game_observation(state, GUARD, registry)
    policy = ScriptedPolicy(
        lambda obs, tools: ToolCall(
            actor_id=GUARD,
            tool_name="move_to",
            arguments={"destination_id": "aircraft"},
        )
    )
    decision = policy.decide(observation, _definitions(registry))

    outcome = asyncio.run(
        AgentExecutionStateMachine(registry).execute(
            decision.tool_call,
            state,
            permissions=CORE_TOOL_PERMISSIONS,
        )
    )

    assert decision.tool_call.arguments["destination_id"] == "aircraft"
    assert outcome.result.success is False
    assert outcome.result.failure.code.value == "target_not_found"
    assert outcome.event is None
    assert outcome.new_state == state


def test_prompted_openai_call_uses_shared_prompt_and_bounded_options(monkeypatch):
    observation, registry = _observation()
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            model="qwen3.6-plus",
            usage={
                "prompt_tokens": 50,
                "completion_tokens": 10,
                "total_tokens": 60,
            },
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "intent": "interact",
                "tool_call": _pick_up_call("provider").dict(),
                "confidence": 0.8,
            })))],
        )

    monkeypatch.setattr(
        "engine.planner_policy.get_llm_config",
        lambda: SimpleNamespace(
            api_key="test-key",
            base_url="https://provider.invalid/v1",
            model="qwen3.6-plus",
        ),
    )
    monkeypatch.setattr(
        "engine.planner_policy.openai.ChatCompletion.create",
        create,
    )
    policy = PromptedLLMPolicy(
        model="qwen3.6-plus",
        max_tokens=256,
        temperature=0.1,
        request_timeout_seconds=7.0,
    )

    with capture_llm_usage() as collector:
        decision = policy.decide(observation, _definitions(registry))

    assert decision.tool_call.tool_name == "pick_up"
    assert captured["messages"] == planner_prompt_messages(observation)
    assert captured["max_tokens"] == 256
    assert captured["enable_thinking"] is False
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["temperature"] == 0.1
    assert captured["request_timeout"] == 7.0
    assert collector.summary().total_tokens == 60


def test_policy_router_propagates_usage_context_into_worker_thread():
    observation, registry = _observation()
    fallback = ScriptedPolicy(lambda obs, tools: _pick_up_call("fallback"))

    def generator(obs, tools):
        call_openai_compatible(
            lambda **kwargs: {
                "model": "thread-model",
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "total_tokens": 15,
                },
                "choices": [],
            },
            operation="planner_policy",
            model="thread-model",
        )
        return _pick_up_call("thread")

    router = PlannerPolicyRouter(
        {
            PlannerPolicyKind.scripted: fallback,
            PlannerPolicyKind.prompt: PromptedLLMPolicy(generator),
        },
        config=PlannerPolicyConfig(active_policy=PlannerPolicyKind.prompt),
    )
    try:
        with capture_llm_usage() as collector:
            router.decide(observation, _definitions(registry))
    finally:
        router.close()

    assert collector.summary().call_count == 1
    assert collector.summary().total_tokens == 15
