"""NovelSim 本地进程栈：一键启动 Web 与独立编译 Worker。

状态只记录本启动器创建的精确 PID，不扫描或终止其他 Python 进程。运行日志和
PID 清单默认放在 ``data/runtime``，均不进入 Git。
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNTIME_DIRECTORY = PROJECT_ROOT / "data" / "runtime"
DEFAULT_HEALTH_PATH = "/api/meta/contract"


class StackError(RuntimeError):
    """本地进程栈操作失败。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _boot_marker() -> int:
    """近似系统启动时间，防止重启后 PID 复用导致误停其他进程。"""

    return int(time.time() - time.monotonic())


def _same_boot(state: Dict[str, Any]) -> bool:
    recorded = int(state.get("boot_marker") or 0)
    return bool(recorded and abs(recorded - _boot_marker()) <= 5)


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) != 0


def _health(url: str, *, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= int(response.status) < 300
    except (OSError, urllib.error.URLError):
        return False


def _state_path(runtime_directory: Path) -> Path:
    return runtime_directory / "stack.json"


def _load_state(runtime_directory: Path) -> Dict[str, Any]:
    path = _state_path(runtime_directory)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_state(runtime_directory: Path, state: Dict[str, Any]) -> None:
    runtime_directory.mkdir(parents=True, exist_ok=True)
    path = _state_path(runtime_directory)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _frontend_ready() -> bool:
    return (PROJECT_ROOT / "web" / "static" / "index.html").is_file()


def _build_frontend() -> None:
    frontend = PROJECT_ROOT / "web" / "frontend"
    npm = "npm.cmd" if os.name == "nt" else "npm"
    if not (frontend / "node_modules").is_dir():
        result = subprocess.run(
            [npm, "ci"],
            cwd=str(frontend),
            check=False,
        )
        if result.returncode != 0:
            raise StackError("前端依赖安装失败，请确认 Node.js 与 npm 可用")
    result = subprocess.run(
        [npm, "run", "build"],
        cwd=str(frontend),
        check=False,
    )
    if result.returncode != 0:
        raise StackError("Vue 生产构建失败")


def _spawn(
    name: str,
    command,
    *,
    runtime_directory: Path,
    environment: Dict[str, str],
) -> Dict[str, Any]:
    stdout_path = runtime_directory / f"{name}.out.log"
    stderr_path = runtime_directory / f"{name}.err.log"
    stdout = stdout_path.open("ab")
    stderr = stderr_path.open("ab")
    kwargs: Dict[str, Any] = {
        "cwd": str(PROJECT_ROOT),
        "env": environment,
        "stdin": subprocess.DEVNULL,
        "stdout": stdout,
        "stderr": stderr,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **kwargs)
    finally:
        stdout.close()
        stderr.close()
    return {
        "pid": process.pid,
        "command": [str(item) for item in command],
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "started_at": _now(),
    }


def _terminate_tree(pid: int) -> None:
    if not _pid_running(pid):
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not _pid_running(pid):
            return
        time.sleep(0.1)
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except ProcessLookupError:
        pass


def stack_status(
    *,
    runtime_directory: Path = DEFAULT_RUNTIME_DIRECTORY,
) -> Dict[str, Any]:
    runtime_directory = Path(runtime_directory).resolve()
    state = _load_state(runtime_directory)
    same_boot = _same_boot(state)
    processes = {}
    for name, item in (state.get("processes") or {}).items():
        pid = int(item.get("pid") or 0)
        processes[name] = {
            **item,
            "running": same_boot and _pid_running(pid),
        }
    health_url = str(state.get("health_url") or "")
    healthy = bool(
        same_boot
        and health_url
        and _health(health_url)
    )
    return {
        "status": (
            "running"
            if processes
            and all(item["running"] for item in processes.values())
            and (not health_url or healthy)
            else "stopped"
        ),
        "url": state.get("url") or "",
        "health_url": health_url,
        "healthy": healthy,
        "processes": processes,
        "runtime_directory": str(runtime_directory),
    }


def start_stack(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    with_worker: bool = True,
    build_frontend: bool = False,
    open_browser: bool = False,
    runtime_directory: Path = DEFAULT_RUNTIME_DIRECTORY,
    wait_seconds: float = 30.0,
    ensure_assets: bool = True,
) -> Dict[str, Any]:
    runtime_directory = Path(runtime_directory).resolve()
    current = stack_status(runtime_directory=runtime_directory)
    if any(
        item.get("running")
        for item in current.get("processes", {}).values()
    ):
        raise StackError(
            f"NovelSim 已由本启动器运行: {current.get('url') or '未知地址'}"
        )
    if not _port_available(host, port):
        raise StackError(f"端口已被占用: {host}:{port}")
    if ensure_assets and (build_frontend or not _frontend_ready()):
        _build_frontend()

    runtime_directory.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment["PYTHONUNBUFFERED"] = "1"
    url = f"http://{host}:{port}"
    health_url = f"{url}{DEFAULT_HEALTH_PATH}"
    processes: Dict[str, Dict[str, Any]] = {}
    try:
        processes["web"] = _spawn(
            "web",
            [
                sys.executable,
                str(PROJECT_ROOT / "web" / "run.py"),
                "--host",
                host,
                "--port",
                str(port),
            ],
            runtime_directory=runtime_directory,
            environment=environment,
        )
        deadline = time.monotonic() + max(1.0, float(wait_seconds))
        while time.monotonic() < deadline:
            if _health(health_url):
                break
            if not _pid_running(int(processes["web"]["pid"])):
                raise StackError(
                    "Web 进程提前退出，请查看 data/runtime/web.err.log"
                )
            time.sleep(0.2)
        else:
            raise StackError(f"Web 健康检查超时: {health_url}")

        if with_worker:
            processes["worker"] = _spawn(
                "worker",
                [sys.executable, "-m", "compiler.worker"],
                runtime_directory=runtime_directory,
                environment=environment,
            )
        state = {
            "schema_version": 1,
            "url": url,
            "health_url": health_url,
            "started_at": _now(),
            "boot_marker": _boot_marker(),
            "processes": processes,
        }
        _save_state(runtime_directory, state)
    except Exception:
        for item in reversed(list(processes.values())):
            _terminate_tree(int(item["pid"]))
        raise

    result = stack_status(runtime_directory=runtime_directory)
    if open_browser:
        webbrowser.open(url)
    return result


def stop_stack(
    *,
    runtime_directory: Path = DEFAULT_RUNTIME_DIRECTORY,
) -> Dict[str, Any]:
    runtime_directory = Path(runtime_directory).resolve()
    state = _load_state(runtime_directory)
    if _same_boot(state):
        for item in reversed(list((state.get("processes") or {}).values())):
            _terminate_tree(int(item.get("pid") or 0))
    path = _state_path(runtime_directory)
    if path.is_file():
        path.unlink()
    return stack_status(runtime_directory=runtime_directory)


def _print_status(result: Dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="一键管理 NovelSim Web + 编译 Worker",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="后台启动 Web 与 Worker")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8000)
    start.add_argument("--no-worker", action="store_true")
    start.add_argument("--build", action="store_true")
    start.add_argument("--open-browser", action="store_true")
    start.add_argument(
        "--runtime-dir",
        type=Path,
        default=DEFAULT_RUNTIME_DIRECTORY,
    )
    status = commands.add_parser("status", help="查询精确 PID 与健康状态")
    status.add_argument(
        "--runtime-dir",
        type=Path,
        default=DEFAULT_RUNTIME_DIRECTORY,
    )
    stop = commands.add_parser("stop", help="停止本启动器创建的进程")
    stop.add_argument(
        "--runtime-dir",
        type=Path,
        default=DEFAULT_RUNTIME_DIRECTORY,
    )
    restart = commands.add_parser("restart", help="停止后重新启动")
    restart.add_argument("--host", default="127.0.0.1")
    restart.add_argument("--port", type=int, default=8000)
    restart.add_argument("--no-worker", action="store_true")
    restart.add_argument("--build", action="store_true")
    restart.add_argument("--open-browser", action="store_true")
    restart.add_argument(
        "--runtime-dir",
        type=Path,
        default=DEFAULT_RUNTIME_DIRECTORY,
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "status":
            result = stack_status(runtime_directory=args.runtime_dir)
        elif args.command == "stop":
            result = stop_stack(runtime_directory=args.runtime_dir)
        else:
            if args.command == "restart":
                stop_stack(runtime_directory=args.runtime_dir)
            result = start_stack(
                host=args.host,
                port=args.port,
                with_worker=not args.no_worker,
                build_frontend=args.build,
                open_browser=args.open_browser,
                runtime_directory=args.runtime_dir,
            )
    except StackError as exc:
        print(f"[novelsim] {exc}", file=sys.stderr)
        return 1
    _print_status(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
