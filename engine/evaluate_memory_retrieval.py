"""运行 SQLite FTS5 与 Qdrant 混合记忆检索的中文基准评测。"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

from dotenv import load_dotenv

from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import NIGHT

from .embeddings import CachedMemoryEmbedder, memory_embedder_from_env
from .memory_retrieval_eval import (
    RetrievalReport,
    evaluate_store,
    load_retrieval_benchmark,
    seed_retrieval_benchmark,
)
from .persistence import PersistenceError, SQLiteWorldStore
from .qdrant_memory import (
    HybridRetrievalWeights,
    QdrantBackedWorldStore,
    QdrantMemoryIndex,
)


WEIGHT_CANDIDATES: Dict[str, HybridRetrievalWeights] = {
    "legacy_45_35": HybridRetrievalWeights(
        semantic=0.45,
        lexical=0.35,
        importance=0.15,
        recency=0.05,
    ),
    "balanced_50_30": HybridRetrievalWeights(
        semantic=0.50,
        lexical=0.30,
        importance=0.15,
        recency=0.05,
    ),
    "semantic_60_20": HybridRetrievalWeights(
        semantic=0.60,
        lexical=0.20,
        importance=0.15,
        recency=0.05,
    ),
    "semantic_70_15": HybridRetrievalWeights(
        semantic=0.70,
        lexical=0.15,
        importance=0.10,
        recency=0.05,
    ),
    "semantic_80_10": HybridRetrievalWeights(
        semantic=0.80,
        lexical=0.10,
        importance=0.05,
        recency=0.05,
    ),
    "semantic_90_05": HybridRetrievalWeights(
        semantic=0.90,
        lexical=0.05,
        importance=0.05,
        recency=0.00,
    ),
    "semantic_only": HybridRetrievalWeights(
        semantic=1.00,
        lexical=0.00,
        importance=0.00,
        recency=0.00,
    ),
}


def _quality_key(item: Tuple[str, RetrievalReport]) -> Tuple[float, ...]:
    _, report = item
    return (
        report.hit_rate,
        report.mrr,
        report.ndcg,
        -report.p95_latency_ms,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_dotenv(".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=Path("benchmarks/memory_retrieval_zh.json"),
    )
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--min-hit-rate", type=float, default=0.85)
    parser.add_argument("--min-mrr", type=float, default=0.65)
    parser.add_argument("--min-ndcg", type=float, default=0.70)
    parser.add_argument(
        "--output",
        type=Path,
        help="可选：保存包含逐查询结果的 JSON 报告",
    )
    args = parser.parse_args(argv)

    benchmark = load_retrieval_benchmark(args.benchmark)
    configured = memory_embedder_from_env()
    if configured is None:
        raise PersistenceError(
            "运行混合检索评测必须配置 MEMORY_EMBEDDING_MODEL"
        )
    embedder = CachedMemoryEmbedder(configured)
    embedder.warm(
        [item.content for item in benchmark.documents]
        + [item.text for item in benchmark.queries]
    )

    with tempfile.TemporaryDirectory(prefix="memory-retrieval-eval-") as root:
        root_path = Path(root)
        sqlite = SQLiteWorldStore(root_path / "world.sqlite3")
        session_id = sqlite.create_session(
            build_snapshot(),
            default_actor_id=NIGHT,
            world_package_id="retrieval_benchmark",
            save_name="记忆检索评测",
        )
        seed_retrieval_benchmark(sqlite, session_id, benchmark)
        lexical = evaluate_store(
            sqlite,
            session_id,
            benchmark,
            mode="sqlite_fts5",
            k=args.k,
        )

        index = QdrantMemoryIndex(
            embedder=embedder,
            collection_name="memory_retrieval_benchmark",
            path=root_path / "qdrant",
        )
        try:
            index.upsert(sqlite.list_character_memories(session_id))
            hybrid_reports = {}
            for name, weights in WEIGHT_CANDIDATES.items():
                store = QdrantBackedWorldStore(
                    sqlite,
                    index,
                    weights=weights,
                )
                hybrid_reports[name] = evaluate_store(
                    store,
                    session_id,
                    benchmark,
                    mode=name,
                    k=args.k,
                )
        finally:
            index.close()

    best_name, best = max(hybrid_reports.items(), key=_quality_key)
    print(lexical.summary())
    for report in hybrid_reports.values():
        marker = " <- best" if report.mode == best_name else ""
        print(report.summary() + marker)
    print(
        "embedding batch: "
        f"{embedder.embedded_text_count} texts / "
        f"{embedder.request_count} request(s)"
    )

    payload = {
        "benchmark": benchmark.name,
        "documents": len(benchmark.documents),
        "queries": len(benchmark.queries),
        "embedding": {
            "texts": embedder.embedded_text_count,
            "requests": embedder.request_count,
        },
        "lexical": lexical.to_dict(),
        "hybrid_candidates": {
            name: report.to_dict()
            for name, report in hybrid_reports.items()
        },
        "best": best_name,
    }
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    passed = best.passed(
        min_hit_rate=args.min_hit_rate,
        min_mrr=args.min_mrr,
        min_ndcg=args.min_ndcg,
    )
    if best.hit_rate < lexical.hit_rate:
        passed = False
        print("FAIL: 最佳混合召回的 Hit@K 低于 SQLite FTS5 基线")
    if not passed:
        print(
            "FAIL: 未达到门槛 "
            f"Hit@{args.k}>={args.min_hit_rate:.2f}, "
            f"MRR>={args.min_mrr:.2f}, nDCG>={args.min_ndcg:.2f}"
        )
        return 1
    print(f"PASS: 推荐权重候选 {best_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
