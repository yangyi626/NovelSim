import json

import pytest
from pydantic import ValidationError

from engine import replay_game_trajectory
from training.audit_leakage import audit_split_manifest, write_leakage_report
from training.build_split import (
    DataSplit,
    SplitManifest,
    build_split_manifest,
    load_split_manifest,
    write_split_manifest,
)
from training.filter_trajectories import filter_trajectories
from training.rollout_collector import collect_scripted_trajectory
from training.scenario_generator import (
    GeneratedScenario,
    ScenarioFamily,
    generate_scenario,
    generate_scenario_grid,
)


def test_three_scenario_families_are_deterministic_and_original():
    scenarios = [
        generate_scenario(family, variant_index=2, seed=37)
        for family in ScenarioFamily
    ]
    repeated = [
        generate_scenario(family, variant_index=2, seed=37)
        for family in ScenarioFamily
    ]

    assert {scenario.scenario_family for scenario in scenarios} == set(
        ScenarioFamily
    )
    assert [scenario.content_hash for scenario in scenarios] == [
        scenario.content_hash for scenario in repeated
    ]
    assert len({scenario.content_hash for scenario in scenarios}) == 3
    assert all(
        scenario.content_origin == "original_for_novelsim_v2"
        and scenario.license_spdx == "CC-BY-4.0"
        and scenario.source_type == "original_parameterized_generator"
        for scenario in scenarios
    )


def test_generated_scenario_hash_rejects_tampering():
    scenario = generate_scenario(
        ScenarioFamily.resource_negotiation,
        variant_index=0,
        seed=11,
    )
    payload = json.loads(scenario.json())
    payload["objective"] = "tampered objective"

    with pytest.raises(ValidationError, match="content_hash mismatch"):
        GeneratedScenario.parse_obj(payload)


def test_scripted_rollouts_for_all_families_pass_runtime_and_filter():
    trajectories = [
        collect_scripted_trajectory(
            generate_scenario(family, variant_index=0, seed=11)
        )
        for family in ScenarioFamily
    ]
    filtered = filter_trajectories(trajectories)

    assert [len(item.steps) for item in trajectories] == [5, 3, 3]
    assert all(item.objective_satisfied for item in trajectories)
    assert all(replay_game_trajectory(item).consistent for item in trajectories)
    assert all(
        not step.failure.illegal_commit
        for trajectory in trajectories
        for step in trajectory.steps
    )
    assert filtered.accepted == trajectories
    assert filtered.rejected == []


def test_family_aware_split_has_no_variant_or_content_leakage(tmp_path):
    scenarios = generate_scenario_grid(variants_per_family=10)
    manifest = build_split_manifest(scenarios)
    audit = audit_split_manifest(manifest)

    assert len(scenarios) == len(manifest.entries) == 150
    assert audit.passed is True
    assert audit.split_counts == {
        "train": 70,
        "dev": 10,
        "test_id": 20,
        "test_ood": 50,
    }
    assert all(value == 0 for value in audit.content_hash_overlap.values())
    assert all(value == 0 for value in audit.variant_overlap.values())
    assert all(value == 0 for value in audit.world_package_overlap.values())
    assert {
        entry.scenario_family
        for entry in manifest.entries
        if entry.split == DataSplit.test_ood
    } == {ScenarioFamily.rescue_escort}
    assert all(
        len({entry.split for entry in manifest.entries if entry.variant_id == variant})
        == 1
        for variant in {entry.variant_id for entry in manifest.entries}
    )

    manifest_path = write_split_manifest(manifest, tmp_path / "split.json")
    report_path = write_leakage_report(audit, tmp_path / "audit.json")
    assert load_split_manifest(manifest_path) == manifest
    assert json.loads(report_path.read_text(encoding="utf-8"))["passed"] is True


def test_leakage_audit_detects_ood_family_contamination():
    scenarios = generate_scenario_grid(variants_per_family=3)
    manifest = build_split_manifest(scenarios)
    entries = list(manifest.entries)
    ood_index = next(
        index
        for index, entry in enumerate(entries)
        if entry.split == DataSplit.test_ood
    )
    entries[ood_index] = entries[ood_index].copy(
        update={"split": DataSplit.train}
    )
    contaminated = manifest.copy(update={"entries": entries})

    audit = audit_split_manifest(contaminated)

    assert audit.passed is False
    assert "ood_family_contamination" in {
        issue.code for issue in audit.issues
    }

