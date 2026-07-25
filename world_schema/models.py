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

from pydantic import BaseModel, Field


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
    source_event_id: Optional[str] = None


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
    plot: Dict[str, PlotArc] = Field(default_factory=dict)
    rules: List[Rule] = Field(default_factory=list)
    world_rules: List[WorldRule] = Field(default_factory=list)
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
    update_relation = "update_relation"  # 关系维度增量
    set_relation = "set_relation"  # 关系整体替换
    update_belief = "update_belief"
    kill_character = "kill_character"
    revive_character = "revive_character"
    change_identity = "change_identity"  # 改 identity_tags
    start_plot = "start_plot"
    advance_plot = "advance_plot"
    complete_plot = "complete_plot"


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
    tags: Optional[List[str]] = None  # change_identity
    fact_id: Optional[str] = None

    class Config:
        extra = "forbid"  # Operation 必须严格受控


class StatePatch(BaseModel):
    """本轮允许的状态变化集合。规则引擎产出，一致性审查后提交。"""

    operations: List[Operation] = Field(default_factory=list)
    notes: str = ""

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
