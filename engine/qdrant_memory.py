"""SQLite 权威记忆与 Qdrant 派生语义索引的组合实现。"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .embeddings import MemoryEmbedder
from .persistence import MemoryRecord, PersistenceError
from .storage import WorldStore


@dataclass(frozen=True)
class QdrantMemoryHit:
    memory_id: str
    score: float


@dataclass(frozen=True)
class HybridRetrievalWeights:
    semantic: float = 0.80
    lexical: float = 0.10
    importance: float = 0.05
    recency: float = 0.05

    def __post_init__(self) -> None:
        values = (
            self.semantic,
            self.lexical,
            self.importance,
            self.recency,
        )
        if any(value < 0.0 for value in values):
            raise ValueError("混合检索权重不能为负数")
        if not math.isclose(sum(values), 1.0, abs_tol=1e-9):
            raise ValueError("混合检索权重之和必须为 1")


class QdrantMemoryIndex:
    """只保存可重建的向量与过滤字段，不承担权威数据职责。"""

    def __init__(
        self,
        *,
        embedder: MemoryEmbedder,
        collection_name: str = "character_memories",
        path: Optional[Union[str, Path]] = None,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        client: Any = None,
        models_module: Any = None,
    ):
        if path is not None and url:
            raise PersistenceError("Qdrant path 与 url 不能同时配置")
        self.embedder = embedder
        self.collection_name = collection_name.strip()
        if not self.collection_name:
            raise PersistenceError("Qdrant collection 名称不能为空")

        if client is None:
            try:
                from qdrant_client import QdrantClient
                from qdrant_client import models
            except ImportError as exc:
                raise PersistenceError(
                    "启用 Qdrant 需要安装 qdrant-client==1.11.3"
                ) from exc
            if url:
                client = QdrantClient(url=url, api_key=api_key or None)
            else:
                local_path = Path(path or "data/qdrant").resolve()
                local_path.parent.mkdir(parents=True, exist_ok=True)
                client = QdrantClient(path=str(local_path))
            models_module = models
        elif models_module is None:
            try:
                from qdrant_client import models as models_module
            except ImportError as exc:
                raise PersistenceError(
                    "测试注入 Qdrant client 时还需要 models_module"
                ) from exc

        self.client = client
        self.models = models_module
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        try:
            names = {
                item.name
                for item in self.client.get_collections().collections
            }
            if self.collection_name not in names:
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=self.models.VectorParams(
                        size=self.embedder.dimensions,
                        distance=self.models.Distance.COSINE,
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"初始化 Qdrant 记忆索引失败: {exc}") from exc

    @staticmethod
    def point_id(memory_id: str) -> str:
        """把 SQLite 文本 ID 稳定映射为 Qdrant 支持的 UUID。"""

        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"ai-transmigration:memory:{memory_id}",
            )
        )

    def upsert(self, records: List[MemoryRecord]) -> None:
        if not records:
            return
        try:
            unique_contents = list(
                dict.fromkeys(record.content for record in records)
            )
            batch = getattr(self.embedder, "embed_many", None)
            if callable(batch):
                embedded = list(batch(unique_contents))
            else:
                embedded = [
                    self.embedder.embed(content)
                    for content in unique_contents
                ]
            if len(embedded) != len(unique_contents):
                raise PersistenceError("Qdrant 批量嵌入返回数量不一致")
            vectors: Dict[str, List[float]] = {
                content: [float(value) for value in vector]
                for content, vector in zip(unique_contents, embedded)
            }
            for content, vector in vectors.items():
                if len(vector) != self.embedder.dimensions:
                    raise PersistenceError(
                        "Qdrant 嵌入维度不一致: "
                        f"expected {self.embedder.dimensions}, "
                        f"got {len(vector)} for {content[:20]}"
                    )
            points = []
            for record in records:
                points.append(
                    self.models.PointStruct(
                        id=self.point_id(record.memory_id),
                        vector=vectors[record.content],
                        payload={
                            "memory_id": record.memory_id,
                            "session_id": record.session_id,
                            "character_id": record.character_id,
                            "memory_type": record.memory_type,
                            "source_event_id": record.source_event_id,
                            "world_version": record.world_version,
                            "evidence_event_ids": list(
                                getattr(record, "evidence_event_ids", ())
                            ),
                            "claim_fact_id": getattr(
                                record,
                                "claim_fact_id",
                                "",
                            ),
                            "claim_belief": getattr(
                                record,
                                "claim_belief",
                                "",
                            ),
                            "claim_confidence": getattr(
                                record,
                                "claim_confidence",
                                0.0,
                            ),
                            "semantic_score": getattr(
                                record,
                                "semantic_score",
                                0.0,
                            ),
                        },
                    )
                )
            self.client.upsert(
                collection_name=self.collection_name,
                points=points,
                wait=True,
            )
        except PersistenceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"写入 Qdrant 记忆索引失败: {exc}") from exc

    def search(
        self,
        session_id: str,
        character_id: str,
        query: str,
        *,
        limit: int,
    ) -> List[QdrantMemoryHit]:
        cleaned = " ".join((query or "").split())
        if not cleaned:
            return []
        try:
            vector = self.embedder.embed(cleaned)
            if len(vector) != self.embedder.dimensions:
                raise PersistenceError(
                    "Qdrant 查询嵌入维度不一致: "
                    f"expected {self.embedder.dimensions}, got {len(vector)}"
                )
            hits = self.client.search(
                collection_name=self.collection_name,
                query_vector=[float(value) for value in vector],
                query_filter=self._filter(
                    session_id=session_id,
                    character_id=character_id,
                ),
                limit=limit,
                with_payload=True,
            )
        except PersistenceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"检索 Qdrant 记忆索引失败: {exc}") from exc
        result = []
        for hit in hits:
            memory_id = str((hit.payload or {}).get("memory_id") or "")
            if memory_id:
                result.append(
                    QdrantMemoryHit(
                        memory_id=memory_id,
                        score=float(hit.score),
                    )
                )
        return result

    def delete(
        self,
        *,
        session_id: Optional[str] = None,
        character_id: Optional[str] = None,
        memory_type: Optional[str] = None,
        memory_ids: Optional[List[str]] = None,
    ) -> None:
        try:
            if memory_ids:
                selector = self.models.PointIdsList(
                    points=[self.point_id(item) for item in memory_ids]
                )
            else:
                selector = self.models.FilterSelector(
                    filter=self._filter(
                        session_id=session_id,
                        character_id=character_id,
                        memory_type=memory_type,
                    )
                )
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=selector,
                wait=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"删除 Qdrant 记忆索引失败: {exc}") from exc

    def close(self) -> None:
        """释放 Local Mode 文件锁或远程客户端连接池。"""

        closer = getattr(self.client, "close", None)
        if callable(closer):
            closer()

    def _filter(
        self,
        *,
        session_id: Optional[str] = None,
        character_id: Optional[str] = None,
        memory_type: Optional[str] = None,
    ) -> Any:
        values = {
            "session_id": session_id,
            "character_id": character_id,
            "memory_type": memory_type,
        }
        must = [
            self.models.FieldCondition(
                key=key,
                match=self.models.MatchValue(value=value),
            )
            for key, value in values.items()
            if value is not None
        ]
        if not must:
            raise PersistenceError("Qdrant 删除或检索必须至少指定一个作用域")
        return self.models.Filter(must=must)


class QdrantBackedWorldStore:
    """装饰任意 WorldStore，写入 Qdrant 并执行词法/语义混合召回。"""

    def __init__(
        self,
        delegate: WorldStore,
        index: QdrantMemoryIndex,
        *,
        weights: Optional[HybridRetrievalWeights] = None,
    ):
        self.delegate = delegate
        self.index = index
        self.weights = weights or HybridRetrievalWeights()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def close(self) -> None:
        self.index.close()
        closer = getattr(self.delegate, "close", None)
        if callable(closer):
            closer()

    def delete_session(self, session_id: str) -> bool:
        deleted = self.delegate.delete_session(session_id)
        if deleted:
            self.index.delete(session_id=session_id)
        return deleted

    def record_character_memories(
        self,
        session_id: str,
        character_ids: List[str],
        *,
        source_event_id: str,
        world_version: int,
        content: str,
        importance: float = 0.6,
        memory_type: str = "episodic",
        evidence_event_ids: Optional[List[str]] = None,
        claim_fact_id: str = "",
        claim_belief: str = "",
        claim_confidence: float = 0.0,
        semantic_score: float = 0.0,
    ) -> List[str]:
        memory_ids = self.delegate.record_character_memories(
            session_id,
            character_ids,
            source_event_id=source_event_id,
            world_version=world_version,
            content=content,
            importance=importance,
            memory_type=memory_type,
            evidence_event_ids=evidence_event_ids,
            claim_fact_id=claim_fact_id,
            claim_belief=claim_belief,
            claim_confidence=claim_confidence,
            semantic_score=semantic_score,
        )
        self.index.upsert(self.delegate.get_character_memories(memory_ids))
        return memory_ids

    def search_character_memories(
        self,
        session_id: str,
        character_id: str,
        query: str,
        *,
        limit: int = 4,
    ) -> List[MemoryRecord]:
        lexical = self.delegate.search_character_memories(
            session_id,
            character_id,
            query,
            limit=min(20, max(limit * 4, limit)),
        )
        try:
            semantic_hits = self.index.search(
                session_id,
                character_id,
                query,
                limit=min(20, max(limit * 4, limit)),
            )
        except PersistenceError:
            return lexical[:limit]

        semantic_scores = {
            hit.memory_id: hit.score
            for hit in semantic_hits
        }
        semantic_records = self.delegate.get_character_memories(
            list(semantic_scores)
        )
        # Qdrant 是派生索引；任何已不在 SQLite 作用域内的陈旧点都不能返回。
        semantic_records = [
            item
            for item in semantic_records
            if item.session_id == session_id
            and item.character_id == character_id
        ]
        lexical_positions = {
            item.memory_id: position
            for position, item in enumerate(lexical)
        }
        records = {item.memory_id: item for item in lexical}
        records.update({item.memory_id: item for item in semantic_records})
        now = datetime.now(timezone.utc)
        ranked = []
        for memory_id, record in records.items():
            lexical_score = (
                1.0 / (lexical_positions[memory_id] + 1)
                if memory_id in lexical_positions
                else 0.0
            )
            raw_semantic = semantic_scores.get(memory_id)
            semantic_score = (
                # 文本嵌入的负余弦不表示“弱相关”，而应视为不相关；
                # 直接截断可保留正相似度之间的区分度，避免词法首位过度放大。
                max(0.0, min(1.0, raw_semantic))
                if raw_semantic is not None
                else 0.0
            )
            try:
                created = datetime.fromisoformat(record.created_at)
                age_days = max(
                    0.0,
                    (now - created).total_seconds() / 86400.0,
                )
            except (TypeError, ValueError):
                age_days = 365.0
            recency = math.exp(-age_days / 30.0)
            score = (
                self.weights.semantic * semantic_score
                + self.weights.lexical * lexical_score
                + self.weights.importance * record.importance
                + self.weights.recency * recency
            )
            ranked.append(replace(record, retrieval_score=score))
        ranked.sort(
            key=lambda item: (
                item.retrieval_score,
                item.importance,
                item.created_at,
            ),
            reverse=True,
        )
        return ranked[:limit]

    def delete_character_memories(
        self,
        session_id: str,
        *,
        memory_type: Optional[str] = None,
    ) -> int:
        deleted = self.delegate.delete_character_memories(
            session_id,
            memory_type=memory_type,
        )
        self.index.delete(
            session_id=session_id,
            memory_type=memory_type,
        )
        return deleted

    def prune_character_memories(
        self,
        session_id: str,
        character_id: str,
        *,
        memory_type: str = "episodic",
        max_records: int = 500,
    ) -> int:
        before = self.delegate.list_character_memories(
            session_id,
            character_id=character_id,
            memory_type=memory_type,
        )
        deleted = self.delegate.prune_character_memories(
            session_id,
            character_id,
            memory_type=memory_type,
            max_records=max_records,
        )
        if deleted:
            after_ids = {
                item.memory_id
                for item in self.delegate.list_character_memories(
                    session_id,
                    character_id=character_id,
                    memory_type=memory_type,
                )
            }
            self.index.delete(
                memory_ids=[
                    item.memory_id
                    for item in before
                    if item.memory_id not in after_ids
                ]
            )
        return deleted

    def rebuild_qdrant_index(
        self,
        session_id: Optional[str] = None,
    ) -> int:
        """从权威记忆表重建 Qdrant，可在索引失败后安全重试。"""

        session_ids = (
            [session_id]
            if session_id is not None
            else [item.session_id for item in self.delegate.list_sessions()]
        )
        written = 0
        for current_session_id in session_ids:
            self.index.delete(session_id=current_session_id)
            records = self.delegate.list_character_memories(current_session_id)
            self.index.upsert(records)
            written += len(records)
        return written
