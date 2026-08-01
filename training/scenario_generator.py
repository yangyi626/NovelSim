"""Deterministic original scenario families for NovelSim V2 training."""

from __future__ import annotations

import hashlib
import json
import random
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field, root_validator

from engine.agent_tools import ToolCall
from engine.information_propagation import get_belief
from engine.scene_controller import SceneEnding
from world_schema import (
    AgentGoal,
    AgentPlan,
    Character,
    CharacterPsyche,
    CharacterRelation,
    Item,
    Location,
    PlanConditionKind,
    PlanStepCondition,
    RelationDimensions,
    WorldConcept,
    WorldConstraint,
    WorldState,
)


class ScenarioFamily(str, Enum):
    secret_transport = "secret_transport"
    resource_negotiation = "resource_negotiation"
    rescue_escort = "rescue_escort"


class ConditionKind(str, Enum):
    item_owner = "item_owner"
    actor_location = "actor_location"
    alliance_members = "alliance_members"
    belief_holder = "belief_holder"
    flag_equals = "flag_equals"


class ScenarioCondition(BaseModel):
    kind: ConditionKind
    entity_id: str = ""
    expected_id: str = ""
    member_ids: List[str] = Field(default_factory=list)
    minimum_member_count: int = Field(0, ge=0)
    goal_key: str = ""
    shared_fact_id: str = ""
    fact_id: str = ""
    flag_key: str = ""
    expected_value: Any = None

    class Config:
        extra = "forbid"
        allow_mutation = False


