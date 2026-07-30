"""Unity 3D 客户端与冻结 API v1 的静态契约门禁。"""

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UNITY_ROOT = ROOT / "unity" / "NovelSim3D"
CONTRACT_CS = (
    UNITY_ROOT
    / "Assets"
    / "NovelSim"
    / "Scripts"
    / "Network"
    / "ApiContractV1.cs"
)


def _constants(source):
    return dict(
        re.findall(
            r'public const string (\w+) = "([^"]+)";',
            source,
        )
    )


def test_unity_client_paths_match_stable_api_v1():
    contract = json.loads(
        (ROOT / "contracts" / "api-v1.json").read_text(encoding="utf-8")
    )
    constants = _constants(CONTRACT_CS.read_text(encoding="utf-8"))
    expected = {
        "Metadata": "/api/meta/contract",
        "StartSession": "/api/start",
        "SecretLetterRun": "/api/scenes/secret-letter/runs",
        "ResumeSession": "/api/session",
        "SubmitTurn": "/api/turn",
        "State": "/api/state",
        "Events": "/api/events",
        "PresentationSnapshot": "/api/presentation-snapshot",
        "PresentationEvents": "/api/presentation-events",
    }

    assert constants["Version"] == contract["contract_version"]
    for name, path in expected.items():
        assert constants[name] == path
        assert path in contract["endpoints"]


def test_unity_project_is_pinned_and_consumes_authoritative_state():
    version = (
        UNITY_ROOT / "ProjectSettings" / "ProjectVersion.txt"
    ).read_text(encoding="utf-8")
    manager = (
        UNITY_ROOT
        / "Assets"
        / "NovelSim"
        / "Scripts"
        / "World"
        / "WorldSessionManager.cs"
    ).read_text(encoding="utf-8")
    api_client = (
        UNITY_ROOT
        / "Assets"
        / "NovelSim"
        / "Scripts"
        / "Network"
        / "NovelSimApiClient.cs"
    ).read_text(encoding="utf-8")

    assert "6000.3.15f1" in version
    assert "State = response.state;" in manager
    assert "response.state.timeline_id" in manager
    assert "X-NovelSim-Contract" in api_client
    assert "ApiContractV1.SubmitTurn" in api_client
    assert "private int timeoutSeconds = 300;" in api_client
    assert "FetchPresentationSnapshot" in api_client
    assert "FetchPresentationEvents" in api_client
    assert "RunSecretLetterScene" in api_client
    assert (
        api_client.index(
            "request.result != UnityWebRequest.Result.Success"
        )
        < api_client.index("ValidateContractHeader(")
    )


def test_unity_phase_two_has_articulated_visuals_navigation_and_preview():
    scripts = UNITY_ROOT / "Assets" / "NovelSim" / "Scripts"
    character = (
        scripts / "Visuals" / "StylizedCharacterFactory.cs"
    ).read_text(encoding="utf-8")
    animator = (
        scripts / "Characters" / "StylizedCharacterAnimator.cs"
    ).read_text(encoding="utf-8")
    patrol = (
        scripts / "Characters" / "NpcPatrolController.cs"
    ).read_text(encoding="utf-8")
    navigation = (
        scripts / "World" / "RuntimeLaneNavMesh.cs"
    ).read_text(encoding="utf-8")
    preview = (
        UNITY_ROOT / "capture-windows-preview.ps1"
    ).read_text(encoding="utf-8")

    assert "Left Arm Pivot" in character
    assert "Left Leg Pivot" in character
    assert "Face" in character
    assert "Hero Front Skirt Left" in character
    assert "High Ponytail Pivot" in character
    assert "Armor Rivet" in character
    assert "Guard Helmet Flap Left" in character
    assert "SetLocomotion" in animator
    assert "NavMeshAgent" in patrol
    assert "NavMeshBuilder.BuildNavMeshData" in navigation
    assert "-novelsim-capture" in preview


def test_unity_tool_event_dispatch_is_idempotent_and_server_projected():
    scripts = UNITY_ROOT / "Assets" / "NovelSim" / "Scripts"
    dispatcher = (
        scripts / "World" / "ToolEventDispatcher.cs"
    ).read_text(encoding="utf-8")
    registry = (
        scripts / "World" / "WorldEntityRegistry.cs"
    ).read_text(encoding="utf-8")
    bootstrap = (
        scripts / "Core" / "VerticalSliceBootstrap.cs"
    ).read_text(encoding="utf-8")

    assert "LastAcknowledgedSequence" in dispatcher
    assert "command.sequence <= LastAcknowledgedSequence" in dispatcher
    assert "FetchPresentationSnapshot" in dispatcher
    assert "FetchPresentationEvents" in dispatcher
    assert 'case "navigate":' in dispatcher
    assert 'case "dialogue":' in dispatcher
    assert 'case "item_destroyed":' in dispatcher
    assert 'case "alliance_formed":' in dispatcher
    assert "Reconcile(PresentationSnapshotDto snapshot)" in registry
    assert "NpcPatrolController" in registry
    assert "ToolEventDispatcher" in bootstrap
    assert "WorldEntityRegistry" in bootstrap
    assert '"char_player"' in bootstrap
    assert '"char_guard"' in bootstrap
    assert "CreateRuntimeCharacter" in registry


def test_unity_showcase_is_explicit_real_http_and_window_only_capture():
    scripts = UNITY_ROOT / "Assets" / "NovelSim" / "Scripts"
    runner = (
        scripts / "Core" / "StandaloneShowcaseRunner.cs"
    ).read_text(encoding="utf-8")
    recorder = (
        UNITY_ROOT / "record-showcase.ps1"
    ).read_text(encoding="utf-8")
    dependency = (
        UNITY_ROOT / "ensure-ffmpeg.ps1"
    ).read_text(encoding="utf-8")

    assert '-novelsim-showcase"' in runner
    assert "interactor.TryInteract()" in runner
    assert 'session.SubmitAction("夜轻歌开飞机飞走了")' in runner
    assert '"WORLD_CONCEPT_UNAVAILABLE"' in runner
    assert "session.ResumeSession(stableSessionId)" in runner
    assert "MainWindowTitle -eq $windowTitle" in recorder
    assert "GetWindowRect" in recorder
    assert "-video_size ${captureWidth}x${captureHeight}" in recorder
    assert "-i desktop" in recorder
    assert "Get-FileHash -LiteralPath $resolvedOutput" in recorder
    assert "gdigrab" in recorder
    assert "-f null" in recorder
    assert "02fa47c83703c37d" in dependency
    assert "2ce797a0f88d7f06" in dependency


def test_unity_hud_exposes_three_persistent_secret_letter_routes():
    scripts = UNITY_ROOT / "Assets" / "NovelSim" / "Scripts"
    hud = (scripts / "UI" / "NovelSimHud.cs").read_text(encoding="utf-8")
    manager = (
        scripts / "World" / "WorldSessionManager.cs"
    ).read_text(encoding="utf-8")
    bootstrap = (
        scripts / "Core" / "VerticalSliceBootstrap.cs"
    ).read_text(encoding="utf-8")
    smoke = (UNITY_ROOT / "run-windows-smoke.ps1").read_text(
        encoding="utf-8"
    )

    for route in (
        "destroy_letter",
        "intercept_letter",
        "expose_truth",
    ):
        assert route in hud
        assert route in bootstrap
        assert route in smoke
    assert "RunSecretLetterRoute" in manager
    assert "SceneRunCompleted" in manager
    assert "-novelsim-smoke-secret-letter" in bootstrap
    assert "presentation_cursor" in bootstrap
    assert "secret_letter_ok" in smoke
