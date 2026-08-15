from evaluation.long_horizon import load_long_horizon_cases, run_long_horizon_suite


def test_dynamic_joint_plan_cases_are_fixed_seed_and_ten_turns():
    cases = load_long_horizon_cases()

    assert len(cases) == 6
    assert len({case.seed for case in cases}) == 6
    assert all(10 <= case.max_turns <= 30 for case in cases)
    assert {case.perturbations[0].kind.value for case in cases} == {
        "destroy_item",
        "move_key_actor",
        "conflicting_evidence",
        "item_competition",
        "wait_cycle",
        "invalid_entity",
    }


def test_dynamic_joint_plan_suite_recovers_and_replays_consistently():
    report = run_long_horizon_suite()

    assert report.passed is True
    assert report.metrics.episode_count == 6
    assert report.metrics.long_horizon_success_rate == 1.0
    assert report.metrics.staleness_recall == 1.0
    assert report.metrics.deadlock_recovery_rate == 1.0
    assert report.metrics.replan_precision == 1.0
    assert report.metrics.unnecessary_replan_rate == 0.0
    assert report.metrics.illegal_commit_count == 0
    assert report.metrics.replay_consistency_rate == 1.0
    assert all(result.turns_executed == 10 for result in report.cases)
    assert all(result.external_llm_call_count == 0 for result in report.cases)

