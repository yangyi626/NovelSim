"""Scenario-family-aware Train/Dev/Test-ID/Test-OOD manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field, root_validator

from .scenario_generator import GeneratedScenario, ScenarioFamily


class DataSplit(str, Enum):
    train = "train"
    dev = "dev"
    test_id = "test_id"
    test_ood = "test_ood"
    adversarial = "adversarial"


class SplitEntry(BaseModel):
    split: DataSplit
    scenario_id: str
    world_package_id: str
    scenario_family: ScenarioFamily
    variant_id: str
    random_seed: int
    content_hash: str
    entity_ids: List[str] = Field(default_factory=list)
    rule_ids: List[str] = Field(default_factory=list)
    template_version: str
    source_type: str

    class Config:
        extra = "forbid"
        allow_mutation = False


class SplitManifest(BaseModel):
    schema_version: str = "scenario_split_manifest.v1"
    manifest_id: str = ""
    split_seed: int
    ood_families: List[ScenarioFamily]
    entries: List[SplitEntry]
    generator_version: str = "scenario_generator.v2"
    manifest_hash: Optional[str] = None

    class Config:
        extra = "forbid"
        allow_mutation = False

    @root_validator(skip_on_failure=True)
    def _validate_unique_entries_and_hash(cls, values):
        entries = values.get("entries") or []
        for field in ("scenario_id", "world_package_id", "content_hash"):
            items = [getattr(entry, field) for entry in entries]
            if len(items) != len(set(items)):
                raise ValueError("split manifest has duplicate %s" % field)
        supplied = values.get("manifest_hash")
        values["manifest_hash"] = None
        calculated = split_manifest_hash(values)
        if supplied not in (None, calculated):
            raise ValueError("split manifest hash mismatch")
        values["manifest_hash"] = calculated
        if not values.get("manifest_id"):
            values["manifest_id"] = "split_%s" % calculated[:16]
        return values


def build_split_manifest(
    scenarios: Iterable[GeneratedScenario],
    *,
    ood_families: Iterable[ScenarioFamily] = (
        ScenarioFamily.rescue_escort,
    ),
    split_seed: int = 20260801,
    dev_variant_fraction: float = 0.1,
    test_id_variant_fraction: float = 0.2,
) -> SplitManifest:
    if not 0.0 <= dev_variant_fraction < 1.0:
        raise ValueError("dev_variant_fraction must be in [0, 1)")
    if not 0.0 <= test_id_variant_fraction < 1.0:
        raise ValueError("test_id_variant_fraction must be in [0, 1)")
    if dev_variant_fraction + test_id_variant_fraction >= 1.0:
        raise ValueError("dev + test_id fractions must be below 1")
    records = list(scenarios)
    ood = {ScenarioFamily(family) for family in ood_families}
    variant_split: Dict[str, DataSplit] = {}
    for family in sorted(
        {scenario.scenario_family for scenario in records} - ood,
        key=lambda value: value.value,
    ):
        variants = sorted({
            scenario.variant_id
            for scenario in records
            if scenario.scenario_family == family
        })
        rng = random.Random(
            split_seed + int(hashlib.sha256(family.value.encode()).hexdigest()[:8], 16)
        )
        rng.shuffle(variants)
        dev_count = max(1, round(len(variants) * dev_variant_fraction))
        test_count = max(
            1,
            round(len(variants) * test_id_variant_fraction),
        )
        if dev_count + test_count >= len(variants):
            raise ValueError(
                "not enough variants in %s for train/dev/test-id" % family.value
            )
        for variant_id in variants[:dev_count]:
            variant_split[variant_id] = DataSplit.dev
        for variant_id in variants[dev_count:dev_count + test_count]:
            variant_split[variant_id] = DataSplit.test_id
        for variant_id in variants[dev_count + test_count:]:
            variant_split[variant_id] = DataSplit.train

    entries = []
    for scenario in sorted(
        records,
        key=lambda item: (
            item.scenario_family.value,
            item.variant_id,
            item.random_seed,
        ),
    ):
        split = (
            DataSplit.test_ood
            if scenario.scenario_family in ood
            else variant_split[scenario.variant_id]
        )
        entries.append(SplitEntry(
            split=split,
            scenario_id=scenario.scenario_id,
            world_package_id=scenario.world_package_id,
            scenario_family=scenario.scenario_family,
            variant_id=scenario.variant_id,
            random_seed=scenario.random_seed,
            content_hash=scenario.content_hash,
            entity_ids=list(scenario.entity_ids),
            rule_ids=list(scenario.rule_ids),
            template_version=scenario.template_version,
            source_type=scenario.source_type,
        ))
    return SplitManifest(
        split_seed=split_seed,
        ood_families=sorted(ood, key=lambda value: value.value),
        entries=entries,
    )


def write_split_manifest(manifest: SplitManifest, output_path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            json.loads(manifest.json()),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    return path


def load_split_manifest(input_path) -> SplitManifest:
    return SplitManifest.parse_raw(Path(input_path).read_text(encoding="utf-8"))


def split_manifest_hash(value) -> str:
    payload = _plain(value)
    payload.pop("manifest_hash", None)
    payload.pop("manifest_id", None)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _plain(value):
    if isinstance(value, BaseModel):
        return _plain(value.dict())
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and audit NovelSim scenario-family data splits",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--audit-output", required=True)
    parser.add_argument("--variants-per-family", type=int, default=10)
    parser.add_argument(
        "--seeds",
        default="11,23,37,51,79",
        help="comma-separated integer seeds",
    )
    parser.add_argument(
        "--ood-family",
        choices=[family.value for family in ScenarioFamily],
        default=ScenarioFamily.rescue_escort.value,
    )
    parser.add_argument("--split-seed", type=int, default=20260801)
    args = parser.parse_args(argv)
    try:
        seeds = [int(value.strip()) for value in args.seeds.split(",") if value.strip()]
    except ValueError as exc:
        parser.error("--seeds must contain comma-separated integers")
        raise exc
    from .audit_leakage import audit_split_manifest, write_leakage_report
    from .scenario_generator import generate_scenario_grid

    scenarios = generate_scenario_grid(
        variants_per_family=args.variants_per_family,
        seeds=seeds,
    )
    manifest = build_split_manifest(
        scenarios,
        ood_families=[ScenarioFamily(args.ood_family)],
        split_seed=args.split_seed,
    )
    audit = audit_split_manifest(manifest)
    write_split_manifest(manifest, args.output)
    write_leakage_report(audit, args.audit_output)
    print(json.dumps({
        "manifest_id": manifest.manifest_id,
        "manifest_hash": manifest.manifest_hash,
        "entry_count": len(manifest.entries),
        "split_counts": audit.split_counts,
        "audit_passed": audit.passed,
        "issue_count": len(audit.issues),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if audit.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
