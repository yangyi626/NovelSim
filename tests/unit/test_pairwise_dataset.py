from evaluation.pairwise_dataset import build_pairwise_samples
from evaluation.real_runner import RealLLMRunRecord


def _source(case_id, objective, narration):
    return RealLLMRunRecord(
        case_id=case_id,
        user_text="固定玩家输入",
        objective=objective,
        status="committed",
        objective_passed=True,
        state_committed=True,
        action_type="swap_object",
        operation_types=["transfer_item"],
        final_version=1,
        replay_consistent=True,
        causal_valid=True,
        narrative_present=True,
        narrative_grounded=True,
        narrative_text=narration,
        dialogue_texts=["char_yeqingge: 到我手里了。"],
        latency_ms=10.0,
    )


def test_pairwise_dataset_uses_real_grounded_text_and_template_baseline():
    samples = build_pairwise_samples(
        [
            _source(
                "robe",
                "obtain_outer_robe",
                "夜轻歌取过外衫。",
            ),
            _source(
                "walk",
                "reach_ye_residence",
                "夜轻歌沿街回府。",
            ),
        ]
    )

    assert len(samples) == 2
    assert samples[0].mode.value == "free"
    assert samples[1].mode.value == "script"
    assert "夜轻歌取过外衫" in samples[0].candidate_a.text
    assert "char_yeqingge" not in samples[0].candidate_a.text
    assert "夜轻歌：到我手里了。" in samples[0].candidate_a.text
    assert "持有者已经变更" in samples[0].candidate_b.text
    assert samples[0].candidate_a.objective_passed


def test_pairwise_dataset_rejects_missing_narrative():
    record = _source("robe", "obtain_outer_robe", " ")

    try:
        build_pairwise_samples([record])
    except ValueError as exc:
        assert "no narrative text" in str(exc)
    else:
        raise AssertionError("missing narrative should fail")
