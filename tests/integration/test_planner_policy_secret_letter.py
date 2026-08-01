import asyncio

import pytest

from engine import (
    CORE_TOOL_PERMISSIONS,
    PlannerIntent,
    PlannerPolicyConfig,
    PlannerPolicyKind,
    PlannerPolicyRouter,
    PlannerPolicySceneSelector,
    PromptedLLMPolicy,
    ReActPolicy,
    SceneConfig,
    SceneController,
    SceneMode,
    SceneStatus,
    ScriptedPolicy,
    ToolCall,
    create_core_tool_registry,
)
from examples.secret_letter import (
    ALLY,
    FACT_PLOT,
    GOAL_PROTECT,
    GUARD,
    LETTER,
    PLAYER,
    STEWARD,
    build_snapshot,
    evaluate_ending,
    next_autonomous_call,
)


def _actor_selector(state, turn_index):
    next_call = next_autonomous_call(state, turn_index)
    return next_call.actor_id if next_call is not None else None


def _grounded_secret_letter_decision(observation, tools):
    sequence = observation.world_version + 1
    calls = {
        0: (GUARD, "pick_up", {"item_id": LETTER}, PlannerIntent.interact),
        1: (GUARD, "observe", {"fact_id": FACT_PLOT}, PlannerIntent.observe),
        2: (
            GUARD,
            "share_information",
            {"target_character_id": STEWARD, "fact_id": FACT_PLOT},
            PlannerIntent.share,
        ),
        3: (
            STEWARD,
            "share_information",
            {"target_character_id": ALLY, "fact_id": FACT_PLOT},
            PlannerIntent.share,
        ),
        4: (
            STEWARD,
            "propose_alliance",
            {
                "target_character_id": ALLY,
                "goal_key": GOAL_PROTECT,
                "shared_fact_id": FACT_PLOT,
            },
            PlannerIntent.ally,
        ),
    }
    if observation.world_version not in calls:
        return None
    actor_id, tool_name, arguments, intent = calls[observation.world_version]
    assert observation.actor_id == actor_id
    return {
        "actor_id": actor_id,
        "intent": intent.value,
        "tool_call": ToolCall(
            call_id="planner_secret_%02d_%s" % (sequence, tool_name),
            actor_id=actor_id,
            tool_name=tool_name,
            arguments=arguments,
        ).dict(),
        "confidence": 1.0,
        "reason_summary": "grounded deterministic integration fixture",
    }


@pytest.mark.parametrize(
    "active_policy",
    [
        PlannerPolicyKind.scripted,
        PlannerPolicyKind.prompt,
        PlannerPolicyKind.react,
    ],
)
def test_secret_letter_switches_policy_without_changing_runtime(active_policy):
    registry = create_core_tool_registry()
    policies = {
        PlannerPolicyKind.scripted: ScriptedPolicy(
            _grounded_secret_letter_decision
        ),
        PlannerPolicyKind.prompt: PromptedLLMPolicy(
            _grounded_secret_letter_decision
        ),
        PlannerPolicyKind.react: ReActPolicy(
            _grounded_secret_letter_decision
        ),
    }
    router = PlannerPolicyRouter(
        policies,
        config=PlannerPolicyConfig(active_policy=active_policy),
    )
    selector = PlannerPolicySceneSelector(
        router,
        registry,
        _actor_selector,
        world_package_id="secret_letter_v1",
        scenario_family="secret_transport",
    )
    controller = SceneController(
        registry,
        permissions=CORE_TOOL_PERMISSIONS,
    )
    try:
        run = asyncio.run(
            controller.run(
                build_snapshot(),
                SceneConfig(
                    scene_id="scene_secret_letter_policy",
                    mode=SceneMode.free,
                    participant_ids=[PLAYER, GUARD, STEWARD, ALLY],
                    objective="阻止密信中的阴谋并形成可信联盟",
                    max_turns=8,
                    random_seed=20260801,
                ),
                free_selector=selector,
                ending_evaluator=evaluate_ending,
            )
        )
    finally:
        router.close()

    assert run.summary.status == SceneStatus.completed
    assert run.summary.objective_satisfied is True
    assert run.state.version == 5
    assert run.summary.tool_sequence == [
        "pick_up",
        "observe",
        "share_information",
        "share_information",
        "propose_alliance",
    ]
    assert len(selector.decisions) == 5
    assert {decision.policy_id for decision in selector.decisions} == {
        active_policy.value
    }
    assert all(outcome.result.success for outcome in run.outcomes)

