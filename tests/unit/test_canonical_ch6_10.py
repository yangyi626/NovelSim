"""第 6–10 章 checkpoint 内容完整性测试。"""

from engine import (
    ActionStep,
    ActorActionChain,
    JointPlan,
    ToolCall,
    create_plan_runtime,
)
from engine.agent_tools import create_core_tool_registry
from engine.joint_plan import check_plan_validity
from examples.huarong_lane.canonical_ch6_10 import (
    build_canonical_ch5_start_state,
)
from world_schema import OperationKind


def test_every_belief_fact_id_exists_in_authoritative_facts():
    """信念与计划引用的 fact_id 必须能解析到权威事实，否则规划校验
    会以 missing_fact 拒绝整条计划（真实踩坑：fact_qingqing_poisoned_tea）。
    """

    state = build_canonical_ch5_start_state()

    referenced = {
        belief.fact_id
        for beliefs in state.beliefs.values()
        for belief in beliefs
    }
    assert referenced, "checkpoint 应至少包含原著知识信念"
    missing = sorted(referenced - set(state.facts))
    assert missing == []


def test_ability_and_dialogue_flags_do_not_collide():
    state = build_canonical_ch5_start_state()
    flags = [
        spec["completion_flag"]
        for spec in state.flags["runtime.ability_specs"].values()
    ] + [
        effect["completion_flag"]
        for effect in state.flags["runtime.dialogue_effects"]
    ]

    assert len(flags) == len(set(flags))
    assert all(flag.startswith("canonical.") for flag in flags)


def test_chapter_goals_are_bounded_within_ch6_10():
    state = build_canonical_ch5_start_state()
    for psyche in state.character_psyches.values():
        for goal in psyche.goals:
            if goal.timeline_id != "canon_first_crazy_ch6_10":
                continue
            assert goal.scope == "chapter"
            assert 6 <= goal.introduced_chapter <= 10
            assert goal.terminal_chapter is None or (
                6 <= goal.terminal_chapter <= 10
            )


def test_trusted_effects_only_write_canonical_namespace():
    state = build_canonical_ch5_start_state()
    flag_ops = [
        operation.path
        for spec in state.flags["runtime.ability_specs"].values()
        for operation in _ability_flag_operations(spec)
    ]
    assert all(path.startswith("canonical.") for path in flag_ops)


def _ability_flag_operations(spec):
    from world_schema import Operation, OperationKind

    completion = str(spec.get("completion_flag") or "")
    return (
        [Operation(op=OperationKind.set_flag, path=completion)]
        if completion
        else []
    )


def test_plan_referencing_poison_fact_is_not_stale():
    """回归：夜正熊规划曾引用 fact_qingqing_poisoned_tea 触发
    missing_fact，两次重规划耗尽后停在 stale。checkpoint 现已把该事实
    登记为权威事实，引用它的计划必须仍然有效。
    """

    state = build_canonical_ch5_start_state()
    registry = create_core_tool_registry()
    plan_actor = "char_yezhengxiong"
    plan = ActorPlanStub(plan_actor)
    runtime = create_plan_runtime(plan.plan)

    result = check_plan_validity(
        plan.plan,
        runtime,
        state,
        registry,
        permissions_by_actor={plan_actor: {"knowledge.share"}},
    )

    assert result.status.name == "valid", result.reasons
    assert not any(
        reason.startswith("missing_fact") for reason in (result.reasons or [])
    )


class ActorPlanStub:
    """构造一条只做 share_information 的两步计划。"""

    def __init__(self, actor_id):
        self.actor_id = actor_id
        self.plan = JointPlan(
            goal_id="goal_test_share",
            base_world_version=0,
            actor_chains={
                actor_id: ActorActionChain(
                    actor_id=actor_id,
                    steps=[
                        ActionStep(
                            step_id="share_poison_fact",
                            tool_call=ToolCall(
                                actor_id=actor_id,
                                tool_name="share_information",
                                arguments={
                                    "target_character_id": "char_yeqingge",
                                    "fact_id": "fact_qingqing_poisoned_tea",
                                },
                            ),
                        )
                    ],
                )
            },
            metadata={"beat_goal": "test"},
        )
