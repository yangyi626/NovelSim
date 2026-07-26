"""真实 PostgreSQL + pgvector 契约测试。

设置 TEST_POSTGRES_URL 后运行：
    pytest -m postgres -o addopts=
"""

import os
import secrets

import pytest

from engine import PostgresWorldStore, commit_event
from examples.huarong_lane.scenario import NIGHT, QINGQING
from world_schema import Operation, OperationKind, StatePatch


pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_store():
    database_url = os.environ.get("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("未设置 TEST_POSTGRES_URL")
    store = PostgresWorldStore(database_url)
    session_ids = []
    yield store, session_ids
    for session_id in session_ids:
        store.delete_session(session_id)


def test_postgres_world_store_contract(postgres_store, snapshot):
    store, session_ids = postgres_store
    session_id = store.create_session(
        snapshot,
        default_actor_id=NIGHT,
        world_package_id="huarong_lane",
        session_id=f"test_{secrets.token_hex(8)}",
    )
    session_ids.append(session_id)
    patch = StatePatch(
        operations=[
            Operation(
                op=OperationKind.set_flag,
                path="test.postgres",
                value=True,
            )
        ]
    )
    event, new_state = commit_event(
        snapshot,
        action_id="act_postgres",
        event_type="test",
        patch=patch,
        actor_ids=[NIGHT],
        expected_version=0,
    )

    store.commit_turn(
        session_id,
        expected_version=0,
        new_state=new_state,
        event=event,
        player_input="测试 PostgreSQL",
        turn_payload={"status": "committed"},
    )
    store.record_character_memories(
        session_id,
        [QINGQING],
        source_event_id=event.event_id,
        world_version=1,
        content="夜清清在旧书店发现秘密账本。",
        importance=0.8,
    )

    assert store.get_state(session_id).flags["test.postgres"] is True
    assert store.list_events(session_id)[0].event_id == event.event_id
    assert store.list_turns(session_id)[0].player_input == "测试 PostgreSQL"
    memories = store.search_character_memories(
        session_id,
        QINGQING,
        "书店账本",
    )
    assert memories[0].source_event_id == event.event_id
