"""Collect replayable Scripted and Safe-Heuristic expert rollouts."""

from __future__ import annotations

import asyncio
from typing import Iterable, List, Optional

from engine import (
    CORE_TOOL_PERMISSIONS,
    GameTrajectory,
    GameTrajectoryRecorder,
    PlannerDecision,
    SceneConfig,
    SceneController,
    SceneMode,
    ScriptBeat,
    build_game_observation,
    create_core_tool_registry,
)

from .scenario_generator import (
    GeneratedScenario,
    ScenarioFamily,
    evaluate_scenario,
)


async def collect_scripted_trajectory_async(
    scenario: GeneratedScenario,
    *,
    code_commit: str = "",
) -> GameTrajectory:
    return await _collect_trajectory_async(
        scenario,
        scenario.scripted_calls,
        policy_id="scripted_expert",
        source_type="scripted_expert",
        route_id="scripted",
        code_commit=code_commit,
    )


async def collect_heuristic_trajectory_async(
    scenario: GeneratedScenario,
    *,
    code_commit: str = "",
) -> GameTrajectory:
    return await _collect_trajectory_async(
        scenario,
        build_safe_heuristic_calls(scenario),
        policy_id="safe_heuristic",
        source_type="safe_heuristic",
        route_id="heuristic",
        code_commit=code_commit,
    )


async def _collect_trajectory_async(
    scenario: GeneratedScenario,
    calls,
    *,
    policy_id: str,
    source_type: str,
    route_id: str,
    code_commit: str,
) -> GameTrajectory:
    registry = create_core_tool_registry()
    controller = SceneController(
        registry,
        permissions=CORE_TOOL_PERMISSIONS,
    )
    config = SceneConfig(
        scene_id=scenario.scenario_id,
        mode=SceneMode.script,
        location_id=scenario.initial_state.current_scene_id,
        participant_ids=list(scenario.participant_ids),
        objective=scenario.objective,
        max_turns=scenario.max_turns,
        random_seed=scenario.random_seed,
        allow_multi_location=(
            len({
                scenario.initial_state.characters[character_id].location_id
                for character_id in scenario.participant_ids
            }) > 1
        ),
        script_beats=[
            ScriptBeat(
                beat_id="step_%03d" % index,
                actor_id=call.actor_id,
                tool_name=call.tool_name,
                arguments=dict(call.arguments),
            )
            for index, call in enumerate(calls, start=1)
        ],
    )
    run = await controller.run(
        scenario.initial_state,
        config,
        ending_evaluator=lambda state: evaluate_scenario(scenario, state),
    )
    recorder = GameTrajectoryRecorder(
        scenario.initial_state,
        episode_id="%s:%s" % (scenario.scenario_id, route_id),
        world_package_id=scenario.world_package_id,
        scenario_family=scenario.scenario_family.value,
        variant_id=scenario.variant_id,
        random_seed=scenario.random_seed,
        policy_id=policy_id,
        code_commit=code_commit,
        source_type=source_type,
        metadata={
            "scenario_content_hash": scenario.content_hash,
            "template_version": scenario.template_version,
            "license_spdx": scenario.license_spdx,
            "route_id": route_id,
        },
    )
    current = scenario.initial_state
    for outcome in run.outcomes:
        call = outcome.execution.active_call
        observation = build_game_observation(
            current,
            call.actor_id,
            registry,
            world_package_id=scenario.world_package_id,
            scenario_family=scenario.scenario_family.value,
        )
        decision = PlannerDecision.from_tool_call(
            call,
            policy_id=policy_id,
            reason_summary="generated scenario %s" % source_type,
        )
        recorder.record(observation, decision, outcome)
        current = outcome.new_state
    return recorder.finish(
        ending_id=run.summary.ending_id or run.summary.status.value,
        objective_satisfied=run.summary.objective_satisfied,
    )


def collect_scripted_trajectory(
    scenario: GeneratedScenario,
    *,
    code_commit: str = "",
) -> GameTrajectory:
    return asyncio.run(
        collect_scripted_trajectory_async(
            scenario,
            code_commit=code_commit,
        )
    )


def collect_heuristic_trajectory(
    scenario: GeneratedScenario,
    *,
    code_commit: str = "",
) -> GameTrajectory:
    return asyncio.run(
        collect_heuristic_trajectory_async(
            scenario,
            code_commit=code_commit,
        )
    )


