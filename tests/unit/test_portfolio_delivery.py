"""求职版仓库内交付物的静态门禁。"""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_readme_first_screen_answers_seven_recruiter_questions():
    readme = _read("README.md")
    first_screen = readme[:5000]

    for phrase in (
        "解决什么 Game AI 问题",
        "为什么不能只用 LLM",
        "如何保证事实与认知一致",
        "NPC 如何调用工具并在 Unity 执行",
        "多 Agent 出现了什么行为",
        "比基线提升了什么",
        "如何在本地 5 分钟运行",
    ):
        assert phrase in first_screen


def test_architecture_doc_contains_three_required_diagrams():
    document = _read("docs/作品集架构与因果图.md")

    assert document.count("```mermaid") >= 3
    assert "项目架构" in document
    assert "Agent 状态机" in document
    assert "信息传播与联盟因果图" in document
    assert "WORLD_CONCEPT_UNAVAILABLE" in document


def test_demo_scripts_cover_video_interview_and_honest_boundaries():
    document = _read("docs/求职版演示脚本.md")

    assert "2–3 分钟无剪辑视频分镜" in document
    assert "10–15 分钟面试演示" in document
    assert "夜轻歌开飞机飞走了" in document
    assert "3–3" in document
    assert "真人盲标" in document


def test_backend_and_unity_have_one_click_windows_launcher():
    powershell = _read("start-unity-demo.ps1")
    command = _read("start-unity-demo.cmd")

    assert "-m web.stack start" in powershell
    assert "/api/meta/contract" in powershell
    assert "NovelSim3D.exe" in powershell
    assert "-novelsim-api-url" in powershell
    assert "start-unity-demo.ps1" in command


def test_original_public_world_and_checksum_are_committed():
    package = ROOT / "portfolio" / "worlds" / "secret-letter-v1.json"
    checksum = package.with_suffix(".json.sha256")

    assert package.is_file()
    assert checksum.is_file()
    assert "original_for_novelsim_portfolio" in package.read_text(
        encoding="utf-8"
    )


def test_real_unity_showcase_video_has_verified_runtime_report():
    video = ROOT / "portfolio" / "video" / "NovelSim-core-demo-v1.mp4"
    report_path = video.with_suffix(".json")
    checksum_path = video.with_suffix(".mp4.sha256")

    assert video.is_file()
    assert video.stat().st_size > 1_000_000
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert 120 <= report["duration_seconds"] <= 180
    assert report["version"] == 1
    assert report["rejection_code"] == "WORLD_CONCEPT_UNAVAILABLE"
    assert report["presentation_commands"] >= 1

    expected = checksum_path.read_text(encoding="utf-8").split()[0].lower()
    actual = hashlib.sha256(video.read_bytes()).hexdigest()
    assert actual == expected
