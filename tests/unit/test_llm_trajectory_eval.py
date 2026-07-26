"""真实 LLM 长轨迹评分器的分块、聚合与门禁测试。"""

import json

import pytest

from engine import LLMTrajectoryEvaluator
from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import NIGHT
from world_schema import StatePatch, WorldEvent


def _events(count):
    return [
        WorldEvent(
            event_id=f"event-{index}",
            event_type="observe",
            actor_ids=[NIGHT],
            patch=StatePatch(),
            previous_version=index - 1,
            new_version=index,
            summary=f"第 {index} 回合推进线索。",
        )
        for index in range(1, count + 1)
    ]


def _score(**overrides):
    payload = {
        "causal_coherence": 0.82,
        "character_consistency": 0.84,
        "goal_progression": 0.76,
        "world_state_consistency": 0.9,
        "repetition_control": 0.74,
        "summary": "轨迹连续且角色目标有所推进。",
        "strengths": ["事件因果清晰"],
        "issues": [],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_long_trajectory_is_chunked_then_aggregated():
    final_state = build_snapshot()
    final_state.version = 25
    evaluator = LLMTrajectoryEvaluator(chunk_size=10)
    calls = []

    def fake_call(messages):
        calls.append(messages)
        return _score()

    evaluator._call_llm = fake_call

    report = evaluator.evaluate(
        _events(25),
        final_state=final_state,
    )

    assert report.chunk_count == 3
    assert len(calls) == 4
    assert report.passed
    assert report.overall_score > 0.7


def test_low_dimension_fails_release_gate():
    final_state = build_snapshot()
    final_state.version = 8
    evaluator = LLMTrajectoryEvaluator(
        chunk_size=12,
        minimum_dimension=0.6,
    )
    evaluator._call_llm = lambda _messages: _score(
        goal_progression=0.42,
    )

    report = evaluator.evaluate(
        _events(8),
        final_state=final_state,
    )

    assert not report.passed
    assert report.aggregate.goal_progression == 0.42


@pytest.mark.llm
def test_real_llm_scores_twenty_event_trajectory():
    final_state = build_snapshot()
    final_state.version = 20

    report = LLMTrajectoryEvaluator(
        chunk_size=10,
    ).evaluate(
        _events(20),
        final_state=final_state,
    )

    assert report.chunk_count == 2
    assert 0.0 <= report.overall_score <= 1.0
    assert report.aggregate.summary
