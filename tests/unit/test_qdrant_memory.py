"""Qdrant 派生索引与 SQLite 权威记忆的组合测试。"""

from types import SimpleNamespace

import pytest

from engine import (
    HybridRetrievalWeights,
    PersistenceError,
    QdrantBackedWorldStore,
    QdrantMemoryHit,
    QdrantMemoryIndex,
    SQLiteWorldStore,
    create_world_store,
)
from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import NIGHT, QINGQING


class _FakeIndex:
    def __init__(self):
        self.upserted = []
        self.deleted = []
        self.hits = []
        self.search_error = None

    def upsert(self, records):
        self.upserted.extend(records)

    def search(self, *_args, **_kwargs):
        if self.search_error is not None:
            raise self.search_error
        return self.hits

    def delete(self, **scope):
        self.deleted.append(scope)


def _store(tmp_path):
    base = SQLiteWorldStore(tmp_path / "world.sqlite3")
    session_id = base.create_session(
        build_snapshot(),
        default_actor_id=NIGHT,
        world_package_id="huarong_lane",
    )
    index = _FakeIndex()
    return base, QdrantBackedWorldStore(base, index), index, session_id


def test_qdrant_point_id_is_stable_uuid():
    first = QdrantMemoryIndex.point_id("memory-a")
    second = QdrantMemoryIndex.point_id("memory-a")

    assert first == second
    assert first != QdrantMemoryIndex.point_id("memory-b")
    assert len(first) == 36


def test_hybrid_weights_must_be_non_negative_and_sum_to_one():
    with pytest.raises(ValueError, match="之和"):
        HybridRetrievalWeights(semantic=0.5)
    with pytest.raises(ValueError, match="负数"):
        HybridRetrievalWeights(
            semantic=1.1,
            lexical=-0.1,
            importance=0.0,
            recency=0.0,
        )


def test_tuned_hybrid_weights_are_the_runtime_default():
    weights = HybridRetrievalWeights()

    assert weights == HybridRetrievalWeights(
        semantic=0.80,
        lexical=0.10,
        importance=0.05,
        recency=0.05,
    )


def test_qdrant_index_uses_collection_scope_and_payload():
    class _Model:
        def __init__(self, **values):
            self.__dict__.update(values)

    models = SimpleNamespace(
        Distance=SimpleNamespace(COSINE="cosine"),
        VectorParams=_Model,
        PointStruct=_Model,
        FieldCondition=_Model,
        MatchValue=_Model,
        Filter=_Model,
        FilterSelector=_Model,
        PointIdsList=_Model,
    )

    class _Client:
        def __init__(self):
            self.created = None
            self.points = []
            self.query_filter = None

        def get_collections(self):
            return SimpleNamespace(collections=[])

        def create_collection(self, **kwargs):
            self.created = kwargs

        def upsert(self, *, points, **_kwargs):
            self.points = points

        def search(self, *, query_filter, **_kwargs):
            self.query_filter = query_filter
            return [
                SimpleNamespace(
                    payload={"memory_id": "memory-a"},
                    score=0.75,
                )
            ]

        def delete(self, **_kwargs):
            pass

    embedder = SimpleNamespace(
        dimensions=3,
        embed=lambda _text: [0.1, 0.2, 0.3],
    )
    client = _Client()
    index = QdrantMemoryIndex(
        embedder=embedder,
        client=client,
        models_module=models,
    )
    record = SimpleNamespace(
        memory_id="memory-a",
        session_id="session-a",
        character_id="character-a",
        source_event_id="event-a",
        world_version=1,
        memory_type="episodic",
        content="一段记忆",
    )

    index.upsert([record])
    hits = index.search(
        "session-a",
        "character-a",
        "查询",
        limit=4,
    )

    assert client.created["vectors_config"].size == 3
    assert client.points[0].payload["session_id"] == "session-a"
    assert hits == [QdrantMemoryHit(memory_id="memory-a", score=0.75)]
    conditions = {
        item.key: item.match.value
        for item in client.query_filter.must
    }
    assert conditions == {
        "session_id": "session-a",
        "character_id": "character-a",
    }


def test_qdrant_wrapper_indexes_durable_sqlite_records(tmp_path):
    base, store, index, session_id = _store(tmp_path)

    ids = store.record_character_memories(
        session_id,
        [QINGQING],
        source_event_id="event-1",
        world_version=1,
        content="她记住了这场雨。",
    )

    assert [item.memory_id for item in index.upserted] == ids
    assert base.get_character_memories(ids)[0].content == "她记住了这场雨。"


def test_hybrid_search_can_return_semantic_only_result(tmp_path):
    _base, store, index, session_id = _store(tmp_path)
    ids = store.record_character_memories(
        session_id,
        [QINGQING],
        source_event_id="event-rain",
        world_version=1,
        content="她在长街暴雨里藏起了密信。",
    )
    index.hits = [QdrantMemoryHit(memory_id=ids[0], score=0.92)]

    found = store.search_character_memories(
        session_id,
        QINGQING,
        "无法被词法命中的语义询问",
        limit=4,
    )

    assert [item.memory_id for item in found] == ids
    assert found[0].retrieval_score > 0


def test_hybrid_search_ignores_stale_qdrant_points(tmp_path):
    _base, store, index, session_id = _store(tmp_path)
    index.hits = [QdrantMemoryHit(memory_id="missing", score=0.99)]

    assert store.search_character_memories(
        session_id,
        QINGQING,
        "任意查询",
    ) == []


def test_hybrid_search_falls_back_to_sqlite_fts(tmp_path):
    _base, store, index, session_id = _store(tmp_path)
    ids = store.record_character_memories(
        session_id,
        [QINGQING],
        source_event_id="event-ledger",
        world_version=1,
        content="她看见了书店账本。",
    )
    index.search_error = PersistenceError("Qdrant 暂时不可用")

    found = store.search_character_memories(
        session_id,
        QINGQING,
        "书店账本",
    )

    assert [item.memory_id for item in found] == ids


def test_prune_and_delete_keep_qdrant_in_sync(tmp_path):
    _base, store, index, session_id = _store(tmp_path)
    first = store.record_character_memories(
        session_id,
        [QINGQING],
        source_event_id="event-low",
        world_version=1,
        content="低重要度记忆",
        importance=0.1,
    )[0]
    store.record_character_memories(
        session_id,
        [QINGQING],
        source_event_id="event-high",
        world_version=2,
        content="高重要度记忆",
        importance=0.9,
    )

    assert store.prune_character_memories(
        session_id,
        QINGQING,
        max_records=1,
    ) == 1
    assert index.deleted[-1] == {"memory_ids": [first]}

    assert store.delete_character_memories(session_id) == 1
    assert index.deleted[-1] == {
        "session_id": session_id,
        "memory_type": None,
    }


def test_factory_can_wrap_sqlite_with_injected_qdrant(tmp_path):
    index = _FakeIndex()

    store = create_world_store(
        sqlite_path=tmp_path / "world.sqlite3",
        database_url="",
        memory_vector_backend="qdrant",
        qdrant_index=index,
    )

    assert isinstance(store, QdrantBackedWorldStore)
    assert isinstance(store.delegate, SQLiteWorldStore)


def test_factory_requires_embedder_when_qdrant_is_enabled(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("MEMORY_EMBEDDING_MODEL", raising=False)

    with pytest.raises(PersistenceError, match="MEMORY_EMBEDDING_MODEL"):
        create_world_store(
            sqlite_path=tmp_path / "world.sqlite3",
            database_url="",
            memory_vector_backend="qdrant",
        )
