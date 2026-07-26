"""长期记忆检索的离线评测指标、基准加载与发布门禁。"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List

from .persistence import MemoryRecord, PersistenceError
from .storage import WorldStore


@dataclass(frozen=True)
class RetrievalDocument:
    document_id: str
    character_id: str
    content: str
    importance: float = 0.7


@dataclass(frozen=True)
class RetrievalQuery:
    query_id: str
    character_id: str
    text: str
    relevant_document_ids: List[str]


@dataclass(frozen=True)
class RetrievalBenchmark:
    name: str
    documents: List[RetrievalDocument]
    queries: List[RetrievalQuery]


@dataclass(frozen=True)
class QueryRetrievalResult:
    query_id: str
    relevant_document_ids: List[str]
    retrieved_document_ids: List[str]
    first_relevant_rank: int
    recall: float
    reciprocal_rank: float
    ndcg: float
    latency_ms: float


@dataclass
class RetrievalReport:
    benchmark_name: str
    mode: str
    k: int
    query_count: int
    hit_rate: float
    recall: float
    mrr: float
    ndcg: float
    irrelevant_rate: float
    p50_latency_ms: float
    p95_latency_ms: float
    results: List[QueryRetrievalResult] = field(default_factory=list)

    def passed(
        self,
        *,
        min_hit_rate: float = 0.85,
        min_mrr: float = 0.65,
        min_ndcg: float = 0.70,
    ) -> bool:
        return (
            self.hit_rate >= min_hit_rate
            and self.mrr >= min_mrr
            and self.ndcg >= min_ndcg
        )

    def summary(self) -> str:
        return (
            f"{self.mode}: Hit@{self.k}={self.hit_rate:.3f}, "
            f"Recall@{self.k}={self.recall:.3f}, "
            f"MRR={self.mrr:.3f}, nDCG@{self.k}={self.ndcg:.3f}, "
            f"irrelevant={self.irrelevant_rate:.3f}, "
            f"P95={self.p95_latency_ms:.1f}ms"
        )

    def to_dict(self, *, include_results: bool = True) -> Dict[str, Any]:
        payload = asdict(self)
        if not include_results:
            payload.pop("results", None)
        return payload


SearchFunction = Callable[[str, str, int], List[MemoryRecord]]


def load_retrieval_benchmark(path: Path) -> RetrievalBenchmark:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PersistenceError(f"读取记忆检索基准失败: {exc}") from exc
    try:
        documents = [
            RetrievalDocument(
                document_id=str(item["id"]).strip(),
                character_id=str(item["character_id"]).strip(),
                content=str(item["content"]).strip(),
                importance=float(item.get("importance", 0.7)),
            )
            for item in raw["documents"]
        ]
        queries = [
            RetrievalQuery(
                query_id=str(item["id"]).strip(),
                character_id=str(item["character_id"]).strip(),
                text=str(item["text"]).strip(),
                relevant_document_ids=[
                    str(value).strip()
                    for value in item["relevant"]
                ],
            )
            for item in raw["queries"]
        ]
        benchmark = RetrievalBenchmark(
            name=str(raw["name"]).strip(),
            documents=documents,
            queries=queries,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PersistenceError(f"记忆检索基准格式无效: {exc}") from exc
    _validate_benchmark(benchmark)
    return benchmark


def _validate_benchmark(benchmark: RetrievalBenchmark) -> None:
    if not benchmark.name or not benchmark.documents or not benchmark.queries:
        raise PersistenceError("记忆检索基准名称、文档和查询均不能为空")
    document_ids = [item.document_id for item in benchmark.documents]
    query_ids = [item.query_id for item in benchmark.queries]
    if any(not value for value in document_ids + query_ids):
        raise PersistenceError("记忆检索基准 ID 不能为空")
    if len(set(document_ids)) != len(document_ids):
        raise PersistenceError("记忆检索基准包含重复文档 ID")
    if len(set(query_ids)) != len(query_ids):
        raise PersistenceError("记忆检索基准包含重复查询 ID")
    documents = {item.document_id: item for item in benchmark.documents}
    for document in benchmark.documents:
        if not document.character_id or not document.content:
            raise PersistenceError("基准文档角色与内容不能为空")
        if not 0.0 <= document.importance <= 1.0:
            raise PersistenceError("基准文档重要度必须在 0 到 1 之间")
    for query in benchmark.queries:
        if (
            not query.character_id
            or not query.text
            or not query.relevant_document_ids
        ):
            raise PersistenceError("基准查询角色、文本和正确答案不能为空")
        for document_id in query.relevant_document_ids:
            document = documents.get(document_id)
            if document is None:
                raise PersistenceError(
                    f"查询 {query.query_id} 引用未知文档: {document_id}"
                )
            if document.character_id != query.character_id:
                raise PersistenceError(
                    f"查询 {query.query_id} 的答案跨越角色记忆作用域"
                )


def seed_retrieval_benchmark(
    store: WorldStore,
    session_id: str,
    benchmark: RetrievalBenchmark,
) -> int:
    for world_version, document in enumerate(benchmark.documents, start=1):
        store.record_character_memories(
            session_id,
            [document.character_id],
            source_event_id=document.document_id,
            world_version=world_version,
            content=document.content,
            importance=document.importance,
            memory_type="benchmark",
        )
    return len(benchmark.documents)


def evaluate_retrieval(
    benchmark: RetrievalBenchmark,
    search: SearchFunction,
    *,
    mode: str,
    k: int = 4,
) -> RetrievalReport:
    if k < 1 or k > 20:
        raise ValueError("检索评测 k 必须在 1 到 20 之间")
    results = []
    total_retrieved = 0
    total_relevant_retrieved = 0
    for query in benchmark.queries:
        started = time.perf_counter()
        memories = search(query.character_id, query.text, k)
        latency_ms = (time.perf_counter() - started) * 1000.0
        retrieved = [item.source_event_id for item in memories]
        relevant = set(query.relevant_document_ids)
        matched = [
            rank
            for rank, document_id in enumerate(retrieved, start=1)
            if document_id in relevant
        ]
        first_rank = matched[0] if matched else 0
        recall = len(set(retrieved) & relevant) / len(relevant)
        reciprocal_rank = 1.0 / first_rank if first_rank else 0.0
        dcg = sum(
            1.0 / math.log2(rank + 1)
            for rank, document_id in enumerate(retrieved, start=1)
            if document_id in relevant
        )
        ideal_count = min(len(relevant), k)
        ideal_dcg = sum(
            1.0 / math.log2(rank + 1)
            for rank in range(1, ideal_count + 1)
        )
        ndcg = dcg / ideal_dcg if ideal_dcg else 0.0
        total_retrieved += len(retrieved)
        total_relevant_retrieved += sum(
            document_id in relevant
            for document_id in retrieved
        )
        results.append(
            QueryRetrievalResult(
                query_id=query.query_id,
                relevant_document_ids=query.relevant_document_ids,
                retrieved_document_ids=retrieved,
                first_relevant_rank=first_rank,
                recall=recall,
                reciprocal_rank=reciprocal_rank,
                ndcg=ndcg,
                latency_ms=latency_ms,
            )
        )
    count = len(results)
    latencies = sorted(item.latency_ms for item in results)
    return RetrievalReport(
        benchmark_name=benchmark.name,
        mode=mode,
        k=k,
        query_count=count,
        hit_rate=sum(item.first_relevant_rank > 0 for item in results) / count,
        recall=sum(item.recall for item in results) / count,
        mrr=sum(item.reciprocal_rank for item in results) / count,
        ndcg=sum(item.ndcg for item in results) / count,
        irrelevant_rate=(
            1.0 - total_relevant_retrieved / total_retrieved
            if total_retrieved
            else 1.0
        ),
        p50_latency_ms=_percentile(latencies, 0.50),
        p95_latency_ms=_percentile(latencies, 0.95),
        results=results,
    )


def evaluate_store(
    store: WorldStore,
    session_id: str,
    benchmark: RetrievalBenchmark,
    *,
    mode: str,
    k: int = 4,
) -> RetrievalReport:
    return evaluate_retrieval(
        benchmark,
        lambda character_id, query, limit: store.search_character_memories(
            session_id,
            character_id,
            query,
            limit=limit,
        ),
        mode=mode,
        k=k,
    )


def _percentile(values: List[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction
