"""真实 qdrant-client Local Mode 契约测试。"""

import pytest

from engine import QdrantBackedWorldStore, QdrantMemoryIndex, SQLiteWorldStore
from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import NIGHT, QINGQING


pytestmark = pytest.mark.qdrant


class _TinyEmbedder:
    dimensions = 4

    def embed(self, text):
        text = text.lower()
        return [
            float("雨" in text),
            float("账本" in text),
            float("密信" in text),
            0.25,
        ]


def test_qdrant_local_mode_round_trip(tmp_path):
    pytest.importorskip("qdrant_client")
    base = SQLiteWorldStore(tmp_path / "world.sqlite3")
    store = QdrantBackedWorldStore(
        base,
        QdrantMemoryIndex(
            embedder=_TinyEmbedder(),
            path=tmp_path / "qdrant",
        ),
    )
    session_id = store.create_session(
        build_snapshot(),
        default_actor_id=NIGHT,
        world_package_id="huarong_lane",
    )
    memory_id = store.record_character_memories(
        session_id,
        [QINGQING],
        source_event_id="event-1",
        world_version=1,
        content="她把密信藏在雨夜的长街。",
    )[0]

    found = store.search_character_memories(
        session_id,
        QINGQING,
        "暴雨中的秘密书信",
        limit=4,
    )

    assert found[0].memory_id == memory_id
    store.close()
