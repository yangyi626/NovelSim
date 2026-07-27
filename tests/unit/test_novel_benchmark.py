"""多小说全书编译基准清单、排队与报告。"""

import hashlib
import json

import pytest

from compiler import CompilationJobStore
from compiler import benchmark as benchmark_module


def _book(path, title):
    text = (
        f"第1章 {title}开始\n\n人物开始行动。\n"
        f"第2章 {title}继续\n\n人物继续推进目标。\n"
    )
    path.write_text(text, encoding="utf-8")
    raw = path.read_bytes()
    return {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "chapters": 2,
        "scenes": 2,
    }


def test_two_book_scan_enqueue_and_report(tmp_path, monkeypatch):
    novels = tmp_path / "novels"
    novels.mkdir()
    first = _book(novels / "first.txt", "第一本")
    second = _book(novels / "second.txt", "第二本")
    manifest = tmp_path / "novels.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "benchmark": "novelsim-real-fullbook-v1",
                "books": [
                    {
                        "book_id": "first",
                        "filename": "first.txt",
                        "package_id": "first_book",
                        "volume_size": 20,
                        "expected": first,
                    },
                    {
                        "book_id": "second",
                        "filename": "second.txt",
                        "package_id": "second_book",
                        "volume_size": 20,
                        "expected": second,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(benchmark_module, "PROJECT_ROOT", tmp_path)
    store = CompilationJobStore(tmp_path / "compiler.sqlite3")

    scan = benchmark_module.scan_manifest(manifest)
    assert scan["passed"] is True
    assert [item["actual"]["chapters"] for item in scan["books"]] == [2, 2]

    estimate = benchmark_module.estimate_manifest(
        manifest,
        profile="quick",
        chapter_limit=1,
        seconds_per_call=10,
        cost_per_call_cny=0.2,
    )
    assert estimate["total_scenes"] == 2
    assert estimate["total_estimated_cost_cny"] == 0.4

    run = benchmark_module.enqueue_benchmark(
        manifest,
        store,
        smoke_chapters=1,
        run_directory=tmp_path / "runs",
    )
    assert len(run["jobs"]) == 2
    assert all(
        store.get_job(item["job_id"]).benchmark_id == run["run_id"]
        for item in run["jobs"]
    )
    assert all(
        store.get_job(item["job_id"]).max_llm_calls >= 3
        for item in run["jobs"]
    )

    report = benchmark_module.benchmark_report(
        tmp_path / "runs" / f"{run['run_id']}.json",
        store,
    )
    assert report["completed"] is False
    assert {item["status"] for item in report["jobs"]} == {"queued"}
    markdown = benchmark_module.render_markdown_report(report)
    assert "全书编译生产演练" in markdown
    assert "LLM 调用" in markdown

    with pytest.raises(ValueError, match="显式确认"):
        benchmark_module.enqueue_benchmark(
            manifest,
            store,
            profile="full",
            run_directory=tmp_path / "runs",
        )
