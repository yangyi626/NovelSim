"""LLM 网关配置。

从 .env 读取 key/base_url/model；支持通用 LLM_API_KEY 和 DashScope 的
DASHSCOPE_API_KEY，并在 import 时处理代理绕过
(本机 Clash 代理 TLS 握手会失败，必须直连)。

所有需要调 LLM 的模块都通过 get_llm_config() 拿配置，不直接读环境变量。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

# 加载 .env (项目根目录)
load_dotenv()

# 处理代理: .env 里可能设了 NO_PROXY=*，确保 requests 也读到。
# 必须在任何 import requests/openai 之前完成，这里在模块加载时就执行。
for _k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
           "ALL_PROXY", "all_proxy"]:
    os.environ.pop(_k, None)
_no = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
if _no:
    os.environ["NO_PROXY"] = _no
    os.environ["no_proxy"] = _no


@dataclass
class LLMConfig:
    api_key: str
    base_url: str
    model: str


_CONFIG: Optional[LLMConfig] = None


def get_llm_config() -> LLMConfig:
    """获取 LLM 配置。缓存首次读取结果。"""
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG
    key = (
        os.environ.get("DASHSCOPE_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or ""
    ).strip()
    if not key:
        raise RuntimeError(
            "DASHSCOPE_API_KEY 或 LLM_API_KEY 未设置。"
            "请复制 .env.example 为 .env 并填入 key。"
        )
    _CONFIG = LLMConfig(
        api_key=key,
        base_url=os.environ.get("LLM_BASE_URL", "https://api.gpt.ge/v1"),
        model=os.environ.get("LLM_MODEL", "qwen3.6-plus"),
    )
    return _CONFIG
