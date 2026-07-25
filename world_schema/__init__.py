"""世界数据标准 (WorldPackage Schema)。

整个项目的"南北极"：Action 是输入，StatePatch 是输出。
中间的规则引擎、角色 Agent、状态转移模型全部依附于这里定义的数据结构。

参考: docs/plan.md 第三节。
"""

from .models import (
    Action,
    ActionType,
    Actor,
    Belief,
    Character,
    CharacterBelief,
    CharacterRelation,
    Item,
    Location,
    Operation,
    OperationKind,
    PlotArc,
    RelationDimensions,
    Rule,
    StatePatch,
    WorldEvent,
    WorldRule,
    WorldState,
)

__all__ = [
    "Action",
    "ActionType",
    "Actor",
    "Belief",
    "Character",
    "CharacterBelief",
    "CharacterRelation",
    "Item",
    "Location",
    "Operation",
    "OperationKind",
    "PlotArc",
    "RelationDimensions",
    "Rule",
    "StatePatch",
    "WorldEvent",
    "WorldRule",
    "WorldState",
]
