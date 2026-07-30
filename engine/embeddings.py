"""长期记忆使用的嵌入接口与 OpenAI 兼容实现。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol

from .llm_telemetry import call_openai_compatible


class EmbeddingError(RuntimeError):
    """嵌入请求或返回数据无效。"""


class MemoryEmbedder(Protocol):
    """存储层依赖的最小嵌入接口。"""

    dimensions: int

    def embed(self, text: str) -> List[float]:
        ...


@dataclass
class OpenAICompatibleEmbedder:
    """通过 OpenAI 兼容 ``/embeddings`` 接口生成向量。"""

    api_key: str
    base_url: str
    model: str
    dimensions: int

    def embed(self, text: str) -> List[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: List[str]) -> List[List[float]]:
        """批量生成向量，并按输入顺序返回。"""

        cleaned = [" ".join((text or "").split()) for text in texts]
        if not cleaned or any(not text for text in cleaned):
            raise EmbeddingError("嵌入文本不能为空")

        import openai

        try:
            response = call_openai_compatible(
                openai.Embedding.create,
                operation="memory_embedding",
                kind="embedding",
                api_key=self.api_key,
                api_base=self.base_url,
                model=self.model,
                input=cleaned,
            )
            data = list(response["data"])
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(f"嵌入请求失败: {exc}") from exc
        if len(data) != len(cleaned):
            raise EmbeddingError(
                "嵌入返回数量不一致: "
                f"expected {len(cleaned)}, got {len(data)}"
            )
        if all("index" in item for item in data):
            data.sort(key=lambda item: int(item["index"]))
        vectors = []
        for item in data:
            vector = list(item["embedding"])
            if len(vector) != self.dimensions:
                raise EmbeddingError(
                    "嵌入维度不一致: "
                    f"expected {self.dimensions}, got {len(vector)}"
                )
            vectors.append([float(value) for value in vector])
        return vectors


@dataclass
class CachedMemoryEmbedder:
    """为批量评测和索引重建提供进程内向量缓存。"""

    delegate: MemoryEmbedder
    cache: Dict[str, List[float]] = field(default_factory=dict)
    request_count: int = 0
    embedded_text_count: int = 0

    @property
    def dimensions(self) -> int:
        return self.delegate.dimensions

    @staticmethod
    def _clean(text: str) -> str:
        cleaned = " ".join((text or "").split())
        if not cleaned:
            raise EmbeddingError("嵌入文本不能为空")
        return cleaned

    def warm(self, texts: List[str]) -> int:
        unique = list(dict.fromkeys(self._clean(text) for text in texts))
        missing = [text for text in unique if text not in self.cache]
        if not missing:
            return 0
        batch = getattr(self.delegate, "embed_many", None)
        if callable(batch):
            vectors = batch(missing)
            self.request_count += 1
        else:
            vectors = [self.delegate.embed(text) for text in missing]
            self.request_count += len(missing)
        if len(vectors) != len(missing):
            raise EmbeddingError(
                "缓存预热返回数量不一致: "
                f"expected {len(missing)}, got {len(vectors)}"
            )
        for text, vector in zip(missing, vectors):
            if len(vector) != self.dimensions:
                raise EmbeddingError(
                    "缓存预热嵌入维度不一致: "
                    f"expected {self.dimensions}, got {len(vector)}"
                )
            self.cache[text] = [float(value) for value in vector]
        self.embedded_text_count += len(missing)
        return len(missing)

    def embed(self, text: str) -> List[float]:
        cleaned = self._clean(text)
        self.warm([cleaned])
        return list(self.cache[cleaned])

    def embed_many(self, texts: List[str]) -> List[List[float]]:
        cleaned = [self._clean(text) for text in texts]
        self.warm(cleaned)
        return [list(self.cache[text]) for text in cleaned]


def memory_embedder_from_env() -> Optional[MemoryEmbedder]:
    """从环境变量创建嵌入器；未配置模型时返回 None。"""

    model = (os.environ.get("MEMORY_EMBEDDING_MODEL") or "").strip()
    if not model:
        return None
    raw_dimensions = os.environ.get("MEMORY_EMBEDDING_DIMENSIONS", "1536")
    try:
        dimensions = int(raw_dimensions)
    except ValueError as exc:
        raise EmbeddingError(
            "MEMORY_EMBEDDING_DIMENSIONS 必须是整数"
        ) from exc
    if dimensions < 1 or dimensions > 2000:
        raise EmbeddingError(
            "MEMORY_EMBEDDING_DIMENSIONS 必须在 1 到 2000 之间"
        )

    api_key = (
        os.environ.get("MEMORY_EMBEDDING_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")
        or ""
    ).strip()
    if not api_key:
        raise EmbeddingError(
            "已配置 MEMORY_EMBEDDING_MODEL，但缺少 "
            "MEMORY_EMBEDDING_API_KEY、LLM_API_KEY "
            "或 DASHSCOPE_API_KEY"
        )
    return OpenAICompatibleEmbedder(
        api_key=api_key,
        base_url=(
            os.environ.get("MEMORY_EMBEDDING_BASE_URL")
            or os.environ.get("LLM_BASE_URL")
            or "https://api.openai.com/v1"
        ),
        model=model,
        dimensions=dimensions,
    )
