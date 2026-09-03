import asyncio
import importlib

from fastapi.testclient import TestClient

from engine import (
    SQLiteWorldStore,
    build_turn_presentation_events,
    commit_event,
    cursor_after_world_version,
    project_presentation_commands,
)
from examples.secret_letter import (
    PLAYER_ROUTE_EXPOSE,
    build_snapshot,
    run_secret_letter_scene,
)
from world_schema import (
    DialogueLine,
    NarrativeOutput,
    Operation,
    OperationKind,
    StatePatch,
)


web_app = importlib.import_module("web.app")


def test_tool_events_project_to_stable_monotonic_commands():
    scene = asyncio.run(
        run_secret_letter_scene(player_route=PLAYER_ROUTE_EXPOSE)
    )

    commands, has_more = project_presentation_commands(
        [outcome.event for outcome in scene.outcomes],
    )
    repeated, _ = project_presentation_commands(
        [outcome.event for outcome in scene.outcomes],
    )

    assert has_more is False
    assert [command.command_type for command in commands] == [
        "item_picked_up",
        "fact_observed",
        "information_shared",
        "information_shared",
        "alliance_formed",
    ]
    assert [command.sequence for command in commands] == [
        1001,
        2001,
        3001,
        4001,
        5001,
    ]
    assert commands == repeated
    assert len({command.command_id for command in commands}) == 5

    remaining, _ = project_presentation_commands(
        [outcome.event for outcome in scene.outcomes],
        after_sequence=commands[1].sequence,
    )
    assert remaining == commands[2:]


def test_legacy_event_without_directives_uses_patch_projection():
    state = build_snapshot()
    event, _ = commit_event(
        state,
        action_id="legacy_move",
        event_type="move",
        patch=StatePatch(
            operations=[
                Operation(
                    op=OperationKind.move_character,
                    target_id="char_guard",
                    location_id="loc_courtyard",
                )
            ]
        ),
        actor_ids=["char_guard"],
    )

    commands, _ = project_presentation_commands([event])

    assert len(commands) == 1
    assert commands[0].command_type == "navigate"
    assert commands[0].actor_id == "char_guard"
    assert commands[0].location_id == "loc_courtyard"


def test_turn_projection_combines_patch_dialogue_and_system_hints():
    state = build_snapshot()
    event, _ = commit_event(
        state,
        action_id="give_letter",
        event_type="gift",
        patch=StatePatch(
            operations=[
                Operation(
                    op=OperationKind.transfer_item,
                    item_id="item_sealed_letter",
                    target_id="char_steward",
                )
            ]
        ),
        actor_ids=["char_player"],
    )
    narrative = NarrativeOutput(
        narration="玩家把密信交给管家。",
        dialogues=[
            DialogueLine(
                speaker_id="char_player",
                to_id="char_steward",
                line="请立刻查看这封信。",
                tone="急切",
            )
        ],
        system_hints=["管家获得密信"],
        grounded_event_ids=[event.event_id],
    )

    directives = build_turn_presentation_events(
        event,
        narrative=narrative,
    )

    assert [item["event_type"] for item in directives] == [
        "item_transferred",
        "narration",
        "dialogue",
        "system_hint",
    ]
    assert directives[1]["payload"] == {
        "text": "玩家把密信交给管家。",
        "viewpoint": "third_person",
        "grounded_event_ids": [event.event_id],
        "referenced_entity_ids": [],
    }


def test_presentation_api_supports_snapshot_cursor_and_resume(tmp_path, monkeypatch):
    store = SQLiteWorldStore(tmp_path / "presentation.sqlite3")
    monkeypatch.setattr(web_app, "SESSIONS", store)
    initial = build_snapshot()
    session_id = store.create_session(
        initial,
        default_actor_id="char_player",
        world_package_id="secret_letter",
    )
    scene = asyncio.run(
        run_secret_letter_scene(player_route=PLAYER_ROUTE_EXPOSE)
    )
    previous = initial
    for outcome in scene.outcomes:
        store.commit_turn(
            session_id,
            expected_version=previous.version,
            new_state=outcome.new_state,
            event=outcome.event,
            player_input="",
            turn_payload={},
        )
        previous = outcome.new_state

    with TestClient(web_app.app) as client:
        snapshot_response = client.get(
            "/api/presentation-snapshot",
            params={"session": session_id},
        )
        stream_response = client.get(
            "/api/presentation-events",
            params={"session": session_id, "after_sequence": 0},
        )

        assert snapshot_response.status_code == 200
        snapshot = snapshot_response.json()["snapshot"]
        assert snapshot["state_version"] == 5
        assert snapshot["last_sequence"] == cursor_after_world_version(5)
        assert isinstance(snapshot["characters"], list)
        assert isinstance(snapshot["items"], list)
        assert isinstance(snapshot["alliances"], list)

        assert stream_response.status_code == 200
        stream = stream_response.json()
        assert stream["next_sequence"] == 5001
        assert stream["latest_sequence"] == cursor_after_world_version(5)
        assert len(stream["commands"]) == 5

        resumed = client.get(
            "/api/presentation-events",
            params={
                "session": session_id,
                "after_sequence": stream["commands"][1]["sequence"],
            },
        )
        assert [
            command["command_id"]
            for command in resumed.json()["commands"]
        ] == [
            command["command_id"]
            for command in stream["commands"][2:]
        ]

        reset = client.get(
            "/api/presentation-events",
            params={
                "session": session_id,
                "after_sequence": snapshot["last_sequence"] + 1,
            },
        )
        assert reset.status_code == 409
        assert reset.json()["status"] == "reset_required"
