"""华容巷世界包的构造逻辑。

为什么用工厂函数而不是直接写字面量:
- 每次测试/每次用户进入世界，都需要一份"干净副本"。Pydantic 的 copy(deep=True)
  比反复解析 JSON 更可靠，且字段类型在构造时即被校验。
- 集中维护"原著事实"，避免散落在各测试文件里漂移。
"""

from __future__ import annotations

from typing import Dict

from world_schema import (
    ActionPolicy,
    AgentGoal,
    AgentPlan,
    Character,
    CharacterBelief,
    CharacterCapability,
    CharacterPsyche,
    CharacterRelation,
    EntityAffordance,
    Item,
    Location,
    Operation,
    OperationKind,
    PlotArc,
    Rule,
    StatePatch,
    WorldConcept,
    WorldConstraint,
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


def build_world_concepts() -> Dict[str, WorldConcept]:
    """当前古代玄幻世界可用和明确不可用的概念目录。"""

    return {
        "concept_walk": WorldConcept(
            concept_id="concept_walk",
            display_name="徒步",
            aliases=["走路", "步行", "徒步"],
            category="transport",
        ),
        "concept_horse": WorldConcept(
            concept_id="concept_horse",
            display_name="马匹",
            aliases=["骑马", "马匹", "坐骑"],
            mention_patterns=[
                r"骑(?:着|上)?(?:一匹)?马",
                r"一匹[^，。\s]{0,8}马",
            ],
            category="transport",
            requires_entity=True,
            required_capability_ids=["transport.ride_horse"],
        ),
        "concept_airplane": WorldConcept(
            concept_id="concept_airplane",
            display_name="飞机",
            aliases=["飞机", "航空器", "直升机"],
            category="technology",
            available=False,
            requires_entity=True,
            required_capability_ids=["transport.pilot_aircraft"],
        ),
        "concept_teleportation": WorldConcept(
            concept_id="concept_teleportation",
            display_name="瞬移",
            aliases=["瞬移", "传送到", "空间传送", "凭空", "隔空取物", "直接变到"],
            category="magic",
            available=False,
            required_capability_ids=["magic.teleport"],
        ),
    }


def build_world_constraints() -> list:
    return [
        WorldConstraint(
            constraint_id="constraint_ancient_transport",
            category="technology",
            statement="当前北月国世界没有现代飞行器，也不存在已注册的瞬移体系。",
            allowed_concept_ids=["concept_walk", "concept_horse"],
            forbidden_concept_ids=[
                "concept_airplane",
                "concept_teleportation",
            ],
            strict_narrative_grounding=True,
            strict_knowledge_boundaries=True,
        )
    ]


def build_character_capabilities() -> Dict[str, list]:
    common = [
        CharacterCapability(capability_id="movement.walk"),
        CharacterCapability(capability_id="social.speak"),
        CharacterCapability(capability_id="perception.observe"),
    ]
    return {
        NIGHT: [cap.copy(deep=True) for cap in common],
        QINGQING: [cap.copy(deep=True) for cap in common],
        LIN: [cap.copy(deep=True) for cap in common],
        GRANDPA: [cap.copy(deep=True) for cap in common],
    }


def build_entity_affordances() -> Dict[str, list]:
    return {
        OUTER_ROBE: [
            EntityAffordance(
                affordance_id="affordance_outer_robe_transfer",
                entity_id=OUTER_ROBE,
                action_type="swap_object",
            )
        ]
    }


def build_action_policies() -> Dict[str, ActionPolicy]:
    return {
        "move": ActionPolicy(
            action_type="move",
            required_parameters=["destination_id"],
            required_capability_ids=["movement.walk"],
            affordance_parameter="transport_entity_id",
            allowed_patch_operations=["move_character"],
        ),
        "swap_object": ActionPolicy(
            action_type="swap_object",
            requires_target=True,
            affordance_from_target=True,
            allowed_patch_operations=[
                "transfer_item",
                "update_relation",
            ],
        ),
        "speak": ActionPolicy(
            action_type="speak",
            # speak 可是公开自言/宣告；定向对话由 talk_to 工具强制目标。
            requires_target=False,
            required_capability_ids=["social.speak"],
            allowed_patch_operations=[
                "update_relation",
                "update_belief",
            ],
        ),
        "observe": ActionPolicy(
            action_type="observe",
            required_capability_ids=["perception.observe"],
            allowed_patch_operations=["update_belief"],
        ),
        "investigate": ActionPolicy(
            action_type="investigate",
            required_capability_ids=["perception.observe"],
            allowed_patch_operations=["update_belief"],
        ),
        "gift": ActionPolicy(
            action_type="gift",
            requires_target=True,
            affordance_from_target=True,
            allowed_patch_operations=["transfer_item", "update_relation"],
        ),
        "use_item": ActionPolicy(
            action_type="use_item",
            requires_target=True,
            affordance_from_target=True,
            allowed_patch_operations=[
                "set_attr",
                "update_belief",
            ],
        ),
        "attack": ActionPolicy(
            action_type="attack",
            requires_target=True,
            allowed_patch_operations=[
                "set_attr",
                "update_relation",
                "kill_character",
            ],
        ),
    }


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


def build_psyches() -> dict:
    """角色 Agent 内在状态 (plan 第八步)。

    关键:
    - 夜轻歌是玩家宿主，is_player=True，永不被自动调度 (由人操控)。
    - 夜清清/林管家是自主 NPC：各有目标、计划、人格、情绪。
      这些是"原著锚点前"的初始心态，会随玩家行动演化。
    - 夜青天(爷爷)暂不配 psyche (溺爱祖父型，被动反应即可)，留待扩展。
    """
    return {
        NIGHT: CharacterPsyche(
            character_id=NIGHT,
            traits=["果决", "冷酷", "佣兵王灵魂"],
            emotion="冷峻审视",
            emotion_intensity=0.6,
            goals=[
                AgentGoal(
                    goal_id="goal_reversal",
                    description="反客为主、洗刷废柴之名、掌控夜府",
                    priority=0.8,
                    target_ids=[QINGQING],
                ),
            ],
            plans=[
                AgentPlan(
                    plan_id="plan_assert_dominance",
                    goal_id="goal_reversal",
                    steps=["先立威压服庶妹", "查清下药陷害真相", "夺回主导权"],
                    current_step=0,
                    status="active",
                ),
            ],
            is_player=True,
        ),
        QINGQING: CharacterPsyche(
            character_id=QINGQING,
            traits=["阴毒", "隐忍", "心机深", "嫉妒成性"],
            emotion="惊疑忌惮",
            emotion_intensity=0.5,
            goals=[
                AgentGoal(
                    goal_id="goal_usurp",
                    description="除掉夜轻歌、上位嫁小王爷",
                    priority=0.9,
                    target_ids=[NIGHT],
                ),
            ],
            plans=[
                AgentPlan(
                    plan_id="plan_frame_sister",
                    goal_id="goal_usurp",
                    steps=["以通奸罪名陷夜轻歌于死地", "联合林管家造势", "取代嫡位"],
                    current_step=0,
                    status="active",
                ),
            ],
        ),
        LIN: CharacterPsyche(
            character_id=LIN,
            traits=["圆滑", "趋炎附势", "与夜清清有私情"],
            emotion="观望盘算",
            emotion_intensity=0.4,
            goals=[
                AgentGoal(
                    goal_id="goal_protect_qingqing",
                    description="保全夜清清、维护自身在夜府的地位",
                    priority=0.7,
                    target_ids=[QINGQING],
                ),
            ],
            plans=[
                AgentPlan(
                    plan_id="plan_assist_framing",
                    goal_id="goal_protect_qingqing",
                    steps=["配合夜清清的陷害计划", "见风使舵保全自己"],
                    current_step=0,
                    status="active",
                ),
            ],
        ),
    }


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
        world_concepts=build_world_concepts(),
        world_constraints=build_world_constraints(),
        character_capabilities=build_character_capabilities(),
        entity_affordances=build_entity_affordances(),
        action_policies=build_action_policies(),
        character_psyches=build_psyches(),
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
