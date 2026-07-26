"""SQLite 编译任务、章节缓存与分层快照仓库。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


JOB_STATUSES = {
    "queued",
    "running",
    "paused",
    "cancelled",
    "failed",
    "completed",
}
ACTIVE_JOB_STATUSES = {"queued", "running", "paused"}


class CompilationJobError(RuntimeError):
    """编译任务仓库操作失败。"""


class CompilationJobNotFound(CompilationJobError):
    """编译任务不存在。"""


class CompilationJobConflict(CompilationJobError):
    """编译任务状态冲突。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class CompilationJob:
    job_id: str
    package_id: str
    novel_path: str
    novel_name: str
    chapters: List[int]
    timeline_plan: Dict[int, str]
    volume_plan: Dict[int, str]
    volume_size: int
    status: str
    total_chapters: int
    completed_chapters: int
    current_chapter: Optional[int]
    progress: float
    prompt_version: str
    model: str
    output_path: str
    result_package_id: str
    error: str
    pause_requested: bool
    cancel_requested: bool
    quality_status: str
    quality_score: Optional[float]
    quality_report: Dict[str, Any]
    created_at: str
    updated_at: str
    started_at: str
    completed_at: str

    def payload(self, *, include_plan: bool = True) -> Dict[str, Any]:
        result = {
            "job_id": self.job_id,
            "package_id": self.package_id,
            "novel_path": self.novel_path,
            "novel_name": self.novel_name,
            "status": self.status,
            "total_chapters": self.total_chapters,
            "completed_chapters": self.completed_chapters,
            "current_chapter": self.current_chapter,
            "progress": self.progress,
            "prompt_version": self.prompt_version,
            "model": self.model,
            "output_path": self.output_path,
            "result_package_id": self.result_package_id,
            "error": self.error,
            "pause_requested": self.pause_requested,
            "cancel_requested": self.cancel_requested,
            "quality_status": self.quality_status,
            "quality_score": self.quality_score,
            "quality_report": dict(self.quality_report),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
        if include_plan:
            result.update(
                {
                    "chapters": list(self.chapters),
                    "timeline_plan": {
                        str(key): value
                        for key, value in self.timeline_plan.items()
                    },
                    "volume_plan": {
                        str(key): value
                        for key, value in self.volume_plan.items()
                    },
                    "volume_size": self.volume_size,
                }
            )
        return result


class CompilationJobStore:
    """编译控制面权威存储。

    所有连接按操作创建，适合 FastAPI 主线程和后台编译线程共同使用。
    """

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _initialise(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS compiler_jobs (
                    job_id TEXT PRIMARY KEY,
                    package_id TEXT NOT NULL,
                    novel_path TEXT NOT NULL,
                    novel_name TEXT NOT NULL DEFAULT '',
                    chapters_json TEXT NOT NULL DEFAULT '[]',
                    timeline_plan_json TEXT NOT NULL DEFAULT '{}',
                    volume_plan_json TEXT NOT NULL DEFAULT '{}',
                    volume_size INTEGER NOT NULL DEFAULT 20,
                    status TEXT NOT NULL,
                    total_chapters INTEGER NOT NULL DEFAULT 0,
                    completed_chapters INTEGER NOT NULL DEFAULT 0,
                    current_chapter INTEGER,
                    prompt_version TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    output_path TEXT NOT NULL DEFAULT '',
                    result_package_id TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    pause_requested INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    quality_status TEXT NOT NULL DEFAULT 'pending',
                    quality_score REAL,
                    quality_report_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_compiler_jobs_status
                ON compiler_jobs(status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS compiler_job_chapters (
                    job_id TEXT NOT NULL,
                    chapter_index INTEGER NOT NULL,
                    heading TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    cache_hits INTEGER NOT NULL DEFAULT 0,
                    cache_misses INTEGER NOT NULL DEFAULT 0,
                    extraction_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (job_id, chapter_index),
                    FOREIGN KEY (job_id) REFERENCES compiler_jobs(job_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS compiler_scene_cache (
                    cache_key TEXT PRIMARY KEY,
                    source_hash TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    model TEXT NOT NULL,
                    scene_id TEXT NOT NULL,
                    extraction_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS compiler_job_snapshots (
                    job_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    level TEXT NOT NULL,
                    chapter_start INTEGER NOT NULL,
                    chapter_end INTEGER NOT NULL,
                    volume_id TEXT NOT NULL DEFAULT '',
                    timeline_ids_json TEXT NOT NULL DEFAULT '[]',
                    state_hash TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, snapshot_id),
                    FOREIGN KEY (job_id) REFERENCES compiler_jobs(job_id)
                        ON DELETE CASCADE
                );
                """
            )
            # 单进程 worker 异常退出后，running 不可能仍有执行者。
            # 重启时转为 paused，保留章节缓存与已完成进度供人工继续。
            conn.execute(
                """
                UPDATE compiler_jobs
                SET status = 'paused', current_chapter = NULL,
                    pause_requested = 0, cancel_requested = 0,
                    error = CASE
                        WHEN error = '' THEN '服务重启，任务已安全暂停'
                        ELSE error
                    END,
                    updated_at = ?
                WHERE status = 'running'
                """,
                (_now(),),
            )

    def create_job(
        self,
        *,
        package_id: str,
        novel_path: str,
        novel_name: str = "",
        chapters: Optional[List[int]] = None,
        timeline_plan: Optional[Dict[int, str]] = None,
        volume_plan: Optional[Dict[int, str]] = None,
        volume_size: int = 20,
        prompt_version: str,
        model: str = "",
        output_path: str = "",
    ) -> CompilationJob:
        now = _now()
        job_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO compiler_jobs (
                    job_id, package_id, novel_path, novel_name,
                    chapters_json, timeline_plan_json, volume_plan_json,
                    volume_size, status, prompt_version, model, output_path,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    package_id,
                    novel_path,
                    novel_name,
                    _json(chapters or []),
                    _json(timeline_plan or {}),
                    _json(volume_plan or {}),
                    max(1, int(volume_size)),
                    prompt_version,
                    model,
                    output_path,
                    now,
                    now,
                ),
            )
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> CompilationJob:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM compiler_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise CompilationJobNotFound(f"编译任务不存在: {job_id}")
        return self._row_to_job(row)

    def list_jobs(self, *, limit: int = 100) -> List[CompilationJob]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM compiler_jobs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def prepare_chapters(
        self,
        job_id: str,
        chapters: List[Dict[str, Any]],
    ) -> None:
        with self._connect() as conn:
            self._require_job(conn, job_id)
            for chapter in chapters:
                conn.execute(
                    """
                    INSERT INTO compiler_job_chapters (
                        job_id, chapter_index, heading
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(job_id, chapter_index) DO UPDATE SET
                        heading = excluded.heading
                    """,
                    (
                        job_id,
                        int(chapter["index"]),
                        str(chapter.get("heading") or ""),
                    ),
                )
            conn.execute(
                """
                UPDATE compiler_jobs
                SET total_chapters = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (len(chapters), _now(), job_id),
            )

    def start_run(self, job_id: str) -> CompilationJob:
        with self._connect() as conn:
            row = self._require_job(conn, job_id)
            if row["status"] not in {"queued", "paused", "failed"}:
                raise CompilationJobConflict(
                    f"任务 {job_id} 当前状态不能启动: {row['status']}"
                )
            now = _now()
            conn.execute(
                """
                UPDATE compiler_jobs
                SET status = 'running', pause_requested = 0,
                    cancel_requested = 0, error = '', updated_at = ?,
                    started_at = CASE
                        WHEN started_at = '' THEN ?
                        ELSE started_at
                    END
                WHERE job_id = ?
                """,
                (now, now, job_id),
            )
        return self.get_job(job_id)

    def set_current_chapter(
        self,
        job_id: str,
        chapter_index: int,
    ) -> None:
        now = _now()
        with self._connect() as conn:
            self._require_job(conn, job_id)
            conn.execute(
                """
                UPDATE compiler_jobs
                SET current_chapter = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (chapter_index, now, job_id),
            )
            conn.execute(
                """
                UPDATE compiler_job_chapters
                SET status = 'running',
                    started_at = CASE
                        WHEN started_at = '' THEN ?
                        ELSE started_at
                    END,
                    error = ''
                WHERE job_id = ? AND chapter_index = ?
                """,
                (now, job_id, chapter_index),
            )

    def mark_chapter_completed(
        self,
        job_id: str,
        chapter_index: int,
        *,
        extraction_count: int,
        cache_hits: int,
        cache_misses: int,
    ) -> None:
        now = _now()
        with self._connect() as conn:
            self._require_job(conn, job_id)
            conn.execute(
                """
                UPDATE compiler_job_chapters
                SET status = 'completed', extraction_count = ?,
                    cache_hits = ?, cache_misses = ?, error = '',
                    completed_at = ?
                WHERE job_id = ? AND chapter_index = ?
                """,
                (
                    extraction_count,
                    cache_hits,
                    cache_misses,
                    now,
                    job_id,
                    chapter_index,
                ),
            )
            completed = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM compiler_job_chapters
                WHERE job_id = ? AND status = 'completed'
                """,
                (job_id,),
            ).fetchone()["count"]
            conn.execute(
                """
                UPDATE compiler_jobs
                SET completed_chapters = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (completed, now, job_id),
            )

    def list_chapters(self, job_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            self._require_job(conn, job_id)
            rows = conn.execute(
                """
                SELECT * FROM compiler_job_chapters
                WHERE job_id = ?
                ORDER BY chapter_index
                """,
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def request_pause(self, job_id: str) -> CompilationJob:
        with self._connect() as conn:
            row = self._require_job(conn, job_id)
            status = row["status"]
            if status == "queued":
                conn.execute(
                    """
                    UPDATE compiler_jobs
                    SET status = 'paused', pause_requested = 0,
                        updated_at = ?
                    WHERE job_id = ?
                    """,
                    (_now(), job_id),
                )
            elif status == "running":
                conn.execute(
                    """
                    UPDATE compiler_jobs
                    SET pause_requested = 1, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (_now(), job_id),
                )
            elif status != "paused":
                raise CompilationJobConflict(
                    f"任务 {job_id} 当前状态不能暂停: {status}"
                )
        return self.get_job(job_id)

    def resume(self, job_id: str) -> CompilationJob:
        with self._connect() as conn:
            row = self._require_job(conn, job_id)
            if row["status"] == "queued":
                return self._row_to_job(row)
            if row["status"] not in {"paused", "failed"}:
                raise CompilationJobConflict(
                    f"任务 {job_id} 当前状态不能继续: {row['status']}"
                )
            conn.execute(
                """
                UPDATE compiler_jobs
                SET status = 'queued', pause_requested = 0,
                    cancel_requested = 0, error = '', updated_at = ?
                WHERE job_id = ?
                """,
                (_now(), job_id),
            )
        return self.get_job(job_id)

    def request_cancel(self, job_id: str) -> CompilationJob:
        with self._connect() as conn:
            row = self._require_job(conn, job_id)
            status = row["status"]
            if status in {"queued", "paused", "failed"}:
                conn.execute(
                    """
                    UPDATE compiler_jobs
                    SET status = 'cancelled', cancel_requested = 0,
                        current_chapter = NULL, updated_at = ?,
                        completed_at = ?
                    WHERE job_id = ?
                    """,
                    (_now(), _now(), job_id),
                )
            elif status == "running":
                conn.execute(
                    """
                    UPDATE compiler_jobs
                    SET cancel_requested = 1, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (_now(), job_id),
                )
            elif status != "cancelled":
                raise CompilationJobConflict(
                    f"任务 {job_id} 当前状态不能取消: {status}"
                )
        return self.get_job(job_id)

    def stop_reason(self, job_id: str) -> str:
        job = self.get_job(job_id)
        if job.cancel_requested or job.status == "cancelled":
            return "cancelled"
        if job.pause_requested or job.status == "paused":
            return "paused"
        return ""

    def mark_stopped(self, job_id: str, reason: str) -> CompilationJob:
        if reason not in {"paused", "cancelled"}:
            raise ValueError("停止原因必须是 paused 或 cancelled")
        now = _now()
        with self._connect() as conn:
            self._require_job(conn, job_id)
            conn.execute(
                """
                UPDATE compiler_jobs
                SET status = ?, pause_requested = 0,
                    cancel_requested = 0, current_chapter = NULL,
                    updated_at = ?,
                    completed_at = CASE
                        WHEN ? = 'cancelled' THEN ?
                        ELSE completed_at
                    END
                WHERE job_id = ?
                """,
                (reason, now, reason, now, job_id),
            )
        return self.get_job(job_id)

    def mark_failed(self, job_id: str, error: str) -> CompilationJob:
        now = _now()
        with self._connect() as conn:
            self._require_job(conn, job_id)
            conn.execute(
                """
                UPDATE compiler_jobs
                SET status = 'failed', error = ?, current_chapter = NULL,
                    pause_requested = 0, cancel_requested = 0,
                    updated_at = ?, completed_at = ?
                WHERE job_id = ?
                """,
                (error[:4000], now, now, job_id),
            )
        return self.get_job(job_id)

    def mark_completed(
        self,
        job_id: str,
        *,
        result_package_id: str,
        output_path: str,
    ) -> CompilationJob:
        now = _now()
        with self._connect() as conn:
            self._require_job(conn, job_id)
            conn.execute(
                """
                UPDATE compiler_jobs
                SET status = 'completed', result_package_id = ?,
                    output_path = ?, current_chapter = NULL,
                    completed_chapters = total_chapters,
                    pause_requested = 0, cancel_requested = 0,
                    updated_at = ?, completed_at = ?
                WHERE job_id = ?
                """,
                (result_package_id, output_path, now, now, job_id),
            )
        return self.get_job(job_id)

    def set_quality(
        self,
        job_id: str,
        *,
        status: str,
        score: Optional[float],
        report: Dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            self._require_job(conn, job_id)
            conn.execute(
                """
                UPDATE compiler_jobs
                SET quality_status = ?, quality_score = ?,
                    quality_report_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (status, score, _json(report), _now(), job_id),
            )

    def get_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT extraction_json FROM compiler_scene_cache
                WHERE cache_key = ?
                """,
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE compiler_scene_cache
                SET hit_count = hit_count + 1, last_used_at = ?
                WHERE cache_key = ?
                """,
                (_now(), cache_key),
            )
        return json.loads(row["extraction_json"])

    def put_cache(
        self,
        *,
        cache_key: str,
        source_hash: str,
        prompt_version: str,
        model: str,
        scene_id: str,
        extraction: Dict[str, Any],
    ) -> None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO compiler_scene_cache (
                    cache_key, source_hash, prompt_version, model,
                    scene_id, extraction_json, created_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    extraction_json = excluded.extraction_json,
                    last_used_at = excluded.last_used_at
                """,
                (
                    cache_key,
                    source_hash,
                    prompt_version,
                    model,
                    scene_id,
                    _json(extraction),
                    now,
                    now,
                ),
            )

    def save_snapshot(
        self,
        job_id: str,
        *,
        metadata: Dict[str, Any],
        state: Dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            self._require_job(conn, job_id)
            conn.execute(
                """
                INSERT INTO compiler_job_snapshots (
                    job_id, snapshot_id, level, chapter_start,
                    chapter_end, volume_id, timeline_ids_json,
                    state_hash, state_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, snapshot_id) DO UPDATE SET
                    level = excluded.level,
                    chapter_start = excluded.chapter_start,
                    chapter_end = excluded.chapter_end,
                    volume_id = excluded.volume_id,
                    timeline_ids_json = excluded.timeline_ids_json,
                    state_hash = excluded.state_hash,
                    state_json = excluded.state_json,
                    created_at = excluded.created_at
                """,
                (
                    job_id,
                    metadata["snapshot_id"],
                    metadata["level"],
                    int(metadata["chapter_start"]),
                    int(metadata["chapter_end"]),
                    str(metadata.get("volume_id") or ""),
                    _json(metadata.get("timeline_ids") or []),
                    metadata["state_hash"],
                    _json(state),
                    _now(),
                ),
            )

    def list_snapshots(
        self,
        job_id: str,
        *,
        include_state: bool = False,
    ) -> List[Dict[str, Any]]:
        columns = "*" if include_state else (
            "job_id, snapshot_id, level, chapter_start, chapter_end, "
            "volume_id, timeline_ids_json, state_hash, created_at"
        )
        with self._connect() as conn:
            self._require_job(conn, job_id)
            rows = conn.execute(
                f"""
                SELECT {columns}
                FROM compiler_job_snapshots
                WHERE job_id = ?
                ORDER BY chapter_end, level
                """,
                (job_id,),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["timeline_ids"] = json.loads(
                item.pop("timeline_ids_json")
            )
            if include_state:
                item["state"] = json.loads(item.pop("state_json"))
            results.append(item)
        return results

    @staticmethod
    def _require_job(
        conn: sqlite3.Connection,
        job_id: str,
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM compiler_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise CompilationJobNotFound(f"编译任务不存在: {job_id}")
        return row

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> CompilationJob:
        total = int(row["total_chapters"])
        completed = int(row["completed_chapters"])
        progress = (completed / total) if total else 0.0
        return CompilationJob(
            job_id=row["job_id"],
            package_id=row["package_id"],
            novel_path=row["novel_path"],
            novel_name=row["novel_name"],
            chapters=[int(item) for item in json.loads(row["chapters_json"])],
            timeline_plan={
                int(key): str(value)
                for key, value in json.loads(
                    row["timeline_plan_json"]
                ).items()
            },
            volume_plan={
                int(key): str(value)
                for key, value in json.loads(
                    row["volume_plan_json"]
                ).items()
            },
            volume_size=int(row["volume_size"]),
            status=row["status"],
            total_chapters=total,
            completed_chapters=completed,
            current_chapter=row["current_chapter"],
            progress=round(progress, 4),
            prompt_version=row["prompt_version"],
            model=row["model"],
            output_path=row["output_path"],
            result_package_id=row["result_package_id"],
            error=row["error"],
            pause_requested=bool(row["pause_requested"]),
            cancel_requested=bool(row["cancel_requested"]),
            quality_status=row["quality_status"],
            quality_score=row["quality_score"],
            quality_report=json.loads(row["quality_report_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )
