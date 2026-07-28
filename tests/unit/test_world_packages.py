"""创作者 WorldPackage 仓库测试。"""

import json

import pytest

from engine import (
    WorldPackageConflict,
    WorldPackageError,
    WorldPackageStore,
    WorldPackageValidationError,
)
from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import NIGHT


def _payload(package_id="huarong_lane"):
    return {
        "package_id": package_id,
        "novel": "第一狂妃：废柴三小姐",
        "scenario": "华容巷",
        "anchor": "夜轻歌被诬通奸、当众受辱",
        "default_actor_id": NIGHT,
        "source_chapters": [1, 2],
        "snapshot": build_snapshot().dict(),
        "revision": 1,
    }


def _store(tmp_path):
    return WorldPackageStore(
        tmp_path / "worlds",
        builtins={"huarong_lane": _payload()},
    )


def test_builtin_package_is_listed_and_read_only(tmp_path):
    store = _store(tmp_path)

    packages = store.list_packages()

    assert len(packages) == 1
    assert packages[0].package_id == "huarong_lane"
    assert packages[0].editable is False
    assert packages[0].manifest["character_count"] == 4


def test_clone_save_and_reload_custom_package(tmp_path):
    store = _store(tmp_path)
    cloned = store.clone("huarong_lane")
    draft = cloned.payload()
    draft["scenario"] = "华容巷·逆转版"
    draft["snapshot"]["characters"][NIGHT]["display_name"] = "夜轻歌（觉醒）"

    saved = store.save(
        cloned.package_id,
        draft,
        expected_revision=1,
    )
    reopened = _store(tmp_path).get(cloned.package_id)

    assert saved.revision == 2
    assert reopened.scenario == "华容巷·逆转版"
    assert reopened.snapshot.characters[NIGHT].display_name == "夜轻歌（觉醒）"
    assert reopened.review_status == "draft"


def test_save_rejects_stale_revision(tmp_path):
    store = _store(tmp_path)
    cloned = store.clone("huarong_lane")

    store.save(
        cloned.package_id,
        cloned.payload(),
        expected_revision=1,
    )
    with pytest.raises(WorldPackageConflict, match="版本冲突"):
        store.save(
            cloned.package_id,
            cloned.payload(),
            expected_revision=1,
        )


def test_validation_rejects_broken_cross_entity_reference(tmp_path):
    store = _store(tmp_path)
    payload = _payload("broken_world")
    payload["snapshot"]["relations"][0]["target_id"] = "char_missing"

    with pytest.raises(WorldPackageValidationError) as exc:
        store.validate(payload)

    assert "关系终点角色不存在" in str(exc.value)


def test_validation_rejects_runtime_snapshot_version(tmp_path):
    store = _store(tmp_path)
    payload = _payload("advanced_world")
    payload["snapshot"]["version"] = 3

    with pytest.raises(WorldPackageValidationError, match="模板版本必须为 0"):
        store.validate(payload)


def test_compiler_output_format_is_loaded_for_creator_editing(tmp_path):
    worlds = tmp_path / "worlds"
    worlds.mkdir()
    payload = _payload("compiled_chapter")
    legacy = {
        "package_id": payload["package_id"],
        "novel": payload["novel"],
        "source_chapters": payload["source_chapters"],
        "manifest": {"character_count": 4},
        "snapshot": payload["snapshot"],
    }
    (worlds / "compiled_chapter.json").write_text(
        json.dumps(legacy, ensure_ascii=False),
        encoding="utf-8",
    )

    loaded = WorldPackageStore(worlds).get("compiled_chapter")

    assert loaded.editable is True
    assert loaded.default_actor_id == NIGHT
    assert loaded.anchor == "待设置介入锚点"


