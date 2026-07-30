from engine import SceneMode
from evaluation.pairwise import PairwiseCandidate, PairwiseSample
from evaluation.strong_baseline import (
    StrongBaselineGenerator,
    _objective_passed,
)


def _sample(action="move"):
    return PairwiseSample(
        sample_id=f"sample_{action}",
        scenario_id="huarong_lane",
        mode=SceneMode.free,
        context=(
            "世界：古代玄幻北月国。\n"
            f"权威动作：{action}\n"
            "玩家输入：我步行回夜府。"
        ),
        candidate_a=PairwiseCandidate(
            system_id="grounded",
            text="夜轻歌沿街回到夜府。",
        ),
        candidate_b=PairwiseCandidate(
            system_id="template",
            text="夜轻歌抵达夜府。",
        ),
    )


def test_direct_prompt_baseline_replaces_only_candidate_b():
    sample = _sample()
    output, report = StrongBaselineGenerator(
        model="fake-model",
        call_llm=lambda messages: "夜轻歌踏着青石路回到了夜府。",
    ).generate([sample])

    assert output[0].candidate_a == sample.candidate_a
    assert output[0].candidate_b.system_id == (
        "direct_prompt_llm_baseline"
    )
    assert output[0].candidate_b.objective_passed
    assert report.sample_count == 1
    assert report.objective_pass_count == 1


def test_baseline_objective_rejects_forbidden_success():
    sample = _sample()

    assert not _objective_passed(
        sample,
        "夜轻歌开飞机回到了夜府。",
    )


def test_baseline_objective_rejects_time_and_companion_hallucination():
    sample = _sample()
    sample.context += "\n世界时间：北月国·某日午前。"

    assert not _objective_passed(
        sample,
        "夜色渐浓，夜轻歌回到夜府。",
    )
    assert not _objective_passed(
        sample,
        "夜清清亦步亦趋，跟着夜轻歌回到夜府。",
    )
    assert _objective_passed(
        sample,
        "外衫落入夜轻歌手中，她随后回到夜府。",
    )


def test_swap_object_baseline_requires_actor_and_item():
    sample = _sample("swap_object")

    assert _objective_passed(sample, "夜轻歌夺过外衫披在肩头。")
    assert _objective_passed(sample, "我取过外衫披在肩头。")
    assert not _objective_passed(sample, "她转身离去。")
