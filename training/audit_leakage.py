"""Leakage and overlap audit for scenario-family split manifests."""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from typing import Dict, List

from pydantic import BaseModel, Field

from .build_split import DataSplit, SplitManifest


class LeakageIssue(BaseModel):
    code: str
    severity: str
    message: str
    sample_keys: List[str] = Field(default_factory=list)

    class Config:
        extra = "forbid"


class LeakageAuditReport(BaseModel):
    schema_version: str = "leakage_audit.v1"
    manifest_id: str
    passed: bool
    entry_count: int
    split_counts: Dict[str, int]
    issues: List[LeakageIssue] = Field(default_factory=list)
    content_hash_overlap: Dict[str, int] = Field(default_factory=dict)
    variant_overlap: Dict[str, int] = Field(default_factory=dict)
    world_package_overlap: Dict[str, int] = Field(default_factory=dict)
    entity_overlap: Dict[str, int] = Field(default_factory=dict)
    rule_overlap: Dict[str, int] = Field(default_factory=dict)

    class Config:
        extra = "forbid"


def audit_split_manifest(manifest: SplitManifest) -> LeakageAuditReport:
    by_split = {
        split: [entry for entry in manifest.entries if entry.split == split]
        for split in DataSplit
    }
    issues: List[LeakageIssue] = []
    content_overlap: Dict[str, int] = {}
    variant_overlap: Dict[str, int] = {}
    package_overlap: Dict[str, int] = {}
    entity_overlap: Dict[str, int] = {}
    rule_overlap: Dict[str, int] = {}
    for left, right in combinations(DataSplit, 2):
        key = "%s__%s" % (left.value, right.value)
        left_entries = by_split[left]
        right_entries = by_split[right]
        overlaps = {
            "content_hash": _overlap(left_entries, right_entries, "content_hash"),
            "variant_id": _overlap(left_entries, right_entries, "variant_id"),
            "world_package_id": _overlap(
                left_entries,
                right_entries,
                "world_package_id",
            ),
        }
        content_overlap[key] = len(overlaps["content_hash"])
        variant_overlap[key] = len(overlaps["variant_id"])
        package_overlap[key] = len(overlaps["world_package_id"])
        entity_overlap[key] = len(
            _nested_overlap(left_entries, right_entries, "entity_ids")
        )
        rule_overlap[key] = len(
            _nested_overlap(left_entries, right_entries, "rule_ids")
        )
        for field, values in overlaps.items():
            if values:
                issues.append(LeakageIssue(
                    code="cross_split_%s" % field,
                    severity="error",
                    message="%s overlaps between %s and %s" % (
                        field,
                        left.value,
                        right.value,
                    ),
                    sample_keys=sorted(values)[:10],
                ))

    ood_families = set(manifest.ood_families)
    contaminated = sorted({
        entry.scenario_family.value
        for entry in manifest.entries
        if entry.scenario_family in ood_families
        and entry.split != DataSplit.test_ood
    })
    missing_ood = sorted({
        entry.scenario_family.value
        for entry in manifest.entries
        if entry.split == DataSplit.test_ood
        and entry.scenario_family not in ood_families
    })
    if contaminated or missing_ood:
        issues.append(LeakageIssue(
            code="ood_family_contamination",
            severity="error",
            message="OOD families must appear exclusively in test_ood",
            sample_keys=contaminated + missing_ood,
        ))

    split_counts = {
        split.value: len(entries)
        for split, entries in by_split.items()
        if entries
    }
    required = {
        DataSplit.train.value,
        DataSplit.dev.value,
        DataSplit.test_id.value,
        DataSplit.test_ood.value,
    }
    missing_splits = sorted(required - set(split_counts))
    if missing_splits:
        issues.append(LeakageIssue(
            code="missing_required_split",
            severity="error",
            message="manifest is missing required data splits",
            sample_keys=missing_splits,
        ))
    return LeakageAuditReport(
        manifest_id=manifest.manifest_id,
        passed=not any(issue.severity == "error" for issue in issues),
        entry_count=len(manifest.entries),
        split_counts=split_counts,
        issues=issues,
        content_hash_overlap=content_overlap,
        variant_overlap=variant_overlap,
        world_package_overlap=package_overlap,
        entity_overlap=entity_overlap,
        rule_overlap=rule_overlap,
    )


def write_leakage_report(report: LeakageAuditReport, output_path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            json.loads(report.json()),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    return path


def _overlap(left, right, field):
    return {
        getattr(entry, field) for entry in left
    } & {
        getattr(entry, field) for entry in right
    }


def _nested_overlap(left, right, field):
    left_values = {
        value for entry in left for value in getattr(entry, field)
    }
    right_values = {
        value for entry in right for value in getattr(entry, field)
    }
    return left_values & right_values

