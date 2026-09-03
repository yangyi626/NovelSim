from pathlib import Path

from engine.chapter_catalog import (
    ChapterCatalogStore,
    ChapterEntryNotPublished,
)


def _write_novel(path: Path, suffix: str = "") -> None:
    path.write_text(
        "第一狂妃\n\n第1章 华容巷\n\n正文一\n\n"
        "第2章 那就脱！\n\n正文二" + suffix,
        encoding="utf-8",
    )


def test_import_is_idempotent_and_persists_all_chapter_content(tmp_path):
    source = tmp_path / "novel.txt"
    _write_novel(source)
    store = ChapterCatalogStore(tmp_path / "catalog.sqlite3")

    first = store.import_book(book_id="first_crazy", novel="第一狂妃", source_path=source)
    second = store.import_book(book_id="first_crazy", novel="第一狂妃", source_path=source)

    assert first.revision == second.revision == 1
    entries = store.list_entries("first_crazy", include_content=True)
    assert [entry.entry_id for entry in entries] == [
        "first_crazy:chapter:1", "first_crazy:chapter:2"
    ]
    assert entries[0].content == "正文一\n"
    assert entries[1].title == "那就脱！"
    assert entries[0].entry_status == "content_ready"


def test_content_change_creates_new_revision_without_overwriting_old_entry(tmp_path):
    source = tmp_path / "novel.txt"
    _write_novel(source)
    store = ChapterCatalogStore(tmp_path / "catalog.sqlite3")
    store.import_book(book_id="book", novel="书", source_path=source)
    _write_novel(source, "\n新增内容")
    latest = store.import_book(book_id="book", novel="书", source_path=source)

    assert latest.revision == 2
    entries = store.list_entries("book", include_content=True)
    assert entries[0].entry_id == "book:r2:chapter:1"
    assert "新增内容" not in entries[0].content
    old = store.get_entry("book:chapter:1", include_content=True)
    assert old is not None
    assert old.revision == 1
    assert old.content == "正文一\n"


def test_canonical_published_entry_is_protected_from_automatic_publish(tmp_path):
    source = tmp_path / "novel.txt"
    _write_novel(source)
    store = ChapterCatalogStore(tmp_path / "catalog.sqlite3")
    store.import_book(book_id="book", novel="书", source_path=source)
    canonical = store.publish_entry(
        "book:chapter:1", package_id="canonical_package",
        snapshot_id="canonical_snapshot", canonical=True,
        canonical_case_id="case-1", mission={"goal": "canonical"},
    )
    result = store.publish_entry(
        "book:chapter:1", package_id="compiled_package",
        snapshot_id="compiled_snapshot", mission={"goal": "automatic"},
    )
    assert result == canonical
    assert result.package_id == "canonical_package"
    assert result.snapshot_id == "canonical_snapshot"
    assert result.canonical_case_id == "case-1"


def test_republishing_same_entry_is_idempotent_in_bindings(tmp_path):
    source = tmp_path / "novel.txt"
    _write_novel(source)
    store = ChapterCatalogStore(tmp_path / "catalog.sqlite3")
    store.import_book(book_id="book", novel="书", source_path=source)
    kwargs = {
        "package_id": "compiled_package", "snapshot_id": "compiled_snapshot",
        "mission": {"goal": "automatic"}, "identity": "玩家",
        "character_summary": ["玩家"], "location_summary": ["华容巷"],
        "compiler_version": "test-v1",
    }
    first = store.publish_entry("book:chapter:1", **kwargs)
    second = store.publish_entry("book:chapter:1", **kwargs)
    assert second.package_id == first.package_id
    assert second.snapshot_id == first.snapshot_id
    assert second.mission == first.mission
    assert second.revision == first.revision


def test_only_published_entries_can_be_started(tmp_path):
    source = tmp_path / "novel.txt"
    _write_novel(source)
    store = ChapterCatalogStore(tmp_path / "catalog.sqlite3")
    store.import_book(book_id="book", novel="书", source_path=source)

    try:
        store.require_published("book:chapter:1")
    except ChapterEntryNotPublished as exc:
        assert "章节世界正在准备" in str(exc)
    else:
        raise AssertionError("unpublished chapter unexpectedly accepted")

    published = store.publish_entry(
        "book:chapter:1", package_id="compiled_chapter_1", snapshot_id="snapshot-1"
    )
    assert published.entry_status == "published"
    assert store.require_published("book:chapter:1").package_id == "compiled_chapter_1"
