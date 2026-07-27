"""可暂停、可恢复的小说编译任务运行器。"""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, Optional

from engine.llm_trajectory_eval import LLMTrajectoryEvaluator
from engine.world_packages import (
    WorldPackageNotFound,
    WorldPackageStore,
)

from .book_compiler import BookCompileResult, BookCompiler
from .cli import _fresh_state, _guess_novel_name
from .extractors import EntityExtractor, SceneExtraction
from .job_store import (
    CompilationBudgetExceeded,
    CompilationJob,
    CompilationJobConflict,
    CompilationJobStore,
)
from .scene_compiler import EntityRegistry, PackageBuilder
from .text_loader import load_novel, split_chapters


EXTRACTOR_PROMPT_VERSION = "book-d-2026-07-27-v1"


class CacheAwareExtractor:
    """以场景内容和模型/Prompt 版本为键复用 LLM 抽取结果。"""

    def __init__(
        self,
        store: CompilationJobStore,
        *,
        delegate=None,
        prompt_version: str = EXTRACTOR_PROMPT_VERSION,
        model: str = "",
        job_id: str = "",
    ):
        self.store = store
        self.delegate = delegate
        self.prompt_version = prompt_version
        self.model = model or getattr(delegate, "model", "") or "default"
        self.job_id = job_id
        self.last_error = ""
        self._chapter_stats: Dict[int, Dict[str, int]] = {}

    def _get_delegate(self):
        if self.delegate is None:
            self.delegate = EntityExtractor(
                model=None if self.model == "default" else self.model,
                before_llm_call=(
                    lambda: self.store.reserve_llm_call(self.job_id)
                    if self.job_id
                    else None
                ),
            )
            if self.model == "default":
                self.model = getattr(self.delegate, "model", "default")
        return self.delegate

    def extract(
        self,
        scene_text: str,
        *,
        scene_id: str,
        known_entities=None,
        chapter_hint: str = "",
    ):
        chapter_index = _chapter_from_scene_id(scene_id)
        stats = self._chapter_stats.setdefault(
            chapter_index,
            {"hits": 0, "misses": 0},
        )
        source_hash = hashlib.sha256(
            scene_text.encode("utf-8")
        ).hexdigest()
        material = json.dumps(
            {
                "source_hash": source_hash,
                "prompt_version": self.prompt_version,
                "model": self.model,
                "scene_id": scene_id,
                "chapter_hint": chapter_hint,
                "known_entities": sorted(
                    (known_entities or {}).items()
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        cache_key = hashlib.sha256(material.encode("utf-8")).hexdigest()
        cached = self.store.get_cache(cache_key)
        if cached is not None:
            stats["hits"] += 1
            return SceneExtraction.parse_obj(cached)

        stats["misses"] += 1
        delegate = self._get_delegate()
        extraction = delegate.extract(
            scene_text,
            scene_id=scene_id,
            known_entities=known_entities,
            chapter_hint=chapter_hint,
        )
        self.last_error = getattr(delegate, "last_error", "")
        if extraction is not None:
            self.store.put_cache(
                cache_key=cache_key,
                source_hash=source_hash,
                prompt_version=self.prompt_version,
                model=self.model,
                scene_id=scene_id,
                extraction=extraction.dict(),
            )
        return extraction

    def chapter_stats(self, chapter_index: int) -> Dict[str, int]:
        return dict(
            self._chapter_stats.get(
                chapter_index,
                {"hits": 0, "misses": 0},
            )
        )


def _chapter_from_scene_id(scene_id: str) -> int:
    try:
        return int(scene_id.split("_", 1)[0].replace("ch", ""))
    except (ValueError, IndexError):
        return 0


class CompilationQualityGate:
    """把编译场景事件送入真实长轨迹评分器。"""

    def __init__(
        self,
        evaluator_factory: Optional[Callable[[], LLMTrajectoryEvaluator]],
    ):
        self.evaluator_factory = evaluator_factory

    def evaluate(
        self,
        result: BookCompileResult,
        *,
        final_state,
        before_llm_call=None,
    ) -> Dict:
        if not result.trajectory_events:
            return {
                "status": "failed",
                "passed": False,
                "score": 0.0,
                "error": "编译结果没有可评分的剧情事件",
            }
        if self.evaluator_factory is None:
            return {
                "status": "skipped",
                "passed": False,
                "score": None,
                "error": "未配置真实 LLM 长轨迹评分器",
            }
        try:
            evaluator = self.evaluator_factory()
            if (
                before_llm_call is not None
                and hasattr(evaluator, "before_llm_call")
            ):
                evaluator.before_llm_call = before_llm_call
            report = evaluator.evaluate(
                result.trajectory_events,
                final_state=final_state,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "error",
                "passed": False,
                "score": None,
                "error": str(exc),
            }
        payload = report.dict()
        payload.update(
            {
                "status": "passed" if report.passed else "failed",
                "score": report.overall_score,
            }
        )
        return payload


class CompilationJobRunner:
    """同步执行一个编译任务；线程调度由 Manager 负责。"""

    def __init__(
        self,
        store: CompilationJobStore,
        *,
        package_store: Optional[WorldPackageStore] = None,
        extractor_factory: Optional[Callable[[CompilationJob], object]] = None,
        quality_evaluator_factory: Optional[
            Callable[[], LLMTrajectoryEvaluator]
        ] = None,
    ):
        self.store = store
        self.package_store = package_store
        self.extractor_factory = extractor_factory
        self.quality_gate = CompilationQualityGate(
            quality_evaluator_factory
        )

    def run(
        self,
        job_id: str,
        *,
        worker_id: str = "",
        lease_seconds: int = 120,
    ) -> CompilationJob:
        try:
            job = (
                self.store.claimed_job(job_id, worker_id)
                if worker_id
                else self.store.start_run(job_id)
            )
            if worker_id:
                self.store.heartbeat(
                    job_id,
                    worker_id,
                    lease_seconds=lease_seconds,
                )
            text = load_novel(job.novel_path)
            all_chapters = split_chapters(text)
            targets = set(job.chapters)
            selected = [
                chapter
                for chapter in all_chapters
                if not targets or chapter.index in targets
            ]
            if not selected:
                raise ValueError(
                    f"未找到目标章节 {job.chapters}，全书共 "
                    f"{len(all_chapters)} 章"
                )
            self.store.prepare_chapters(
                job_id,
                [
                    {
                        "index": chapter.index,
                        "heading": chapter.heading,
                    }
                    for chapter in selected
                ],
            )
            delegate = (
                self.extractor_factory(job)
                if self.extractor_factory
                else None
            )
            extractor = CacheAwareExtractor(
                self.store,
                delegate=delegate,
                prompt_version=job.prompt_version,
                model=job.model,
                job_id=job.job_id,
            )
            state = _fresh_state(job.package_id)
            registry = EntityRegistry()
            compiler = BookCompiler(
                extractor=extractor,
                volume_size=job.volume_size,
            )

            def chapter_started(chapter) -> None:
                if worker_id:
                    self.store.heartbeat(
                        job_id,
                        worker_id,
                        lease_seconds=lease_seconds,
                    )
                self.store.set_current_chapter(job_id, chapter.index)

            def chapter_completed(
                chapter,
                chapter_result,
                snapshot,
            ) -> None:
                stats = extractor.chapter_stats(chapter.index)
                self.store.mark_chapter_completed(
                    job_id,
                    chapter.index,
                    extraction_count=chapter_result.extraction_count,
                    cache_hits=stats["hits"],
                    cache_misses=stats["misses"],
                )
                self.store.save_snapshot(
                    job_id,
                    metadata=snapshot.metadata(),
                    state=snapshot.state.dict(),
                )
                if worker_id:
                    self.store.heartbeat(
                        job_id,
                        worker_id,
                        lease_seconds=lease_seconds,
                    )

            result = compiler.compile(
                selected,
                registry,
                state,
                timeline_plan=job.timeline_plan,
                volume_plan=job.volume_plan,
                stop_requested=lambda: self.store.stop_reason(job_id),
                on_chapter_started=chapter_started,
                on_chapter_completed=chapter_completed,
            )
            for snapshot in result.snapshots:
                self.store.save_snapshot(
                    job_id,
                    metadata=snapshot.metadata(),
                    state=snapshot.state.dict(),
                )
            if result.interrupted:
                return self.store.mark_stopped(
                    job_id,
                    result.interrupted,
                )

            if worker_id:
                self.store.heartbeat(
                    job_id,
                    worker_id,
                    lease_seconds=lease_seconds,
                )
            quality = self.quality_gate.evaluate(
                result,
                final_state=state,
                before_llm_call=lambda: self.store.reserve_llm_call(job_id),
            )
            self.store.set_quality(
                job_id,
                status=quality["status"],
                score=quality.get("score"),
                report=quality,
            )
            compiler_metadata = result.manifest()
            compiler_metadata["quality_gate"] = quality
            package = PackageBuilder().build(
                package_id=job.package_id,
                novel=job.novel_name
                or _guess_novel_name(job.novel_path),
                source_chapters=result.source_chapters,
                state=state,
                registry=registry,
                compiler_metadata=compiler_metadata,
            )
            output_path = self._persist_package(
                package,
                quality=quality,
            )
            return self.store.mark_completed(
                job_id,
                result_package_id=package.package_id,
                output_path=output_path,
            )
        except CompilationBudgetExceeded as exc:
            return self.store.pause_for_budget(job_id, str(exc))
        except CompilationJobConflict:
            raise
        except Exception as exc:  # noqa: BLE001
            return self.store.mark_failed(job_id, str(exc))

    def _persist_package(self, package, *, quality: Dict) -> str:
        if self.package_store is None:
            output = Path(f"worlds/{package.package_id}.json").resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            package.save(str(output))
            return str(output)

        payload = json.loads(package.to_json())
        first_character = next(
            iter(package.snapshot.characters),
            "",
        )
        payload.update(
            {
                "scenario": package.novel,
                "anchor": "全书编译生成，待创作者选择介入锚点",
                "default_actor_id": first_character,
            }
        )
        try:
            current = self.package_store.get(package.package_id)
            expected_revision = current.revision
        except WorldPackageNotFound:
            expected_revision = None
        saved = self.package_store.save(
            package.package_id,
            payload,
            expected_revision=expected_revision,
        )
        if quality.get("passed"):
            saved = self.package_store.transition_review(
                package.package_id,
                "pending_review",
                expected_revision=saved.revision,
                note=(
                    "编译任务自动质量门禁通过，"
                    f"长轨迹评分 {quality.get('score')}"
                ),
            )
        return str(
            self.package_store.directory
            / f"{saved.package_id}.json"
        )


class CompilationJobManager:
    """单进程后台任务调度器。

    默认单 worker，避免 SQLite/Qdrant Local 和模型限流下的并发争用。
    """

    def __init__(
        self,
        runner: CompilationJobRunner,
        *,
        max_workers: int = 1,
    ):
        self.runner = runner
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="novel-compiler",
        )
        self._futures: Dict[str, Future] = {}
        self._lock = threading.Lock()

    def start(self, job_id: str) -> None:
        with self._lock:
            current = self._futures.get(job_id)
            if current is not None and not current.done():
                return
            self._futures[job_id] = self._executor.submit(
                self.runner.run,
                job_id,
            )

    def is_running(self, job_id: str) -> bool:
        with self._lock:
            future = self._futures.get(job_id)
            return future is not None and not future.done()

    def shutdown(self, *, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait)
