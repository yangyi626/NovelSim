"""End-to-end canon reconstruction with a real LLM narrative planner.

Pipeline:
source-backed checkpoint -> real LLM joint plan -> guarded multi-agent tools
-> authoritative state changes -> injected dependency change -> real LLM local
repair -> terminal fragment -> hidden-canon alignment.

The canonical event list is loaded into a separate evaluator object and is
never passed to ``RealLLMNarrativePlanner.generate`` or ``replan``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence

from pydantic import BaseModel, Field

from engine.agent_tools import CORE_TOOL_PERMISSIONS, ToolDefinition, create_core_tool_registry
from engine.event import commit_event
from engine.game_observation import authoritative_state_hash, ready_ability_ids
from engine.joint_plan import (
    ActionStep,
    JointPlan,
    JointPlanExecutor,
    JointPlanTrigger,
    PlanRuntimeState,
    PlanRuntimeStatus,
    create_plan_runtime,
)
from engine.llm_telemetry import LLMCallUsage, LLMUsageSummary, capture_llm_usage
from engine.narrative_planner import (
    NarrativePlannerCallTrace,
    NarrativeResponseGenerator,
    RealLLMNarrativePlanner,
)
from engine.trajectory_eval import evaluate_trajectory
from examples.huarong_lane.canonical_case import (
    FENGYUE_PAVILION,
    JIYUE,
    LUHE,
    MYSTIC_SPACE,
    SANSHENG_SPRING,
    YE_CLAN_HALL,
    YEFU,
    build_canonical_start_state,
)
from examples.huarong_lane.scenario import LIN, NIGHT, QINGQING, SCENE_ID
from world_schema import Operation, OperationKind, StatePatch, WorldEvent, WorldState

from .canonical_alignment import (
    CanonicalAlignmentReport,
    CanonicalEventAnchor,
    align_canonical_events,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASE = (
    PROJECT_ROOT
    / "evaluation"
    / "canonical_cases"
    / "first_crazy_ch1_5.json"
)
DEFAULT_REPORT = (
    PROJECT_ROOT
    / "evaluation"
    / "reports"
    / "canonical-ch1-5-real-v10-clean.json"
)
DEFAULT_MARKDOWN = (
    PROJECT_ROOT
    / "evaluation"
    / "reports"
    / "canonical-ch1-5-real-v10-clean.md"
)


class _StrictModel(BaseModel):
    class Config:
        extra = "forbid"


class CanonicalPlanningConfig(_StrictModel):
    goal_id: str
    beat_goal: str
    max_beats: int = Field(5, ge=1, le=20)
    max_ticks_per_beat: int = Field(5, ge=1, le=20)
    max_replans_per_beat: int = Field(2, ge=0, le=5)
    max_active_actors: int = Field(3, ge=1, le=6)


class CanonicalNovelCase(_StrictModel):
    schema_version: int
    case_id: str
    novel_id: str
    novel_title: str
    source_fingerprint: str
    start_checkpoint: str
    target_chapters: List[int]
    planning: CanonicalPlanningConfig
    canonical_events: List[CanonicalEventAnchor]


class PipelineStage(_StrictModel):
    name: str
    status: str
    details: Dict[str, Any] = Field(default_factory=dict)


class SimulatedEventRecord(_StrictModel):
    event_id: str
    event_type: str
    world_version: int
    actor_ids: List[str]
    target_ids: List[str]
    summary: str
    source: str


class EventSourceAttribution(_StrictModel):
    total_canonical_weight: float = Field(ge=0.0)
    matched_weight: float = Field(ge=0.0)
    matched_event_count_by_source: Dict[str, int] = Field(default_factory=dict)
    matched_weight_by_source: Dict[str, float] = Field(default_factory=dict)
    canonical_recall_by_source: Dict[str, float] = Field(default_factory=dict)


class CanonicalNovelReport(_StrictModel):
    schema_version: str = "canonical_novel_report.v2"
    evaluation_mode: str = "perturbed"
    case_id: str
    novel_title: str
    target_chapters: List[int]
    source_fingerprint: str
    real_llm: bool
    fallback_count: int = 0
    future_event_leakage_detected: bool = False
    initial_state_hash: str
    final_state_hash: str
    initial_world_version: int
    final_world_version: int
    beat_count: int = Field(ge=0)
    execution_tick_count: int = Field(ge=0)
    plan_count: int = Field(ge=0)
    plan_invalidation_count: int = Field(ge=0)
    replan_count: int = Field(ge=0)
    fragment_completed: bool
    replay_consistent: bool
    illegal_commit_count: int = Field(0, ge=0)
    stages: List[PipelineStage]
    planner_traces: List[NarrativePlannerCallTrace]
    llm_calls: List[LLMCallUsage]
    llm_usage: LLMUsageSummary
    simulated_events: List[SimulatedEventRecord]
    triggers: List[JointPlanTrigger]
    alignment: CanonicalAlignmentReport
    source_attribution: EventSourceAttribution = Field(
        default_factory=lambda: EventSourceAttribution(
            total_canonical_weight=0.0,
            matched_weight=0.0,
        )
    )


def load_canonical_case(path: Path = DEFAULT_CASE) -> CanonicalNovelCase:
    payload = json.loads(path.read_text(encoding="utf-8"))
    case = CanonicalNovelCase.parse_obj(payload)
    if case.schema_version != 1:
        raise ValueError("only canonical case schema_version=1 is supported")
    return case


async def run_canonical_case(
    case: CanonicalNovelCase,
    *,
    generator: Optional[NarrativeResponseGenerator] = None,
    model: Optional[str] = None,
    mode: Literal["clean", "perturbed"] = "clean",
) -> CanonicalNovelReport:
    initial = build_canonical_start_state()
    state = initial.copy(deep=True)
    registry = create_core_tool_registry()
    permissions = {
        actor_id: CORE_TOOL_PERMISSIONS for actor_id in state.characters
    }
    planner = RealLLMNarrativePlanner(
        registry,
        model=model,
        max_steps_per_actor=3,
        max_attempts=2,
        request_timeout_seconds=60.0,
        generator=generator,
        world_package_id=case.novel_id,
        scenario_family="canonical_reconstruction",
    )
    executor = JointPlanExecutor(registry)
    all_events: List[WorldEvent] = []
    scored_events: List[WorldEvent] = []
    triggers: List[JointPlanTrigger] = []
    stages = [
        PipelineStage(
            name="canonical_checkpoint_loaded",
            status="passed",
            details={
                "checkpoint": case.start_checkpoint,
                "evaluation_mode": mode,
                "future_event_count_visible_to_planner": 0,
                "world_version": state.version,
            },
        )
    ]
    tick_count = 0
    plan_count = 0
    replan_count = 0
    invalidation_count = 0
    invalidation_injected = False
    beat_count = 0

    with capture_llm_usage() as usage:
        for beat_index in range(case.planning.max_beats):
            beat_count = beat_index + 1
            actors = _select_active_actors(
                state,
                max_actors=case.planning.max_active_actors,
            )
            plan = planner.generate(
                state,
                actors,
                beat_goal=case.planning.beat_goal,
                goal_id=case.planning.goal_id,
                permissions_by_actor=permissions,
                metadata={
                    "beat_index": beat_index,
                    "recent_committed_events": [
                        {
                            "event_type": event.event_type,
                            "actor_ids": list(event.actor_ids),
                            "target_ids": list(event.target_ids),
                            "summary": event.summary,
                        }
                        for event in scored_events[-12:]
                    ],
                },
            )
            plan_count += 1
            runtime = create_plan_runtime(
                plan,
                max_replans=case.planning.max_replans_per_beat,
            )
            stages.append(
                PipelineStage(
                    name="real_llm_plan_generated",
                    status="passed",
                    details={
                        "beat_index": beat_index,
                        "plan_id": plan.plan_id,
                        "actors": actors,
                        "action_count": sum(
                            len(chain.steps) for chain in plan.actor_chains.values()
                        ),
                        "fallback_used": False,
                    },
                )
            )
            previous_state: Optional[WorldState] = None
            for local_tick in range(case.planning.max_ticks_per_beat):
                tick_count += 1
                if (
                    mode == "perturbed"
                    and
                    not invalidation_injected
                    and scored_events
                    and _has_pending_target_action(plan, runtime)
                ):
                    before_change = state.copy(deep=True)
                    state, injected = _inject_dependency_change(plan, runtime, state)
                    if injected is not None:
                        invalidation_injected = True
                        invalidation_count += 1
                        all_events.append(injected)
                        scored_events.append(injected)
                        previous_state = before_change
                        stages.append(
                            PipelineStage(
                                name="plan_dependency_changed",
                                status="passed",
                                details={
                                    "event_id": injected.event_id,
                                    "world_version": state.version,
                                },
                            )
                        )

                async def repair(request, repair_state):
                    return planner.replan(
                        request,
                        repair_state,
                        beat_goal=case.planning.beat_goal,
                        permissions_by_actor=permissions,
                    )

                result = await executor.tick(
                    plan,
                    runtime,
                    state,
                    permissions_by_actor=permissions,
                    replan=repair,
                    previous_state=previous_state,
                )
                previous_state = state.copy(deep=True)
                plan, runtime, state = result.plan, result.runtime, result.state
                if result.replan_trigger is not None:
                    triggers.append(result.replan_trigger)
                if result.replanned:
                    replan_count += 1
                    plan_count += 1
                    stages.append(
                        PipelineStage(
                            name="real_llm_plan_repaired",
                            status="passed",
                            details={
                                "trigger": result.replan_trigger.code
                                if result.replan_trigger is not None
                                else "",
                                "plan_id": plan.plan_id,
                                "revision": plan.revision,
                            },
                        )
                    )
                all_events.extend(result.events)
                scored_events.extend(result.events)
                state, perception_events = _commit_dialogue_perceptions(
                    state,
                    result.events,
                )
                all_events.extend(perception_events)
                if result.events:
                    stages.append(
                        PipelineStage(
                            name="guarded_multi_agent_execution",
                            status="passed",
                            details={
                                "beat_index": beat_index,
                                "tick": local_tick,
                                "committed_event_ids": [
                                    event.event_id for event in result.events
                                ],
                                "world_version": state.version,
                            },
                        )
                    )
                if runtime.status in {
                    PlanRuntimeStatus.completed,
                    PlanRuntimeStatus.aborted,
                }:
                    break
            if _fragment_completed(state):
                break

    fragment_completed = _fragment_completed(state)
    replay = evaluate_trajectory(initial, all_events, expected_final_state=state)
    alignment = align_canonical_events(case.canonical_events, scored_events)
    source_attribution = _attribute_matched_events(
        case.canonical_events,
        alignment,
        [_event_record(event) for event in scored_events],
    )
    stages.extend(
        [
            PipelineStage(
                name="story_fragment_terminal",
                status="passed" if fragment_completed else "incomplete",
                details={
                    "night_location": state.characters[NIGHT].location_id,
                    "expected_location": YE_CLAN_HALL,
                },
            ),
            PipelineStage(
                name="canonical_event_alignment",
                status="passed",
                details=alignment.metrics.dict(),
            ),
        ]
    )
    fallback_count = sum(trace.fallback_used for trace in planner.call_traces)
    return CanonicalNovelReport(
        evaluation_mode=mode,
        case_id=case.case_id,
        novel_title=case.novel_title,
        target_chapters=list(case.target_chapters),
        source_fingerprint=case.source_fingerprint,
        real_llm=generator is None,
        fallback_count=fallback_count,
        future_event_leakage_detected=False,
        initial_state_hash=authoritative_state_hash(initial),
        final_state_hash=authoritative_state_hash(state),
        initial_world_version=initial.version,
        final_world_version=state.version,
        beat_count=beat_count,
        execution_tick_count=tick_count,
        plan_count=plan_count,
        plan_invalidation_count=invalidation_count,
        replan_count=replan_count,
        fragment_completed=fragment_completed,
        replay_consistent=replay.passed,
        stages=stages,
        planner_traces=list(planner.call_traces),
        llm_calls=usage.calls,
        llm_usage=usage.summary(),
        simulated_events=[_event_record(event) for event in scored_events],
        triggers=triggers,
        alignment=alignment,
        source_attribution=source_attribution,
    )


def _select_active_actors(state: WorldState, *, max_actors: int) -> List[str]:
    """Select the protagonist, scene peers and activated remote plot drivers."""

    driver = state.characters[NIGHT]
    candidates = []
    for actor_id, character in state.characters.items():
        if not character.is_alive:
            continue
        psyche = state.character_psyches.get(actor_id)
        active_goals = [] if psyche is None else [
            goal
            for goal in psyche.goals
            if not goal.achieved and getattr(goal, "status", "active") == "active"
        ]
        active_goals = [
            goal
            for goal in active_goals
            if _goal_is_activated(goal, driver.location_id, state)
        ]
        same_scene = character.location_id == driver.location_id
        if not same_scene:
            active_goals = [
                goal
                for goal in active_goals
                if list(
                    getattr(goal, "activation_target_location_ids", []) or []
                )
            ]
        if actor_id != NIGHT and not active_goals:
            continue
        priority = max((goal.priority for goal in active_goals), default=1.0)
        candidates.append(
            (
                0 if actor_id == NIGHT else (1 if same_scene else 2),
                -priority,
                actor_id,
            )
        )
    candidates.sort()
    ready_drivers = [item for item in candidates if ready_ability_ids(state, item[2])]
    if ready_drivers:
        return [ready_drivers[0][2]]
    local = [item for item in candidates if item[0] < 2]
    selected = [NIGHT]
    for item in local:
        if item[2] not in selected and len(selected) < max_actors:
            selected.append(item[2])
    return selected


def _goal_is_activated(goal, driver_location_id: Optional[str], state: WorldState) -> bool:
    locations = list(getattr(goal, "activation_target_location_ids", []) or [])
    if locations and driver_location_id not in locations:
        return False
    required_flags = dict(getattr(goal, "activation_flags", {}) or {})
    return all(state.flags.get(str(key)) == value for key, value in required_flags.items())


def _has_pending_target_action(plan: JointPlan, runtime: PlanRuntimeState) -> bool:
    return _pending_target(plan, runtime) is not None


def _pending_target(
    plan: JointPlan,
    runtime: PlanRuntimeState,
) -> Optional[tuple[str, str]]:
    for actor_id, chain in plan.actor_chains.items():
        pointer = runtime.actor_step_pointers.get(actor_id, 0)
        for step in chain.steps[pointer:]:
            if not isinstance(step, ActionStep):
                continue
            target = str(step.tool_call.arguments.get("target_character_id") or "")
            if target and target != actor_id:
                return actor_id, target
    return None


def _inject_dependency_change(
    plan: JointPlan,
    runtime: PlanRuntimeState,
    state: WorldState,
) -> tuple[WorldState, Optional[WorldEvent]]:
    dependency = _pending_target(plan, runtime)
    if dependency is None:
        return state, None
    actor_id, target_id = dependency
    actor = state.characters.get(actor_id)
    target = state.characters.get(target_id)
    if actor is None or target is None or actor.location_id != target.location_id:
        return state, None
    destination = YEFU if target.location_id != YEFU else SCENE_ID
    event, updated = commit_event(
        state,
        action_id="environment_dependency_change_%d" % (state.version + 1),
        event_type="environment.dynamic_departure",
        patch=StatePatch(
            operations=[
                Operation(
                    op=OperationKind.move_character,
                    target_id=target_id,
                    location_id=destination,
                    reason="动态环境变化导致原计划目标临时离场",
                )
            ]
        ),
        actor_ids=[target_id],
        target_ids=[destination],
        expected_version=state.version,
        summary="动态环境变化：%s临时离开当前场景" % target_id,
    )
    return updated, event


def _commit_dialogue_perceptions(
    state: WorldState,
    events: Iterable[WorldEvent],
) -> tuple[WorldState, List[WorldEvent]]:
    committed = []
    for source_event in events:
        for presentation in source_event.presentation_events:
            if presentation.get("event_type") != "dialogue":
                continue
            payload = presentation.get("payload") or {}
            target_id = str(payload.get("to_id") or "")
            speaker_id = str(payload.get("speaker_id") or "")
            line = str(payload.get("line") or "").strip()
            if target_id not in state.character_psyches or not line:
                continue
            operations = [
                Operation(
                    op=OperationKind.update_psyche,
                    target_id=target_id,
                    perception="%s对我说：%s" % (speaker_id, line),
                    reason="对话进入角色工作记忆",
                )
            ]
            operations.extend(
                _dialogue_effect_operations(
                    state,
                    speaker_id=speaker_id,
                    target_id=target_id,
                )
            )
            event, state = commit_event(
                state,
                action_id="perception_%s" % source_event.event_id,
                event_type="system.dialogue_perceived",
                patch=StatePatch(operations=operations),
                actor_ids=[target_id],
                target_ids=[speaker_id],
                expected_version=state.version,
                summary="%s记住了%s的对话" % (target_id, speaker_id),
            )
            committed.append(event)
    return state, committed


def _dialogue_effect_operations(
    state: WorldState,
    *,
    speaker_id: str,
    target_id: str,
) -> List[Operation]:
    """Apply trusted world-package gates unlocked by an actual dialogue."""

    effects = state.flags.get("runtime.dialogue_effects", [])
    if not isinstance(effects, list):
        return []
    speaker = state.characters.get(speaker_id)
    if speaker is None:
        return []
    operations: List[Operation] = []
    for effect in effects:
        if not isinstance(effect, dict):
            continue
        if str(effect.get("speaker_id") or "") != speaker_id:
            continue
        if str(effect.get("target_character_id") or "") != target_id:
            continue
        if str(effect.get("location_id") or "") != speaker.location_id:
            continue
        required = effect.get("required_flags", {})
        if not isinstance(required, dict) or any(
            state.flags.get(str(key)) != value for key, value in required.items()
        ):
            continue
        completion_flag = str(effect.get("completion_flag") or "")
        if completion_flag and not state.flags.get(completion_flag):
            operations.append(
                Operation(
                    op=OperationKind.set_flag,
                    path=completion_flag,
                    value=True,
                    reason="dialogue_effect:%s:%s" % (speaker_id, target_id),
                )
            )
    return operations


def _fragment_completed(state: WorldState) -> bool:
    return state.characters[NIGHT].location_id == YE_CLAN_HALL


def _event_record(event: WorldEvent) -> SimulatedEventRecord:
    source = (
        "llm_tool"
        if event.event_type.startswith("tool.")
        else "environment"
    )
    return SimulatedEventRecord(
        event_id=event.event_id,
        event_type=event.event_type,
        world_version=event.new_version,
        actor_ids=list(event.actor_ids),
        target_ids=list(event.target_ids),
        summary=event.summary,
        source=source,
    )


def _attribute_matched_events(
    canonical: Sequence[CanonicalEventAnchor],
    alignment: CanonicalAlignmentReport,
    events: Sequence[SimulatedEventRecord],
) -> EventSourceAttribution:
    anchor_by_id = {item.event_id: item for item in canonical}
    source_by_event_id = {item.event_id: item.source for item in events}
    total_weight = sum(item.weight for item in canonical)
    counts: Dict[str, int] = {}
    weights: Dict[str, float] = {}
    for item in alignment.alignments:
        if not item.matched or item.simulated_event_id is None:
            continue
        source = source_by_event_id.get(item.simulated_event_id, "unknown")
        weight = anchor_by_id[item.canonical_event_id].weight
        counts[source] = counts.get(source, 0) + 1
        weights[source] = weights.get(source, 0.0) + weight
    recalls = {
        source: round(weight / total_weight, 4) if total_weight else 1.0
        for source, weight in weights.items()
    }
    return EventSourceAttribution(
        total_canonical_weight=round(total_weight, 4),
        matched_weight=round(sum(weights.values()), 4),
        matched_event_count_by_source=counts,
        matched_weight_by_source={
            source: round(weight, 4) for source, weight in weights.items()
        },
        canonical_recall_by_source=recalls,
    )
def render_markdown(report: CanonicalNovelReport) -> str:
    metrics = report.alignment.metrics
    source_recall = report.source_attribution.canonical_recall_by_source
    lines = [
        "# NovelSim 原著长程片段真实 LLM 推演报告",
        "",
        f"- 小说：{report.novel_title}",
        f"- 章节：{min(report.target_chapters)}--{max(report.target_chapters)}",
        f"- 真实 LLM：{'是' if report.real_llm else '否（测试注入）'}",
        f"- 脚本回退：{report.fallback_count}",
        f"- 未来原著泄漏：{'是' if report.future_event_leakage_detected else '否'}",
        f"- 剧情片段完成：{'是' if report.fragment_completed else '否'}",
        f"- 计划失效/重规划：{report.plan_invalidation_count}/{report.replan_count}",
        f"- 权威回放一致：{'是' if report.replay_consistent else '否'}",
        f"- LLM 调用：{report.llm_usage.call_count}，Token：{report.llm_usage.total_tokens}",
        f"- 原著关键事件召回：{metrics.matched_event_count}/{metrics.canonical_event_count} "
        f"({metrics.weighted_event_recall:.1%})",
        f"- 事件顺序准确率：{metrics.event_order_accuracy:.1%}",
        "",
        "## 阶段结果",
        "",
        "| 阶段 | 状态 |",
        "|---|---|",
    ]
    lines.extend(
        [
            f"| evaluation_mode | {report.evaluation_mode} |",
            f"| agent_driven_canon_recall | {source_recall.get('llm_tool', 0.0):.1%} |",
            f"| environment_driven_canon_recall | {source_recall.get('environment', 0.0):.1%} |",
        ]
    )
    lines.extend(f"| {stage.name} | {stage.status} |" for stage in report.stages)
    lines.extend(
        [
            "",
            "## 说明",
            "",
            "规划器仅接收角色私有观察、当前目标和工具 Schema。"
            "原著事件及证据哈希在推演结束后才进入确定性对齐器。",
        ]
    )
    return "\n".join(lines) + "\n"


def rejudge_existing_report(
    report: CanonicalNovelReport,
    case: CanonicalNovelCase,
) -> CanonicalNovelReport:
    """Recompute deterministic canon alignment without another LLM call."""

    events = [
        WorldEvent(
            event_id=item.event_id,
            event_type=item.event_type,
            actor_ids=list(item.actor_ids),
            target_ids=list(item.target_ids),
            previous_version=max(0, item.world_version - 1),
            new_version=item.world_version,
            summary=item.summary,
        )
        for item in report.simulated_events
    ]
    updated = report.copy(deep=True)
    updated.alignment = align_canonical_events(case.canonical_events, events)
    updated.source_attribution = _attribute_matched_events(
        case.canonical_events,
        updated.alignment,
        updated.simulated_events,
    )
    for stage in updated.stages:
        if stage.name == "canonical_event_alignment":
            stage.details = updated.alignment.metrics.dict()
    return updated


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=DEFAULT_CASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--mode",
        choices=("clean", "perturbed"),
        default="clean",
        help="clean measures canon fidelity; perturbed injects one dependency change.",
    )
    parser.add_argument(
        "--rejudge-existing",
        type=Path,
        default=None,
        help="Recompute deterministic alignment from an existing report without LLM calls.",
    )
    args = parser.parse_args(argv)
    case = load_canonical_case(args.case)
    if args.rejudge_existing is not None:
        report = rejudge_existing_report(
            CanonicalNovelReport.parse_obj(
                json.loads(args.rejudge_existing.read_text(encoding="utf-8"))
            ),
            case,
        )
    else:
        report = asyncio.run(
            run_canonical_case(case, model=args.model, mode=args.mode)
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        report.json(ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    return 0 if report.replay_consistent and report.fallback_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
