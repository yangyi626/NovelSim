import json

from engine import SceneMode
from evaluation.pairwise import (
    BlindWinner,
    HumanBlindLabel,
    OriginalWinner,
    PairwiseCandidate,
    PairwiseEvaluator,
    PairwiseSample,
    PairwiseStatus,
    apply_human_labels,
    blind_order,
    calibrate_pairwise_report,
    load_pairwise_samples,
    render_human_packet,
    render_pairwise_markdown,
)


def _sample(
    sample_id="sample_1",
    *,
    mode=SceneMode.free,
    a_passed=True,
    b_passed=True,
    human=None,
):
    return PairwiseSample(
        sample_id=sample_id,
        scenario_id="secret_letter",
        mode=mode,
        context="守卫发现一封可能改变庄园命运的密信。",
        candidate_a=PairwiseCandidate(
            system_id="full_hybrid_system",
            text="守卫先核对火漆，再把可信证据交给管家。",
            objective_passed=a_passed,
        ),
        candidate_b=PairwiseCandidate(
            system_id="prompt_only_baseline",
            text="守卫立刻宣告所有人已经结盟。",
            objective_passed=b_passed,
        ),
        human_winner=human,
    )


def _payload(mode, winner):
    value = {
        "anthropomorphism": {
            "winner": winner,
            "rationale": "角色反应更自然",
        },
        "character_fidelity": {
            "winner": winner,
            "rationale": "符合有限认知",
        },
        "immersion_setting": {
            "winner": winner,
            "rationale": "保持庄园设定",
        },
        "writing_quality": {
            "winner": winner,
            "rationale": "表达具体清晰",
        },
        "overall_winner": winner,
        "overall_rationale": "综合表现更好",
    }
    key = (
        "storyline_quality"
        if mode == SceneMode.script
        else "creativity"
    )
    value[key] = {
        "winner": winner,
        "rationale": "模式专属维度更好",
    }
    return json.dumps(value, ensure_ascii=False)


def _blind_winner_for_original(sample_id, seed, original):
    left, right = blind_order(sample_id, seed)
    if original == OriginalWinner.tie:
        return BlindWinner.tie.value
    return (
        BlindWinner.left.value
        if left == original
        else BlindWinner.right.value
    )


def test_blinding_is_stable_and_prompt_hides_system_identity():
    captured = []

    def judge(messages):
        captured.append(messages)
        return _payload(SceneMode.free, "left")

    sample = _sample()
    evaluator = PairwiseEvaluator(
        random_seed=42,
        call_llm=judge,
    )
    first = evaluator.judge(sample)
    second = evaluator.judge(sample)
    prompt = captured[0][1]["content"]

    assert first.blind_left_original == second.blind_left_original
    assert first.prompt_hash == second.prompt_hash
    assert first.status == PairwiseStatus.judged
    assert "full_hybrid_system" not in prompt
    assert "prompt_only_baseline" not in prompt
    assert "creativity" in prompt
    assert "storyline_quality" not in first.dimensions


def test_objective_gate_overrides_subjective_preference():
    seed = 77
    sample = _sample(a_passed=True, b_passed=False)
    blind_winner = _blind_winner_for_original(
        sample.sample_id,
        seed,
        OriginalWinner.b,
    )
    record = PairwiseEvaluator(
        random_seed=seed,
        call_llm=lambda messages: _payload(
            SceneMode.free,
            blind_winner,
        ),
    ).judge(sample)

    assert record.judge_winner_original == OriginalWinner.b
    assert record.effective_winner_original == OriginalWinner.a
    assert record.objective_override is True


def test_missing_mode_dimension_is_parse_failure_not_implicit_tie():
    invalid = json.loads(_payload(SceneMode.script, "tie"))
    sample = _sample(mode=SceneMode.free)
    record = PairwiseEvaluator(
        call_llm=lambda messages: json.dumps(
            invalid,
            ensure_ascii=False,
        ),
        max_retries=0,
    ).judge(sample)

    assert record.status == PairwiseStatus.parse_failed
    assert record.effective_winner_original is None
    assert record.judge_winner_original is None
    assert record.error


