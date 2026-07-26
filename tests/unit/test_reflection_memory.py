"""带证据链、幂等更新和冲突保护的反思记忆测试。"""

import json

import pytest

from engine import (
    MemoryRecord,
    ReflectionCandidate,
    ReflectionGenerator,
    ReflectionSemanticJudge,
    ReflectionSemanticScore,
    SQLiteWorldStore,
    filter_compatible_memories,
    reflect_character_memories,
    reflection_source_id,
)
from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import NIGHT, QINGQING
from world_schema import Belief, CharacterBelief


def _store_with_episodes(tmp_path, count=3):
    store = SQLiteWorldStore(tmp_path / "world.sqlite3")
    state = build_snapshot()
    session_id = store.create_session(
        state,
        default_actor_id=NIGHT,
        world_package_id="huarong_lane",
    )
    for index in range(1, count + 1):
        store.record_character_memories(
            session_id,
            [QINGQING],
            source_event_id=f"episode-{index}",
            world_version=index,
            content=f"第 {index} 次看见夜轻歌隐藏真正实力。",
            importance=0.8,
        )
    state.version = count
    return store, state, session_id


class _CandidateGenerator:
    last_error = ""

    def __init__(self, *, fact_id="inference_hidden_power"):
        self.fact_id = fact_id
        self.calls = 0

    def generate(self, _character_id, _state, episodes):
        self.calls += 1
        return [
            ReflectionCandidate(
                content="我怀疑夜轻歌一直在隐藏真正实力。",
                fact_id=self.fact_id,
                belief=Belief.suspected_true,
                confidence=0.82,
                evidence_event_ids=[
                    episodes[0].source_event_id,
                    episodes[1].source_event_id,
                ],
                related_entity_ids=[NIGHT],
                keywords=["隐藏实力", "伪装"],
            )
        ]


def test_reflection_generator_parses_structured_payload():
    state = build_snapshot()
    episodes = [
        MemoryRecord(
            memory_id=f"m-{index}",
            session_id="session",
            character_id=QINGQING,
            source_event_id=f"event-{index}",
            world_version=index,
            memory_type="episodic",
            content=f"经历 {index}",
            importance=0.8,
            created_at="2026-01-01T00:00:00+00:00",
        )
        for index in (1, 2)
    ]
    generator = object.__new__(ReflectionGenerator)
    generator.last_error = ""
    generator._call_llm = lambda _messages: json.dumps(
        {
            "reflections": [
                {
                    "content": "我怀疑她的弱小只是伪装。",
                    "fact_id": "inference_hidden_power",
                    "belief": "suspected_true",
                    "confidence": 0.75,
                    "evidence_event_ids": ["event-1", "event-2"],
                    "related_entity_ids": [NIGHT],
                    "keywords": ["伪装"],
                }
            ]
        },
        ensure_ascii=False,
    )

    candidates = generator.generate(QINGQING, state, episodes)

    assert len(candidates) == 1
    assert candidates[0].belief == Belief.suspected_true
    assert candidates[0].evidence_event_ids == ["event-1", "event-2"]


