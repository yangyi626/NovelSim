"""Real-LLM narrative-beat planning for executable multi-agent worlds.

This module deliberately sits above :mod:`engine.joint_plan`: the language
model proposes short actor-scoped tool chains, while the existing joint-plan
runtime validates, executes, monitors and repairs them.  The evaluation path
never falls back to a scripted policy.  A provider/configuration/schema error
therefore remains visible in the report instead of being disguised as a model
success.
"""

from __future__ import annotations

import hashlib
import json
from time import perf_counter
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence
from uuid import uuid4

import openai
from pydantic import BaseModel, Field, ValidationError, root_validator, validator

from world_schema import WorldState

from .agent_tools import ToolCall, ToolDefinition, ToolRegistry
from .config import get_llm_config
from .game_observation import GameObservation, PlannerFeedback, build_game_observation
from .joint_plan import (
    ActionStep,
    ActorActionChain,
    JointPlan,
    ReplanRequest,
    validate_joint_plan,
)
from .llm_telemetry import call_openai_compatible, chat_generation_options
from .planner_prompt import extract_json_object


NARRATIVE_PLANNER_PROMPT_VERSION = "novelsim_narrative_beat.v7"


class _StrictModel(BaseModel):
    class Config:
        extra = "forbid"


class PlannedToolStep(_StrictModel):
    """One model-proposed tool call before runtime IDs are attached."""

    step_id: str
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)

    @validator("step_id", "tool_name")
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("planner step identifiers cannot be blank")
        return value