def test_validation_rejects_location_cycle_and_dual_item_placement(tmp_path):
    store = _store(tmp_path)
    payload = _payload("broken_geography")
    payload["snapshot"]["locations"]["loc_huarong_lane"]["parent_id"] = (
        "loc_huarong_lane"
    )
    item = payload["snapshot"]["items"]["item_qingqing_outer_robe"]
    item["location_id"] = "loc_huarong_lane"

    with pytest.raises(WorldPackageValidationError) as exc:
        store.validate(payload)

    assert "不能以自身为父地点" in str(exc.value)
    assert "不能同时属于角色和放置在地点" in str(exc.value)


def test_validation_rejects_broken_psyche_goal_and_plan_refs(tmp_path):
    store = _store(tmp_path)
    payload = _payload("broken_psyche")
    psyche = payload["snapshot"]["character_psyches"][NIGHT]
    psyche["goals"][0]["target_ids"] = ["char_missing"]
    psyche["goals"][0]["status"] = "forgotten"
    psyche["goals"][0]["scope"] = "galaxy"
    psyche["plans"][0]["goal_id"] = "goal_missing"
    psyche["plans"][0]["current_step"] = 99

    with pytest.raises(WorldPackageValidationError) as exc:
        store.validate(payload)

    assert "引用未知角色 char_missing" in str(exc.value)
    assert "状态无效: forgotten" in str(exc.value)
    assert "作用域无效: galaxy" in str(exc.value)
    assert "引用未知目标 goal_missing" in str(exc.value)
    assert "当前步骤超出范围" in str(exc.value)


def test_validation_rejects_duplicate_or_invalid_character_beliefs(tmp_path):
    store = _store(tmp_path)
    payload = _payload("broken_beliefs")
    beliefs = payload["snapshot"]["beliefs"][NIGHT]
    duplicate = dict(beliefs[0])
    duplicate["source_type"] = "telepathy"
    duplicate["keywords"] = ["下毒", "下毒"]
    beliefs.append(duplicate)

    with pytest.raises(WorldPackageValidationError) as exc:
        store.validate(payload)

    assert "存在重复认知" in str(exc.value)
    assert "未知来源类型 telepathy" in str(exc.value)
    assert "含重复关键词" in str(exc.value)


def test_revision_history_and_structured_diff(tmp_path):
    store = _store(tmp_path)
    cloned = store.clone("huarong_lane")
    draft = cloned.payload()
    draft["scenario"] = "华容巷·证据版"

    saved = store.save(
        cloned.package_id,
        draft,
        expected_revision=1,
    )
    revisions = store.list_revisions(cloned.package_id)
    diff = store.diff_revisions(
        cloned.package_id,
        from_revision=1,
        to_revision=2,
    )

    assert saved.revision == 2
    assert [item["revision"] for item in revisions] == [2, 1]
    assert any(
        item["path"] == "scenario"
        and item["before"] == "华容巷"
        and item["after"] == "华容巷·证据版"
        for item in diff["changes"]
    )


def test_review_workflow_and_editing_published_package_resets_draft(tmp_path):
    store = _store(tmp_path)
    package = store.clone("huarong_lane")

    pending = store.transition_review(
        package.package_id,
        "pending_review",
        expected_revision=1,
        note="请审核角色认知。",
    )
    approved = store.transition_review(
        package.package_id,
        "approved",
        expected_revision=pending.revision,
        note="审核通过。",
    )
    published = store.transition_review(
        package.package_id,
        "published",
        expected_revision=approved.revision,
    )

    assert pending.review_status == "pending_review"
    assert approved.reviewed_at
    assert published.review_status == "published"
    assert published.published_at

    draft = published.payload()
    draft["anchor"] = "修改后的介入锚点"
    edited = store.save(
        package.package_id,
        draft,
        expected_revision=published.revision,
    )
    assert edited.review_status == "draft"
    assert edited.review_note == ""


def test_review_workflow_rejects_invalid_transition(tmp_path):
    store = _store(tmp_path)
    package = store.clone("huarong_lane")

    with pytest.raises(WorldPackageError, match="不能从 draft"):
        store.transition_review(
            package.package_id,
            "published",
            expected_revision=1,
        )
