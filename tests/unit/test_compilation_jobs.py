"""SQLite 编译任务、缓存、断点续跑和自动审核测试。"""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from compiler import (
    CacheAwareExtractor,
    CompilationBudgetExceeded,
    CompilationJobConflict,
    CompilationJobRunner,
    CompilationJobStore,
    RawEntity,
    RawEvent,
    SceneExtraction,
)
from compiler.job_runner import _scene_cache_key
from compiler.worker import CompilationWorker
from engine import LLMTrajectoryEvaluator, WorldPackageStore
from web.auth import AuthStore


NOVEL = """第1章 初见

夜轻歌在雨夜醒来，并决定查明自己的身份。

第2章 追查

夜轻歌找到第一条线索，继续推进调查。
"""


class FakeExtractor:
    model = "fake-compiler-model"
    last_error = ""

    def __init__(self, store=None, job_id=""):
        self.store = store
        self.job_id = job_id
        self.calls = 0

    def extract(self, text, *, scene_id, **kwargs):
        self.calls += 1
        if self.store and self.calls == 1:
            self.store.request_pause(self.job_id)
        return SceneExtraction(
            scene_id=scene_id,
            summary=f"{scene_id} 推进调查",
            entities=[
                RawEntity(
                    raw_name="夜轻歌",
                    global_identity="soul_night",
                    evidence="夜轻歌出现",
                )
            ],
            events=[
                RawEvent(
                    summary="夜轻歌推进调查",
                    actor_names=["夜轻歌"],
                )
            ],
        )


class BudgetedFakeExtractor(FakeExtractor):
    def __init__(self, store, job_id):
        super().__init__()
        self.budget_store = store
        self.job_id = job_id

    def extract(self, text, *, scene_id, **kwargs):
        self.budget_store.reserve_llm_call(self.job_id)
        return super().extract(text, scene_id=scene_id, **kwargs)


class FakeQualityReport:
    passed = True
    overall_score = 0.88

    def dict(self):
        return {
            "event_count": 2,
            "chunk_count": 1,
            "overall_score": self.overall_score,
            "passed": self.passed,
        }


class FakeEvaluator:
    def evaluate(self, events, *, final_state):
        assert events
        assert final_state.characters
        return FakeQualityReport()


def _create_job(
    store,
    novel_path,
    package_id="compiled_book",
    model="fake-compiler-model",
):
    return store.create_job(
        package_id=package_id,
        novel_path=str(novel_path),
        novel_name="测试全书",
        chapters=[],
        timeline_plan={1: "origin", 2: "novel"},
        volume_size=1,
        prompt_version="test-v1",
        model=model,
    )


def test_job_store_pause_resume_cancel_and_progress(tmp_path):
    store = CompilationJobStore(tmp_path / "compiler.sqlite3")
    job = _create_job(store, tmp_path / "novel.txt")
    store.prepare_chapters(
        job.job_id,
        [
            {"index": 1, "heading": "第一章"},
            {"index": 2, "heading": "第二章"},
        ],
    )
    store.start_run(job.job_id)
    paused = store.request_pause(job.job_id)
    assert paused.pause_requested is True
    assert store.stop_reason(job.job_id) == "paused"
    store.mark_stopped(job.job_id, "paused")
    resumed = store.resume(job.job_id)
    assert resumed.status == "queued"
    cancelled = store.request_cancel(job.job_id)
    assert cancelled.status == "cancelled"


def test_running_job_is_recovered_as_paused_after_restart(tmp_path):
    database = tmp_path / "compiler.sqlite3"
    store = CompilationJobStore(database)
    job = _create_job(store, tmp_path / "novel.txt")
    store.start_run(job.job_id)

    reopened = CompilationJobStore(database)
    recovered = reopened.get_job(job.job_id)

    assert recovered.status == "paused"
    assert "服务重启" in recovered.error


