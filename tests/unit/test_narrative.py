"""NarrativeGenerator + 一致性审查器测试。"""

import json
from unittest import mock

import pytest

from engine import NarrativeGenerator, check_narrative
from engine.narrative import _extract_json
from world_schema import (
    Action,
    Actor,
    ActionType,
    DialogueLine,
    NarrativeOutput,
    Operation,
    OperationKind,
    StatePatch,
    WorldEvent,
)

from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import LIN, NIGHT, OUTER_ROBE, QINGQING


# ---------------------------------------------------------------------------
# 一致性审查器
# ---------------------------------------------------------------------------


class TestConsistencyChecker:
    def test_dead_speaker_rejected(self, snapshot):
        dead = snapshot.copy(deep=True)
        dead.characters[LIN].is_alive = False
        narr = NarrativeOutput(dialogues=[DialogueLine(speaker_id=LIN, line="我还活着")])
        r = check_narrative(narr, _ev(), dead)
        assert not r.valid
        assert "speaker_alive" in r.why()

    def test_unknown_speaker_rejected(self, snapshot):
        narr = NarrativeOutput(dialogues=[DialogueLine(speaker_id="ghost", line="...")])
        r = check_narrative(narr, _ev(), snapshot)
        assert not r.valid
        assert "speaker_exists" in r.why()

    def test_valid_dialogue_passes(self, snapshot):
        narr = NarrativeOutput(
            narration="夜轻歌冷冷开口",
            dialogues=[DialogueLine(speaker_id=NIGHT, line="脱下来", to_id=QINGQING)],
        )
        assert check_narrative(narr, _ev(), snapshot).valid

    def test_knowledge_leak_warning(self, snapshot):
        # 夜清清 belief 里 fact_qingqing_poisoned_tea=believed_true (她干的)
        # 但测一个她 unknown 的 fact: 临时塞一个，带中文关键词
        from world_schema import CharacterBelief
        from world_schema.models import Belief
        s = snapshot.copy(deep=True)
        s.beliefs[QINGQING].append(CharacterBelief(
            fact_id="fact_night_is_transmigrator",
            belief=Belief.unknown,
            keywords=["穿越者", "佣兵王"],
        ))
        # 夜清清对白里提到 "穿越者" -> 触发 warning (但不是 error，仍 valid)
        narr = NarrativeOutput(
            dialogues=[DialogueLine(
                speaker_id=QINGQING,
                line="你居然是个穿越者！",
            )],
        )
        r = check_narrative(narr, _ev(), s)
        # warning 不阻断 valid
        assert r.valid
        assert any(v.rule_id == "knowledge_leak" for v in r.violations)


# ---------------------------------------------------------------------------
# NarrativeGenerator (mock)
# ---------------------------------------------------------------------------


def _ev(**kw):
    base = dict(event_id="e1", event_type="swap_object")
    base.update(kw)
    return WorldEvent(**base)


def _make_gen(raw_outputs):
    g = NarrativeGenerator()
    g._call_llm = mock.Mock(side_effect=raw_outputs)
    return g


def _event_with_robe_transfer():
    patch = StatePatch(operations=[
        Operation(op=OperationKind.transfer_item, item_id=OUTER_ROBE, target_id=NIGHT,
                  reason="夜轻歌拿走外衫"),
    ])
    return WorldEvent(
        event_id="e1", event_type="swap_object",
        actor_ids=[NIGHT], target_ids=[OUTER_ROBE],
        patch=patch,
    )


class TestGeneratorParse:
    def test_valid_narrative_returned(self, snapshot):
        raw = json.dumps({
            "narration": "夜轻歌趁夜清清不备，将其外衫取过，反手披在肩上。",
            "dialogues": [
                {"speaker_id": NIGHT, "line": "不愧是我的好妹妹。",
                 "tone": "欣慰", "to_id": QINGQING}
            ],
            "system_hints": ["夜轻歌获得: 夜清清的外衫"],
            "viewpoint": "third_person",
        }, ensure_ascii=False)
        g = _make_gen([raw])
        ev = _event_with_robe_transfer()
        narr = g.generate(ev, snapshot)
        assert narr is not None
        assert "夜轻歌" in narr.narration
        assert len(narr.dialogues) == 1
        assert narr.dialogues[0].speaker_id == NIGHT

    def test_dead_speaker_triggers_retry(self, snapshot):
        """第一次让死人说活 -> 审查拒 -> 重试返回合法的。"""
        dead = snapshot.copy(deep=True)
        dead.characters[LIN].is_alive = False
        bad = json.dumps({
            "narration": "...",
            "dialogues": [{"speaker_id": LIN, "line": "我还说话"}],
        }, ensure_ascii=False)
        good = json.dumps({"narration": "夜轻歌独自离去。", "dialogues": []},
                          ensure_ascii=False)
        g = _make_gen([bad, good])
        # 注意: 用 dead state，LIN 已死
        narr = g.generate(_event_with_robe_transfer(), dead)
        assert narr is not None
        assert narr.narration == "夜轻歌独自离去。"

    def test_garbage_then_valid(self, snapshot):
        good = json.dumps({"narration": "一切归于平静。", "dialogues": []},
                          ensure_ascii=False)
        g = _make_gen(["not json", good])
        assert g.generate(_event_with_robe_transfer(), snapshot) is not None

    def test_all_fail_returns_none(self, snapshot):
        g = _make_gen(["g1", "g2", "g3"])
        assert g.generate(_event_with_robe_transfer(), snapshot) is None
        assert g.last_error is not None


# ---------------------------------------------------------------------------
# JSON 提取
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_plain(self):
        assert _extract_json('{"a":1}') == {"a": 1}

    def test_code_block(self):
        assert _extract_json("```json\n{\"a\":2}\n```") == {"a": 2}

    def test_empty(self):
        assert _extract_json("") is None


# ---------------------------------------------------------------------------
# 真实 LLM (默认跳过)
# ---------------------------------------------------------------------------


@pytest.mark.llm
class TestRealLLMNarrative:
    def test_real_generate_smoke(self, snapshot):
        g = NarrativeGenerator()
        ev = _event_with_robe_transfer()
        action = Action(
            action_id="a1", action_type=ActionType.swap_object,
            actor=Actor(actor_id=NIGHT), target_ids=[OUTER_ROBE],
            declared_goal="反客为主拿走外衫",
        )
        narr = g.generate(ev, snapshot, action)
        if narr is None:
            pytest.skip(f"LLM 失败: {g.last_error}")
        print(f"\n[LLM-NARR] 旁白: {narr.narration}")
        for d in narr.dialogues:
            print(f"[LLM-NARR] {d.speaker_id}: {d.line} ({d.tone})")
        for h in narr.system_hints:
            print(f"[LLM-NARR] 提示: {h}")
        assert narr.narration  # 至少有旁白
