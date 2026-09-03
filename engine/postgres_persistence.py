"""PostgreSQL + pgvector 世界存储。

与 ``SQLiteWorldStore`` 保持同一公开契约。PostgreSQL 保存权威状态、事件
和回合，角色记忆使用 GIN 全文索引与 pgvector HNSW 混合召回。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from world_schema import WorldEvent, WorldState

from .chapter_progression import (
    CampaignProgression,
    CampaignRecord,
    CarryoverManifest,
    RewardLedgerRecord,
    SessionLineage,
    SettlementReceipt,
    TransitionRequest,
    TransitionResult,
    UnlockGrant,
    UnlockRecord,
)
from .embeddings import EmbeddingError, MemoryEmbedder
from .event import replay_events
from .manuscript import (
    ManuscriptGenerationStatus,
    ManuscriptPassage,
    ManuscriptRevision,
    ManuscriptSource,
    WorldlineManuscript,
)
from .persistence import (
    ManuscriptRevisionConflict,
    MemoryRecord,
    PersistenceError,
    SessionMetadata,
    SessionNotFound,
    SQLiteWorldStore,
    StateVersionUnavailable,
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
                        base_state_json JSONB NOT NULL,
                        base_state_version INTEGER NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        book_id TEXT NOT NULL DEFAULT '',
                        entry_id TEXT NOT NULL DEFAULT '',
                        chapter_number INTEGER NOT NULL DEFAULT 0,
                        entry_revision INTEGER NOT NULL DEFAULT 0
                    );
                    ALTER TABLE world_sessions ADD COLUMN IF NOT EXISTS book_id TEXT NOT NULL DEFAULT '';
                    ALTER TABLE world_sessions ADD COLUMN IF NOT EXISTS entry_id TEXT NOT NULL DEFAULT '';
                    ALTER TABLE world_sessions ADD COLUMN IF NOT EXISTS chapter_number INTEGER NOT NULL DEFAULT 0;
                    ALTER TABLE world_sessions ADD COLUMN IF NOT EXISTS entry_revision INTEGER NOT NULL DEFAULT 0;
                    ALTER TABLE world_sessions ADD COLUMN IF NOT EXISTS base_state_json JSONB;
                    ALTER TABLE world_sessions ADD COLUMN IF NOT EXISTS base_state_version INTEGER;
                    UPDATE world_sessions
                    SET base_state_json = state_json,
                        base_state_version = state_version
                    WHERE base_state_json IS NULL OR base_state_version IS NULL;
                    ALTER TABLE world_sessions ALTER COLUMN base_state_json SET NOT NULL;
                    ALTER TABLE world_sessions ALTER COLUMN base_state_version SET NOT NULL;

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

                    CREATE TABLE IF NOT EXISTS progression_campaigns (
                        campaign_id TEXT PRIMARY KEY,
                        root_session_id TEXT NOT NULL UNIQUE
                            REFERENCES world_sessions(session_id)
                            ON DELETE CASCADE,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS session_lineage (
                        session_id TEXT PRIMARY KEY
                            REFERENCES world_sessions(session_id)
                            ON DELETE CASCADE,
                        campaign_id TEXT NOT NULL
                            REFERENCES progression_campaigns(campaign_id)
                            ON DELETE CASCADE,
                        root_session_id TEXT NOT NULL
                            REFERENCES world_sessions(session_id)
                            ON DELETE CASCADE,
                        parent_session_id TEXT
                            REFERENCES world_sessions(session_id)
                            ON DELETE CASCADE,
                        depth INTEGER NOT NULL CHECK (depth >= 0),
                        source_settlement_id TEXT,
                        target_world_package_id TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        UNIQUE (parent_session_id, target_world_package_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_session_lineage_campaign
                    ON session_lineage(campaign_id, depth, created_at);

                    CREATE TABLE IF NOT EXISTS progression_settlements (
                        settlement_id TEXT PRIMARY KEY,
                        campaign_id TEXT NOT NULL
                            REFERENCES progression_campaigns(campaign_id)
                            ON DELETE CASCADE,
                        session_id TEXT NOT NULL UNIQUE
                            REFERENCES world_sessions(session_id)
                            ON DELETE CASCADE,
                        world_package_id TEXT NOT NULL,
                        settlement_event_id TEXT NOT NULL,
                        settled_world_version INTEGER NOT NULL,
                        ending_id TEXT NOT NULL,
                        ending_title TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        reward_points INTEGER NOT NULL,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        created_at TIMESTAMPTZ NOT NULL,
                        UNIQUE (session_id, settlement_event_id),
                        FOREIGN KEY (session_id, settlement_event_id)
                            REFERENCES world_events(session_id, event_id)
                            ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS progression_reward_ledger (
                        ledger_id TEXT PRIMARY KEY,
                        campaign_id TEXT NOT NULL
                            REFERENCES progression_campaigns(campaign_id)
                            ON DELETE CASCADE,
                        settlement_id TEXT NOT NULL UNIQUE
                            REFERENCES progression_settlements(settlement_id)
                            ON DELETE CASCADE,
                        session_id TEXT NOT NULL
                            REFERENCES world_sessions(session_id)
                            ON DELETE CASCADE,
                        points_delta INTEGER NOT NULL,
                        reason TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS progression_unlocks (
                        unlock_id TEXT PRIMARY KEY,
                        campaign_id TEXT NOT NULL
                            REFERENCES progression_campaigns(campaign_id)
                            ON DELETE CASCADE,
                        source_settlement_id TEXT NOT NULL
                            REFERENCES progression_settlements(settlement_id)
                            ON DELETE CASCADE,
                        source_session_id TEXT NOT NULL
                            REFERENCES world_sessions(session_id)
                            ON DELETE CASCADE,
                        unlock_key TEXT NOT NULL,
                        unlock_type TEXT NOT NULL,
                        payload_json JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        UNIQUE (campaign_id, unlock_key)
                    );

                    CREATE TABLE IF NOT EXISTS progression_manifests (
                        manifest_id TEXT PRIMARY KEY,
                        campaign_id TEXT NOT NULL
                            REFERENCES progression_campaigns(campaign_id)
                            ON DELETE CASCADE,
                        parent_session_id TEXT NOT NULL
                            REFERENCES world_sessions(session_id)
                            ON DELETE CASCADE,
                        child_session_id TEXT NOT NULL UNIQUE
                            REFERENCES world_sessions(session_id)
                            ON DELETE CASCADE,
                        target_world_package_id TEXT NOT NULL,
                        source_settlement_id TEXT NOT NULL
                            REFERENCES progression_settlements(settlement_id)
                            ON DELETE CASCADE,
                        payload_json JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        UNIQUE (parent_session_id, target_world_package_id)
                    );

                    CREATE TABLE IF NOT EXISTS progression_transitions (
                        transition_id TEXT PRIMARY KEY,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        campaign_id TEXT NOT NULL
                            REFERENCES progression_campaigns(campaign_id)
                            ON DELETE CASCADE,
                        parent_session_id TEXT NOT NULL
                            REFERENCES world_sessions(session_id)
                            ON DELETE CASCADE,
                        target_world_package_id TEXT NOT NULL,
                        child_session_id TEXT NOT NULL UNIQUE
                            REFERENCES world_sessions(session_id)
                            ON DELETE CASCADE,
                        settlement_id TEXT NOT NULL
                            REFERENCES progression_settlements(settlement_id)
                            ON DELETE CASCADE,
                        manifest_id TEXT NOT NULL UNIQUE
                            REFERENCES progression_manifests(manifest_id)
                            ON DELETE CASCADE,
                        created_at TIMESTAMPTZ NOT NULL,
                        UNIQUE (parent_session_id, target_world_package_id)
                    );

                    CREATE TABLE IF NOT EXISTS worldline_manuscripts (
                        manuscript_id TEXT PRIMARY KEY,
                        campaign_id TEXT NOT NULL UNIQUE
                            REFERENCES progression_campaigns(campaign_id)
                            ON DELETE CASCADE,
                        root_session_id TEXT NOT NULL
                            REFERENCES world_sessions(session_id)
                            ON DELETE CASCADE,
                        timeline_id TEXT NOT NULL DEFAULT '',
                        title TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'active',
                        current_revision INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS manuscript_passages (
                        passage_id TEXT PRIMARY KEY,
                        manuscript_id TEXT NOT NULL
                            REFERENCES worldline_manuscripts(manuscript_id)
                            ON DELETE CASCADE,
                        session_id TEXT NOT NULL
                            REFERENCES world_sessions(session_id)
                            ON DELETE CASCADE,
                        chapter_number INTEGER NOT NULL DEFAULT 0,
                        entry_id TEXT NOT NULL DEFAULT '',
                        entry_revision INTEGER NOT NULL DEFAULT 0,
                        manuscript_sequence INTEGER NOT NULL,
                        title TEXT NOT NULL DEFAULT '',
                        source_event_ids_json JSONB NOT NULL,
                        source_fingerprint TEXT NOT NULL,
                        from_world_version INTEGER NOT NULL,
                        to_world_version INTEGER NOT NULL,
                        generation_kind TEXT NOT NULL,
                        generation_status TEXT NOT NULL,
                        current_revision INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT NOT NULL DEFAULT '',
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        UNIQUE (manuscript_id, source_fingerprint),
                        UNIQUE (manuscript_id, manuscript_sequence)
                    );

                    CREATE INDEX IF NOT EXISTS idx_manuscript_passages_session
                    ON manuscript_passages(session_id, manuscript_sequence);

                    CREATE TABLE IF NOT EXISTS manuscript_passage_revisions (
                        passage_id TEXT NOT NULL
                            REFERENCES manuscript_passages(passage_id)
                            ON DELETE CASCADE,
                        revision_number INTEGER NOT NULL,
                        revision_json JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        PRIMARY KEY (passage_id, revision_number)
                    );
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
        book_id: str = "",
        entry_id: str = "",
        chapter_number: int = 0,
        entry_revision: int = 0,
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
                            base_state_json, base_state_version,
                            created_at, updated_at, book_id, entry_id,
                            chapter_number, entry_revision
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            sid,
                            self._validate_save_name(save_name),
                            world_package_id,
                            default_actor_id,
                            Json(state.dict()),
                            state.version,
                            Json(state.dict()),
                            state.version,
                            now,
                            now,
                            book_id,
                            entry_id,
                            int(chapter_number),
                            int(entry_revision),
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

    def get_state_at_version(
        self,
        session_id: str,
        world_version: int,
    ) -> Optional[WorldState]:
        """从不变基线快照和追加事件重建指定版本。"""

        requested = int(world_version)
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT state_json, state_version,
                           base_state_json, base_state_version
                    FROM world_sessions
                    WHERE session_id = %s
                    """,
                    (session_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                current_version = int(row["state_version"])
                base_version = int(row["base_state_version"])
                if requested < base_version or requested > current_version:
                    raise StateVersionUnavailable(
                        "历史世界版本不可用: "
                        f"requested={requested}, available="
                        f"{base_version}..{current_version}"
                    )
                if requested == current_version:
                    return WorldState.parse_obj(
                        _json_object(row["state_json"])
                    )
                base_state = WorldState.parse_obj(
                    _json_object(row["base_state_json"])
                )
                cursor.execute(
                    """
                    SELECT event_json
                    FROM world_events
                    WHERE session_id = %s
                      AND new_version > %s
                      AND new_version <= %s
                    ORDER BY new_version ASC
                    """,
                    (session_id, base_version, requested),
                )
                events = [
                    WorldEvent.parse_obj(_json_object(item["event_json"]))
                    for item in cursor.fetchall()
                ]
            rebuilt = replay_events(base_state, events)
            if rebuilt.version != requested:
                raise StateVersionUnavailable(
                    "历史世界事件链不完整: "
                    f"requested={requested}, rebuilt={rebuilt.version}"
                )
            return rebuilt
        except (PersistenceError, StateVersionUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(
                f"PostgreSQL 历史世界状态损坏: "
                f"{session_id}@{requested}"
            ) from exc
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
            book_id=str(row.get("book_id") or ""),
            entry_id=str(row.get("entry_id") or ""),
            chapter_number=int(row.get("chapter_number") or 0),
            entry_revision=int(row.get("entry_revision") or 0),
        )

    def get_metadata(self, session_id: str) -> Optional[SessionMetadata]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT session_id, save_name, world_package_id,
                           default_actor_id, state_version,
                           created_at, updated_at, book_id, entry_id,
                           chapter_number, entry_revision
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
                           created_at, updated_at, book_id, entry_id,
                           chapter_number, entry_revision
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

    @staticmethod
    def _lineage(row) -> SessionLineage:
        return SessionLineage(
            campaign_id=str(row["campaign_id"]),
            session_id=str(row["session_id"]),
            root_session_id=str(row["root_session_id"]),
            parent_session_id=(
                str(row["parent_session_id"])
                if row["parent_session_id"] is not None
                else None
            ),
            depth=int(row["depth"]),
            source_settlement_id=(
                str(row["source_settlement_id"])
                if row["source_settlement_id"] is not None
                else None
            ),
            target_world_package_id=str(row["target_world_package_id"]),
            created_at=_iso(row["created_at"]),
        )

    @staticmethod
    def _settlement(row) -> SettlementReceipt:
        return SettlementReceipt(
            settlement_id=str(row["settlement_id"]),
            campaign_id=str(row["campaign_id"]),
            session_id=str(row["session_id"]),
            world_package_id=str(row["world_package_id"]),
            settlement_event_id=str(row["settlement_event_id"]),
            settled_world_version=int(row["settled_world_version"]),
            ending_id=str(row["ending_id"]),
            ending_title=str(row["ending_title"]),
            summary=str(row["summary"]),
            reward_points=int(row["reward_points"]),
            idempotency_key=str(row["idempotency_key"]),
            created_at=_iso(row["created_at"]),
        )

    @staticmethod
    def _manifest(row) -> CarryoverManifest:
        return CarryoverManifest(
            manifest_id=str(row["manifest_id"]),
            campaign_id=str(row["campaign_id"]),
            parent_session_id=str(row["parent_session_id"]),
            child_session_id=str(row["child_session_id"]),
            target_world_package_id=str(row["target_world_package_id"]),
            source_settlement_id=str(row["source_settlement_id"]),
            payload=dict(_json_object(row["payload_json"])),
            created_at=_iso(row["created_at"]),
        )

    def _ensure_session_lineage_cursor(
        self,
        cursor,
        session_id: str,
    ) -> SessionLineage:
        cursor.execute(
            "SELECT * FROM session_lineage WHERE session_id = %s",
            (session_id,),
        )
        row = cursor.fetchone()
        if row is not None:
            return self._lineage(row)
        cursor.execute(
            """
            SELECT session_id, world_package_id, created_at
            FROM world_sessions
            WHERE session_id = %s
            FOR UPDATE
            """,
            (session_id,),
        )
        session = cursor.fetchone()
        if session is None:
            raise SessionNotFound(f"会话不存在: {session_id}")
        campaign_id = f"campaign_{session_id}"
        cursor.execute(
            """
            INSERT INTO progression_campaigns (
                campaign_id, root_session_id, created_at, updated_at
            ) VALUES (%s, %s, %s, %s)
            ON CONFLICT (root_session_id) DO NOTHING
            """,
            (
                campaign_id,
                session_id,
                session["created_at"],
                session["created_at"],
            ),
        )
        cursor.execute(
            """
            SELECT campaign_id
            FROM progression_campaigns
            WHERE root_session_id = %s
            """,
            (session_id,),
        )
        campaign_id = str(cursor.fetchone()["campaign_id"])
        cursor.execute(
            """
            INSERT INTO session_lineage (
                session_id, campaign_id, root_session_id,
                parent_session_id, depth, source_settlement_id,
                target_world_package_id, created_at
            ) VALUES (%s, %s, %s, NULL, 0, NULL, %s, %s)
            ON CONFLICT (session_id) DO NOTHING
            """,
            (
                session_id,
                campaign_id,
                session_id,
                str(session["world_package_id"]),
                session["created_at"],
            ),
        )
        cursor.execute(
            "SELECT * FROM session_lineage WHERE session_id = %s",
            (session_id,),
        )
        return self._lineage(cursor.fetchone())

    def ensure_session_lineage(self, session_id: str) -> SessionLineage:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                lineage = self._ensure_session_lineage_cursor(
                    cursor, session_id
                )
            conn.commit()
            return lineage
        except SessionNotFound:
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise PersistenceError(
                f"PostgreSQL 创建世界线谱系失败: {exc}"
            ) from exc
        finally:
            conn.close()

    def get_session_lineage(
        self,
        session_id: str,
    ) -> Optional[SessionLineage]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM session_lineage WHERE session_id = %s",
                    (session_id,),
                )
                row = cursor.fetchone()
            return self._lineage(row) if row is not None else None
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(
                f"PostgreSQL 读取世界线谱系失败: {exc}"
            ) from exc
        finally:
            conn.close()

    def record_settlement_progression(
        self,
        session_id: str,
        *,
        settlement_event_id: str,
        settled_world_version: int,
        ending_id: str,
        ending_title: str,
        summary: str,
        reward_points: int,
        idempotency_key: str,
        unlocks: Sequence[UnlockGrant] = (),
    ) -> SettlementReceipt:
        key = (idempotency_key or "").strip()
        if not key:
            raise PersistenceError("结算幂等键不能为空")
        if reward_points < 0:
            raise PersistenceError("结算奖励不能为负数")
        _, Json, _ = self._driver()
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM progression_settlements
                    WHERE idempotency_key = %s OR session_id = %s
                    FOR UPDATE
                    """,
                    (key, session_id),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    if (
                        str(existing["session_id"]) != session_id
                        or str(existing["settlement_event_id"])
                        != settlement_event_id
                    ):
                        raise PersistenceError(
                            "结算幂等键或会话已关联其他结算"
                        )
                    result = self._settlement(existing)
                    conn.commit()
                    return result
                cursor.execute(
                    """
                    SELECT world_package_id, state_version
                    FROM world_sessions
                    WHERE session_id = %s
                    FOR UPDATE
                    """,
                    (session_id,),
                )
                session = cursor.fetchone()
                if session is None:
                    raise SessionNotFound(f"会话不存在: {session_id}")
                cursor.execute(
                    """
                    SELECT new_version
                    FROM world_events
                    WHERE session_id = %s AND event_id = %s
                    """,
                    (session_id, settlement_event_id),
                )
                event = cursor.fetchone()
                if event is None:
                    raise PersistenceError(
                        "结算事件尚未提交，不能记录章节进度"
                    )
                if int(event["new_version"]) != int(settled_world_version):
                    raise PersistenceError("结算事件版本与回执版本不一致")
                if int(session["state_version"]) < int(settled_world_version):
                    raise PersistenceError("权威世界状态尚未到达结算版本")
                lineage = self._ensure_session_lineage_cursor(
                    cursor, session_id
                )
                now = self._now()
                settlement_id = f"settlement_{secrets.token_hex(12)}"
                cursor.execute(
                    """
                    INSERT INTO progression_settlements (
                        settlement_id, campaign_id, session_id,
                        world_package_id, settlement_event_id,
                        settled_world_version, ending_id, ending_title,
                        summary, reward_points, idempotency_key, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    ) RETURNING *
                    """,
                    (
                        settlement_id,
                        lineage.campaign_id,
                        session_id,
                        str(session["world_package_id"]),
                        settlement_event_id,
                        int(settled_world_version),
                        ending_id,
                        ending_title,
                        summary,
                        int(reward_points),
                        key,
                        now,
                    ),
                )
                result = self._settlement(cursor.fetchone())
                cursor.execute(
                    """
                    INSERT INTO progression_reward_ledger (
                        ledger_id, campaign_id, settlement_id, session_id,
                        points_delta, reason, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        f"reward_{secrets.token_hex(12)}",
                        lineage.campaign_id,
                        settlement_id,
                        session_id,
                        int(reward_points),
                        f"settlement:{ending_id}",
                        now,
                    ),
                )
                for grant in unlocks:
                    unlock_key = (grant.unlock_key or "").strip()
                    if not unlock_key:
                        raise PersistenceError("解锁键不能为空")
                    cursor.execute(
                        """
                        INSERT INTO progression_unlocks (
                            unlock_id, campaign_id,
                            source_settlement_id, source_session_id,
                            unlock_key, unlock_type, payload_json, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (campaign_id, unlock_key) DO NOTHING
                        """,
                        (
                            f"unlock_{secrets.token_hex(12)}",
                            lineage.campaign_id,
                            settlement_id,
                            session_id,
                            unlock_key,
                            grant.unlock_type,
                            Json(grant.payload),
                            now,
                        ),
                    )
                cursor.execute(
                    """
                    UPDATE progression_campaigns
                    SET updated_at = %s WHERE campaign_id = %s
                    """,
                    (now, lineage.campaign_id),
                )
            conn.commit()
            return result
        except (SessionNotFound, PersistenceError):
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise PersistenceError(
                f"PostgreSQL 记录结算进度失败: {exc}"
            ) from exc
        finally:
            conn.close()

    def _transition_result_cursor(
        self,
        cursor,
        row,
        *,
        created: bool,
    ) -> TransitionResult:
        cursor.execute(
            "SELECT * FROM session_lineage WHERE session_id = %s",
            (row["child_session_id"],),
        )
        lineage = self._lineage(cursor.fetchone())
        cursor.execute(
            "SELECT * FROM progression_manifests WHERE manifest_id = %s",
            (row["manifest_id"],),
        )
        manifest = self._manifest(cursor.fetchone())
        return TransitionResult(
            transition_id=str(row["transition_id"]),
            idempotency_key=str(row["idempotency_key"]),
            parent_session_id=str(row["parent_session_id"]),
            target_world_package_id=str(row["target_world_package_id"]),
            child_session_id=str(row["child_session_id"]),
            settlement_id=str(row["settlement_id"]),
            lineage=lineage,
            manifest=manifest,
            created=created,
            created_at=_iso(row["created_at"]),
        )

    def create_or_get_child_session(
        self,
        request: TransitionRequest,
    ) -> TransitionResult:
        key = (request.idempotency_key or "").strip()
        target = (request.target_world_package_id or "").strip()
        if not key or not target:
            raise PersistenceError("转场幂等键和目标世界包不能为空")
        event = request.genesis_event
        state = request.child_state
        if (
            event.previous_version != 0
            or event.new_version != state.version
            or state.version != 1
        ):
            raise PersistenceError("子世界创世事件必须是 0->1 且匹配子状态版本")
        _, Json, _ = self._driver()
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                parent_lineage = self._ensure_session_lineage_cursor(
                    cursor, request.parent_session_id
                )
                cursor.execute(
                    """
                    SELECT * FROM progression_transitions
                    WHERE idempotency_key = %s
                       OR (parent_session_id = %s
                           AND target_world_package_id = %s)
                    FOR UPDATE
                    """,
                    (key, request.parent_session_id, target),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    if (
                        str(existing["parent_session_id"])
                        != request.parent_session_id
                        or str(existing["target_world_package_id"]) != target
                    ):
                        raise PersistenceError(
                            "转场幂等键已用于其他父会话或目标世界"
                        )
                    result = self._transition_result_cursor(
                        cursor, existing, created=False
                    )
                    conn.commit()
                    return result
                cursor.execute(
                    """
                    SELECT * FROM progression_settlements
                    WHERE session_id = %s
                    """,
                    (request.parent_session_id,),
                )
                settlement = cursor.fetchone()
                if settlement is None:
                    raise PersistenceError(
                        "父会话尚未记录权威结算，不能创建子世界"
                    )
                child_session_id = (
                    request.child_session_id or secrets.token_hex(8)
                )
                now = self._now()
                cursor.execute(
                    """
                        INSERT INTO world_sessions (
                            session_id, save_name, world_package_id,
                            default_actor_id, state_json, state_version,
                            base_state_json, base_state_version,
                            created_at, updated_at, book_id, entry_id,
                            chapter_number, entry_revision
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s
                        )

                    """,
                    (
                        child_session_id,
                        self._validate_save_name(request.save_name),
                        target,
                        request.default_actor_id,
                        Json(state.dict()),
                        state.version,
                        Json(state.dict()),
                        state.version,
                        now,
                        now,
                        request.target_book_id,
                        request.target_entry_id,
                        int(request.target_chapter_number),
                        int(request.target_entry_revision),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO world_events (
                        session_id, event_id, previous_version,
                        new_version, event_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        child_session_id,
                        event.event_id,
                        event.previous_version,
                        event.new_version,
                        Json(event.dict()),
                        now,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO session_lineage (
                        session_id, campaign_id, root_session_id,
                        parent_session_id, depth, source_settlement_id,
                        target_world_package_id, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        child_session_id,
                        parent_lineage.campaign_id,
                        parent_lineage.root_session_id,
                        request.parent_session_id,
                        parent_lineage.depth + 1,
                        str(settlement["settlement_id"]),
                        target,
                        now,
                    ),
                )
                manifest_id = f"manifest_{secrets.token_hex(12)}"
                cursor.execute(
                    """
                    INSERT INTO progression_manifests (
                        manifest_id, campaign_id, parent_session_id,
                        child_session_id, target_world_package_id,
                        source_settlement_id, payload_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        manifest_id,
                        parent_lineage.campaign_id,
                        request.parent_session_id,
                        child_session_id,
                        target,
                        str(settlement["settlement_id"]),
                        Json(request.manifest),
                        now,
                    ),
                )
                transition_id = f"transition_{secrets.token_hex(12)}"
                cursor.execute(
                    """
                    INSERT INTO progression_transitions (
                        transition_id, idempotency_key, campaign_id,
                        parent_session_id, target_world_package_id,
                        child_session_id, settlement_id,
                        manifest_id, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        transition_id,
                        key,
                        parent_lineage.campaign_id,
                        request.parent_session_id,
                        target,
                        child_session_id,
                        str(settlement["settlement_id"]),
                        manifest_id,
                        now,
                    ),
                )
                transition = cursor.fetchone()
                cursor.execute(
                    """
                    UPDATE progression_campaigns
                    SET updated_at = %s WHERE campaign_id = %s
                    """,
                    (now, parent_lineage.campaign_id),
                )
                result = self._transition_result_cursor(
                    cursor, transition, created=True
                )
            conn.commit()
            return result
        except (SessionNotFound, PersistenceError):
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            if getattr(exc, "pgcode", None) == "23505":
                raise PersistenceError(
                    "父会话/目标世界或子会话已存在"
                ) from exc
            raise PersistenceError(
                f"PostgreSQL 创建子世界失败: {exc}"
            ) from exc
        finally:
            conn.close()

    def list_campaign_progression(
        self,
        campaign_id: str,
    ) -> CampaignProgression:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM progression_campaigns WHERE campaign_id = %s",
                    (campaign_id,),
                )
                campaign = cursor.fetchone()
                if campaign is None:
                    raise PersistenceError(
                        f"章节旅程不存在: {campaign_id}"
                    )
                cursor.execute(
                    """
                    SELECT * FROM session_lineage
                    WHERE campaign_id = %s
                    ORDER BY depth, created_at, session_id
                    """,
                    (campaign_id,),
                )
                lineage_rows = cursor.fetchall()
                cursor.execute(
                    """
                    SELECT * FROM progression_settlements
                    WHERE campaign_id = %s
                    ORDER BY created_at, settlement_id
                    """,
                    (campaign_id,),
                )
                settlement_rows = cursor.fetchall()
                cursor.execute(
                    """
                    SELECT * FROM progression_reward_ledger
                    WHERE campaign_id = %s
                    ORDER BY created_at, ledger_id
                    """,
                    (campaign_id,),
                )
                reward_rows = cursor.fetchall()
                cursor.execute(
                    """
                    SELECT * FROM progression_unlocks
                    WHERE campaign_id = %s
                    ORDER BY created_at, unlock_id
                    """,
                    (campaign_id,),
                )
                unlock_rows = cursor.fetchall()
                cursor.execute(
                    """
                    SELECT * FROM progression_manifests
                    WHERE campaign_id = %s
                    ORDER BY created_at, manifest_id
                    """,
                    (campaign_id,),
                )
                manifest_rows = cursor.fetchall()
                cursor.execute(
                    """
                    SELECT * FROM progression_transitions
                    WHERE campaign_id = %s
                    ORDER BY created_at, transition_id
                    """,
                    (campaign_id,),
                )
                transition_rows = cursor.fetchall()
                transitions = tuple(
                    self._transition_result_cursor(
                        cursor, row, created=False
                    )
                    for row in transition_rows
                )
            return CampaignProgression(
                campaign=CampaignRecord(
                    campaign_id=str(campaign["campaign_id"]),
                    root_session_id=str(campaign["root_session_id"]),
                    created_at=_iso(campaign["created_at"]),
                    updated_at=_iso(campaign["updated_at"]),
                ),
                lineage=tuple(self._lineage(row) for row in lineage_rows),
                settlements=tuple(
                    self._settlement(row) for row in settlement_rows
                ),
                rewards=tuple(
                    RewardLedgerRecord(
                        ledger_id=str(row["ledger_id"]),
                        campaign_id=str(row["campaign_id"]),
                        settlement_id=str(row["settlement_id"]),
                        session_id=str(row["session_id"]),
                        points_delta=int(row["points_delta"]),
                        reason=str(row["reason"]),
                        created_at=_iso(row["created_at"]),
                    )
                    for row in reward_rows
                ),
                unlocks=tuple(
                    UnlockRecord(
                        unlock_id=str(row["unlock_id"]),
                        campaign_id=str(row["campaign_id"]),
                        source_settlement_id=str(
                            row["source_settlement_id"]
                        ),
                        source_session_id=str(row["source_session_id"]),
                        unlock_key=str(row["unlock_key"]),
                        unlock_type=str(row["unlock_type"]),
                        payload=dict(_json_object(row["payload_json"])),
                        created_at=_iso(row["created_at"]),
                    )
                    for row in unlock_rows
                ),
                manifests=tuple(
                    self._manifest(row) for row in manifest_rows
                ),
                transitions=transitions,
            )
        except PersistenceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(
                f"PostgreSQL 读取章节旅程失败: {exc}"
            ) from exc
        finally:
            conn.close()

    @staticmethod
    def _manuscript(row: Dict[str, Any]) -> WorldlineManuscript:
        return WorldlineManuscript(
            manuscript_id=str(row["manuscript_id"]),
            timeline_id=str(row["timeline_id"]),
            campaign_id=str(row["campaign_id"]),
            root_session_id=str(row["root_session_id"]),
            title=str(row["title"]),
            status=str(row["status"]),
            current_revision=int(row["current_revision"]),
            created_at=_iso(row["created_at"]),
            updated_at=_iso(row["updated_at"]),
        )

    @staticmethod
    def _passage_fingerprint(
        session_id: str,
        source_event_ids: Sequence[str],
    ) -> str:
        raw = session_id + "\n" + "\n".join(source_event_ids)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _hydrate_manuscript_passage_cursor(
        self,
        cursor,
        row: Dict[str, Any],
    ) -> ManuscriptPassage:
        revision_number = int(row["current_revision"])
        content: Optional[ManuscriptPassage] = None
        if revision_number > 0:
            cursor.execute(
                """
                SELECT revision_json FROM manuscript_passage_revisions
                WHERE passage_id = %s AND revision_number = %s
                """,
                (str(row["passage_id"]), revision_number),
            )
            revision_row = cursor.fetchone()
            if revision_row is not None:
                revision = ManuscriptRevision.parse_obj(
                    _json_object(revision_row["revision_json"])
                )
                if revision.passages:
                    content = revision.passages[0]
        source_ids = list(_json_object(row["source_event_ids_json"]))
        base = content or ManuscriptPassage(
            passage_id=str(row["passage_id"]),
            source_event_ids=source_ids,
            from_world_version=int(row["from_world_version"]),
            to_world_version=int(row["to_world_version"]),
            generation_kind=str(row["generation_kind"]),
            generation_status=str(row["generation_status"]),
        )
        return base.copy(
            update={
                "passage_id": str(row["passage_id"]),
                "manuscript_id": str(row["manuscript_id"]),
                "session_id": str(row["session_id"]),
                "chapter_number": int(row["chapter_number"]),
                "entry_id": str(row["entry_id"]),
                "entry_revision": int(row["entry_revision"]),
                "manuscript_sequence": int(row["manuscript_sequence"]),
                "title": str(row["title"]),
                "source_event_ids": source_ids,
                "source_fingerprint": str(row["source_fingerprint"]),
                "from_world_version": int(row["from_world_version"]),
                "to_world_version": int(row["to_world_version"]),
                "generation_kind": ManuscriptSource(
                    str(row["generation_kind"])
                ),
                "generation_status": ManuscriptGenerationStatus(
                    str(row["generation_status"])
                ),
                "current_revision": revision_number,
                "last_error": str(row["last_error"]),
                "created_at": _iso(row["created_at"]),
                "updated_at": _iso(row["updated_at"]),
            },
            deep=True,
        )

    def ensure_manuscript(self, session_id: str) -> WorldlineManuscript:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                lineage = self._ensure_session_lineage_cursor(
                    cursor, session_id
                )
                cursor.execute(
                    """
                    SELECT * FROM worldline_manuscripts
                    WHERE campaign_id = %s FOR UPDATE
                    """,
                    (lineage.campaign_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        """
                        SELECT state_json, save_name FROM world_sessions
                        WHERE session_id = %s FOR UPDATE
                        """,
                        (session_id,),
                    )
                    session = cursor.fetchone()
                    if session is None:
                        raise SessionNotFound(f"会话不存在: {session_id}")
                    state = WorldState.parse_obj(
                        _json_object(session["state_json"])
                    )
                    now = self._now()
                    cursor.execute(
                        """
                        INSERT INTO worldline_manuscripts (
                            manuscript_id, campaign_id, root_session_id,
                            timeline_id, title, status, current_revision,
                            created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, 'active', 0, %s, %s)
                        ON CONFLICT (campaign_id) DO UPDATE
                        SET updated_at = EXCLUDED.updated_at
                        RETURNING *
                        """,
                        (
                            f"manuscript_{secrets.token_hex(12)}",
                            lineage.campaign_id,
                            lineage.root_session_id,
                            state.timeline_id,
                            str(session["save_name"]),
                            now,
                            now,
                        ),
                    )
                    row = cursor.fetchone()
                result = self._manuscript(row)
            conn.commit()
            return result
        except (SessionNotFound, PersistenceError):
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise PersistenceError(
                f"PostgreSQL 创建世界线稿件失败: {exc}"
            ) from exc
        finally:
            conn.close()

    def get_manuscript_for_session(
        self,
        session_id: str,
    ) -> Optional[WorldlineManuscript]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT manuscript.*
                    FROM worldline_manuscripts AS manuscript
                    JOIN session_lineage AS lineage
                      ON lineage.campaign_id = manuscript.campaign_id
                    WHERE lineage.session_id = %s
                    """,
                    (session_id,),
                )
                row = cursor.fetchone()
            return self._manuscript(row) if row is not None else None
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(
                f"PostgreSQL 读取世界线稿件失败: {exc}"
            ) from exc
        finally:
            conn.close()

    def reserve_manuscript_passage(
        self,
        session_id: str,
        source_event_ids: Sequence[str],
        *,
        generation_kind: str = "deterministic",
    ) -> ManuscriptPassage:
        source_ids = [str(item).strip() for item in source_event_ids if str(item).strip()]
        if not source_ids or len(source_ids) != len(set(source_ids)):
            raise PersistenceError("稿件来源事件必须非空且唯一")
        try:
            kind = ManuscriptSource(generation_kind)
        except ValueError as exc:
            raise PersistenceError(f"未知稿件生成类型: {generation_kind}") from exc
        manuscript = self.ensure_manuscript(session_id)
        _, Json, _ = self._driver()
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM worldline_manuscripts
                    WHERE manuscript_id = %s FOR UPDATE
                    """,
                    (manuscript.manuscript_id,),
                )
                manuscript_row = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT event_id, previous_version, new_version
                    FROM world_events
                    WHERE session_id = %s AND event_id = ANY(%s)
                    ORDER BY new_version
                    """,
                    (session_id, source_ids),
                )
                rows = cursor.fetchall()
                if [str(row["event_id"]) for row in rows] != source_ids:
                    raise PersistenceError("稿件来源事件不存在或顺序不一致")
                for previous, current in zip(rows, rows[1:]):
                    if int(current["previous_version"]) != int(previous["new_version"]):
                        raise PersistenceError("稿件来源事件版本不连续")
                fingerprint = self._passage_fingerprint(
                    session_id, source_ids
                )
                cursor.execute(
                    """
                    SELECT * FROM manuscript_passages
                    WHERE manuscript_id = %s AND source_fingerprint = %s
                    """,
                    (manuscript.manuscript_id, fingerprint),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    result = self._hydrate_manuscript_passage_cursor(
                        cursor, existing
                    )
                    conn.commit()
                    return result
                cursor.execute(
                    """
                    SELECT chapter_number, entry_id, entry_revision
                    FROM world_sessions WHERE session_id = %s
                    """,
                    (session_id,),
                )
                session = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT COALESCE(MAX(manuscript_sequence), 0) + 1
                           AS next_sequence
                    FROM manuscript_passages WHERE manuscript_id = %s
                    """,
                    (manuscript.manuscript_id,),
                )
                sequence = cursor.fetchone()
                now = self._now()
                cursor.execute(
                    """
                    INSERT INTO manuscript_passages (
                        passage_id, manuscript_id, session_id, chapter_number,
                        entry_id, entry_revision, manuscript_sequence, title,
                        source_event_ids_json, source_fingerprint,
                        from_world_version, to_world_version, generation_kind,
                        generation_status, current_revision, last_error,
                        created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, '', %s, %s,
                        %s, %s, %s, 'pending', 0, '', %s, %s
                    )
                    ON CONFLICT (manuscript_id, source_fingerprint)
                    DO UPDATE SET updated_at = EXCLUDED.updated_at
                    RETURNING *
                    """,
                    (
                        f"passage_{secrets.token_hex(12)}",
                        manuscript.manuscript_id,
                        session_id,
                        int(session["chapter_number"]),
                        str(session["entry_id"]),
                        int(session["entry_revision"]),
                        int(sequence["next_sequence"]),
                        Json(source_ids),
                        fingerprint,
                        int(rows[0]["new_version"]),
                        int(rows[-1]["new_version"]),
                        kind.value,
                        now,
                        now,
                    ),
                )
                row = cursor.fetchone()
                result = self._hydrate_manuscript_passage_cursor(cursor, row)
            conn.commit()
            return result
        except (SessionNotFound, PersistenceError):
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise PersistenceError(
                f"PostgreSQL 预留稿件段落失败: {exc}"
            ) from exc
        finally:
            conn.close()

    def complete_manuscript_passage(
        self,
        passage_id: str,
        revision: ManuscriptRevision,
        *,
        expected_current_revision: Optional[int] = None,
    ) -> ManuscriptPassage:
        if len(revision.passages) != 1:
            raise PersistenceError("一个预留 passage 必须对应一个正文 passage")
        _, Json, _ = self._driver()
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM manuscript_passages
                    WHERE passage_id = %s FOR UPDATE
                    """,
                    (passage_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise PersistenceError(f"稿件段落不存在: {passage_id}")
                current_revision = int(row["current_revision"])
                if (
                    expected_current_revision is not None
                    and current_revision != expected_current_revision
                ):
                    raise ManuscriptRevisionConflict(
                        f"稿件段落修订冲突: 期望 {expected_current_revision}，"
                        f"实际 {current_revision}"
                    )
                source_ids = list(_json_object(row["source_event_ids_json"]))
                if revision.source_event_ids != source_ids:
                    raise PersistenceError("稿件修订的来源事件与预留记录不一致")
                next_revision = current_revision + 1
                parent_id = None
                if int(row["current_revision"]) > 0:
                    cursor.execute(
                        """
                        SELECT revision_json
                        FROM manuscript_passage_revisions
                        WHERE passage_id = %s AND revision_number = %s
                        """,
                        (passage_id, int(row["current_revision"])),
                    )
                    previous = cursor.fetchone()
                    if previous is not None:
                        parent_id = ManuscriptRevision.parse_obj(
                            _json_object(previous["revision_json"])
                        ).revision_id
                content = revision.passages[0].copy(
                    update={
                        "passage_id": passage_id,
                        "manuscript_id": str(row["manuscript_id"]),
                        "session_id": str(row["session_id"]),
                        "chapter_number": int(row["chapter_number"]),
                        "entry_id": str(row["entry_id"]),
                        "entry_revision": int(row["entry_revision"]),
                        "manuscript_sequence": int(row["manuscript_sequence"]),
                        "source_fingerprint": str(row["source_fingerprint"]),
                        "from_world_version": int(row["from_world_version"]),
                        "to_world_version": int(row["to_world_version"]),
                        "generation_kind": revision.source,
                        "generation_status": ManuscriptGenerationStatus.ready,
                        "current_revision": next_revision,
                        "last_error": "",
                    },
                    deep=True,
                )
                stored_revision = revision.copy(
                    update={
                        "revision_id": f"{passage_id}_r{next_revision}",
                        "manuscript_id": str(row["manuscript_id"]),
                        "revision_number": next_revision,
                        "parent_revision_id": parent_id,
                        "passages": [content],
                    },
                    deep=True,
                )
                now = self._now()
                cursor.execute(
                    """
                    INSERT INTO manuscript_passage_revisions (
                        passage_id, revision_number, revision_json, created_at
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    (
                        passage_id,
                        next_revision,
                        Json(stored_revision.dict()),
                        now,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE manuscript_passages
                    SET generation_kind = %s, generation_status = 'ready',
                        current_revision = %s, last_error = '', updated_at = %s
                    WHERE passage_id = %s RETURNING *
                    """,
                    (
                        revision.source.value,
                        next_revision,
                        now,
                        passage_id,
                    ),
                )
                updated = cursor.fetchone()
                cursor.execute(
                    """
                    UPDATE worldline_manuscripts
                    SET current_revision = current_revision + 1,
                        updated_at = %s
                    WHERE manuscript_id = %s
                    """,
                    (now, str(row["manuscript_id"])),
                )
                result = self._hydrate_manuscript_passage_cursor(
                    cursor, updated
                )
            conn.commit()
            return result
        except PersistenceError:
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise PersistenceError(
                f"PostgreSQL 完成稿件段落失败: {exc}"
            ) from exc
        finally:
            conn.close()

    def fail_manuscript_passage(
        self,
        passage_id: str,
        error: str,
    ) -> ManuscriptPassage:
        message = str(error or "").strip()[:4000] or "稿件生成失败"
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM manuscript_passages
                    WHERE passage_id = %s FOR UPDATE
                    """,
                    (passage_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise PersistenceError(f"稿件段落不存在: {passage_id}")
                status = "ready" if int(row["current_revision"]) > 0 else "failed"
                cursor.execute(
                    """
                    UPDATE manuscript_passages
                    SET generation_status = %s, last_error = %s,
                        updated_at = %s
                    WHERE passage_id = %s RETURNING *
                    """,
                    (status, message, self._now(), passage_id),
                )
                result = self._hydrate_manuscript_passage_cursor(
                    cursor, cursor.fetchone()
                )
            conn.commit()
            return result
        except PersistenceError:
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise PersistenceError(
                f"PostgreSQL 标记稿件失败状态失败: {exc}"
            ) from exc
        finally:
            conn.close()

    def get_manuscript_passage(
        self,
        passage_id: str,
    ) -> Optional[ManuscriptPassage]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM manuscript_passages WHERE passage_id = %s",
                    (passage_id,),
                )
                row = cursor.fetchone()
                return (
                    self._hydrate_manuscript_passage_cursor(cursor, row)
                    if row is not None
                    else None
                )
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(
                f"PostgreSQL 读取稿件段落失败: {exc}"
            ) from exc
        finally:
            conn.close()

    def list_manuscript_passages(
        self,
        session_id: str,
    ) -> List[ManuscriptPassage]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM manuscript_passages
                    WHERE session_id = %s ORDER BY manuscript_sequence
                    """,
                    (session_id,),
                )
                rows = cursor.fetchall()
                return [
                    self._hydrate_manuscript_passage_cursor(cursor, row)
                    for row in rows
                ]
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(
                f"PostgreSQL 读取稿件段落列表失败: {exc}"
            ) from exc
        finally:
            conn.close()

    def list_campaign_manuscript_passages(
        self,
        session_id: str,
    ) -> List[ManuscriptPassage]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT passage.*
                    FROM manuscript_passages AS passage
                    JOIN session_lineage AS requested_lineage
                      ON requested_lineage.session_id = %s
                    JOIN session_lineage AS passage_lineage
                      ON passage_lineage.session_id = passage.session_id
                     AND passage_lineage.campaign_id = requested_lineage.campaign_id
                    ORDER BY passage.manuscript_sequence
                    """,
                    (session_id,),
                )
                rows = cursor.fetchall()
                return [
                    self._hydrate_manuscript_passage_cursor(cursor, row)
                    for row in rows
                ]
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(
                f"PostgreSQL 读取 campaign 稿件段落列表失败: {exc}"
            ) from exc
        finally:
            conn.close()

    def list_manuscript_passage_revisions(
        self,
        passage_id: str,
    ) -> List[ManuscriptRevision]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT revision_json FROM manuscript_passage_revisions
                    WHERE passage_id = %s ORDER BY revision_number
                    """,
                    (passage_id,),
                )
                rows = cursor.fetchall()
            return [
                ManuscriptRevision.parse_obj(_json_object(row["revision_json"]))
                for row in rows
            ]
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(
                f"PostgreSQL 读取稿件修订失败: {exc}"
            ) from exc
        finally:
            conn.close()

    def select_manuscript_passage_revision(
        self,
        passage_id: str,
        revision_number: int,
        *,
        expected_current_revision: Optional[int] = None,
    ) -> ManuscriptPassage:
        """Only move the passage's current pointer; immutable revisions remain."""

        target_revision = int(revision_number)
        if target_revision < 1:
            raise PersistenceError("稿件修订号必须大于 0")
        _, Json, _ = self._driver()
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM manuscript_passages
                    WHERE passage_id = %s FOR UPDATE
                    """,
                    (passage_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise PersistenceError(f"稿件段落不存在: {passage_id}")
                current_revision = int(row["current_revision"])
                if (
                    expected_current_revision is not None
                    and current_revision != expected_current_revision
                ):
                    raise ManuscriptRevisionConflict(
                        f"稿件段落修订冲突: 期望 {expected_current_revision}，"
                        f"实际 {current_revision}"
                    )
                cursor.execute(
                    """
                    SELECT revision_json
                    FROM manuscript_passage_revisions
                    WHERE passage_id = %s AND revision_number = %s
                    """,
                    (passage_id, target_revision),
                )
                revision_row = cursor.fetchone()
                if revision_row is None:
                    raise PersistenceError(
                        f"稿件修订不存在: {passage_id}@{target_revision}"
                    )
                revision = ManuscriptRevision.parse_obj(
                    _json_object(revision_row["revision_json"])
                )
                if not revision.passages:
                    raise PersistenceError("稿件修订缺少正文段落")
                source_ids = list(_json_object(row["source_event_ids_json"]))
                if revision.source_event_ids != source_ids:
                    raise PersistenceError("稿件修订来源事件与段落不一致")
                content = revision.passages[0]
                now = self._now()
                cursor.execute(
                    """
                    UPDATE manuscript_passages
                    SET title = %s, generation_kind = %s,
                        generation_status = 'ready', current_revision = %s,
                        last_error = '', updated_at = %s
                    WHERE passage_id = %s
                    RETURNING *
                    """,
                    (
                        content.title,
                        revision.source.value,
                        target_revision,
                        now,
                        passage_id,
                    ),
                )
                updated = cursor.fetchone()
                conn.commit()
                return self._hydrate_manuscript_passage_cursor(cursor, updated)
        except (ManuscriptRevisionConflict, PersistenceError):
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise PersistenceError(
                f"选择稿件修订失败: {exc}"
            ) from exc
        finally:
            conn.close()

    def export_session(self, session_id: str) -> Dict[str, Any]:
        metadata = self.get_metadata(session_id)
        state = self.get_state(session_id)
        if metadata is None or state is None:
            raise SessionNotFound(f"会话不存在: {session_id}")
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT base_state_json, base_state_version
                    FROM world_sessions WHERE session_id = %s
                    """,
                    (session_id,),
                )
                base_row = cursor.fetchone()
        finally:
            conn.close()
        if base_row is None:
            raise SessionNotFound(f"会话不存在: {session_id}")
        try:
            base_state = WorldState.parse_obj(
                _json_object(base_row["base_state_json"])
            )
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(
                f"会话基线状态损坏: {session_id}"
            ) from exc
        events = self.list_events(session_id)
        turns = self.list_turns(session_id)
        manuscript = self.get_manuscript_for_session(session_id)
        passages = self.list_manuscript_passages(session_id)
        passage_revisions = [
            {
                "passage_id": passage.passage_id,
                "revisions": [
                    revision.dict()
                    for revision in self.list_manuscript_passage_revisions(
                        passage.passage_id
                    )
                ],
            }
            for passage in passages
        ]
        return {
            "format": "ai-transmigration-save",
            "format_version": 3,
            "exported_at": _iso(self._now()),
            "source_session_id": session_id,
            "save": {
                "name": metadata.save_name,
                "world_package_id": metadata.world_package_id,
                "default_actor_id": metadata.default_actor_id,
                "created_at": metadata.created_at,
                "updated_at": metadata.updated_at,
                "book_id": metadata.book_id,
                "entry_id": metadata.entry_id,
                "chapter_number": metadata.chapter_number,
                "entry_revision": metadata.entry_revision,
            },
            "base_state": base_state.dict(),
            "base_state_version": int(base_row["base_state_version"]),
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
            "manuscript": manuscript.dict() if manuscript is not None else None,
            "manuscript_passages": [passage.dict() for passage in passages],
            "manuscript_passage_revisions": passage_revisions,
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
            base_state,
            events,
            turns,
            world_package_id,
            default_actor_id,
            manuscript,
            passages,
            revisions_by_passage,
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
                            base_state_json, base_state_version,
                            created_at, updated_at, book_id, entry_id,
                            chapter_number, entry_revision
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            session_id,
                            imported_name,
                            world_package_id,
                            default_actor_id,
                            Json(state.dict()),
                            state.version,
                            Json(base_state.dict()),
                            base_state.version,
                            now,
                            now,
                            str(save.get("book_id") or ""),
                            str(save.get("entry_id") or ""),
                            int(save.get("chapter_number") or 0),
                            int(save.get("entry_revision") or 0),
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
                    if manuscript is not None:
                        campaign_id = f"campaign_{session_id}"
                        manuscript_id = f"manuscript_{secrets.token_hex(12)}"
                        cursor.execute(
                            """
                            INSERT INTO progression_campaigns (
                                campaign_id, root_session_id,
                                created_at, updated_at
                            ) VALUES (%s, %s, %s, %s)
                            """,
                            (campaign_id, session_id, now, now),
                        )
                        cursor.execute(
                            """
                            INSERT INTO session_lineage (
                                session_id, campaign_id, root_session_id,
                                parent_session_id, depth, source_settlement_id,
                                target_world_package_id, created_at
                            ) VALUES (%s, %s, %s, NULL, 0, NULL, %s, %s)
                            """,
                            (
                                session_id,
                                campaign_id,
                                session_id,
                                world_package_id,
                                now,
                            ),
                        )
                        cursor.execute(
                            """
                            INSERT INTO worldline_manuscripts (
                                manuscript_id, campaign_id, root_session_id,
                                timeline_id, title, status,
                                current_revision, created_at, updated_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                manuscript_id,
                                campaign_id,
                                session_id,
                                state.timeline_id,
                                manuscript.title or imported_name,
                                manuscript.status,
                                sum(
                                    len(
                                        revisions_by_passage.get(
                                            passage.passage_id,
                                            [],
                                        )
                                    )
                                    for passage in passages
                                ),
                                now,
                                now,
                            ),
                        )
                        passage_id_map = {
                            passage.passage_id: f"passage_{secrets.token_hex(12)}"
                            for passage in passages
                        }
                        for sequence, passage in enumerate(passages, start=1):
                            new_passage_id = passage_id_map[passage.passage_id]
                            revisions = revisions_by_passage.get(
                                passage.passage_id,
                                [],
                            )
                            current_revision = min(
                                passage.current_revision,
                                len(revisions),
                            )
                            cursor.execute(
                                """
                                INSERT INTO manuscript_passages (
                                    passage_id, manuscript_id, session_id,
                                    chapter_number, entry_id, entry_revision,
                                    manuscript_sequence, title,
                                    source_event_ids_json, source_fingerprint,
                                    from_world_version, to_world_version,
                                    generation_kind, generation_status,
                                    current_revision, last_error,
                                    created_at, updated_at
                                ) VALUES (
                                    %s, %s, %s, %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                                )
                                """,
                                (
                                    new_passage_id,
                                    manuscript_id,
                                    session_id,
                                    passage.chapter_number or int(
                                        save.get("chapter_number") or 0
                                    ),
                                    passage.entry_id or str(
                                        save.get("entry_id") or ""
                                    ),
                                    passage.entry_revision or int(
                                        save.get("entry_revision") or 0
                                    ),
                                    sequence,
                                    passage.title,
                                    Json(passage.source_event_ids),
                                    self._passage_fingerprint(
                                        session_id,
                                        passage.source_event_ids,
                                    ),
                                    passage.from_world_version,
                                    passage.to_world_version,
                                    passage.generation_kind.value,
                                    passage.generation_status.value,
                                    current_revision,
                                    passage.last_error,
                                    now,
                                    now,
                                ),
                            )
                            parent_revision_id = None
                            for revision in revisions:
                                content = revision.passages[0].copy(
                                    update={
                                        "passage_id": new_passage_id,
                                        "manuscript_id": manuscript_id,
                                        "session_id": session_id,
                                        "manuscript_sequence": sequence,
                                    },
                                    deep=True,
                                )
                                stored_revision = revision.copy(
                                    update={
                                        "revision_id": (
                                            f"{new_passage_id}_r"
                                            f"{revision.revision_number}"
                                        ),
                                        "manuscript_id": manuscript_id,
                                        "parent_revision_id": parent_revision_id,
                                        "passages": [content],
                                    },
                                    deep=True,
                                )
                                cursor.execute(
                                    """
                                    INSERT INTO manuscript_passage_revisions (
                                        passage_id, revision_number,
                                        revision_json, created_at
                                    ) VALUES (%s, %s, %s, %s)
                                    """,
                                    (
                                        new_passage_id,
                                        revision.revision_number,
                                        Json(stored_revision.dict()),
                                        now,
                                    ),
                                )
                                parent_revision_id = stored_revision.revision_id
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