def test_runner_resumes_from_scene_cache_and_sends_package_to_review(tmp_path):
    novel_path = tmp_path / "novel.txt"
    novel_path.write_text(NOVEL, encoding="utf-8")
    store = CompilationJobStore(tmp_path / "compiler.sqlite3")
    package_store = WorldPackageStore(tmp_path / "worlds")
    first_job = _create_job(store, novel_path)

    pausing_extractor = FakeExtractor(store, first_job.job_id)
    runner = CompilationJobRunner(
        store,
        package_store=package_store,
        extractor_factory=lambda job: pausing_extractor,
        quality_evaluator_factory=lambda: FakeEvaluator(),
    )
    paused = runner.run(first_job.job_id)

    assert paused.status == "paused"
    assert paused.completed_chapters == 1
    assert store.list_chapters(first_job.job_id)[0]["cache_misses"] == 1

    store.resume(first_job.job_id)
    resumed_extractor = FakeExtractor()
    runner.extractor_factory = lambda job: resumed_extractor
    completed = runner.run(first_job.job_id)

    assert completed.status == "completed"
    assert completed.progress == 1.0
    assert completed.quality_status == "passed"
    assert completed.quality_score == 0.88
    assert resumed_extractor.calls == 1
    chapters = store.list_chapters(first_job.job_id)
    assert chapters[0]["cache_hits"] == 0
    assert chapters[0]["extraction_count"] == 1
    assert chapters[1]["cache_misses"] == 1
    assert {
        item["level"]
        for item in store.list_snapshots(first_job.job_id)
    } == {"chapter_start", "chapter", "volume", "book"}
    start_snapshots = [
        item
        for item in store.list_snapshots(first_job.job_id)
        if item["level"] == "chapter_start"
    ]
    assert [item["chapter_start"] for item in start_snapshots] == [1, 2]

    package = package_store.get("compiled_book")
    assert package.review_status == "pending_review"
    assert package.manifest["compiler"]["stage"] == "D"
    assert package.manifest["compiler"]["quality_gate"]["passed"] is True


def test_default_model_resume_uses_canonical_and_legacy_cache_keys(tmp_path):
    store = CompilationJobStore(tmp_path / "compiler.sqlite3")
    delegate = FakeExtractor()
    source_text = "夜轻歌在雨夜醒来。"
    source_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    scene_id = "ch0001_sc01"
    chapter_hint = "第1章 初见"
    extraction = SceneExtraction(
        scene_id=scene_id,
        summary="夜轻歌醒来",
    )
    legacy_key = _scene_cache_key(
        source_hash=source_hash,
        prompt_version="test-v1",
        model="default",
        scene_id=scene_id,
        chapter_hint=chapter_hint,
        known_entities={},
    )
    store.put_cache(
        cache_key=legacy_key,
        source_hash=source_hash,
        prompt_version="test-v1",
        model="fake-compiler-model",
        scene_id=scene_id,
        extraction=extraction.dict(),
    )

    extractor = CacheAwareExtractor(
        store,
        delegate=delegate,
        prompt_version="test-v1",
        model="default",
    )
    restored = extractor.extract(
        source_text,
        scene_id=scene_id,
        chapter_hint=chapter_hint,
        known_entities={},
    )

    assert extractor.model == "fake-compiler-model"
    assert restored.summary == "夜轻歌醒来"
    assert delegate.calls == 0
    assert extractor.chapter_stats(1) == {"hits": 1, "misses": 0}
    canonical_key = _scene_cache_key(
        source_hash=source_hash,
        prompt_version="test-v1",
        model="fake-compiler-model",
        scene_id=scene_id,
        chapter_hint=chapter_hint,
        known_entities={},
    )
    assert store.get_cache(canonical_key)["summary"] == "夜轻歌醒来"


