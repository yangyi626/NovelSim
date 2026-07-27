import importlib
import json
from pathlib import Path


web_app = importlib.import_module("web.app")
ROOT = Path(__file__).resolve().parents[2]


def test_v1_api_contract_paths_and_methods_are_stable():
    contract = json.loads(
        (ROOT / "contracts" / "api-v1.json").read_text(encoding="utf-8")
    )
    assert web_app.API_CONTRACT_VERSION == contract["contract_version"]
    openapi = web_app.app.openapi()
    assert openapi["info"]["version"] == contract["contract_version"]

    for path, methods in contract["endpoints"].items():
        assert path in openapi["paths"], f"缺少稳定 API 路径: {path}"
        actual = {method.upper() for method in openapi["paths"][path]}
        assert set(methods).issubset(actual), (
            f"{path} 缺少稳定方法: {set(methods) - actual}"
        )


def test_contract_declares_worker_auth_and_publish_invariants():
    contract = json.loads(
        (ROOT / "contracts" / "api-v1.json").read_text(encoding="utf-8")
    )
    text = "\n".join(contract["invariants"])

    assert "独立 Worker" in text
    assert "发布" in text
    assert contract["security"]["creator"] == "Bearer token"
