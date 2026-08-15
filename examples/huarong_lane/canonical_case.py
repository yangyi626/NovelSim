"""Curated runtime checkpoint for the chapter 1-5 canon reconstruction case.

The full-book compiler is optimized for entity extraction and creator review;
its early snapshots intentionally leave many runtime positions unlabelled.
This module turns the source-backed chapter-1 checkpoint into a playable state
without copying novel prose.  Future canonical events are stored separately in
the evaluation case and are never attached to this state.
"""

from __future__ import annotations

from world_schema import AgentGoal, AgentPlan, Character, CharacterCapability, CharacterPsyche, Location

from .scenario import (
    GRANDPA,
    LIN,
    NIGHT,
    QINGQING,
    SCENE_ID,
    build_snapshot,
)


JIYUE = "char_jiyue"
LUHE = "char_luhe"
YEZHENGXIONG = "char_yezhengxiong"
YEFU = "loc_yefu"
MYSTIC_SPACE = "loc_jiyue_space"
SANSHENG_SPRING = "loc_sansheng_spring"
FENGYUE_PAVILION = "loc_fengyue_pavilion"
YE_CLAN_HALL = "loc_ye_clan_hall"
PULL_INTO_SPACE = "mystic.pull_into_space"
OPEN_SANSHENG_SPRING = "mystic.open_sansheng_spring"
RETURN_FROM_SPACE = "mystic.return_from_space"
ISSUE_HALL_SUMMONS = "social.issue_hall_summons"


