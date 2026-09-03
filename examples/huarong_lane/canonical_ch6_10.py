"""Curated runtime checkpoint for the chapter 6-10 canon reconstruction case.

The checkpoint freezes the authoritative state right after the chapter-5
endpoint: 夜轻歌 stands summoned in 夜家大堂 while the clan confrontation is
about to unfold.  Chapter 6-10 progression, trusted abilities and dialogue
effects live here; the evaluation case separately stores abstract anchors so
novel prose never enters planner prompts.
"""

from __future__ import annotations

from world_schema import (
    AgentGoal,
    AgentPlan,
    Belief,
    Character,
    CharacterCapability,
    CharacterPsyche,
    Item,
    Location,
    WorldFact,
)

from .canonical_case import (
    FENGYUE_PAVILION,
    JIYUE,
    MYSTIC_SPACE,
    NIGHT,
    QINGQING,
    YE_CLAN_HALL,
    YEZHENGXIONG,
    YEFU,
    build_canonical_start_state,
)
from .scenario import LIN


QINLAN = "char_qinlan"
JINGJING = "char_yejingjing"
JINGJING_MOTHER = "char_jingjing_mother"

CORRIDOR = "loc_jiuqu_corridor"
INNER_CHAMBER = "loc_inner_chamber"

JADE_PENDANT = "item_glazed_jade_pendant"

PRESSURE_WAVE = "cultivation.spirit_pressure_wave"
NONLETHAL_RESTRAIN = "martial.nonlethal_restrain"
SHOW_JADE_PENDANT = "social.show_jade_pendant"
SEEK_COUNSEL = "mystic.seek_counsel"


