"""中文长期记忆检索基准、指标与发布门禁测试。"""

import json
from pathlib import Path

import pytest

from engine import (
    MemoryRecord,
    PersistenceError,
    RetrievalBenchmark,
    RetrievalDocument,
    RetrievalQuery,
    evaluate_retrieval,
    load_retrieval_benchmark,
    seed_retrieval_benchmark,
)
from engine.persistence import SQLiteWorldStore
from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import NIGHT, QINGQING


BENCHMARK_PATH = (
    Path(__file__).resolve().parents[2]
    / "benchmarks"
    / "memory_retrieval_zh.json"
)


def _memory(document_id):
    return MemoryRecord(
        memory_id=f"memory-{document_id}",
        session_id="session",
        character_id=QINGQING,
        source_event_id=document_id,
        world_version=1,
        memory_type="benchmark",
        content=document_id,
        importance=0.7,
        created_at="2026-01-01T00:00:00+00:00",
    )


def test_chinese_benchmark_has_fifty_scoped_queries():
    benchmark = load_retrieval_benchmark(BENCHMARK_PATH)

    assert len(benchmark.documents) == 25
    assert len(benchmark.queries) == 50
    assert all(query.relevant_document_ids for query in benchmark.queries)


def test_benchmark_rejects_cross_character_answer(tmp_path):
    payload = {
        "name": "invalid",
        "documents": [
            {
                "id": "doc",
                "character_id": "character-a",
                "content": "记忆",
            }
        ],
        "queries": [
            {
                "id": "query",
                "character_id": "character-b",
                "text": "查询",
                "relevant": ["doc"],
            }
        ],
    }
    path = tmp_path / "benchmark.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PersistenceError, match="跨越角色"):
        load_retrieval_benchmark(path)


def test_retrieval_metrics_include_rank_recall_and_irrelevant_rate():
    benchmark = RetrievalBenchmark(
        name="metrics",
        documents=[
            RetrievalDocument("a", QINGQING, "A"),
            RetrievalDocument("b", QINGQING, "B"),
            RetrievalDocument("c", QINGQING, "C"),
        ],
        queries=[
            RetrievalQuery("q1", QINGQING, "one", ["a"]),
            RetrievalQuery("q2", QINGQING, "two", ["b", "c"]),
        ],
    )

    def search(_character_id, text, _limit):
        if text == "one":
            return [_memory("x"), _memory("a")]
        return [_memory("c")]

    report = evaluate_retrieval(
        benchmark,
        search,
        mode="controlled",
        k=4,
    )

    assert report.hit_rate == 1.0
    assert report.recall == 0.75
    assert report.mrr == 0.75
    assert report.ndcg == pytest.approx(0.622, abs=0.01)
    assert report.irrelevant_rate == pytest.approx(1 / 3)
    assert report.results[0].first_relevant_rank == 2
    assert report.passed(min_hit_rate=1.0, min_mrr=0.7, min_ndcg=0.6)


def test_seed_benchmark_writes_stable_source_ids(tmp_path):
    store = SQLiteWorldStore(tmp_path / "world.sqlite3")
    session_id = store.create_session(
        build_snapshot(),
        default_actor_id=NIGHT,
        world_package_id="benchmark",
    )
    benchmark = RetrievalBenchmark(
        name="seed",
        documents=[
            RetrievalDocument("doc-a", QINGQING, "第一段记忆"),
            RetrievalDocument("doc-b", QINGQING, "第二段记忆"),
        ],
        queries=[
            RetrievalQuery("query", QINGQING, "记忆", ["doc-a"]),
        ],
    )

    written = seed_retrieval_benchmark(store, session_id, benchmark)
    records = store.list_character_memories(session_id)

    assert written == 2
    assert [item.source_event_id for item in records] == ["doc-a", "doc-b"]