class ActorNarrativePlan(_StrictModel):
    """Short plan generated from exactly one actor's private observation."""

    actor_id: str
    intent: str
    steps: List[PlannedToolStep] = Field(default_factory=list)
    stop_conditions: List[str] = Field(default_factory=list)

    @root_validator(skip_on_failure=True)
    def _unique_step_ids(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        actor_id = str(values.get("actor_id") or "").strip()
        if not actor_id:
            raise ValueError("actor_id cannot be blank")
        ids = [step.step_id for step in values.get("steps") or []]
        if len(ids) != len(set(ids)):
            raise ValueError("step_id must be unique in one actor plan")
        return values


class NarrativePlannerCallTrace(_StrictModel):
    """Auditable provider record without storing copyrighted prompt text."""

    actor_id: str
    attempt: int = Field(ge=1)
    operation: str
    model_id: str
    prompt_version: str = NARRATIVE_PLANNER_PROMPT_VERSION
    response_id: str = ""
    raw_response_sha256: str = ""
    latency_ms: float = Field(0.0, ge=0.0)
    success: bool
    error_type: str = ""
    fallback_used: bool = False


class NarrativePlannerError(RuntimeError):
    """A real planning request failed; callers must not hide it with a script."""


NarrativeResponseGenerator = Callable[
    [str, GameObservation, Sequence[ToolDefinition], str, Optional[PlannerFeedback]],
    Mapping[str, Any],
]


class RealLLMNarrativePlanner:
    """Generate and repair short multi-agent plans using real LLM calls.

    Every actor is called separately with :class:`GameObservation`, so another
    actor's beliefs, memories and private goals never enter its prompt.  A
    deterministic assembler only adds IDs and validates tool schemas; it does
    not invent fallback actions.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        model: Optional[str] = None,
        max_steps_per_actor: int = 3,
        max_attempts: int = 2,
        max_tokens: int = 900,
        temperature: float = 0.2,
        request_timeout_seconds: float = 60.0,
        generator: Optional[NarrativeResponseGenerator] = None,
        world_package_id: str = "",
        scenario_family: str = "",
    ) -> None:
        if not 1 <= max_steps_per_actor <= 6:
            raise ValueError("max_steps_per_actor must be in [1, 6]")
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be in [1, 3]")
        if max_tokens < 128:
            raise ValueError("max_tokens must be at least 128")
        if not 0.0 <= temperature <= 2.0:
            raise ValueError("temperature must be in [0, 2]")
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        self.registry = registry
        self.model = model
        self.max_steps_per_actor = max_steps_per_actor
        self.max_attempts = max_attempts
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.request_timeout_seconds = request_timeout_seconds
        self.generator = generator
        self.world_package_id = world_package_id
        self.scenario_family = scenario_family
        self.call_traces: List[NarrativePlannerCallTrace] = []

    def generate(
        self,
        state: WorldState,
        actor_ids: Sequence[str],
        *,
        beat_goal: str,
        goal_id: str,
        permissions_by_actor: Optional[Mapping[str, Iterable[str]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        feedback_by_actor: Optional[Mapping[str, PlannerFeedback]] = None,
        revision: int = 0,
        parent_plan_id: Optional[str] = None,
    ) -> JointPlan:
        """Generate one executable beat; an all-empty result is an error."""

        ordered_actor_ids = list(dict.fromkeys(str(item) for item in actor_ids))
        if not ordered_actor_ids:
            raise NarrativePlannerError("narrative beat has no actors")
        missing = [item for item in ordered_actor_ids if item not in state.characters]
        if missing:
            raise NarrativePlannerError(
                "narrative beat references missing actors: %s" % ", ".join(missing)
            )
        feedbacks = feedback_by_actor or {}
        plan_metadata = dict(metadata or {})
        chains: Dict[str, ActorActionChain] = {}
        actor_intents: Dict[str, str] = {}
        stop_conditions: Dict[str, List[str]] = {}
        for actor_id in ordered_actor_ids:
            runtime_context = dict(plan_metadata)
            remaining_by_actor = runtime_context.get("remaining_failed_steps")
            if isinstance(remaining_by_actor, Mapping):
                runtime_context["remaining_failed_steps"] = list(
                    remaining_by_actor.get(actor_id) or []
                )
            observation = build_game_observation(
                state,
                actor_id,
                self.registry,
                world_package_id=self.world_package_id,
                scenario_family=self.scenario_family,
                feedback=feedbacks.get(actor_id),
                metadata={
                    "beat_goal": beat_goal,
                    "participant_actor_ids": ordered_actor_ids,
                    "prompt_version": NARRATIVE_PLANNER_PROMPT_VERSION,
                    "runtime_context": runtime_context,
                },
            )
            definitions = self._definitions_for(observation)
            draft = self._generate_actor_plan(
                actor_id,
                observation,
                definitions,
                beat_goal,
                feedbacks.get(actor_id),
            )
            actor_intents[actor_id] = draft.intent
            stop_conditions[actor_id] = list(draft.stop_conditions)
            steps = []
            for index, proposed in enumerate(draft.steps):
                call = ToolCall(
                    call_id=uuid4().hex,
                    actor_id=actor_id,
                    tool_name=proposed.tool_name,
                    arguments=dict(proposed.arguments),
                )
                steps.append(
                    ActionStep(
                        step_id="%s_%02d_%s" % (
                            actor_id,
                            index + 1,
                            _safe_step_id(proposed.step_id),
                        ),
                        tool_call=call,
                    )
                )
            chains[actor_id] = ActorActionChain(actor_id=actor_id, steps=steps)
        if not any(chain.steps for chain in chains.values()):
            raise NarrativePlannerError("real LLM returned no executable actions")
        plan = JointPlan(
            plan_id=uuid4().hex,
            goal_id=goal_id,
            base_world_version=state.version,
            actor_chains=chains,
            revision=revision,
            parent_plan_id=parent_plan_id,
            metadata={
                "source": "real_llm_actor_scoped_narrative_planner",
                "prompt_version": NARRATIVE_PLANNER_PROMPT_VERSION,
                "beat_goal": beat_goal,
                "actor_intents": actor_intents,
                "stop_conditions": stop_conditions,
                "fallback_used": False,
                **plan_metadata,
            },
        )
        validate_joint_plan(
            plan,
            state,
            self.registry,
            permissions_by_actor=permissions_by_actor,
            enforce_shared_scope=False,
        )
        return plan

    def replan(
        self,
        request: ReplanRequest,
        state: WorldState,
        *,
        beat_goal: str,
        permissions_by_actor: Optional[Mapping[str, Iterable[str]]] = None,
    ) -> JointPlan:
        feedback = {
            actor_id: PlannerFeedback(
                success=False,
                failure_code=(
                    request.failure_codes[0] if request.failure_codes else None
                ),
                summary="; ".join(request.failure_codes),
                retryable=True,
            )
            for actor_id in request.affected_actor_ids
        }
        remaining = {
            actor_id: [
                step.dict()
                for step in request.remaining_chains.get(
                    actor_id,
                    ActorActionChain(actor_id=actor_id, steps=[]),
                ).steps
            ]
            for actor_id in request.affected_actor_ids
        }
        return self.generate(
            state,
            request.affected_actor_ids,
            beat_goal=beat_goal,
            goal_id=request.goal_id,
            permissions_by_actor=permissions_by_actor,
            metadata={
                "replan_of": request.original_plan_id,
                "failure_codes": list(request.failure_codes),
                "remaining_failed_steps": remaining,
            },
            feedback_by_actor=feedback,
            revision=request.revision + 1,
            parent_plan_id=request.original_plan_id,
        )

    def _definitions_for(
        self,
        observation: GameObservation,
    ) -> List[ToolDefinition]:
        allowed = {item.name for item in observation.available_tools}
        return [
            definition
            for name in self.registry.names()
            for definition in [self.registry.get(name)]
            if definition is not None and name in allowed
        ]

    def _generate_actor_plan(
        self,
        actor_id: str,
        observation: GameObservation,
        definitions: Sequence[ToolDefinition],
        beat_goal: str,
        feedback: Optional[PlannerFeedback],
    ) -> ActorNarrativePlan:
        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            started = perf_counter()
            try:
                if self.generator is not None:
                    payload = self.generator(
                        actor_id,
                        observation,
                        definitions,
                        beat_goal,
                        feedback,
                    )
                    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                    response_id = "injected-generator"
                    model_id = self.model or "injected-generator"
                else:
                    payload, raw, response_id, model_id = self._call_provider(
                        actor_id,
                        observation,
                        definitions,
                        beat_goal,
                        feedback,
                        validation_error=last_error,
                    )
                draft = ActorNarrativePlan.parse_obj(payload)
                if draft.actor_id != actor_id:
                    raise ValueError("model changed actor_id")
                if len(draft.steps) > self.max_steps_per_actor:
                    raise ValueError(
                        "model returned %d steps; limit is %d"
                        % (len(draft.steps), self.max_steps_per_actor)
                    )
                names = {definition.name for definition in definitions}
                unknown = [
                    step.tool_name for step in draft.steps if step.tool_name not in names
                ]
                if unknown:
                    raise ValueError("unknown tools: %s" % ", ".join(unknown))
                definition_by_name = {
                    definition.name: definition for definition in definitions
                }
                for step in draft.steps:
                    arguments_model = definition_by_name[step.tool_name].arguments_model
                    model_validate = getattr(arguments_model, "model_validate", None)
                    if callable(model_validate):
                        model_validate(step.arguments)
                    else:
                        arguments_model.parse_obj(step.arguments)
                if self.generator is None:
                    _reject_stagnant_dialogue_loop(draft, observation)
                self.call_traces.append(
                    NarrativePlannerCallTrace(
                        actor_id=actor_id,
                        attempt=attempt,
                        operation="narrative_plan",
                        model_id=model_id,
                        response_id=response_id,
                        raw_response_sha256=_sha256(raw),
                        latency_ms=_elapsed_ms(started),
                        success=True,
                    )
                )
                return draft
            except Exception as exc:
                last_error = "%s: %s" % (type(exc).__name__, str(exc)[:400])
                self.call_traces.append(
                    NarrativePlannerCallTrace(
                        actor_id=actor_id,
                        attempt=attempt,
                        operation="narrative_plan",
                        model_id=self.model or _configured_model_or_unknown(),
                        latency_ms=_elapsed_ms(started),
                        success=False,
                        error_type=type(exc).__name__,
                    )
                )
        raise NarrativePlannerError(
            "real LLM narrative planning failed for %s after %d attempts: %s"
            % (actor_id, self.max_attempts, last_error)
        )

    def _call_provider(
        self,
        actor_id: str,
        observation: GameObservation,
        definitions: Sequence[ToolDefinition],
        beat_goal: str,
        feedback: Optional[PlannerFeedback],
        *,
        validation_error: str,
    ) -> tuple[Mapping[str, Any], str, str, str]:
        cfg = get_llm_config()
        openai.api_key = cfg.api_key
        openai.api_base = cfg.base_url
        model = self.model or cfg.model
        messages = _actor_plan_messages(
            actor_id,
            observation,
            definitions,
            beat_goal,
            feedback,
            max_steps=self.max_steps_per_actor,
            validation_error=validation_error,
        )
        response = call_openai_compatible(
            openai.ChatCompletion.create,
            operation="narrative_joint_plan",
            model=model,
            messages=messages,
            temperature=self.temperature,
            request_timeout=self.request_timeout_seconds,
            response_format={"type": "json_object"},
            **chat_generation_options(
                model,
                max_tokens=self.max_tokens,
                thinking=False,
            ),
        )
        raw = str(response.choices[0].message.content or "").strip()
        payload = extract_json_object(raw)
        if payload is None:
            raise ValueError("provider response is not a JSON object")
        response_id = str(
            getattr(response, "id", "")
            or getattr(response, "request_id", "")
            or ""
        )
        model_id = str(getattr(response, "model", "") or model)
        return payload, raw, response_id, model_id


def _actor_plan_messages(
    actor_id: str,
    observation: GameObservation,
    definitions: Sequence[ToolDefinition],
    beat_goal: str,
    feedback: Optional[PlannerFeedback],
    *,
    max_steps: int,
    validation_error: str,
) -> List[Dict[str, str]]:
    tools = [
        {
            "name": definition.name,
            "description": definition.description,
            "parameters": definition.parameters_schema(),
        }
        for definition in definitions
    ]
    system = (
        "你是小说世界中的角色规划器，只为指定角色生成短期可执行动作链。"
        "你只能使用角色私有观察中出现的信息、实体ID和工具；不得使用其他角色"
        "的秘密，不得预测或复述未提供的原著未来，不得直接修改世界状态。"
        "每一步只能调用一个给定工具。若前一步移动后才能交流，应按顺序写入动作链。"
        "收到执行失败反馈时，应先修复失败的前置条件，再继续运行时上下文中仍然有效"
        "的剩余动作意图，不得只处理移动而遗忘原目标。"
        "只输出一个JSON对象，不要Markdown、解释或额外字段。"
    )
    contract = {
        "actor_id": actor_id,
        "intent": "该角色基于自身目标的本轮意图",
        "steps": [
            {
                "step_id": "简短且唯一的英文或拼音ID",
                "tool_name": "给定工具名",
                "arguments": {"严格符合对应工具JSON Schema": ""},
            }
        ],
        "stop_conditions": ["何时结束本轮短计划"],
    }
    user_payload = {
        "prompt_version": NARRATIVE_PLANNER_PROMPT_VERSION,
        "beat_goal": beat_goal,
        "max_steps": max_steps,
        "ability_instruction": (
            "actor_observation.available_abilities contains only abilities whose "
            "authoritative preconditions are currently satisfied. When one "
            "directly advances the actor's active goal, call invoke_ability with "
            "that ability_id instead of replacing it with ordinary movement. "
            "If the affected character is already co-located and the active goal "
            "requires an explanation, warning, promise or command, put talk_to "
            "before invoke_ability in the same chain. Do not repeat an interaction "
            "that has already produced no new progress."
        ),
        "actor_observation": observation.dict(),
        "available_tools": tools,
        "execution_feedback": feedback.dict() if feedback is not None else None,
        "previous_validation_error": validation_error or None,
        "required_output": contract,
    }
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
        },
    ]


def _reject_stagnant_dialogue_loop(
    draft: ActorNarrativePlan,
    observation: GameObservation,
    *,
    repeat_limit: int = 2,
) -> None:
    """Reject a pure dialogue plan that repeats a recent no-progress loop."""

    if not draft.steps or any(step.tool_name != "talk_to" for step in draft.steps):
        return
    runtime_context = observation.metadata.get("runtime_context", {})
    recent = runtime_context.get("recent_committed_events", [])
    if not isinstance(recent, list):
        return
    location_id = observation.actor_location_id
    for step in draft.steps:
        target_id = str(step.arguments.get("target_character_id") or "")
        repeats = 0
        for event in recent:
            if not isinstance(event, Mapping):
                continue
            if event.get("event_type") != "tool.talk_to":
                continue
            actors = {str(value) for value in event.get("actor_ids", [])}
            targets = {str(value) for value in event.get("target_ids", [])}
            if (
                observation.actor_id in actors
                and target_id in targets
                and (not location_id or location_id in targets)
            ):
                repeats += 1
        if repeats < repeat_limit:
            return
    raise ValueError(
        "stagnant dialogue loop: choose a state-progressing action or a new target"
    )


def _safe_step_id(value: str) -> str:
    cleaned = "".join(
        char if char.isalnum() or char in {"_", "-"} else "_" for char in value
    ).strip("_")
    return cleaned[:64] or "step"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000.0, 3)


def _configured_model_or_unknown() -> str:
    try:
        return get_llm_config().model
    except Exception:
        return "unknown"
