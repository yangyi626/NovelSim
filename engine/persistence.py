"""世界会话与事件的 SQLite 持久化。

这一层只负责保存已经由 TurnPipeline 结算完成的 WorldState / WorldEvent，
不参与规则判断或状态推演。存储接口保持独立，后续迁移 PostgreSQL 时，
TurnPipeline 和世界模型无需改动。
"""

from __future__ import annotations

import json
import math
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from world_schema import WorldEvent, WorldState


class PersistenceError(RuntimeError):
    """持久化读写失败。"""


class SessionNotFound(PersistenceError):
    """会话不存在。"""


class VersionConflict(PersistenceError):
    """持久化时发现世界版本已被其他请求推进。"""


@dataclass(frozen=True)
class SessionMetadata:
    session_id: str
    save_name: str
    world_package_id: str
    default_actor_id: str
    state_version: int
    created_at: str
    updated_at: str


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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
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
                            state_json, state_version, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sid,
                            self._validate_save_name(save_name),
                            world_package_id,
                            default_actor_id,
                            state.json(ensure_ascii=False),
                            state.version,
                            now,
                            now,
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
                       state_version, created_at, updated_at
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
                       state_version, created_at, updated_at
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

    def export_session(self, session_id: str) -> Dict[str, Any]:
        """导出可移植的完整世界线备份。

        备份不复用原 session_id；再次导入时总会生成新的会话 ID，避免覆盖
        本机已有世界线。
        """

        metadata = self.get_metadata(session_id)
        state = self.get_state(session_id)
        if metadata is None or state is None:
            raise SessionNotFound(f"会话不存在: {session_id}")

        events = self.list_events(session_id)
        turns = self.list_turns(session_id)
        return {
            "format": "ai-transmigration-save",
            "format_version": 1,
            "exported_at": self._now(),
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

    @staticmethod
    def _validate_backup(
        backup: Dict[str, Any],
    ) -> tuple:
        if not isinstance(backup, dict):
            raise PersistenceError("存档备份必须是 JSON 对象")
        if backup.get("format") != "ai-transmigration-save":
            raise PersistenceError("不是 AI 快穿系统存档")
        if backup.get("format_version") != 1:
            raise PersistenceError(
                f"不支持的存档格式版本: {backup.get('format_version')}"
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

        return (
            save,
            state,
            events,
            turns,
            world_package_id,
            default_actor_id,
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
            events,
            turns,
            world_package_id,
            default_actor_id,
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
                        state_json, state_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        imported_name,
                        world_package_id,
                        default_actor_id,
                        state.json(ensure_ascii=False),
                        state.version,
                        now,
                        now,
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
