import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from engine import PromptedLLMPolicy, ToolCall, call_openai_compatible
from training.build_split import load_split_manifest
from training.prompted_smoke import (
    PromptedSmokeConfig,
    build_prompted_smoke_plan,
    collect_prompted_episode,
    load_prompted_smoke_config,
    select_prompted_smoke_entries,
)
from training.scenario_generator import ScenarioFamily, generate_scenario


REPO_ROOT = Path(__file__).resolve().parents[2]


def _config(**updates):
    config = load_prompted_smoke_config(
        REPO_ROOT / "training/configs/prompted_smoke_v1.json"
    )
    return config.copy(update=updates, deep=True)


def _telemetried_policy(calls, *, fail=False):
    index = {"value": 0}

    def generator(observation, definitions):
        def create(**kwargs):
            if fail:
                raise TimeoutError("provider timeout")
            return {
                "model": "fake-provider-model",
                "usage": {
                    "prompt_tokens": 80,
                    "completion_tokens": 20,
                    "total_tokens": 100,
                },
                "choices": [],
            }

        call_openai_compatible(
            create,
            operation="planner_policy",
            model="fake-request-model",
        )
        call = calls[index["value"]]
        index["value"] += 1
        return {
            "intent": "interact",
            "tool_call": call.dict(),
            "confidence": 0.9,
            "reason_summary": "grounded fake model action",
        }

    return PromptedLLMPolicy(generator, model="fake-request-model")


def test_prompted_smoke_plan_is_stratified_and_never_selects_test():
    config = _config()
    manifest = load_split_manifest(REPO_ROOT / config.manifest)

    entries = select_prompted_smoke_entries(manifest, config)
    plan = build_prompted_smoke_plan(manifest, config)

    assert len(entries) == plan.scenario_count == 4
    assert {(entry.split.value, entry.scenario_family.value) for entry in entries} == {
        ("train", "resource_negotiation"),
        ("train", "secret_transport"),
        ("dev", "resource_negotiation"),
        ("dev", "secret_transport"),
    }
    assert plan.executes_provider_calls is False
    assert plan.allowed_splits == ["train", "dev"]
    assert {"test_id", "test_ood"}.issubset(plan.sealed_splits_not_selected)
    assert plan.maximum_model_calls == 24


@pytest.mark.parametrize(
    "splits",
    [["train", "test_id"], ["train"], ["dev", "train"]],
)
def test_prompted_config_rejects_any_noncanonical_split_boundary(splits):
    payload = json.loads(
        (
            REPO_ROOT / "training/configs/prompted_smoke_v1.json"
        ).read_text(encoding="utf-8")
    )
    payload["allowed_splits"] = splits

    with pytest.raises(ValidationError, match="train, dev"):
        PromptedSmokeConfig.parse_obj(payload)


def test_prompted_episode_records_real_usage_gate_and_replay():
    scenario = generate_scenario(
        ScenarioFamily.resource_negotiation,
        variant_index=0,
        seed=11,
    )
    policy = _telemetried_policy(scenario.scripted_calls)
    config = _config(
        model_id="fake-request-model",
        max_turns_per_episode=3,
        max_model_calls=3,
        max_total_tokens=100000,
    )

    trajectory, audit = collect_prompted_episode(
        scenario,
        data_split="train",
        config=config,
        policy=policy,
        code_commit="test",
    )

    assert audit.objective_satisfied is True
    assert audit.replay_consistent is True
    assert audit.eligible_for_sft_review is True
    assert audit.provider_call_count == 3
    assert audit.provider_failed_call_count == 0
    assert audit.provider_model_ids == ["fake-provider-model"]
    assert audit.total_tokens == 300
    assert audit.cached_tokens == 0
    assert audit.fallback_count == 0
    assert audit.gate_accepted_count == 3
    assert audit.illegal_proposal_count == 0
    assert audit.illegal_commit_count == 0
    assert all(item.model_schema_accepted for item in audit.decisions)
    assert all(
        step.planner_usage.model_id == "fake-provider-model"
        and step.planner_usage.total_tokens == 100
        for step in trajectory.steps
    )


def test_prompted_episode_reports_provider_failure_and_fallback_separately():
    scenario = generate_scenario(
        ScenarioFamily.resource_negotiation,
        variant_index=0,
        seed=11,
    )
    policy = _telemetried_policy(scenario.scripted_calls, fail=True)
    config = _config(
        model_id="fake-request-model",
        max_turns_per_episode=3,
        max_model_calls=3,
        max_total_tokens=100000,
    )

    trajectory, audit = collect_prompted_episode(
        scenario,
        data_split="dev",
        config=config,
        policy=policy,
    )

    assert trajectory.objective_satisfied is True
    assert audit.objective_satisfied is True
    assert audit.replay_consistent is True
    assert audit.eligible_for_sft_review is False
    assert audit.provider_call_count == 3
    assert audit.provider_failed_call_count == 3
    assert audit.total_tokens == 0
    assert audit.fallback_count == 3
    assert audit.gate_accepted_count == 3
    assert all(not item.model_schema_accepted for item in audit.decisions)
    assert all(item.decision_source == "scripted_fallback" for item in audit.decisions)
    assert all(item.fallback_reason.startswith("TimeoutError") for item in audit.decisions)


def test_prompted_collection_rejects_sealed_split_before_policy_call():
    scenario = generate_scenario(
        ScenarioFamily.rescue_escort,
        variant_index=0,
        seed=11,
    )
    policy = _telemetried_policy(scenario.scripted_calls)

    with pytest.raises(ValueError, match="train/dev"):
        collect_prompted_episode(
            scenario,
            data_split="test_ood",
            config=_config(),
            policy=policy,
        )


def test_prompted_actor_schedule_does_not_advance_after_gate_rejection():
    scenario = generate_scenario(
        ScenarioFamily.secret_transport,
        variant_index=0,
        seed=11,
    )
    invalid_share = ToolCall(
        actor_id="char_guard",
        tool_name="share_information",
        arguments={
            "target_character_id": "missing_character",
            "fact_id": "fact_regent_plot",
        },
    )
    calls = iter([
        scenario.scripted_calls[0],
        scenario.scripted_calls[1],
        invalid_share,
        scenario.scripted_calls[2],
    ])
    observed_actors = []

    def generator(observation, definitions):
        observed_actors.append(observation.actor_id)
        return next(calls)

    _, audit = collect_prompted_episode(
        scenario,
        data_split="train",
        config=_config(
            max_turns_per_episode=4,
            max_model_calls=4,
            max_total_tokens=100000,
        ),
        policy=PromptedLLMPolicy(generator, model="fake-request-model"),
    )

    assert observed_actors == ["char_guard"] * 4
    assert audit.decisions[2].gate_accepted is False
    assert audit.decisions[3].gate_accepted is True


def test_prompted_token_budget_stops_before_provider_call():
    scenario = generate_scenario(
        ScenarioFamily.resource_negotiation,
        variant_index=0,
        seed=11,
    )
    called = {"value": False}

    def generator(observation, definitions):
        called["value"] = True
        return scenario.scripted_calls[0]

    trajectory, audit = collect_prompted_episode(
        scenario,
        data_split="train",
        config=_config(max_total_tokens=1),
        policy=PromptedLLMPolicy(generator, model="qwen3.6-plus"),
    )

    assert called["value"] is False
    assert trajectory.steps == []
    assert audit.decision_attempt_count == 0
    assert audit.total_tokens == 0
    assert audit.termination_reason == "budget_exhausted"
