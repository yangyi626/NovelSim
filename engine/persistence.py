"""世界会话与事件的 SQLite 持久化。

这一层只负责保存已经由 TurnPipeline 结算完成的 WorldState / WorldEvent，
不参与规则判断或状态推演。存储接口保持独立，后续迁移 PostgreSQL 时，
TurnPipeline 和世界模型无需改动。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

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
from .event import replay_events
from .manuscript import (
    ManuscriptGenerationStatus,
    ManuscriptPassage,
    ManuscriptRevision,
    ManuscriptSource,
    WorldlineManuscript,
)


class PersistenceError(RuntimeError):
    """持久化读写失败。"""


class SessionNotFound(PersistenceError):
    """会话不存在。"""


class VersionConflict(PersistenceError):
    """持久化时发现世界版本已被其他请求推进。"""


class StateVersionUnavailable(PersistenceError):
    """请求的历史世界版本无法从已保存基线重建。"""


class ManuscriptRevisionConflict(PersistenceError):
    """稿件段落已被其他写入推进到不同修订。"""


@dataclass(frozen=True)
class SessionMetadata:
    session_id: str
    save_name: str
    world_package_id: str
    default_actor_id: str
    state_version: int
    created_at: str
    updated_at: str
    book_id: str = ""
    entry_id: str = ""
    chapter_number: int = 0
    entry_revision: int = 0


@dataclass(frozen=True)
class TurnRecord:
    session_id: str
    turn_sequence: int
    world_version: int
    player_input: str
    result: Dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class MemoryRecord:
    """一个角色可检索的长期记忆片段。"""

    memory_id: str
    session_id: str
    character_id: str
    source_event_id: str
    world_version: int
    memory_type: str
    content: str
    importance: float
    created_at: str
    retrieval_score: float = 0.0
    evidence_event_ids: Tuple[str, ...] = ()
    claim_fact_id: str = ""
    claim_belief: str = ""
    claim_confidence: float = 0.0
    semantic_score: float = 0.0


_SEARCH_RUN_RE = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u9fff]+")


def _memory_search_terms(text: str) -> List[str]:
    """生成兼容 SQLite unicode61 的中英文词项。

    unicode61 不做中文分词，因此额外生成相邻双字词；这样无需引入分词器，
    也能在当前 SQLite 阶段检索中文事件摘要。
    """

    terms: List[str] = []
    for run in _SEARCH_RUN_RE.findall(text.lower()):
        if "\u3400" <= run[0] <= "\u9fff":
            if len(run) <= 2:
                terms.append(run)
            else:
                terms.extend(run[index:index + 2] for index in range(len(run) - 1))
        else:
            terms.append(run)
    # 保序去重，避免长叙事让 FTS 索引膨胀得过快。
    return list(dict.fromkeys(term for term in terms if term))


class SQLiteWorldStore:
    """SQLite 世界存储。

    每个方法使用独立连接，适合 FastAPI 的多线程同步端点。
    commit_turn 使用 ``BEGIN IMMEDIATE`` 和版本号检查，保证状态快照与事件
    要么同时写入，要么都不写入。
    """

    def __init__(self, database_path: Union[str, Path]):
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._fts5_enabled = False
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.database_path),
            timeout=30,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS world_sessions (
                    session_id TEXT PRIMARY KEY,
                    save_name TEXT NOT NULL,
                    world_package_id TEXT NOT NULL,
                    default_actor_id TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    state_version INTEGER NOT NULL,
                    base_state_json TEXT NOT NULL,
                    base_state_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    book_id TEXT NOT NULL DEFAULT '',
                    entry_id TEXT NOT NULL DEFAULT '',
                    chapter_number INTEGER NOT NULL DEFAULT 0,
                    entry_revision INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS world_events (
                    session_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    previous_version INTEGER NOT NULL,
                    new_version INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, event_id),
                    UNIQUE (session_id, new_version),
                    FOREIGN KEY (session_id)
                        REFERENCES world_sessions(session_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_world_events_session_version
                    ON world_events(session_id, new_version);

                CREATE TABLE IF NOT EXISTS world_turns (
                    session_id TEXT NOT NULL,
                    turn_sequence INTEGER NOT NULL,
                    world_version INTEGER NOT NULL,
                    player_input TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, turn_sequence),
                    FOREIGN KEY (session_id)
                        REFERENCES world_sessions(session_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_world_turns_session_sequence
                    ON world_turns(session_id, turn_sequence);

                CREATE TABLE IF NOT EXISTS character_memories (
                    memory_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    character_id TEXT NOT NULL,
                    source_event_id TEXT NOT NULL,
                    world_version INTEGER NOT NULL,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    search_text TEXT NOT NULL,
                    importance REAL NOT NULL,
                    evidence_event_ids_json TEXT NOT NULL DEFAULT '[]',
                    claim_fact_id TEXT NOT NULL DEFAULT '',
                    claim_belief TEXT NOT NULL DEFAULT '',
                    claim_confidence REAL NOT NULL DEFAULT 0.0,
                    semantic_score REAL NOT NULL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    UNIQUE (
                        session_id, character_id, source_event_id, memory_type
                    ),
                    FOREIGN KEY (session_id)
                        REFERENCES world_sessions(session_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_character_memories_scope
                    ON character_memories(
                        session_id, character_id, world_version
                    );

                CREATE TABLE IF NOT EXISTS joint_plan_runtime (
                    session_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    runtime_json TEXT NOT NULL,
                    observed_world_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, plan_id),
                    FOREIGN KEY (session_id)
                        REFERENCES world_sessions(session_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_joint_plan_runtime_active
                    ON joint_plan_runtime(session_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS progression_campaigns (
                    campaign_id TEXT PRIMARY KEY,
                    root_session_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (root_session_id)
                        REFERENCES world_sessions(session_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS session_lineage (
                    session_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    root_session_id TEXT NOT NULL,
                    parent_session_id TEXT,
                    depth INTEGER NOT NULL CHECK (depth >= 0),
                    source_settlement_id TEXT,
                    target_world_package_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (parent_session_id, target_world_package_id),
                    FOREIGN KEY (session_id)
                        REFERENCES world_sessions(session_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (campaign_id)
                        REFERENCES progression_campaigns(campaign_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (root_session_id)
                        REFERENCES world_sessions(session_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (parent_session_id)
                        REFERENCES world_sessions(session_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_session_lineage_campaign
                    ON session_lineage(campaign_id, depth, created_at);

                CREATE TABLE IF NOT EXISTS progression_settlements (
                    settlement_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    session_id TEXT NOT NULL UNIQUE,
                    world_package_id TEXT NOT NULL,
                    settlement_event_id TEXT NOT NULL,
                    settled_world_version INTEGER NOT NULL,
                    ending_id TEXT NOT NULL,
                    ending_title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    reward_points INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    UNIQUE (session_id, settlement_event_id),
                    FOREIGN KEY (campaign_id)
                        REFERENCES progression_campaigns(campaign_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (session_id)
                        REFERENCES world_sessions(session_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (session_id, settlement_event_id)
                        REFERENCES world_events(session_id, event_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS progression_reward_ledger (
                    ledger_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    settlement_id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL,
                    points_delta INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (campaign_id)
                        REFERENCES progression_campaigns(campaign_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (settlement_id)
                        REFERENCES progression_settlements(settlement_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (session_id)
                        REFERENCES world_sessions(session_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS progression_unlocks (
                    unlock_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    source_settlement_id TEXT NOT NULL,
                    source_session_id TEXT NOT NULL,
                    unlock_key TEXT NOT NULL,
                    unlock_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (campaign_id, unlock_key),
                    FOREIGN KEY (campaign_id)
                        REFERENCES progression_campaigns(campaign_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (source_settlement_id)
                        REFERENCES progression_settlements(settlement_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (source_session_id)
                        REFERENCES world_sessions(session_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS progression_manifests (
                    manifest_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    parent_session_id TEXT NOT NULL,
                    child_session_id TEXT NOT NULL UNIQUE,
                    target_world_package_id TEXT NOT NULL,
                    source_settlement_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (parent_session_id, target_world_package_id),
                    FOREIGN KEY (campaign_id)
                        REFERENCES progression_campaigns(campaign_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (parent_session_id)
                        REFERENCES world_sessions(session_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (child_session_id)
                        REFERENCES world_sessions(session_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (source_settlement_id)
                        REFERENCES progression_settlements(settlement_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS progression_transitions (
                    transition_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    campaign_id TEXT NOT NULL,
                    parent_session_id TEXT NOT NULL,
                    target_world_package_id TEXT NOT NULL,
                    child_session_id TEXT NOT NULL UNIQUE,
                    settlement_id TEXT NOT NULL,
                    manifest_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    UNIQUE (parent_session_id, target_world_package_id),
                    FOREIGN KEY (campaign_id)
                        REFERENCES progression_campaigns(campaign_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (parent_session_id)
                        REFERENCES world_sessions(session_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (child_session_id)
                        REFERENCES world_sessions(session_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (settlement_id)
                        REFERENCES progression_settlements(settlement_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (manifest_id)
                        REFERENCES progression_manifests(manifest_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS worldline_manuscripts (
                    manuscript_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL UNIQUE,
                    root_session_id TEXT NOT NULL,
                    timeline_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    current_revision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (campaign_id)
                        REFERENCES progression_campaigns(campaign_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (root_session_id)
                        REFERENCES world_sessions(session_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS manuscript_passages (
                    passage_id TEXT PRIMARY KEY,
                    manuscript_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    chapter_number INTEGER NOT NULL DEFAULT 0,
                    entry_id TEXT NOT NULL DEFAULT '',
                    entry_revision INTEGER NOT NULL DEFAULT 0,
                    manuscript_sequence INTEGER NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    source_event_ids_json TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    from_world_version INTEGER NOT NULL,
                    to_world_version INTEGER NOT NULL,
                    generation_kind TEXT NOT NULL,
                    generation_status TEXT NOT NULL,
                    current_revision INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (manuscript_id, source_fingerprint),
                    UNIQUE (manuscript_id, manuscript_sequence),
                    FOREIGN KEY (manuscript_id)
                        REFERENCES worldline_manuscripts(manuscript_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (session_id)
                        REFERENCES world_sessions(session_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_manuscript_passages_session
                    ON manuscript_passages(session_id, manuscript_sequence);

                CREATE TABLE IF NOT EXISTS manuscript_passage_revisions (
                    passage_id TEXT NOT NULL,
                    revision_number INTEGER NOT NULL,
                    revision_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (passage_id, revision_number),
                    FOREIGN KEY (passage_id)
                        REFERENCES manuscript_passages(passage_id)
                        ON DELETE CASCADE
                );
                """
            )
            # 兼容第一版 SQLite 数据库：原 world_sessions 没有存档名。
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(world_sessions)")
            }
            if "save_name" not in columns:
                conn.execute(
                    """
                    ALTER TABLE world_sessions
                    ADD COLUMN save_name TEXT NOT NULL DEFAULT '华容巷世界线'
                    """
                )
            for column, definition in {
                "book_id": "TEXT NOT NULL DEFAULT ''",
                "entry_id": "TEXT NOT NULL DEFAULT ''",
                "chapter_number": "INTEGER NOT NULL DEFAULT 0",
                "entry_revision": "INTEGER NOT NULL DEFAULT 0",
                "base_state_json": "TEXT NOT NULL DEFAULT ''",
                "base_state_version": "INTEGER NOT NULL DEFAULT -1",
            }.items():
                if column not in columns:
                    conn.execute(
                        f"ALTER TABLE world_sessions ADD COLUMN {column} {definition}"
                    )
            conn.execute(
                """
                UPDATE world_sessions
                SET base_state_json = state_json,
                    base_state_version = state_version
                WHERE base_state_json = '' OR base_state_version < 0
                """
            )
            memory_columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(character_memories)"
                )
            }
            memory_migrations = {
                "evidence_event_ids_json": (
                    "TEXT NOT NULL DEFAULT '[]'"
                ),
                "claim_fact_id": "TEXT NOT NULL DEFAULT ''",
                "claim_belief": "TEXT NOT NULL DEFAULT ''",
                "claim_confidence": "REAL NOT NULL DEFAULT 0.0",
                "semantic_score": "REAL NOT NULL DEFAULT 0.0",
            }
            for column, definition in memory_migrations.items():
                if column not in memory_columns:
                    conn.execute(
                        f"""
                        ALTER TABLE character_memories
                        ADD COLUMN {column} {definition}
                        """
                    )

            # FTS 是派生检索索引；极少数不带 FTS5 的 SQLite 构建仍可用
            # LIKE 回退，不影响权威事件和状态的持久化。
            try:
                fts_already_exists = conn.execute(
                    """
                    SELECT 1
                    FROM sqlite_master
                    WHERE type = 'table'
                      AND name = 'character_memories_fts'
                    """
                ).fetchone() is not None
                conn.executescript(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS character_memories_fts
                    USING fts5(
                        content,
                        search_text,
                        content='character_memories',
                        content_rowid='rowid',
                        tokenize='unicode61 remove_diacritics 2'
                    );

                    CREATE TRIGGER IF NOT EXISTS character_memories_ai
                    AFTER INSERT ON character_memories BEGIN
                        INSERT INTO character_memories_fts(
                            rowid, content, search_text
                        ) VALUES (new.rowid, new.content, new.search_text);
                    END;

                    CREATE TRIGGER IF NOT EXISTS character_memories_ad
                    AFTER DELETE ON character_memories BEGIN
                        INSERT INTO character_memories_fts(
                            character_memories_fts,
                            rowid,
                            content,
                            search_text
                        ) VALUES (
                            'delete',
                            old.rowid,
                            old.content,
                            old.search_text
                        );
                    END;

                    CREATE TRIGGER IF NOT EXISTS character_memories_au
                    AFTER UPDATE ON character_memories BEGIN
                        INSERT INTO character_memories_fts(
                            character_memories_fts,
                            rowid,
                            content,
                            search_text
                        ) VALUES (
                            'delete',
                            old.rowid,
                            old.content,
                            old.search_text
                        );
                        INSERT INTO character_memories_fts(
                            rowid, content, search_text
                        ) VALUES (new.rowid, new.content, new.search_text);
                    END;
                    """
                )
                if not fts_already_exists:
                    conn.execute(
                        "INSERT INTO character_memories_fts"
                        "(character_memories_fts) VALUES ('rebuild')"
                    )
                self._fts5_enabled = True
            except sqlite3.OperationalError:
                self._fts5_enabled = False

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

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
        """保存初始快照并返回会话 ID。"""

        attempts = 1 if session_id else 5
        for _ in range(attempts):
            sid = session_id or secrets.token_hex(8)
            now = self._now()
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO world_sessions (
                            session_id, save_name, world_package_id, default_actor_id,
                            state_json, state_version,
                            base_state_json, base_state_version,
                            created_at, updated_at, book_id, entry_id,
                            chapter_number, entry_revision
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sid,
                            self._validate_save_name(save_name),
                            world_package_id,
                            default_actor_id,
                            state.json(ensure_ascii=False),
                            state.version,
                            state.json(ensure_ascii=False),
                            state.version,
                            now,
                            now,
                            book_id,
                            entry_id,
                            int(chapter_number),
                            int(entry_revision),
                        ),
                    )
                return sid
            except sqlite3.IntegrityError as exc:
                if session_id:
                    raise PersistenceError(f"会话已存在: {sid}") from exc
        raise PersistenceError("生成唯一会话 ID 失败")

    def get_state(self, session_id: str) -> Optional[WorldState]:
        """读取当前权威状态；会话不存在时返回 None。"""

        with self._connect() as conn:
            row = conn.execute(
                "SELECT state_json FROM world_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return WorldState.parse_raw(row["state_json"])
        except Exception as exc:
            raise PersistenceError(f"会话状态损坏: {session_id}") from exc

    def get_state_at_version(
        self,
        session_id: str,
        world_version: int,
    ) -> Optional[WorldState]:
        """从不变基线快照和追加事件重建指定版本。"""

        requested = int(world_version)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT state_json, state_version,
                       base_state_json, base_state_version
                FROM world_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
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
            try:
                if requested == current_version:
                    return WorldState.parse_raw(row["state_json"])
                base_state = WorldState.parse_raw(row["base_state_json"])
                event_rows = conn.execute(
                    """
                    SELECT event_json
                    FROM world_events
                    WHERE session_id = ?
                      AND new_version > ?
                      AND new_version <= ?
                    ORDER BY new_version ASC
                    """,
                    (session_id, base_version, requested),
                ).fetchall()
                events = [
                    WorldEvent.parse_raw(item["event_json"])
                    for item in event_rows
                ]
                rebuilt = replay_events(base_state, events)
                if rebuilt.version != requested:
                    raise StateVersionUnavailable(
                        "历史世界事件链不完整: "
                        f"requested={requested}, rebuilt={rebuilt.version}"
                    )
                return rebuilt
            except StateVersionUnavailable:
                raise
            except Exception as exc:
                raise PersistenceError(
                    f"历史世界状态损坏: {session_id}@{requested}"
                ) from exc

    def save_joint_plan_runtime(
        self,
        session_id: str,
        plan: Any,
        runtime: Any,
    ) -> None:
        """Persist an executable plan and its synchronization pointers.

        The authoritative world and the plan runtime deliberately remain
        separate records.  Recovery reconciles action ``call_id`` values with
        the append-only event log before dispatch, so a crash between the two
        commits cannot execute an already committed action twice.
        """

        if runtime.plan_id != plan.plan_id:
            raise PersistenceError("计划与运行时 plan_id 不一致")
        now = self._now()
        try:
            plan_json = plan.json(ensure_ascii=False)
            runtime_json = runtime.json(ensure_ascii=False)
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                session = conn.execute(
                    "SELECT state_version FROM world_sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if session is None:
                    raise SessionNotFound(f"会话不存在: {session_id}")
                if int(runtime.observed_world_version) > int(session["state_version"]):
                    raise VersionConflict(
                        "计划运行时版本不能领先于权威世界: "
                        f"runtime={runtime.observed_world_version}, "
                        f"world={session['state_version']}"
                    )
                conn.execute(
                    """
                    INSERT INTO joint_plan_runtime (
                        session_id, plan_id, plan_json, runtime_json,
                        observed_world_version, status, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (session_id, plan_id) DO UPDATE SET
                        plan_json = excluded.plan_json,
                        runtime_json = excluded.runtime_json,
                        observed_world_version = excluded.observed_world_version,
                        status = excluded.status,
                        updated_at = excluded.updated_at
                    """,
                    (
                        session_id,
                        plan.plan_id,
                        plan_json,
                        runtime_json,
                        int(runtime.observed_world_version),
                        runtime.status.value,
                        now,
                    ),
                )
        except (SessionNotFound, VersionConflict):
            raise
        except Exception as exc:
            raise PersistenceError("保存联合计划运行时失败") from exc

    def get_joint_plan_runtime(
        self,
        session_id: str,
        plan_id: Optional[str] = None,
    ) -> Optional[Tuple[Any, Any]]:
        """Load one plan/runtime pair, defaulting to the most recent one."""

        from .joint_plan import JointPlan, PlanRuntimeState

        with self._connect() as conn:
            if plan_id is None:
                row = conn.execute(
                    """
                    SELECT plan_json, runtime_json
                    FROM joint_plan_runtime
                    WHERE session_id = ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (session_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT plan_json, runtime_json
                    FROM joint_plan_runtime
                    WHERE session_id = ? AND plan_id = ?
                    """,
                    (session_id, plan_id),
                ).fetchone()
        if row is None:
            return None
        try:
            return (
                JointPlan.parse_raw(row["plan_json"]),
                PlanRuntimeState.parse_raw(row["runtime_json"]),
            )
        except Exception as exc:
            raise PersistenceError("联合计划运行时数据损坏") from exc

    def list_joint_plan_runtimes(self, session_id: str) -> List[Tuple[Any, Any]]:
        """List saved plan revisions in newest-first order."""

        from .joint_plan import JointPlan, PlanRuntimeState

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT plan_json, runtime_json
                FROM joint_plan_runtime
                WHERE session_id = ?
                ORDER BY updated_at DESC
                """,
                (session_id,),
            ).fetchall()
        try:
            return [
                (
                    JointPlan.parse_raw(row["plan_json"]),
                    PlanRuntimeState.parse_raw(row["runtime_json"]),
                )
                for row in rows
            ]
        except Exception as exc:
            raise PersistenceError("联合计划运行时数据损坏") from exc

    def get_metadata(self, session_id: str) -> Optional[SessionMetadata]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT session_id, save_name, world_package_id, default_actor_id,
                       state_version, created_at, updated_at, book_id, entry_id,
                       chapter_number, entry_revision
                FROM world_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return SessionMetadata(**dict(row))

    def list_sessions(self) -> List[SessionMetadata]:
        """按最近游玩时间倒序列出全部存档。"""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT session_id, save_name, world_package_id, default_actor_id,
                       state_version, created_at, updated_at, book_id, entry_id,
                       chapter_number, entry_revision
                FROM world_sessions
                ORDER BY updated_at DESC, created_at DESC
                """
            ).fetchall()
        return [SessionMetadata(**dict(row)) for row in rows]

    @staticmethod
    def _validate_save_name(save_name: str) -> str:
        name = (save_name or "").strip()
        if not name:
            raise PersistenceError("存档名不能为空")
        if len(name) > 80:
            raise PersistenceError("存档名不能超过 80 个字符")
        return name

    def rename_session(self, session_id: str, save_name: str) -> None:
        """修改存档显示名。"""

        name = self._validate_save_name(save_name)
        with self._connect() as conn:
            updated = conn.execute(
                """
                UPDATE world_sessions
                SET save_name = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (name, self._now(), session_id),
            )
        if updated.rowcount != 1:
            raise SessionNotFound(f"会话不存在: {session_id}")

    def delete_session(self, session_id: str) -> bool:
        """删除一个存档；事件和回合记录由外键级联删除。"""

        with self._connect() as conn:
            deleted = conn.execute(
                "DELETE FROM world_sessions WHERE session_id = ?",
                (session_id,),
            )
        return deleted.rowcount == 1

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
        """为多个角色幂等记录同一事件记忆。

        ``source_event_id`` 与角色、类型组成唯一键；接口重试不会产生重复
        记忆。记忆只是事件日志的检索投影，不参与权威状态回放。
        """

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
        characters = list(dict.fromkeys(cid.strip() for cid in character_ids if cid.strip()))
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

        search_text = " ".join(_memory_search_terms(cleaned_content))
        evidence_json = json.dumps(
            cleaned_evidence,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        now = self._now()
        memory_ids: List[str] = []
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                for character_id in characters:
                    memory_id = secrets.token_hex(12)
                    conn.execute(
                        """
                        INSERT INTO character_memories (
                            memory_id, session_id, character_id,
                            source_event_id, world_version, memory_type,
                            content, search_text, importance,
                            evidence_event_ids_json, claim_fact_id,
                            claim_belief, claim_confidence, semantic_score,
                            created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (
                            session_id, character_id,
                            source_event_id, memory_type
                        ) DO UPDATE SET
                            world_version = excluded.world_version,
                            content = excluded.content,
                            search_text = excluded.search_text,
                            importance = excluded.importance,
                            evidence_event_ids_json =
                                excluded.evidence_event_ids_json,
                            claim_fact_id = excluded.claim_fact_id,
                            claim_belief = excluded.claim_belief,
                            claim_confidence = excluded.claim_confidence,
                            semantic_score = excluded.semantic_score
                        """,
                        (
                            memory_id,
                            session_id,
                            character_id,
                            cleaned_source,
                            int(world_version),
                            cleaned_type,
                            cleaned_content,
                            search_text,
                            float(importance),
                            evidence_json,
                            cleaned_fact_id,
                            cleaned_claim_belief,
                            float(claim_confidence),
                            float(semantic_score),
                            now,
                        ),
                    )
                    row = conn.execute(
                        """
                        SELECT memory_id
                        FROM character_memories
                        WHERE session_id = ?
                          AND character_id = ?
                          AND source_event_id = ?
                          AND memory_type = ?
                        """,
                        (
                            session_id,
                            character_id,
                            cleaned_source,
                            cleaned_type,
                        ),
                    ).fetchone()
                    memory_ids.append(str(row["memory_id"]))
        except sqlite3.IntegrityError as exc:
            raise PersistenceError(f"记录角色记忆失败: {exc}") from exc
        except sqlite3.Error as exc:
            raise PersistenceError(f"记录角色记忆失败: {exc}") from exc
        return memory_ids

    def search_character_memories(
        self,
        session_id: str,
        character_id: str,
        query: str,
        *,
        limit: int = 4,
    ) -> List[MemoryRecord]:
        """按角色作用域检索长期记忆，并综合相关性、重要度与新近性排序。"""

        if limit < 1 or limit > 20:
            raise PersistenceError("记忆检索 limit 必须在 1 到 20 之间")
        terms = _memory_search_terms(query)
        try:
            with self._connect() as conn:
                if self._fts5_enabled and terms:
                    match_query = " OR ".join(
                        '"{}"'.format(term.replace('"', '""')) for term in terms
                    )
                    rows = conn.execute(
                        """
                        SELECT m.*, bm25(
                            character_memories_fts, 1.0, 2.0
                        ) AS lexical_rank
                        FROM character_memories_fts
                        JOIN character_memories AS m
                          ON m.rowid = character_memories_fts.rowid
                        WHERE character_memories_fts MATCH ?
                          AND m.session_id = ?
                          AND m.character_id = ?
                        ORDER BY lexical_rank
                        LIMIT ?
                        """,
                        (match_query, session_id, character_id, limit * 4),
                    ).fetchall()
                elif terms:
                    like_terms = [f"%{term}%" for term in terms[:12]]
                    clauses = " OR ".join("search_text LIKE ?" for _ in like_terms)
                    rows = conn.execute(
                        f"""
                        SELECT *, 0.0 AS lexical_rank
                        FROM character_memories
                        WHERE session_id = ?
                          AND character_id = ?
                          AND ({clauses})
                        ORDER BY importance DESC, created_at DESC
                        LIMIT ?
                        """,
                        (session_id, character_id, *like_terms, limit * 4),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT *, 0.0 AS lexical_rank
                        FROM character_memories
                        WHERE session_id = ? AND character_id = ?
                        ORDER BY importance DESC, created_at DESC
                        LIMIT ?
                        """,
                        (session_id, character_id, limit * 4),
                    ).fetchall()
        except sqlite3.Error as exc:
            raise PersistenceError(f"检索角色记忆失败: {exc}") from exc

        now = datetime.now(timezone.utc)
        ranked = []
        for position, row in enumerate(rows):
            try:
                created = datetime.fromisoformat(str(row["created_at"]))
                age_days = max(0.0, (now - created).total_seconds() / 86400.0)
            except (TypeError, ValueError):
                age_days = 365.0
            relevance = 1.0 / (position + 1) if terms else 0.5
            recency = math.exp(-age_days / 30.0)
            score = (
                0.65 * relevance
                + 0.25 * float(row["importance"])
                + 0.10 * recency
            )
            ranked.append(self._memory_from_row(row, score))
        ranked.sort(
            key=lambda item: (
                item.retrieval_score,
                item.importance,
                item.created_at,
            ),
            reverse=True,
        )
        return ranked[:limit]

    @staticmethod
    def _memory_from_row(
        row: sqlite3.Row,
        retrieval_score: float = 0.0,
    ) -> MemoryRecord:
        try:
            evidence = tuple(
                str(item)
                for item in json.loads(
                    str(row["evidence_event_ids_json"] or "[]")
                )
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            evidence = ()
        return MemoryRecord(
            memory_id=str(row["memory_id"]),
            session_id=str(row["session_id"]),
            character_id=str(row["character_id"]),
            source_event_id=str(row["source_event_id"]),
            world_version=int(row["world_version"]),
            memory_type=str(row["memory_type"]),
            content=str(row["content"]),
            importance=float(row["importance"]),
            created_at=str(row["created_at"]),
            retrieval_score=retrieval_score,
            evidence_event_ids=evidence,
            claim_fact_id=str(row["claim_fact_id"] or ""),
            claim_belief=str(row["claim_belief"] or ""),
            claim_confidence=float(row["claim_confidence"] or 0.0),
            semantic_score=float(row["semantic_score"] or 0.0),
        )

    def get_character_memories(
        self,
        memory_ids: List[str],
    ) -> List[MemoryRecord]:
        """按稳定 ID 批量读取记忆，供派生向量索引回查权威记录。"""

        ids = list(dict.fromkeys(item.strip() for item in memory_ids if item.strip()))
        if not ids:
            return []
        placeholders = ", ".join("?" for _ in ids)
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM character_memories
                    WHERE memory_id IN ({placeholders})
                    """,
                    ids,
                ).fetchall()
        except sqlite3.Error as exc:
            raise PersistenceError(f"读取角色记忆失败: {exc}") from exc
        by_id = {
            str(row["memory_id"]): self._memory_from_row(row)
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
        """列出指定世界线的权威记忆，用于索引校准与重建。"""

        clauses = ["session_id = ?"]
        params: List[Any] = [session_id]
        if character_id is not None:
            clauses.append("character_id = ?")
            params.append(character_id)
        if memory_type is not None:
            clauses.append("memory_type = ?")
            params.append(memory_type)
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM character_memories
                    WHERE {' AND '.join(clauses)}
                    ORDER BY world_version, created_at, memory_id
                    """,
                    params,
                ).fetchall()
        except sqlite3.Error as exc:
            raise PersistenceError(f"列出角色记忆失败: {exc}") from exc
        return [self._memory_from_row(row) for row in rows]

    def delete_character_memories(
        self,
        session_id: str,
        *,
        memory_type: Optional[str] = None,
    ) -> int:
        """删除一条世界线的派生记忆，供索引重建使用。"""

        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                if memory_type is None:
                    deleted = conn.execute(
                        """
                        DELETE FROM character_memories
                        WHERE session_id = ?
                        """,
                        (session_id,),
                    )
                else:
                    deleted = conn.execute(
                        """
                        DELETE FROM character_memories
                        WHERE session_id = ? AND memory_type = ?
                        """,
                        (session_id, memory_type),
                    )
            return deleted.rowcount
        except sqlite3.Error as exc:
            raise PersistenceError(f"删除角色记忆失败: {exc}") from exc

    def prune_character_memories(
        self,
        session_id: str,
        character_id: str,
        *,
        memory_type: str = "episodic",
        max_records: int = 500,
    ) -> int:
        """只保留高重要度且较新的指定类型记忆。"""

        if max_records < 1:
            raise PersistenceError("记忆容量上限必须大于 0")
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    """
                    SELECT memory_id
                    FROM character_memories
                    WHERE session_id = ?
                      AND character_id = ?
                      AND memory_type = ?
                    ORDER BY importance DESC, created_at DESC
                    LIMIT -1 OFFSET ?
                    """,
                    (
                        session_id,
                        character_id,
                        memory_type,
                        max_records,
                    ),
                ).fetchall()
                memory_ids = [str(row["memory_id"]) for row in rows]
                if not memory_ids:
                    return 0
                placeholders = ", ".join("?" for _ in memory_ids)
                deleted = conn.execute(
                    f"""
                    DELETE FROM character_memories
                    WHERE memory_id IN ({placeholders})
                    """,
                    memory_ids,
                )
            return deleted.rowcount
        except sqlite3.Error as exc:
            raise PersistenceError(f"裁剪角色记忆失败: {exc}") from exc

    @staticmethod
    def _next_turn_sequence(conn: sqlite3.Connection, session_id: str) -> int:
        row = conn.execute(
            """
            SELECT COALESCE(MAX(turn_sequence), 0) + 1 AS next_sequence
            FROM world_turns
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        return int(row["next_sequence"])

    def _insert_turn(
        self,
        conn: sqlite3.Connection,
        session_id: str,
        *,
        world_version: int,
        player_input: str,
        turn_payload: Dict[str, Any],
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO world_turns (
                session_id, turn_sequence, world_version,
                player_input, result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                self._next_turn_sequence(conn, session_id),
                world_version,
                player_input,
                json.dumps(turn_payload, ensure_ascii=False, default=str),
                created_at,
            ),
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
        """原子写入一个回合的新状态和事件。

        expected_version 必须仍等于数据库中的版本。两个并发请求若都从 v0
        开始推演，只有第一个能提交为 v1，第二个会收到 VersionConflict。
        """

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

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state_version FROM world_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise SessionNotFound(f"会话不存在: {session_id}")
            current_version = int(row["state_version"])
            if current_version != expected_version:
                raise VersionConflict(
                    f"世界版本冲突: expected {expected_version}, "
                    f"got {current_version}"
                )

            now = self._now()
            conn.execute(
                """
                INSERT INTO world_events (
                    session_id, event_id, previous_version, new_version,
                    event_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    event.event_id,
                    event.previous_version,
                    event.new_version,
                    event.json(ensure_ascii=False),
                    now,
                ),
            )
            updated = conn.execute(
                """
                UPDATE world_sessions
                SET state_json = ?, state_version = ?, updated_at = ?
                WHERE session_id = ? AND state_version = ?
                """,
                (
                    new_state.json(ensure_ascii=False),
                    new_state.version,
                    now,
                    session_id,
                    expected_version,
                ),
            )
            if updated.rowcount != 1:
                raise VersionConflict(
                    f"提交期间世界版本发生变化: expected {expected_version}"
                )
            if turn_payload is not None:
                self._insert_turn(
                    conn,
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
        except sqlite3.Error as exc:
            conn.rollback()
            raise PersistenceError(f"SQLite 提交失败: {exc}") from exc
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
        """保存未产生 WorldEvent 的交互，如规则拒绝或解析失败。"""

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state_version FROM world_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
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
                conn,
                session_id,
                world_version=current_version,
                player_input=player_input,
                turn_payload=turn_payload,
                created_at=now,
            )
            conn.execute(
                """
                UPDATE world_sessions
                SET updated_at = ?
                WHERE session_id = ?
                """,
                (now, session_id),
            )
            conn.commit()
        except (SessionNotFound, VersionConflict):
            conn.rollback()
            raise
        except sqlite3.Error as exc:
            conn.rollback()
            raise PersistenceError(f"SQLite 保存回合失败: {exc}") from exc
        finally:
            conn.close()

    def list_events(self, session_id: str) -> List[WorldEvent]:
        """按版本顺序读取会话的全部事件。"""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_json
                FROM world_events
                WHERE session_id = ?
                ORDER BY new_version ASC
                """,
                (session_id,),
            ).fetchall()
        try:
            return [WorldEvent.parse_raw(row["event_json"]) for row in rows]
        except Exception as exc:
            raise PersistenceError(f"事件日志损坏: {session_id}") from exc

    def list_turns(self, session_id: str) -> List[TurnRecord]:
        """按交互顺序读取玩家输入与对应回合结果。"""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT session_id, turn_sequence, world_version,
                       player_input, result_json, created_at
                FROM world_turns
                WHERE session_id = ?
                ORDER BY turn_sequence ASC
                """,
                (session_id,),
            ).fetchall()
        try:
            return [
                TurnRecord(
                    session_id=row["session_id"],
                    turn_sequence=row["turn_sequence"],
                    world_version=row["world_version"],
                    player_input=row["player_input"],
                    result=json.loads(row["result_json"]),
                    created_at=row["created_at"],
                )
                for row in rows
            ]
        except Exception as exc:
            raise PersistenceError(f"回合历史损坏: {session_id}") from exc

    @staticmethod
    def _lineage_from_row(row: sqlite3.Row) -> SessionLineage:
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
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _settlement_from_row(row: sqlite3.Row) -> SettlementReceipt:
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
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _manifest_from_row(row: sqlite3.Row) -> CarryoverManifest:
        return CarryoverManifest(
            manifest_id=str(row["manifest_id"]),
            campaign_id=str(row["campaign_id"]),
            parent_session_id=str(row["parent_session_id"]),
            child_session_id=str(row["child_session_id"]),
            target_world_package_id=str(row["target_world_package_id"]),
            source_settlement_id=str(row["source_settlement_id"]),
            payload=json.loads(str(row["payload_json"])),
            created_at=str(row["created_at"]),
        )

    def _ensure_session_lineage_tx(
        self,
        conn: sqlite3.Connection,
        session_id: str,
    ) -> SessionLineage:
        row = conn.execute(
            "SELECT * FROM session_lineage WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is not None:
            return self._lineage_from_row(row)
        session = conn.execute(
            """
            SELECT session_id, world_package_id, created_at
            FROM world_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if session is None:
            raise SessionNotFound(f"会话不存在: {session_id}")
        campaign_id = f"campaign_{session_id}"
        created_at = str(session["created_at"])
        conn.execute(
            """
            INSERT INTO progression_campaigns (
                campaign_id, root_session_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT (root_session_id) DO NOTHING
            """,
            (campaign_id, session_id, created_at, created_at),
        )
        campaign = conn.execute(
            """
            SELECT campaign_id
            FROM progression_campaigns
            WHERE root_session_id = ?
            """,
            (session_id,),
        ).fetchone()
        campaign_id = str(campaign["campaign_id"])
        conn.execute(
            """
            INSERT INTO session_lineage (
                session_id, campaign_id, root_session_id,
                parent_session_id, depth, source_settlement_id,
                target_world_package_id, created_at
            ) VALUES (?, ?, ?, NULL, 0, NULL, ?, ?)
            """,
            (
                session_id,
                campaign_id,
                session_id,
                str(session["world_package_id"]),
                created_at,
            ),
        )
        row = conn.execute(
            "SELECT * FROM session_lineage WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return self._lineage_from_row(row)

    def ensure_session_lineage(self, session_id: str) -> SessionLineage:
        """Backfill a legacy session as a self-rooted one-session campaign."""

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            lineage = self._ensure_session_lineage_tx(conn, session_id)
            conn.commit()
            return lineage
        except SessionNotFound:
            conn.rollback()
            raise
        except sqlite3.Error as exc:
            conn.rollback()
            raise PersistenceError(f"创建世界线谱系失败: {exc}") from exc
        finally:
            conn.close()

    def get_session_lineage(
        self,
        session_id: str,
    ) -> Optional[SessionLineage]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM session_lineage WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return self._lineage_from_row(row) if row is not None else None

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
        """Atomically project an already committed settlement event.

        The settlement state/event commit remains the authority and must happen
        first. This method verifies that event and writes campaign, receipt,
        reward-ledger, and unlock rows in one transaction. It is safe to retry
        after a crash with the same idempotency key.
        """

        key = (idempotency_key or "").strip()
        if not key:
            raise PersistenceError("结算幂等键不能为空")
        if reward_points < 0:
            raise PersistenceError("结算奖励不能为负数")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT * FROM progression_settlements
                WHERE idempotency_key = ? OR session_id = ?
                """,
                (key, session_id),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["session_id"]) != session_id
                    or str(existing["settlement_event_id"])
                    != settlement_event_id
                ):
                    raise PersistenceError("结算幂等键或会话已关联其他结算")
                conn.commit()
                return self._settlement_from_row(existing)
            session = conn.execute(
                """
                SELECT world_package_id, state_version
                FROM world_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if session is None:
                raise SessionNotFound(f"会话不存在: {session_id}")
            event = conn.execute(
                """
                SELECT new_version
                FROM world_events
                WHERE session_id = ? AND event_id = ?
                """,
                (session_id, settlement_event_id),
            ).fetchone()
            if event is None:
                raise PersistenceError("结算事件尚未提交，不能记录章节进度")
            if int(event["new_version"]) != int(settled_world_version):
                raise PersistenceError("结算事件版本与回执版本不一致")
            if int(session["state_version"]) < int(settled_world_version):
                raise PersistenceError("权威世界状态尚未到达结算版本")
            lineage = self._ensure_session_lineage_tx(conn, session_id)
            now = self._now()
            settlement_id = f"settlement_{secrets.token_hex(12)}"
            conn.execute(
                """
                INSERT INTO progression_settlements (
                    settlement_id, campaign_id, session_id,
                    world_package_id, settlement_event_id,
                    settled_world_version, ending_id, ending_title,
                    summary, reward_points, idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            conn.execute(
                """
                INSERT INTO progression_reward_ledger (
                    ledger_id, campaign_id, settlement_id, session_id,
                    points_delta, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
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
                conn.execute(
                    """
                    INSERT INTO progression_unlocks (
                        unlock_id, campaign_id, source_settlement_id,
                        source_session_id, unlock_key, unlock_type,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (campaign_id, unlock_key) DO NOTHING
                    """,
                    (
                        f"unlock_{secrets.token_hex(12)}",
                        lineage.campaign_id,
                        settlement_id,
                        session_id,
                        unlock_key,
                        grant.unlock_type,
                        json.dumps(grant.payload, ensure_ascii=False, default=str),
                        now,
                    ),
                )
            conn.execute(
                """
                UPDATE progression_campaigns
                SET updated_at = ?
                WHERE campaign_id = ?
                """,
                (now, lineage.campaign_id),
            )
            row = conn.execute(
                """
                SELECT * FROM progression_settlements
                WHERE settlement_id = ?
                """,
                (settlement_id,),
            ).fetchone()
            conn.commit()
            return self._settlement_from_row(row)
        except (SessionNotFound, PersistenceError):
            conn.rollback()
            raise
        except sqlite3.Error as exc:
            conn.rollback()
            raise PersistenceError(f"记录结算进度失败: {exc}") from exc
        finally:
            conn.close()

    def _transition_result_tx(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        created: bool,
    ) -> TransitionResult:
        lineage_row = conn.execute(
            "SELECT * FROM session_lineage WHERE session_id = ?",
            (row["child_session_id"],),
        ).fetchone()
        manifest_row = conn.execute(
            "SELECT * FROM progression_manifests WHERE manifest_id = ?",
            (row["manifest_id"],),
        ).fetchone()
        return TransitionResult(
            transition_id=str(row["transition_id"]),
            idempotency_key=str(row["idempotency_key"]),
            parent_session_id=str(row["parent_session_id"]),
            target_world_package_id=str(row["target_world_package_id"]),
            child_session_id=str(row["child_session_id"]),
            settlement_id=str(row["settlement_id"]),
            lineage=self._lineage_from_row(lineage_row),
            manifest=self._manifest_from_row(manifest_row),
            created=created,
            created_at=str(row["created_at"]),
        )

    def create_or_get_child_session(
        self,
        request: TransitionRequest,
    ) -> TransitionResult:
        """Create exactly one clean child session for a parent/target pair.

        Only the prepared child snapshot and its genesis event are inserted;
        parent events, turns, and memories are never copied.
        """

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
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT * FROM progression_transitions
                WHERE idempotency_key = ?
                   OR (parent_session_id = ? AND target_world_package_id = ?)
                """,
                (key, request.parent_session_id, target),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["parent_session_id"])
                    != request.parent_session_id
                    or str(existing["target_world_package_id"]) != target
                ):
                    raise PersistenceError("转场幂等键已用于其他父会话或目标世界")
                result = self._transition_result_tx(
                    conn, existing, created=False
                )
                conn.commit()
                return result
            parent_lineage = self._ensure_session_lineage_tx(
                conn, request.parent_session_id
            )
            settlement = conn.execute(
                """
                SELECT * FROM progression_settlements
                WHERE session_id = ?
                """,
                (request.parent_session_id,),
            ).fetchone()
            if settlement is None:
                raise PersistenceError("父会话尚未记录权威结算，不能创建子世界")
            attempts = 1 if request.child_session_id else 5
            child_session_id = request.child_session_id
            now = self._now()
            for _ in range(attempts):
                child_session_id = child_session_id or secrets.token_hex(8)
                try:
                    conn.execute(
                        """
                        INSERT INTO world_sessions (
                            session_id, save_name, world_package_id,
                            default_actor_id, state_json, state_version,
                            base_state_json, base_state_version,
                            created_at, updated_at, book_id, entry_id,
                            chapter_number, entry_revision
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            child_session_id,
                            self._validate_save_name(request.save_name),
                            target,
                            request.default_actor_id,
                            state.json(ensure_ascii=False),
                            state.version,
                            state.json(ensure_ascii=False),
                            state.version,
                            now,
                            now,
                            request.target_book_id,
                            request.target_entry_id,
                            int(request.target_chapter_number),
                            int(request.target_entry_revision),
                        ),
                    )
                    break
                except sqlite3.IntegrityError:
                    if request.child_session_id:
                        raise PersistenceError(
                            f"子会话已存在: {child_session_id}"
                        )
                    child_session_id = None
            if child_session_id is None:
                raise PersistenceError("生成唯一子会话 ID 失败")
            conn.execute(
                """
                INSERT INTO world_events (
                    session_id, event_id, previous_version, new_version,
                    event_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    child_session_id,
                    event.event_id,
                    event.previous_version,
                    event.new_version,
                    event.json(ensure_ascii=False),
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO session_lineage (
                    session_id, campaign_id, root_session_id,
                    parent_session_id, depth, source_settlement_id,
                    target_world_package_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            conn.execute(
                """
                INSERT INTO progression_manifests (
                    manifest_id, campaign_id, parent_session_id,
                    child_session_id, target_world_package_id,
                    source_settlement_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manifest_id,
                    parent_lineage.campaign_id,
                    request.parent_session_id,
                    child_session_id,
                    target,
                    str(settlement["settlement_id"]),
                    json.dumps(request.manifest, ensure_ascii=False, default=str),
                    now,
                ),
            )
            transition_id = f"transition_{secrets.token_hex(12)}"
            conn.execute(
                """
                INSERT INTO progression_transitions (
                    transition_id, idempotency_key, campaign_id,
                    parent_session_id, target_world_package_id,
                    child_session_id, settlement_id, manifest_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            conn.execute(
                """
                UPDATE progression_campaigns
                SET updated_at = ?
                WHERE campaign_id = ?
                """,
                (now, parent_lineage.campaign_id),
            )
            row = conn.execute(
                """
                SELECT * FROM progression_transitions
                WHERE transition_id = ?
                """,
                (transition_id,),
            ).fetchone()
            result = self._transition_result_tx(conn, row, created=True)
            conn.commit()
            return result
        except (SessionNotFound, PersistenceError):
            conn.rollback()
            raise
        except sqlite3.Error as exc:
            conn.rollback()
            raise PersistenceError(f"创建子世界失败: {exc}") from exc
        finally:
            conn.close()

    def list_campaign_progression(
        self,
        campaign_id: str,
    ) -> CampaignProgression:
        with self._connect() as conn:
            campaign = conn.execute(
                "SELECT * FROM progression_campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            if campaign is None:
                raise PersistenceError(f"章节旅程不存在: {campaign_id}")
            lineage_rows = conn.execute(
                """
                SELECT * FROM session_lineage
                WHERE campaign_id = ? ORDER BY depth, created_at, session_id
                """,
                (campaign_id,),
            ).fetchall()
            settlement_rows = conn.execute(
                """
                SELECT * FROM progression_settlements
                WHERE campaign_id = ? ORDER BY created_at, settlement_id
                """,
                (campaign_id,),
            ).fetchall()
            reward_rows = conn.execute(
                """
                SELECT * FROM progression_reward_ledger
                WHERE campaign_id = ? ORDER BY created_at, ledger_id
                """,
                (campaign_id,),
            ).fetchall()
            unlock_rows = conn.execute(
                """
                SELECT * FROM progression_unlocks
                WHERE campaign_id = ? ORDER BY created_at, unlock_id
                """,
                (campaign_id,),
            ).fetchall()
            manifest_rows = conn.execute(
                """
                SELECT * FROM progression_manifests
                WHERE campaign_id = ? ORDER BY created_at, manifest_id
                """,
                (campaign_id,),
            ).fetchall()
            transition_rows = conn.execute(
                """
                SELECT * FROM progression_transitions
                WHERE campaign_id = ? ORDER BY created_at, transition_id
                """,
                (campaign_id,),
            ).fetchall()
            transitions = tuple(
                self._transition_result_tx(conn, row, created=False)
                for row in transition_rows
            )
        return CampaignProgression(
            campaign=CampaignRecord(
                campaign_id=str(campaign["campaign_id"]),
                root_session_id=str(campaign["root_session_id"]),
                created_at=str(campaign["created_at"]),
                updated_at=str(campaign["updated_at"]),
            ),
            lineage=tuple(
                self._lineage_from_row(row) for row in lineage_rows
            ),
            settlements=tuple(
                self._settlement_from_row(row) for row in settlement_rows
            ),
            rewards=tuple(
                RewardLedgerRecord(
                    ledger_id=str(row["ledger_id"]),
                    campaign_id=str(row["campaign_id"]),
                    settlement_id=str(row["settlement_id"]),
                    session_id=str(row["session_id"]),
                    points_delta=int(row["points_delta"]),
                    reason=str(row["reason"]),
                    created_at=str(row["created_at"]),
                )
                for row in reward_rows
            ),
            unlocks=tuple(
                UnlockRecord(
                    unlock_id=str(row["unlock_id"]),
                    campaign_id=str(row["campaign_id"]),
                    source_settlement_id=str(row["source_settlement_id"]),
                    source_session_id=str(row["source_session_id"]),
                    unlock_key=str(row["unlock_key"]),
                    unlock_type=str(row["unlock_type"]),
                    payload=json.loads(str(row["payload_json"])),
                    created_at=str(row["created_at"]),
                )
                for row in unlock_rows
            ),
            manifests=tuple(
                self._manifest_from_row(row) for row in manifest_rows
            ),
            transitions=transitions,
        )

    @staticmethod
    def _manuscript_from_row(row: sqlite3.Row) -> WorldlineManuscript:
        return WorldlineManuscript(
            manuscript_id=str(row["manuscript_id"]),
            timeline_id=str(row["timeline_id"]),
            campaign_id=str(row["campaign_id"]),
            root_session_id=str(row["root_session_id"]),
            title=str(row["title"]),
            status=str(row["status"]),
            current_revision=int(row["current_revision"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _passage_fingerprint(
        session_id: str,
        source_event_ids: Sequence[str],
    ) -> str:
        raw = session_id + "\n" + "\n".join(source_event_ids)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _hydrate_manuscript_passage_tx(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> ManuscriptPassage:
        revision_number = int(row["current_revision"])
        content: Optional[ManuscriptPassage] = None
        if revision_number > 0:
            revision_row = conn.execute(
                """
                SELECT revision_json
                FROM manuscript_passage_revisions
                WHERE passage_id = ? AND revision_number = ?
                """,
                (str(row["passage_id"]), revision_number),
            ).fetchone()
            if revision_row is not None:
                revision = ManuscriptRevision.parse_raw(
                    str(revision_row["revision_json"])
                )
                if revision.passages:
                    content = revision.passages[0]
        base = content or ManuscriptPassage(
            passage_id=str(row["passage_id"]),
            source_event_ids=json.loads(str(row["source_event_ids_json"])),
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
                "source_event_ids": json.loads(
                    str(row["source_event_ids_json"])
                ),
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
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            },
            deep=True,
        )

    def ensure_manuscript(self, session_id: str) -> WorldlineManuscript:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            lineage = self._ensure_session_lineage_tx(conn, session_id)
            row = conn.execute(
                """
                SELECT * FROM worldline_manuscripts WHERE campaign_id = ?
                """,
                (lineage.campaign_id,),
            ).fetchone()
            if row is None:
                state_row = conn.execute(
                    """
                    SELECT state_json, save_name FROM world_sessions
                    WHERE session_id = ?
                    """,
                    (session_id,),
                ).fetchone()
                if state_row is None:
                    raise SessionNotFound(f"会话不存在: {session_id}")
                state = WorldState.parse_raw(str(state_row["state_json"]))
                now = self._now()
                manuscript_id = f"manuscript_{secrets.token_hex(12)}"
                conn.execute(
                    """
                    INSERT INTO worldline_manuscripts (
                        manuscript_id, campaign_id, root_session_id,
                        timeline_id, title, status, current_revision,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'active', 0, ?, ?)
                    """,
                    (
                        manuscript_id,
                        lineage.campaign_id,
                        lineage.root_session_id,
                        state.timeline_id,
                        str(state_row["save_name"]),
                        now,
                        now,
                    ),
                )
                row = conn.execute(
                    """
                    SELECT * FROM worldline_manuscripts
                    WHERE manuscript_id = ?
                    """,
                    (manuscript_id,),
                ).fetchone()
            result = self._manuscript_from_row(row)
            conn.commit()
            return result
        except (SessionNotFound, PersistenceError):
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise PersistenceError(f"创建世界线稿件失败: {exc}") from exc
        finally:
            conn.close()

    def get_manuscript_for_session(
        self,
        session_id: str,
    ) -> Optional[WorldlineManuscript]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT manuscript.*
                FROM worldline_manuscripts AS manuscript
                JOIN session_lineage AS lineage
                  ON lineage.campaign_id = manuscript.campaign_id
                WHERE lineage.session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return self._manuscript_from_row(row) if row is not None else None

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
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            lineage = self._ensure_session_lineage_tx(conn, session_id)
            manuscript_row = conn.execute(
                "SELECT * FROM worldline_manuscripts WHERE campaign_id = ?",
                (lineage.campaign_id,),
            ).fetchone()
            if manuscript_row is None:
                conn.rollback()
                self.ensure_manuscript(session_id)
                conn.execute("BEGIN IMMEDIATE")
                manuscript_row = conn.execute(
                    "SELECT * FROM worldline_manuscripts WHERE campaign_id = ?",
                    (lineage.campaign_id,),
                ).fetchone()
            placeholders = ",".join("?" for _ in source_ids)
            rows = conn.execute(
                f"""
                SELECT event_id, previous_version, new_version
                FROM world_events
                WHERE session_id = ? AND event_id IN ({placeholders})
                ORDER BY new_version
                """,
                (session_id, *source_ids),
            ).fetchall()
            if [str(row["event_id"]) for row in rows] != source_ids:
                raise PersistenceError("稿件来源事件不存在或顺序不一致")
            for previous, current in zip(rows, rows[1:]):
                if int(current["previous_version"]) != int(previous["new_version"]):
                    raise PersistenceError("稿件来源事件版本不连续")
            fingerprint = self._passage_fingerprint(session_id, source_ids)
            existing = conn.execute(
                """
                SELECT * FROM manuscript_passages
                WHERE manuscript_id = ? AND source_fingerprint = ?
                """,
                (str(manuscript_row["manuscript_id"]), fingerprint),
            ).fetchone()
            if existing is not None:
                result = self._hydrate_manuscript_passage_tx(conn, existing)
                conn.commit()
                return result
            session = conn.execute(
                """
                SELECT chapter_number, entry_id, entry_revision
                FROM world_sessions WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            sequence_row = conn.execute(
                """
                SELECT COALESCE(MAX(manuscript_sequence), 0) + 1 AS next_sequence
                FROM manuscript_passages WHERE manuscript_id = ?
                """,
                (str(manuscript_row["manuscript_id"]),),
            ).fetchone()
            now = self._now()
            passage_id = f"passage_{secrets.token_hex(12)}"
            conn.execute(
                """
                INSERT INTO manuscript_passages (
                    passage_id, manuscript_id, session_id, chapter_number,
                    entry_id, entry_revision, manuscript_sequence, title,
                    source_event_ids_json, source_fingerprint,
                    from_world_version, to_world_version, generation_kind,
                    generation_status, current_revision, last_error,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, 'pending', 0, '', ?, ?)
                """,
                (
                    passage_id,
                    str(manuscript_row["manuscript_id"]),
                    session_id,
                    int(session["chapter_number"]),
                    str(session["entry_id"]),
                    int(session["entry_revision"]),
                    int(sequence_row["next_sequence"]),
                    json.dumps(source_ids, ensure_ascii=False),
                    fingerprint,
                    int(rows[0]["new_version"]),
                    int(rows[-1]["new_version"]),
                    kind.value,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM manuscript_passages WHERE passage_id = ?",
                (passage_id,),
            ).fetchone()
            result = self._hydrate_manuscript_passage_tx(conn, row)
            conn.commit()
            return result
        except (SessionNotFound, PersistenceError):
            conn.rollback()
            raise
        except sqlite3.Error as exc:
            conn.rollback()
            raise PersistenceError(f"预留稿件段落失败: {exc}") from exc
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
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM manuscript_passages WHERE passage_id = ?",
                (passage_id,),
            ).fetchone()
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
            source_ids = json.loads(str(row["source_event_ids_json"]))
            if revision.source_event_ids != source_ids:
                raise PersistenceError("稿件修订的来源事件与预留记录不一致")
            next_revision = current_revision + 1
            parent_id = None
            if int(row["current_revision"]) > 0:
                previous = conn.execute(
                    """
                    SELECT revision_json FROM manuscript_passage_revisions
                    WHERE passage_id = ? AND revision_number = ?
                    """,
                    (passage_id, int(row["current_revision"])),
                ).fetchone()
                if previous is not None:
                    parent_id = ManuscriptRevision.parse_raw(
                        str(previous["revision_json"])
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
            conn.execute(
                """
                INSERT INTO manuscript_passage_revisions (
                    passage_id, revision_number, revision_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    passage_id,
                    next_revision,
                    stored_revision.json(ensure_ascii=False),
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE manuscript_passages
                SET generation_kind = ?, generation_status = 'ready',
                    current_revision = ?, last_error = '', updated_at = ?
                WHERE passage_id = ?
                """,
                (revision.source.value, next_revision, now, passage_id),
            )
            conn.execute(
                """
                UPDATE worldline_manuscripts
                SET current_revision = current_revision + 1, updated_at = ?
                WHERE manuscript_id = ?
                """,
                (now, str(row["manuscript_id"])),
            )
            updated = conn.execute(
                "SELECT * FROM manuscript_passages WHERE passage_id = ?",
                (passage_id,),
            ).fetchone()
            result = self._hydrate_manuscript_passage_tx(conn, updated)
            conn.commit()
            return result
        except PersistenceError:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise PersistenceError(f"完成稿件段落失败: {exc}") from exc
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
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM manuscript_passages WHERE passage_id = ?",
                (passage_id,),
            ).fetchone()
            if row is None:
                raise PersistenceError(f"稿件段落不存在: {passage_id}")
            status = "ready" if int(row["current_revision"]) > 0 else "failed"
            conn.execute(
                """
                UPDATE manuscript_passages
                SET generation_status = ?, last_error = ?, updated_at = ?
                WHERE passage_id = ?
                """,
                (status, message, self._now(), passage_id),
            )
            updated = conn.execute(
                "SELECT * FROM manuscript_passages WHERE passage_id = ?",
                (passage_id,),
            ).fetchone()
            result = self._hydrate_manuscript_passage_tx(conn, updated)
            conn.commit()
            return result
        except PersistenceError:
            conn.rollback()
            raise
        except sqlite3.Error as exc:
            conn.rollback()
            raise PersistenceError(f"标记稿件失败状态失败: {exc}") from exc
        finally:
            conn.close()

    def get_manuscript_passage(
        self,
        passage_id: str,
    ) -> Optional[ManuscriptPassage]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM manuscript_passages WHERE passage_id = ?",
                (passage_id,),
            ).fetchone()
            return (
                self._hydrate_manuscript_passage_tx(conn, row)
                if row is not None
                else None
            )

    def list_manuscript_passages(
        self,
        session_id: str,
    ) -> List[ManuscriptPassage]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM manuscript_passages
                WHERE session_id = ? ORDER BY manuscript_sequence
                """,
                (session_id,),
            ).fetchall()
            return [self._hydrate_manuscript_passage_tx(conn, row) for row in rows]

    def list_campaign_manuscript_passages(
        self,
        session_id: str,
    ) -> List[ManuscriptPassage]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT passage.*
                FROM manuscript_passages AS passage
                JOIN session_lineage AS requested_lineage
                  ON requested_lineage.session_id = ?
                JOIN session_lineage AS passage_lineage
                  ON passage_lineage.session_id = passage.session_id
                 AND passage_lineage.campaign_id = requested_lineage.campaign_id
                ORDER BY passage.manuscript_sequence
                """,
                (session_id,),
            ).fetchall()
            return [self._hydrate_manuscript_passage_tx(conn, row) for row in rows]

    def list_manuscript_passage_revisions(
        self,
        passage_id: str,
    ) -> List[ManuscriptRevision]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT revision_json FROM manuscript_passage_revisions
                WHERE passage_id = ? ORDER BY revision_number
                """,
                (passage_id,),
            ).fetchall()
        return [ManuscriptRevision.parse_raw(str(row["revision_json"])) for row in rows]

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
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM manuscript_passages WHERE passage_id = ?",
                (passage_id,),
            ).fetchone()
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
            revision_row = conn.execute(
                """
                SELECT revision_json
                FROM manuscript_passage_revisions
                WHERE passage_id = ? AND revision_number = ?
                """,
                (passage_id, target_revision),
            ).fetchone()
            if revision_row is None:
                raise PersistenceError(
                    f"稿件修订不存在: {passage_id}@{target_revision}"
                )
            revision = ManuscriptRevision.parse_raw(
                str(revision_row["revision_json"])
            )
            if not revision.passages:
                raise PersistenceError("稿件修订缺少正文段落")
            content = revision.passages[0]
            source_ids = json.loads(str(row["source_event_ids_json"]))
            if revision.source_event_ids != source_ids:
                raise PersistenceError("稿件修订来源事件与段落不一致")
            now = self._now()
            conn.execute(
                """
                UPDATE manuscript_passages
                SET title = ?, generation_kind = ?, generation_status = 'ready',
                    current_revision = ?, last_error = '', updated_at = ?
                WHERE passage_id = ?
                """,
                (
                    content.title,
                    revision.source.value,
                    target_revision,
                    now,
                    passage_id,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM manuscript_passages WHERE passage_id = ?",
                (passage_id,),
            ).fetchone()
            conn.commit()
            return self._hydrate_manuscript_passage_tx(conn, updated)
        except (ManuscriptRevisionConflict, PersistenceError):
            conn.rollback()
            raise
        except sqlite3.Error as exc:
            conn.rollback()
            raise PersistenceError(f"选择稿件修订失败: {exc}") from exc
        finally:
            conn.close()

    def export_session(self, session_id: str) -> Dict[str, Any]:
        """导出可移植的完整世界线备份。

        备份不复用原 session_id；再次导入时总会生成新的会话 ID，避免覆盖
        本机已有世界线。
        """

        metadata = self.get_metadata(session_id)
        state = self.get_state(session_id)
        if metadata is None or state is None:
            raise SessionNotFound(f"会话不存在: {session_id}")
        with self._connect() as conn:
            base_row = conn.execute(
                """
                SELECT base_state_json, base_state_version
                FROM world_sessions WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if base_row is None:
            raise SessionNotFound(f"会话不存在: {session_id}")
        try:
            base_state = WorldState.parse_raw(base_row["base_state_json"])
        except Exception as exc:
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
            "exported_at": self._now(),
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

    @staticmethod
    def _validate_backup(
        backup: Dict[str, Any],
    ) -> tuple:
        if not isinstance(backup, dict):
            raise PersistenceError("存档备份必须是 JSON 对象")
        if backup.get("format") != "ai-transmigration-save":
            raise PersistenceError("不是 AI 快穿系统存档")
        format_version = backup.get("format_version")
        if format_version not in {1, 2, 3}:
            raise PersistenceError(
                f"不支持的存档格式版本: {format_version}"
            )

        save = backup.get("save")
        if not isinstance(save, dict):
            raise PersistenceError("存档缺少 save 元数据")
        world_package_id = str(save.get("world_package_id") or "").strip()
        default_actor_id = str(save.get("default_actor_id") or "").strip()
        if not world_package_id or not default_actor_id:
            raise PersistenceError("存档缺少世界包或默认角色")

        try:
            state = WorldState.parse_obj(backup.get("state"))
        except Exception as exc:
            raise PersistenceError(f"存档世界状态无效: {exc}") from exc

        if format_version == 3:
            try:
                base_state = WorldState.parse_obj(backup.get("base_state"))
            except Exception as exc:
                raise PersistenceError(f"存档基线状态无效: {exc}") from exc
            base_state_version = backup.get("base_state_version")
            if (
                not isinstance(base_state_version, int)
                or base_state_version != base_state.version
                or base_state.version > state.version
            ):
                raise PersistenceError("存档基线状态版本无效")
        else:
            # 旧备份没有不变基线，只能从导入时的最新版本继续记录。
            base_state = state.copy(deep=True)

        raw_events = backup.get("events", [])
        if not isinstance(raw_events, list):
            raise PersistenceError("存档事件日志必须是数组")
        try:
            events = [WorldEvent.parse_obj(item) for item in raw_events]
        except Exception as exc:
            raise PersistenceError(f"存档事件日志无效: {exc}") from exc

        expected_version = 0
        event_ids = set()
        for event in events:
            if event.event_id in event_ids:
                raise PersistenceError(f"存档含重复事件 ID: {event.event_id}")
            event_ids.add(event.event_id)
            if (
                event.previous_version != expected_version
                or event.new_version != expected_version + 1
            ):
                raise PersistenceError(
                    "存档事件版本链断裂: "
                    f"expected {expected_version}->{expected_version + 1}, "
                    f"got {event.previous_version}->{event.new_version}"
                )
            expected_version = event.new_version
        if state.version != expected_version:
            raise PersistenceError(
                "存档状态版本与事件日志不一致: "
                f"state={state.version}, events={expected_version}"
            )
        if format_version == 3:
            try:
                rebuilt = replay_events(
                    base_state,
                    [
                        event
                        for event in events
                        if event.new_version > base_state.version
                    ],
                )
            except Exception as exc:
                raise PersistenceError(
                    f"存档基线无法重放到最新状态: {exc}"
                ) from exc
            if rebuilt.dict() != state.dict():
                raise PersistenceError("存档基线重放结果与最新状态不一致")

        raw_turns = backup.get("turns", [])
        if not isinstance(raw_turns, list):
            raise PersistenceError("存档剧情历史必须是数组")
        turns = []
        for index, item in enumerate(raw_turns, start=1):
            if not isinstance(item, dict):
                raise PersistenceError(f"第 {index} 条剧情历史无效")
            sequence = item.get("turn_sequence")
            world_version = item.get("world_version")
            player_input = item.get("player_input")
            result = item.get("result")
            if sequence != index:
                raise PersistenceError(
                    f"剧情历史序号不连续: expected {index}, got {sequence}"
                )
            if (
                not isinstance(world_version, int)
                or world_version < 0
                or world_version > state.version
            ):
                raise PersistenceError(f"第 {index} 条剧情历史版本无效")
            if not isinstance(player_input, str) or not isinstance(result, dict):
                raise PersistenceError(f"第 {index} 条剧情历史内容无效")
            turns.append(
                {
                    "turn_sequence": sequence,
                    "world_version": world_version,
                    "player_input": player_input,
                    "result": result,
                    "created_at": str(item.get("created_at") or ""),
                }
            )

        manuscript = None
        passages: List[ManuscriptPassage] = []
        revisions_by_passage: Dict[str, List[ManuscriptRevision]] = {}
        if format_version in {2, 3}:
            raw_manuscript = backup.get("manuscript")
            if raw_manuscript is not None:
                try:
                    manuscript = WorldlineManuscript.parse_obj(raw_manuscript)
                except Exception as exc:
                    raise PersistenceError(f"存档稿件元数据无效: {exc}") from exc
            raw_passages = backup.get("manuscript_passages", [])
            raw_revisions = backup.get("manuscript_passage_revisions", [])
            if not isinstance(raw_passages, list) or not isinstance(raw_revisions, list):
                raise PersistenceError("存档稿件段落与修订必须是数组")
            try:
                passages = [
                    ManuscriptPassage.parse_obj(item) for item in raw_passages
                ]
            except Exception as exc:
                raise PersistenceError(f"存档稿件段落无效: {exc}") from exc
            passage_ids = [passage.passage_id for passage in passages]
            if len(passage_ids) != len(set(passage_ids)):
                raise PersistenceError("存档稿件含重复 passage ID")
            event_by_id = {event.event_id: event for event in events}
            for passage in passages:
                if passage.session_id and passage.session_id != str(
                    backup.get("source_session_id") or ""
                ):
                    raise PersistenceError("存档稿件段落属于其他会话")
                if any(
                    event_id not in event_ids
                    for event_id in passage.source_event_ids
                ):
                    raise PersistenceError("存档稿件引用了不存在的事件")
                source_events = [
                    event_by_id[event_id]
                    for event_id in passage.source_event_ids
                ]
                if source_events and (
                    passage.from_world_version != source_events[0].new_version
                    or passage.to_world_version != source_events[-1].new_version
                ):
                    raise PersistenceError("存档稿件版本范围与来源事件不一致")
                for previous, current in zip(
                    source_events,
                    source_events[1:],
                ):
                    if current.previous_version != previous.new_version:
                        raise PersistenceError("存档稿件来源事件版本不连续")
            for item in raw_revisions:
                if not isinstance(item, dict):
                    raise PersistenceError("存档稿件修订记录无效")
                passage_id = str(item.get("passage_id") or "")
                if passage_id not in set(passage_ids):
                    raise PersistenceError("存档稿件修订引用了不存在的 passage")
                try:
                    revisions = [
                        ManuscriptRevision.parse_obj(revision)
                        for revision in item.get("revisions", [])
                    ]
                except Exception as exc:
                    raise PersistenceError(f"存档稿件修订无效: {exc}") from exc
                numbers = [revision.revision_number for revision in revisions]
                if numbers != list(range(1, len(numbers) + 1)):
                    raise PersistenceError("存档稿件修订序号不连续")
                passage = next(
                    passage
                    for passage in passages
                    if passage.passage_id == passage_id
                )
                if passage.current_revision != len(revisions):
                    raise PersistenceError("存档稿件当前修订号与历史不一致")
                if passage.generation_status == ManuscriptGenerationStatus.ready and not revisions:
                    raise PersistenceError("ready 稿件段落缺少修订历史")
                source_ids = next(
                    passage.source_event_ids
                    for passage in passages
                    if passage.passage_id == passage_id
                )
                if any(revision.source_event_ids != source_ids for revision in revisions):
                    raise PersistenceError("存档稿件修订来源事件不一致")
                revisions_by_passage[passage_id] = revisions
            if manuscript is None and passages:
                raise PersistenceError("存档稿件段落缺少 manuscript 元数据")
            if manuscript is not None and manuscript.current_revision != sum(
                len(revisions) for revisions in revisions_by_passage.values()
            ):
                raise PersistenceError("存档稿件总修订号与段落历史不一致")

        return (
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
        )

    def import_session(
        self,
        backup: Dict[str, Any],
        *,
        save_name: Optional[str] = None,
    ) -> str:
        """校验并原子导入完整世界线，返回新会话 ID。"""

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
        ) = self._validate_backup(backup)
        source_name = str(save.get("name") or "导入的世界线")
        imported_name = self._validate_save_name(
            save_name or f"{source_name[:76]}（导入）"
        )

        for _ in range(5):
            session_id = secrets.token_hex(8)
            now = self._now()
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    INSERT INTO world_sessions (
                            session_id, save_name, world_package_id, default_actor_id,
                            state_json, state_version,
                            base_state_json, base_state_version,
                            created_at, updated_at, book_id, entry_id,
                            chapter_number, entry_revision
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

                    """,
                    (
                        session_id,
                        imported_name,
                        world_package_id,
                        default_actor_id,
                        state.json(ensure_ascii=False),
                        state.version,
                        base_state.json(ensure_ascii=False),
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
                    conn.execute(
                        """
                        INSERT INTO world_events (
                            session_id, event_id, previous_version, new_version,
                            event_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session_id,
                            event.event_id,
                            event.previous_version,
                            event.new_version,
                            event.json(ensure_ascii=False),
                            now,
                        ),
                    )
                for turn in turns:
                    conn.execute(
                        """
                        INSERT INTO world_turns (
                            session_id, turn_sequence, world_version,
                            player_input, result_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session_id,
                            turn["turn_sequence"],
                            turn["world_version"],
                            turn["player_input"],
                            json.dumps(
                                turn["result"], ensure_ascii=False, default=str
                            ),
                            turn["created_at"] or now,
                        ),
                    )
                if manuscript is not None:
                    campaign_id = f"campaign_{session_id}"
                    manuscript_id = f"manuscript_{secrets.token_hex(12)}"
                    conn.execute(
                        """
                        INSERT INTO progression_campaigns (
                            campaign_id, root_session_id, created_at, updated_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (campaign_id, session_id, now, now),
                    )
                    conn.execute(
                        """
                        INSERT INTO session_lineage (
                            session_id, campaign_id, root_session_id,
                            parent_session_id, depth, source_settlement_id,
                            target_world_package_id, created_at
                        ) VALUES (?, ?, ?, NULL, 0, NULL, ?, ?)
                        """,
                        (
                            session_id,
                            campaign_id,
                            session_id,
                            world_package_id,
                            now,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO worldline_manuscripts (
                            manuscript_id, campaign_id, root_session_id,
                            timeline_id, title, status, current_revision,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            manuscript_id,
                            campaign_id,
                            session_id,
                            state.timeline_id,
                            manuscript.title or imported_name,
                            manuscript.status,
                            sum(
                                len(revisions_by_passage.get(passage.passage_id, []))
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
                        conn.execute(
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
                                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                            )
                            """,
                            (
                                new_passage_id,
                                manuscript_id,
                                session_id,
                                passage.chapter_number or int(
                                    save.get("chapter_number") or 0
                                ),
                                passage.entry_id or str(save.get("entry_id") or ""),
                                passage.entry_revision or int(
                                    save.get("entry_revision") or 0
                                ),
                                sequence,
                                passage.title,
                                json.dumps(
                                    passage.source_event_ids,
                                    ensure_ascii=False,
                                ),
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
                            conn.execute(
                                """
                                INSERT INTO manuscript_passage_revisions (
                                    passage_id, revision_number,
                                    revision_json, created_at
                                ) VALUES (?, ?, ?, ?)
                                """,
                                (
                                    new_passage_id,
                                    revision.revision_number,
                                    stored_revision.json(ensure_ascii=False),
                                    now,
                                ),
                            )
                            parent_revision_id = stored_revision.revision_id
                conn.commit()
                return session_id
            except sqlite3.IntegrityError:
                conn.rollback()
            except sqlite3.Error as exc:
                conn.rollback()
                raise PersistenceError(f"SQLite 导入失败: {exc}") from exc
            finally:
                conn.close()
        raise PersistenceError("生成唯一导入会话 ID 失败")
