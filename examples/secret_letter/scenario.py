"""秘密信件的 Free/Script 场景与真实玩家干预路线。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from engine.agent_runtime import AgentExecutionOutcome
from engine.agent_tools import (
    CORE_TOOL_PERMISSIONS,
    ToolCall,
    create_core_tool_registry,
)
from engine.information_propagation import get_belief
from engine.scene_controller import (
    SceneConfig,
    SceneController,
    SceneEnding,
    SceneMode,
    SceneStatus,
    SceneSummary,
    ScriptBeat,
)
from world_schema import (
    AgentGoal,
    AgentPlan,
    Belief,
    Character,
    CharacterPsyche,
    CharacterRelation,
    EntityAffordance,
    Item,
    Location,
    PlanConditionKind,
    PlanStepCondition,
    RelationDimensions,
    WorldFact,
    WorldState,
)


GATEHOUSE = "loc_gatehouse"
COURTYARD = "loc_courtyard"
PLAYER = "char_player"
GUARD = "char_guard"
STEWARD = "char_steward"
ALLY = "char_ally"
RIVAL = "char_rival"
LETTER = "item_sealed_letter"
FACT_PLOT = "fact_regent_plot"
GOAL_PROTECT = "protect_estate"

PLAYER_ROUTE_DESTROY = "destroy_letter"
PLAYER_ROUTE_INTERCEPT = "intercept_letter"
PLAYER_ROUTE_EXPOSE = "expose_truth"


@dataclass(frozen=True)
class SecretLetterRun:
    state: WorldState
    outcomes: List[AgentExecutionOutcome]
    ending: str
    summary: SceneSummary

    @property
    def successful(self) -> bool:
        return (
            self.summary.status == SceneStatus.completed
            and all(outcome.result.success for outcome in self.outcomes)
        )


def build_snapshot() -> WorldState:
    """创建未被干预的权威初始快照。"""

    characters = {
        PLAYER: Character(
            character_id=PLAYER,
            display_name="玩家",
            location_id=GATEHOUSE,
        ),
        GUARD: Character(
            character_id=GUARD,
            display_name="守卫",
            location_id=GATEHOUSE,
        ),
        STEWARD: Character(
            character_id=STEWARD,
            display_name="管家",
            location_id=GATEHOUSE,
        ),
        ALLY: Character(
            character_id=ALLY,
            display_name="盟友",
            location_id=GATEHOUSE,
        ),
        RIVAL: Character(
            character_id=RIVAL,
            display_name="政敌",
            location_id=COURTYARD,
        ),
    }
    shared_goal = AgentGoal(
        goal_id=GOAL_PROTECT,
        goal_key=GOAL_PROTECT,
        description="保护庄园，阻止摄政王的阴谋",
        priority=0.9,
    )
    return WorldState(
        timeline_id="secret_letter_root",
        current_scene_id=GATEHOUSE,
        characters=characters,
        locations={
            GATEHOUSE: Location(
                location_id=GATEHOUSE,
                display_name="门房",
            ),
            COURTYARD: Location(
                location_id=COURTYARD,
                display_name="庭院",
            ),
        },
        items={
            LETTER: Item(
                item_id=LETTER,
                display_name="密封信件",
                location_id=GATEHOUSE,
                accessible=True,
            )
        },
        facts={
            FACT_PLOT: WorldFact(
                fact_id=FACT_PLOT,
                statement="摄政王将在午夜派人夺取庄园印信。",
                truth=Belief.believed_true,
                location_id=GATEHOUSE,
                item_id=LETTER,
                observable=True,
                base_confidence=1.0,
                keywords=["摄政王", "午夜", "夺取印信"],
            )
        },
        entity_affordances={
            LETTER: [
                EntityAffordance(
                    affordance_id="affordance_destroy_secret_letter",
                    entity_id=LETTER,
                    action_type="destroy_item",
                )
            ]
        },
        relations=[
            _relation(STEWARD, GUARD, trust=0.8),
            _relation(GUARD, STEWARD, trust=0.8),
            _relation(ALLY, STEWARD, trust=0.75),
            _relation(STEWARD, ALLY, trust=0.75),
            _relation(STEWARD, PLAYER, trust=0.7),
            _relation(ALLY, PLAYER, trust=0.7),
        ],
        character_psyches={
            PLAYER: CharacterPsyche(
                character_id=PLAYER,
                traits=["由玩家控制"],
                is_player=True,
            ),
            GUARD: CharacterPsyche(
                character_id=GUARD,
                traits=["警觉", "守序"],
                emotion="戒备",
                emotion_intensity=0.65,
                goals=[shared_goal.copy(deep=True)],
                plans=[
                    AgentPlan(
                        plan_id="guard_verify_letter",
                        goal_id=GOAL_PROTECT,
                        steps=["取得密信", "核验内容", "报告管家"],
                        step_conditions=[
                            PlanStepCondition(
                                kind=PlanConditionKind.item_owner,
                                item_id=LETTER,
                                character_id=GUARD,
                            ),
                            PlanStepCondition(
                                kind=PlanConditionKind.belief_known,
                                character_id=GUARD,
                                fact_id=FACT_PLOT,
                            ),
                            PlanStepCondition(
                                kind=PlanConditionKind.belief_known,
                                character_id=STEWARD,
                                fact_id=FACT_PLOT,
                            ),
                        ],
                    )
                ],
                recent_perceptions=["午夜前门房出现一封来历不明的密信。"],
            ),
            STEWARD: CharacterPsyche(
                character_id=STEWARD,
                traits=["谨慎", "忠诚"],
                emotion="疑虑",
                emotion_intensity=0.55,
                goals=[shared_goal.copy(deep=True)],
                plans=[
                    AgentPlan(
                        plan_id="steward_build_defense",
                        goal_id=GOAL_PROTECT,
                        steps=["核对守卫证词", "通知盟友", "建立防卫联盟"],
                        step_conditions=[
                            PlanStepCondition(
                                kind=PlanConditionKind.belief_known,
                                character_id=STEWARD,
                                fact_id=FACT_PLOT,
                            ),
                            PlanStepCondition(
                                kind=PlanConditionKind.belief_known,
                                character_id=ALLY,
                                fact_id=FACT_PLOT,
                            ),
                            PlanStepCondition(
                                kind=PlanConditionKind.alliance_formed,
                                member_ids=[STEWARD, ALLY],
                                goal_key=GOAL_PROTECT,
                            ),
                        ],
                    )
                ],
                recent_perceptions=["摄政王近期频繁试探庄园守备。"],
            ),
            ALLY: CharacterPsyche(
                character_id=ALLY,
                traits=["果断", "忠诚"],
                emotion="警醒",
                emotion_intensity=0.6,
                goals=[shared_goal.copy(deep=True)],
                plans=[
                    AgentPlan(
                        plan_id="ally_prepare_response",
                        goal_id=GOAL_PROTECT,
                        steps=["等待可信证据", "确认共同目标", "调集护卫"],
                    )
                ],
                recent_perceptions=["管家要求她在门房附近待命。"],
            ),
            RIVAL: CharacterPsyche(
                character_id=RIVAL,
                traits=["多疑", "逐利"],
                emotion="盘算",
                emotion_intensity=0.7,
                goals=[
                    AgentGoal(
                        goal_id="seize_letter",
                        goal_key="seize_letter",
                        description="抢先控制密信并阻止防卫联盟形成",
                        priority=0.85,
                    )
                ],
                plans=[
                    AgentPlan(
                        plan_id="rival_intercept_letter",
                        goal_id="seize_letter",
                        steps=["观察门房动静", "截获密信", "带往庭院"],
                    )
                ],
                recent_perceptions=["有人将在午夜前把关键消息送进庄园。"],
            ),
        },
    )


def next_autonomous_call(
    state: WorldState,
    turn_index: Optional[int] = None,
) -> Optional[ToolCall]:
    """按权威状态选择下一步，不读取隐藏结局，也不直接修改状态。"""

    if state.alliances:
        return None
    steward_belief = get_belief(state, STEWARD, FACT_PLOT)
    ally_belief = get_belief(state, ALLY, FACT_PLOT)
    if steward_belief is not None and ally_belief is not None:
        return _call(
            state,
            STEWARD,
            "propose_alliance",
            {
                "target_character_id": ALLY,
                "goal_key": GOAL_PROTECT,
                "shared_fact_id": FACT_PLOT,
            },
        )

    letter = state.items[LETTER]
    if letter.quantity <= 0 or not letter.accessible:
        return None
    if letter.owner_id in (PLAYER, RIVAL):
        return None
    if letter.owner_id is None:
        return _call(state, GUARD, "pick_up", {"item_id": LETTER})
    if letter.owner_id != GUARD:
        return None
    if get_belief(state, GUARD, FACT_PLOT) is None:
        return _call(state, GUARD, "observe", {"fact_id": FACT_PLOT})
    if steward_belief is None:
        return _call(
            state,
            GUARD,
            "share_information",
            {
                "target_character_id": STEWARD,
                "fact_id": FACT_PLOT,
            },
        )
    if ally_belief is None:
        return _call(
            state,
            STEWARD,
            "share_information",
            {
                "target_character_id": ALLY,
                "fact_id": FACT_PLOT,
            },
        )
    return None


def player_intervention_calls(route: Optional[str]) -> List[ToolCall]:
    """把玩家路线表示为普通 ToolCall；不设置状态标记或预写结局。"""

    if route in (None, "none"):
        return []
    if route == PLAYER_ROUTE_DESTROY:
        return [
            _player_call(1, "pick_up", {"item_id": LETTER}, route),
            _player_call(2, "destroy_item", {"item_id": LETTER}, route),
        ]
    if route in (PLAYER_ROUTE_INTERCEPT, "rival_intercepts"):
        return [
            _player_call(1, "pick_up", {"item_id": LETTER}, route),
            _player_call(
                2,
                "move_to",
                {"destination_id": COURTYARD},
                route,
            ),
        ]
    if route == PLAYER_ROUTE_EXPOSE:
        return [
            _player_call(1, "pick_up", {"item_id": LETTER}, route),
            _player_call(2, "observe", {"fact_id": FACT_PLOT}, route),
            _player_call(
                3,
                "share_information",
                {
                    "target_character_id": STEWARD,
                    "fact_id": FACT_PLOT,
                },
                route,
            ),
            _player_call(
                4,
                "share_information",
                {
                    "target_character_id": ALLY,
                    "fact_id": FACT_PLOT,
                },
                route,
            ),
        ]
    raise ValueError(f"unknown player route: {route}")


def build_script_beats() -> List[ScriptBeat]:
    """标准演示大纲；每一拍仍会通过工具前置条件和 Patch 门禁。"""

    return [
        ScriptBeat(
            beat_id="find_letter",
            actor_id=GUARD,
            tool_name="pick_up",
            arguments={"item_id": LETTER},
            objective="守卫取得密信",
        ),
        ScriptBeat(
            beat_id="read_letter",
            actor_id=GUARD,
            tool_name="observe",
            arguments={"fact_id": FACT_PLOT},
            objective="守卫获得直接证据",
        ),
        ScriptBeat(
            beat_id="report_to_steward",
            actor_id=GUARD,
            tool_name="share_information",
            arguments={
                "target_character_id": STEWARD,
                "fact_id": FACT_PLOT,
            },
            objective="管家获知阴谋",
        ),
        ScriptBeat(
            beat_id="notify_ally",
            actor_id=STEWARD,
            tool_name="share_information",
            arguments={
                "target_character_id": ALLY,
                "fact_id": FACT_PLOT,
            },
            objective="盟友获知阴谋",
        ),
        ScriptBeat(
            beat_id="form_alliance",
            actor_id=STEWARD,
            tool_name="propose_alliance",
            arguments={
                "target_character_id": ALLY,
                "goal_key": GOAL_PROTECT,
                "shared_fact_id": FACT_PLOT,
            },
            objective="建立有共同证据的防卫联盟",
        ),
    ]


async def run_secret_letter_scene(
    *,
    mode: SceneMode = SceneMode.free,
    player_route: Optional[str] = None,
    max_turns: int = 12,
    initial_state: Optional[WorldState] = None,
    store: Optional[Any] = None,
    session_id: Optional[str] = None,
) -> SecretLetterRun:
    state = (
        initial_state.copy(deep=True)
        if initial_state is not None
        else build_snapshot()
    )
    config = SceneConfig(
        scene_id="scene_secret_letter",
        mode=mode,
        location_id=GATEHOUSE,
        participant_ids=[PLAYER, GUARD, STEWARD, ALLY],
        objective="阻止密信中的阴谋并形成可信联盟",
        max_turns=max_turns,
        random_seed=20260729,
        script_beats=build_script_beats() if mode == SceneMode.script else [],
    )
    controller = SceneController(
        create_core_tool_registry(),
        permissions=CORE_TOOL_PERMISSIONS,
    )
    run = await controller.run(
        state,
        config,
        free_selector=next_autonomous_call,
        ending_evaluator=evaluate_ending,
        initial_calls=player_intervention_calls(player_route),
        store=store,
        session_id=session_id,
    )
    return SecretLetterRun(
        state=run.state,
        outcomes=run.outcomes,
        ending=run.summary.ending_id or run.summary.status.value,
        summary=run.summary,
    )


async def run_autonomous(
    *,
    intervention: str = "none",
    max_steps: int = 8,
) -> SecretLetterRun:
    """兼容旧入口；所有非空干预现在均通过真实 ToolCall 执行。"""

    route = (
        PLAYER_ROUTE_INTERCEPT
        if intervention == "rival_intercepts"
        else intervention
    )
    return await run_secret_letter_scene(
        mode=SceneMode.free,
        player_route=route,
        max_turns=max_steps,
    )


def evaluate_ending(state: WorldState) -> Optional[SceneEnding]:
    letter = state.items[LETTER]
    if letter.quantity <= 0 or letter.attrs.get("destroyed"):
        return SceneEnding(
            ending_id="letter_destroyed",
            objective_satisfied=False,
            reason="玩家销毁了唯一密信，传播链被物理中断。",
        )
    if state.alliances:
        exposed_by_player = any(
            get_belief(state, character_id, FACT_PLOT) is not None
            and get_belief(
                state,
                character_id,
                FACT_PLOT,
            ).source_character_id == PLAYER
            for character_id in (STEWARD, ALLY)
        )
        return SceneEnding(
            ending_id=(
                "truth_exposed"
                if exposed_by_player
                else "defenders_allied"
            ),
            objective_satisfied=True,
            reason="管家与盟友依据共同证据形成防卫联盟。",
        )
    if (
        letter.owner_id == PLAYER
        and state.characters[PLAYER].location_id != GATEHOUSE
    ):
        return SceneEnding(
            ending_id="player_intercepted",
            objective_satisfied=False,
            reason="玩家携带密信离开，NPC 无法继续观察或传播。",
        )
    if letter.owner_id == RIVAL:
        return SceneEnding(
            ending_id="rival_advantage",
            objective_satisfied=False,
            reason="政敌控制密信，防卫方无法取得证据。",
        )
    return None


def _call(
    state: WorldState,
    actor_id: str,
    tool_name: str,
    arguments: dict,
) -> ToolCall:
    sequence = state.version + 1
    return ToolCall(
        call_id=f"scene_secret_letter_{sequence:02d}_{tool_name}",
        actor_id=actor_id,
        tool_name=tool_name,
        arguments=arguments,
    )


def _player_call(
    sequence: int,
    tool_name: str,
    arguments: dict,
    route: str,
) -> ToolCall:
    return ToolCall(
        call_id=f"secret_letter_player_{route}_{sequence:02d}_{tool_name}",
        actor_id=PLAYER,
        tool_name=tool_name,
        arguments=arguments,
    )


def _relation(
    source_id: str,
    target_id: str,
    *,
    trust: float,
) -> CharacterRelation:
    return CharacterRelation(
        source_id=source_id,
        target_id=target_id,
        private_relation="共同守护庄园",
        dimensions=RelationDimensions(
            trust=trust,
            hostility=0.05,
        ),
    )
