"""把完成的全书编译任务发布为运行时章节入口。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from engine.chapter_catalog import ChapterCatalogStore, ChapterEntry
from engine.event import state_hash
from engine.world_packages import WorldPackageNotFound, WorldPackageStore
from world_schema import WorldState

from .job_store import CompilationJob, CompilationJobStore
from .scene_compiler import EntityRegistry, PackageBuilder


class ChapterPublishError(RuntimeError):
    """章节运行时入口发布失败。"""


class ChapterRuntimePublisher:
    """将编译快照、正文缓存和运行包绑定为章节入口。"""

    def _preflight(
        self,
        *,
        job,
        job_store: CompilationJobStore,
        novel_name: str,
    ) -> tuple[str, List[Dict[str, Any]]]:
        current = job_store.get_job(job.job_id)
        if current.status != "completed":
            raise ChapterPublishError(
                f"任务 {current.job_id} 尚未完成，不能发布章节入口: {current.status}"
            )
        book_id = str(current.book_id or "").strip()
        if not book_id:
            raise ChapterPublishError("编译任务缺少 book_id，不能发布章节入口")
        if current.quality_status != "passed":
            raise ChapterPublishError(
                f"质量门禁未通过，不能发布章节入口: {current.quality_status}"
            )
        if not isinstance(current.quality_report, dict) or (
            current.quality_report.get("passed") is not True
        ):
            raise ChapterPublishError("质量报告未确认 passed，不能发布章节入口")
        if not current.source_hash:
            raise ChapterPublishError("编译任务缺少源文件指纹，不能发布章节入口")
        try:
            source_hash = hashlib.sha256(
                Path(current.novel_path).read_bytes()
            ).hexdigest()
        except OSError as exc:
            raise ChapterPublishError("读取编译源文件失败") from exc
        if source_hash != current.source_hash:
            raise ChapterPublishError("编译源文件已变化，拒绝发布旧世界状态")
        snapshots = job_store.list_snapshots(current.job_id, include_state=True)
        starts = {
            int(item["chapter_start"]): item
            for item in snapshots
            if item.get("level") == "chapter_start"
        }
        completed = sorted(
            int(item["chapter_index"])
            for item in job_store.list_chapters(current.job_id)
            if item.get("status") == "completed"
        )
        eligible = self._continuous_prefix(completed)
        if not eligible:
            return book_id, []
        full_package_id = str(current.result_package_id or current.package_id or "")
        if not full_package_id:
            raise ChapterPublishError("编译任务缺少全书世界包")
        try:
            full_package = self.package_store.get(full_package_id)
        except Exception as exc:
            raise ChapterPublishError(
                f"全书世界包不存在或无效: {full_package_id}"
            ) from exc
        compiler_meta = dict(full_package.manifest.get("compiler") or {})
        if compiler_meta.get("book_id") != current.book_id:
            raise ChapterPublishError("全书世界包 book_id 与编译任务不一致")
        if compiler_meta.get("source_hash") != current.source_hash:
            raise ChapterPublishError("全书世界包源文件指纹与编译任务不一致")
        if current.chapters:
            expected = set(int(item) for item in current.chapters)
            if not expected.issubset(set(completed)):
                raise ChapterPublishError("任务仍有目标章节未完成，不能发布")
        plan: List[Dict[str, Any]] = []
        for chapter_number in eligible:
            snapshot = starts.get(chapter_number)
            if snapshot is None:
                raise ChapterPublishError(
                    f"章节 {chapter_number} 缺少 chapter_start snapshot"
                )
            expected_id = f"chapter_{chapter_number:06d}_start"
            if str(snapshot.get("snapshot_id")) != expected_id:
                raise ChapterPublishError(
                    f"章节 {chapter_number} 起始快照 ID 不一致"
                )
            if (
                int(snapshot.get("chapter_start", 0)) != chapter_number
                or int(snapshot.get("chapter_end", 0)) != chapter_number
            ):
                raise ChapterPublishError(
                    f"章节 {chapter_number} 起始快照范围不一致"
                )
            state_payload = snapshot.get("state")
            if not isinstance(state_payload, dict):
                raise ChapterPublishError(f"章节 {chapter_number} 起始快照状态缺失")
            state = WorldState.parse_obj(state_payload)
            if not snapshot.get("state_hash") or state_hash(state) != str(
                snapshot.get("state_hash")
            ):
                raise ChapterPublishError(
                    f"章节 {chapter_number} 起始快照 state_hash 校验失败"
                )
            plan.append(
                {
                    "chapter_number": chapter_number,
                    "snapshot": snapshot,
                    "state": state,
                    "package_id": self._package_id(
                        current.package_id, chapter_number
                    ),
                }
            )
        return book_id, plan

    def __init__(
        self,
        catalog: ChapterCatalogStore,
        package_store: WorldPackageStore,
    ):
        self.catalog = catalog
        self.package_store = package_store

    def publish(
        self,
        *,
        job: CompilationJob,
        job_store: CompilationJobStore,
        registry: EntityRegistry,
        novel_name: str,
    ) -> List[ChapterEntry]:
        current = job_store.get_job(job.job_id)
        book_id, plan = self._preflight(
            job=current,
            job_store=job_store,
            novel_name=novel_name,
        )
        if not plan:
            return []
        existing_book = self.catalog.get_book(book_id)
        if existing_book is not None and existing_book.source_hash != current.source_hash:
            raise ChapterPublishError("章节目录源文件指纹与编译任务不一致")
        self.catalog.import_book(
            book_id=book_id,
            novel=novel_name,
            source_path=current.novel_path,
        )
        entries = {
            entry.chapter_number: entry
            for entry in self.catalog.list_entries(book_id)
        }
        for item in plan:
            entry = entries.get(item["chapter_number"])
            if entry is None:
                raise ChapterPublishError(
                    f"章节 {item['chapter_number']} 缺少目录入口"
                )
            item["entry"] = entry
            if entry.book_id != book_id or (
                entry.chapter_start != item["chapter_number"]
                or entry.chapter_end != item["chapter_number"]
            ):
                raise ChapterPublishError(
                    f"章节 {item['chapter_number']} 目录入口范围不一致"
                )
            if entry.entry_status == "published" and not entry.canonical:
                if entry.package_id != item["package_id"] or entry.snapshot_id != str(
                    item["snapshot"]["snapshot_id"]
                ):
                    raise ChapterPublishError(
                        f"章节 {item['chapter_number']} 已发布入口绑定不一致"
                    )
        published: List[ChapterEntry] = []
        for item in plan:
            chapter_number = item["chapter_number"]
            snapshot = item["snapshot"]
            state = item["state"]
            entry = item["entry"]
            package_id = item["package_id"]
            if entry.entry_status == "published" and entry.canonical:
                published.append(entry)
                continue
            package = PackageBuilder().build(
                package_id=package_id,
                novel=novel_name,
                source_chapters=[chapter_number],
                state=state,
                registry=registry,
                compiler_metadata={
                    "stage": "chapter_runtime",
                    "book_id": book_id,
                    "source_hash": current.source_hash,
                    "entry_id": entry.entry_id,
                    "chapter_start": chapter_number,
                    "chapter_end": chapter_number,
                    "snapshot_id": snapshot["snapshot_id"],
                    "compiler_job_id": current.job_id,
                },
            )
            self._save_package(package)
            manifest = {
                "title": entry.title,
                "description": f"{novel_name} 第{chapter_number}章的编译起始世界状态",
                "progress_flags": [],
            }
            first_character = next(iter(state.characters.values()), None)
            published.append(
                self.catalog.publish_entry(
                    entry.entry_id,
                    package_id=package_id,
                    snapshot_id=str(snapshot["snapshot_id"]),
                    mission=manifest,
                    identity=(first_character.display_name if first_character else ""),
                    character_summary=[
                        item.display_name for item in state.characters.values()
                    ][:12],
                    location_summary=[
                        item.display_name for item in state.locations.values()
                    ][:12],
                    compiler_version=str(current.prompt_version or "compiled"),
                )
            )
        return published

    @staticmethod
    def _continuous_prefix(chapters: Iterable[int]) -> List[int]:
        values = sorted({int(item) for item in chapters if int(item) > 0})
        result: List[int] = []
        expected = 1
        for chapter in values:
            if chapter != expected:
                break
            result.append(chapter)
            expected += 1
        return result

    @staticmethod
    def _package_id(base: str, chapter_number: int) -> str:
        candidate = f"{base}_chapter_{chapter_number}"
        if len(candidate) <= 64 and re.fullmatch(r"[a-z][a-z0-9_-]*", candidate):
            return candidate
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        prefix = re.sub(r"[^a-z0-9_-]", "-", base.lower()).strip("-") or "compiled"
        return f"{prefix[:48]}_{digest[:10]}"

    def _save_package(self, package) -> None:
        payload = json.loads(package.to_json())
        payload.update(
            {
                "scenario": f"{package.novel} · 第{package.source_chapters[0]}章",
                "anchor": "章节开始前的编译世界状态",
                "default_actor_id": next(iter(package.snapshot.characters), ""),
                "manifest": {
                    **dict(payload.get("manifest") or {}),
                    "entry_kind": "compiled_chapter",
                    "chapter_start": package.source_chapters[0],
                    "chapter_end": package.source_chapters[-1],
                },
            }
        )
        try:
            current = self.package_store.get(package.package_id)
        except WorldPackageNotFound:
            current = None
        if current is not None:
            unchanged = (
                current.novel == package.novel
                and current.source_chapters == list(package.source_chapters)
                and current.snapshot == package.snapshot
                and current.manifest.get("compiler") == (
                    payload.get("manifest") or {}
                ).get("compiler")
                and current.manifest.get("entry_kind") == (
                    payload.get("manifest") or {}
                ).get("entry_kind")
                and current.manifest.get("chapter_start") == (
                    payload.get("manifest") or {}
                ).get("chapter_start")
                and current.manifest.get("chapter_end") == (
                    payload.get("manifest") or {}
                ).get("chapter_end")
                and current.scenario == payload["scenario"]
                and current.anchor == payload["anchor"]
                and current.default_actor_id == payload["default_actor_id"]
            )
            if unchanged:
                return
        expected_revision = current.revision if current is not None else None
        self.package_store.save(
            package.package_id,
            payload,
            expected_revision=expected_revision,
        )
