"""创作者后台编译任务 API。"""

import importlib
import json

from compiler import CompilationJobStore


web_app = importlib.import_module("web.app")


def _configure(tmp_path, monkeypatch):
    novel_dir = tmp_path / "novels"
    novel_dir.mkdir()
    (novel_dir / "book.txt").write_text(
        "第1章 开始\n\n夜轻歌醒来。",
        encoding="utf-8",
    )
    store = CompilationJobStore(tmp_path / "compiler.sqlite3")
    monkeypatch.setattr(web_app, "NOVEL_DIRECTORY", novel_dir.resolve())
    monkeypatch.setattr(web_app, "COMPILATION_JOBS", store)
    return store


def test_create_list_get_and_control_compilation_job(
    tmp_path,
    monkeypatch,
):
    store = _configure(tmp_path, monkeypatch)
    created = web_app.api_create_compilation_job(
        web_app.CompilationJobRequest(
            novel_path="book.txt",
            package_id="compiled_api",
            book_id="book",
            chapters=[2, 1, 1],
            timeline_plan={1: "origin", 2: "novel"},
            max_llm_calls=7,
            auto_start=True,
        )
    )
    job_id = created["job"]["job_id"]

    assert store.get_job(job_id).status == "queued"
    assert created["job"]["chapters"] == [1, 2]
    assert created["job"]["max_llm_calls"] == 7
    assert created["job"]["llm_calls_used"] == 0
    listed = web_app.api_list_compilation_jobs()
    assert listed["jobs"][0]["job_id"] == job_id
    detail = web_app.api_get_compilation_job(job_id)
    assert detail["job"]["timeline_plan"] == {
        "1": "origin",
        "2": "novel",
    }
    assert detail["worker_active"] is False

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


def test_compilation_api_requires_book_id(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    response = web_app.api_create_compilation_job(
        web_app.CompilationJobRequest(
            novel_path="book.txt",
            package_id="compiled_missing_book",
        )
    )
    payload = json.loads(response.body)
    assert response.status_code == 422
    assert "book_id" in payload["error"]


def test_compilation_api_validates_expected_source_hash(tmp_path, monkeypatch):
    store = _configure(tmp_path, monkeypatch)
    response = web_app.api_create_compilation_job(
        web_app.CompilationJobRequest(
            novel_path="book.txt",
            package_id="compiled_hash",
            book_id="book",
            expected_source_hash="0" * 64,
        )
    )
    payload = json.loads(response.body)
    assert response.status_code == 409
    assert "指纹" in payload["error"]
    assert store.list_jobs() == []


def test_compilation_api_supports_budget_retry_and_report(tmp_path, monkeypatch):
    store = _configure(tmp_path, monkeypatch)
    created = web_app.api_create_compilation_job(
        web_app.CompilationJobRequest(
            novel_path="book.txt",
            package_id="compiled_controls",
            book_id="book",
            max_llm_calls=2,
            auto_start=False,
        )
    )
    job_id = created["job"]["job_id"]
    store.prepare_chapters(job_id, [{"index": 1, "heading": "第1章 开始"}])
    store.start_run(job_id)
    store.mark_failed(
        job_id,
        "HTTP 503",
        failure_kind="transient",
        retryable=True,
    )

    retried = web_app.api_control_compilation_job(
        job_id,
        web_app.CompilationJobActionRequest(action="retry"),
    )
    assert retried["job"]["status"] == "queued"
    assert retried["job"]["retry_count"] == 1

    extended = web_app.api_extend_compilation_budget(
        job_id,
        web_app.CompilationJobBudgetRequest(additional_llm_calls=3, reason="继续演练"),
    )
    assert extended["job"]["max_llm_calls"] == 5
    assert "execution_token" not in extended["job"]

    report = web_app.api_compilation_job_report(job_id)
    assert report["status"] == "ok"
    assert report["report"]["job"]["retry_count"] == 1
    assert report["report"]["chapter_count"] == 1
    assert report["report"]["snapshot_count"] == 0


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
            book_id="book",
        )
    )
    payload = json.loads(response.body)

    assert response.status_code == 422
    assert "novels" in payload["error"]
