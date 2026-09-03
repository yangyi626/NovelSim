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
        store.get_job(item["job_id"]).book_id == item["book_id"]
        for item in run["jobs"]
    )
    assert all(
        store.get_job(item["job_id"]).max_llm_calls >= 3
        for item in run["jobs"]
    )

    run_path = tmp_path / "runs" / f"{run['run_id']}.json"
    original = run_path.read_text(encoding="utf-8")
    pure = benchmark_module.build_benchmark_report(
        json.loads(original),
        store,
    )
    assert pure["completed"] is False
    assert run_path.read_text(encoding="utf-8") == original

    report = benchmark_module.benchmark_report(
        run_path,
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


def test_normalized_fingerprint_is_independent_of_line_endings(
    tmp_path,
    monkeypatch,
):
    novels = tmp_path / "novels"
    novels.mkdir()
    crlf = "第1章 开始\r\n\r\n人物开始行动。\r\n"
    lf = crlf.replace("\r\n", "\n")
    first_path = novels / "first.txt"
    second_path = novels / "second.txt"
    first_path.write_bytes(crlf.encode("utf-8"))
    second_path.write_bytes(lf.encode("utf-8"))
    canonical = lf.encode("utf-8")
    expected = {
        "bytes": len(canonical),
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "chapters": 1,
        "scenes": 1,
    }
    manifest = tmp_path / "novels.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "benchmark": "cross-platform-fingerprint",
                "fingerprint_mode": "normalized_utf8_lf",
                "books": [
                    {
                        "book_id": "first",
                        "filename": "first.txt",
                        "package_id": "first",
                        "expected": expected,
                    },
                    {
                        "book_id": "second",
                        "filename": "second.txt",
                        "package_id": "second",
                        "expected": expected,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(benchmark_module, "PROJECT_ROOT", tmp_path)

    scan = benchmark_module.scan_manifest(manifest)

    assert scan["passed"] is True
    assert scan["fingerprint_mode"] == "normalized_utf8_lf"
    assert scan["books"][0]["actual"] == scan["books"][1]["actual"]