def build_canonical_start_state():
    """Return the source-backed state immediately after the chapter-1 clash."""

    state = build_snapshot()
    state.timeline_id = "canon_first_crazy_ch1_5"
    state.world_time = "北月国·华容巷冲突当日"
    state.current_scene_id = SCENE_ID
    state.flags.update(
        {
            "canonical.source_book_id": "first_crazy_waste_third_lady",
            "canonical.checkpoint_chapter": 1,
            "canonical.future_events_exposed_to_planner": False,
            "plot.transmigration_done": True,
        }
    )
    state.characters[NIGHT].location_id = SCENE_ID
    state.characters[QINGQING].location_id = SCENE_ID
    state.characters[LIN].location_id = YEFU
    state.characters[GRANDPA].location_id = YEFU
    state.locations.update(
        {
            MYSTIC_SPACE: Location(
                location_id=MYSTIC_SPACE,
                display_name="姬月异空间",
                accessible=False,
                blocks_ordinary_exit=True,
            ),
            SANSHENG_SPRING: Location(
                location_id=SANSHENG_SPRING,
                display_name="三生泉",
                parent_id=MYSTIC_SPACE,
                accessible=False,
                blocks_ordinary_exit=True,
            ),
            FENGYUE_PAVILION: Location(
                location_id=FENGYUE_PAVILION,
                display_name="风月阁",
                parent_id=YEFU,
                accessible=True,
                requires_flag="canonical.returned_fengyue_pavilion",
            ),
            YE_CLAN_HALL: Location(
                location_id=YE_CLAN_HALL,
                display_name="夜家大堂",
                parent_id=YEFU,
                accessible=True,
                requires_flag="canonical.hall_summons_issued",
            ),
        }
    )
    state.characters.update(
        {
            JIYUE: Character(
                character_id=JIYUE,
                display_name="姬月",
                aliases=["猫狐", "火红大袍男子"],
                location_id=MYSTIC_SPACE,
                identity_tags=["异空间主人", "神秘强者"],
            ),
            LUHE: Character(
                character_id=LUHE,
                display_name="绿荷",
                aliases=["夜清清的丫鬟"],
                location_id=FENGYUE_PAVILION,
                identity_tags=["丫鬟", "夜清清阵营"],
            ),
            YEZHENGXIONG: Character(
                character_id=YEZHENGXIONG,
                display_name="夜正熊",
                aliases=["夜家家主"],
                location_id=YE_CLAN_HALL,
                identity_tags=["家主", "夜家嫡系"],
            ),
        }
    )
    common_capabilities = [
        CharacterCapability(capability_id="movement.walk"),
        CharacterCapability(capability_id="social.speak"),
        CharacterCapability(capability_id="perception.observe"),
    ]
    for actor_id in (JIYUE, LUHE, YEZHENGXIONG):
        state.character_capabilities[actor_id] = [
            item.copy(deep=True) for item in common_capabilities
        ]
    state.character_capabilities[JIYUE].extend(
        [
            CharacterCapability(capability_id=PULL_INTO_SPACE),
            CharacterCapability(capability_id=OPEN_SANSHENG_SPRING),
            CharacterCapability(capability_id=RETURN_FROM_SPACE),
        ]
    )
    state.character_capabilities[LUHE].append(
        CharacterCapability(capability_id=ISSUE_HALL_SUMMONS)
    )
    # Trusted ability specifications belong to the world package.  The LLM sees
    # only the capability IDs and can request an invocation; it never receives
    # or writes these deterministic patches.
    state.flags["runtime.ability_specs"] = {
        PULL_INTO_SPACE: {
            "owner_id": JIYUE,
            "target_character_id": NIGHT,
            "destination_id": MYSTIC_SPACE,
            "actor_source_locations": [MYSTIC_SPACE],
            "target_source_locations": [YEFU],
            "move_actor": True,
            "move_target": True,
            "required_flags": {"canonical.lin_warning_done": True},
            "completion_flag": "canonical.entered_mystic_space",
            "summary": "姬月触发异空间联系，将夜轻歌带入姬月异空间",
            "perceptions": {
                NIGHT: "神秘猫狐触发空间变化，我进入了陌生异空间。",
                JIYUE: "夜轻歌的灵魂变化已触发异空间联系。",
            },
        },
        OPEN_SANSHENG_SPRING: {
            "owner_id": JIYUE,
            "target_character_id": NIGHT,
            "destination_id": SANSHENG_SPRING,
            "actor_source_locations": [MYSTIC_SPACE],
            "target_source_locations": [MYSTIC_SPACE],
            "requires_co_location": True,
            "required_flags": {
                "canonical.entered_mystic_space": True,
                "canonical.jiyue_revealed": True,
            },
            "move_actor": True,
            "move_target": True,
            "completion_flag": "canonical.entered_sansheng_spring",
            "summary": "姬月开放三生泉，将夜轻歌带入泉中疗伤",
            "perceptions": {NIGHT: "姬月开放三生泉供我疗伤。"},
        },
        RETURN_FROM_SPACE: {
            "owner_id": JIYUE,
            "target_character_id": NIGHT,
            "destination_id": FENGYUE_PAVILION,
            "actor_source_locations": [SANSHENG_SPRING],
            "target_source_locations": [SANSHENG_SPRING],
            "requires_co_location": True,
            "required_flags": {
                "canonical.entered_sansheng_spring": True,
                "canonical.dantian_promise": True,
            },
            "move_actor": True,
            "move_target": True,
            "completion_flag": "canonical.returned_fengyue_pavilion",
            "summary": "姬月关闭异空间通道，将夜轻歌送回风月阁",
            "perceptions": {NIGHT: "我已从异空间返回风月阁。"},
        },
        ISSUE_HALL_SUMMONS: {
            "owner_id": LUHE,
            "target_character_id": NIGHT,
            "destination_id": "",
            "actor_source_locations": [FENGYUE_PAVILION],
            "target_source_locations": [FENGYUE_PAVILION],
            "requires_co_location": True,
            "move_actor": False,
            "move_target": False,
            "required_flags": {"canonical.returned_fengyue_pavilion": True},
            "completion_flag": "canonical.hall_summons_issued",
            "summary": "绿荷奉命传唤夜轻歌前往夜家大堂",
            "perceptions": {NIGHT: "绿荷传令，要求我立即前往夜家大堂。"},
        },
    }
    state.flags["runtime.dialogue_effects"] = [
        {
            "speaker_id": NIGHT,
            "target_character_id": LIN,
            "location_id": YEFU,
            "completion_flag": "canonical.lin_warning_done",
        },
        {
            "speaker_id": LIN,
            "target_character_id": NIGHT,
            "location_id": YEFU,
            "completion_flag": "canonical.lin_warning_done",
        },
        {
            "speaker_id": JIYUE,
            "target_character_id": NIGHT,
            "location_id": MYSTIC_SPACE,
            "required_flags": {"canonical.entered_mystic_space": True},
            "completion_flag": "canonical.jiyue_revealed",
        },
        {
            "speaker_id": JIYUE,
            "target_character_id": NIGHT,
            "location_id": SANSHENG_SPRING,
            "required_flags": {"canonical.entered_sansheng_spring": True},
            "completion_flag": "canonical.dantian_promise",
        },
    ]
    night = state.character_psyches[NIGHT]
    night.is_player = False
    night.recent_perceptions = [
        "夜清清正在华容巷当众指控我与林管家私通。",
        "我衣衫不整且身上有伤，必须先脱离围观并返回夜府。",
    ]
    night.plans = [
        AgentPlan(
            plan_id="canon_night_immediate_response",
            goal_id="goal_reversal",
            steps=["反驳当众诬陷", "恢复体面", "返回夜府寻找庇护"],
            current_step=0,
            status="active",
        )
    ]
    state.character_psyches[QINGQING].recent_perceptions = [
        "夜轻歌醒来后态度与过去不同，并开始利用嫡庶身份反击。"
    ]
    state.character_psyches[LIN].recent_perceptions = [
        "夜清清要求我继续配合她压制夜轻歌。"
    ]
    state.character_psyches[JIYUE] = CharacterPsyche(
        character_id=JIYUE,
        traits=["强势", "神秘", "言语戏谑", "重视自身恢复"],
        emotion="观察等待",
        emotion_intensity=0.4,
        goals=[
            AgentGoal(
                goal_id="goal_jiyue_contact",
                description="确认夜轻歌灵魂变化并建立互利关系",
                priority=0.85,
                target_ids=[NIGHT],
                activation_target_location_ids=[
                    YEFU,
                    MYSTIC_SPACE,
                    SANSHENG_SPRING,
                ],
            )
        ],
        plans=[
            AgentPlan(
                plan_id="plan_jiyue_contact",
                goal_id="goal_jiyue_contact",
                steps=["等待接触契机", "解释双方处境", "提出互利承诺"],
                status="active",
            )
        ],
    )
    state.character_psyches[LUHE] = CharacterPsyche(
        character_id=LUHE,
        traits=["势利", "仗势欺人", "服从夜清清"],
        emotion="轻蔑",
        emotion_intensity=0.6,
        goals=[
            AgentGoal(
                goal_id="goal_summon_night",
                description="奉命把夜轻歌带到夜家大堂接受质问",
                priority=0.8,
                target_ids=[NIGHT],
                activation_target_location_ids=[FENGYUE_PAVILION],
            )
        ],
    )
    state.character_psyches[YEZHENGXIONG] = CharacterPsyche(
        character_id=YEZHENGXIONG,
        traits=["重视家族颜面", "威严", "偏见"],
        emotion="震怒",
        emotion_intensity=0.75,
        goals=[
            AgentGoal(
                goal_id="goal_question_night",
                description="在夜家大堂查问华容巷丑闻并维护家族秩序",
                priority=0.85,
                target_ids=[NIGHT],
                activation_target_location_ids=[YE_CLAN_HALL],
            )
        ],
    )
    return state


__all__ = [
    "FENGYUE_PAVILION",
    "JIYUE",
    "LUHE",
    "MYSTIC_SPACE",
    "SANSHENG_SPRING",
    "YEZHENGXIONG",
    "YE_CLAN_HALL",
    "YEFU",
    "PULL_INTO_SPACE",
    "OPEN_SANSHENG_SPRING",
    "RETURN_FROM_SPACE",
    "ISSUE_HALL_SUMMONS",
    "build_canonical_start_state",
]
