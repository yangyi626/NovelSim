"""双数据库后端、嵌入配置与 pgvector 辅助逻辑测试。"""

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from engine import (
    CachedMemoryEmbedder,
    EmbeddingError,
    OpenAICompatibleEmbedder,
    PersistenceError,
    PostgresWorldStore,
    SQLiteWorldStore,
    create_world_store,
    memory_embedder_from_env,
)
from engine.postgres_persistence import _vector_literal


def test_store_factory_defaults_to_sqlite(tmp_path):
    store = create_world_store(
        sqlite_path=tmp_path / "world.sqlite3",
        memory_vector_backend="",
    )

    assert isinstance(store, SQLiteWorldStore)


def test_store_factory_rejects_unknown_database_scheme(tmp_path):
    with pytest.raises(PersistenceError, match="仅支持"):
        create_world_store(
            sqlite_path=tmp_path / "world.sqlite3",
            database_url="mysql://localhost/game",
        )


def test_store_factory_selects_postgres_without_importing_driver(
    tmp_path, monkeypatch
):
    captured = {}

    class _FakePostgresStore:
        def __init__(self, database_url, *, embedder=None):
            captured["url"] = database_url
            captured["embedder"] = embedder

    import engine.postgres_persistence as postgres_module

    monkeypatch.setattr(
        postgres_module,
        "PostgresWorldStore",
        _FakePostgresStore,
    )
    marker_embedder = object()
    store = create_world_store(
        sqlite_path=tmp_path / "unused.sqlite3",
        database_url="postgresql://db/game",
        embedder=marker_embedder,
        memory_vector_backend="",
    )

    assert isinstance(store, _FakePostgresStore)
    assert captured == {
        "url": "postgresql://db/game",
        "embedder": marker_embedder,
    }


def test_manuscript_backend_signatures_are_symmetric():
    sqlite_complete = inspect.signature(
        SQLiteWorldStore.complete_manuscript_passage
    )
    postgres_complete = inspect.signature(
        PostgresWorldStore.complete_manuscript_passage
    )

    assert sqlite_complete == postgres_complete
    assert "expected_current_revision" in sqlite_complete.parameters
    assert inspect.signature(
        SQLiteWorldStore.list_campaign_manuscript_passages
    ) == inspect.signature(
        PostgresWorldStore.list_campaign_manuscript_passages
    )
    assert inspect.signature(
        SQLiteWorldStore.select_manuscript_passage_revision
    ) == inspect.signature(
        PostgresWorldStore.select_manuscript_passage_revision
    )
    assert inspect.signature(
        SQLiteWorldStore.get_state_at_version
    ) == inspect.signature(
        PostgresWorldStore.get_state_at_version
    )


def test_postgres_store_configuration_is_lazy_without_driver():
    store = PostgresWorldStore(
        "postgresql://localhost/game",
        vector_dimensions=8,
        initialize=False,
    )

    assert store.vector_dimensions == 8
    assert store.embedder is None


def test_postgres_hnsw_dimension_limit_is_validated():
    with pytest.raises(PersistenceError, match="HNSW"):
        PostgresWorldStore(
            "postgresql://localhost/game",
            vector_dimensions=2001,
            initialize=False,
        )


def test_vector_literal_rejects_non_finite_values():
    assert _vector_literal([0, 1.25, -2]) == "[0,1.25,-2]"
    with pytest.raises(PersistenceError, match="非有限"):
        _vector_literal([float("nan")])


def test_memory_embedder_env_is_optional(monkeypatch):
    monkeypatch.delenv("MEMORY_EMBEDDING_MODEL", raising=False)

    assert memory_embedder_from_env() is None