def test_scene_cache_is_shared_between_jobs(tmp_path):
    novel_path = tmp_path / "novel.txt"
    novel_path.write_text(NOVEL, encoding="utf-8")
    store = CompilationJobStore(tmp_path / "compiler.sqlite3")
    package_store = WorldPackageStore(tmp_path / "worlds")
    runner = CompilationJobRunner(
        store,
        package_store=package_store,
        extractor_factory=lambda job: FakeExtractor(),
        quality_evaluator_factory=lambda: FakeEvaluator(),
    )
    first = _create_job(store, novel_path, "compiled_first")
    second = _create_job(store, novel_path, "compiled_second")

    assert runner.run(first.job_id).status == "completed"
    assert runner.run(second.job_id).status == "completed"
    second_chapters = store.list_chapters(second.job_id)
    assert sum(item["cache_hits"] for item in second_chapters) == 2
    assert sum(item["cache_misses"] for item in second_chapters) == 0


def test_llm_budget_is_atomic_and_pauses_with_cache_preserved(tmp_path):
    novel_path = tmp_path / "novel.txt"
    novel_path.write_text(NOVEL, encoding="utf-8")
    store = CompilationJobStore(tmp_path / "compiler.sqlite3")
    job = store.create_job(
        package_id="budgeted_book",
        novel_path=str(novel_path),
        novel_name="预算测试",
        chapters=[],
        prompt_version="test-v1",
        model="fake-compiler-model",
        max_llm_calls=1,
    )
    runner = CompilationJobRunner(
        store,
        extractor_factory=lambda current: BudgetedFakeExtractor(
            store,
            current.job_id,
        ),
    )

    paused = runner.run(job.job_id)

    assert paused.status == "paused"
    assert paused.llm_calls_used == 1
    assert paused.max_llm_calls == 1
    assert "预算已用完" in paused.error
    assert store.list_chapters(job.job_id)[0]["status"] == "completed"
    with pytest.raises(CompilationBudgetExceeded):
        store.reserve_llm_call(job.job_id)


def test_budget_can_be_extended_without_resetting_usage(tmp_path):
    novel_path = tmp_path / "novel.txt"
    novel_path.write_text(NOVEL, encoding="utf-8")
    store = CompilationJobStore(tmp_path / "compiler.sqlite3")
    job = store.create_job(
        package_id="budget_extension",
        novel_path=str(novel_path),
        prompt_version="test-v1",
        max_llm_calls=2,
    )

    store.reserve_llm_call(job.job_id)
    extended = store.increase_llm_budget(job.job_id, 3)

    assert extended.max_llm_calls == 5
    assert extended.llm_calls_used == 1
    with pytest.raises(ValueError):
        store.increase_llm_budget(job.job_id, 1.5)


def test_retry_requires_transient_failure_and_honors_limit(tmp_path):
    novel_path = tmp_path / "novel.txt"
    novel_path.write_text(NOVEL, encoding="utf-8")
    store = CompilationJobStore(tmp_path / "compiler.sqlite3")
    job = store.create_job(
        package_id="retryable",
        novel_path=str(novel_path),
        prompt_version="test-v1",
        max_retries=1,
    )
    store.start_run(job.job_id)
    failed = store.mark_failed(
        job.job_id,
        "connection reset",
        failure_kind="transient",
        retryable=True,
    )
    assert failed.retry_count == 0

    queued = store.retry_failed(job.job_id)
    assert queued.status == "queued"
    assert queued.retry_count == 1
    store.start_run(job.job_id)
    store.mark_failed(
        job.job_id,
        "connection reset again",
        failure_kind="transient",
        retryable=True,
    )
    with pytest.raises(CompilationJobConflict, match="最大重试"):
        store.retry_failed(job.job_id)


def test_permanent_failure_cannot_resume_or_retry(tmp_path):
    novel_path = tmp_path / "novel.txt"
    novel_path.write_text(NOVEL, encoding="utf-8")
    store = CompilationJobStore(tmp_path / "compiler.sqlite3")
    job = store.create_job(
        package_id="permanent",
        novel_path=str(novel_path),
        prompt_version="test-v1",
    )
    store.start_run(job.job_id)
    store.mark_failed(job.job_id, "schema invalid", failure_kind="permanent")
    with pytest.raises(Exception, match="不可重试"):
        store.resume(job.job_id)
    with pytest.raises(Exception, match="不可重试"):
        store.retry_failed(job.job_id)