def build_canonical_ch5_start_state():
    """Return the authoritative state at the end of chapter five."""

    state = build_canonical_start_state()
    state.timeline_id = "canon_first_crazy_ch6_10"
    state.world_time = "北月国·夜府当夜，华容巷风波次日"
    state.current_scene_id = YE_CLAN_HALL
    state.flags.update(
        {
            "canonical.checkpoint_chapter": 5,
            "canonical.hall_summons_issued": True,
            "canonical.returned_fengyue_pavilion": True,
            "plot.transmigration_done": True,
        }
    )
    # 第 1–5 章的章节级目标已经闭合；继续保留会把旧目标带进新世界线。
    for psyche in state.character_psyches.values():
        for goal in list(psyche.goals):
            psyche.goals.remove(goal)
        psyche.plans = []

    state.characters[NIGHT].location_id = YE_CLAN_HALL
    state.characters[QINGQING].location_id = YE_CLAN_HALL
    state.characters[LIN].location_id = YE_CLAN_HALL
    state.locations[FENGYUE_PAVILION] = Location(
        location_id=FENGYUE_PAVILION,
        display_name="风月阁",
        parent_id=YEFU,
        accessible=True,
        requires_flag="",
    )
    state.locations.update(
        {
            CORRIDOR: Location(
                location_id=CORRIDOR,
                display_name="九曲环廊",
                parent_id=YEFU,
                accessible=True,
            ),
            INNER_CHAMBER: Location(
                location_id=INNER_CHAMBER,
                display_name="正堂内室",
                parent_id=YEFU,
                accessible=False,
                requires_permission=["家主", "家主夫人"],
            ),
        }
    )
    state.characters.update(
        {
            QINLAN: Character(
                character_id=QINLAN,
                display_name="秦岚",
                aliases=["夜家家主夫人"],
                location_id=YE_CLAN_HALL,
                identity_tags=["家主夫人", "夜轻歌继母"],
            ),
            JINGJING: Character(
                character_id=JINGJING,
                display_name="夜菁菁",
                aliases=["小丫头"],
                location_id=YE_CLAN_HALL,
                identity_tags=["夜家幼辈", "天真烂漫"],
            ),
            JINGJING_MOTHER: Character(
                character_id=JINGJING_MOTHER,
                display_name="夜菁菁之母",
                aliases=["抱着小丫头的女子"],
                location_id=YE_CLAN_HALL,
                identity_tags=["夜家旁支"],
            ),
        }
    )
    state.items[JADE_PENDANT] = Item(
        item_id=JADE_PENDANT,
        display_name="琉璃玉佩",
        owner_id=LIN,
        accessible=True,
        attrs={"carved_character": "歌", "note": "来历存疑的信物"},
    )
    state.characters[LIN].inventory.append(JADE_PENDANT)

    # 第 1–5 章剧情中已确立的知识事实。角色信念引用这些 fact_id，
    # 规划器生成的 share_information/条件也必须能解析到权威事实条目。
    state.facts.update(
        {
            "fact_qingqing_poisoned_tea": WorldFact(
                fact_id="fact_qingqing_poisoned_tea",
                statement="华容巷风波中的茶水被动了手脚，夜清清与此事有关。",
                truth=Belief.believed_true,
                observable=False,
                base_confidence=0.95,
                keywords=["下毒", "茶", "陷害"],
            ),
            "fact_self_framing_sister": WorldFact(
                fact_id="fact_self_framing_sister",
                statement="夜清清构陷了自己的嫡姐。",
                truth=Belief.believed_true,
                observable=False,
                base_confidence=1.0,
                keywords=["构陷", "嫡姐"],
            ),
            "fact_self_in_huarong_lane": WorldFact(
                fact_id="fact_self_in_huarong_lane",
                statement="夜轻歌在华容巷冲突后幸存，并被穿越者接管。",
                truth=Belief.believed_true,
                observable=False,
                base_confidence=1.0,
                keywords=["华容巷", "夜轻歌"],
            ),
        }
    )

    common_capabilities = [
        CharacterCapability(capability_id="movement.walk"),
        CharacterCapability(capability_id="social.speak"),
        CharacterCapability(capability_id="perception.observe"),
    ]
    for actor_id in (NIGHT, LIN, QINGQING, QINLAN, YEZHENGXIONG):
        state.character_capabilities[actor_id] = [
            item.copy(deep=True) for item in common_capabilities
        ]
    state.character_capabilities[NIGHT].append(
        CharacterCapability(capability_id=NONLETHAL_RESTRAIN)
    )
    state.character_capabilities[LIN].append(
        CharacterCapability(capability_id=SHOW_JADE_PENDANT)
    )
    state.character_capabilities[YEZHENGXIONG].append(
        CharacterCapability(capability_id=PRESSURE_WAVE)
    )
    state.character_capabilities[JIYUE].append(
        CharacterCapability(capability_id=SEEK_COUNSEL)
    )

    # 受信能力规格属于世界包；LLM 只看到能力 ID，从不接触这些确定性补丁。
    state.flags["runtime.ability_specs"] = {
        PRESSURE_WAVE: {
            "owner_id": YEZHENGXIONG,
            "target_character_id": NIGHT,
            "move_actor": False,
            "move_target": False,
            "requires_co_location": True,
            "actor_source_locations": [YE_CLAN_HALL],
            "target_source_locations": [YE_CLAN_HALL],
            "required_flags": {"canonical.hall_summons_issued": True},
            "completion_flag": "canonical.spirit_pressure_applied",
            "summary": "夜正熊释放灵气威压，逼迫夜轻歌下跪",
            "perceptions": {
                NIGHT: "家主的灵气威压如刀剑般压来，我咳出血沫却没有下跪。",
            },
        },
        SHOW_JADE_PENDANT: {
            "owner_id": LIN,
            "target_character_id": NIGHT,
            "move_actor": False,
            "move_target": False,
            "requires_co_location": True,
            "actor_source_locations": [YE_CLAN_HALL],
            "target_source_locations": [YE_CLAN_HALL],
            "required_flags": {"canonical.refuse_marriage": True},
            "completion_flag": "canonical.jade_shown",
            "summary": "林尘高举琉璃玉佩，声称它是夜轻歌所赠的定情信物",
            "perceptions": {
                NIGHT: "林尘展示一块刻着歌字的琉璃玉佩，声称这是我给的定情信物。",
            },
        },
        NONLETHAL_RESTRAIN: {
            "owner_id": NIGHT,
            "target_character_id": QINGQING,
            "move_actor": False,
            "move_target": False,
            "requires_co_location": True,
            "actor_source_locations": [FENGYUE_PAVILION],
            "target_source_locations": [FENGYUE_PAVILION],
            "required_flags": {"canonical.identity_confrontation": True},
            "completion_flag": "canonical.qingqing_restrained",
            "summary": "夜轻歌以古武擒拿反制夜清清，未造成致命伤害",
            "perceptions": {
                QINGQING: "我被夜轻歌反手擒拿，颜面尽失却动弹不得。",
            },
        },
        SEEK_COUNSEL: {
            "owner_id": JIYUE,
            "target_character_id": NIGHT,
            "move_actor": False,
            "move_target": False,
            "required_flags": {"canonical.steam_bun_clue": True},
            "completion_flag": "canonical.counterattack_resolve",
            "summary": "姬月借空间联系与夜轻歌确认童年旧事，坚定主动反击之意",
            "perceptions": {
                NIGHT: "姬月确认了我童年分馒头的旧事；既然有人要我死，我不会坐以待毙。",
            },
        },
    }

    # 对话效应把关键推进从纯聊天转成服务器可信的完成标志。
    state.flags["runtime.dialogue_effects"] = [
        {
            "speaker_id": NIGHT,
            "target_character_id": YEZHENGXIONG,
            "location_id": YE_CLAN_HALL,
            "required_flags": {"canonical.spirit_pressure_applied": True},
            "completion_flag": "canonical.kneel_refused",
        },
        {
            "speaker_id": QINLAN,
            "target_character_id": NIGHT,
            "location_id": YE_CLAN_HALL,
            "required_flags": {"canonical.kneel_refused": True},
            "completion_flag": "canonical.marriage_forced_proposed",
        },
        {
            "speaker_id": NIGHT,
            "target_character_id": QINLAN,
            "location_id": YE_CLAN_HALL,
            "required_flags": {"canonical.marriage_forced_proposed": True},
            "completion_flag": "canonical.refuse_marriage",
        },
        {
            "speaker_id": NIGHT,
            "target_character_id": LIN,
            "location_id": YE_CLAN_HALL,
            "required_flags": {"canonical.jade_shown": True},
            "completion_flag": "canonical.jade_claim_dismissed",
        },
        {
            "speaker_id": NIGHT,
            "target_character_id": YEZHENGXIONG,
            "location_id": YE_CLAN_HALL,
            "required_flags": {"canonical.jade_claim_dismissed": True},
            "completion_flag": "canonical.elder_arbitration_requested",
        },
        {
            "speaker_id": JINGJING_MOTHER,
            "target_character_id": NIGHT,
            "location_id": CORRIDOR,
            "completion_flag": "canonical.jingjing_warning",
        },
        {
            "speaker_id": QINGQING,
            "target_character_id": NIGHT,
            "location_id": FENGYUE_PAVILION,
            "required_flags": {"canonical.elder_arbitration_requested": True},
            "completion_flag": "canonical.identity_confrontation",
        },
        {
            "speaker_id": QINGQING,
            "target_character_id": LIN,
            "location_id": FENGYUE_PAVILION,
            "required_flags": {"canonical.qingqing_restrained": True},
            "completion_flag": "canonical.kill_order_issued",
        },
        {
            "speaker_id": LIN,
            "target_character_id": NIGHT,
            "location_id": FENGYUE_PAVILION,
            "required_flags": {"canonical.kill_order_issued": True},
            "completion_flag": "canonical.steam_bun_clue",
        },
        {
            "speaker_id": QINLAN,
            "target_character_id": YEZHENGXIONG,
            "location_id": INNER_CHAMBER,
            "required_flags": {"canonical.steam_bun_clue": True},
            "completion_flag": "canonical.snow_marriage_scheme",
        },
    ]

    night_psyche = state.character_psyches[NIGHT]
    night_psyche.recent_perceptions = [
        "我应传唤抵达夜家大堂，所有人都在等我对华容巷丑闻表态。",
        "要想在夜青天出关前保住自己，必须正面撑过今晚。",
    ]
    night_psyche.plans = [
        AgentPlan(
            plan_id="canon_ch6_stand_ground",
            goal_id="goal_ch6_survive_hall",
            steps=[
                "顶住家主的威压与质问",
                "拆穿婚事算计和所谓信物",
                "回风月阁并掌握反制证据",
            ],
            current_step=0,
            status="active",
        )
    ]
    night_psyche.goals = [
        AgentGoal(
            goal_id="goal_ch6_survive_hall",
            description="在大堂对峙中不下跪、不被婚事算计困住",
            priority=0.9,
            target_ids=[YEZHENGXIONG],
            activation_target_location_ids=[YE_CLAN_HALL],
            scope="chapter",
            timeline_id="canon_first_crazy_ch6_10",
            introduced_chapter=6,
            last_progress_chapter=6,
            terminal_chapter=8,
        ),
        AgentGoal(
            goal_id="goal_ch9_secure_retreat",
            description="识破暗处的危险，安全退守风月阁",
            priority=0.75,
            target_ids=[QINGQING],
            activation_target_location_ids=[CORRIDOR, FENGYUE_PAVILION],
            scope="chapter",
            timeline_id="canon_first_crazy_ch6_10",
            introduced_chapter=9,
            terminal_chapter=10,
        ),
    ]
    state.character_psyches[QINGQING].recent_perceptions = [
        "大哥被夜轻歌抢了风头，我在大堂必须为林尘的事添柴加火。"
    ]
    state.character_psyches[QINGQING].goals = [
        AgentGoal(
            goal_id="goal_ch6_fall_night",
            description="趁丑闻让夜轻歌彻底失去退路",
            priority=0.85,
            target_ids=[NIGHT],
            activation_target_location_ids=[YE_CLAN_HALL, FENGYUE_PAVILION],
            scope="chapter",
            timeline_id="canon_first_crazy_ch6_10",
            introduced_chapter=6,
            terminal_chapter=10,
        )
    ]
    state.character_psyches[LIN].recent_perceptions = [
        "我要在大堂上把三小姐留在身边这件事坐实。"
    ]
    state.character_psyches[LIN].goals = [
        AgentGoal(
            goal_id="goal_ch6_push_marriage",
            description="坚持与夜轻歌的婚事说法",
            priority=0.7,
            target_ids=[NIGHT],
            activation_target_location_ids=[YE_CLAN_HALL],
            scope="chapter",
            timeline_id="canon_first_crazy_ch6_10",
            introduced_chapter=6,
            terminal_chapter=8,
        ),
        AgentGoal(
            goal_id="goal_ch9_watch_qingqing",
            description="守在风月阁外，关注夜清清的一举一动",
            priority=0.65,
            target_ids=[QINGQING],
            activation_target_location_ids=[FENGYUE_PAVILION],
            scope="chapter",
            timeline_id="canon_first_crazy_ch6_10",
            introduced_chapter=9,
            terminal_chapter=10,
        ),
    ]
    qinlan_psyche = CharacterPsyche(
        character_id=QINLAN,
        traits=["雍容", "深藏机心", "重家族布局"],
        emotion="从容",
        emotion_intensity=0.4,
        goals=[
            AgentGoal(
                goal_id="goal_ch6_direct_outcome",
                description="主导这场婚事风波的处理方式",
                priority=0.8,
                target_ids=[NIGHT],
                activation_target_location_ids=[YE_CLAN_HALL, INNER_CHAMBER],
                scope="chapter",
                timeline_id="canon_first_crazy_ch6_10",
                introduced_chapter=6,
                terminal_chapter=10,
            )
        ],
    )
    qinlan_psyche.recent_perceptions = [
        "丑闻已传遍京城，夜家的联姻棋局需要重新落子。"
    ]
    state.character_psyches[QINLAN] = qinlan_psyche
    state.character_psyches[JINGJING] = CharacterPsyche(
        character_id=JINGJING,
        traits=["天真", "口无遮拦"],
        emotion="好奇",
        emotion_intensity=0.3,
        goals=[
            AgentGoal(
                goal_id="goal_ch6_follow_curiosity",
                description="跟着娘亲看看热闹，说出自己听到的话",
                priority=0.4,
                target_ids=[NIGHT],
                activation_target_location_ids=[YE_CLAN_HALL, CORRIDOR],
                scope="chapter",
                timeline_id="canon_first_crazy_ch6_10",
                introduced_chapter=6,
                terminal_chapter=9,
            )
        ],
    )
    state.character_psyches[JINGJING_MOTHER] = CharacterPsyche(
        character_id=JINGJING_MOTHER,
        traits=["谨慎", "护短"],
        emotion="担忧",
        emotion_intensity=0.5,
        goals=[
            AgentGoal(
                goal_id="goal_ch9_guard_jingjing",
                description="管住菁菁的嘴，提醒三小姐夜里当心",
                priority=0.55,
                target_ids=[NIGHT],
                activation_target_location_ids=[CORRIDOR],
                scope="chapter",
                timeline_id="canon_first_crazy_ch6_10",
                introduced_chapter=9,
                terminal_chapter=9,
            )
        ],
    )
    state.character_psyches[YEZHENGXIONG].recent_perceptions = [
        "华容巷的丑闻几乎扫尽夜家颜面，必须在家法与大长老之间稳住局面。"
    ]
    state.character_psyches[YEZHENGXIONG].goals = [
        AgentGoal(
            goal_id="goal_ch6_defend_face",
            description="用家主威仪审住场面，避免把皇室牵扯进来",
            priority=0.85,
            target_ids=[NIGHT, LIN],
            activation_target_location_ids=[YE_CLAN_HALL, INNER_CHAMBER],
            scope="chapter",
            timeline_id="canon_first_crazy_ch6_10",
            introduced_chapter=6,
            terminal_chapter=10,
        )
    ]
    jiyue_psyche = state.character_psyches.get(JIYUE)
    if jiyue_psyche is not None:
        jiyue_psyche.goals = [
            AgentGoal(
                goal_id="goal_ch9_seek_counsel",
                description="在夜轻歌需要决断时确认童年旧事并推她一把",
                priority=0.6,
                target_ids=[NIGHT],
                scope="chapter",
                timeline_id="canon_first_crazy_ch6_10",
                introduced_chapter=9,
                terminal_chapter=10,
            )
        ]
    return state


__all__ = [
    "build_canonical_ch5_start_state",
]