def collect_scripted_trajectories(
    scenarios: Iterable[GeneratedScenario],
    *,
    code_commit: str = "",
    limit: Optional[int] = None,
) -> List[GameTrajectory]:
    selected = list(scenarios)
    if limit is not None:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        selected = selected[:limit]
    return [
        collect_scripted_trajectory(
            scenario,
            code_commit=code_commit,
        )
        for scenario in selected
    ]


def collect_expert_trajectories(
    scenarios: Iterable[GeneratedScenario],
    *,
    code_commit: str = "",
    include_scripted: bool = True,
    include_heuristic: bool = True,
) -> List[GameTrajectory]:
    if not include_scripted and not include_heuristic:
        raise ValueError("at least one expert policy must be enabled")
    trajectories: List[GameTrajectory] = []
    for scenario in scenarios:
        if include_scripted:
            trajectories.append(collect_scripted_trajectory(
                scenario,
                code_commit=code_commit,
            ))
        if include_heuristic:
            trajectories.append(collect_heuristic_trajectory(
                scenario,
                code_commit=code_commit,
            ))
    return trajectories


def build_safe_heuristic_calls(
    scenario: GeneratedScenario,
) -> List:
    """Return a legal route that is semantically distinct from Scripted."""

    variant = int(scenario.variant_id.rsplit("v", 1)[-1])
    seed = scenario.random_seed
    prefix = "heuristic_%s_%03d_%06d" % (
        scenario.scenario_family.value,
        variant,
        seed,
    )
    if scenario.scenario_family == ScenarioFamily.secret_transport:
        guard = "char_guard"
        steward = "char_steward"
        ally = "char_ally"
        letter = "item_sealed_letter"
        fact = "fact_regent_plot"
        return [
            _tool_call(prefix, 1, guard, "pick_up", {"item_id": letter}),
            _tool_call(
                prefix,
                2,
                guard,
                "give_item",
                {"target_character_id": steward, "item_id": letter},
            ),
            _tool_call(prefix, 3, steward, "observe", {"fact_id": fact}),
            _tool_call(
                prefix,
                4,
                steward,
                "share_information",
                {"target_character_id": ally, "fact_id": fact},
            ),
            _tool_call(
                prefix,
                5,
                steward,
                "propose_alliance",
                {
                    "target_character_id": ally,
                    "goal_key": "protect_estate",
                    "shared_fact_id": fact,
                },
            ),
        ]
    if scenario.scenario_family == ScenarioFamily.resource_negotiation:
        actor = "char_quartermaster"
        target = "char_settlement_leader"
        item_id = "item_relief_resource"
        return [
            _tool_call(prefix, 1, actor, "pick_up", {"item_id": item_id}),
            _tool_call(
                prefix,
                2,
                actor,
                "talk_to",
                {
                    "target_character_id": target,
                    "message": "请确认领取人、公共用途与分配记录后接收物资。",
                    "tone": "审慎协商",
                },
            ),
            _tool_call(
                prefix,
                3,
                actor,
                "give_item",
                {"target_character_id": target, "item_id": item_id},
            ),
        ]
    actor = "char_rescuer"
    patient = "char_patient"
    medicine = "item_rescue_medicine"
    infirmary = "loc_field_infirmary"
    return [
        _tool_call(prefix, 1, actor, "pick_up", {"item_id": medicine}),
        _tool_call(
            prefix,
            2,
            actor,
            "move_to",
            {"destination_id": infirmary},
        ),
        _tool_call(
            prefix,
            3,
            actor,
            "talk_to",
            {
                "target_character_id": patient,
                "message": "我先核对你的身份与伤情，再交付对应药物。",
                "tone": "冷静安抚",
            },
        ),
        _tool_call(
            prefix,
            4,
            actor,
            "give_item",
            {"target_character_id": patient, "item_id": medicine},
        ),
    ]


def _tool_call(prefix, index, actor_id, tool_name, arguments):
    from engine import ToolCall

    return ToolCall(
        call_id="%s_%02d_%s" % (prefix, index, tool_name),
        actor_id=actor_id,
        tool_name=tool_name,
        arguments=arguments,
    )
