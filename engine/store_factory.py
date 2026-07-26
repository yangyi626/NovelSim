"""按部署配置选择 SQLite 或 PostgreSQL 世界存储。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

from .embeddings import MemoryEmbedder, memory_embedder_from_env
from .persistence import PersistenceError, SQLiteWorldStore
from .qdrant_memory import QdrantBackedWorldStore, QdrantMemoryIndex
from .storage import WorldStore


def create_world_store(
    *,
    sqlite_path: Union[str, Path],
    database_url: Optional[str] = None,
    embedder: Optional[MemoryEmbedder] = None,
    memory_vector_backend: Optional[str] = None,
    qdrant_index: Optional[QdrantMemoryIndex] = None,
) -> WorldStore:
    """创建统一世界存储，并按需叠加派生向量索引。

    - 未提供 URL：SQLite。
    - ``postgresql://`` / ``postgres://``：PostgreSQL + pgvector。
    - ``MEMORY_VECTOR_BACKEND=qdrant``：叠加 Qdrant Local/Server 索引。
    """

    configured_url = (
        database_url
        if database_url is not None
        else os.environ.get("WORLD_DATABASE_URL", "")
    ).strip()
    if not configured_url:
        store: WorldStore = SQLiteWorldStore(sqlite_path)
    elif configured_url.startswith(("postgresql://", "postgres://")):
        from .postgres_persistence import PostgresWorldStore

        store = PostgresWorldStore(
            configured_url,
            embedder=(
                embedder
                if embedder is not None
                else memory_embedder_from_env()
            ),
        )
    else:
        raise PersistenceError(
            "WORLD_DATABASE_URL 仅支持 postgresql:// 或 postgres://；"
            "SQLite 请留空并使用 WORLD_DB_PATH"
        )

    configured_backend = (
        memory_vector_backend
        if memory_vector_backend is not None
        else os.environ.get("MEMORY_VECTOR_BACKEND", "")
    ).strip().lower()
    if configured_backend in {"", "none", "sqlite", "fts5"}:
        return store
    if configured_backend != "qdrant":
        raise PersistenceError(
            "MEMORY_VECTOR_BACKEND 仅支持 qdrant、sqlite/fts5 或留空"
        )

    index = qdrant_index
    if index is None:
        configured_embedder = (
            embedder
            if embedder is not None
            else memory_embedder_from_env()
        )
        if configured_embedder is None:
            raise PersistenceError(
                "启用 Qdrant 必须配置 MEMORY_EMBEDDING_MODEL"
            )
        qdrant_url = (os.environ.get("QDRANT_URL") or "").strip()
        qdrant_path = (
            os.environ.get("QDRANT_PATH") or "data/qdrant"
        ).strip()
        index = QdrantMemoryIndex(
            embedder=configured_embedder,
            collection_name=(
                os.environ.get("QDRANT_COLLECTION")
                or "character_memories"
            ),
            path=None if qdrant_url else qdrant_path,
            url=qdrant_url or None,
            api_key=(os.environ.get("QDRANT_API_KEY") or "").strip() or None,
        )
    return QdrantBackedWorldStore(store, index)