def test_reflection_generator_rechecks_an_initial_empty_result():
    state = build_snapshot()
    episodes = [
        MemoryRecord(
            memory_id=f"m-{index}",
            session_id="session",
            character_id=QINGQING,
            source_event_id=f"event-{index}",
            world_version=index,
            content=f"第 {index} 次看见夜轻歌隐藏真正实力。",
            importance=0.8,
            memory_type="episodic",
            created_at="2026-01-01T00:00:00+00:00",
        )
        for index in (1, 2)
    ]
    responses = iter(
        [
            '{"reflections":[]}',
            json.dumps(
                {
                    "reflections": [
                        {
                            "content": "我怀疑她的弱小只是伪装。",
                            "fact_id": "inference_hidden_power",
                            "belief": "suspected_true",
                            "confidence": 0.76,
                            "evidence_event_ids": ["event-1", "event-2"],
                            "related_entity_ids": [NIGHT],
                            "keywords": ["伪装", "实力"],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        ]
    )
    calls = []
    generator = object.__new__(ReflectionGenerator)
    generator.max_retries = 1
    generator.last_error = ""

    def fake_call(messages):
        calls.append(messages)
        return next(responses)

    generator._call_llm = fake_call

    candidates = generator.generate(QINGQING, state, episodes)

    assert len(candidates) == 1
    assert len(calls) == 2
    assert "再检查一次" in calls[1][-1]["content"]


def test_reflection_waits_for_enough_unprocessed_episodes(tmp_path):
    store, state, session_id = _store_with_episodes(tmp_path, count=2)

    class _NeverCalled:
        last_error = ""

        def generate(self, *_args):
            raise AssertionError("情景不足时不应调用 LLM")

    report = reflect_character_memories(
        store,
        session_id,
        state,
        QINGQING,
        generator=_NeverCalled(),
        min_new_episodes=3,
    )

    assert report.written_count == 0
    assert report.eligible_count == 2
    assert "至少 3 条" in report.skipped_reason


def test_reflection_persists_evidence_and_structured_claim(tmp_path):
    store, state, session_id = _store_with_episodes(tmp_path)

    report = reflect_character_memories(
        store,
        session_id,
        state,
        QINGQING,
        generator=_CandidateGenerator(),
    )
    reflections = store.list_character_memories(
        session_id,
        character_id=QINGQING,
        memory_type="reflection",
    )

    assert report.written_count == 1
    assert len(reflections) == 1
    assert reflections[0].evidence_event_ids == (
        "episode-1",
        "episode-2",
    )
    assert reflections[0].claim_fact_id == "inference_hidden_power"
    assert reflections[0].claim_belief == "suspected_true"
    assert reflections[0].claim_confidence == 0.82


def test_semantic_judge_score_is_persisted(tmp_path):
    store, state, session_id = _store_with_episodes(tmp_path)

    class _Judge:
        last_error = ""

        def score(self, candidate, _episodes):
            return ReflectionSemanticScore(
                fact_id=candidate.fact_id,
                entailed=True,
                contradicted=False,
                evidence_coverage=1.0,
                semantic_score=0.88,
                reason="两条经历共同支持隐藏实力。",
            )

    report = reflect_character_memories(
        store,
        session_id,
        state,
        QINGQING,
        generator=_CandidateGenerator(),
        semantic_judge=_Judge(),
    )
    reflections = store.list_character_memories(
        session_id,
        character_id=QINGQING,
        memory_type="reflection",
    )

    assert report.written_count == 1
    assert report.semantic_scores["inference_hidden_power"] == 0.88
    assert reflections[0].semantic_score == 0.88


def test_semantic_judge_rejects_over_inference(tmp_path):
    store, state, session_id = _store_with_episodes(tmp_path)

    class _Judge:
        last_error = ""

        def score(self, candidate, _episodes):
            return ReflectionSemanticScore(
                fact_id=candidate.fact_id,
                entailed=False,
                contradicted=False,
                evidence_coverage=0.5,
                semantic_score=0.41,
                reason="只有一条经历能支持该主张。",
            )

    report = reflect_character_memories(
        store,
        session_id,
        state,
        QINGQING,
        generator=_CandidateGenerator(),
        semantic_judge=_Judge(),
    )

    assert report.written_count == 0
    assert report.rejected_count == 1
    assert "不能直接支持" in report.rejection_reasons[0]


def test_same_fact_updates_idempotently_and_merges_evidence(tmp_path):
    store, state, session_id = _store_with_episodes(tmp_path)
    generator = _CandidateGenerator()
    reflect_character_memories(
        store,
        session_id,
        state,
        QINGQING,
        generator=generator,
    )
    for index in (4, 5):
        store.record_character_memories(
            session_id,
            [QINGQING],
            source_event_id=f"episode-{index}",
            world_version=index,
            content=f"第 {index} 次发现夜轻歌刻意示弱。",
        )
    state.version = 5

    second = reflect_character_memories(
        store,
        session_id,
        state,
        QINGQING,
        generator=generator,
    )
    reflections = store.list_character_memories(
        session_id,
        character_id=QINGQING,
        memory_type="reflection",
    )

    assert second.written_count == 1
    assert len(reflections) == 1
    assert reflections[0].source_event_id == reflection_source_id(
        QINGQING,
        "inference_hidden_power",
    )
    assert reflections[0].evidence_event_ids == (
        "episode-1",
        "episode-2",
        "episode-3",
        "episode-4",
    )


def test_reflection_rejects_claim_opposed_by_authoritative_belief(tmp_path):
    store, state, session_id = _store_with_episodes(tmp_path)
    state.beliefs[QINGQING] = [
        CharacterBelief(
            fact_id="inference_hidden_power",
            belief=Belief.believed_false,
            confidence=0.9,
            source_type="observation",
        )
    ]

    report = reflect_character_memories(
        store,
        session_id,
        state,
        QINGQING,
        generator=_CandidateGenerator(),
    )

    assert report.written_count == 0
    assert report.rejected_count == 1
    assert "认知相反" in report.rejection_reasons[0]


def test_retrieval_filters_reflection_when_belief_later_conflicts():
    state = build_snapshot()
    state.beliefs[QINGQING] = [
        CharacterBelief(
            fact_id="inference_hidden_power",
            belief=Belief.believed_false,
            confidence=0.95,
        )
    ]
    reflection = MemoryRecord(
        memory_id="reflection",
        session_id="session",
        character_id=QINGQING,
        source_event_id="reflection:1",
        world_version=3,
        memory_type="reflection",
        content="我怀疑她在隐藏实力。",
        importance=0.8,
        created_at="2026-01-01T00:00:00+00:00",
        evidence_event_ids=("event-1", "event-2"),
        claim_fact_id="inference_hidden_power",
        claim_belief="suspected_true",
        claim_confidence=0.8,
    )
    episode = MemoryRecord(
        memory_id="episode",
        session_id="session",
        character_id=QINGQING,
        source_event_id="event-3",
        world_version=3,
        memory_type="episodic",
        content="普通情景记忆。",
        importance=0.7,
        created_at="2026-01-01T00:00:00+00:00",
    )

    assert filter_compatible_memories(
        [reflection, episode],
        state,
        QINGQING,
    ) == [episode]


@pytest.mark.llm
def test_real_llm_semantic_judge_accepts_direct_cross_event_evidence():
    candidate = ReflectionCandidate(
        content="我怀疑夜轻歌一直在隐藏真正实力。",
        fact_id="inference_hidden_power",
        belief=Belief.suspected_true,
        confidence=0.82,
        evidence_event_ids=["event-1", "event-2"],
        related_entity_ids=[NIGHT],
        keywords=["隐藏实力"],
    )
    episodes = [
        MemoryRecord(
            memory_id=f"memory-{index}",
            session_id="semantic-smoke",
            character_id=QINGQING,
            source_event_id=f"event-{index}",
            world_version=index,
            memory_type="episodic",
            content=content,
            importance=0.9,
            created_at="2026-07-27T00:00:00+00:00",
        )
        for index, content in [
            (1, "夜轻歌徒手折断侍卫刀刃，动作极快。"),
            (2, "持刀侍卫明显畏惧夜轻歌，警告同伴不要招惹她。"),
        ]
    ]

    score = ReflectionSemanticJudge().score(candidate, episodes)

    assert score is not None
    assert score.fact_id == candidate.fact_id
    assert 0.0 <= score.semantic_score <= 1.0
    assert score.reason
