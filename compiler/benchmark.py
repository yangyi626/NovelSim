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
import math
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
DEFAULT_SECONDS_PER_CALL = 185.793
RAW_FINGERPRINT = "raw_bytes"
NORMALIZED_FINGERPRINT = "normalized_utf8_lf"
SUPPORTED_FINGERPRINT_MODES = {
    RAW_FINGERPRINT,
    NORMALIZED_FINGERPRINT,
}
PROFILES = {
    "quick": {
        "chapters": 20,
        "description": "两本书各 20 章，验证 Worker、缓存和质量闭环",
    },
    "stress": {
        "chapters": 200,
        "description": "两本书各 200 章，验证长时间运行与断点恢复",
    },
    "full": {
        "chapters": 0,
        "description": "真实全书，仅在确认调用预算后允许排队",
    },
}


def _load_manifest(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("只支持 schema_version=1 的小说基准清单")
    fingerprint_mode = payload.get("fingerprint_mode", RAW_FINGERPRINT)
    if fingerprint_mode not in SUPPORTED_FINGERPRINT_MODES:
        raise ValueError(f"不支持的小说指纹模式: {fingerprint_mode}")
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


def _source_fingerprint(path: Path, text: str, mode: str) -> Dict[str, Any]:
    """生成跨平台可重现的书源指纹。

    旧清单默认保留 raw_bytes 语义；正式基准使用 normalized_utf8_lf，
    避免 Git 在 Windows/Unix 间转换 CRLF/LF 时误报原文变更。
    """

    if mode == RAW_FINGERPRINT:
        source = path.read_bytes()
    elif mode == NORMALIZED_FINGERPRINT:
        source = (
            text.replace("\r\n", "\n")
            .replace("\r", "\n")
            .encode("utf-8")
        )
    else:
        raise ValueError(f"不支持的小说指纹模式: {mode}")
    return {
        "bytes": len(source),
        "sha256": hashlib.sha256(source).hexdigest(),
    }


def scan_book(
    spec: Dict[str, Any],
    *,
    fingerprint_mode: str = RAW_FINGERPRINT,
) -> Dict[str, Any]:
    path = _novel_path(str(spec["filename"]))
    started = time.perf_counter()
    text = load_novel(str(path))
    chapters = split_chapters(text)
    scenes = sum(len(split_scenes(chapter)) for chapter in chapters)
    actual = {
        **_source_fingerprint(path, text, fingerprint_mode),
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
        "fingerprint_mode": fingerprint_mode,
        "actual": actual,
        "mismatches": mismatches,
        "passed": not mismatches and actual["chapters"] > 0,
        "scan_seconds": round(time.perf_counter() - started, 3),
    }


def scan_manifest(manifest_path: Path) -> Dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    fingerprint_mode = manifest.get("fingerprint_mode", RAW_FINGERPRINT)
    books = [
        scan_book(spec, fingerprint_mode=fingerprint_mode)
        for spec in manifest["books"]
    ]
    return {
        "benchmark": manifest["benchmark"],
        "fingerprint_mode": fingerprint_mode,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "passed": all(book["passed"] for book in books),
        "books": books,
    }


def estimate_manifest(
    manifest_path: Path,
    *,
    profile: str = "quick",
    chapter_limit: int = 0,
    seconds_per_call: float = DEFAULT_SECONDS_PER_CALL,
    cost_per_call_cny: float = 0.0,
) -> Dict[str, Any]:
    """按真实章节/场景结构估算调用量、墙钟时间和可选费用。"""

    manifest = _load_manifest(manifest_path)
    if profile not in PROFILES:
        raise ValueError(f"未知演练档位: {profile}")
    limit = max(0, int(chapter_limit))
    if not limit:
        limit = int(PROFILES[profile]["chapters"])
    books = []
    for spec in manifest["books"]:
        path = _novel_path(str(spec["filename"]))
        chapters = split_chapters(load_novel(str(path)))
        selected = chapters[:limit] if limit else chapters
        scenes = sum(len(split_scenes(chapter)) for chapter in selected)
        # 预留 15% JSON 修复重试空间，并按每 12 个场景一个评分窗口
        # 估计分块评分 + 聚合调用。
        quality_calls = math.ceil(scenes / 12) + (1 if scenes > 12 else 0)
        recommended_max = max(
            1,
            math.ceil(scenes * 1.15) + quality_calls,
        )
        estimated_seconds = round(scenes * max(0.0, seconds_per_call), 3)
        books.append(
            {
                "book_id": spec["book_id"],
                "filename": spec["filename"],
                "chapters": len(selected),
                "scenes": scenes,
                "expected_extractor_calls": scenes,
                "recommended_max_llm_calls": recommended_max,
                "estimated_quality_calls": quality_calls,
                "estimated_seconds": estimated_seconds,
                "estimated_hours": round(estimated_seconds / 3600, 3),
                "estimated_cost_cny": (
                    round(scenes * cost_per_call_cny, 2)
                    if cost_per_call_cny > 0
                    else None
                ),
            }
        )
    return {
        "schema_version": 1,
        "benchmark": manifest["benchmark"],
        "profile": profile,
        "description": PROFILES[profile]["description"],
        "chapter_limit": limit,
        "seconds_per_call": seconds_per_call,
        "cost_per_call_cny": (
            cost_per_call_cny if cost_per_call_cny > 0 else None
        ),
        "cost_estimate_status": (
            "estimated" if cost_per_call_cny > 0 else "requires_rate"
        ),
        "books": books,
        "total_chapters": sum(item["chapters"] for item in books),
        "total_scenes": sum(item["scenes"] for item in books),
        "total_expected_extractor_calls": sum(
            item["expected_extractor_calls"] for item in books
        ),
        "total_estimated_hours": round(
            sum(item["estimated_seconds"] for item in books) / 3600,
            3,
        ),
        "total_estimated_cost_cny": (
            round(
                sum(item["estimated_cost_cny"] or 0 for item in books),
                2,
            )
            if cost_per_call_cny > 0
            else None
        ),
    }


def enqueue_benchmark(
    manifest_path: Path,
    store: CompilationJobStore,
    *,
    model: str = "",
    smoke_chapters: int = 0,
    profile: str = "quick",
    max_llm_calls: int = 0,
    seconds_per_call: float = DEFAULT_SECONDS_PER_CALL,
    cost_per_call_cny: float = 0.0,
    confirm_fullbook: bool = False,
    run_directory: Path = DEFAULT_RUN_DIRECTORY,
) -> Dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    scan = scan_manifest(manifest_path)
    if not scan["passed"]:
        raise ValueError("原文指纹或章节结构与基准清单不一致")
    if profile not in PROFILES:
        raise ValueError(f"未知演练档位: {profile}")
    chapter_limit = max(0, int(smoke_chapters))
    if not chapter_limit:
        chapter_limit = int(PROFILES[profile]["chapters"])
    if not chapter_limit and not confirm_fullbook:
        raise ValueError(
            "全书档位需要显式确认；请先运行 estimate，再传 "
            "--confirm-fullbook"
        )
    estimate = estimate_manifest(
        manifest_path,
        profile=profile,
        chapter_limit=chapter_limit,
        seconds_per_call=seconds_per_call,
        cost_per_call_cny=cost_per_call_cny,
    )
    mode = profile if not smoke_chapters else f"custom-{chapter_limit}"
    run_id = (
        f"{manifest['benchmark']}-{mode}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{uuid.uuid4().hex[:6]}"
    )
    jobs = []
    for spec in manifest["books"]:
        chapters = list(range(1, chapter_limit + 1)) if chapter_limit else []
        package_id = str(spec["package_id"])
        if chapter_limit:
            package_id = f"{package_id}_{mode}_{chapter_limit}"
        book_estimate = next(
            item
            for item in estimate["books"]
            if item["book_id"] == spec["book_id"]
        )
        call_budget = (
            max(0, int(max_llm_calls))
            or int(book_estimate["recommended_max_llm_calls"])
        )
        job = store.create_job(
            package_id=package_id,
            novel_path=str(_novel_path(spec["filename"])),
            benchmark_id=run_id,
            book_id=str(spec["book_id"]),
            novel_name=str(spec["filename"]).rsplit(".", 1)[0],
            chapters=chapters,
            volume_size=int(spec.get("volume_size") or 20),
            prompt_version=EXTRACTOR_PROMPT_VERSION,
            model=model,
            max_llm_calls=call_budget,
        )
        jobs.append(
            {
                "book_id": spec["book_id"],
                "job_id": job.job_id,
                "package_id": job.package_id,
                "expected_chapters": (
                    chapter_limit
                    or int(spec["expected"]["chapters"])
                ),
                "expected_scenes": book_estimate["scenes"],
                "max_llm_calls": call_budget,
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
        "estimate": estimate,
        "jobs": jobs,
    }
    run_directory.mkdir(parents=True, exist_ok=True)
    (run_directory / f"{run_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def _elapsed_seconds(
    started_at: str,
    completed_at: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[float]:
    if not started_at:
        return None
    end = completed_at or (now or datetime.now(timezone.utc)).isoformat()
    try:
        started = datetime.fromisoformat(started_at)
        ended = datetime.fromisoformat(end)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if ended.tzinfo is None:
            ended = ended.replace(tzinfo=timezone.utc)
        return round((ended - started).total_seconds(), 3)
    except ValueError:
        return None


def build_job_report(
    job,
    chapters: List[Dict[str, Any]],
    *,
    item: Optional[Dict[str, Any]] = None,
    cost_per_call_cny: float = 0.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    report = {
        **(item or {}),
        "job_id": job.job_id,
        "book_id": job.book_id,
        "package_id": job.package_id,
        "status": job.status,
        "worker_id": job.worker_id,
        "attempt_count": job.attempt_count,
        "retry_count": job.retry_count,
        "failure_kind": job.failure_kind,
        "retryable": job.retryable,
        "completed_chapters": job.completed_chapters,
        "total_chapters": job.total_chapters,
        "current_chapter": job.current_chapter,
        "progress": job.progress,
        "cache_hits": sum(int(chapter.get("cache_hits") or 0) for chapter in chapters),
        "cache_misses": sum(int(chapter.get("cache_misses") or 0) for chapter in chapters),
        "extraction_count": sum(int(chapter.get("extraction_count") or 0) for chapter in chapters),
        "llm_calls_used": job.llm_calls_used,
        "max_llm_calls": job.max_llm_calls,
        "llm_calls_remaining": (
            max(0, job.max_llm_calls - job.llm_calls_used)
            if job.max_llm_calls
            else None
        ),
        "elapsed_seconds": _elapsed_seconds(
            job.started_at,
            job.completed_at,
            now=now,
        ),
        "quality_status": job.quality_status,
        "quality_score": job.quality_score,
        "quality_report": dict(job.quality_report),
        "result_package_id": job.result_package_id,
        "error": job.error,
    }
    elapsed = report["elapsed_seconds"]
    report["chapters_per_hour"] = (
        round(report["completed_chapters"] * 3600 / elapsed, 3)
        if elapsed and report["completed_chapters"]
        else None
    )
    total_cache = report["cache_hits"] + report["cache_misses"]
    report["cache_hit_rate"] = (
        round(report["cache_hits"] / total_cache, 4)
        if total_cache
        else None
    )
    report["estimated_cost_cny"] = (
        round(report["llm_calls_used"] * cost_per_call_cny, 2)
        if cost_per_call_cny > 0
        else None
    )
    return report


def build_benchmark_report(
    payload: Dict[str, Any],
    store: CompilationJobStore,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    rate = float((payload.get("estimate") or {}).get("cost_per_call_cny") or 0)
    reports = [
        build_job_report(
            (job := store.get_job(item["job_id"])),
            store.list_chapters(job.job_id),
            item=item,
            cost_per_call_cny=rate,
            now=now,
        )
        for item in payload["jobs"]
    ]
    return {
        **payload,
        "reported_at": (now or datetime.now(timezone.utc)).isoformat(),
        "completed": all(report["status"] == "completed" for report in reports),
        "jobs": reports,
    }


def benchmark_report(
    run_path: Path,
    store: CompilationJobStore,
) -> Dict[str, Any]:
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    result = build_benchmark_report(payload, store)
    run_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def render_markdown_report(report: Dict[str, Any]) -> str:
    """把机器报告压缩成适合里程碑归档的 Markdown。"""

    lines = [
        f"# 全书编译生产演练：{report['run_id']}",
        "",
        f"- 档位：`{report['mode']}`",
        f"- 模型：`{report['model']}`",
        f"- 完成：{'是' if report['completed'] else '否'}",
        f"- 报告时间：{report['reported_at']}",
        "",
        "| 小说 | 状态 | 章节 | LLM 调用 | 缓存命中率 | 耗时 | 质量 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for item in report["jobs"]:
        elapsed = (
            f"{item['elapsed_seconds']:.1f}s"
            if item["elapsed_seconds"] is not None
            else "—"
        )
        cache_rate = (
            f"{item['cache_hit_rate']:.1%}"
            if item["cache_hit_rate"] is not None
            else "—"
        )
        quality = (
            f"{item['quality_score']:.3f}"
            if item["quality_score"] is not None
            else item["quality_status"]
        )
        lines.append(
            "| {name} | {status} | {done}/{total} | {used}/{maximum} | "
            "{cache} | {elapsed} | {quality} |".format(
                name=item["book_id"],
                status=item["status"],
                done=item["completed_chapters"],
                total=item["total_chapters"] or item["expected_chapters"],
                used=item["llm_calls_used"],
                maximum=item["max_llm_calls"] or "∞",
                cache=cache_rate,
                elapsed=elapsed,
                quality=quality,
            )
        )
    lines.extend(
        [
            "",
            "> 费用只有在排队时提供 `--cost-per-call-cny` 后才估算；"
            "实际账单仍以模型服务商为准。",
            "",
        ]
    )
    return "\n".join(lines)


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
    estimate = commands.add_parser(
        "estimate",
        help="执行前估算章节、场景、调用量、时间和费用",
    )
    estimate.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default="quick",
    )
    estimate.add_argument("--chapters", type=int, default=0)
    estimate.add_argument(
        "--seconds-per-call",
        type=float,
        default=DEFAULT_SECONDS_PER_CALL,
    )
    estimate.add_argument("--cost-per-call-cny", type=float, default=0.0)
    enqueue = commands.add_parser("enqueue", help="创建真实编译基准任务")
    enqueue.add_argument("--model", default="")
    enqueue.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default="quick",
    )
    enqueue.add_argument(
        "--smoke-chapters",
        type=int,
        default=0,
        help="仅编译每本书前 N 章；0 表示真实全书",
    )
    enqueue.add_argument("--max-llm-calls", type=int, default=0)
    enqueue.add_argument(
        "--seconds-per-call",
        type=float,
        default=DEFAULT_SECONDS_PER_CALL,
    )
    enqueue.add_argument("--cost-per-call-cny", type=float, default=0.0)
    enqueue.add_argument("--confirm-fullbook", action="store_true")
    report = commands.add_parser("report", help="汇总最近或指定基准")
    report.add_argument("--run", type=Path)
    report.add_argument("--watch", action="store_true", help="只读轮询运行进度")
    report.add_argument("--interval", type=float, default=3.0, help="watch 轮询秒数")
    args = parser.parse_args(argv)

    if args.command == "scan":
        result = scan_manifest(args.manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1

    if args.command == "estimate":
        result = estimate_manifest(
            args.manifest,
            profile=args.profile,
            chapter_limit=max(0, args.chapters),
            seconds_per_call=max(0.0, args.seconds_per_call),
            cost_per_call_cny=max(0.0, args.cost_per_call_cny),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    store = CompilationJobStore(_database_path(args.db))
    if args.command == "enqueue":
        result = enqueue_benchmark(
            args.manifest,
            store,
            model=args.model,
            smoke_chapters=max(0, args.smoke_chapters),
            profile=args.profile,
            max_llm_calls=max(0, args.max_llm_calls),
            seconds_per_call=max(0.0, args.seconds_per_call),
            cost_per_call_cny=max(0.0, args.cost_per_call_cny),
            confirm_fullbook=args.confirm_fullbook,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(
            "\n下一步运行: "
            ".venv\\Scripts\\python.exe -m compiler.worker"
        )
        return 0

    run_path = args.run or _latest_run(DEFAULT_RUN_DIRECTORY)
    if args.watch:
        interval = max(0.1, float(args.interval))
        while True:
            payload = json.loads(run_path.read_text(encoding="utf-8"))
            result = build_benchmark_report(payload, store)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if result["completed"]:
                return 0
            time.sleep(interval)

    result = benchmark_report(run_path, store)
    markdown_path = run_path.with_suffix(".md")
    markdown_path.write_text(
        render_markdown_report(result),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nMarkdown 报告: {markdown_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
