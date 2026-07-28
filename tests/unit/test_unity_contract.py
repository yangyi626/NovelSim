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
        "ResumeSession": "/api/session",
        "SubmitTurn": "/api/turn",
        "State": "/api/state",
        "Events": "/api/events",
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
    assert "X-NovelSim-Contract" in api_client
    assert "ApiContractV1.SubmitTurn" in api_client
    assert "private int timeoutSeconds = 300;" in api_client
    assert (
        api_client.index(
            "request.result != UnityWebRequest.Result.Success"
        )
        < api_client.index("ValidateContractHeader(")
    )
