"""SQLite 世界状态与事件持久化测试。"""

import sqlite3

import pytest

from engine import (
    PersistenceError,
    SessionNotFound,
    SQLiteWorldStore,
    VersionConflict,
    commit_event,
    rebuild_session_memories,
    replay_events,
)
from engine.event import state_hash
from examples.huarong_lane.scenario import LIN, NIGHT, QINGQING
from world_schema import Operation, OperationKind, StatePatch


def _store(tmp_path):
    return SQLiteWorldStore(tmp_path / "world.sqlite3")


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
                snapshot.json(ensure_ascii=False),
                0,
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

    def test_export_import_round_trip_preserves_complete_world_line(
        self, tmp_path, snapshot
    ):
        store = _store(tmp_path)
        sid = store.create_session(
            snapshot,
            default_actor_id=NIGHT,
            world_package_id="huarong_lane",
            save_name="待备份世界线",
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

        backup = store.export_session(sid)
        imported_sid = store.import_session(backup)

        assert imported_sid != sid
        imported_state = store.get_state(imported_sid)
        imported_meta = store.get_metadata(imported_sid)
        assert imported_state is not None
        assert imported_meta is not None
        assert imported_meta.save_name == "待备份世界线（导入）"
        assert state_hash(imported_state) == state_hash(new_state)
        assert [
            item.dict() for item in store.list_events(imported_sid)
        ] == [event.dict()]
        imported_turns = store.list_turns(imported_sid)
        assert imported_turns[0].player_input == "检查巷口"
        assert (
            imported_turns[0].result["narrative"]["narration"]
            == "她检查了巷口。"
        )

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
