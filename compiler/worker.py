"""独立小说编译 Worker。

Web 服务只负责把任务写入 SQLite；本进程通过租约原子领取任务，执行编译、质量
门禁和世界包落盘。多个 Worker 可以安全竞争任务，同一任务只会被一个租约持有。
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from engine import LLMTrajectoryEvaluator, WorldPackageStore
from web.auth import AuthStore, SYSTEM_ACTOR

from .job_runner import CompilationJobRunner
from .job_store import CompilationJob, CompilationJobStore


class CompilationWorker:
    def __init__(
        self,
        store: CompilationJobStore,
        runner: CompilationJobRunner,
        *,
        worker_id: str,
        lease_seconds: int = 180,
        poll_seconds: float = 2.0,
        audit_store: Optional[AuthStore] = None,
    ):
        self.store = store
        self.runner = runner
        self.worker_id = worker_id
        self.lease_seconds = max(30, int(lease_seconds))
        self.poll_seconds = max(0.1, float(poll_seconds))
        self.audit_store = audit_store

    def run_once(self) -> Optional[CompilationJob]:
        self.store.recover_stale_jobs()
        job = self.store.claim_next_job(
            self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return None

        stop_heartbeat = threading.Event()

        def keep_lease_alive() -> None:
            interval = max(10.0, self.lease_seconds / 3.0)
            while not stop_heartbeat.wait(interval):
                try:
                    self.store.heartbeat(
                        job.job_id,
                        self.worker_id,
                        lease_seconds=self.lease_seconds,
                    )
                except Exception:
                    return

        heartbeat = threading.Thread(
            target=keep_lease_alive,
            name=f"compiler-heartbeat-{job.job_id[:8]}",
            daemon=True,
        )
        heartbeat.start()
        try:
            result = self.runner.run(
                job.job_id,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
            if self.audit_store is not None:
                self.audit_store.audit(
                    SYSTEM_ACTOR,
                    action=f"compiler_job.{result.status}",
                    resource_type="compiler_job",
                    resource_id=result.job_id,
                    detail={
                        "benchmark_id": result.benchmark_id,
                        "package_id": result.result_package_id,
                        "quality_status": result.quality_status,
                        "quality_score": result.quality_score,
                        "worker_id": self.worker_id,
                    },
                )
                if (
                    result.status == "completed"
                    and result.quality_status == "passed"
                    and result.result_package_id
                ):
                    self.audit_store.audit(
                        SYSTEM_ACTOR,
                        action="world_package.review.pending_review",
                        resource_type="world_package",
                        resource_id=result.result_package_id,
                        detail={
                            "source": "compiler_quality_gate",
                            "job_id": result.job_id,
                            "quality_score": result.quality_score,
                        },
                    )
            return result
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=2)

    def serve_forever(self) -> None:
        print(
            f"[compiler-worker] {self.worker_id} 已启动，"
            f"数据库: {self.store.path}"
        )
        while True:
            job = self.run_once()
            if job is None:
                time.sleep(self.poll_seconds)
                continue
            print(
                f"[compiler-worker] {job.job_id} -> {job.status} "
                f"({job.completed_chapters}/{job.total_chapters})"
            )


def _project_path(value: str, default: str) -> Path:
    root = Path(__file__).resolve().parent.parent
    configured = Path(value or default)
    return configured if configured.is_absolute() else root / configured


def build_worker(
    *,
    database_path: Path,
    world_directory: Path,
    worker_id: str,
    lease_seconds: int,
    poll_seconds: float,
    auth_database_path: Optional[Path] = None,
) -> CompilationWorker:
    store = CompilationJobStore(database_path)
    package_store = WorldPackageStore(world_directory)
    quality_enabled = (
        os.environ.get("COMPILER_QUALITY_GATE_ENABLED", "true")
        .strip()
        .lower()
        not in {"0", "false", "no", "off"}
    )
    runner = CompilationJobRunner(
        store,
        package_store=package_store,
        quality_evaluator_factory=(
            (lambda: LLMTrajectoryEvaluator())
            if quality_enabled
            else None
        ),
    )
    return CompilationWorker(
        store,
        runner,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        poll_seconds=poll_seconds,
        audit_store=(
            AuthStore(auth_database_path)
            if auth_database_path is not None
            else None
        ),
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="启动独立小说编译 Worker")
    parser.add_argument(
        "--db",
        default=os.environ.get("COMPILER_DB_PATH", "data/compiler.sqlite3"),
    )
    parser.add_argument(
        "--worlds",
        default=os.environ.get("WORLD_PACKAGE_DIR", "worlds"),
    )
    parser.add_argument(
        "--auth-db",
        default=os.environ.get("AUTH_DB_PATH", "data/auth.sqlite3"),
    )
    parser.add_argument(
        "--worker-id",
        default=(
            f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        ),
    )
    parser.add_argument("--lease-seconds", type=int, default=180)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument(
        "--once",
        action="store_true",
        help="只领取并执行一个任务；没有任务时立即退出",
    )
    args = parser.parse_args(argv)
    worker = build_worker(
        database_path=_project_path(args.db, "data/compiler.sqlite3"),
        world_directory=_project_path(args.worlds, "worlds"),
        worker_id=args.worker_id,
        lease_seconds=args.lease_seconds,
        poll_seconds=args.poll_seconds,
        auth_database_path=_project_path(args.auth_db, "data/auth.sqlite3"),
    )
    if args.once:
        job = worker.run_once()
        if job is None:
            print("[compiler-worker] 当前没有排队任务")
        return 0
    try:
        worker.serve_forever()
    except KeyboardInterrupt:
        print("[compiler-worker] 已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
