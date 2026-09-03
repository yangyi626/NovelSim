"""真实 PostgreSQL + pgvector 契约测试。

设置 TEST_POSTGRES_URL 后运行：
    pytest -m postgres -o addopts=
"""

import os
import secrets

import pytest

from engine import (
    ManuscriptPassage,
    ManuscriptRevision,
    ManuscriptRevisionConflict,
    PostgresWorldStore,
    commit_event,
)
from engine.chapter_progression import TransitionRequest
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
    assert store.get_state_at_version(session_id, 0).dict() == snapshot.dict()
    assert store.get_state_at_version(session_id, 1).dict() == new_state.dict()
    assert store.list_events(session_id)[0].event_id == event.event_id
    assert store.list_turns(session_id)[0].player_input == "测试 PostgreSQL"
    memories = store.search_character_memories(
        session_id,
        QINGQING,
        "书店账本",
    )
    assert memories[0].source_event_id == event.event_id


def test_postgres_manuscript_revision_and_campaign_contract(
    postgres_store, snapshot
):
    store, session_ids = postgres_store
    parent_id = store.create_session(
        snapshot,
        default_actor_id=NIGHT,
        world_package_id="chapter_one",
        session_id=f"test_{secrets.token_hex(8)}",
    )
    session_ids.append(parent_id)
    parent_event, parent_state = commit_event(
        snapshot,
        action_id="act_parent_manuscript",
        event_type="settlement.claimed",
        patch=StatePatch(
            operations=[
                Operation(
                    op=OperationKind.set_flag,
                    path="test.parent_manuscript",
                    value=True,
                )
            ]
        ),
        actor_ids=[NIGHT],
        expected_version=0,
    )
    store.commit_turn(
        parent_id,
        expected_version=0,
        new_state=parent_state,
        event=parent_event,
    )
    manuscript = store.ensure_manuscript(parent_id)
    parent_passage = store.reserve_manuscript_passage(
        parent_id, [parent_event.event_id]
    )
    revision = ManuscriptRevision(
        revision_id=f"draft_revision_{parent_event.event_id}",
        manuscript_id=manuscript.manuscript_id,
        timeline_id=manuscript.timeline_id,
        revision_number=1,
        passages=[
            ManuscriptPassage(
                passage_id=f"draft_{parent_event.event_id}",
                manuscript_id=manuscript.manuscript_id,
                paragraphs=["父会话事件已写入正文。"],
                source_event_ids=[parent_event.event_id],
                from_world_version=parent_event.new_version,
                to_world_version=parent_event.new_version,
            )
        ],
    )
    ready = store.complete_manuscript_passage(
        parent_passage.passage_id,
        revision,
        expected_current_revision=0,
    )
    failed = store.fail_manuscript_passage(ready.passage_id, "保留原错误")

    with pytest.raises(ManuscriptRevisionConflict):
        store.complete_manuscript_passage(
            ready.passage_id,
            revision,
            expected_current_revision=0,
        )

    assert store.get_manuscript_passage(ready.passage_id) == failed
    assert len(store.list_manuscript_passage_revisions(ready.passage_id)) == 1

    store.record_settlement_progression(
        parent_id,
        settlement_event_id=parent_event.event_id,
        settled_world_version=1,
        ending_id="ending_parent",
        ending_title="第一章终点",
        summary="进入下一章。",
        reward_points=10,
        idempotency_key=f"settlement_{secrets.token_hex(8)}",
    )
    child_initial = snapshot.copy(deep=True)
    child_initial.version = 0
    child_event, child_state = commit_event(
        child_initial,
        action_id="act_child_genesis",
        event_type="chapter.inherited",
        patch=StatePatch(
            operations=[
                Operation(
                    op=OperationKind.set_flag,
                    path="test.child_manuscript",
                    value=True,
                )
            ]
        ),
        actor_ids=[NIGHT],
        expected_version=0,
    )
    transition = store.create_or_get_child_session(
        TransitionRequest(
            parent_session_id=parent_id,
            target_world_package_id="chapter_two",
            child_state=child_state,
            genesis_event=child_event,
            manifest={"entries": []},
            default_actor_id=NIGHT,
            idempotency_key=f"transition_{secrets.token_hex(8)}",
            save_name="第二章世界线",
        )
    )
    child_id = transition.child_session_id
    session_ids.append(child_id)
    child_passage = store.reserve_manuscript_passage(
        child_id, [child_event.event_id]
    )

    assert store.list_manuscript_passages(parent_id) == [failed]
    assert store.list_manuscript_passages(child_id) == [child_passage]
    assert store.list_campaign_manuscript_passages(child_id) == [
        failed,
        child_passage,
    ]
