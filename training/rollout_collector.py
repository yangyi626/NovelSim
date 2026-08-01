"""Collect replayable scripted expert rollouts from generated scenarios."""

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

from .scenario_generator import GeneratedScenario, evaluate_scenario


async def collect_scripted_trajectory_async(
    scenario: GeneratedScenario,
    *,
    code_commit: str = "",
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
            for index, call in enumerate(scenario.scripted_calls, start=1)
        ],
    )
    run = await controller.run(
        scenario.initial_state,
        config,
        ending_evaluator=lambda state: evaluate_scenario(scenario, state),
    )
    recorder = GameTrajectoryRecorder(
        scenario.initial_state,
        episode_id="%s:scripted" % scenario.scenario_id,
        world_package_id=scenario.world_package_id,
        scenario_family=scenario.scenario_family.value,
        variant_id=scenario.variant_id,
        random_seed=scenario.random_seed,
        policy_id="scripted_expert",
        code_commit=code_commit,
        source_type="scripted_expert",
        metadata={
            "scenario_content_hash": scenario.content_hash,
            "template_version": scenario.template_version,
            "license_spdx": scenario.license_spdx,
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
            policy_id="scripted_expert",
            reason_summary="generated scenario scripted expert",
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
