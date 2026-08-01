"""Interchangeable high-level planner policies and safe configuration router."""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextvars import copy_context
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Union

import openai
from pydantic import BaseModel, Field, ValidationError, validator

from .agent_tools import ToolCall, ToolDefinition
from .config import get_llm_config
from .game_observation import GameObservation, PlannerFeedback
from .game_observation import build_game_observation
from .llm_telemetry import call_openai_compatible, chat_generation_options
from .planner_decision import PlannerDecision, PlannerIntent
from .planner_prompt import (
    PLANNER_PROMPT_VERSION,
    planner_prompt_messages,
)
from world_schema import WorldState


PlannerValue = Union[PlannerDecision, ToolCall, Mapping[str, Any], None]
DecisionGenerator = Callable[
    [GameObservation, Sequence[ToolDefinition]],
    PlannerValue,
]
ActorSelector = Callable[[WorldState, int], Optional[str]]


class PlannerPolicy(Protocol):
    policy_id: str

    def decide(
        self,
        observation: GameObservation,
        available_tools: Sequence[ToolDefinition],
    ) -> PlannerDecision:
        ...


class PlannerPolicyKind(str, Enum):
    scripted = "scripted"
    prompt = "prompt"
    react = "react"


class PlannerPolicyConfig(BaseModel):
    active_policy: PlannerPolicyKind = PlannerPolicyKind.scripted
    fallback_policy: PlannerPolicyKind = PlannerPolicyKind.scripted
    timeout_seconds: float = Field(3.0, gt=0.0, le=60.0)

    class Config:
        extra = "forbid"

    @validator("fallback_policy")
    def _fallback_is_deterministic(cls, value):
        if value != PlannerPolicyKind.scripted:
            raise ValueError("fallback_policy must be scripted")
        return value

    @classmethod
    def from_env(cls) -> "PlannerPolicyConfig":
        return cls(
            active_policy=os.environ.get(
                "NOVELSIM_PLANNER_POLICY",
                PlannerPolicyKind.scripted.value,
            ).strip().lower(),
            fallback_policy=os.environ.get(
                "NOVELSIM_PLANNER_FALLBACK",
                PlannerPolicyKind.scripted.value,
            ).strip().lower(),
            timeout_seconds=float(
                os.environ.get("NOVELSIM_PLANNER_TIMEOUT_SECONDS", "3.0")
            ),
        )


class PlannerPolicyError(RuntimeError):
    pass


class ScriptedPolicy:
    """Deterministic expert/heuristic adapter."""

    def __init__(
        self,
        selector: DecisionGenerator,
        *,
        policy_id: str = PlannerPolicyKind.scripted.value,
    ) -> None:
        self.policy_id = policy_id
        self._selector = selector

    def decide(
        self,
        observation: GameObservation,
        available_tools: Sequence[ToolDefinition],
    ) -> PlannerDecision:
        return coerce_planner_decision(
            self._selector(observation, available_tools),
            observation=observation,
            policy_id=self.policy_id,
        )


class PromptedLLMPolicy:
    """Direct-prompt adapter with an injectable generator for tests/providers."""

    def __init__(
        self,
        generator: Optional[DecisionGenerator] = None,
        *,
        policy_id: str = PlannerPolicyKind.prompt.value,
        model: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
        request_timeout_seconds: float = 30.0,
    ) -> None:
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if not 0.0 <= temperature <= 2.0:
            raise ValueError("temperature must be in [0, 2]")
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        self.policy_id = policy_id
        self.model = model
        self.prompt_version = PLANNER_PROMPT_VERSION
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.request_timeout_seconds = request_timeout_seconds
        self._generator = generator or self._generate_openai_compatible

    def decide(
        self,
        observation: GameObservation,
        available_tools: Sequence[ToolDefinition],
    ) -> PlannerDecision:
        return coerce_planner_decision(
            self._generator(observation, available_tools),
            observation=observation,
            policy_id=self.policy_id,
        )

    def _generate_openai_compatible(
        self,
        observation: GameObservation,
        available_tools: Sequence[ToolDefinition],
    ) -> Mapping[str, Any]:
        cfg = get_llm_config()
        openai.api_key = cfg.api_key
        openai.api_base = cfg.base_url
        messages = planner_prompt_messages(observation)
        model = self.model or cfg.model
        response = call_openai_compatible(
            openai.ChatCompletion.create,
            operation="planner_policy",
            model=model,
            messages=messages,
            temperature=self.temperature,
            request_timeout=self.request_timeout_seconds,
            **chat_generation_options(
                model,
                max_tokens=self.max_tokens,
                thinking=False,
            ),
        )
        raw = response.choices[0].message.content.strip()
        parsed = _extract_json(raw)
        if parsed is None:
            raise PlannerPolicyError("planner response is not a JSON object")
        return parsed


class ReActPolicy(PromptedLLMPolicy):
    """Prompt policy that explicitly consumes structured execution feedback."""

    def __init__(
        self,
        generator: Optional[DecisionGenerator] = None,
        *,
        policy_id: str = PlannerPolicyKind.react.value,
        model: Optional[str] = None,
    ) -> None:
        super().__init__(generator, policy_id=policy_id, model=model)

    def replan(
        self,
        observation: GameObservation,
        available_tools: Sequence[ToolDefinition],
        feedback: PlannerFeedback,
    ) -> PlannerDecision:
        updated = observation.copy(update={"feedback": feedback}, deep=True)
        return self.decide(updated, available_tools)


