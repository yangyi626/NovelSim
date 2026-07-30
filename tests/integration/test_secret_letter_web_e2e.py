"""原创密信三条路线的真实 FastAPI/SQLite/表现流 E2E。"""

import importlib

import pytest
from fastapi.testclient import TestClient

from engine import SQLiteWorldStore, replay_events
from examples.secret_letter import build_snapshot


web_app = importlib.import_module("web.app")


@pytest.mark.parametrize(
    ("route", "ending", "version", "tool_sequence"),
    [
        (
            "destroy_letter",
            "letter_destroyed",
            2,
            ["pick_up", "destroy_item"],
        ),
        (
            "intercept_letter",
            "player_intercepted",
            2,
            ["pick_up", "move_to"],
        ),
        (
            "expose_truth",
            "truth_exposed",
            5,
            [
                "pick_up",
                "observe",
                "share_information",
                "share_information",
                "propose_alliance",
            ],
        ),
    ],
)
def test_secret_letter_player_routes_persist_resume_and_project(
    tmp_path,
    monkeypatch,
    route,
    ending,
    version,
    tool_sequence,
):
    store = SQLiteWorldStore(tmp_path / f"{route}.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)

    with TestClient(web_app.app) as client:
        response = client.post(
            "/api/scenes/secret-letter/runs",
            json={"mode": "free", "route": route},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "completed"
        assert payload["world_package_id"] == "secret_letter_v1"
        assert payload["ending"] == ending
        assert payload["state"]["version"] == version
        assert payload["summary"]["tool_sequence"] == tool_sequence
        assert len(payload["tool_results"]) == version
        assert all(item["success"] for item in payload["tool_results"])
        assert len(set(payload["trace_ids"])) == version
        assert payload["memory_record_count"] >= version
        assert payload["memory_warning"] == ""

        session_id = payload["session_id"]
        resumed = client.get(
            "/api/session",
            params={"session": session_id},
        )
        events = client.get(
            "/api/events",
            params={"session": session_id},
        )
        presentation = client.get(
            "/api/presentation-events",
            params={"session": session_id, "after_sequence": 0},
        )

    assert resumed.status_code == 200
    assert resumed.json()["resumed"] is True
    assert resumed.json()["state"] == payload["state"]
    assert events.status_code == 200
    persisted_events = store.list_events(session_id)
    assert len(persisted_events) == version
    assert replay_events(build_snapshot(), persisted_events) == (
        store.get_state(session_id)
    )
    assert presentation.status_code == 200
    assert presentation.json()["commands"]
    assert presentation.json()["latest_sequence"] == (
        payload["presentation_cursor"]
    )
    assert store.search_character_memories(
        session_id,
        "char_player",
        "tool",
    )


def test_secret_letter_run_rejects_unknown_route_without_session(
    tmp_path,
    monkeypatch,
):
    store = SQLiteWorldStore(tmp_path / "invalid.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)

    with TestClient(web_app.app) as client:
        response = client.post(
            "/api/scenes/secret-letter/runs",
            json={"mode": "free", "route": "invented_route"},
        )

    assert response.status_code == 422
    assert response.json()["status"] == "invalid"
    assert store.list_sessions() == []
