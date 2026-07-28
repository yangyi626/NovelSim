"""LLM 网关环境变量兼容测试。"""

from engine import config


def test_dashscope_key_takes_priority(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.setenv("LLM_API_KEY", "legacy-key")
    monkeypatch.setenv(
        "LLM_BASE_URL",
        "https://dashscope.example/v1",
    )
    monkeypatch.setenv("LLM_MODEL", "qwen-test")
    monkeypatch.setattr(config, "_CONFIG", None)

    resolved = config.get_llm_config()

    assert resolved.api_key == "dashscope-key"
    assert resolved.base_url == "https://dashscope.example/v1"
    assert resolved.model == "qwen-test"


def test_generic_llm_key_remains_supported(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "legacy-key")
    monkeypatch.setattr(config, "_CONFIG", None)

    resolved = config.get_llm_config()

    assert resolved.api_key == "legacy-key"
