"""隔离的世界门禁与长期记忆消融。

所有实验使用内存快照或临时 SQLite；禁用门禁只做反事实判定，不提交事件。
这样可以量化 G0–G3 的差异，又不会向正式运行时增加“关闭安全校验”的开关。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Dict, List

from engine import (
    ActionParser,
    AgentExecutionStateMachine,
    CORE_TOOL_PERMISSIONS,
    RuleEngine,
    ToolCall,
    check_narrative,
    create_core_tool_registry,
    evaluate_retrieval,
    evaluate_store,
    load_retrieval_benchmark,
    seed_retrieval_benchmark,
    validate_action_patch,
)
from engine.persistence import SQLiteWorldStore
from examples.huarong_lane import build_snapshot as build_huarong_snapshot
from examples.huarong_lane.scenario import NIGHT
from examples.secret_letter import FACT_PLOT, GUARD, STEWARD
from examples.secret_letter import build_snapshot as build_letter_snapshot
from world_schema import (
    Action,
    Actor,
    CausalEvidence,
    EntityAffordance,
    IntentStatus,
    Item,
    NarrativeOutput,
    Operation,
    OperationKind,
    StatePatch,
    WorldEvent,
)
from world_schema.models import ActionType

from .models import (
    AblationReport,
    GuardAblationProfile,
    GuardProbeResult,
    GuardProfileResult,
    MemoryVariantResult,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MEMORY_BENCHMARK = (
    PROJECT_ROOT / "benchmarks" / "memory_retrieval_zh.json"
)

GUARD_PROFILES = [
    GuardAblationProfile(
        profile_id="G0",
        label="仅提示词",
        enabled_gates=[],
    ),
    GuardAblationProfile(
        profile_id="G1",
        label="结构化世界规则",
        enabled_gates=["world_concept", "entity"],
    ),
    GuardAblationProfile(
        profile_id="G2",
        label="执行门禁",
        enabled_gates=[
            "world_concept",
            "entity",
            "capability_affordance",
            "cognitive_boundary",
            "causal_authorization",
        ],
    ),
    GuardAblationProfile(
        profile_id="G3",
        label="完整闭环",
        enabled_gates=[
            "world_concept",
            "entity",
            "capability_affordance",
            "cognitive_boundary",
            "causal_authorization",
            "narrative_grounding",
        ],
    ),
]


async def run_ablation_suite() -> AblationReport:
    production_probes = await _production_guard_probes()
    failures = [
        probe_id
        for probe_id, (_, rejected) in production_probes.items()
        if not rejected
    ]
    guard_profiles = [
        _evaluate_guard_profile(profile, production_probes)
        for profile in GUARD_PROFILES
    ]
    memory_variants = _memory_ablations()
    full = next(
        item
        for item in guard_profiles
        if item.profile.profile_id == "G3"
    )
    memory_enabled = next(
        item for item in memory_variants if item.memory_enabled
    )
    no_memory = next(
        item for item in memory_variants if not item.memory_enabled
    )
    return AblationReport(
        isolated=True,
        authoritative_store_used=False,
        guard_profiles=guard_profiles,
        memory_variants=memory_variants,
        production_probe_failures=failures,
        passed=(
            not failures
            and full.violation_accept_count == 0
            and memory_enabled.hit_rate > no_memory.hit_rate
            and memory_enabled.mrr > no_memory.mrr
        ),
    )


async def _production_guard_probes() -> Dict[str, tuple]:
    world = build_huarong_snapshot()
    concept_result = ActionParser._precheck_world_concepts(
        "夜轻歌开飞机飞走了",
        world,
    )

    parser = ActionParser()
    entity_result = parser._try_build_intent_result(
        json.dumps(
            {
                "status": "accepted",
                "action_type": "observe",
                "actor_id": NIGHT,
                "target_ids": ["missing_entity"],
                "parameters": {},
            }
        ),
        "观察不存在的实体",
        world,
        NIGHT,
    )

    modern = world.copy(deep=True)
    modern.world_concepts["concept_airplane"].available = True
    modern.world_constraints[0].forbidden_concept_ids = [
        value
        for value in modern.world_constraints[0].forbidden_concept_ids
        if value != "concept_airplane"
    ]
    modern.items["vehicle_airplane_eval"] = Item(
        item_id="vehicle_airplane_eval",
        display_name="评测飞机",
        location_id=modern.current_scene_id,
    )
    modern.entity_affordances["vehicle_airplane_eval"] = [
        EntityAffordance(
            affordance_id="affordance_eval_airplane",
            entity_id="vehicle_airplane_eval",
            action_type="move",
            concept_id="concept_airplane",
            required_capability_ids=["transport.pilot_aircraft"],
        )
    ]
    capability_action = Action(
        action_id="evaluation_capability_action",
        action_type=ActionType.move,
        actor=Actor(actor_id=NIGHT),
        parameters={
            "destination_id": "loc_yefu",
            "transport_entity_id": "vehicle_airplane_eval",
            "concept_ids": ["concept_airplane"],
            "capability_id": "transport.pilot_aircraft",
        },
    )
    capability_result = RuleEngine().validate(
        modern,
        capability_action,
    )

    cognitive = await AgentExecutionStateMachine(
        create_core_tool_registry(),
        max_retries=0,
        max_replans=0,
    ).execute(
        ToolCall(
            call_id="evaluation_cognitive_probe",
            actor_id=GUARD,
            tool_name="share_information",
            arguments={
                "target_character_id": STEWARD,
                "fact_id": FACT_PLOT,
            },
        ),
        build_letter_snapshot(),
        permissions=CORE_TOOL_PERMISSIONS,
    )

    causal_action = Action(
        action_id="evaluation_causal_action",
        action_type=ActionType.move,
        actor=Actor(actor_id=NIGHT),
        parameters={"destination_id": "loc_yefu"},
    )
    causal_patch = StatePatch(
        operations=[
            Operation(
                op=OperationKind.change_identity,
                target_id=NIGHT,
                tags=["皇帝"],
            )
        ],
        causal_evidence=CausalEvidence(
            action_id=causal_action.action_id,
            actor_id=NIGHT,
            authority="player_action",
        ),
    )
    causal_result = validate_action_patch(
        world,
        causal_action,
        causal_patch,
    )

    narrative_event = WorldEvent(
        event_id="evaluation_narrative_event",
        event_type="observe",
    )
    narrative_result = check_narrative(
        NarrativeOutput(
            narration="夜轻歌只是环顾四周。",
            referenced_entity_ids=[NIGHT],
        ),
        narrative_event,
        world,
    )
    return {
        "forbidden_world_concept": (
            "world_concept",
            concept_result is not None
            and concept_result.status == IntentStatus.rejected,
        ),
        "unknown_entity": (
            "entity",
            entity_result is not None
            and entity_result.status == IntentStatus.rejected,
        ),
        "missing_capability": (
            "capability_affordance",
            not capability_result.allowed,
        ),
        "knowledge_boundary": (
            "cognitive_boundary",
            not cognitive.result.success
            and cognitive.result.failure is not None
            and cognitive.result.failure.code.value
            == "cognitive_boundary",
        ),
        "action_patch_causality": (
            "causal_authorization",
            not causal_result.valid,
        ),
        "narrative_event_basis": (
            "narrative_grounding",
            not narrative_result.valid,
        ),
    }


def _evaluate_guard_profile(
    profile: GuardAblationProfile,
    probes: Dict[str, tuple],
) -> GuardProfileResult:
    results: List[GuardProbeResult] = []
    enabled = set(profile.enabled_gates)
    for probe_id, (gate, production_rejected) in probes.items():
        profile_rejected = bool(
            production_rejected and gate in enabled
        )
        results.append(
            GuardProbeResult(
                probe_id=probe_id,
                gate=gate,
                production_rejected=production_rejected,
                profile_rejected=profile_rejected,
                violation_accepted=not profile_rejected,
            )
        )
    rejected = sum(item.profile_rejected for item in results)
    return GuardProfileResult(
        profile=profile,
        probe_count=len(results),
        rejected_count=rejected,
        violation_accept_count=len(results) - rejected,
        rejection_rate=(
            round(rejected / len(results), 6)
            if results
            else 0.0
        ),
        probes=results,
    )


def _memory_ablations() -> List[MemoryVariantResult]:
    benchmark = load_retrieval_benchmark(MEMORY_BENCHMARK)
    no_memory = evaluate_retrieval(
        benchmark,
        lambda character_id, query, limit: [],
        mode="no_memory",
        k=4,
    )
    with tempfile.TemporaryDirectory(
        prefix="novelsim-evaluation-memory-"
    ) as root:
        store = SQLiteWorldStore(Path(root) / "evaluation.sqlite3")
        session_id = store.create_session(
            build_huarong_snapshot(),
            default_actor_id=NIGHT,
            world_package_id="evaluation_memory_ablation",
            save_name="隔离评测",
        )
        seed_retrieval_benchmark(store, session_id, benchmark)
        lexical = evaluate_store(
            store,
            session_id,
            benchmark,
            mode="sqlite_fts5",
            k=4,
        )
    return [
        _memory_variant(no_memory, enabled=False),
        _memory_variant(lexical, enabled=True),
    ]


def _memory_variant(report, *, enabled: bool) -> MemoryVariantResult:
    return MemoryVariantResult(
        variant_id=report.mode,
        memory_enabled=enabled,
        query_count=report.query_count,
        hit_rate=round(report.hit_rate, 6),
        recall=round(report.recall, 6),
        mrr=round(report.mrr, 6),
        ndcg=round(report.ndcg, 6),
        irrelevant_rate=round(report.irrelevant_rate, 6),
        p95_latency_ms=round(report.p95_latency_ms, 3),
    )
