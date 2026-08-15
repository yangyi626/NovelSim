"""受控 Agent 工具协议与第一批确定性工具。

LLM 只能生成 :class:`ToolCall`。注册表负责参数 Schema、实体、权限和前置
条件校验；工具处理器只在世界快照副本上生成候选 :class:`StatePatch`，不能
直接修改权威状态。候选 Patch 的校验与提交由 Agent 状态机完成。
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import inspect
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Optional,
    Tuple,
    Type,
    Union,
)
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError, constr, root_validator

from world_schema import (
    Belief,
    BeliefEvidence,
    Operation,
    OperationKind,
    StatePatch,
    WorldState,
)

from .alliance_rules import build_alliance_patch
from .information_propagation import PropagationError, build_propagation_patch


class ToolFailureCode(str, Enum):
    unknown_tool = "unknown_tool"
    invalid_arguments = "invalid_arguments"
    actor_not_found = "actor_not_found"
    actor_dead = "actor_dead"
    target_not_found = "target_not_found"
    permission_denied = "permission_denied"
    precondition_failed = "precondition_failed"
    spatial_constraint = "spatial_constraint"
    cognitive_boundary = "cognitive_boundary"
    patch_rejected = "patch_rejected"
    version_conflict = "version_conflict"
    timeout = "timeout"
    execution_error = "execution_error"
    retry_exhausted = "retry_exhausted"


class ToolFailure(BaseModel):
    """可聚合、可评测的结构化失败。"""

    code: ToolFailureCode
    message: str
    stage: str
    retryable: bool = False
    details: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"


class ToolCall(BaseModel):
    """模型或规划器给出的高层候选工具调用。"""

    call_id: str = Field(default_factory=lambda: uuid4().hex)
    actor_id: str
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    parent_trace_id: Optional[str] = None

    class Config:
        extra = "forbid"


class PresentationEvent(BaseModel):
    """供 Unity/Web 消费的非权威表现事件。"""

    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"


class ToolCandidate(BaseModel):
    """工具处理器返回的候选效果，尚未提交。"""

    patch: StatePatch = Field(default_factory=StatePatch)
    output: Dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    target_ids: List[str] = Field(default_factory=list)
    presentation_events: List[PresentationEvent] = Field(default_factory=list)

    class Config:
        extra = "forbid"


class ToolResult(BaseModel):
    """一次工具调用的统一结果协议。"""

    call_id: str
    tool_name: str
    success: bool
    output: Dict[str, Any] = Field(default_factory=dict)
    failure: Optional[ToolFailure] = None
    latency_ms: float = 0.0
    candidate_patch: Optional[StatePatch] = None
    presentation_events: List[PresentationEvent] = Field(default_factory=list)
    committed_event_id: Optional[str] = None
    world_version: Optional[int] = None
    retry_count: int = 0
    replan_required: bool = False

    class Config:
        extra = "forbid"

    @root_validator(skip_on_failure=True)
    def _failure_matches_success(cls, values):
        success = values.get("success")
        failure = values.get("failure")
        if success and failure is not None:
            raise ValueError("successful ToolResult cannot contain failure")
        if success is False and failure is None:
            raise ValueError("failed ToolResult must contain failure")
        return values


class ToolExecutionError(RuntimeError):
    """工具校验或执行的预期失败。"""

    def __init__(self, failure: ToolFailure):
        super().__init__(failure.message)
        self.failure = failure


class StrictArguments(BaseModel):
    """工具参数基类：拒绝模型夹带未声明字段。"""

    class Config:
        extra = "forbid"
        anystr_strip_whitespace = True


NonEmptyId = constr(strip_whitespace=True, min_length=1, max_length=128)
DialogueText = constr(strip_whitespace=True, min_length=1, max_length=500)
ToneText = constr(strip_whitespace=True, min_length=1, max_length=64)


class MoveToArguments(StrictArguments):
    destination_id: NonEmptyId


class TalkToArguments(StrictArguments):
    target_character_id: NonEmptyId
    message: DialogueText
    tone: ToneText


class PickUpArguments(StrictArguments):
    item_id: NonEmptyId


class ShareInformationArguments(StrictArguments):
    target_character_id: NonEmptyId
    fact_id: NonEmptyId


class ObserveArguments(StrictArguments):
    fact_id: NonEmptyId


class GiveItemArguments(StrictArguments):
    item_id: NonEmptyId
    target_character_id: NonEmptyId


class TakeItemArguments(StrictArguments):
    item_id: NonEmptyId
    target_character_id: NonEmptyId


class InvokeAbilityArguments(StrictArguments):
    ability_id: NonEmptyId


class DestroyItemArguments(StrictArguments):
    item_id: NonEmptyId


class ProposeAllianceArguments(StrictArguments):
    target_character_id: NonEmptyId
    goal_key: NonEmptyId
    shared_fact_id: NonEmptyId


@dataclass(frozen=True)
class ToolContext:
    """只读语义的工具上下文。

    registry 会传入权威状态的深拷贝，即使自定义处理器误改 ``state``，也不会
    污染调用方持有的权威快照。
    """

    state: WorldState
    actor_id: str
    permissions: FrozenSet[str] = frozenset()
    metadata: Dict[str, Any] = field(default_factory=dict)


ToolHandler = Callable[
    [ToolContext, BaseModel],
    Union[ToolCandidate, Awaitable[ToolCandidate]],
]


@dataclass(frozen=True)
class ToolDefinition:
    """工具的 Schema、权限和确定性处理器。"""

    name: str
    description: str
    arguments_model: Type[BaseModel]
    handler: ToolHandler
    required_permissions: FrozenSet[str] = frozenset()
    allowed_patch_operations: FrozenSet[OperationKind] = frozenset()
    requires_navigation_state: bool = False
    timeout_seconds: float = 2.0

    def parameters_schema(self) -> Dict[str, Any]:
        """返回 provider-neutral JSON Schema，兼容 Pydantic 1/2。"""

        model_json_schema = getattr(self.arguments_model, "model_json_schema", None)
        if callable(model_json_schema):
            schema = model_json_schema()
        else:
            schema = self.arguments_model.schema()
        return dict(schema)

    def as_function_tool(self) -> Dict[str, Any]:
        """导出 OpenAI/主流 function calling 可直接使用的工具描述。"""

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema(),
                "strict": True,
            },
        }


@dataclass(frozen=True)
class PreparedToolCall:
    definition: ToolDefinition
    arguments: BaseModel


_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ToolRegistry:
    """工具注册、发现、Schema 校验和受控调用入口。"""

    def __init__(self) -> None:
        self._definitions: Dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if not _TOOL_NAME.fullmatch(definition.name):
            raise ValueError(
                "tool name must be 1-64 characters: letters, digits, '_' or '-'"
            )
        if definition.name in self._definitions:
            raise ValueError(f"duplicate tool: {definition.name}")
        if definition.timeout_seconds <= 0:
            raise ValueError("tool timeout_seconds must be positive")
        self._definitions[definition.name] = definition

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._definitions.get(name)

    def names(self) -> List[str]:
        return sorted(self._definitions)

    def function_tools(self) -> List[Dict[str, Any]]:
        return [
            self._definitions[name].as_function_tool()
            for name in self.names()
        ]

    def prepare(
        self,
        call: ToolCall,
        state: WorldState,
        *,
        permissions: Iterable[str] = (),
    ) -> PreparedToolCall:
        """依次完成工具、参数、调用者和权限校验，不修改状态。"""

        definition = self.get(call.tool_name)
        if definition is None:
            raise _tool_error(
                ToolFailureCode.unknown_tool,
                f"unknown tool: {call.tool_name}",
                stage="validate_tool",
                details={"tool_name": call.tool_name},
            )

        try:
            model_validate = getattr(
                definition.arguments_model,
                "model_validate",
                None,
            )
            if callable(model_validate):
                arguments = model_validate(call.arguments)
            else:
                arguments = definition.arguments_model.parse_obj(call.arguments)
        except ValidationError as exc:
            raise _tool_error(
                ToolFailureCode.invalid_arguments,
                f"invalid arguments for {call.tool_name}",
                stage="validate_tool",
                details={"errors": exc.errors()},
            ) from exc

        actor = state.characters.get(call.actor_id)
        if actor is None:
            raise _tool_error(
                ToolFailureCode.actor_not_found,
                f"unknown actor: {call.actor_id}",
                stage="validate_tool",
                details={"actor_id": call.actor_id},
            )
        if not actor.is_alive:
            raise _tool_error(
                ToolFailureCode.actor_dead,
                f"dead actor cannot use tools: {call.actor_id}",
                stage="validate_tool",
                details={"actor_id": call.actor_id},
            )

        granted = frozenset(permissions)
        missing = sorted(definition.required_permissions - granted)
        if missing:
            raise _tool_error(
                ToolFailureCode.permission_denied,
                f"missing permissions for {call.tool_name}: {', '.join(missing)}",
                stage="validate_tool",
                details={"missing_permissions": missing},
            )
        return PreparedToolCall(definition=definition, arguments=arguments)

    async def invoke(
        self,
        prepared: PreparedToolCall,
        call: ToolCall,
        state: WorldState,
        *,
        permissions: Iterable[str] = (),
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ToolCandidate:
        """在状态副本上调用处理器；同步处理器在线程池运行。"""

        context = ToolContext(
            state=state.copy(deep=True),
            actor_id=call.actor_id,
            permissions=frozenset(permissions),
            metadata=dict(metadata or {}),
        )
        handler = prepared.definition.handler
        if inspect.iscoroutinefunction(handler):
            result = await handler(context, prepared.arguments)
        else:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                functools.partial(handler, context, prepared.arguments),
            )
            if inspect.isawaitable(result):
                result = await result
        if not isinstance(result, ToolCandidate):
            raise TypeError(
                f"tool {prepared.definition.name} returned "
                f"{type(result).__name__}, expected ToolCandidate"
            )
        return result


def _tool_error(
    code: ToolFailureCode,
    message: str,
    *,
    stage: str,
    retryable: bool = False,
    details: Optional[Dict[str, Any]] = None,
) -> ToolExecutionError:
    return ToolExecutionError(
        ToolFailure(
            code=code,
            message=message,
            stage=stage,
            retryable=retryable,
            details=dict(details or {}),
        )
    )


def _character(context: ToolContext, character_id: str):
    character = context.state.characters.get(character_id)
    if character is None:
        raise _tool_error(
            ToolFailureCode.target_not_found,
            f"unknown character: {character_id}",
            stage="execute_tool",
            details={"target_id": character_id},
        )
    if not character.is_alive:
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"target character is dead: {character_id}",
            stage="execute_tool",
            details={"target_id": character_id},
        )
    return character


def _require_distinct_target(context: ToolContext, target_id: str):
    if target_id == context.actor_id:
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            "actor cannot target itself",
            stage="execute_tool",
            details={"target_id": target_id},
        )
    return _character(context, target_id)


def _require_same_location(context: ToolContext, target_id: str) -> None:
    actor = context.state.characters[context.actor_id]
    target = context.state.characters[target_id]
    if (
        actor.location_id is None
        or target.location_id is None
        or actor.location_id != target.location_id
    ):
        raise _tool_error(
            ToolFailureCode.spatial_constraint,
            f"{context.actor_id} and {target_id} are not co-located",
            stage="execute_tool",
            details={
                "actor_location_id": actor.location_id,
                "target_location_id": target.location_id,
            },
        )


def _move_to(context: ToolContext, raw: BaseModel) -> ToolCandidate:
    args = raw  # type: MoveToArguments
    actor = context.state.characters[context.actor_id]
    destination_id = args.destination_id

    if destination_id in context.state.locations:
        location_id = destination_id
    elif destination_id in context.state.characters:
        target = _character(context, destination_id)
        location_id = target.location_id
        if location_id is None:
            raise _tool_error(
                ToolFailureCode.spatial_constraint,
                f"target has no current location: {destination_id}",
                stage="execute_tool",
                details={"target_id": destination_id},
            )
    else:
        raise _tool_error(
            ToolFailureCode.target_not_found,
            f"unknown destination: {destination_id}",
            stage="execute_tool",
            details={"destination_id": destination_id},
        )

    location = context.state.locations.get(location_id)
    if location is None:
        raise _tool_error(
            ToolFailureCode.target_not_found,
            f"unknown location: {location_id}",
            stage="execute_tool",
            details={"location_id": location_id},
        )
    if not location.accessible:
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"location is inaccessible: {location_id}",
            stage="execute_tool",
            details={"location_id": location_id},
        )
    current_location = context.state.locations.get(actor.location_id or "")
    if (
        current_location is not None
        and bool(getattr(current_location, "blocks_ordinary_exit", False))
        and actor.location_id != location_id
    ):
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"ordinary movement cannot exit location: {actor.location_id}",
            stage="execute_tool",
            details={"location_id": actor.location_id},
        )
    required_flag = str(getattr(location, "requires_flag", "") or "")
    if required_flag and not context.state.flags.get(required_flag):
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"location entry condition is not satisfied: {location_id}",
            stage="execute_tool",
            details={
                "location_id": location_id,
                "required_flag": required_flag,
            },
        )
    missing_tags = sorted(
        set(location.requires_permission) - set(actor.identity_tags)
    )
    if missing_tags:
        raise _tool_error(
            ToolFailureCode.permission_denied,
            f"actor cannot enter {location_id}",
            stage="execute_tool",
            details={"missing_identity_tags": missing_tags},
        )
    if actor.location_id == location_id:
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"actor is already at {location_id}",
            stage="execute_tool",
            details={"location_id": location_id},
        )

    patch = StatePatch(
        operations=[
            Operation(
                op=OperationKind.move_character,
                target_id=context.actor_id,
                location_id=location_id,
                reason=f"move_to:{destination_id}",
            )
        ],
        notes=f"{context.actor_id} moves to {location_id}",
    )
    return ToolCandidate(
        patch=patch,
        output={
            "destination_id": destination_id,
            "location_id": location_id,
        },
        summary=f"{context.actor_id} moved to {location_id}",
        target_ids=list(dict.fromkeys([destination_id, location_id])),
        presentation_events=[
            PresentationEvent(
                event_type="navigate",
                payload={
                    "character_id": context.actor_id,
                    "destination_id": destination_id,
                    "location_id": location_id,
                },
            )
        ],
    )


def _talk_to(context: ToolContext, raw: BaseModel) -> ToolCandidate:
    args = raw  # type: TalkToArguments
    target = _require_distinct_target(context, args.target_character_id)
    _require_same_location(context, target.character_id)
    return ToolCandidate(
        output={
            "speaker_id": context.actor_id,
            "target_character_id": target.character_id,
            "message": args.message,
            "tone": args.tone,
        },
        summary=f"{context.actor_id} talked to {target.character_id}",
        target_ids=[
            value
            for value in [target.character_id, context.state.characters[context.actor_id].location_id]
            if value
        ],
        presentation_events=[
            PresentationEvent(
                event_type="dialogue",
                payload={
                    "speaker_id": context.actor_id,
                    "to_id": target.character_id,
                    "line": args.message,
                    "tone": args.tone,
                },
            )
        ],
    )


def _pick_up(context: ToolContext, raw: BaseModel) -> ToolCandidate:
    args = raw  # type: PickUpArguments
    actor = context.state.characters[context.actor_id]
    item = context.state.items.get(args.item_id)
    if item is None:
        raise _tool_error(
            ToolFailureCode.target_not_found,
            f"unknown item: {args.item_id}",
            stage="execute_tool",
            details={"item_id": args.item_id},
        )
    if item.owner_id is not None:
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"item is already owned: {args.item_id}",
            stage="execute_tool",
            details={"owner_id": item.owner_id},
        )
    if not item.accessible or item.quantity <= 0:
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"item is not available: {args.item_id}",
            stage="execute_tool",
            details={
                "accessible": item.accessible,
                "quantity": item.quantity,
            },
        )
    if actor.location_id is None or item.location_id != actor.location_id:
        raise _tool_error(
            ToolFailureCode.spatial_constraint,
            f"item is not at actor location: {args.item_id}",
            stage="execute_tool",
            details={
                "actor_location_id": actor.location_id,
                "item_location_id": item.location_id,
            },
        )

    patch = StatePatch(
        operations=[
            Operation(
                op=OperationKind.transfer_item,
                item_id=item.item_id,
                target_id=context.actor_id,
                reason=f"pick_up:{item.item_id}",
            )
        ],
        notes=f"{context.actor_id} picks up {item.item_id}",
    )
    return ToolCandidate(
        patch=patch,
        output={"item_id": item.item_id, "owner_id": context.actor_id},
        summary=f"{context.actor_id} picked up {item.item_id}",
        target_ids=[item.item_id],
        presentation_events=[
            PresentationEvent(
                event_type="item_picked_up",
                payload={
                    "character_id": context.actor_id,
                    "item_id": item.item_id,
                },
            )
        ],
    )


def _observe(context: ToolContext, raw: BaseModel) -> ToolCandidate:
    args = raw  # type: ObserveArguments
    actor = context.state.characters[context.actor_id]
    fact = context.state.facts.get(args.fact_id)
    if fact is None:
        raise _tool_error(
            ToolFailureCode.target_not_found,
            f"unknown fact: {args.fact_id}",
            stage="execute_tool",
            details={"fact_id": args.fact_id},
        )
    if not fact.observable:
        raise _tool_error(
            ToolFailureCode.cognitive_boundary,
            f"fact is not directly observable: {args.fact_id}",
            stage="execute_tool",
            details={"fact_id": args.fact_id},
        )
    if fact.location_id and actor.location_id != fact.location_id:
        raise _tool_error(
            ToolFailureCode.spatial_constraint,
            f"fact is not observable at actor location: {args.fact_id}",
            stage="execute_tool",
            details={
                "actor_location_id": actor.location_id,
                "fact_location_id": fact.location_id,
            },
        )
    if fact.item_id:
        item = context.state.items.get(fact.item_id)
        if item is None:
            raise _tool_error(
                ToolFailureCode.target_not_found,
                f"fact references unknown item: {fact.item_id}",
                stage="execute_tool",
                details={"item_id": fact.item_id},
            )
        actor_can_access_item = (
            item.owner_id == context.actor_id
            or (
                item.owner_id is None
                and item.accessible
                and item.quantity > 0
                and item.location_id == actor.location_id
            )
        )
        if not actor_can_access_item:
            raise _tool_error(
                ToolFailureCode.spatial_constraint,
                f"evidence item is not accessible: {fact.item_id}",
                stage="execute_tool",
                details={
                    "item_id": fact.item_id,
                    "owner_id": item.owner_id,
                    "item_location_id": item.location_id,
                },
            )

    existing = next(
        (
            value
            for value in context.state.beliefs.get(context.actor_id, [])
            if value.fact_id == args.fact_id
        ),
        None,
    )
    if (
        existing is not None
        and existing.source_type == "observation"
        and existing.belief == fact.truth
        and existing.confidence >= fact.base_confidence
    ):
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"fact was already observed: {args.fact_id}",
            stage="execute_tool",
            details={"fact_id": args.fact_id},
        )

    event_id = str(
        context.metadata.get("tool_call_id")
        or f"observe_{context.actor_id}_{args.fact_id}"
    )
    digest = hashlib.sha256(
        (
            f"{context.state.timeline_id}|{context.state.version}|"
            f"{context.actor_id}|{args.fact_id}|{event_id}"
        ).encode("utf-8")
    ).hexdigest()[:12]
    evidence_id = f"evidence_{digest}"
    evidence = BeliefEvidence(
        evidence_id=evidence_id,
        fact_id=args.fact_id,
        holder_id=context.actor_id,
        source_type="observation",
        source_event_id=event_id,
        reliability=fact.base_confidence,
    )
    patch = StatePatch(
        operations=[
            Operation(
                op=OperationKind.update_belief,
                target_id=context.actor_id,
                fact_id=args.fact_id,
                belief=fact.truth,
                confidence=fact.base_confidence,
                source_type="observation",
                source_event_id=event_id,
                evidence_event_ids=[event_id],
                valid_from=fact.valid_from,
                valid_to=fact.valid_to,
                reason=f"direct observation of {args.fact_id}",
            ),
            Operation(
                op=OperationKind.record_evidence,
                evidence_id=evidence_id,
                value=evidence.dict(),
                reason=f"observation evidence for {args.fact_id}",
            ),
        ],
        notes=f"{context.actor_id} observes {args.fact_id}",
    )
    return ToolCandidate(
        patch=patch,
        output={
            "character_id": context.actor_id,
            "fact_id": args.fact_id,
            "belief": fact.truth.value,
            "confidence": fact.base_confidence,
            "evidence_id": evidence_id,
        },
        summary=f"{context.actor_id} observed {args.fact_id}",
        target_ids=[args.fact_id],
        presentation_events=[
            PresentationEvent(
                event_type="fact_observed",
                payload={
                    "character_id": context.actor_id,
                    "fact_id": args.fact_id,
                    "evidence_id": evidence_id,
                },
            )
        ],
    )


def _give_item(context: ToolContext, raw: BaseModel) -> ToolCandidate:
    args = raw  # type: GiveItemArguments
    target = _require_distinct_target(context, args.target_character_id)
    _require_same_location(context, target.character_id)
    item = context.state.items.get(args.item_id)
    if item is None:
        raise _tool_error(
            ToolFailureCode.target_not_found,
            f"unknown item: {args.item_id}",
            stage="execute_tool",
            details={"item_id": args.item_id},
        )
    if item.owner_id != context.actor_id:
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"actor does not own item: {args.item_id}",
            stage="execute_tool",
            details={"owner_id": item.owner_id},
        )
    if not item.accessible or item.quantity <= 0:
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"item is not transferable: {args.item_id}",
            stage="execute_tool",
            details={
                "accessible": item.accessible,
                "quantity": item.quantity,
            },
        )

    patch = StatePatch(
        operations=[
            Operation(
                op=OperationKind.transfer_item,
                item_id=item.item_id,
                target_id=target.character_id,
                reason=f"given by {context.actor_id}",
            )
        ],
        notes=(
            f"{context.actor_id} gives {item.item_id} "
            f"to {target.character_id}"
        ),
    )
    return ToolCandidate(
        patch=patch,
        output={
            "item_id": item.item_id,
            "source_character_id": context.actor_id,
            "target_character_id": target.character_id,
        },
        summary=(
            f"{context.actor_id} gave {item.item_id} "
            f"to {target.character_id}"
        ),
        target_ids=[item.item_id, target.character_id],
        presentation_events=[
            PresentationEvent(
                event_type="item_given",
                payload={
                    "item_id": item.item_id,
                    "from_id": context.actor_id,
                    "to_id": target.character_id,
                },
            )
        ],
    )


def _take_item(context: ToolContext, raw: BaseModel) -> ToolCandidate:
    """Transfer an explicitly transferable item from a co-located character.

    This models game actions such as confiscating or forcibly taking an item.
    The model cannot choose the resulting patch: ownership, co-location and the
    entity affordance are checked deterministically before one fixed transfer
    operation is proposed.
    """

    args = raw  # type: TakeItemArguments
    target = _require_distinct_target(context, args.target_character_id)
    _require_same_location(context, target.character_id)
    item = context.state.items.get(args.item_id)
    if item is None:
        raise _tool_error(
            ToolFailureCode.target_not_found,
            f"unknown item: {args.item_id}",
            stage="execute_tool",
            details={"item_id": args.item_id},
        )
    if item.owner_id != target.character_id:
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"target does not own item: {args.item_id}",
            stage="execute_tool",
            details={
                "owner_id": item.owner_id,
                "target_character_id": target.character_id,
            },
        )
    if not item.accessible or item.quantity <= 0:
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"item is not transferable: {args.item_id}",
            stage="execute_tool",
            details={
                "accessible": item.accessible,
                "quantity": item.quantity,
            },
        )
    affordances = context.state.entity_affordances.get(item.item_id, [])
    if not any(
        affordance.enabled
        and affordance.action_type in {"take_item", "swap_object"}
        for affordance in affordances
    ):
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"item does not allow taking: {args.item_id}",
            stage="execute_tool",
            details={"item_id": args.item_id},
        )
    patch = StatePatch(
        operations=[
            Operation(
                op=OperationKind.transfer_item,
                item_id=item.item_id,
                target_id=context.actor_id,
                reason=(
                    f"taken by {context.actor_id} from {target.character_id}"
                ),
            )
        ],
        notes=(
            f"{context.actor_id} takes {item.item_id} "
            f"from {target.character_id}"
        ),
    )
    return ToolCandidate(
        patch=patch,
        output={
            "item_id": item.item_id,
            "source_character_id": target.character_id,
            "target_character_id": context.actor_id,
        },
        summary=(
            f"{context.actor_id} took {item.item_id} "
            f"from {target.character_id}"
        ),
        target_ids=[item.item_id, target.character_id],
        presentation_events=[
            PresentationEvent(
                event_type="item_taken",
                payload={
                    "item_id": item.item_id,
                    "from_id": target.character_id,
                    "to_id": context.actor_id,
                },
            )
        ],
    )


def _invoke_ability(context: ToolContext, raw: BaseModel) -> ToolCandidate:
    """Invoke one trusted, data-driven world ability.

    The LLM only selects an enabled ``ability_id``.  The target, legal source
    locations and deterministic effects live in the authoritative world
    package under ``runtime.ability_specs``; model output can never supply a
    destination or a state patch.
    """

    args = raw  # type: InvokeAbilityArguments
    capabilities = {
        capability.capability_id
        for capability in context.state.character_capabilities.get(
            context.actor_id, []
        )
        if capability.enabled
    }
    if args.ability_id not in capabilities:
        raise _tool_error(
            ToolFailureCode.permission_denied,
            f"actor lacks enabled ability: {args.ability_id}",
            stage="execute_tool",
            details={"ability_id": args.ability_id},
        )

    specs = context.state.flags.get("runtime.ability_specs", {})
    spec = specs.get(args.ability_id) if isinstance(specs, dict) else None
    if not isinstance(spec, dict):
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"ability has no trusted world specification: {args.ability_id}",
            stage="execute_tool",
            details={"ability_id": args.ability_id},
        )
    owner_id = str(spec.get("owner_id") or "")
    if owner_id != context.actor_id:
        raise _tool_error(
            ToolFailureCode.permission_denied,
            f"ability is owned by another actor: {args.ability_id}",
            stage="execute_tool",
            details={"owner_id": owner_id},
        )

    target_id = str(spec.get("target_character_id") or "")
    target = _character(context, target_id)
    actor = context.state.characters[context.actor_id]
    move_actor = bool(spec.get("move_actor", True))
    move_target = bool(spec.get("move_target", True))
    destination_id = str(spec.get("destination_id") or "")
    if (move_actor or move_target) and destination_id not in context.state.locations:
        raise _tool_error(
            ToolFailureCode.target_not_found,
            f"ability destination does not exist: {destination_id}",
            stage="execute_tool",
            details={"destination_id": destination_id},
        )

    completion_flag = str(spec.get("completion_flag") or "")
    if completion_flag and context.state.flags.get(completion_flag):
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"ability was already completed: {args.ability_id}",
            stage="execute_tool",
            details={"completion_flag": completion_flag},
        )
    required_flags = spec.get("required_flags", {})
    if not isinstance(required_flags, dict) or any(
        context.state.flags.get(str(key)) != value
        for key, value in required_flags.items()
    ):
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"ability flags are not satisfied: {args.ability_id}",
            stage="execute_tool",
            details={"required_flags": required_flags},
        )

    actor_sources = {str(value) for value in spec.get("actor_source_locations", [])}
    target_sources = {
        str(value) for value in spec.get("target_source_locations", [])
    }
    if actor_sources and actor.location_id not in actor_sources:
        raise _tool_error(
            ToolFailureCode.spatial_constraint,
            f"actor is outside the ability source region: {args.ability_id}",
            stage="execute_tool",
            details={"actor_location_id": actor.location_id},
        )
    if target_sources and target.location_id not in target_sources:
        raise _tool_error(
            ToolFailureCode.spatial_constraint,
            f"target is outside the ability source region: {args.ability_id}",
            stage="execute_tool",
            details={"target_location_id": target.location_id},
        )
    if bool(spec.get("requires_co_location")):
        _require_same_location(context, target.character_id)

    summary = str(spec.get("summary") or f"{context.actor_id} invoked {args.ability_id}")
    operations: List[Operation] = []
    moved_ids: List[str] = []
    if move_actor:
        operations.append(
            Operation(
                op=OperationKind.move_character,
                target_id=context.actor_id,
                location_id=destination_id,
                reason=f"ability:{args.ability_id}",
            )
        )
        moved_ids.append(context.actor_id)
    if move_target:
        operations.append(
            Operation(
                op=OperationKind.move_character,
                target_id=target.character_id,
                location_id=destination_id,
                reason=f"ability:{args.ability_id}",
            )
        )
        moved_ids.append(target.character_id)

    perceptions = spec.get("perceptions", {})
    if isinstance(perceptions, dict):
        for character_id, perception in perceptions.items():
            if character_id not in context.state.character_psyches:
                continue
            text = str(perception).strip()
            if text:
                operations.append(
                    Operation(
                        op=OperationKind.update_psyche,
                        target_id=str(character_id),
                        perception=text,
                        reason=f"ability:{args.ability_id}",
                    )
                )
    if completion_flag:
        operations.append(
            Operation(
                op=OperationKind.set_flag,
                path=completion_flag,
                value=True,
                reason=f"ability:{args.ability_id}",
            )
        )
    if not operations:
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"ability has no executable effects: {args.ability_id}",
            stage="execute_tool",
        )

    return ToolCandidate(
        patch=StatePatch(operations=operations, notes=summary),
        output={
            "ability_id": args.ability_id,
            "owner_id": context.actor_id,
            "target_character_id": target.character_id,
            "destination_id": destination_id,
            "moved_character_ids": moved_ids,
        },
        summary=summary,
        target_ids=[
            value
            for value in [
                target.character_id,
                destination_id,
                actor.location_id,
            ]
            if value
        ],
        presentation_events=[
            PresentationEvent(
                event_type="ability_invoked",
                payload={
                    "ability_id": args.ability_id,
                    "owner_id": context.actor_id,
                    "target_character_id": target.character_id,
                    "destination_id": destination_id,
                },
            )
        ],
    )


def _destroy_item(context: ToolContext, raw: BaseModel) -> ToolCandidate:
    args = raw  # type: DestroyItemArguments
    item = context.state.items.get(args.item_id)
    if item is None:
        raise _tool_error(
            ToolFailureCode.target_not_found,
            f"unknown item: {args.item_id}",
            stage="execute_tool",
            details={"item_id": args.item_id},
        )
    if item.owner_id != context.actor_id:
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"actor does not own item: {args.item_id}",
            stage="execute_tool",
            details={"owner_id": item.owner_id},
        )
    if not item.accessible or item.quantity <= 0:
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"item is not available: {args.item_id}",
            stage="execute_tool",
            details={
                "accessible": item.accessible,
                "quantity": item.quantity,
            },
        )
    affordances = context.state.entity_affordances.get(item.item_id, [])
    if not any(
        affordance.action_type == "destroy_item"
        for affordance in affordances
    ):
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"item is not destructible: {args.item_id}",
            stage="execute_tool",
            details={"item_id": args.item_id},
        )

    patch = StatePatch(
        operations=[
            Operation(
                op=OperationKind.destroy_item,
                item_id=item.item_id,
                reason=f"destroyed by {context.actor_id}",
            )
        ],
        notes=f"{context.actor_id} destroys {item.item_id}",
    )
    return ToolCandidate(
        patch=patch,
        output={
            "item_id": item.item_id,
            "destroyed_by": context.actor_id,
        },
        summary=f"{context.actor_id} destroyed {item.item_id}",
        target_ids=[item.item_id],
        presentation_events=[
            PresentationEvent(
                event_type="item_destroyed",
                payload={
                    "item_id": item.item_id,
                    "character_id": context.actor_id,
                },
            )
        ],
    )


def _share_information(
    context: ToolContext,
    raw: BaseModel,
) -> ToolCandidate:
    args = raw  # type: ShareInformationArguments
    target = _require_distinct_target(context, args.target_character_id)
    _require_same_location(context, target.character_id)

    try:
        outcome = build_propagation_patch(
            context.state,
            source_character_id=context.actor_id,
            target_character_id=target.character_id,
            fact_id=args.fact_id,
            causal_event_id=context.metadata.get("tool_call_id"),
        )
    except PropagationError as exc:
        failure_code = {
            "ENTITY_NOT_FOUND": ToolFailureCode.target_not_found,
            "KNOWLEDGE_BOUNDARY_VIOLATION": ToolFailureCode.cognitive_boundary,
            "SPATIAL_PRECONDITION_FAILED": ToolFailureCode.spatial_constraint,
        }.get(exc.code, ToolFailureCode.precondition_failed)
        raise _tool_error(
            failure_code,
            exc.message,
            stage="execute_tool",
            details={
                "actor_id": context.actor_id,
                "target_character_id": target.character_id,
                "fact_id": args.fact_id,
                "propagation_code": exc.code,
            },
        ) from exc

    shared_confidence = round(outcome.record.resulting_confidence, 4)
    return ToolCandidate(
        patch=outcome.patch,
        output={
            "source_character_id": context.actor_id,
            "target_character_id": target.character_id,
            "fact_id": args.fact_id,
            "belief": outcome.record.resulting_belief.value,
            "confidence": shared_confidence,
            "evidence_id": outcome.evidence.evidence_id,
            "propagation_id": outcome.record.propagation_id,
            "target_changed": True,
        },
        summary=(
            f"{context.actor_id} shared {args.fact_id} "
            f"with {target.character_id}"
        ),
        target_ids=[target.character_id],
        presentation_events=[
            PresentationEvent(
                event_type="information_shared",
                payload={
                    "speaker_id": context.actor_id,
                    "to_id": target.character_id,
                    "fact_id": args.fact_id,
                    "confidence": shared_confidence,
                },
            )
        ],
    )


def _propose_alliance(
    context: ToolContext,
    raw: BaseModel,
) -> ToolCandidate:
    args = raw  # type: ProposeAllianceArguments
    target = _require_distinct_target(context, args.target_character_id)
    _require_same_location(context, target.character_id)
    try:
        patch = build_alliance_patch(
            context.state,
            proposer_id=context.actor_id,
            target_id=target.character_id,
            goal_key=args.goal_key,
            shared_fact_id=args.shared_fact_id,
            causal_event_id=context.metadata.get("tool_call_id"),
        )
    except ValueError as exc:
        raise _tool_error(
            ToolFailureCode.precondition_failed,
            f"alliance rejected: {exc}",
            stage="execute_tool",
            details={
                "proposer_id": context.actor_id,
                "target_character_id": target.character_id,
                "goal_key": args.goal_key,
                "shared_fact_id": args.shared_fact_id,
            },
        ) from exc

    alliance_id = patch.operations[0].alliance_id
    return ToolCandidate(
        patch=patch,
        output={
            "alliance_id": alliance_id,
            "member_ids": sorted([context.actor_id, target.character_id]),
            "goal_key": args.goal_key,
            "shared_fact_id": args.shared_fact_id,
        },
        summary=(
            f"{context.actor_id} formed alliance {alliance_id} "
            f"with {target.character_id}"
        ),
        target_ids=[target.character_id],
        presentation_events=[
            PresentationEvent(
                event_type="alliance_formed",
                payload={
                    "alliance_id": alliance_id,
                    "member_ids": sorted(
                        [context.actor_id, target.character_id]
                    ),
                    "goal_key": args.goal_key,
                },
            )
        ],
    )


CORE_TOOL_PERMISSIONS: FrozenSet[str] = frozenset(
    {
        "character.move",
        "character.communicate",
        "inventory.destroy",
        "inventory.give",
        "inventory.take",
        "inventory.pick_up",
        "knowledge.observe",
        "knowledge.share",
        "alliance.propose",
        "ability.invoke",
    }
)


def create_core_tool_registry() -> ToolRegistry:
    """注册受控世界交互与认知传播核心工具。"""

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="invoke_ability",
            description=(
                "Invoke one enabled character ability. The authoritative world "
                "package fixes its target, preconditions and state effects."
            ),
            arguments_model=InvokeAbilityArguments,
            handler=_invoke_ability,
            required_permissions=frozenset({"ability.invoke"}),
            allowed_patch_operations=frozenset(
                {
                    OperationKind.move_character,
                    OperationKind.update_psyche,
                    OperationKind.set_flag,
                }
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="move_to",
            description="Move the actor to a location or to a character's location.",
            arguments_model=MoveToArguments,
            handler=_move_to,
            required_permissions=frozenset({"character.move"}),
            allowed_patch_operations=frozenset(
                {OperationKind.move_character}
            ),
            requires_navigation_state=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="talk_to",
            description="Speak to a living character at the actor's location.",
            arguments_model=TalkToArguments,
            handler=_talk_to,
            required_permissions=frozenset({"character.communicate"}),
            allowed_patch_operations=frozenset(),
        )
    )
    registry.register(
        ToolDefinition(
            name="pick_up",
            description="Pick up an accessible unowned item at the actor's location.",
            arguments_model=PickUpArguments,
            handler=_pick_up,
            required_permissions=frozenset({"inventory.pick_up"}),
            allowed_patch_operations=frozenset(
                {OperationKind.transfer_item}
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="destroy_item",
            description=(
                "Destroy an owned item whose world affordances explicitly "
                "allow destruction."
            ),
            arguments_model=DestroyItemArguments,
            handler=_destroy_item,
            required_permissions=frozenset({"inventory.destroy"}),
            allowed_patch_operations=frozenset(
                {OperationKind.destroy_item}
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="give_item",
            description=(
                "Give an owned accessible item to a co-located living "
                "character."
            ),
            arguments_model=GiveItemArguments,
            handler=_give_item,
            required_permissions=frozenset({"inventory.give"}),
            allowed_patch_operations=frozenset(
                {OperationKind.transfer_item}
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="take_item",
            description=(
                "Take an accessible item from a co-located character only "
                "when the item's world affordance permits taking or transfer."
            ),
            arguments_model=TakeItemArguments,
            handler=_take_item,
            required_permissions=frozenset({"inventory.take"}),
            allowed_patch_operations=frozenset(
                {OperationKind.transfer_item}
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="observe",
            description=(
                "Directly observe an accessible authoritative world fact "
                "and record its evidence."
            ),
            arguments_model=ObserveArguments,
            handler=_observe,
            required_permissions=frozenset({"knowledge.observe"}),
            allowed_patch_operations=frozenset(
                {
                    OperationKind.update_belief,
                    OperationKind.record_evidence,
                }
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="share_information",
            description=(
                "Share a fact the actor actually knows with a co-located "
                "living character."
            ),
            arguments_model=ShareInformationArguments,
            handler=_share_information,
            required_permissions=frozenset({"knowledge.share"}),
            allowed_patch_operations=frozenset(
                {
                    OperationKind.update_belief,
                    OperationKind.record_evidence,
                    OperationKind.record_propagation,
                }
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="propose_alliance",
            description=(
                "Propose an alliance backed by bilateral trust, a common "
                "active goal, and shared evidence."
            ),
            arguments_model=ProposeAllianceArguments,
            handler=_propose_alliance,
            required_permissions=frozenset({"alliance.propose"}),
            allowed_patch_operations=frozenset(
                {OperationKind.form_alliance}
            ),
        )
    )
    return registry
