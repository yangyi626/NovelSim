"""SQLite 世界状态与事件持久化测试。"""

import sqlite3

import pytest

from engine import (
    ManuscriptGenerationStatus,
    ManuscriptPassage,
    ManuscriptRevision,
    ManuscriptRevisionConflict,
    PersistenceError,
    SessionNotFound,
    SQLiteWorldStore,
    StateVersionUnavailable,
    VersionConflict,
    commit_event,
    rebuild_session_memories,
    replay_events,
)
from engine.chapter_progression import TransitionRequest
from engine.event import state_hash
from examples.huarong_lane.scenario import LIN, NIGHT, QINGQING
from world_schema import Operation, OperationKind, StatePatch


def _store(tmp_path):
    return SQLiteWorldStore(tmp_path / "world.sqlite3")


def _revision(manuscript, event, *, chapter_number=0):
    passage = ManuscriptPassage(
        passage_id=f"draft_{event.event_id}",
        manuscript_id=manuscript.manuscript_id,
        chapter_number=chapter_number,
        paragraphs=[f"事件 {event.event_id} 已写入正文。"],
        source_event_ids=[event.event_id],
        from_world_version=event.new_version,
        to_world_version=event.new_version,
    )
    return ManuscriptRevision(
        revision_id=f"draft_revision_{event.event_id}",
        manuscript_id=manuscript.manuscript_id,
        timeline_id=manuscript.timeline_id,
        revision_number=1,
        passages=[passage],
    )


def _advance(state, action_id="act_test"):
    patch = StatePatch(
        operations=[
            Operation(
                op=OperationKind.set_flag,
                path=f"test.turn_{state.version + 1}",
                value=True,
            )
        ]
    )
    return commit_event(
        state,
        action_id=action_id,
        event_type="test_turn",
        patch=patch,
        actor_ids=[NIGHT],
        expected_version=state.version,
    )


