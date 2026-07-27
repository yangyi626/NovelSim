"""多小说真实全书编译基准。

基准分为三步：
1. scan：验证原文指纹和章节/场景结构；
2. enqueue：为每本书创建 SQLite 全书任务；
3. report：汇总 Worker 进度、缓存命中、耗时和质量门禁结果。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .job_runner import EXTRACTOR_PROMPT_VERSION
from .job_store import CompilationJobStore
from .text_loader import load_novel, split_chapters, split_scenes


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = PROJECT_ROOT / "benchmarks" / "novels.json"
DEFAULT_RUN_DIRECTORY = PROJECT_ROOT / "data" / "benchmarks"


def _load_manifest(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("只支持 schema_version=1 的小说基准清单")
    books = payload.get("books")
    if not isinstance(books, list) or len(books) < 2:
        raise ValueError("多小说基准至少需要两本书")
    return payload


def _novel_path(filename: str) -> Path:
    directory = (PROJECT_ROOT / "novels").resolve()
    path = (directory / filename).resolve()
    if path.parent != directory or path.suffix.lower() != ".txt":
        raise ValueError(f"非法小说路径: {filename}")
    if not path.is_file():
        raise ValueError(f"小说不存在: {filename}")
    return path


def scan_book(spec: Dict[str, Any]) -> Dict[str, Any]:
    path = _novel_path(str(spec["filename"]))
    started = time.perf_counter()
    source = path.read_bytes()
    text = load_novel(str(path))
    chapters = split_chapters(text)
    scenes = sum(len(split_scenes(chapter)) for chapter in chapters)
    actual = {
        "bytes": len(source),
        "sha256": hashlib.sha256(source).hexdigest(),
        "characters": len(text),
        "chapters": len(chapters),
        "scenes": scenes,
    }
    expected = dict(spec.get("expected") or {})
    mismatches = {
        key: {"expected": value, "actual": actual.get(key)}
        for key, value in expected.items()
        if actual.get(key) != value
    }
    return {
        "book_id": spec["book_id"],
        "filename": spec["filename"],
        "actual": actual,
        "mismatches": mismatches,
        "passed": not mismatches and actual["chapters"] > 0,
        "scan_seconds": round(time.perf_counter() - started, 3),
    }


def scan_manifest(manifest_path: Path) -> Dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    books = [scan_book(spec) for spec in manifest["books"]]
    return {
        "benchmark": manifest["benchmark"],
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "passed": all(book["passed"] for book in books),
        "books": books,
    }


def enqueue_benchmark(
    manifest_path: Path,
    store: CompilationJobStore,
    *,
    model: str = "",
    smoke_chapters: int = 0,
    run_directory: Path = DEFAULT_RUN_DIRECTORY,
) -> Dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    scan = scan_manifest(manifest_path)
    if not scan["passed"]:
        raise ValueError("原文指纹或章节结构与基准清单不一致")
    mode = "smoke" if smoke_chapters else "fullbook"
    run_id = (
        f"{manifest['benchmark']}-{mode}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{uuid.uuid4().hex[:6]}"
    )
    jobs = []
    for spec in manifest["books"]:
        chapters = (
            list(range(1, smoke_chapters + 1))
            if smoke_chapters
            else []
        )
        package_id = str(spec["package_id"])
        if smoke_chapters:
            package_id = f"{package_id}_smoke_{smoke_chapters}"
        job = store.create_job(
            package_id=package_id,
            novel_path=str(_novel_path(spec["filename"])),
            benchmark_id=run_id,
            novel_name=str(spec["filename"]).rsplit(".", 1)[0],
            chapters=chapters,
            volume_size=int(spec.get("volume_size") or 20),
            prompt_version=EXTRACTOR_PROMPT_VERSION,
            model=model,
        )
        jobs.append(
            {
                "book_id": spec["book_id"],
                "job_id": job.job_id,
                "package_id": job.package_id,
                "expected_chapters": (
                    smoke_chapters
                    or int(spec["expected"]["chapters"])
                ),
            }
        )
    payload = {
        "schema_version": 1,
        "benchmark": manifest["benchmark"],
        "run_id": run_id,
        "mode": mode,
        "model": model or "default",
        "prompt_version": EXTRACTOR_PROMPT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scan": scan,
        "jobs": jobs,
    }
    run_directory.mkdir(parents=True, exist_ok=True)
    (run_directory / f"{run_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def _elapsed_seconds(started_at: str, completed_at: str) -> Optional[float]:
    if not started_at:
        return None
    end = completed_at or datetime.now(timezone.utc).isoformat()
    try:
        return round(
            (
                datetime.fromisoformat(end)
                - datetime.fromisoformat(started_at)
            ).total_seconds(),
            3,
        )
    except ValueError:
        return None


def benchmark_report(
    run_path: Path,
    store: CompilationJobStore,
) -> Dict[str, Any]:
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    reports: List[Dict[str, Any]] = []
    for item in payload["jobs"]:
        job = store.get_job(item["job_id"])
        chapters = store.list_chapters(job.job_id)
        reports.append(
            {
                **item,
                "status": job.status,
                "worker_id": job.worker_id,
                "attempt_count": job.attempt_count,
                "completed_chapters": job.completed_chapters,
                "total_chapters": job.total_chapters,
                "progress": job.progress,
                "cache_hits": sum(
                    int(chapter["cache_hits"]) for chapter in chapters
                ),
                "cache_misses": sum(
                    int(chapter["cache_misses"]) for chapter in chapters
                ),
                "extraction_count": sum(
                    int(chapter["extraction_count"]) for chapter in chapters
                ),
                "elapsed_seconds": _elapsed_seconds(
                    job.started_at,
                    job.completed_at,
                ),
                "quality_status": job.quality_status,
                "quality_score": job.quality_score,
                "result_package_id": job.result_package_id,
                "error": job.error,
            }
        )
    result = {
        **payload,
        "reported_at": datetime.now(timezone.utc).isoformat(),
        "completed": all(
            report["status"] == "completed" for report in reports
        ),
        "jobs": reports,
    }
    run_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _database_path(value: str) -> Path:
    configured = Path(value)
    return configured if configured.is_absolute() else PROJECT_ROOT / configured


def _latest_run(directory: Path) -> Path:
    runs = list(directory.glob("novelsim-real-fullbook-v1-*.json"))
    if not runs:
        raise ValueError("尚无基准运行记录")
    def created_at(path: Path) -> str:
        try:
            return str(
                json.loads(path.read_text(encoding="utf-8")).get(
                    "created_at",
                    "",
                )
            )
        except (OSError, ValueError):
            return ""

    return max(runs, key=created_at)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="多小说真实全书编译基准")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--db",
        default=os.environ.get("COMPILER_DB_PATH", "data/compiler.sqlite3"),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("scan", help="验证两本原文的稳定输入指纹")
    enqueue = commands.add_parser("enqueue", help="创建真实编译基准任务")
    enqueue.add_argument("--model", default="")
    enqueue.add_argument(
        "--smoke-chapters",
        type=int,
        default=0,
        help="仅编译每本书前 N 章；0 表示真实全书",
    )
    report = commands.add_parser("report", help="汇总最近或指定基准")
    report.add_argument("--run", type=Path)
    args = parser.parse_args(argv)

    if args.command == "scan":
        result = scan_manifest(args.manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1

    store = CompilationJobStore(_database_path(args.db))
    if args.command == "enqueue":
        result = enqueue_benchmark(
            args.manifest,
            store,
            model=args.model,
            smoke_chapters=max(0, args.smoke_chapters),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(
            "\n下一步运行: "
            ".venv\\Scripts\\python.exe -m compiler.worker"
        )
        return 0

    run_path = args.run or _latest_run(DEFAULT_RUN_DIRECTORY)
    result = benchmark_report(run_path, store)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
