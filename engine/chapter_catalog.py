"""小说目录、章节正文缓存与可直接进入的章节入口。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ENTRY_STATUSES = {"content_ready", "world_ready", "published", "failed"}


class ChapterCatalogError(RuntimeError):
    """章节目录操作失败。"""


class ChapterEntryNotFound(ChapterCatalogError):
    """章节入口不存在。"""


class ChapterEntryNotPublished(ChapterCatalogError):
    """章节正文存在，但世界入口尚未发布。"""


@dataclass(frozen=True)
class ChapterEntry:
    book_id: str
    novel: str
    entry_id: str
    chapter_number: int
    raw_number: str
    title: str
    chapter_start: int
    chapter_end: int
    source_hash: str
    snapshot_id: str
    package_id: str
    entry_status: str
    canonical: bool
    canonical_case_id: str
    mission: Dict[str, Any]
    identity: str
    character_summary: List[str]
    location_summary: List[str]
    previous_entry_id: str
    next_entry_id: str
    revision: int
    compiler_version: str
    error: str
    content: str = ""
    paragraphs: List[str] = None

    def payload(self, *, include_content: bool = False) -> Dict[str, Any]:
        result = {
            "book_id": self.book_id,
            "novel": self.novel,
            "entry_id": self.entry_id,
            "chapter_number": self.chapter_number,
            "raw_number": self.raw_number,
            "title": self.title,
            "label": f"第 {self.chapter_number} 章 {self.title}".strip(),
            "chapter_start": self.chapter_start,
            "chapter_end": self.chapter_end,
            "source_hash": self.source_hash,
            "snapshot_id": self.snapshot_id,
            "package_id": self.package_id,
            "entry_status": self.entry_status,
            "content_ready": self.entry_status in ENTRY_STATUSES - {"failed"},
            "world_ready": self.entry_status in {"world_ready", "published"},
            "published": self.entry_status == "published",
            "canonical": self.canonical,
            "canonical_case_id": self.canonical_case_id,
            "mission": dict(self.mission),
            "identity": self.identity,
            "character_summary": list(self.character_summary),
            "location_summary": list(self.location_summary),
            "previous_entry_id": self.previous_entry_id,
            "next_entry_id": self.next_entry_id,
            "revision": self.revision,
            "compiler_version": self.compiler_version,
            "error": self.error,
        }
        if include_content:
            result["content"] = self.content
            result["paragraphs"] = list(self.paragraphs or [])
        return result


@dataclass(frozen=True)
class BookCatalog:
    book_id: str
    novel: str
    source_path: str
    source_hash: str
    revision: int
    chapter_count: int
    updated_at: str

    def payload(self) -> Dict[str, Any]:
        return {
            "book_id": self.book_id,
            "novel": self.novel,
            "source_hash": self.source_hash,
            "revision": self.revision,
            "chapter_count": self.chapter_count,
            "updated_at": self.updated_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class ChapterCatalogStore:
    """运行时章节目录仓库；正文共享保存，session 只引用 entry。"""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(str(self.database_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS book_catalog (
                    book_id TEXT PRIMARY KEY,
                    novel TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    chapter_count INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(book_id, revision)
                );
                CREATE TABLE IF NOT EXISTS chapter_content_cache (
                    book_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    chapter_number INTEGER NOT NULL,
                    raw_number TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    paragraphs_json TEXT NOT NULL,
                    start_offset INTEGER NOT NULL,
                    source_hash TEXT NOT NULL,
                    PRIMARY KEY(book_id, revision, chapter_number),
                    FOREIGN KEY(book_id) REFERENCES book_catalog(book_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS chapter_entries (
                    entry_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    chapter_number INTEGER NOT NULL,
                    raw_number TEXT NOT NULL,
                    title TEXT NOT NULL,
                    chapter_start INTEGER NOT NULL,
                    chapter_end INTEGER NOT NULL,
                    source_hash TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL DEFAULT '',
                    package_id TEXT NOT NULL DEFAULT '',
                    entry_status TEXT NOT NULL,
                    canonical INTEGER NOT NULL DEFAULT 0,
                    canonical_case_id TEXT NOT NULL DEFAULT '',
                    mission_json TEXT NOT NULL DEFAULT '{}',
                    identity TEXT NOT NULL DEFAULT '',
                    character_summary_json TEXT NOT NULL DEFAULT '[]',
                    location_summary_json TEXT NOT NULL DEFAULT '[]',
                    previous_entry_id TEXT NOT NULL DEFAULT '',
                    next_entry_id TEXT NOT NULL DEFAULT '',
                    compiler_version TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    UNIQUE(book_id, revision, chapter_number)
                );
                CREATE INDEX IF NOT EXISTS idx_chapter_entries_book
                    ON chapter_entries(book_id, revision, chapter_number);
                """
            )

    def import_book(
        self,
        *,
        book_id: str,
        novel: str,
        source_path: str | Path,
        revision: Optional[int] = None,
    ) -> BookCatalog:
        path = Path(source_path).resolve()
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ChapterCatalogError(f"读取小说失败: {path}") from exc
        source_hash = hashlib.sha256(raw).hexdigest()
        # 延迟导入编译器文本工具，避免 compiler.__init__ 导入任务发布器时
        # 反向加载本目录模块形成循环依赖。
        from compiler.text_loader import load_novel, split_chapters

        text = load_novel(str(path))
        chapters = split_chapters(text)
        if not chapters:
            raise ChapterCatalogError("小说未识别到任何章节")
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT revision, source_hash FROM book_catalog WHERE book_id = ? "
                "ORDER BY revision DESC LIMIT 1", (book_id,)
            ).fetchone()
            if existing and existing["source_hash"] == source_hash:
                return self.get_book(book_id)  # type: ignore[return-value]
            current_revision = int(existing["revision"]) if existing else 0
            target_revision = int(revision or current_revision + 1)
            now = _now()
            if existing:
                conn.execute(
                    "UPDATE book_catalog SET novel=?, source_path=?, source_hash=?, "
                    "revision=?, chapter_count=?, updated_at=? WHERE book_id=?",
                    (novel, str(path), source_hash, target_revision, len(chapters), now, book_id),
                )
            else:
                conn.execute(
                    "INSERT INTO book_catalog "
                    "(book_id, novel, source_path, source_hash, revision, chapter_count, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (book_id, novel, str(path), source_hash, target_revision, len(chapters), now),
                )
            for chapter in chapters:
                chapter_hash = hashlib.sha256(
                    chapter.content.encode("utf-8")
                ).hexdigest()
                conn.execute(
                    "INSERT OR REPLACE INTO chapter_content_cache "
                    "(book_id, revision, chapter_number, raw_number, title, content, "
                    "paragraphs_json, start_offset, source_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (book_id, target_revision, chapter.index, chapter.raw_number,
                     chapter.title, chapter.content, _json(chapter.paragraphs),
                     chapter.start_offset, chapter_hash),
                )
                entry_prefix = book_id if target_revision == 1 else f"{book_id}:r{target_revision}"
                entry_id = f"{entry_prefix}:chapter:{chapter.index}"
                previous = (
                    f"{entry_prefix}:chapter:{chapter.index - 1}"
                    if chapter.index > 1 else ""
                )
                next_id = (
                    f"{entry_prefix}:chapter:{chapter.index + 1}"
                    if chapter.index < len(chapters) else ""
                )
                conn.execute(
                    "INSERT OR REPLACE INTO chapter_entries "
                    "(entry_id, book_id, revision, chapter_number, raw_number, title, "
                    "chapter_start, chapter_end, source_hash, entry_status, previous_entry_id, "
                    "next_entry_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (entry_id, book_id, target_revision, chapter.index, chapter.raw_number,
                     chapter.title, chapter.index, chapter.index, chapter_hash,
                     "content_ready", previous, next_id, now),
                )
        return self.get_book(book_id)  # type: ignore[return-value]

    def publish_entry(
        self,
        entry_id: str,
        *,
        package_id: str,
        snapshot_id: str = "",
        canonical: bool = False,
        canonical_case_id: str = "",
        mission: Optional[Dict[str, Any]] = None,
        identity: str = "",
        character_summary: Optional[List[str]] = None,
        location_summary: Optional[List[str]] = None,
        compiler_version: str = "",
    ) -> ChapterEntry:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM chapter_entries WHERE entry_id = ? ORDER BY revision DESC LIMIT 1",
                (entry_id,),
            ).fetchone()
            if row is None:
                raise ChapterEntryNotFound(f"章节入口不存在: {entry_id}")
            if (
                bool(row["canonical"])
                and row["entry_status"] == "published"
                and not canonical
            ):
                # 自动编译只能补齐普通入口，不能覆盖作者已经确认的 canonical 入口。
                return self.get_entry(entry_id)  # type: ignore[return-value]
            conn.execute(
                    "UPDATE chapter_entries SET package_id=?, snapshot_id=?, entry_status=?, "
                    "canonical=?, canonical_case_id=?, mission_json=?, identity=?, "
                    "character_summary_json=?, location_summary_json=?, compiler_version=?, "
                    "error='', updated_at=? WHERE entry_id=? AND revision=?",
                (package_id, snapshot_id, "published", int(canonical), canonical_case_id,
                 _json(mission or {}), identity, _json(character_summary or []),
                 _json(location_summary or []), compiler_version, _now(), entry_id,
                 int(row["revision"])),
            )
        return self.get_entry(entry_id)  # type: ignore[return-value]

    def get_book(self, book_id: str) -> Optional[BookCatalog]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM book_catalog WHERE book_id=? ORDER BY revision DESC LIMIT 1",
                (book_id,),
            ).fetchone()
        if row is None:
            return None
        return BookCatalog(str(row["book_id"]), str(row["novel"]), str(row["source_path"]),
                           str(row["source_hash"]), int(row["revision"]),
                           int(row["chapter_count"]), str(row["updated_at"]))

    def list_books(self) -> List[BookCatalog]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT b.* FROM book_catalog b "
                "WHERE b.revision=(SELECT MAX(x.revision) FROM book_catalog x WHERE x.book_id=b.book_id) "
                "ORDER BY b.novel"
            ).fetchall()
        return [BookCatalog(str(r["book_id"]), str(r["novel"]), str(r["source_path"]),
                            str(r["source_hash"]), int(r["revision"]),
                            int(r["chapter_count"]), str(r["updated_at"])) for r in rows]

    def list_entries(self, book_id: str, *, include_content: bool = False) -> List[ChapterEntry]:
        with self._connect() as conn:
            book = conn.execute(
                "SELECT * FROM book_catalog WHERE book_id=? ORDER BY revision DESC LIMIT 1",
                (book_id,),
            ).fetchone()
            if book is None:
                raise ChapterEntryNotFound(f"书籍不存在: {book_id}")
            rows = conn.execute(
                "SELECT e.*, c.content, c.paragraphs_json FROM chapter_entries e "
                "LEFT JOIN chapter_content_cache c ON c.book_id=e.book_id AND c.revision=e.revision "
                "AND c.chapter_number=e.chapter_number WHERE e.book_id=? AND e.revision=? "
                "ORDER BY e.chapter_number", (book_id, int(book["revision"]))
            ).fetchall()
        return [self._entry(row, str(book["novel"]), include_content) for row in rows]

    def get_entry(self, entry_id: str, *, include_content: bool = False) -> Optional[ChapterEntry]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT e.*, b.novel, c.content, c.paragraphs_json FROM chapter_entries e "
                "JOIN book_catalog b ON b.book_id=e.book_id "
                "LEFT JOIN chapter_content_cache c ON c.book_id=e.book_id AND c.revision=e.revision "
                "AND c.chapter_number=e.chapter_number WHERE e.entry_id=?",
                (entry_id,),
            ).fetchone()
        return self._entry(row, str(row["novel"]), include_content) if row else None

    def _entry(self, row, novel: str, include_content: bool) -> ChapterEntry:
        content = str(row["content"] or "") if include_content else ""
        return ChapterEntry(
            book_id=str(row["book_id"]), novel=novel, entry_id=str(row["entry_id"]),
            chapter_number=int(row["chapter_number"]), raw_number=str(row["raw_number"]),
            title=str(row["title"]), chapter_start=int(row["chapter_start"]),
            chapter_end=int(row["chapter_end"]), source_hash=str(row["source_hash"]),
            snapshot_id=str(row["snapshot_id"]), package_id=str(row["package_id"]),
            entry_status=str(row["entry_status"]), canonical=bool(row["canonical"]),
            canonical_case_id=str(row["canonical_case_id"]),
            mission=json.loads(row["mission_json"] or "{}"), identity=str(row["identity"]),
            character_summary=json.loads(row["character_summary_json"] or "[]"),
            location_summary=json.loads(row["location_summary_json"] or "[]"),
            previous_entry_id=str(row["previous_entry_id"]), next_entry_id=str(row["next_entry_id"]),
            revision=int(row["revision"]), compiler_version=str(row["compiler_version"]),
            error=str(row["error"]), content=content,
            paragraphs=json.loads(row["paragraphs_json"] or "[]") if include_content else [],
        )

    def require_published(self, entry_id: str) -> ChapterEntry:
        entry = self.get_entry(entry_id)
        if entry is None:
            raise ChapterEntryNotFound(f"章节入口不存在: {entry_id}")
        if entry.entry_status != "published":
            raise ChapterEntryNotPublished(
                f"章节世界正在准备: {entry.title or entry.entry_id}"
            )
        return entry
