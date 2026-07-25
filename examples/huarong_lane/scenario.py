"""华容巷世界包的构造逻辑。

为什么用工厂函数而不是直接写字面量:
- 每次测试/每次用户进入世界，都需要一份"干净副本"。Pydantic 的 copy(deep=True)
  比反复解析 JSON 更可靠，且字段类型在构造时即被校验。
- 集中维护"原著事实"，避免散落在各测试文件里漂移。
"""

from __future__ import annotations

from typing import Dict

from world_schema import (
    Character,
    CharacterBelief,
    CharacterRelation,
    Item,
    Location,
    Operation,
    OperationKind,
    PlotArc,
    Rule,
    StatePatch,
    WorldRule,
    WorldState,
)
from world_schema.models import Belief


# 场景内固定 ID，规则引擎和测试都引用这些常量，避免拼写漂移。
SCENE_ID = "loc_huarong_lane"
NIGHT = "char_yeqingge"          # 夜轻歌 (嫡三小姐，用户宿主)
QINGQING = "char_yeqingqing"     # 夜清清 (庶妹，反派)
LIN = "char_lin_guanjia"         # 林管家
GRANDPA = "char_yeqingtian"      # 夜青天 (大长老，爷爷)
OUTER_ROBE = "item_qingqing_outer_robe"  # 夜清清的外衫


def build_world_rules() -> list:
    return [
        WorldRule(
            rule_id="rule_concubine_obey",
            category="politics",
            statement="北月国长幼尊卑: 庶出不可忤逆嫡系，否则大逆不道。",
        ),
        WorldRule(
            rule_id="rule_dantian",
            category="magic",
            statement="丹田破碎者无法修炼，被视为废物。",
        ),
        WorldRule(
            rule_id="rule_engagement",
            category="identity",
            statement="夜轻歌为小王爷未过门王妃，婚约未解除前不得另嫁。",
        ),
    ]


def build_locations() -> Dict[str, Location]:
    return {
        SCENE_ID: Location(
            location_id=SCENE_ID,
            display_name="华容巷",
            parent_id="loc_yefu",
            accessible=True,
        ),
        "loc_yefu": Location(
            location_id="loc_yefu",
            display_name="夜府",
            accessible=True,
        ),
    }


def build_characters() -> Dict[str, Character]:
    return {
        NIGHT: Character(
            character_id=NIGHT,
            display_name="夜轻歌",
            aliases=["三小姐", "废柴三小姐"],
            is_alive=True,
            location_id=SCENE_ID,
            identity_tags=["嫡系", "三小姐", "未婚", "废柴"],
            attrs={
                "cultivation_level": "凡人",
                "dantian_broken": True,
                "birthmark": "半脸紫红胎记",
                "soul": "佣兵王·无名(穿越)",
                "body_condition": "衣衫不整、遍体鳞伤",
            },
        ),
        QINGQING: Character(
            character_id=QINGQING,
            display_name="夜清清",
            aliases=["庶妹"],
            is_alive=True,
            location_id=SCENE_ID,
            identity_tags=["庶出", "妹妹"],
            attrs={
                "cultivation_level": "凡人",
                "secret_goal": "上位嫁小王爷",
            },
        ),
        LIN: Character(
            character_id=LIN,
            display_name="林管家",
            aliases=["林总管"],
            is_alive=True,
            location_id="loc_yefu",
            identity_tags=["夜家总管"],
            attrs={"age": 20, "relation_to_qingqing": "私情"},
        ),
        GRANDPA: Character(
            character_id=GRANDPA,
            display_name="夜青天",
            aliases=["大长老", "爷爷"],
            is_alive=True,
            location_id="loc_yefu",
            identity_tags=["大长老"],
            attrs={"doting_on": NIGHT},
        ),
    }


def build_items() -> Dict[str, Item]:
    return {
        OUTER_ROBE: Item(
            item_id=OUTER_ROBE,
            display_name="夜清清的外衫",
            owner_id=QINGQING,
            location_id=None,
            unique=True,
            accessible=True,
        ),
    }


def build_relations() -> list:
    return [
        CharacterRelation(
            source_id=NIGHT,
            target_id=QINGQING,
            public_relation="嫡姐-庶妹",
            private_relation="防备",
            dimensions={"affection": -0.2, "trust": -0.5, "hostility": 0.3},
        ),
        CharacterRelation(
            source_id=QINGQING,
            target_id=NIGHT,
            public_relation="庶妹-嫡姐",
            private_relation="嫉妒/陷害",
            dimensions={"affection": -0.6, "trust": -0.8, "hostility": 0.7},
        ),
        CharacterRelation(
            source_id=QINGQING,
            target_id=LIN,
            public_relation="主仆",
            private_relation="私情",
            dimensions={"affection": 0.4, "trust": 0.5},
        ),
        CharacterRelation(
            source_id=GRANDPA,
            target_id=NIGHT,
            public_relation="祖孙",
            private_relation="溺爱",
            dimensions={"affection": 0.9, "trust": 0.8},
        ),
    ]


def build_beliefs() -> dict:
    """锚点前各角色已知/未知的事实。

    关键: 夜清清"下药陷害"是她干的，她知道；夜轻歌(本尊)昏迷不知，
    被穿越者接管后从记忆得知。林管家是否知情——原著留白，设为 suspected。
    """
    return {
        NIGHT: [
            CharacterBelief(
                fact_id="fact_qingqing_poisoned_tea",
                belief=Belief.believed_true,
                confidence=0.9,
                source_type="inference",
                source_event_id="event_canon_0001",
            ),
            CharacterBelief(
                fact_id="fact_self_in_huarong_lane",
                belief=Belief.believed_true,
                confidence=1.0,
                source_type="observation",
            ),
        ],
        QINGQING: [
            CharacterBelief(
                fact_id="fact_qingqing_poisoned_tea",
                belief=Belief.believed_true,
                confidence=1.0,
                source_type="secret",
            ),
            CharacterBelief(
                fact_id="fact_self_framing_sister",
                belief=Belief.believed_true,
                confidence=1.0,
                source_type="secret",
            ),
        ],
        LIN: [
            CharacterBelief(
                fact_id="fact_qingqing_poisoned_tea",
                belief=Belief.suspected_true,
                confidence=0.5,
                source_type="inference",
            ),
        ],
    }


def build_rules() -> list:
    """确定性规则 (DSL 载体，本阶段主要由 engine.rules 内置校验消费)。"""
    return [
        Rule(
            rule_id="rule_swap_robe_same_scene",
            priority=50,
            description="交换物品需双方在同一场景",
            preconditions=[{"eq": ["actor.location_id", "target.location_id"]}],
            effects=[],
        ),
    ]


def build_snapshot() -> WorldState:
    """锚点前快照: 用户(快穿者)刚接管夜轻歌身体，尚未行动。"""
    return WorldState(
        timeline_id="runtime_huarong_lane_root",
        version=0,
        world_time="北月国·某日午前",
        current_scene_id=SCENE_ID,
        characters=build_characters(),
        items=build_items(),
        locations=build_locations(),
        relations=build_relations(),
        beliefs=build_beliefs(),
        plot={
            "arc_huarong_shaming": PlotArc(
                arc_id="arc_huarong_shaming",
                title="华容巷受辱",
                kind="main",
                stage="active",
                completed=False,
            ),
        },
        rules=build_rules(),
        world_rules=build_world_rules(),
        flags={
            "plot.shaming_in_progress": True,
            "plot.poisoning_happened": True,
            "plot.transmigration_done": True,
        },
    )


def build_world_package() -> dict:
    """返回整个世界包的结构化概览 (用于 manifest/调试)。"""
    snap = build_snapshot()
    return {
        "package_id": "huarong_lane",
        "novel": "第一狂妃：废柴三小姐",
        "source_chapters": ["第1章 华容巷", "第2章 那就脱！"],
        "anchor": "夜轻歌被诬通奸、当众受辱",
        "characters": list(snap.characters.keys()),
        "items": list(snap.items.keys()),
        "rules": [r.rule_id for r in snap.world_rules],
        "flags_at_anchor": dict(snap.flags),
    }
