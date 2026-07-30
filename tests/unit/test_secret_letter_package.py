"""原创公开密信世界包的完整性与命令行展示测试。"""

import json

from engine.world_packages import validate_world_package_payload
from examples.secret_letter.package import (
    PACKAGE_ID,
    build_world_package_payload,
    canonical_package_bytes,
    export_world_package,
    verify_world_package,
)
from examples.secret_letter.demo import main as demo_main


def test_secret_letter_public_package_is_valid_and_original():
    payload = build_world_package_payload()
    record = validate_world_package_payload(payload)

    assert record.package_id == PACKAGE_ID
    assert record.review_status == "published"
    assert record.default_actor_id == "char_player"
    assert record.manifest["content_origin"] == (
        "original_for_novelsim_portfolio"
    )
    assert record.manifest["license_spdx"] == "CC-BY-4.0"
    assert record.manifest["character_count"] == 5


def test_secret_letter_npcs_have_independent_agent_state():
    state = build_world_package_payload()["snapshot"]
    player_id = "char_player"
    npc_ids = set(state["characters"]) - {player_id}

    assert len(npc_ids) == 4
    for character_id in npc_ids:
        psyche = state["character_psyches"][character_id]
        assert psyche["traits"]
        assert psyche["emotion"]
        assert psyche["goals"]
        assert psyche["plans"]
        assert psyche["recent_perceptions"]
        assert psyche["is_player"] is False


def test_secret_letter_export_is_deterministic_and_verified(tmp_path):
    output = tmp_path / "secret-letter-v1.json"

    path, first_digest = export_world_package(output)
    first_bytes = path.read_bytes()
    _, second_digest = export_world_package(output)

    assert first_bytes == canonical_package_bytes() == path.read_bytes()
    assert first_digest == second_digest == verify_world_package(output)
    assert json.loads(first_bytes)["package_id"] == PACKAGE_ID


def test_secret_letter_demo_prints_replayable_result(capsys):
    exit_code = demo_main(["--mode", "free", "--route", "expose_truth"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ending"] == "truth_exposed"
    assert payload["world_version"] == 5
    assert payload["propagation_count"] == 2
    assert payload["alliances"]


def test_secret_letter_public_package_is_registered_in_web():
    from web import app as web_app

    package = web_app.PACKAGES.get(PACKAGE_ID)

    assert package.source == "builtin"
    assert package.review_status == "published"
    assert package.default_actor_id == "char_player"
