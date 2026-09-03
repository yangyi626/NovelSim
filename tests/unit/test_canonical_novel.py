import asyncio
import json

import pytest

from engine import (
    AgentExecutionStateMachine,
    CORE_TOOL_PERMISSIONS,
    ToolCall,
    build_game_observation,
    create_core_tool_registry,
)
from evaluation.canonical_novel import load_canonical_case, run_canonical_case
from engine.narrative_planner import (
    ActorNarrativePlan,
    NarrativePlannerError,
    RealLLMNarrativePlanner,
    _actor_plan_messages,
    _reject_invalid_immediate_action,
    _reject_stagnant_dialogue_loop,
)
from examples.huarong_lane.canonical_case import (
    FENGYUE_PAVILION,
    JIYUE,
    ISSUE_HALL_SUMMONS,
    LUHE,
    MYSTIC_SPACE,
    OPEN_SANSHENG_SPRING,
    PULL_INTO_SPACE,
    RETURN_FROM_SPACE,
    SANSHENG_SPRING,
    YE_CLAN_HALL,
    YEFU,
    build_canonical_start_state,
)
from examples.huarong_lane.scenario import LIN, NIGHT, OUTER_ROBE, QINGQING, SCENE_ID


def test_take_item_is_guarded_by_location_ownership_and_affordance():
    state = build_canonical_start_state()
    machine = AgentExecutionStateMachine(create_core_tool_registry())
    outcome = asyncio.run(
        machine.execute(
            ToolCall(
                actor_id=NIGHT,
                tool_name="take_item",
                arguments={
                    "item_id": OUTER_ROBE,
                    "target_character_id": QINGQING,
                },
            ),
            state,
            permissions=CORE_TOOL_PERMISSIONS,
        )
    )
    assert outcome.result.success is True
    assert outcome.new_state.items[OUTER_ROBE].owner_id == NIGHT
    assert state.items[OUTER_ROBE].owner_id == QINGQING


