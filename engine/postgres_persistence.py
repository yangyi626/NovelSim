"""PostgreSQL + pgvector 世界存储。

与 ``SQLiteWorldStore`` 保持同一公开契约。PostgreSQL 保存权威状态、事件
和回合，角色记忆使用 GIN 全文索引与 pgvector HNSW 混合召回。
"""

from __future__ import annotations

import json
import math
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from world_schema import WorldEvent, WorldState

from .embeddings import EmbeddingError, MemoryEmbedder
from .persistence import (
    MemoryRecord,
    PersistenceError,
    SessionMetadata,
    SessionNotFound,
    SQLiteWorldStore,
    TurnRecord,
    VersionConflict,
    _memory_search_terms,
)


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _json_object(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _vector_literal(values: Sequence[float]) -> str:
    """生成只含有限浮点数的 pgvector 文本表示。"""

    normalized = []
    for value in values:
        number = float(value)
        if not math.isfinite(number):
            raise PersistenceError("嵌入向量含非有限数值")
        normalized.append(format(number, ".12g"))
    return "[" + ",".join(normalized) + "]"


class PostgresWorldStore:
    """面向多进程部署的 PostgreSQL 世界存储。"""

    def __init__(
        self,
        database_url: str,
        *,
        embedder: Optional[MemoryEmbedder] = None,
        vector_dimensions: Optional[int] = None,
        initialize: bool = True,
    ):
        self.database_url = database_url.strip()
        if not self.database_url.startswith(("postgresql://", "postgres://")):
            raise PersistenceError("PostgreSQL URL 格式无效")
        self.embedder = embedder
        try:
            configured_dimensions = (
                vector_dimensions
                if vector_dimensions is not None
                else (
                    embedder.dimensions
                    if embedder is not None
                    else int(
                        os.environ.get(
                            "MEMORY_EMBEDDING_DIMENSIONS",
                            "1536",
                        )
                    )
                )
            )
        except ValueError as exc:
            raise PersistenceError(
                "MEMORY_EMBEDDING_DIMENSIONS 必须是整数"
            ) from exc
        if configured_dimensions < 1 or configured_dimensions > 2000:
            raise PersistenceError(
                "当前 HNSW vector 索引维度必须在 1 到 2000 之间"
            )
        self.vector_dimensions = configured_dimensions
        if initialize:
            self._initialize()

    @staticmethod
    def _driver():
        try:
            import psycopg2
            from psycopg2.extras import Json, RealDictCursor
        except ImportError as exc:
            raise PersistenceError(
                "PostgreSQL 后端需要安装 production 依赖："
                "pip install -e '.[production]'"
            ) from exc
        return psycopg2, Json, RealDictCursor

    def _connect(self):
        psycopg2, _, cursor_factory = self._driver()
        try:
            return psycopg2.connect(
                self.database_url,
                connect_timeout=10,
                cursor_factory=cursor_factory,
                application_name="ai-transmigration",
            )
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"连接 PostgreSQL 失败: {exc}") from exc

    def _initialize(self) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS world_sessions (
                        session_id TEXT PRIMARY KEY,
                        save_name TEXT NOT NULL,
                        world_package_id TEXT NOT NULL,
                        default_actor_id TEXT NOT NULL,
                        state_json JSONB NOT NULL,
                        state_version INTEGER NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS world_events (
                        session_id TEXT NOT NULL
                            REFERENCES world_sessions(session_id)
                            ON DELETE CASCADE,
                        event_id TEXT NOT NULL,
                        previous_version INTEGER NOT NULL,
                        new_version INTEGER NOT NULL,
                        event_json JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        PRIMARY KEY (session_id, event_id),
                        UNIQUE (session_id, new_version)
                    );

                    CREATE INDEX IF NOT EXISTS
                        idx_world_events_session_version
                    ON world_events(session_id, new_version);

                    CREATE TABLE IF NOT EXISTS world_turns (
                        session_id TEXT NOT NULL
                            REFERENCES world_sessions(session_id)
                            ON DELETE CASCADE,
                        turn_sequence INTEGER NOT NULL,
                        world_version INTEGER NOT NULL,
                        player_input TEXT NOT NULL,
                        result_json JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        PRIMARY KEY (session_id, turn_sequence)
                    );

                    CREATE INDEX IF NOT EXISTS
                        idx_world_turns_session_sequence
                    ON world_turns(session_id, turn_sequence);

                    CREATE TABLE IF NOT EXISTS character_memories (
                        memory_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL
                            REFERENCES world_sessions(session_id)
                            ON DELETE CASCADE,
                        character_id TEXT NOT NULL,
                        source_event_id TEXT NOT NULL,
                        world_version INTEGER NOT NULL,
                        memory_type TEXT NOT NULL,
                        content TEXT NOT NULL,
                        search_text TEXT NOT NULL,
                        search_document TSVECTOR GENERATED ALWAYS AS (
                            to_tsvector('simple', search_text)
                        ) STORED,
                        embedding vector({self.vector_dimensions}),
                        importance DOUBLE PRECISION NOT NULL
                            CHECK (importance >= 0 AND importance <= 1),
                        evidence_event_ids_json JSONB NOT NULL
                            DEFAULT '[]'::jsonb,
                        claim_fact_id TEXT NOT NULL DEFAULT '',
                        claim_belief TEXT NOT NULL DEFAULT '',
                        claim_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                        semantic_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                        created_at TIMESTAMPTZ NOT NULL,
                        UNIQUE (
                            session_id,
                            character_id,
                            source_event_id,
                            memory_type
                        )
                    );

                    CREATE INDEX IF NOT EXISTS
                        idx_character_memories_scope
                    ON character_memories(
                        session_id, character_id, world_version
                    );

                    CREATE INDEX IF NOT EXISTS
                        idx_character_memories_search
                    ON character_memories USING GIN(search_document);

                    CREATE INDEX IF NOT EXISTS
                        idx_character_memories_embedding_hnsw
                    ON character_memories
                    USING hnsw (embedding vector_cosine_ops);

                    ALTER TABLE character_memories
                    ADD COLUMN IF NOT EXISTS evidence_event_ids_json
                        JSONB NOT NULL DEFAULT '[]'::jsonb;
                    ALTER TABLE character_memories
                    ADD COLUMN IF NOT EXISTS claim_fact_id
                        TEXT NOT NULL DEFAULT '';
                    ALTER TABLE character_memories
                    ADD COLUMN IF NOT EXISTS claim_belief
                        TEXT NOT NULL DEFAULT '';
                    ALTER TABLE character_memories
                    ADD COLUMN IF NOT EXISTS claim_confidence
                        DOUBLE PRECISION NOT NULL DEFAULT 0.0;
                    ALTER TABLE character_memories
                    ADD COLUMN IF NOT EXISTS semantic_score
                        DOUBLE PRECISION NOT NULL DEFAULT 0.0;
                    """
                )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise PersistenceError(
                "初始化 PostgreSQL Schema 失败；请确认数据库允许创建 "
                f"vector 扩展: {exc}"
            ) from exc
        finally:
            conn.close()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _validate_save_name(save_name: str) -> str:
        return SQLiteWorldStore._validate_save_name(save_name)

    def create_session(
        self,
        state: WorldState,
        *,
        default_actor_id: str,
        world_package_id: str,
        session_id: Optional[str] = None,
        save_name: str = "华容巷世界线",
    ) -> str:
        attempts = 1 if session_id else 5
        _, Json, _ = self._driver()
        for _ in range(attempts):
            sid = session_id or secrets.token_hex(8)
            now = self._now()
            conn = self._connect()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO world_sessions (
                            session_id, save_name, world_package_id,
                            default_actor_id, state_json, state_version,
                            created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            sid,
                            self._validate_save_name(save_name),
                            world_package_id,
                            default_actor_id,
                            Json(state.dict()),
                            state.version,
                            now,
                            now,
                        ),
                    )
                conn.commit()
                return sid
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                if getattr(exc, "pgcode", None) == "23505":
                    if session_id:
                        raise PersistenceError(
                            f"会话已存在: {sid}"
                        ) from exc
                    continue
                raise PersistenceError(
                    f"PostgreSQL 创建会话失败: {exc}"
                ) from exc
            finally:
                conn.close()
        raise PersistenceError("生成唯一会话 ID 失败")

    def get_state(self, session_id: str) -> Optional[WorldState]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT state_json FROM world_sessions "
                    "WHERE session_id = %s",
                    (session_id,),
                )
                row = cursor.fetchone()
            if row is None:
                return None
            return WorldState.parse_obj(_json_object(row["state_json"]))
        except PersistenceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"读取世界状态失败: {exc}") from exc
        finally:
            conn.close()

    @staticmethod
    def _metadata(row) -> SessionMetadata:
        return SessionMetadata(
            session_id=str(row["session_id"]),
            save_name=str(row["save_name"]),
            world_package_id=str(row["world_package_id"]),
            default_actor_id=str(row["default_actor_id"]),
            state_version=int(row["state_version"]),
            created_at=_iso(row["created_at"]),
            updated_at=_iso(row["updated_at"]),
        )

    def get_metadata(self, session_id: str) -> Optional[SessionMetadata]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT session_id, save_name, world_package_id,
                           default_actor_id, state_version,
                           created_at, updated_at
                    FROM world_sessions
                    WHERE session_id = %s
                    """,
                    (session_id,),
                )
                row = cursor.fetchone()
            return self._metadata(row) if row else None
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"读取会话元数据失败: {exc}") from exc
        finally:
            conn.close()

    def list_sessions(self) -> List[SessionMetadata]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT session_id, save_name, world_package_id,
                           default_actor_id, state_version,
                           created_at, updated_at
                    FROM world_sessions
                    ORDER BY updated_at DESC, created_at DESC
                    """
                )
                rows = cursor.fetchall()
            return [self._metadata(row) for row in rows]
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"读取存档列表失败: {exc}") from exc
        finally:
            conn.close()

    def rename_session(self, session_id: str, save_name: str) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE world_sessions
                    SET save_name = %s, updated_at = %s
                    WHERE session_id = %s
                    """,
                    (
                        self._validate_save_name(save_name),
                        self._now(),
                        session_id,
                    ),
                )
                updated = cursor.rowcount
            conn.commit()
            if updated != 1:
                raise SessionNotFound(f"会话不存在: {session_id}")
        except SessionNotFound:
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise PersistenceError(f"修改存档名失败: {exc}") from exc
        finally:
            conn.close()

    def delete_session(self, session_id: str) -> bool:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM world_sessions WHERE session_id = %s",
                    (session_id,),
                )
                deleted = cursor.rowcount
            conn.commit()
            return deleted == 1
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise PersistenceError(f"删除存档失败: {exc}") from exc
        finally:
            conn.close()

    def _embedding(self, content: str) -> Optional[str]:
        if self.embedder is None:
            return None
        try:
            vector = self.embedder.embed(content)
        except EmbeddingError as exc:
            raise PersistenceError(str(exc)) from exc
        if len(vector) != self.vector_dimensions:
            raise PersistenceError(
                "嵌入维度不一致: "
                f"expected {self.vector_dimensions}, got {len(vector)}"
            )
        return _vector_literal(vector)

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
        cleaned_content = " ".join((content or "").split())
        cleaned_type = (memory_type or "").strip()
        cleaned_source = (source_event_id or "").strip()
        cleaned_evidence = list(
            dict.fromkeys(
                item.strip()
                for item in (evidence_event_ids or [])
                if item.strip()
            )
        )
        cleaned_fact_id = (claim_fact_id or "").strip()
        cleaned_claim_belief = (claim_belief or "").strip()
        characters = list(
            dict.fromkeys(
                cid.strip() for cid in character_ids if cid.strip()
            )
        )
        if not cleaned_content:
            raise PersistenceError("记忆内容不能为空")
        if not cleaned_type:
            raise PersistenceError("记忆类型不能为空")
        if not cleaned_source:
            raise PersistenceError("记忆必须关联来源事件")
        if not 0.0 <= importance <= 1.0:
            raise PersistenceError("记忆重要度必须在 0 到 1 之间")
        if not 0.0 <= claim_confidence <= 1.0:
            raise PersistenceError("记忆主张置信度必须在 0 到 1 之间")
        if not 0.0 <= semantic_score <= 1.0:
            raise PersistenceError("记忆语义一致性分必须在 0 到 1 之间")
        if not characters:
            return []

        _, _, _ = self._driver()
        embedding = self._embedding(cleaned_content)
        search_text = " ".join(_memory_search_terms(cleaned_content))
        now = self._now()
        memory_ids: List[str] = []
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                for character_id in characters:
                    cursor.execute(
                        """
                        INSERT INTO character_memories (
                            memory_id, session_id, character_id,
                            source_event_id, world_version, memory_type,
                            content, search_text, embedding,
                            importance, evidence_event_ids_json,
                            claim_fact_id, claim_belief,
                            claim_confidence, semantic_score, created_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s,
                            %s, %s, %s::vector, %s, %s::jsonb,
                            %s, %s, %s, %s, %s
                        )
                        ON CONFLICT (
                            session_id, character_id,
                            source_event_id, memory_type
                        ) DO UPDATE SET
                            world_version = EXCLUDED.world_version,
                            content = EXCLUDED.content,
                            search_text = EXCLUDED.search_text,
                            embedding = EXCLUDED.embedding,
                            importance = EXCLUDED.importance,
                            evidence_event_ids_json =
                                EXCLUDED.evidence_event_ids_json,
                            claim_fact_id = EXCLUDED.claim_fact_id,
                            claim_belief = EXCLUDED.claim_belief,
                            claim_confidence = EXCLUDED.claim_confidence,
                            semantic_score = EXCLUDED.semantic_score
                        RETURNING memory_id
                        """,
                        (
                            secrets.token_hex(12),
                            session_id,
                            character_id,
                            cleaned_source,
                            int(world_version),
                            cleaned_type,
                            cleaned_content,
                            search_text,
                            embedding,
                            float(importance),
                            json.dumps(cleaned_evidence, ensure_ascii=False),
                            cleaned_fact_id,
                            cleaned_claim_belief,
                            float(claim_confidence),
                            float(semantic_score),
                            now,
                        ),
                    )
                    memory_ids.append(str(cursor.fetchone()["memory_id"]))
            conn.commit()
            return memory_ids
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise PersistenceError(
                f"PostgreSQL 记录角色记忆失败: {exc}"
            ) from exc
        finally:
            conn.close()

    @staticmethod
    def _memory(row, retrieval_score: float) -> MemoryRecord:
        raw_evidence = row.get("evidence_event_ids_json", [])
        if isinstance(raw_evidence, str):
            try:
                raw_evidence = json.loads(raw_evidence)
            except json.JSONDecodeError:
                raw_evidence = []
        return MemoryRecord(
            memory_id=str(row["memory_id"]),
            session_id=str(row["session_id"]),
            character_id=str(row["character_id"]),
            source_event_id=str(row["source_event_id"]),
            world_version=int(row["world_version"]),
            memory_type=str(row["memory_type"]),
            content=str(row["content"]),
            importance=float(row["importance"]),
            created_at=_iso(row["created_at"]),
            retrieval_score=retrieval_score,
            evidence_event_ids=tuple(
                str(item) for item in (raw_evidence or [])
            ),
            claim_fact_id=str(row.get("claim_fact_id") or ""),
            claim_belief=str(row.get("claim_belief") or ""),
            claim_confidence=float(row.get("claim_confidence") or 0.0),
            semantic_score=float(row.get("semantic_score") or 0.0),
        )

    def search_character_memories(
        self,
        session_id: str,
        character_id: str,
        query: str,
        *,
        limit: int = 4,
    ) -> List[MemoryRecord]:
        if limit < 1 or limit > 20:
            raise PersistenceError("记忆检索 limit 必须在 1 到 20 之间")

        terms = _memory_search_terms(query)
        query_vector = self._embedding(query) if query.strip() else None
        candidate_limit = limit * 4
        lexical_rows = []
        semantic_rows = []
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                if terms:
                    tsquery = " | ".join(terms[:48])
                    cursor.execute(
                        """
                        SELECT memory_id, session_id, character_id,
                               source_event_id, world_version, memory_type,
                               content, importance, created_at
                        FROM character_memories
                        WHERE session_id = %s
                          AND character_id = %s
                          AND search_document @@
                              to_tsquery('simple', %s)
                        ORDER BY ts_rank_cd(
                            search_document,
                            to_tsquery('simple', %s)
                        ) DESC
                        LIMIT %s
                        """,
                        (
                            session_id,
                            character_id,
                            tsquery,
                            tsquery,
                            candidate_limit,
                        ),
                    )
                    lexical_rows = cursor.fetchall()
                if query_vector is not None:
                    cursor.execute(
                        """
                        SELECT memory_id, session_id, character_id,
                               source_event_id, world_version, memory_type,
                               content, importance, created_at
                        FROM character_memories
                        WHERE session_id = %s
                          AND character_id = %s
                          AND embedding IS NOT NULL
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        (
                            session_id,
                            character_id,
                            query_vector,
                            candidate_limit,
                        ),
                    )
                    semantic_rows = cursor.fetchall()
                if not terms and query_vector is None:
                    cursor.execute(
                        """
                        SELECT memory_id, session_id, character_id,
                               source_event_id, world_version, memory_type,
                               content, importance, created_at
                        FROM character_memories
                        WHERE session_id = %s AND character_id = %s
                        ORDER BY importance DESC, created_at DESC
                        LIMIT %s
                        """,
                        (session_id, character_id, candidate_limit),
                    )
                    lexical_rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(
                f"PostgreSQL 检索角色记忆失败: {exc}"
            ) from exc
        finally:
            conn.close()

        candidates: Dict[str, Dict[str, Any]] = {}
        lexical_rank = {}
        semantic_rank = {}
        for rank, row in enumerate(lexical_rows, start=1):
            memory_id = str(row["memory_id"])
            candidates[memory_id] = row
            lexical_rank[memory_id] = rank
        for rank, row in enumerate(semantic_rows, start=1):
            memory_id = str(row["memory_id"])
            candidates[memory_id] = row
            semantic_rank[memory_id] = rank

        now = datetime.now(timezone.utc)
        memories = []
        modality_count = int(bool(terms)) + int(query_vector is not None)
        for memory_id, row in candidates.items():
            relevance = 0.0
            if memory_id in lexical_rank:
                relevance += 1.0 / lexical_rank[memory_id]
            if memory_id in semantic_rank:
                relevance += 1.0 / semantic_rank[memory_id]
            relevance /= max(1, modality_count)
            created = row["created_at"]
            if not isinstance(created, datetime):
                created = datetime.fromisoformat(str(created))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_days = max(
                0.0,
                (now - created).total_seconds() / 86400.0,
            )
            recency = math.exp(-age_days / 30.0)
            score = (
                0.65 * relevance
                + 0.25 * float(row["importance"])
                + 0.10 * recency
            )
            memories.append(self._memory(row, score))
        memories.sort(
            key=lambda item: (
                item.retrieval_score,
                item.importance,
                item.created_at,
            ),
            reverse=True,
        )
        return memories[:limit]

    def get_character_memories(
        self,
        memory_ids: List[str],
    ) -> List[MemoryRecord]:
        ids = list(dict.fromkeys(item.strip() for item in memory_ids if item.strip()))
        if not ids:
            return []
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM character_memories
                    WHERE memory_id = ANY(%s)
                    """,
                    (ids,),
                )
                rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(
                f"PostgreSQL 读取角色记忆失败: {exc}"
            ) from exc
        finally:
            conn.close()
        by_id = {
            str(row["memory_id"]): self._memory(row, 0.0)
            for row in rows
        }
        return [by_id[memory_id] for memory_id in ids if memory_id in by_id]

    def list_character_memories(
        self,
        session_id: str,
        *,
        character_id: Optional[str] = None,
        memory_type: Optional[str] = None,
    ) -> List[MemoryRecord]:
        clauses = ["session_id = %s"]
        params: List[Any] = [session_id]
        if character_id is not None:
            clauses.append("character_id = %s")
            params.append(character_id)
        if memory_type is not None:
            clauses.append("memory_type = %s")
            params.append(memory_type)
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM character_memories
                    WHERE {' AND '.join(clauses)}
                    ORDER BY world_version, created_at, memory_id
                    """,
                    tuple(params),
                )
                rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(
                f"PostgreSQL 列出角色记忆失败: {exc}"
            ) from exc
        finally:
            conn.close()
        return [self._memory(row, 0.0) for row in rows]

    def delete_character_memories(
        self,
        session_id: str,
        *,
        memory_type: Optional[str] = None,
    ) -> int:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                if memory_type is None:
                    cursor.execute(
                        """
                        DELETE FROM character_memories
                        WHERE session_id = %s
                        """,
                        (session_id,),
                    )
                else:
                    cursor.execute(
                        """
                        DELETE FROM character_memories
                        WHERE session_id = %s AND memory_type = %s
                        """,
                        (session_id, memory_type),
                    )
                deleted = cursor.rowcount
            conn.commit()
            return deleted
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise PersistenceError(
                f"PostgreSQL 删除角色记忆失败: {exc}"
            ) from exc
        finally:
            conn.close()

    def prune_character_memories(
        self,
        session_id: str,
        character_id: str,
        *,
        memory_type: str = "episodic",
        max_records: int = 500,
    ) -> int:
        if max_records < 1:
            raise PersistenceError("记忆容量上限必须大于 0")
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM character_memories
                    WHERE memory_id IN (
                        SELECT memory_id
                        FROM character_memories
                        WHERE session_id = %s
                          AND character_id = %s
                          AND memory_type = %s
                        ORDER BY importance DESC, created_at DESC
                        OFFSET %s
                    )
                    """,
                    (
                        session_id,
                        character_id,
                        memory_type,
                        max_records,
                    ),
                )
                deleted = cursor.rowcount
            conn.commit()
            return deleted
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise PersistenceError(
                f"PostgreSQL 裁剪角色记忆失败: {exc}"
            ) from exc
        finally:
            conn.close()

    @staticmethod
    def _next_turn_sequence(cursor, session_id: str) -> int:
        cursor.execute(
            """
            SELECT COALESCE(MAX(turn_sequence), 0) + 1 AS next_sequence
            FROM world_turns
            WHERE session_id = %s
            """,
            (session_id,),
        )
        return int(cursor.fetchone()["next_sequence"])

    def _insert_turn(
        self,
        cursor,
        Json,
        session_id: str,
        *,
        world_version: int,
        player_input: str,
        turn_payload: Dict[str, Any],
        created_at: datetime,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO world_turns (
                session_id, turn_sequence, world_version,
                player_input, result_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                session_id,
                self._next_turn_sequence(cursor, session_id),
                world_version,
                player_input,
                Json(turn_payload),
                created_at,
            ),
        )

    @staticmethod
    def _validate_event_versions(
        expected_version: int,
        new_state: WorldState,
        event: WorldEvent,
    ) -> None:
        if new_state.version != expected_version + 1:
            raise PersistenceError(
                f"新状态版本不连续: expected {expected_version + 1}, "
                f"got {new_state.version}"
            )
        if (
            event.previous_version != expected_version
            or event.new_version != new_state.version
        ):
            raise PersistenceError(
                "事件版本与状态版本不一致: "
                f"event={event.previous_version}->{event.new_version}, "
                f"state={new_state.version}"
            )

    def commit_turn(
        self,
        session_id: str,
        *,
        expected_version: int,
        new_state: WorldState,
        event: WorldEvent,
        player_input: str = "",
        turn_payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._validate_event_versions(expected_version, new_state, event)
        _, Json, _ = self._driver()
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT state_version
                    FROM world_sessions
                    WHERE session_id = %s
                    FOR UPDATE
                    """,
                    (session_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise SessionNotFound(f"会话不存在: {session_id}")
                current_version = int(row["state_version"])
                if current_version != expected_version:
                    raise VersionConflict(
                        f"世界版本冲突: expected {expected_version}, "
                        f"got {current_version}"
                    )
                now = self._now()
                cursor.execute(
                    """
                    INSERT INTO world_events (
                        session_id, event_id, previous_version,
                        new_version, event_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        session_id,
                        event.event_id,
                        event.previous_version,
                        event.new_version,
                        Json(event.dict()),
                        now,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE world_sessions
                    SET state_json = %s,
                        state_version = %s,
                        updated_at = %s
                    WHERE session_id = %s AND state_version = %s
                    """,
                    (
                        Json(new_state.dict()),
                        new_state.version,
                        now,
                        session_id,
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise VersionConflict(
                        "提交期间世界版本发生变化: "
                        f"expected {expected_version}"
                    )
                if turn_payload is not None:
                    self._insert_turn(
                        cursor,
                        Json,
                        session_id,
                        world_version=new_state.version,
                        player_input=player_input,
                        turn_payload=turn_payload,
                        created_at=now,
                    )
            conn.commit()
        except (SessionNotFound, VersionConflict):
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            if getattr(exc, "pgcode", None) == "23505":
                raise VersionConflict(
                    f"世界版本或事件冲突: {expected_version}"
                ) from exc
            raise PersistenceError(
                f"PostgreSQL 提交失败: {exc}"
            ) from exc
        finally:
            conn.close()

    def append_turn(
        self,
        session_id: str,
        *,
        expected_version: int,
        player_input: str,
        turn_payload: Dict[str, Any],
    ) -> None:
        _, Json, _ = self._driver()
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT state_version
                    FROM world_sessions
                    WHERE session_id = %s
                    FOR UPDATE
                    """,
                    (session_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise SessionNotFound(f"会话不存在: {session_id}")
                current_version = int(row["state_version"])
                if current_version != expected_version:
                    raise VersionConflict(
                        f"世界版本冲突: expected {expected_version}, "
                        f"got {current_version}"
                    )
                now = self._now()
                self._insert_turn(
                    cursor,
                    Json,
                    session_id,
                    world_version=current_version,
                    player_input=player_input,
                    turn_payload=turn_payload,
                    created_at=now,
                )
                cursor.execute(
                    """
                    UPDATE world_sessions
                    SET updated_at = %s
                    WHERE session_id = %s
                    """,
                    (now, session_id),
                )
            conn.commit()
        except (SessionNotFound, VersionConflict):
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise PersistenceError(
                f"PostgreSQL 保存回合失败: {exc}"
            ) from exc
        finally:
            conn.close()

    def list_events(self, session_id: str) -> List[WorldEvent]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT event_json
                    FROM world_events
                    WHERE session_id = %s
                    ORDER BY new_version ASC
                    """,
                    (session_id,),
                )
                rows = cursor.fetchall()
            return [
                WorldEvent.parse_obj(_json_object(row["event_json"]))
                for row in rows
            ]
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(
                f"事件日志损坏或读取失败: {session_id}: {exc}"
            ) from exc
        finally:
            conn.close()

    def list_turns(self, session_id: str) -> List[TurnRecord]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT session_id, turn_sequence, world_version,
                           player_input, result_json, created_at
                    FROM world_turns
                    WHERE session_id = %s
                    ORDER BY turn_sequence ASC
                    """,
                    (session_id,),
                )
                rows = cursor.fetchall()
            return [
                TurnRecord(
                    session_id=str(row["session_id"]),
                    turn_sequence=int(row["turn_sequence"]),
                    world_version=int(row["world_version"]),
                    player_input=str(row["player_input"]),
                    result=dict(_json_object(row["result_json"])),
                    created_at=_iso(row["created_at"]),
                )
                for row in rows
            ]
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(
                f"回合历史损坏或读取失败: {session_id}: {exc}"
            ) from exc
        finally:
            conn.close()

    def export_session(self, session_id: str) -> Dict[str, Any]:
        metadata = self.get_metadata(session_id)
        state = self.get_state(session_id)
        if metadata is None or state is None:
            raise SessionNotFound(f"会话不存在: {session_id}")
        events = self.list_events(session_id)
        turns = self.list_turns(session_id)
        return {
            "format": "ai-transmigration-save",
            "format_version": 1,
            "exported_at": _iso(self._now()),
            "source_session_id": session_id,
            "save": {
                "name": metadata.save_name,
                "world_package_id": metadata.world_package_id,
                "default_actor_id": metadata.default_actor_id,
                "created_at": metadata.created_at,
                "updated_at": metadata.updated_at,
            },
            "state": state.dict(),
            "events": [event.dict() for event in events],
            "turns": [
                {
                    "turn_sequence": turn.turn_sequence,
                    "world_version": turn.world_version,
                    "player_input": turn.player_input,
                    "result": turn.result,
                    "created_at": turn.created_at,
                }
                for turn in turns
            ],
        }

    def import_session(
        self,
        backup: Dict[str, Any],
        *,
        save_name: Optional[str] = None,
    ) -> str:
        (
            save,
            state,
            events,
            turns,
            world_package_id,
            default_actor_id,
        ) = SQLiteWorldStore._validate_backup(backup)
        source_name = str(save.get("name") or "导入的世界线")
        imported_name = self._validate_save_name(
            save_name or f"{source_name[:76]}（导入）"
        )
        _, Json, _ = self._driver()

        for _ in range(5):
            session_id = secrets.token_hex(8)
            now = self._now()
            conn = self._connect()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO world_sessions (
                            session_id, save_name, world_package_id,
                            default_actor_id, state_json, state_version,
                            created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            session_id,
                            imported_name,
                            world_package_id,
                            default_actor_id,
                            Json(state.dict()),
                            state.version,
                            now,
                            now,
                        ),
                    )
                    for event in events:
                        cursor.execute(
                            """
                            INSERT INTO world_events (
                                session_id, event_id, previous_version,
                                new_version, event_json, created_at
                            ) VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (
                                session_id,
                                event.event_id,
                                event.previous_version,
                                event.new_version,
                                Json(event.dict()),
                                now,
                            ),
                        )
                    for turn in turns:
                        cursor.execute(
                            """
                            INSERT INTO world_turns (
                                session_id, turn_sequence, world_version,
                                player_input, result_json, created_at
                            ) VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (
                                session_id,
                                turn["turn_sequence"],
                                turn["world_version"],
                                turn["player_input"],
                                Json(turn["result"]),
                                turn["created_at"] or now,
                            ),
                        )
                conn.commit()
                return session_id
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                if getattr(exc, "pgcode", None) == "23505":
                    continue
                raise PersistenceError(
                    f"PostgreSQL 导入失败: {exc}"
                ) from exc
            finally:
                conn.close()
        raise PersistenceError("生成唯一导入会话 ID 失败")
