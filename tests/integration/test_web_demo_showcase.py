"""无需 API Key 的三条招聘演示链路。"""

import importlib

import pytest
from fastapi.testclient import TestClient

from engine import SQLiteWorldStore
from engine.action_parser import ActionParser


web_app = importlib.import_module("web.app")


def _forbid_llm_calls(*args, **kwargs):
    raise AssertionError("one-click demo must not call an external LLM")


@pytest.mark.parametrize(
    (
        "case_id",
        "version",
        "tool_calls",
        "propagation",
        "alliances",
        "objective_satisfied",
    ),
    [
        ("invalid_airplane", 0, 0, 0, 0, None),
        ("valid_intervention", 2, 2, 0, 0, False),
        ("multi_agent", 5, 5, 2, 1, True),
    ],
)
def test_demo_cases_are_offline_persisted_and_resumable(
    tmp_path,
    monkeypatch,
    case_id,
    version,
    tool_calls,
    propagation,
    alliances,
    objective_satisfied,
):
    store = SQLiteWorldStore(tmp_path / f"{case_id}.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    monkeypatch.setattr(ActionParser, "_call_llm", _forbid_llm_calls)

    with TestClient(web_app.app) as client:
        response = client.post("/api/demo/runs", json={"case_id": case_id})

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["demo"]["case_id"] == case_id
        assert payload["demo"]["requires_api_key"] is False
        assert payload["state"]["version"] == version
        assert payload["demo"]["evidence"] == {
            "world_version": version,
            "tool_calls": tool_calls,
            "propagation_count": propagation,
            "alliance_count": alliances,
            "objective_satisfied": objective_satisfied,
        }
        assert payload["turns"]

        resumed = client.get(
            "/api/session",
            params={"session": payload["session_id"]},
        )

    assert resumed.status_code == 200
    assert resumed.json()["state"] == payload["state"]
    assert resumed.json()["turns"] == payload["turns"]

    if case_id == "invalid_airplane":
        decision = payload["turns"][-1]
        assert decision["status"] == "rejected"
        assert decision["rejection_code"] == "WORLD_CONCEPT_UNAVAILABLE"
        assert store.list_events(payload["session_id"]) == []
    else:
        summary_turn = payload["turns"][-1]
        assert summary_turn["status"] == "committed"
        assert summary_turn["action"]["type"] == "deterministic_showcase"
        assert len(store.list_events(payload["session_id"])) == version


def test_demo_endpoint_rejects_unknown_case_without_creating_session(
    tmp_path,
    monkeypatch,
):
    store = SQLiteWorldStore(tmp_path / "invalid.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)

    with TestClient(web_app.app) as client:
        response = client.post(
            "/api/demo/runs",
            json={"case_id": "invented"},
        )

    assert response.status_code == 422
    assert response.json()["available_cases"] == [
        "invalid_airplane",
        "multi_agent",
        "valid_intervention",
    ]
    assert store.list_sessions() == []
