"""创作者后台编译任务 API。"""

import importlib
import json

from compiler import CompilationJobStore


web_app = importlib.import_module("web.app")


class FakeManager:
    def __init__(self):
        self.started = []

    def start(self, job_id):
        self.started.append(job_id)

    def is_running(self, job_id):
        return job_id in self.started


def _configure(tmp_path, monkeypatch):
    novel_dir = tmp_path / "novels"
    novel_dir.mkdir()
    (novel_dir / "book.txt").write_text(
        "第1章 开始\n\n夜轻歌醒来。",
        encoding="utf-8",
    )
    store = CompilationJobStore(tmp_path / "compiler.sqlite3")
    manager = FakeManager()
    monkeypatch.setattr(web_app, "NOVEL_DIRECTORY", novel_dir.resolve())
    monkeypatch.setattr(web_app, "COMPILATION_JOBS", store)
    monkeypatch.setattr(web_app, "COMPILATION_MANAGER", manager)
    return store, manager


def test_create_list_get_and_control_compilation_job(
    tmp_path,
    monkeypatch,
):
    store, manager = _configure(tmp_path, monkeypatch)
    created = web_app.api_create_compilation_job(
        web_app.CompilationJobRequest(
            novel_path="book.txt",
            package_id="compiled_api",
            chapters=[2, 1, 1],
            timeline_plan={1: "origin", 2: "novel"},
            auto_start=True,
        )
    )
    job_id = created["job"]["job_id"]

    assert manager.started == [job_id]
    assert created["job"]["chapters"] == [1, 2]
    listed = web_app.api_list_compilation_jobs()
    assert listed["jobs"][0]["job_id"] == job_id
    detail = web_app.api_get_compilation_job(job_id)
    assert detail["job"]["timeline_plan"] == {
        "1": "origin",
        "2": "novel",
    }
    assert detail["worker_active"] is True

    paused = web_app.api_control_compilation_job(
        job_id,
        web_app.CompilationJobActionRequest(action="pause"),
    )
    assert paused["job"]["status"] == "paused"
    resumed = web_app.api_control_compilation_job(
        job_id,
        web_app.CompilationJobActionRequest(action="resume"),
    )
    assert resumed["job"]["status"] == "queued"
    cancelled = web_app.api_control_compilation_job(
        job_id,
        web_app.CompilationJobActionRequest(action="cancel"),
    )
    assert cancelled["job"]["status"] == "cancelled"
    assert store.get_job(job_id).status == "cancelled"


def test_compilation_api_rejects_path_outside_novels(
    tmp_path,
    monkeypatch,
):
    _configure(tmp_path, monkeypatch)
    outside = tmp_path / "outside.txt"
    outside.write_text("第1章 越界", encoding="utf-8")

    response = web_app.api_create_compilation_job(
        web_app.CompilationJobRequest(
            novel_path=str(outside),
            package_id="compiled_outside",
        )
    )
    payload = json.loads(response.body)

    assert response.status_code == 422
    assert "novels" in payload["error"]