def test_memory_embedder_env_validates_required_key(monkeypatch):
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "embedding-model")
    monkeypatch.delenv("MEMORY_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("MEMORY_EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    with pytest.raises(EmbeddingError, match="缺少"):
        memory_embedder_from_env()


def test_memory_embedder_accepts_dashscope_key(monkeypatch):
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "embedding-model")
    monkeypatch.setenv("MEMORY_EMBEDDING_DIMENSIONS", "1024")
    monkeypatch.delenv("MEMORY_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("MEMORY_EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.setenv(
        "LLM_BASE_URL",
        "https://dashscope.example/v1",
    )

    embedder = memory_embedder_from_env()

    assert embedder.api_key == "dashscope-key"
    assert embedder.base_url == "https://dashscope.example/v1"
    assert embedder.model == "embedding-model"
    assert embedder.dimensions == 1024


def test_openai_compatible_embedder_validates_dimensions(monkeypatch):
    fake_openai = SimpleNamespace(
        Embedding=SimpleNamespace(
            create=lambda **_: {"data": [{"embedding": [0.1, 0.2]}]}
        )
    )
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    embedder = OpenAICompatibleEmbedder(
        api_key="test",
        base_url="https://example.test/v1",
        model="embedding-model",
        dimensions=3,
    )

    with pytest.raises(EmbeddingError, match="维度不一致"):
        embedder.embed("一段记忆")


def test_openai_compatible_embedder_batches_and_restores_order(monkeypatch):
    fake_openai = SimpleNamespace(
        Embedding=SimpleNamespace(
            create=lambda **_: {
                "data": [
                    {"index": 1, "embedding": [2.0, 2.0]},
                    {"index": 0, "embedding": [1.0, 1.0]},
                ]
            }
        )
    )
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    embedder = OpenAICompatibleEmbedder(
        api_key="test",
        base_url="https://example.test/v1",
        model="embedding-model",
        dimensions=2,
    )

    assert embedder.embed_many(["第一段", "第二段"]) == [
        [1.0, 1.0],
        [2.0, 2.0],
    ]


def test_cached_embedder_warms_unique_texts_in_one_batch():
    calls = []

    class _BatchEmbedder:
        dimensions = 2

        def embed_many(self, texts):
            calls.append(texts)
            return [[float(index), 1.0] for index, _ in enumerate(texts)]

        def embed(self, _text):
            raise AssertionError("应优先使用批量接口")

    cached = CachedMemoryEmbedder(_BatchEmbedder())

    assert cached.warm(["甲", "乙", "甲"]) == 2
    assert cached.warm(["乙"]) == 0
    assert cached.embed("甲") == [0.0, 1.0]
    assert calls == [["甲", "乙"]]
    assert cached.request_count == 1
    assert cached.embedded_text_count == 2


def test_postgres_schema_contains_jsonb_gin_and_hnsw():
    executed = []

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=None):
            executed.append((sql, params))

    class _Connection:
        def cursor(self):
            return _Cursor()

        def commit(self):
            executed.append(("COMMIT", None))

        def rollback(self):
            executed.append(("ROLLBACK", None))

        def close(self):
            pass

    store = PostgresWorldStore(
        "postgresql://localhost/game",
        vector_dimensions=8,
        initialize=False,
    )
    store._connect = lambda: _Connection()
    store._initialize()
    schema = "\n".join(item[0] for item in executed)

    assert "CREATE EXTENSION IF NOT EXISTS vector" in schema
    assert "state_json JSONB" in schema
    assert "base_state_json JSONB" in schema
    assert "base_state_version INTEGER" in schema
    assert "USING GIN(search_document)" in schema
    assert "embedding vector(8)" in schema
    assert "USING hnsw (embedding vector_cosine_ops)" in schema
    assert "evidence_event_ids_json JSONB" in schema
    assert "claim_fact_id TEXT" in schema
    assert "semantic_score DOUBLE PRECISION" in schema
    assert "CREATE TABLE IF NOT EXISTS worldline_manuscripts" in schema
    assert "CREATE TABLE IF NOT EXISTS manuscript_passages" in schema
    assert "source_event_ids_json JSONB" in schema
    assert "UNIQUE (manuscript_id, source_fingerprint)" in schema
    assert "CREATE TABLE IF NOT EXISTS manuscript_passage_revisions" in schema
    assert "revision_json JSONB" in schema
