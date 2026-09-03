"""一键启动器的真实 Web/Worker 生命周期测试。"""

import json
import socket

from web.stack import start_stack, stack_status, stop_stack


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_status_without_runtime_state_is_stopped(tmp_path):
    result = stack_status(runtime_directory=tmp_path / "runtime")

    assert result["status"] == "stopped"
    assert result["healthy"] is False
    assert result["ready"] is False
    assert result["processes"] == {}


def test_stale_boot_state_never_treats_reused_pid_as_ours(
    tmp_path,
    monkeypatch,
):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "stack.json").write_text(
        json.dumps(
            {
                "boot_marker": 1,
                "processes": {
                    "web": {"pid": 123, "command": ["python"]},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("web.stack._pid_running", lambda _pid: True)

    result = stack_status(runtime_directory=runtime)

    assert result["status"] == "stopped"
    assert result["processes"]["web"]["running"] is False


def test_start_status_and_stop_web_worker_stack(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    port = _free_port()
    monkeypatch.setenv("MEMORY_VECTOR_BACKEND", "fts5")
    monkeypatch.setenv("COMPILER_QUALITY_GATE_ENABLED", "false")
    monkeypatch.setenv("WORLD_DB_PATH", str(tmp_path / "world.sqlite3"))
    monkeypatch.setenv(
        "COMPILER_DB_PATH",
        str(tmp_path / "compiler.sqlite3"),
    )
    monkeypatch.setenv("AUTH_DB_PATH", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("WORLD_PACKAGE_DIR", str(tmp_path / "worlds"))

    try:
        started = start_stack(
            port=port,
            with_worker=True,
            runtime_directory=runtime,
            wait_seconds=30,
            ensure_assets=False,
        )

        assert started["status"] == "running"
        assert started["healthy"] is True
        assert started["ready"] is True
        assert set(started["processes"]) == {"web", "worker"}
        assert all(
            item["running"]
            for item in started["processes"].values()
        )
        current = stack_status(runtime_directory=runtime)
        assert current["url"] == f"http://127.0.0.1:{port}"
        assert current["healthy"] is True
        assert current["ready"] is True
    finally:
        stopped = stop_stack(runtime_directory=runtime)

    assert stopped["status"] == "stopped"
    assert stopped["processes"] == {}
