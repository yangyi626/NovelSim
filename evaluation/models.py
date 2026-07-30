"""评测案例、运行明细和聚合报告的数据契约。"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, root_validator, validator

from engine.llm_telemetry import LLMCallUsage, LLMUsageSummary
from engine.scene_controller import SceneMode, SceneStatus


class BenchmarkToolCall(BaseModel):
    actor_id: str
    tool_name: str
    arguments: Dict[str, object] = Field(default_factory=dict)
    call_id: Optional[str] = None

    class Config:
        extra = "forbid"


class BenchmarkExpectation(BaseModel):
    status: SceneStatus
    ending_id: Optional[str] = None
    objective_satisfied: bool
    tool_sequence: List[str] = Field(default_factory=list)
    rejection_codes: List[str] = Field(default_factory=list)
    final_version: int = Field(..., ge=0)
    propagation_count: int = Field(0, ge=0)
    alliance_count: int = Field(0, ge=0)

    class Config:
        extra = "forbid"


class BenchmarkCase(BaseModel):
    schema_version: int = 1
    case_id: str
    scenario_id: str
    mode: SceneMode = SceneMode.free
    player_route: Optional[str] = None
    max_turns: int = Field(12, ge=1, le=200)
    random_seed: int = 20260729
    repetitions: int = Field(1, ge=1, le=100)
    tags: List[str] = Field(default_factory=list)
    initial_calls: List[BenchmarkToolCall] = Field(default_factory=list)
    expected: BenchmarkExpectation

    class Config:
        extra = "forbid"

    @validator("schema_version")
    def _schema_version_is_supported(cls, value):
        if value != 1:
            raise ValueError("only benchmark schema_version=1 is supported")
        return value

    @root_validator(skip_on_failure=True)
    def _route_and_explicit_calls_are_exclusive(cls, values):
        if values.get("player_route") and values.get("initial_calls"):
            raise ValueError(
                "player_route and initial_calls cannot both be configured"
            )
        return values


class PricingConfig(BaseModel):
    currency: str = "USD"
    input_per_million: float = Field(0.0, ge=0.0)
    cached_input_per_million: float = Field(0.0, ge=0.0)
    output_per_million: float = Field(0.0, ge=0.0)

    class Config:
        extra = "forbid"

    @property
    def configured(self) -> bool:
        return any(
            value > 0
            for value in (
                self.input_per_million,
                self.cached_input_per_million,
                self.output_per_million,
            )
        )


class LatencyStats(BaseModel):
    sample_count: int = Field(0, ge=0)
    mean_ms: float = Field(0.0, ge=0.0)
    p50_ms: float = Field(0.0, ge=0.0)
    p95_ms: float = Field(0.0, ge=0.0)
    max_ms: float = Field(0.0, ge=0.0)

    class Config:
        extra = "forbid"


class CaseRunRecord(BaseModel):
    case_id: str
    repetition: int = Field(..., ge=1)
    scenario_id: str
    mode: SceneMode
    tags: List[str] = Field(default_factory=list)
    random_seed: int
    passed: bool
    assertion_failures: List[str] = Field(default_factory=list)
    status: SceneStatus
    ending_id: Optional[str] = None
    objective_satisfied: bool
    turns_used: int = Field(..., ge=0)
    initial_version: int = Field(..., ge=0)
    final_version: int = Field(..., ge=0)
    initial_state_hash: str
    final_state_hash: str
    tool_sequence: List[str] = Field(default_factory=list)
    tool_call_count: int = Field(..., ge=0)
    successful_tool_calls: int = Field(..., ge=0)
    expected_rejection_count: int = Field(..., ge=0)
    matched_expected_rejection_count: int = Field(..., ge=0)
    unexpected_rejection_count: int = Field(..., ge=0)
    failure_codes: List[str] = Field(default_factory=list)
    failure_distribution: Dict[str, int] = Field(default_factory=dict)
    trace_ids: List[str] = Field(default_factory=list)
    event_ids: List[str] = Field(default_factory=list)
    replay_consistent: bool
    illegal_patch_commit_count: int = Field(..., ge=0)
    unknown_entity_accept_count: int = Field(..., ge=0)
    causal_violation_count: int = Field(..., ge=0)
    knowledge_leak_count: int = Field(..., ge=0)
    propagation_count: int = Field(..., ge=0)
    valid_propagation_count: int = Field(..., ge=0)
    evidence_chain_sample_count: int = Field(..., ge=0)
    complete_evidence_chain_count: int = Field(..., ge=0)
    alliance_count: int = Field(..., ge=0)
    invalid_loop_count: int = Field(..., ge=0)
    structural_character_violation_count: int = Field(..., ge=0)
    latency_ms: float = Field(..., ge=0.0)
    stage_latency_ms: Dict[str, float] = Field(default_factory=dict)
    llm_calls: List[LLMCallUsage] = Field(default_factory=list)
    llm_usage: LLMUsageSummary = Field(default_factory=LLMUsageSummary)
    estimated_cost: Optional[float] = Field(None, ge=0.0)
    cost_currency: Optional[str] = None

    class Config:
        extra = "forbid"


class AggregateMetrics(BaseModel):
    run_count: int = Field(..., ge=0)
    benchmark_pass_rate: float = Field(..., ge=0.0, le=1.0)
    objective_completion_rate: float = Field(..., ge=0.0, le=1.0)
    core_event_completion_rate: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
    )
    tool_success_rate: Optional[float] = Field(None, ge=0.0, le=1.0)
    expected_rejection_rate: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
    )
    replay_consistency_rate: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
    )
    propagation_accuracy: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
    )
    evidence_chain_completeness: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
    )
    structural_character_consistency: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
    )
    alliance_formation_rate: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
    )
    illegal_patch_commit_count: int = Field(..., ge=0)
    unknown_entity_accept_count: int = Field(..., ge=0)
    causal_violation_count: int = Field(..., ge=0)
    knowledge_leak_count: int = Field(..., ge=0)
    unexpected_rejection_count: int = Field(..., ge=0)
    invalid_loop_count: int = Field(..., ge=0)
    failure_distribution: Dict[str, int] = Field(default_factory=dict)
    total_latency: LatencyStats
    stage_latency: Dict[str, LatencyStats] = Field(default_factory=dict)
    llm_usage: LLMUsageSummary
    estimated_cost: Optional[float] = Field(None, ge=0.0)
    cost_currency: Optional[str] = None

    class Config:
        extra = "forbid"


class AcceptanceCheck(BaseModel):
    check_id: str
    passed: bool
    measured: bool = True
    required_for_suite: bool = True
    actual: str
    threshold: str
    evidence: str = ""

    class Config:
        extra = "forbid"


class GuardAblationProfile(BaseModel):
    profile_id: str
    label: str
    enabled_gates: List[str] = Field(default_factory=list)

    class Config:
        extra = "forbid"


class GuardProbeResult(BaseModel):
    probe_id: str
    gate: str
    production_rejected: bool
    profile_rejected: bool
    violation_accepted: bool

    class Config:
        extra = "forbid"


class GuardProfileResult(BaseModel):
    profile: GuardAblationProfile
    probe_count: int = Field(..., ge=0)
    rejected_count: int = Field(..., ge=0)
    violation_accept_count: int = Field(..., ge=0)
    rejection_rate: float = Field(..., ge=0.0, le=1.0)
    probes: List[GuardProbeResult] = Field(default_factory=list)

    class Config:
        extra = "forbid"


class MemoryVariantResult(BaseModel):
    variant_id: str
    memory_enabled: bool
    query_count: int = Field(..., ge=0)
    hit_rate: float = Field(..., ge=0.0, le=1.0)
    recall: float = Field(..., ge=0.0, le=1.0)
    mrr: float = Field(..., ge=0.0, le=1.0)
    ndcg: float = Field(..., ge=0.0, le=1.0)
    irrelevant_rate: float = Field(..., ge=0.0, le=1.0)
    p95_latency_ms: float = Field(..., ge=0.0)

    class Config:
        extra = "forbid"


class AblationReport(BaseModel):
    isolated: bool = True
    authoritative_store_used: bool = False
    guard_profiles: List[GuardProfileResult] = Field(default_factory=list)
    memory_variants: List[MemoryVariantResult] = Field(default_factory=list)
    production_probe_failures: List[str] = Field(default_factory=list)
    passed: bool

    class Config:
        extra = "forbid"


class ModeComparison(BaseModel):
    comparison_id: str
    sample_pairs: int = Field(..., ge=0)
    free_pass_rate: Optional[float] = Field(None, ge=0.0, le=1.0)
    script_pass_rate: Optional[float] = Field(None, ge=0.0, le=1.0)
    same_final_state_rate: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
    )
    same_tool_chain_rate: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
    )
    free_p50_latency_ms: float = Field(0.0, ge=0.0)
    script_p50_latency_ms: float = Field(0.0, ge=0.0)

    class Config:
        extra = "forbid"


class EvaluationReport(BaseModel):
    schema_version: int = 1
    suite_id: str
    run_id: str
    generated_at: str
    deterministic: bool
    random_seed: int
    pricing: PricingConfig
    case_count: int = Field(..., ge=0)
    run_count: int = Field(..., ge=0)
    records: List[CaseRunRecord] = Field(default_factory=list)
    metrics: AggregateMetrics
    mode_comparisons: List[ModeComparison] = Field(default_factory=list)
    ablations: Optional[AblationReport] = None
    acceptance_checks: List[AcceptanceCheck] = Field(default_factory=list)
    passed: bool

    class Config:
        extra = "forbid"