class TestSQLiteWorldStore:
    def test_session_survives_store_recreation(self, tmp_path, snapshot):
        database = tmp_path / "world.sqlite3"
        first = SQLiteWorldStore(database)
        sid = first.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
            session_id="session_restart",
        )

        reopened = SQLiteWorldStore(database)
        restored = reopened.get_state(sid)
        metadata = reopened.get_metadata(sid)

        assert restored is not None
        assert state_hash(restored) == state_hash(snapshot)
        assert metadata is not None
        assert metadata.world_package_id == "huarong_lane"
        assert metadata.default_actor_id == NIGHT
        assert metadata.state_version == 0

    def test_commit_persists_state_and_event_atomically(self, tmp_path, snapshot):
        store = _store(tmp_path)
        sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
        )
        event, new_state = _advance(snapshot)

        store.commit_turn(
            sid,
            expected_version=0,
            new_state=new_state,
            event=event,
            player_input="向前一步",
            turn_payload={"status": "committed", "narrative": {"narration": "她向前一步。"}},
        )

        restored = store.get_state(sid)
        events = store.list_events(sid)
        turns = store.list_turns(sid)
        assert restored is not None
        assert restored.version == 1
        assert restored.flags["test.turn_1"] is True
        assert [item.event_id for item in events] == [event.event_id]
        assert turns[0].player_input == "向前一步"
        assert turns[0].result["narrative"]["narration"] == "她向前一步。"
        assert state_hash(replay_events(snapshot, events)) == state_hash(restored)

    def test_get_state_at_version_replays_from_immutable_base(
        self, tmp_path, snapshot
    ):
        store = _store(tmp_path)
        sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
        )
        event_one, state_one = _advance(snapshot, "act_history_one")
        store.commit_turn(
            sid,
            expected_version=0,
            new_state=state_one,
            event=event_one,
        )
        event_two, state_two = _advance(state_one, "act_history_two")
        store.commit_turn(
            sid,
            expected_version=1,
            new_state=state_two,
            event=event_two,
        )

        version_zero = store.get_state_at_version(sid, 0)
        version_one = store.get_state_at_version(sid, 1)
        version_two = store.get_state_at_version(sid, 2)

        assert version_zero is not None
        assert version_one is not None
        assert version_two is not None
        assert version_zero.dict() == snapshot.dict()
        assert version_one.dict() == state_one.dict()
        assert version_two.dict() == state_two.dict()
        assert store.get_state_at_version("missing", 0) is None
        with pytest.raises(StateVersionUnavailable, match="available=0..2"):
            store.get_state_at_version(sid, 3)

    def test_manuscript_passage_is_idempotent_and_revisioned(
        self, tmp_path, snapshot
    ):
        store = _store(tmp_path)
        sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
            chapter_number=2,
        )
        event, new_state = _advance(snapshot)
        store.commit_turn(
            sid,
            expected_version=0,
            new_state=new_state,
            event=event,
        )

        manuscript = store.ensure_manuscript(sid)
        reserved = store.reserve_manuscript_passage(
            sid, [event.event_id], generation_kind="deterministic"
        )
        repeated = store.reserve_manuscript_passage(
            sid, [event.event_id], generation_kind="deterministic"
        )

        assert repeated.passage_id == reserved.passage_id
        assert reserved.generation_status == ManuscriptGenerationStatus.pending
        revision = _revision(manuscript, event, chapter_number=2)
        ready = store.complete_manuscript_passage(
            reserved.passage_id,
            revision,
            expected_current_revision=0,
        )

        assert ready.generation_status == ManuscriptGenerationStatus.ready
        assert ready.current_revision == 1
        assert ready.chapter_number == 2
        assert ready.source_event_ids == [event.event_id]
        assert ready.paragraphs
        assert store.list_manuscript_passages(sid) == [ready]
        assert len(store.list_manuscript_passage_revisions(ready.passage_id)) == 1

        failed_retry = store.fail_manuscript_passage(
            ready.passage_id, "临时 writer 失败"
        )
        assert failed_retry.generation_status == ManuscriptGenerationStatus.ready
        assert failed_retry.last_error == "临时 writer 失败"

        revised = store.complete_manuscript_passage(
            ready.passage_id,
            revision,
            expected_current_revision=1,
        )
        assert revised.current_revision == 2
        revisions = store.list_manuscript_passage_revisions(ready.passage_id)
        assert [item.revision_number for item in revisions] == [1, 2]
        assert store.get_manuscript_for_session(sid).campaign_id == manuscript.campaign_id

    def test_manuscript_revision_conflict_leaves_passage_unchanged(
        self, tmp_path, snapshot
    ):
        store = _store(tmp_path)
        sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
        )
        event, new_state = _advance(snapshot)
        store.commit_turn(
            sid,
            expected_version=0,
            new_state=new_state,
            event=event,
        )
        manuscript = store.ensure_manuscript(sid)
        reserved = store.reserve_manuscript_passage(sid, [event.event_id])
        revision = _revision(manuscript, event)
        ready = store.complete_manuscript_passage(
            reserved.passage_id,
            revision,
            expected_current_revision=0,
        )
        failed = store.fail_manuscript_passage(
            ready.passage_id, "保留原错误"
        )
        manuscript_before = store.get_manuscript_for_session(sid)
        revisions_before = store.list_manuscript_passage_revisions(
            ready.passage_id
        )

        with pytest.raises(ManuscriptRevisionConflict):
            store.complete_manuscript_passage(
                ready.passage_id,
                revision,
                expected_current_revision=0,
            )

        unchanged = store.get_manuscript_passage(ready.passage_id)
        manuscript_after = store.get_manuscript_for_session(sid)
        revisions_after = store.list_manuscript_passage_revisions(
            ready.passage_id
        )
        assert unchanged == failed
        assert unchanged.current_revision == 1
        assert unchanged.last_error == "保留原错误"
        assert revisions_after == revisions_before
        assert manuscript_after.current_revision == manuscript_before.current_revision

    def test_select_manuscript_revision_moves_pointer_without_deleting_history(
        self, tmp_path, snapshot
    ):
        store = _store(tmp_path)
        sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
        )
        event, state = _advance(snapshot, "act_select_revision")
        store.commit_turn(sid, expected_version=0, new_state=state, event=event)
        manuscript = store.ensure_manuscript(sid)
        reserved = store.reserve_manuscript_passage(sid, [event.event_id])
        first = store.complete_manuscript_passage(
            reserved.passage_id,
            _revision(manuscript, event),
            expected_current_revision=0,
        )
        second = store.complete_manuscript_passage(
            first.passage_id,
            _revision(manuscript, event),
            expected_current_revision=1,
        )

        selected = store.select_manuscript_passage_revision(
            second.passage_id,
            1,
            expected_current_revision=2,
        )

        assert selected.current_revision == 1
        assert selected.paragraphs == first.paragraphs
        assert len(store.list_manuscript_passage_revisions(second.passage_id)) == 2
        with pytest.raises(ManuscriptRevisionConflict):
            store.select_manuscript_passage_revision(
                second.passage_id,
                2,
                expected_current_revision=2,
            )

    def test_campaign_manuscript_passages_include_parent_and_child_sessions(
        self, tmp_path, snapshot
    ):
        store = _store(tmp_path)
        parent_sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="chapter_one",
        )
        parent_event, parent_state = _advance(snapshot, "act_parent")
        store.commit_turn(
            parent_sid,
            expected_version=0,
            new_state=parent_state,
            event=parent_event,
        )
        parent_passage = store.reserve_manuscript_passage(
            parent_sid, [parent_event.event_id]
        )
        store.record_settlement_progression(
            parent_sid,
            settlement_event_id=parent_event.event_id,
            settled_world_version=1,
            ending_id="ending_parent",
            ending_title="第一章终点",
            summary="进入下一章。",
            reward_points=10,
            idempotency_key="settlement-parent",
        )
        child_initial = snapshot.copy(deep=True)
        child_initial.version = 0
        child_event, child_state = _advance(child_initial, "act_child_genesis")
        transition = store.create_or_get_child_session(
            TransitionRequest(
                parent_session_id=parent_sid,
                target_world_package_id="chapter_two",
                child_state=child_state,
                genesis_event=child_event,
                manifest={"entries": []},
                default_actor_id=NIGHT,
                idempotency_key="transition-child",
                save_name="第二章世界线",
            )
        )
        child_sid = transition.child_session_id
        child_passage = store.reserve_manuscript_passage(
            child_sid, [child_event.event_id]
        )

        assert store.list_manuscript_passages(parent_sid) == [parent_passage]
        assert store.list_manuscript_passages(child_sid) == [child_passage]
        assert store.list_campaign_manuscript_passages(child_sid) == [
            parent_passage,
            child_passage,
        ]
        assert store.get_state_at_version(child_sid, 1).dict() == (
            child_state.dict()
        )
        with pytest.raises(StateVersionUnavailable, match="available=1..1"):
            store.get_state_at_version(child_sid, 0)
        assert [
            item.manuscript_sequence
            for item in store.list_campaign_manuscript_passages(parent_sid)
        ] == [1, 2]

    def test_failed_pending_manuscript_does_not_change_world(
        self, tmp_path, snapshot
    ):
        store = _store(tmp_path)
        sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
        )
        event, new_state = _advance(snapshot)
        store.commit_turn(
            sid,
            expected_version=0,
            new_state=new_state,
            event=event,
        )
        store.ensure_manuscript(sid)
        reserved = store.reserve_manuscript_passage(sid, [event.event_id])

        failed = store.fail_manuscript_passage(
            reserved.passage_id, "生成器不可用"
        )

        assert failed.generation_status == ManuscriptGenerationStatus.failed
        assert store.get_state(sid).version == 1
        assert [item.event_id for item in store.list_events(sid)] == [event.event_id]

    def test_stale_writer_is_rejected_without_overwrite(self, tmp_path, snapshot):
        store = _store(tmp_path)
        sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
        )
        first_event, first_state = _advance(snapshot, "act_first")
        stale_event, stale_state = _advance(snapshot, "act_stale")

        store.commit_turn(
            sid,
            expected_version=0,
            new_state=first_state,
            event=first_event,
        )
        with pytest.raises(VersionConflict):
            store.commit_turn(
                sid,
                expected_version=0,
                new_state=stale_state,
                event=stale_event,
            )

        restored = store.get_state(sid)
        events = store.list_events(sid)
        assert restored is not None
        assert state_hash(restored) == state_hash(first_state)
        assert [item.action_id for item in events] == ["act_first"]

    def test_inconsistent_event_version_is_rejected(self, tmp_path, snapshot):
        store = _store(tmp_path)
        sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
        )
        event, new_state = _advance(snapshot)
        event.new_version = 99

        with pytest.raises(PersistenceError, match="事件版本"):
            store.commit_turn(
                sid,
                expected_version=0,
                new_state=new_state,
                event=event,
            )

        assert store.get_state(sid).version == 0
        assert store.list_events(sid) == []

    def test_missing_session_commit_is_rejected(self, tmp_path, snapshot):
        store = _store(tmp_path)
        event, new_state = _advance(snapshot)

        with pytest.raises(SessionNotFound):
            store.commit_turn(
                "missing",
                expected_version=0,
                new_state=new_state,
                event=event,
            )

    def test_non_event_turn_is_saved_without_advancing_world(
        self, tmp_path, snapshot
    ):
        store = _store(tmp_path)
        sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
        )

        store.append_turn(
            sid,
            expected_version=0,
            player_input="飞到月亮上",
            turn_payload={"status": "rejected", "error": "规则拒绝"},
        )

        assert store.get_state(sid).version == 0
        assert store.list_events(sid) == []
        turns = store.list_turns(sid)
        assert len(turns) == 1
        assert turns[0].world_version == 0
        assert turns[0].result["status"] == "rejected"

    def test_save_list_rename_and_delete_cascade(self, tmp_path, snapshot):
        store = _store(tmp_path)
        sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
            save_name="初始名字",
        )
        event, new_state = _advance(snapshot)
        store.commit_turn(
            sid,
            expected_version=0,
            new_state=new_state,
            event=event,
            player_input="行动",
            turn_payload={"status": "committed"},
        )

        store.rename_session(sid, "逆转华容巷")
        saves = store.list_sessions()
        assert saves[0].save_name == "逆转华容巷"
        assert saves[0].state_version == 1

        assert store.delete_session(sid) is True
        assert store.get_state(sid) is None
        assert store.list_events(sid) == []
        assert store.list_turns(sid) == []
        assert store.search_character_memories(sid, NIGHT, "行动") == []

    def test_character_memory_fts_is_scoped_and_handles_chinese(
        self, tmp_path, snapshot
    ):
        store = _store(tmp_path)
        first_sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
        )
        second_sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
        )
        store.record_character_memories(
            first_sid,
            [QINGQING],
            source_event_id="evt_book",
            world_version=1,
            content="雨夜里，她在旧书店发现了一本隐藏账本。",
            importance=0.8,
        )
        store.record_character_memories(
            first_sid,
            [LIN],
            source_event_id="evt_book",
            world_version=1,
            content="林管家也见过那本账本。",
        )
        store.record_character_memories(
            second_sid,
            [QINGQING],
            source_event_id="evt_other_world",
            world_version=1,
            content="另一条世界线也有一本账本。",
        )

        found = store.search_character_memories(
            first_sid,
            QINGQING,
            "书店账本",
        )

        assert len(found) == 1
        assert found[0].source_event_id == "evt_book"
        assert found[0].session_id == first_sid
        assert found[0].character_id == QINGQING

    def test_character_memory_write_is_idempotent(
        self, tmp_path, snapshot
    ):
        store = _store(tmp_path)
        sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
        )
        first_ids = store.record_character_memories(
            sid,
            [QINGQING],
            source_event_id="evt_repeat",
            world_version=1,
            content="夜清清记住了第一次冲突。",
            importance=0.5,
        )
        second_ids = store.record_character_memories(
            sid,
            [QINGQING],
            source_event_id="evt_repeat",
            world_version=1,
            content="夜清清重新整理了这次冲突。",
            importance=0.9,
        )

        assert second_ids == first_ids
        found = store.search_character_memories(sid, QINGQING, "冲突")
        assert len(found) == 1
        assert found[0].content == "夜清清重新整理了这次冲突。"
        assert found[0].importance == 0.9

    def test_episodic_memories_can_be_rebuilt_from_event_history(
        self, tmp_path, snapshot
    ):
        store = _store(tmp_path)
        sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
        )
        event, new_state = _advance(snapshot)
        event.actor_ids = [QINGQING]
        store.commit_turn(
            sid,
            expected_version=0,
            new_state=new_state,
            event=event,
            player_input="去旧书店寻找账本",
            turn_payload={
                "status": "committed",
                "narrative": {"narration": "她找到了隐藏账本。"},
            },
        )
        store.record_character_memories(
            sid,
            [QINGQING],
            source_event_id="reflection_1",
            world_version=1,
            content="她反思自己不能再轻信旁人。",
            memory_type="reflection",
        )

        written = rebuild_session_memories(store, sid)

        assert written == 1
        episodic = store.search_character_memories(
            sid,
            QINGQING,
            "书店账本",
        )
        assert episodic[0].source_event_id == event.event_id
        reflections = store.search_character_memories(
            sid,
            QINGQING,
            "轻信旁人",
        )
        assert reflections[0].memory_type == "reflection"

    def test_memory_lifecycle_prunes_low_importance_episodes_only(
        self, tmp_path, snapshot
    ):
        store = _store(tmp_path)
        sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
        )
        for index, importance in enumerate([0.2, 0.9, 0.6], start=1):
            store.record_character_memories(
                sid,
                [QINGQING],
                source_event_id=f"evt_{index}",
                world_version=index,
                content=f"第 {index} 次情景经历",
                importance=importance,
            )
        store.record_character_memories(
            sid,
            [QINGQING],
            source_event_id="reflection_keep",
            world_version=3,
            content="必须保留的反思",
            importance=0.1,
            memory_type="reflection",
        )

        deleted = store.prune_character_memories(
            sid,
            QINGQING,
            max_records=2,
        )

        assert deleted == 1
        episodes = store.search_character_memories(sid, QINGQING, "")
        source_ids = {memory.source_event_id for memory in episodes}
        assert "evt_1" not in source_ids
        assert {"evt_2", "evt_3", "reflection_keep"} <= source_ids

    def test_existing_v1_database_is_migrated(self, tmp_path, snapshot):
        database = tmp_path / "legacy.sqlite3"
        legacy_state = snapshot.copy(deep=True)
        legacy_state.version = 4
        conn = sqlite3.connect(str(database))
        conn.execute(
            """
            CREATE TABLE world_sessions (
                session_id TEXT PRIMARY KEY,
                world_package_id TEXT NOT NULL,
                default_actor_id TEXT NOT NULL,
                state_json TEXT NOT NULL,
                state_version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO world_sessions VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy",
                "huarong_lane",
                NIGHT,
                legacy_state.json(ensure_ascii=False),
                legacy_state.version,
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()
        conn.close()

        migrated = SQLiteWorldStore(database)
        metadata = migrated.get_metadata("legacy")
        assert metadata is not None
        assert metadata.save_name == "华容巷世界线"
        assert migrated.list_turns("legacy") == []
        assert migrated.get_state_at_version("legacy", 4).dict() == (
            legacy_state.dict()
        )
        with pytest.raises(StateVersionUnavailable, match="available=4..4"):
            migrated.get_state_at_version("legacy", 3)

    def test_export_import_round_trip_preserves_complete_world_line(
        self, tmp_path, snapshot
    ):
        store = _store(tmp_path)
        sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
            save_name="待备份世界线",
            book_id="first_crazy",
            entry_id="first_crazy:chapter:6",
            chapter_number=6,
            entry_revision=3,
        )
        event, new_state = _advance(snapshot)
        store.commit_turn(
            sid,
            expected_version=0,
            new_state=new_state,
            event=event,
            player_input="检查巷口",
            turn_payload={
                "status": "committed",
                "narrative": {"narration": "她检查了巷口。"},
            },
        )

        manuscript = store.ensure_manuscript(sid)
        reserved = store.reserve_manuscript_passage(
            sid,
            [event.event_id],
        )
        revision = _revision(manuscript, event, chapter_number=6)
        ready = store.complete_manuscript_passage(
            reserved.passage_id,
            revision,
        )

        backup = store.export_session(sid)
        imported_sid = store.import_session(backup)

        assert backup["format_version"] == 3
        assert backup["base_state_version"] == 0
        assert imported_sid != sid
        imported_state = store.get_state(imported_sid)
        imported_meta = store.get_metadata(imported_sid)
        assert imported_state is not None
        assert imported_meta is not None
        assert imported_meta.save_name == "待备份世界线（导入）"
        assert imported_meta.book_id == "first_crazy"
        assert imported_meta.entry_id == "first_crazy:chapter:6"
        assert imported_meta.chapter_number == 6
        assert imported_meta.entry_revision == 3
        assert state_hash(imported_state) == state_hash(new_state)
        assert store.get_state_at_version(imported_sid, 0).dict() == snapshot.dict()
        assert store.get_state_at_version(imported_sid, 1).dict() == new_state.dict()
        assert [
            item.dict() for item in store.list_events(imported_sid)
        ] == [event.dict()]
        imported_turns = store.list_turns(imported_sid)
        assert imported_turns[0].player_input == "检查巷口"
        assert (
            imported_turns[0].result["narrative"]["narration"]
            == "她检查了巷口。"
        )
        imported_manuscript = store.get_manuscript_for_session(imported_sid)
        imported_passages = store.list_manuscript_passages(imported_sid)
        assert imported_manuscript is not None
        assert imported_manuscript.manuscript_id != manuscript.manuscript_id
        assert len(imported_passages) == 1
        assert imported_passages[0].passage_id != ready.passage_id
        assert imported_passages[0].session_id == imported_sid
        assert imported_passages[0].source_event_ids == [event.event_id]
        assert imported_passages[0].paragraphs == ready.paragraphs
        assert imported_passages[0].current_revision == 1
        assert len(
            store.list_manuscript_passage_revisions(
                imported_passages[0].passage_id
            )
        ) == 1

    def test_import_accepts_legacy_v1_backup_without_manuscript(
        self, tmp_path, snapshot
    ):
        store = _store(tmp_path)
        sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
        )
        backup = store.export_session(sid)
        backup["format_version"] = 1
        backup.pop("manuscript", None)
        backup.pop("manuscript_passages", None)
        backup.pop("manuscript_passage_revisions", None)

        imported_sid = store.import_session(backup)

        assert store.get_state(imported_sid) is not None
        assert store.get_manuscript_for_session(imported_sid) is None
        assert store.list_manuscript_passages(imported_sid) == []

    def test_import_rejects_broken_event_chain_without_partial_save(
        self, tmp_path, snapshot
    ):
        store = _store(tmp_path)
        sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
        )
        event, new_state = _advance(snapshot)
        store.commit_turn(
            sid,
            expected_version=0,
            new_state=new_state,
            event=event,
        )
        backup = store.export_session(sid)
        backup["events"][0]["previous_version"] = 8
        before = len(store.list_sessions())

        with pytest.raises(PersistenceError, match="版本链断裂"):
            store.import_session(backup)

        assert len(store.list_sessions()) == before