def test_planner_observation_hides_unsatisfied_location_gate_and_rejects_noop_move():
    state = build_canonical_start_state()
    registry = create_core_tool_registry()
    qingqing_observation = build_game_observation(state, QINGQING, registry)
    fengyue = next(
        location
        for location in qingqing_observation.visible_locations
        if location.location_id == FENGYUE_PAVILION
    )
    assert fengyue.accessible is False

    night_observation = build_game_observation(state, NIGHT, registry)
    noop = ActorNarrativePlan.parse_obj(
        {
            "actor_id": NIGHT,
            "intent": "重复移动",
            "steps": [
                {
                    "step_id": "move_current",
                    "tool_name": "move_to",
                    "arguments": {"destination_id": SCENE_ID},
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="already at"):
        _reject_invalid_immediate_action(noop, night_observation)

    self_talk = ActorNarrativePlan.parse_obj(
        {
            "actor_id": NIGHT,
            "intent": "无效自我对话",
            "steps": [
                {
                    "step_id": "talk_to_self",
                    "tool_name": "talk_to",
                    "arguments": {
                        "target_character_id": NIGHT,
                        "message": "自言自语",
                        "tone": "平静",
                    },
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="actor itself"):
        _reject_invalid_immediate_action(self_talk, night_observation)

    delayed_noop = ActorNarrativePlan.parse_obj(
        {
            "actor_id": NIGHT,
            "intent": "对话后重复移动",
            "steps": [
                {
                    "step_id": "warn_sister",
                    "tool_name": "talk_to",
                    "arguments": {
                        "target_character_id": QINGQING,
                        "message": "不要再试探我。",
                        "tone": "冷厉",
                    },
                },
                {
                    "step_id": "repeat_location",
                    "tool_name": "move_to",
                    "arguments": {"destination_id": SCENE_ID},
                },
            ],
        }
    )
    with pytest.raises(ValueError, match="already at"):
        _reject_invalid_immediate_action(delayed_noop, night_observation)

    state.flags["canonical.returned_fengyue_pavilion"] = True
    unlocked = build_game_observation(state, QINGQING, registry)
    assert next(
        location
        for location in unlocked.visible_locations
        if location.location_id == FENGYUE_PAVILION
    ).accessible is True


def test_world_ability_is_owner_scoped_and_uses_fixed_effects():
    state = build_canonical_start_state()
    registry = create_core_tool_registry()
    night_observation = build_game_observation(state, NIGHT, registry)
    assert OUTER_ROBE in {item.item_id for item in night_observation.visible_items}
    unavailable = build_game_observation(state, JIYUE, registry)
    assert unavailable.available_abilities == ()
    assert "invoke_ability" not in {
        item.name for item in unavailable.available_tools
    }

    state.characters[NIGHT].location_id = YEFU
    state.flags["canonical.lin_warning_done"] = True
    ready = build_game_observation(state, JIYUE, registry)
    assert [item.ability_id for item in ready.available_abilities] == [
        PULL_INTO_SPACE
    ]
    assert "invoke_ability" in {item.name for item in ready.available_tools}
    machine = AgentExecutionStateMachine(registry)

    denied = asyncio.run(
        machine.execute(
            ToolCall(
                actor_id=NIGHT,
                tool_name="invoke_ability",
                arguments={"ability_id": PULL_INTO_SPACE},
            ),
            state,
            permissions=CORE_TOOL_PERMISSIONS,
        )
    )
    assert denied.result.success is False
    assert denied.result.failure.code.value == "permission_denied"

    outcome = asyncio.run(
        machine.execute(
            ToolCall(
                actor_id=JIYUE,
                tool_name="invoke_ability",
                arguments={"ability_id": PULL_INTO_SPACE},
            ),
            state,
            permissions=CORE_TOOL_PERMISSIONS,
        )
    )
    assert outcome.result.success is True
    assert outcome.new_state.characters[NIGHT].location_id == MYSTIC_SPACE
    assert outcome.new_state.characters[JIYUE].location_id == MYSTIC_SPACE
    assert outcome.new_state.flags["canonical.entered_mystic_space"] is True
    assert state.characters[NIGHT].location_id == YEFU


def test_repeated_pure_dialogue_plan_is_rejected_as_stagnant():
    state = build_canonical_start_state()
    observation = build_game_observation(
        state,
        NIGHT,
        create_core_tool_registry(),
        metadata={
            "runtime_context": {
                "recent_committed_events": [
                    {
                        "event_type": "tool.talk_to",
                        "actor_ids": [NIGHT],
                        "target_ids": [QINGQING, SCENE_ID],
                        "summary": "repeated dialogue",
                    },
                    {
                        "event_type": "tool.talk_to",
                        "actor_ids": [NIGHT],
                        "target_ids": [QINGQING, SCENE_ID],
                        "summary": "repeated dialogue",
                    },
                ]
            }
        },
    )
    draft = ActorNarrativePlan.parse_obj(
        {
            "actor_id": NIGHT,
            "intent": "继续重复争辩",
            "steps": [
                {
                    "step_id": "repeat",
                    "tool_name": "talk_to",
                    "arguments": {
                        "target_character_id": QINGQING,
                        "message": "重复同一轮争辩。",
                        "tone": "冷静",
                    },
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="stagnant dialogue loop"):
        _reject_stagnant_dialogue_loop(draft, observation)


def test_ready_ability_rejects_pure_dialogue_after_one_matching_turn():
    state = build_canonical_start_state()
    state.characters[NIGHT].location_id = MYSTIC_SPACE
    state.characters[JIYUE].location_id = MYSTIC_SPACE
    state.flags["canonical.entered_mystic_space"] = True
    state.flags["canonical.jiyue_revealed"] = True
    observation = build_game_observation(
        state,
        JIYUE,
        create_core_tool_registry(),
        metadata={
            "runtime_context": {
                "recent_committed_events": [
                    {
                        "event_type": "tool.talk_to",
                        "actor_ids": [JIYUE],
                        "target_ids": [NIGHT, MYSTIC_SPACE],
                        "summary": "姬月已经解释过一次",
                    }
                ]
            }
        },
    )
    assert OPEN_SANSHENG_SPRING in {
        item.ability_id for item in observation.available_abilities
    }
    draft = ActorNarrativePlan.parse_obj(
        {
            "actor_id": JIYUE,
            "intent": "继续解释而不推进状态",
            "steps": [
                {
                    "step_id": "repeat_explanation",
                    "tool_name": "talk_to",
                    "arguments": {
                        "target_character_id": NIGHT,
                        "message": "我再重复解释一次。",
                        "tone": "平静",
                    },
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="stagnant dialogue loop"):
        _reject_stagnant_dialogue_loop(draft, observation)


def test_real_planner_retries_stagnant_jiyue_dialogue_with_ability_instruction():
    state = build_canonical_start_state()
    state.characters[NIGHT].location_id = YEFU
    state.flags["canonical.lin_warning_done"] = True
    registry = create_core_tool_registry()
    observation = build_game_observation(
        state,
        JIYUE,
        registry,
        world_package_id="first_crazy_ch1_5",
        scenario_family="canonical_reconstruction",
        metadata={
            "runtime_context": {
                "recent_committed_events": [
                    {
                        "event_type": "tool.talk_to",
                        "actor_ids": [JIYUE],
                        "target_ids": [NIGHT, MYSTIC_SPACE],
                        "summary": "姬月已经解释过一次",
                    }
                ]
            }
        },
    )
    ability_id = observation.available_abilities[0].ability_id
    calls = []

    def provider(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        if len(calls) == 1:
            return {
                "actor_id": JIYUE,
                "intent": "继续解释",
                "steps": [{
                    "step_id": "repeat",
                    "tool_name": "talk_to",
                    "arguments": {
                        "target_character_id": NIGHT,
                        "message": "我再重复解释一次。",
                        "tone": "平静",
                    },
                }],
            }, "raw-1", "response-1", "test-model"
        return {
            "actor_id": JIYUE,
            "intent": "推进异空间联系",
            "steps": [{
                "step_id": "invoke",
                "tool_name": "invoke_ability",
                "arguments": {"ability_id": ability_id},
            }],
        }, "raw-2", "response-2", "test-model"

    planner = RealLLMNarrativePlanner(
        registry,
        generator=None,
        max_attempts=2,
        world_package_id="first_crazy_ch1_5",
        scenario_family="canonical_reconstruction",
    )
    planner._call_provider = provider
    plan = planner.generate(
        state,
        [JIYUE],
        beat_goal="推进姬月与夜轻歌的关系",
        goal_id="jiyue-retry",
        permissions_by_actor={JIYUE: CORE_TOOL_PERMISSIONS},
        metadata={
            "recent_committed_events": [
                {
                    "event_type": "tool.talk_to",
                    "actor_ids": [JIYUE],
                    "target_ids": [NIGHT, MYSTIC_SPACE],
                    "summary": "姬月已经解释过一次",
                }
            ]
        },
    )

    assert plan.actor_chains[JIYUE].steps[0].tool_call.tool_name == "invoke_ability"
    assert len(calls) == 2
    assert "stagnant dialogue loop" in calls[1]["kwargs"]["validation_error"]
    retry_messages = _actor_plan_messages(
        calls[1]["args"][0],
        calls[1]["args"][1],
        calls[1]["args"][2],
        calls[1]["args"][3],
        calls[1]["args"][4],
        max_steps=3,
        validation_error=calls[1]["kwargs"]["validation_error"],
    )
    retry_payload = json.loads(retry_messages[1]["content"])
    assert retry_payload["validation_repair"]["category"] == "stagnant_dialogue"
    assert retry_payload["validation_repair"]["available_ability_ids"] == [ability_id]
    assert retry_payload["validation_repair"]["forbidden_tool_names"] == ["talk_to"]
    assert planner.call_traces[0].error_category == "stagnant_dialogue"


def test_stagnant_dialogue_without_ability_forbids_talk_on_retry():
    state = build_canonical_start_state()
    registry = create_core_tool_registry()
    observation = build_game_observation(
        state,
        QINGQING,
        registry,
        world_package_id="first_crazy_ch1_5",
        scenario_family="canonical_reconstruction",
        metadata={
            "runtime_context": {
                "recent_committed_events": [
                    {
                        "event_type": "tool.talk_to",
                        "actor_ids": [QINGQING],
                        "target_ids": [NIGHT, SCENE_ID],
                        "summary": "夜清清已经重复交涉",
                    },
                    {
                        "event_type": "tool.talk_to",
                        "actor_ids": [QINGQING],
                        "target_ids": [NIGHT, SCENE_ID],
                        "summary": "夜清清再次重复交涉",
                    },
                ]
            }
        },
    )

    assert not observation.available_abilities
    definitions = [
        definition
        for name in registry.names()
        for definition in [registry.get(name)]
        if definition is not None
        and name in {item.name for item in observation.available_tools}
    ]
    messages = _actor_plan_messages(
        QINGQING,
        observation,
        definitions,
        "依据角色目标推进下一段剧情",
        None,
        max_steps=3,
        validation_error=(
            "ValueError: stagnant dialogue loop: choose a state-progressing "
            "action or a new target"
        ),
    )
    payload = json.loads(messages[1]["content"])
    repair = payload["validation_repair"]

    assert repair["category"] == "stagnant_dialogue"
    assert repair["forbidden_tool_names"] == ["talk_to"]
    assert "talk_to" not in repair["available_non_dialogue_tools"]
    assert "available_ability_ids" not in repair
    assert "不得再次输出 talk_to" in messages[0]["content"]


def test_real_planner_keeps_failing_after_stagnant_dialogue_without_fallback():
    state = build_canonical_start_state()
    state.characters[NIGHT].location_id = YEFU
    state.flags["canonical.lin_warning_done"] = True
    registry = create_core_tool_registry()
    calls = []

    def provider(*_args, **_kwargs):
        calls.append(True)
        return {
            "actor_id": JIYUE,
            "intent": "继续解释",
            "steps": [{
                "step_id": "repeat",
                "tool_name": "talk_to",
                "arguments": {
                    "target_character_id": NIGHT,
                    "message": "我再重复解释一次。",
                    "tone": "平静",
                },
            }],
        }, "raw", "response", "test-model"

    planner = RealLLMNarrativePlanner(
        registry,
        generator=None,
        max_attempts=2,
        world_package_id="first_crazy_ch1_5",
        scenario_family="canonical_reconstruction",
    )
    planner._call_provider = provider
    with pytest.raises(NarrativePlannerError, match="stagnant dialogue loop"):
        planner.generate(
            state,
            [JIYUE],
            beat_goal="推进姬月与夜轻歌的关系",
            goal_id="jiyue-fail",
            permissions_by_actor={JIYUE: CORE_TOOL_PERMISSIONS},
            metadata={
                "recent_committed_events": [{
                    "event_type": "tool.talk_to",
                    "actor_ids": [JIYUE],
                    "target_ids": [NIGHT, MYSTIC_SPACE],
                    "summary": "姬月已经解释过一次",
                }]
            },
        )
    assert len(calls) == 2
    assert not planner.call_traces[-1].success
    assert planner.call_traces[-1].error_category == "stagnant_dialogue"


def test_dialogue_cannot_claim_item_owned_by_another_character():
    state = build_canonical_start_state()
    state.characters[NIGHT].location_id = YEFU
    state.items[OUTER_ROBE].owner_id = LIN
    state.characters[QINGQING].inventory = []
    state.characters[LIN].inventory = [OUTER_ROBE]
    observation = build_game_observation(
        state,
        NIGHT,
        create_core_tool_registry(),
    )
    draft = ActorNarrativePlan.parse_obj(
        {
            "actor_id": NIGHT,
            "intent": "错误声称持有外衫",
            "steps": [
                {
                    "step_id": "claim_robe",
                    "tool_name": "talk_to",
                    "arguments": {
                        "target_character_id": LIN,
                        "message": "这件外衫明明在我手中。",
                        "tone": "冷静",
                    },
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="claims possession"):
        _reject_invalid_immediate_action(draft, observation)


def test_canonical_pipeline_keeps_hidden_events_out_of_planner_and_replays():
    case = load_canonical_case()
    captured_inputs = []

    def generator(actor_id, observation, _tools, _goal, feedback):
        serialized = json.dumps(observation.dict(), ensure_ascii=False)
        captured_inputs.append(serialized)
        location = observation.actor_location_id
        memories = " ".join(item.content for item in observation.memories)
        if actor_id == NIGHT:
            if feedback is not None:
                remaining = observation.metadata["runtime_context"].get(
                    "remaining_failed_steps", []
                )
                target = QINGQING
                if remaining:
                    arguments = remaining[0].get("tool_call", {}).get("arguments", {})
                    target = arguments.get("target_character_id", target)
                return {
                    "actor_id": actor_id,
                    "intent": "追上临时离场的目标并完成原计划",
                    "steps": [
                        {
                            "step_id": "follow_target",
                            "tool_name": "move_to",
                            "arguments": {"destination_id": target},
                        },
                        {
                            "step_id": "take_robe_after_follow",
                            "tool_name": "take_item",
                            "arguments": {
                                "item_id": OUTER_ROBE,
                                "target_character_id": QINGQING,
                            },
                        },
                        {
                            "step_id": "return_yefu_after_repair",
                            "tool_name": "move_to",
                            "arguments": {"destination_id": YEFU},
                        },
                    ],
                    "stop_conditions": ["恢复体面并返回夜府"],
                }
            if location == SCENE_ID:
                return {
                    "actor_id": actor_id,
                    "intent": "反制诬陷并返回夜府",
                    "steps": [
                        {
                            "step_id": "rebuke",
                            "tool_name": "talk_to",
                            "arguments": {
                                "target_character_id": QINGQING,
                                "message": "庶出不可污蔑嫡姐。",
                                "tone": "冷静",
                            },
                        },
                        {
                            "step_id": "take_robe",
                            "tool_name": "take_item",
                            "arguments": {
                                "item_id": OUTER_ROBE,
                                "target_character_id": QINGQING,
                            },
                        },
                        {
                            "step_id": "return_yefu",
                            "tool_name": "move_to",
                            "arguments": {"destination_id": YEFU},
                        },
                    ],
                    "stop_conditions": ["离开围观"],
                }
            if location in {MYSTIC_SPACE, SANSHENG_SPRING}:
                return _talk(actor_id, JIYUE, "询问姬月的目的与丹田修复条件")
            if location == FENGYUE_PAVILION and "夜家大堂" in memories:
                return {
                    "actor_id": actor_id,
                    "intent": "回应传唤",
                    "steps": [
                        {
                            "step_id": "enter_hall",
                            "tool_name": "move_to",
                            "arguments": {"destination_id": YE_CLAN_HALL},
                        }
                    ],
                    "stop_conditions": ["抵达夜家大堂"],
                }
            if location == FENGYUE_PAVILION:
                return _talk(actor_id, LUHE, "询问传唤缘由")
            if location == YEFU:
                return _talk(actor_id, LIN, "警告林管家不得继续参与陷害")
        if actor_id == QINGQING:
            if location != state_location(observation, NIGHT):
                return _move(actor_id, NIGHT, "追上夜轻歌")
            return _talk(actor_id, NIGHT, "继续维持指控并试探夜轻歌")
        if actor_id == JIYUE:
            if observation.available_abilities:
                return _ability(
                    actor_id,
                    observation.available_abilities[0].ability_id,
                    "执行当前可用的异空间能力",
                )
            visible_ids = {
                character.character_id
                for character in observation.visible_characters
            }
            if NIGHT not in visible_ids:
                return {
                    "actor_id": actor_id,
                    "intent": "等待异空间联系前置条件",
                    "steps": [],
                    "stop_conditions": ["等待"],
                }
            if location == MYSTIC_SPACE:
                return _talk(actor_id, NIGHT, "解释蛊毒与受控处境")
            if location == SANSHENG_SPRING:
                return _talk(actor_id, NIGHT, "承诺帮助修复丹田")
            return _talk(actor_id, NIGHT, "确认互利约定")
        if actor_id == LUHE:
            if observation.available_abilities:
                return _ability(actor_id, ISSUE_HALL_SUMMONS, "传达家主命令")
            return _talk(actor_id, NIGHT, "传达立即前往夜家大堂的命令")
        return {"actor_id": actor_id, "intent": "观察", "steps": [], "stop_conditions": []}

    report = asyncio.run(
        run_canonical_case(case, generator=generator, mode="perturbed")
    )
    assert report.real_llm is False
    assert report.fallback_count == 0
    assert report.future_event_leakage_detected is False
    assert report.plan_invalidation_count == 1
    assert report.replan_count >= 1
    assert report.fragment_completed is True
    assert report.replay_consistent is True
    assert report.illegal_commit_count == 0
    assert report.final_world_version > report.initial_world_version
    hidden_ids = {item.event_id for item in case.canonical_events}
    assert all(not any(event_id in value for event_id in hidden_ids) for value in captured_inputs)
    assert report.alignment.metrics.matched_event_count >= 5
    assert report.source_attribution.canonical_recall_by_source.get(
        "environment", 0.0
    ) == 0.0
    assert report.source_attribution.canonical_recall_by_source.get(
        "llm_tool", 0.0
    ) == report.alignment.metrics.weighted_event_recall

    clean_report = asyncio.run(
        run_canonical_case(case, generator=generator, mode="clean")
    )
    assert clean_report.evaluation_mode == "clean"
    assert clean_report.plan_invalidation_count == 0
    assert clean_report.fragment_completed is True
    assert clean_report.source_attribution.canonical_recall_by_source.get(
        "environment", 0.0
    ) == 0.0


def _talk(actor_id, target_id, message):
    return {
        "actor_id": actor_id,
        "intent": message,
        "steps": [
            {
                "step_id": "talk",
                "tool_name": "talk_to",
                "arguments": {
                    "target_character_id": target_id,
                    "message": message,
                    "tone": "克制",
                },
            }
        ],
        "stop_conditions": ["完成交流"],
    }


def _move(actor_id, destination_id, intent):
    return {
        "actor_id": actor_id,
        "intent": intent,
        "steps": [
            {
                "step_id": "move",
                "tool_name": "move_to",
                "arguments": {"destination_id": destination_id},
            }
        ],
        "stop_conditions": ["抵达目标"],
    }


def _ability(actor_id, ability_id, intent):
    return {
        "actor_id": actor_id,
        "intent": intent,
        "steps": [
            {
                "step_id": "invoke_ability",
                "tool_name": "invoke_ability",
                "arguments": {"ability_id": ability_id},
            }
        ],
        "stop_conditions": ["能力效果已由世界引擎提交"],
    }


def state_location(observation, actor_id):
    for character in observation.visible_characters:
        if character.character_id == actor_id:
            return character.location_id
    return None