class PlannerPolicyRouter:
    """Config-selectable policy runner with timeout and scripted fallback."""

    def __init__(
        self,
        policies: Mapping[Union[PlannerPolicyKind, str], PlannerPolicy],
        *,
        config: Optional[PlannerPolicyConfig] = None,
    ) -> None:
        self.config = config or PlannerPolicyConfig.from_env()
        self._policies = {
            PlannerPolicyKind(key): policy for key, policy in policies.items()
        }
        required = {self.config.active_policy, self.config.fallback_policy}
        missing = sorted(kind.value for kind in required if kind not in self._policies)
        if missing:
            raise ValueError("planner policies not registered: %s" % ", ".join(missing))
        self.policy_id = "router:%s" % self.config.active_policy.value
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="novelsim-planner",
        )

    def decide(
        self,
        observation: GameObservation,
        available_tools: Sequence[ToolDefinition],
    ) -> PlannerDecision:
        active = self._policies[self.config.active_policy]
        try:
            context = copy_context()
            future = self._executor.submit(
                context.run,
                active.decide,
                observation,
                available_tools,
            )
            decision = future.result(timeout=self.config.timeout_seconds)
            _validate_policy_result(decision, observation)
            return decision
        except FutureTimeout:
            future.cancel()
            self._replace_executor()
            return self._fallback(
                observation,
                available_tools,
                "timeout:%s" % self.config.active_policy.value,
            )
        except Exception as exc:
            return self._fallback(
                observation,
                available_tools,
                "%s:%s" % (type(exc).__name__, str(exc)[:200]),
            )

    def close(self) -> None:
        self._executor.shutdown(wait=False)

    def _replace_executor(self) -> None:
        previous = self._executor
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="novelsim-planner",
        )
        previous.shutdown(wait=False)

    def _fallback(
        self,
        observation: GameObservation,
        available_tools: Sequence[ToolDefinition],
        reason: str,
    ) -> PlannerDecision:
        fallback = self._policies[self.config.fallback_policy]
        decision = fallback.decide(observation, available_tools)
        _validate_policy_result(decision, observation)
        return decision.copy(
            update={
                "fallback_reason": reason,
                "metadata": {
                    **decision.metadata,
                    "requested_policy": self.config.active_policy.value,
                    "fallback_policy": self.config.fallback_policy.value,
                },
            },
            deep=True,
        )


class PlannerPolicySceneSelector:
    """Adapt a policy router to the existing SceneController free selector.

    Actor scheduling stays deterministic and outside the language model.  The
    selected actor receives an isolated observation; the returned ToolCall then
    follows the unchanged ToolRegistry/FSM/Gate execution path.
    """

    def __init__(
        self,
        router: PlannerPolicyRouter,
        registry,
        actor_selector: ActorSelector,
        *,
        world_package_id: str = "",
        scenario_family: str = "",
    ) -> None:
        self.router = router
        self.registry = registry
        self.actor_selector = actor_selector
        self.world_package_id = world_package_id
        self.scenario_family = scenario_family
        self.observations: List[GameObservation] = []
        self.decisions: List[PlannerDecision] = []

    def __call__(
        self,
        state: WorldState,
        turn_index: int,
    ) -> Optional[ToolCall]:
        actor_id = self.actor_selector(state.copy(deep=True), turn_index)
        if actor_id is None:
            return None
        observation = build_game_observation(
            state,
            actor_id,
            self.registry,
            world_package_id=self.world_package_id,
            scenario_family=self.scenario_family,
            metadata={"scene_turn": turn_index},
        )
        definitions = tuple(
            self.registry.get(name)
            for name in self.registry.names()
            if self.registry.get(name) is not None
        )
        decision = self.router.decide(observation, definitions)
        self.observations.append(observation)
        self.decisions.append(decision)
        return decision.tool_call


def coerce_planner_decision(
    value: PlannerValue,
    *,
    observation: GameObservation,
    policy_id: str,
) -> PlannerDecision:
    if value is None:
        return PlannerDecision(
            policy_id=policy_id,
            actor_id=observation.actor_id,
            intent=PlannerIntent.wait,
            confidence=1.0,
            reason_summary="policy selected no grounded action",
        )
    if isinstance(value, ToolCall):
        decision = PlannerDecision.from_tool_call(
            value,
            policy_id=policy_id,
        )
    elif isinstance(value, PlannerDecision):
        decision = value.copy(update={"policy_id": policy_id}, deep=True)
    else:
        payload = dict(value)
        payload["policy_id"] = policy_id
        payload.setdefault("actor_id", observation.actor_id)
        try:
            decision = PlannerDecision.parse_obj(payload)
        except ValidationError as exc:
            raise PlannerPolicyError("invalid PlannerDecision: %s" % exc) from exc
    _validate_policy_result(decision, observation)
    return decision


def tool_definitions_from_observation(
    observation: GameObservation,
    definitions: Sequence[ToolDefinition],
) -> Sequence[ToolDefinition]:
    """Keep only definitions advertised by the observation contract."""

    advertised = {tool.name for tool in observation.available_tools}
    return tuple(
        definition
        for definition in definitions
        if definition.name in advertised
    )


def _validate_policy_result(
    decision: PlannerDecision,
    observation: GameObservation,
) -> None:
    if not isinstance(decision, PlannerDecision):
        raise PlannerPolicyError("policy must return PlannerDecision")
    if decision.actor_id != observation.actor_id:
        raise PlannerPolicyError("policy changed observation actor_id")


def _extract_json(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        try:
            value = json.loads(match.group(1))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            pass
    return None
