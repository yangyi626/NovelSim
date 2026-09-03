"""世界状态、行动、补丁、事件的核心 Pydantic 模型。

设计原则:
- 每个实体都带 `extra="allow"`，因为不同小说的世界字段差异巨大，
  固定字段放在具名字段上，小说特有字段（如 `cultivation_level`、
  `bloodline`）放进 `attrs`/`state` 这种自由 JSONB 口袋里。
- StatePatch 只允许有限操作集合 (OperationKind)，禁止任意 SQL/赋值，
  这是"LLM 可以提议，但不能直接改库"的权限边界。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, root_validator


class AllowExtra(BaseModel):
    """所有世界实体的基类：允许携带小说特有字段。"""

    class Config:
        extra = "allow"
        # 便于断言/回放：输出稳定字典
        allow_population_by_field_name = True


# ---------------------------------------------------------------------------
# 角色认知
# ---------------------------------------------------------------------------


class Belief(str, Enum):
    """角色对某条事实的认知程度。"""

    believed_true = "believed_true"  # 认定为真
    suspected_true = "suspected_true"  # 怀疑为真
    unknown = "unknown"  # 不知道
    suspected_false = "suspected_false"  # 怀疑为假
    believed_false = "believed_false"  # 认定为假


class CharacterBelief(AllowExtra):
    """角色认为某条事实是真是假。

    关键: 角色只能依据自己知道/看到/听到/推断的信息行动。
    一致性审查器会校验: 角色对白中提到的事实，必须 belief != unknown。
    """

    fact_id: str
    belief: Belief = Belief.unknown
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    source_type: str = "unknown"  # observation / hearsay / inference / secret
    source_character_id: Optional[str] = None
    source_event_id: Optional[str] = None
    evidence_event_ids: List[str] = Field(default_factory=list)
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    # 该事实的中文/可读关键词，用于认知泄漏检测。
    # 例: fact_id=fact_qingqing_poisoned_tea -> keywords=["下毒","毒茶"]
    # 留空则审查器从 fact_id 粗略提取 (对中文效果差)。
    keywords: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 关系
# ---------------------------------------------------------------------------


class RelationDimensions(BaseModel):
    """多维关系。不能只存"朋友/敌人"。"""

    affection: float = Field(0.0, ge=-1.0, le=1.0)  # 好感
    trust: float = Field(0.0, ge=-1.0, le=1.0)  # 信任
    fear: float = Field(0.0, ge=0.0, le=1.0)  # 恐惧
    hostility: float = Field(0.0, ge=0.0, le=1.0)  # 敌意
    respect: float = Field(0.0, ge=-1.0, le=1.0)  # 敬意
    debt: float = Field(0.0, ge=-1.0, le=1.0)  # 恩怨/债务

    class Config:
        extra = "allow"


class CharacterRelation(AllowExtra):
    """两个角色之间的关系。source -> target 单向。"""

    source_id: str
    target_id: str
    public_relation: str = ""  # 公开关系: 主仆/姐妹/未婚夫妻
    private_relation: str = ""  # 私下关系: 信任/仇视
    dimensions: RelationDimensions = Field(default_factory=RelationDimensions)
    valid_from_event_id: Optional[str] = None
    valid_to_event_id: Optional[str] = None


# ---------------------------------------------------------------------------
# 实体: 角色 / 地点 / 物品
# ---------------------------------------------------------------------------


class Character(AllowExtra):
    """角色。is_alive / location_id 等核心字段单列，其余塞 attrs。"""

    character_id: str
    display_name: str
    aliases: List[str] = Field(default_factory=list)
    is_alive: bool = True
    location_id: Optional[str] = None
    inventory: List[str] = Field(default_factory=list)  # 持有物品 id
    identity_tags: List[str] = Field(default_factory=list)  # 嫡/庶/皇子/废柴
    attrs: Dict[str, Any] = Field(default_factory=dict)  # cultivation_level/bloodline...


class Location(AllowExtra):
    location_id: str
    display_name: str
    parent_id: Optional[str] = None  # 包含关系
    accessible: bool = True
    requires_permission: List[str] = Field(default_factory=list)  # 身份标签
    attrs: Dict[str, Any] = Field(default_factory=dict)


class Item(AllowExtra):
    item_id: str
    display_name: str
    owner_id: Optional[str] = None
    location_id: Optional[str] = None  # 不在身上时
    quantity: int = 1
    unique: bool = True
    accessible: bool = True
    attrs: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 世界规则 (确定性规则引擎用)
# ---------------------------------------------------------------------------


class Rule(AllowExtra):
    """一条世界规则。preconditions/effects 用受控 DSL，详见 engine.rules。

    本阶段只做数据载体; 求值逻辑在 engine 包。
    """

    rule_id: str
    priority: int = 50
    preconditions: List[Dict[str, Any]] = Field(default_factory=list)
    effects: List[Dict[str, Any]] = Field(default_factory=list)
    description: str = ""


class PlotArc(AllowExtra):
    """剧情线: 主线/支线/伏笔。"""

    arc_id: str
    title: str
    kind: str = "main"  # main / side / foreshadow
    stage: str = "not_started"
    completed: bool = False
    attrs: Dict[str, Any] = Field(default_factory=dict)


class WorldRule(AllowExtra):
    """高层世界规则文本(魔法体系/死亡规则等)，用于一致性审查时供 LLM 参考。"""

    rule_id: str
    category: str  # magic / death / identity / politics / time
    statement: str


class WorldConcept(AllowExtra):
    """世界中可被行动引用的概念。

    ``available=False`` 表示这个概念被世界包明确声明为不存在或不可用。别名只
    属于世界数据，不属于引擎硬编码，因此同一个概念可以在不同世界包中有不同
    可用性。
    """

    concept_id: str
    display_name: str
    aliases: List[str] = Field(default_factory=list)
    mention_patterns: List[str] = Field(default_factory=list)
    category: str = "general"  # technology / magic / transport / social / general
    available: bool = True
    requires_entity: bool = False
    required_capability_ids: List[str] = Field(default_factory=list)


class WorldConstraint(AllowExtra):
    """可执行的世界级约束，而不是只供 LLM 阅读的自然语言规则。"""

    constraint_id: str
    category: str = "general"
    statement: str = ""
    allowed_concept_ids: List[str] = Field(default_factory=list)
    forbidden_concept_ids: List[str] = Field(default_factory=list)
    strict_allowlist: bool = False
    strict_narrative_grounding: bool = False
    strict_knowledge_boundaries: bool = False


class CharacterCapability(AllowExtra):
    """角色当前具备的一项能力。"""

    capability_id: str
    enabled: bool = True
    level: float = Field(1.0, ge=0.0)
    source: str = "world_package"


class EntityAffordance(AllowExtra):
    """某个已注册实体允许执行的动作及其能力前置条件。"""

    affordance_id: str
    entity_id: str
    action_type: str
    concept_id: Optional[str] = None
    enabled: bool = True
    required_capability_ids: List[str] = Field(default_factory=list)


class ActionPolicy(AllowExtra):
    """Action 的确定性参数和 Patch 权限策略。"""

    action_type: str
    required_parameters: List[str] = Field(default_factory=list)
    requires_target: bool = False
    required_capability_ids: List[str] = Field(default_factory=list)
    affordance_parameter: Optional[str] = None
    affordance_from_target: bool = False
    allowed_patch_operations: List[str] = Field(default_factory=list)


class WorldFact(AllowExtra):
    """权威世界事实；角色是否知道它由 CharacterBelief 单独表示。"""

    fact_id: str
    statement: str
    truth: Belief = Belief.believed_true
    location_id: Optional[str] = None
    item_id: Optional[str] = None
    observable: bool = False
    base_confidence: float = Field(1.0, ge=0.0, le=1.0)
    keywords: List[str] = Field(default_factory=list)
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None


class BeliefEvidence(AllowExtra):
    """一条可追溯的角色认知证据。"""

    evidence_id: str
    fact_id: str
    holder_id: str
    source_type: str
    source_character_id: Optional[str] = None
    source_event_id: Optional[str] = None
    parent_evidence_ids: List[str] = Field(default_factory=list)
    reliability: float = Field(1.0, ge=0.0, le=1.0)


class PropagationRecord(AllowExtra):
    """一次信息从 source 传播到 target 的确定性计算记录。"""

    propagation_id: str
    fact_id: str
    source_character_id: str
    target_character_id: str
    source_confidence: float = Field(..., ge=0.0, le=1.0)
    source_reliability: float = Field(..., ge=0.0, le=1.0)
    trust_factor: float = Field(..., ge=0.0, le=1.0)
    channel_decay: float = Field(..., ge=0.0, le=1.0)
    corroboration_bonus: float = Field(0.0, ge=0.0, le=1.0)
    conflict_penalty: float = Field(0.0, ge=0.0, le=1.0)
    resulting_confidence: float = Field(..., ge=0.0, le=1.0)
    resulting_belief: Belief
    evidence_id: str


class AllianceState(AllowExtra):
    """由确定性规则形成的联盟。"""

    alliance_id: str
    member_ids: List[str]
    goal_key: str
    shared_fact_ids: List[str] = Field(default_factory=list)
    evidence_event_ids: List[str] = Field(default_factory=list)
    status: str = "active"
    formed_event_id: Optional[str] = None


# ---------------------------------------------------------------------------
# 世界状态
# ---------------------------------------------------------------------------


class WorldState(AllowExtra):
    """当前客观世界事实。权威状态的快照。

    version 是乐观锁版本号: 每次提交 Event -> version + 1。
    回放 = 从某快照开始重放事件序列，应得到相同的最终状态。
    """

    timeline_id: str
    version: int = 0
    world_time: str = ""
    current_scene_id: Optional[str] = None
    characters: Dict[str, Character] = Field(default_factory=dict)
    items: Dict[str, Item] = Field(default_factory=dict)
    locations: Dict[str, Location] = Field(default_factory=dict)
    relations: List[CharacterRelation] = Field(default_factory=list)
    beliefs: Dict[str, List[CharacterBelief]] = Field(default_factory=dict)
    facts: Dict[str, WorldFact] = Field(default_factory=dict)
    belief_evidence: Dict[str, BeliefEvidence] = Field(default_factory=dict)
    propagation_history: List[PropagationRecord] = Field(default_factory=list)
    alliances: Dict[str, AllianceState] = Field(default_factory=dict)
    # 角色 Agent 内在状态 (plan 第八步)。空 dict = 该世界未启用自主 NPC。
    character_psyches: Dict[str, "CharacterPsyche"] = Field(default_factory=dict)
    plot: Dict[str, PlotArc] = Field(default_factory=dict)
    rules: List[Rule] = Field(default_factory=list)
    world_rules: List[WorldRule] = Field(default_factory=list)
    world_concepts: Dict[str, WorldConcept] = Field(default_factory=dict)
    world_constraints: List[WorldConstraint] = Field(default_factory=list)
    character_capabilities: Dict[str, List[CharacterCapability]] = Field(
        default_factory=dict
    )
    entity_affordances: Dict[str, List[EntityAffordance]] = Field(
        default_factory=dict
    )
    action_policies: Dict[str, ActionPolicy] = Field(default_factory=dict)
    flags: Dict[str, Any] = Field(default_factory=dict)  # plot.xxx 自由布尔/数值


# ---------------------------------------------------------------------------
# Action: 玩家或 NPC 想做什么
# ---------------------------------------------------------------------------


class ActionType(str, Enum):
    """受控行动类型白名单。新增类型需同步更新规则引擎。"""

    swap_object = "swap_object"  # 交换物品
    move = "move"  # 移动
    speak = "speak"  # 说话
    use_item = "use_item"  # 使用物品
    investigate = "investigate"  # 调查
    attack = "attack"
    gift = "gift"  # 赠予
    observe = "observe"
    destroy_item = "destroy_item"  # 销毁具有对应 Affordance 的物品


class Actor(AllowExtra):
    """行动发起者。简化: actor_id 即角色 id 或 "player"。"""

    actor_id: str
    actor_type: str = "character"  # character / player / npc


class Action(AllowExtra):
    """一次行动。由 Action Parser (LLM) 生成，规则引擎校验合法性。"""

    action_id: str
    action_type: ActionType
    actor: Actor
    target_ids: List[str] = Field(default_factory=list)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    declared_goal: str = ""
    visibility: str = "overt"  # overt / covert / hidden


class IntentStatus(str, Enum):
    accepted = "accepted"
    rejected = "rejected"
    parse_failed = "parse_failed"


class IntentRejectionCode(str, Enum):
    ambiguous_intent = "AMBIGUOUS_INTENT"
    entity_not_found = "ENTITY_NOT_FOUND"
    world_concept_unavailable = "WORLD_CONCEPT_UNAVAILABLE"
    capability_missing = "CAPABILITY_MISSING"
    affordance_missing = "AFFORDANCE_MISSING"
    permission_denied = "PERMISSION_DENIED"
    spatial_precondition_failed = "SPATIAL_PRECONDITION_FAILED"
    knowledge_boundary_violation = "KNOWLEDGE_BOUNDARY_VIOLATION"
    patch_not_authorized = "PATCH_NOT_AUTHORIZED"
    narrative_not_grounded = "NARRATIVE_NOT_GROUNDED"
    invalid_action = "INVALID_ACTION"


class IntentParseResult(BaseModel):
    """自然语言解析的显式结果；拒绝不是一个伪装成 observe 的 Action。"""

    status: IntentStatus
    action: Optional[Action] = None
    reason_code: Optional[IntentRejectionCode] = None
    message: str = ""
    raw_input: str = ""
    details: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"

    @root_validator(skip_on_failure=True)
    def _status_matches_payload(cls, values):
        status = values.get("status")
        action = values.get("action")
        reason_code = values.get("reason_code")
        if status == IntentStatus.accepted and action is None:
            raise ValueError("accepted intent requires action")
        if status != IntentStatus.accepted and action is not None:
            raise ValueError("rejected/failed intent cannot contain action")
        if status == IntentStatus.rejected and reason_code is None:
            raise ValueError("rejected intent requires reason_code")
        return values


# ---------------------------------------------------------------------------
# StatePatch: 本轮允许发生的状态变化
# ---------------------------------------------------------------------------


class OperationKind(str, Enum):
    """操作白名单。LLM 只能从这里挑操作，禁止任意赋值。

    参考 docs/plan.md 第七节 与 模块表 10.8。
    """

    set_flag = "set_flag"  # flags[path] = value
    set_attr = "set_attr"  # 角色/物品的属性
    increment_value = "increment_value"  # 数值 += delta
    move_character = "move_character"
    transfer_item = "transfer_item"  # owner/location 之间转移
    destroy_item = "destroy_item"  # 销毁可销毁物品并移出世界
    update_relation = "update_relation"  # 关系维度增量
    set_relation = "set_relation"  # 关系整体替换
    update_belief = "update_belief"
    kill_character = "kill_character"
    revive_character = "revive_character"
    change_identity = "change_identity"  # 改 identity_tags
    start_plot = "start_plot"
    advance_plot = "advance_plot"
    complete_plot = "complete_plot"
    update_psyche = "update_psyche"  # 改 character_psyches[x]: 情绪/感知
    advance_plan = "advance_plan"  # 角色计划推进一步
    record_evidence = "record_evidence"
    add_fact = "add_fact"  # 登记权威 WorldFact（仅系统级事件使用）
    record_propagation = "record_propagation"
    form_alliance = "form_alliance"


class Operation(BaseModel):
    """单条状态操作。path 用点号分隔，如 'plot.poisoning_prevented'。"""

    op: OperationKind
    path: str = ""  # 主目标定位，如 character_id / flags 路径
    value: Any = None
    # 通用扩展槽: 不同 op 用不同字段
    target_id: Optional[str] = None
    source_id: Optional[str] = None
    dimension: Optional[str] = None  # relation op 的维度名
    delta: Optional[float] = None  # increment / update_relation
    item_id: Optional[str] = None
    location_id: Optional[str] = None
    belief: Optional[Belief] = None
    confidence: Optional[float] = None
    source_type: Optional[str] = None  # update_belief: observation/hearsay/inference/secret
    source_character_id: Optional[str] = None
    source_event_id: Optional[str] = None
    evidence_event_ids: Optional[List[str]] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    evidence_id: Optional[str] = None
    propagation_id: Optional[str] = None
    alliance_id: Optional[str] = None
    tags: Optional[List[str]] = None  # change_identity
    fact_id: Optional[str] = None
    reason: str = ""  # 该操作的理由 (便于审查/调试，LLM 产出时填写)
    # ---- 角色 Agent 专用 (update_psyche / advance_plan) ----
    emotion: Optional[str] = None  # 新情绪标签
    intensity: Optional[float] = None  # 情绪强度 0-1
    perception: Optional[str] = None  # 新增感知摘要 (append 到 recent_perceptions)
    plan_id: Optional[str] = None  # advance_plan 的目标计划
    step_delta: Optional[int] = None  # advance_plan 推进步数 (默认 +1)

    class Config:
        extra = "forbid"  # Operation 必须严格受控


class CausalEvidence(BaseModel):
    """一次状态变化的授权来源。"""

    action_id: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    actor_id: Optional[str] = None
    authority: str = "runtime"

    class Config:
        extra = "forbid"


class StatePatch(BaseModel):
    """本轮允许的状态变化集合。规则引擎产出，一致性审查后提交。"""

    operations: List[Operation] = Field(default_factory=list)
    notes: str = ""
    causal_evidence: Optional[CausalEvidence] = None

    class Config:
        extra = "forbid"


# ---------------------------------------------------------------------------
# WorldEvent: 已经正式发生的事实 (不可变)
# ---------------------------------------------------------------------------


class WorldEvent(AllowExtra):
    """一个已提交的事件。append-only，是事件溯源的基石。

    new_state_version = previous_version + 1。
    回放: snapshot(version=N) + events[v>N] -> state(version=M)。
    """

    event_id: str
    event_type: str
    actor_ids: List[str] = Field(default_factory=list)
    target_ids: List[str] = Field(default_factory=list)
    action_id: Optional[str] = None
    preconditions: List[str] = Field(default_factory=list)
    patch: StatePatch = Field(default_factory=StatePatch)
    random_seed: Optional[int] = None
    previous_version: int = 0
    new_version: int = 1
    summary: str = ""
    presentation_events: List[Dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Narrative: 已发生事实的可读化表现 (Observation 层)
# ---------------------------------------------------------------------------


class DialogueLine(BaseModel):
    """一句对白。speaker 必须是已存在且存活的角色。"""

    speaker_id: str
    line: str
    tone: str = ""  # 语气: 冷淡/愤怒/嘲讽...
    to_id: Optional[str] = None  # 对谁说

    class Config:
        extra = "forbid"


class NarrativeOutput(BaseModel):
    """叙事生成结果。把已提交的 WorldEvent 翻译成玩家可读的内容。

    这是"表现层"的数据契约: 前端按 narration/dialogues/system_hints 渲染。
    所有内容必须忠于已提交状态，不得违反角色认知。
    """

    narration: str = ""  # 旁白: 描写发生了什么
    dialogues: List[DialogueLine] = Field(default_factory=list)
    system_hints: List[str] = Field(default_factory=list)  # 系统提示: 如"夜清清对你起了疑心"
    viewpoint: str = "third_person"  # 视角: third_person / character_id
    grounded_event_ids: List[str] = Field(default_factory=list)
    referenced_entity_ids: List[str] = Field(default_factory=list)

    class Config:
        extra = "forbid"


# ---------------------------------------------------------------------------
# 角色 Agent: 人格 / 目标 / 计划 (plan 第八步)
# ---------------------------------------------------------------------------


class AgentGoal(AllowExtra):
    """角色的一个目标。Utility 决策会对活跃目标打分。

    priority 是初始权重 (可在剧情中调整)；只有 status=active 且处于
    当前时间线/世界作用域的目标才会进入 Agent 决策。
    """

    goal_id: str
    description: str  # 人话目标: "除掉夜轻歌、上位嫁小王爷"
    priority: float = Field(0.5, ge=0.0, le=1.0)
    target_ids: List[str] = Field(default_factory=list)  # 目标针对谁
    achieved: bool = False
    goal_key: str = ""
    status: str = "active"
    scope: str = "world"  # chapter / arc / timeline / world / book
    timeline_id: str = ""
    world_id: str = ""
    introduced_chapter: int = 0
    last_progress_chapter: int = 0
    terminal_chapter: Optional[int] = None
    terminal_reason: str = ""
    evolution: List[Dict[str, Any]] = Field(default_factory=list)


class PlanConditionKind(str, Enum):
    """权威 Runtime 可判定的计划步骤完成条件。"""

    item_owner = "item_owner"
    character_at = "character_at"
    belief_known = "belief_known"
    information_propagated = "information_propagated"
    alliance_formed = "alliance_formed"
    tool_committed = "tool_committed"


class PlanStepCondition(BaseModel):
    """一个计划步骤的确定性完成条件。

    条件只引用权威状态或已通过门禁的 ToolCall。它不是交给模型填写的
    ``StatePatch``，而是世界作者声明的 GOAP/HTN 式成功谓词。
    """

    kind: PlanConditionKind
    item_id: Optional[str] = None
    character_id: Optional[str] = None
    location_id: Optional[str] = None
    fact_id: Optional[str] = None
    source_character_id: Optional[str] = None
    target_character_id: Optional[str] = None
    member_ids: List[str] = Field(default_factory=list)
    goal_key: Optional[str] = None
    shared_fact_id: Optional[str] = None
    minimum_member_count: int = Field(2, ge=2)
    min_confidence: float = Field(0.0, ge=0.0, le=1.0)
    actor_id: Optional[str] = None
    tool_name: Optional[str] = None
    argument_equals: Dict[str, Any] = Field(default_factory=dict)

    @root_validator
    def validate_required_fields(cls, values):
        kind = values.get("kind")
        required = {
            PlanConditionKind.item_owner: ("item_id", "character_id"),
            PlanConditionKind.character_at: ("character_id", "location_id"),
            PlanConditionKind.belief_known: ("character_id", "fact_id"),
            PlanConditionKind.information_propagated: (
                "source_character_id",
                "target_character_id",
                "fact_id",
            ),
            PlanConditionKind.tool_committed: ("tool_name",),
        }.get(kind, ())
        missing = [name for name in required if not values.get(name)]
        if kind == PlanConditionKind.alliance_formed:
            if not values.get("member_ids"):
                missing.append("member_ids>=1")
            if values.get("minimum_member_count", 2) < len(
                values.get("member_ids") or []
            ):
                missing.append("minimum_member_count>=len(member_ids)")
        if missing:
            raise ValueError(
                "%s requires %s" % (kind.value, ", ".join(missing))
            )
        return values

    class Config:
        extra = "forbid"


class AgentPlan(AllowExtra):
    """角色当前的计划 (简化为有序步骤)。对应 plan 第八节 "当前计划 Plan"。

    本阶段是单一线性计划；后续可升级为多分支目标树。
    """

    plan_id: str
    goal_id: str
    steps: List[str] = Field(default_factory=list)  # 人话步骤，如 ["先示弱","寻机陷害"]
    step_conditions: List[PlanStepCondition] = Field(default_factory=list)
    current_step: int = 0  # 指向 steps 索引
    status: str = "active"  # active / paused / completed / abandoned

    @root_validator
    def conditions_align_with_steps(cls, values):
        steps = values.get("steps") or []
        conditions = values.get("step_conditions") or []
        if conditions and len(conditions) != len(steps):
            raise ValueError("step_conditions must align one-to-one with steps")
        return values


class CharacterPsyche(AllowExtra):
    """角色 Agent 的内在状态: 人格 + 目标 + 计划 + 情绪 + 记忆指针。

    挂在 WorldState 里 (character_psyches[character_id])，随事件演化。
    Agent 决策时读取它，决策后由 patch 更新它。
    """

    character_id: str
    traits: List[str] = Field(default_factory=list)  # ["阴毒","隐忍","心机深"]
    emotion: str = ""  # 当前主导情绪: "屈辱"、"惊疑"、"得意"
    emotion_intensity: float = Field(0.5, ge=0.0, le=1.0)
    goals: List[AgentGoal] = Field(default_factory=list)
    plans: List[AgentPlan] = Field(default_factory=list)
    # 短期工作记忆: 最近感知到的事件摘要 (供 LLM 决策参考)
    recent_perceptions: List[str] = Field(default_factory=list)
    is_player: bool = False  # 玩家宿主不自动行动 (由人操控)


class AgentCandidateAction(BaseModel):
    """LLM 提议的候选动作之一 (含理由与效用)。

    下游会把它转成真正的 Action + 候选 StatePatch 再校验。
    """

    action_type: str  # 借用 ActionType 的 value (不直接用枚举，便于容错)
    intent: str  # 人话意图: "当众反讽夜轻歌不自量力"
    target_ids: List[str] = Field(default_factory=list)
    dialogue: str = ""  # 若为 speak/attack 类，附一句台词
    tone: str = ""
    expected_patch: List[Dict[str, Any]] = Field(default_factory=list)  # LLM 预期状态变化 (草稿 op)
    utility: float = 0.0  # LLM 自评效用 (0-1)，调度器可二次打分覆盖
    rationale: str = ""  # 为何选这个动作

    class Config:
        extra = "allow"


class AgentDecision(BaseModel):
    """一次角色决策的完整产物。角色 Agent 的"提议单元"。"""

    character_id: str
    decided: bool = False  # False = 本轮选择按兵不动
    action: Optional[AgentCandidateAction] = None
    emotion_update: str = ""  # 决策后的新情绪
    emotion_intensity: Optional[float] = None
    perception_summary: str = ""  # 这一轮角色"感受"到什么 (可进记忆)

    class Config:
        extra = "allow"


# WorldState 用了前向引用字符串 "CharacterPsyche" (定义在其后)，
# Pydantic 1.x 需要在所有相关模型定义完成后解析一次。
WorldState.update_forward_refs()