def test_schema_failure_is_retried_with_validation_feedback():
    sample = _sample()
    calls = []

    def judge(messages):
        calls.append(messages)
        if len(calls) == 1:
            return '{"overall_winner":"left"}'
        return _payload(SceneMode.free, "left")

    record = PairwiseEvaluator(
        call_llm=judge,
        max_retries=1,
    ).judge(sample)

    assert record.status == PairwiseStatus.judged
    assert len(calls) == 2
    assert "严格 Schema" in calls[1][-1]["content"]


def test_human_agreement_and_cohen_kappa_use_original_ab_labels():
    seed = 2026
    originals = [
        OriginalWinner.a,
        OriginalWinner.b,
        OriginalWinner.tie,
    ]
    samples = [
        _sample(
            f"calibration_{index}",
            human=winner,
        )
        for index, winner in enumerate(originals, start=1)
    ]
    responses = [
        _payload(
            SceneMode.free,
            _blind_winner_for_original(
                sample.sample_id,
                seed,
                winner,
            ),
        )
        for sample, winner in zip(samples, originals)
    ]

    def judge(messages):
        return responses.pop(0)

    report = PairwiseEvaluator(
        random_seed=seed,
        call_llm=judge,
    ).evaluate(samples)
    markdown = render_pairwise_markdown(report)

    assert report.judged_count == 3
    assert report.parse_failure_count == 0
    assert report.human_label_count == 3
    assert report.human_agreement_rate == 1.0
    assert report.cohen_kappa == 1.0
    assert report.original_a_win_count == 1
    assert report.original_b_win_count == 1
    assert report.tie_count == 1
    assert report.per_system_wins == {
        "full_hybrid_system": 1,
        "prompt_only_baseline": 1,
    }
    assert "Cohen's κ：1.000" in markdown


def test_pairwise_jsonl_rejects_duplicate_sample_ids(tmp_path):
    sample = _sample().dict()
    path = tmp_path / "pairwise.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(sample, ensure_ascii=False, default=str),
                json.dumps(sample, ensure_ascii=False, default=str),
            ]
        ),
        encoding="utf-8",
    )

    try:
        load_pairwise_samples(path)
    except ValueError as exc:
        assert "duplicate pairwise sample_id" in str(exc)
    else:
        raise AssertionError("duplicate sample_id should fail")


def test_blind_human_labels_are_mapped_back_to_original_candidates():
    seed = 20260730
    sample = _sample()
    left, _ = blind_order(sample.sample_id, seed)
    labeled = apply_human_labels(
        [sample],
        [
            HumanBlindLabel(
                sample_id=sample.sample_id,
                winner=BlindWinner.left,
            )
        ],
        random_seed=seed,
    )
    packet = render_human_packet([sample], random_seed=seed)

    assert labeled[0].human_winner == left
    assert "full_hybrid_system" not in packet
    assert "prompt_only_baseline" not in packet
    assert "人工选择：`_____`" in packet


def test_existing_report_can_be_human_calibrated_without_new_llm_calls():
    seed = 20260730
    samples = [_sample("offline_1"), _sample("offline_2")]
    report = PairwiseEvaluator(
        random_seed=seed,
        call_llm=lambda messages: _payload(SceneMode.free, "left"),
    ).evaluate(samples)
    labels = [
        HumanBlindLabel(
            sample_id=record.sample_id,
            winner=record.judge_winner_blind,
        )
        for record in report.records
    ]

    calibrated = calibrate_pairwise_report(report, labels)

    assert report.human_label_count == 0
    assert calibrated.human_label_count == 2
    assert calibrated.human_agreement_rate == 1.0
    assert calibrated.cohen_kappa == 1.0
    assert calibrated.llm_usage == report.llm_usage