class GeneratedScenario(BaseModel):
    schema_version: str = "generated_scenario.v1"
    scenario_id: str
    world_package_id: str
    scenario_family: ScenarioFamily
    variant_id: str
    random_seed: int
    template_version: str = "scenario_generator.v3"
    source_type: str = "original_parameterized_generator"
    content_origin: str = "original_for_novelsim_v2"
    license_spdx: str = "CC-BY-4.0"
    objective: str
    participant_ids: List[str]
    max_turns: int = Field(ge=1, le=100)
    initial_state: WorldState
    scripted_calls: List[ToolCall]
    success_conditions: List[ScenarioCondition]
    accepted_outcomes: List[str] = Field(default_factory=lambda: ["success"])
    parameters: Dict[str, Any] = Field(default_factory=dict)
    entity_ids: List[str] = Field(default_factory=list)
    rule_ids: List[str] = Field(default_factory=list)
    content_hash: Optional[str] = None

    class Config:
        extra = "forbid"
        allow_mutation = False

    @root_validator(skip_on_failure=True)
    def _validate_contract_and_hash(cls, values):
        state = values.get("initial_state")
        participants = set(values.get("participant_ids") or [])
        calls = values.get("scripted_calls") or []
        if state is not None:
            missing = sorted(participants - set(state.characters))
            if missing:
                raise ValueError(
                    "scenario participants missing from state: %s"
                    % ", ".join(missing)
                )
        call_ids = [call.call_id for call in calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("scenario scripted call_ids must be unique")
        outsiders = sorted(
            {call.actor_id for call in calls if call.actor_id not in participants}
        )
        if outsiders:
            raise ValueError(
                "scripted actors outside participants: %s"
                % ", ".join(outsiders)
            )
        supplied = values.get("content_hash")
        values["content_hash"] = None
        calculated = scenario_content_hash(values)
        if supplied not in (None, calculated):
            raise ValueError("scenario content_hash mismatch")
        values["content_hash"] = calculated
        return values


def generate_scenario(
    family: ScenarioFamily,
    *,
    variant_index: int,
    seed: int,
) -> GeneratedScenario:
    if variant_index < 0:
        raise ValueError("variant_index must be non-negative")
    family = ScenarioFamily(family)
    if family == ScenarioFamily.secret_transport:
        payload = _secret_transport(variant_index, seed)
    elif family == ScenarioFamily.resource_negotiation:
        payload = _resource_negotiation(variant_index, seed)
    else:
        payload = _rescue_escort(variant_index, seed)
    return GeneratedScenario.parse_obj(payload)


def generate_scenario_grid(
    *,
    families: Iterable[ScenarioFamily] = tuple(ScenarioFamily),
    variants_per_family: int = 10,
    seeds: Iterable[int] = (11, 23, 37, 51, 79),
) -> List[GeneratedScenario]:
    if variants_per_family < 1:
        raise ValueError("variants_per_family must be positive")
    return [
        generate_scenario(family, variant_index=variant, seed=seed)
        for family in families
        for variant in range(variants_per_family)
        for seed in seeds
    ]


def evaluate_scenario(
    scenario: GeneratedScenario,
    state: WorldState,
) -> Optional[SceneEnding]:
    if not all(_condition_matches(condition, state) for condition in scenario.success_conditions):
        return None
    return SceneEnding(
        ending_id="success",
        objective_satisfied=True,
        reason="all deterministic scenario success conditions are satisfied",
    )


def scenario_progress(
    scenario: GeneratedScenario,
    state: WorldState,
) -> float:
    """Return deterministic objective progress in ``[0, 1]``.

    GRPO rewards use the fraction of independently verifiable success
    conditions instead of comparing an action with one privileged expert
    action.  This keeps multiple legal solution paths rewardable.
    """

    conditions = list(scenario.success_conditions)
    if not conditions:
        return 1.0
    matched = sum(_condition_matches(condition, state) for condition in conditions)
    return round(float(matched) / float(len(conditions)), 6)


def scenario_content_hash(value) -> str:
    payload = _plain(value)
    payload.pop("content_hash", None)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _secret_transport(variant: int, seed: int) -> Dict[str, Any]:
    from examples.secret_letter import (
        ALLY,
        FACT_PLOT,
        GOAL_PROTECT,
        GUARD,
        PLAYER,
        STEWARD,
        build_script_beats,
        build_snapshot,
    )

    rng = random.Random((variant + 1) * 1000003 + seed)
    state = build_snapshot()
    letter_names = ["密封信件", "火漆密函", "加密账页", "军情信筒"]
    item_id = "item_sealed_letter"
    state.items[item_id].display_name = letter_names[variant % len(letter_names)]
    state.world_time = "day_%02d_%02d" % (variant + 1, seed % 24)
    state.flags["scenario.variant"] = variant
    state.flags["scenario.seed"] = seed
    for relation in state.relations:
        relation.dimensions.trust = min(
            0.95,
            max(0.7, relation.dimensions.trust + rng.uniform(-0.02, 0.02)),
        )
    state.world_concepts["aircraft"] = WorldConcept(
        concept_id="aircraft",
        display_name="飞机",
        aliases=["飞机", "直升机"],
        category="transport",
        available=False,
        requires_entity=True,
    )
    state.world_constraints.append(WorldConstraint(
        constraint_id="secret_transport_no_modern_aircraft",
        category="technology",
        statement="当前古典庄园世界不存在飞机或现代航空载具。",
        forbidden_concept_ids=["aircraft"],
        strict_allowlist=True,
    ))
    calls = [
        ToolCall(
            call_id="secret_transport_%02d_%02d_%02d_%s"
            % (variant, seed, index, beat.tool_name),
            actor_id=beat.actor_id,
            tool_name=beat.tool_name,
            arguments=dict(beat.arguments),
        )
        for index, beat in enumerate(build_script_beats(), start=1)
    ]
    return _scenario_payload(
        family=ScenarioFamily.secret_transport,
        variant=variant,
        seed=seed,
        state=state,
        participants=[PLAYER, GUARD, STEWARD, ALLY],
        objective="取得密信、传播可信证据并形成防卫联盟",
        calls=calls,
        success_conditions=[
            ScenarioCondition(
                kind=ConditionKind.belief_holder,
                entity_id=ALLY,
                fact_id=FACT_PLOT,
            ),
            ScenarioCondition(
                kind=ConditionKind.alliance_members,
                member_ids=[STEWARD],
                minimum_member_count=2,
                goal_key=GOAL_PROTECT,
                shared_fact_id=FACT_PLOT,
            ),
        ],
        parameters={
            "letter_style": state.items[item_id].display_name,
            "trust_jitter": "deterministic_seeded",
        },
    )


def _resource_negotiation(variant: int, seed: int) -> Dict[str, Any]:
    actor = "char_quartermaster"
    target = "char_settlement_leader"
    player = "char_player"
    market = "loc_relief_market"
    item_id = "item_relief_resource"
    resource_names = ["净水箱", "药草包", "谷物袋", "御寒毯"]
    resource_name = resource_names[variant % len(resource_names)]
    state = WorldState(
        timeline_id="resource_negotiation_root",
        current_scene_id=market,
        world_time="relief_day_%02d" % (variant + 1),
        characters={
            actor: Character(
                character_id=actor,
                display_name="救济物资官",
                location_id=market,
                identity_tags=["negotiator", "quartermaster"],
            ),
            target: Character(
                character_id=target,
                display_name="聚落代表%02d" % variant,
                location_id=market,
                identity_tags=["recipient", "civilian_leader"],
            ),
            player: Character(
                character_id=player,
                display_name="玩家观察员",
                location_id=market,
            ),
        },
        locations={
            market: Location(location_id=market, display_name="救济集市"),
        },
        items={
            item_id: Item(
                item_id=item_id,
                display_name=resource_name,
                location_id=market,
                accessible=True,
            ),
        },
        relations=[
            CharacterRelation(
                source_id=actor,
                target_id=target,
                private_relation="需要建立可信交易",
                dimensions=RelationDimensions(trust=0.55 + 0.01 * (seed % 5)),
            )
        ],
        character_psyches={
            actor: CharacterPsyche(
                character_id=actor,
                traits=["务实", "守信"],
                emotion="审慎",
                goals=[AgentGoal(
                    goal_id="deliver_relief",
                    goal_key="deliver_relief",
                    description="将稀缺物资交付给聚落代表",
                    priority=0.9,
                )],
                plans=[AgentPlan(
                    plan_id="negotiate_delivery",
                    goal_id="deliver_relief",
                    steps=["取得物资", "说明条件", "完成交付"],
                    step_conditions=[
                        PlanStepCondition(
                            kind=PlanConditionKind.item_owner,
                            item_id=item_id,
                            character_id=actor,
                        ),
                        PlanStepCondition(
                            kind=PlanConditionKind.tool_committed,
                            actor_id=actor,
                            tool_name="talk_to",
                            argument_equals={"target_character_id": target},
                        ),
                        PlanStepCondition(
                            kind=PlanConditionKind.item_owner,
                            item_id=item_id,
                            character_id=target,
                        ),
                    ],
                )],
            ),
            target: CharacterPsyche(
                character_id=target,
                traits=["保护聚落", "谨慎"],
                emotion="焦虑",
            ),
            player: CharacterPsyche(
                character_id=player,
                traits=["由玩家控制"],
                is_player=True,
            ),
        },
        flags={"scenario.variant": variant, "scenario.seed": seed},
    )
    calls = [
        _call(variant, seed, 1, actor, "pick_up", {"item_id": item_id}),
        _call(
            variant,
            seed,
            2,
            actor,
            "talk_to",
            {
                "target_character_id": target,
                "message": "先确认物资用于聚落公共救济，再完成交付。",
                "tone": "务实",
            },
        ),
        _call(
            variant,
            seed,
            3,
            actor,
            "give_item",
            {"target_character_id": target, "item_id": item_id},
        ),
    ]
    return _scenario_payload(
        family=ScenarioFamily.resource_negotiation,
        variant=variant,
        seed=seed,
        state=state,
        participants=[player, actor, target],
        objective="协商公共用途并将稀缺救济物资交付给聚落代表",
        calls=calls,
        success_conditions=[ScenarioCondition(
            kind=ConditionKind.item_owner,
            entity_id=item_id,
            expected_id=target,
        )],
        parameters={"resource_name": resource_name, "recipient_variant": variant},
    )


def _rescue_escort(variant: int, seed: int) -> Dict[str, Any]:
    actor = "char_rescuer"
    patient = "char_patient"
    player = "char_player"
    outpost = "loc_rescue_outpost"
    infirmary = "loc_field_infirmary"
    medicine = "item_rescue_medicine"
    medicines = ["止血药", "解毒剂", "退热药", "抗感染药"]
    medicine_name = medicines[variant % len(medicines)]
    state = WorldState(
        timeline_id="rescue_escort_root",
        current_scene_id=outpost,
        world_time="rescue_hour_%02d" % (seed % 24),
        characters={
            actor: Character(
                character_id=actor,
                display_name="救援队员",
                location_id=outpost,
                identity_tags=["rescuer"],
            ),
            patient: Character(
                character_id=patient,
                display_name="伤员%02d" % variant,
                location_id=infirmary,
                identity_tags=["patient"],
            ),
            player: Character(
                character_id=player,
                display_name="玩家调度员",
                location_id=outpost,
            ),
        },
        locations={
            outpost: Location(location_id=outpost, display_name="救援前哨"),
            infirmary: Location(
                location_id=infirmary,
                display_name="野战医务所",
            ),
        },
        items={
            medicine: Item(
                item_id=medicine,
                display_name=medicine_name,
                location_id=outpost,
                accessible=True,
            ),
        },
        character_psyches={
            actor: CharacterPsyche(
                character_id=actor,
                traits=["果断", "重视生命"],
                emotion="紧迫",
                emotion_intensity=0.8,
                goals=[AgentGoal(
                    goal_id="deliver_medicine",
                    goal_key="deliver_medicine",
                    description="把正确药物送到伤员手中",
                    priority=1.0,
                )],
                plans=[AgentPlan(
                    plan_id="rescue_delivery",
                    goal_id="deliver_medicine",
                    steps=["取得药物", "前往医务所", "交给伤员"],
                    step_conditions=[
                        PlanStepCondition(
                            kind=PlanConditionKind.item_owner,
                            item_id=medicine,
                            character_id=actor,
                        ),
                        PlanStepCondition(
                            kind=PlanConditionKind.character_at,
                            character_id=actor,
                            location_id=infirmary,
                        ),
                        PlanStepCondition(
                            kind=PlanConditionKind.item_owner,
                            item_id=medicine,
                            character_id=patient,
                        ),
                    ],
                )],
            ),
            patient: CharacterPsyche(
                character_id=patient,
                traits=["等待救援"],
                emotion="痛苦",
            ),
            player: CharacterPsyche(
                character_id=player,
                traits=["由玩家控制"],
                is_player=True,
            ),
        },
        flags={
            "scenario.variant": variant,
            "scenario.seed": seed,
            "rescue.deadline_steps": 4 + (seed % 3),
        },
    )
    calls = [
        _call(variant, seed, 1, actor, "pick_up", {"item_id": medicine}),
        _call(
            variant,
            seed,
            2,
            actor,
            "move_to",
            {"destination_id": infirmary},
        ),
        _call(
            variant,
            seed,
            3,
            actor,
            "give_item",
            {"target_character_id": patient, "item_id": medicine},
        ),
    ]
    return _scenario_payload(
        family=ScenarioFamily.rescue_escort,
        variant=variant,
        seed=seed,
        state=state,
        participants=[player, actor, patient],
        objective="在时限内取得药物、抵达医务所并完成救援交付",
        calls=calls,
        success_conditions=[
            ScenarioCondition(
                kind=ConditionKind.item_owner,
                entity_id=medicine,
                expected_id=patient,
            ),
            ScenarioCondition(
                kind=ConditionKind.actor_location,
                entity_id=actor,
                expected_id=infirmary,
            ),
        ],
        parameters={
            "medicine_name": medicine_name,
            "deadline_steps": state.flags["rescue.deadline_steps"],
        },
    )


def _scenario_payload(
    *,
    family: ScenarioFamily,
    variant: int,
    seed: int,
    state: WorldState,
    participants: List[str],
    objective: str,
    calls: List[ToolCall],
    success_conditions: List[ScenarioCondition],
    parameters: Dict[str, Any],
) -> Dict[str, Any]:
    variant_id = "%s_v%03d" % (family.value, variant)
    package_id = "%s_s%06d" % (variant_id, seed)
    entity_ids = sorted(
        list(state.characters) + list(state.items) + list(state.locations)
    )
    rule_ids = sorted(
        [rule.rule_id for rule in state.rules]
        + [constraint.constraint_id for constraint in state.world_constraints]
    )
    return {
        "scenario_id": package_id,
        "world_package_id": package_id,
        "scenario_family": family,
        "variant_id": variant_id,
        "random_seed": seed,
        "objective": objective,
        "participant_ids": participants,
        "max_turns": max(8, len(calls) + 2),
        "initial_state": state,
        "scripted_calls": calls,
        "success_conditions": success_conditions,
        "parameters": parameters,
        "entity_ids": entity_ids,
        "rule_ids": rule_ids,
    }


def _call(
    variant: int,
    seed: int,
    index: int,
    actor_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
) -> ToolCall:
    return ToolCall(
        call_id="scenario_%02d_%02d_%02d_%s"
        % (variant, seed, index, tool_name),
        actor_id=actor_id,
        tool_name=tool_name,
        arguments=arguments,
    )


def _condition_matches(
    condition: ScenarioCondition,
    state: WorldState,
) -> bool:
    if condition.kind == ConditionKind.item_owner:
        item = state.items.get(condition.entity_id)
        return item is not None and item.owner_id == condition.expected_id
    if condition.kind == ConditionKind.actor_location:
        actor = state.characters.get(condition.entity_id)
        return actor is not None and actor.location_id == condition.expected_id
    if condition.kind == ConditionKind.alliance_members:
        expected = set(condition.member_ids)
        return any(
            alliance.status == "active"
            and expected.issubset(alliance.member_ids)
            and len(set(alliance.member_ids)) >= condition.minimum_member_count
            and (
                not condition.goal_key
                or alliance.goal_key == condition.goal_key
            )
            and (
                not condition.shared_fact_id
                or condition.shared_fact_id in alliance.shared_fact_ids
            )
            for alliance in state.alliances.values()
        )
    if condition.kind == ConditionKind.belief_holder:
        return get_belief(state, condition.entity_id, condition.fact_id) is not None
    if condition.kind == ConditionKind.flag_equals:
        return state.flags.get(condition.flag_key) == condition.expected_value
    return False


def _plain(value):
    if isinstance(value, BaseModel):
        return _plain(value.dict())
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value
