import pytest

from compiler.chapter_publisher import ChapterPublishError, ChapterRuntimePublisher
from compiler.job_store import CompilationJobStore
from compiler.cli import _fresh_state
from engine.chapter_catalog import ChapterCatalogStore
from engine.event import state_hash
from engine.world_packages import WorldPackageStore
from world_schema import Character


NOVEL = """第1章 初见

第一章正文。

第2章 追查

第二章正文。
"""


def _state(flag=None):
    state = _fresh_state("compiled_book")
    state.characters["char_player"] = Character(
        character_id="char_player",
        display_name="玩家",
    )
    if flag:
        state.flags[flag] = True
    return state


def test_publisher_binds_entry_to_chapter_start_snapshot(tmp_path):
    novel = tmp_path / "novel.txt"
    novel.write_text(NOVEL, encoding="utf-8")
    compiler_db = tmp_path / "compiler.sqlite3"
    world_db = tmp_path / "world.sqlite3"
    store = CompilationJobStore(compiler_db)
    job = store.create_job(
        package_id="compiled_book",
        book_id="book",
        novel_path=str(novel),
        novel_name="测试书",
        chapters=[1, 2],
        prompt_version="test-v1",
    )
    store.prepare_chapters(
        job.job_id,
        [
            {"index": 1, "heading": "第1章 初见"},
            {"index": 2, "heading": "第2章 追查"},
        ],
    )
    for chapter in (1, 2):
        store.mark_chapter_completed(
            job.job_id,
            chapter,
            extraction_count=1,
            cache_hits=0,
            cache_misses=1,
        )
        start = _state("compiled.before_chapter_2" if chapter == 2 else None)
        store.save_snapshot(
            job.job_id,
            metadata={
                "snapshot_id": f"chapter_{chapter:06d}_start",
                "level": "chapter_start",
                "chapter_start": chapter,
                "chapter_end": chapter,
                "volume_id": "volume_0001",
                "timeline_ids": ["timeline_root"],
                "state_hash": state_hash(start),
            },
            state=start.dict(),
        )

    catalog = ChapterCatalogStore(world_db)
    packages = WorldPackageStore(tmp_path / "worlds")
    full_package = {
        "package_id": "compiled_book",
        "novel": "测试书",
        "source_chapters": [1, 2],
        "scenario": "测试书 · 全书",
        "anchor": "测试起始状态",
        "default_actor_id": "char_player",
        "manifest": {
            "compiler": {
                "book_id": "book",
                "source_hash": job.source_hash,
                "stage": "D",
                "quality_gate": {"passed": True},
            }
        },
        "snapshot": _state().dict(),
    }
    packages.save("compiled_book", full_package)
    store.set_quality(
        job.job_id,
        status="passed",
        score=1.0,
        report={"passed": True},
    )
    store.mark_completed(
        job.job_id,
        result_package_id="compiled_book",
        output_path=str(tmp_path / "worlds" / "compiled_book.json"),
    )
    published = ChapterRuntimePublisher(catalog, packages).publish(
        job=store.get_job(job.job_id),
        job_store=store,
        registry=type("Registry", (), {"alias_index": {}})(),
        novel_name="测试书",
    )

    assert [entry.chapter_number for entry in published] == [1, 2]
    entry = catalog.require_published("book:chapter:2")
    assert entry.snapshot_id == "chapter_000002_start"
    package = packages.get(entry.package_id)
    assert package.snapshot.flags["compiled.before_chapter_2"] is True
    assert package.snapshot.version == 0

    repeated = ChapterRuntimePublisher(catalog, packages).publish(
        job=store.get_job(job.job_id),
        job_store=store,
        registry=type("Registry", (), {"alias_index": {}})(),
        novel_name="测试书",
    )
    assert [entry.package_id for entry in repeated] == [
        entry.package_id for entry in published
    ]
    assert packages.get(entry.package_id).revision == package.revision


def test_publisher_rejects_incomplete_job_before_catalog_write(tmp_path):
    novel = tmp_path / "novel.txt"
    novel.write_text(NOVEL, encoding="utf-8")
    store = CompilationJobStore(tmp_path / "compiler.sqlite3")
    job = store.create_job(
        package_id="compiled_book",
        book_id="book",
        novel_path=str(novel),
        novel_name="测试书",
        chapters=[1],
        prompt_version="test-v1",
    )
    catalog = ChapterCatalogStore(tmp_path / "world.sqlite3")
    packages = WorldPackageStore(tmp_path / "worlds")
    with pytest.raises(ChapterPublishError, match="尚未完成"):
        ChapterRuntimePublisher(catalog, packages).publish(
            job=job,
            job_store=store,
            registry=type("Registry", (), {"alias_index": {}})(),
            novel_name="测试书",
        )
    assert catalog.get_book("book") is None


def test_publisher_rejects_changed_source_before_catalog_write(tmp_path):
    novel = tmp_path / "novel.txt"
    novel.write_text(NOVEL, encoding="utf-8")
    store = CompilationJobStore(tmp_path / "compiler.sqlite3")
    job = store.create_job(
        package_id="compiled_book",
        book_id="book",
        novel_path=str(novel),
        novel_name="测试书",
        chapters=[1],
        prompt_version="test-v1",
    )
    store.prepare_chapters(job.job_id, [{"index": 1, "heading": "第1章 初见"}])
    store.mark_chapter_completed(job.job_id, 1, extraction_count=1, cache_hits=0, cache_misses=1)
    store.set_quality(job.job_id, status="passed", score=1.0, report={"passed": True})
    store.mark_completed(job.job_id, result_package_id="compiled_book", output_path="")
    novel.write_text(NOVEL + "\n源文件已被修改", encoding="utf-8")
    catalog = ChapterCatalogStore(tmp_path / "world.sqlite3")
    packages = WorldPackageStore(tmp_path / "worlds")
    with pytest.raises(ChapterPublishError, match="源文件已变化"):
        ChapterRuntimePublisher(catalog, packages).publish(
            job=store.get_job(job.job_id),
            job_store=store,
            registry=type("Registry", (), {"alias_index": {}})(),
            novel_name="测试书",
        )
    assert catalog.get_book("book") is None


def test_publisher_does_not_publish_non_contiguous_chapters(tmp_path):
    novel = tmp_path / "novel.txt"
    novel.write_text(NOVEL, encoding="utf-8")
    store = CompilationJobStore(tmp_path / "compiler.sqlite3")
    job = store.create_job(
        package_id="compiled_book",
        book_id="book",
        novel_path=str(novel),
        novel_name="测试书",
        chapters=[2],
        prompt_version="test-v1",
    )
    store.prepare_chapters(job.job_id, [{"index": 2, "heading": "第2章 追查"}])
    store.mark_chapter_completed(
        job.job_id,
        2,
        extraction_count=1,
        cache_hits=0,
        cache_misses=1,
    )
    store.set_quality(
        job.job_id,
        status="passed",
        score=1.0,
        report={"passed": True},
    )
    store.mark_completed(
        job.job_id,
        result_package_id="compiled_book",
        output_path="",
    )
    catalog = ChapterCatalogStore(tmp_path / "world.sqlite3")
    packages = WorldPackageStore(tmp_path / "worlds")
    catalog.import_book(
        book_id="book",
        novel="测试书",
        source_path=str(novel),
    )
    published = ChapterRuntimePublisher(catalog, packages).publish(
        job=store.get_job(job.job_id),
        job_store=store,
        registry=type("Registry", (), {"alias_index": {}})(),
        novel_name="测试书",
    )
    assert published == []
    assert catalog.list_entries("book")[1].entry_status == "content_ready"
