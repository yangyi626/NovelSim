from engine.trajectory_regression import run_suite


def test_versioned_trajectory_regression_suite_passes():
    report = run_suite()

    assert report["passed"] is True
    assert report["case_count"] == 5
    assert {item["event_count"] for item in report["results"]} >= {1, 2, 20, 60}