def test_expired_lease_and_old_token_are_fenced(tmp_path):
    novel_path = tmp_path / "novel.txt"
    novel_path.write_text(NOVEL, encoding="utf-8")
    store = CompilationJobStore(tmp_path / "compiler.sqlite3")
    job = store.create_job(
        package_id="fenced",
        novel_path=str(novel_path),
        prompt_version="test-v1",
    )
    claimed = store.claim_next_job("worker-a", lease_seconds=30)
    assert claimed is not None
    with store._connect() as conn:
        conn.execute(
            "UPDATE compiler_jobs SET lease_expires_at = ? WHERE job_id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), job.job_id),
        )
    with pytest.raises(Exception, match="租约"):
        store.heartbeat(job.job_id, "worker-a", execution_token=claimed.execution_token)


def test_external_worker_claims_job_with_lease_and_completes(tmp_path):
    novel_path = tmp_path / "novel.txt"
    novel_path.write_text(NOVEL, encoding="utf-8")
    store = CompilationJobStore(tmp_path / "compiler.sqlite3")
    package_store = WorldPackageStore(tmp_path / "worlds")
    audit_store = AuthStore(tmp_path / "auth.sqlite3")
    job = _create_job(store, novel_path, "worker_compiled")
    runner = CompilationJobRunner(
        store,
        package_store=package_store,
        extractor_factory=lambda current: FakeExtractor(),
        quality_evaluator_factory=lambda: FakeEvaluator(),
    )
    worker = CompilationWorker(
        store,
        runner,
        worker_id="worker-a",
        lease_seconds=60,
        poll_seconds=0.1,
        audit_store=audit_store,
    )

    claimed = store.claim_next_job("worker-b", lease_seconds=60)
    assert claimed.job_id == job.job_id
    assert store.is_worker_active(job.job_id) is True
    assert store.claim_next_job("worker-a", lease_seconds=60) is None
    store.mark_failed(
        job.job_id,
        "归还测试任务",
        failure_kind="transient",
        retryable=True,
    )
    store.resume(job.job_id)

    completed = worker.run_once()

    assert completed.job_id == job.job_id
    assert completed.status == "completed"
    assert completed.worker_id == ""
    assert completed.attempt_count == 2
    assert package_store.get("worker_compiled").review_status == "pending_review"
    assert {
        event["action"]
        for event in audit_store.list_audit(limit=10)
    } >= {
        "compiler_job.completed",
        "world_package.review.pending_review",
    }


@pytest.mark.llm
def test_real_compilation_job_runs_quality_gate_and_creates_review_draft(
    tmp_path,
):
    novel_path = tmp_path / "real_job_novel.txt"
    novel_path.write_text(
        """第1章 毒茶线索

夜轻歌发现宴席毒茶，决定查出幕后主使。她看到夜清清袖口沾着药粉，将其作为待查线索。

第2章 真相揭晓

夜轻歌确认夜清清就是毒茶主使，公开袖口药粉证据，完成查明幕后主使的目标。
""",
        encoding="utf-8",
    )
    store = CompilationJobStore(tmp_path / "compiler.sqlite3")
    package_store = WorldPackageStore(tmp_path / "worlds")
    job = _create_job(
        store,
        novel_path,
        "real_compiled_book",
        model="",
    )
    runner = CompilationJobRunner(
        store,
        package_store=package_store,
        quality_evaluator_factory=lambda: LLMTrajectoryEvaluator(
            chunk_size=10,
        ),
    )

    completed = runner.run(job.job_id)

    assert completed.status == "completed"
    assert completed.quality_status in {"passed", "failed"}
    assert completed.quality_score is not None
    package = package_store.get("real_compiled_book")
    assert package.manifest["compiler"]["stage"] == "D"
    assert package.manifest["compiler"]["quality_gate"]["status"] in {
        "passed",
        "failed",
    }
    assert package.review_status == (
        "pending_review"
        if completed.quality_status == "passed"
        else "draft"
    )
