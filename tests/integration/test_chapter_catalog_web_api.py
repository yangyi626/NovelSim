"""真实 FastAPI 章节目录、发布与直接进入闭环测试。"""

import importlib

from fastapi.testclient import TestClient

from engine import SQLiteWorldStore
from engine.chapter_catalog import ChapterCatalogStore
from examples.huarong_lane.scenario import NIGHT


web_app = importlib.import_module("web.app")


def _write_novel(path):
    path.write_text(
        "第一狂妃：测试本\n\n"
        "第1章 华容巷\n\n"
        "夜轻歌在华容巷醒来，发现局势已经失控。\n\n"
        "第2章 那就脱！\n\n"
        "冲突继续，新的线索浮出水面。\n",
        encoding="utf-8",
    )


def test_real_chapter_catalog_and_direct_start_round_trip(tmp_path, monkeypatch):
    catalog = ChapterCatalogStore(tmp_path / "catalog.sqlite3")
    source = tmp_path / "novel.txt"
    _write_novel(source)
    catalog.import_book(
        book_id="test_book",
        novel="第一狂妃：测试本",
        source_path=source,
    )
    catalog.publish_entry(
        "test_book:chapter:1",
        package_id=web_app.CANONICAL_CH1_PACKAGE_ID,
        snapshot_id="test-book-ch1-snapshot",
        mission={"goal": "验证直接进入"},
        identity="快穿者 · 夜轻歌",
    )

    store = SQLiteWorldStore(tmp_path / "world.sqlite3")
    monkeypatch.setattr(web_app, "CHAPTER_CATALOG", catalog)
    monkeypatch.setattr(web_app, "SESSIONS", store)

    with TestClient(web_app.app) as client:
        books = client.get("/api/books")
        assert books.status_code == 200
        book_payload = books.json()["books"]
        assert len(book_payload) == 1
        assert book_payload[0]["book_id"] == "test_book"
        assert book_payload[0]["novel"] == "第一狂妃：测试本"
        assert book_payload[0]["revision"] == 1
        assert book_payload[0]["chapter_count"] == 2
        assert book_payload[0]["source_hash"]
        assert book_payload[0]["updated_at"]

        chapters = client.get("/api/books/test_book/chapters")
        assert chapters.status_code == 200
        chapter_payload = chapters.json()["chapters"]
        assert len(chapter_payload) == 2
        assert "content" not in chapter_payload[0]
        assert chapter_payload[0]["published"] is True
        assert chapter_payload[1]["published"] is False
        assert chapter_payload[1]["content_ready"] is True

        with_content = client.get(
            "/api/books/test_book/chapters",
            params={"include_content": "true"},
        )
        assert with_content.status_code == 200
        assert with_content.json()["chapters"][0]["content"] == (
            "夜轻歌在华容巷醒来，发现局势已经失控。\n"
        )
        assert with_content.json()["chapters"][0]["paragraphs"]

        assert client.get("/api/books/unknown/chapters").status_code == 404
        unpublished = client.post(
            "/api/start",
            json={"entry_id": "test_book:chapter:2"},
        )
        assert unpublished.status_code == 409

        started = client.post(
            "/api/start",
            json={"entry_id": "test_book:chapter:1"},
        )
        assert started.status_code == 200
        payload = started.json()
        assert payload["status"] == "ok"
        session_id = payload["session_id"]
        assert payload["world_meta"]["book_id"] == "test_book"
        assert payload["world_meta"]["entry_id"] == "test_book:chapter:1"
        assert payload["world_meta"]["chapter_number"] == 1
        assert payload["world_meta"]["entry_revision"] == 1

        restored = client.get("/api/session", params={"session": session_id})
        assert restored.status_code == 200
        assert restored.json()["save"]["book_id"] == "test_book"
        assert restored.json()["save"]["entry_id"] == "test_book:chapter:1"
        assert restored.json()["save"]["chapter_number"] == 1
        assert restored.json()["save"]["entry_revision"] == 1

    metadata = store.get_metadata(session_id)
    assert metadata is not None
    assert metadata.default_actor_id == NIGHT
    assert metadata.book_id == "test_book"
    assert metadata.entry_id == "test_book:chapter:1"
    assert metadata.chapter_number == 1
    assert metadata.entry_revision == 1
    assert store.get_session_lineage(session_id) is None
    assert store.list_sessions()[0].session_id == session_id


def test_chapter_catalog_start_missing_entry_returns_not_found(tmp_path, monkeypatch):
    catalog = ChapterCatalogStore(tmp_path / "catalog.sqlite3")
    store = SQLiteWorldStore(tmp_path / "world.sqlite3")
    monkeypatch.setattr(web_app, "CHAPTER_CATALOG", catalog)
    monkeypatch.setattr(web_app, "SESSIONS", store)

    with TestClient(web_app.app) as client:
        response = client.post(
            "/api/start",
            json={"entry_id": "missing:chapter:1"},
        )

    assert response.status_code == 404
    assert store.list_sessions() == []
