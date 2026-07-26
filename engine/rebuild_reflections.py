"""为现有世界线从情景记忆生成或重建 NPC 反思记忆。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Optional, Sequence

from dotenv import load_dotenv

from .memory_projection import rebuild_session_memories
from .qdrant_memory import QdrantBackedWorldStore
from .reflection_memory import (
    ReflectionSemanticJudge,
    reflect_character_memories,
)
from .store_factory import create_world_store


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_dotenv(".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite",
        default=os.environ.get("WORLD_DB_PATH", "data/world.sqlite3"),
    )
    parser.add_argument(
        "--session",
        help="只处理指定世界线；不传则处理全部存档",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="先删除已有反思，再从情景记忆重新生成",
    )
    parser.add_argument(
        "--rebuild-episodes",
        action="store_true",
        help="生成反思前先从事件链幂等重建情景记忆",
    )
    parser.add_argument("--min-episodes", type=int, default=3)
    parser.add_argument(
        "--semantic-threshold",
        type=float,
        default=float(
            os.environ.get(
                "MEMORY_REFLECTION_SEMANTIC_THRESHOLD",
                "0.72",
            )
        ),
    )
    parser.add_argument(
        "--no-semantic-judge",
        action="store_true",
        help="仅执行结构与认知冲突校验，不调用独立语义审查模型",
    )
    args = parser.parse_args(argv)

    store = create_world_store(sqlite_path=Path(args.sqlite))
    session_ids = (
        [args.session]
        if args.session
        else [item.session_id for item in store.list_sessions()]
    )
    result = {}
    semantic_judge = (
        None
        if args.no_semantic_judge
        else ReflectionSemanticJudge()
    )
    try:
        for session_id in session_ids:
            state = store.get_state(session_id)
            if state is None:
                result[session_id] = {"error": "会话不存在"}
                continue
            if args.rebuild_episodes:
                rebuild_session_memories(store, session_id)
            if args.force:
                store.delete_character_memories(
                    session_id,
                    memory_type="reflection",
                )
            reports = []
            for character_id, psyche in state.character_psyches.items():
                if psyche.is_player:
                    continue
                report = reflect_character_memories(
                    store,
                    session_id,
                    state,
                    character_id,
                    semantic_judge=semantic_judge,
                    semantic_threshold=args.semantic_threshold,
                    min_new_episodes=max(2, args.min_episodes),
                )
                reports.append(
                    {
                        "character_id": character_id,
                        "episodes": report.episodic_count,
                        "eligible": report.eligible_count,
                        "generated": report.generated_count,
                        "written": report.written_count,
                        "rejected": report.rejected_count,
                        "skipped": report.skipped_reason,
                        "rejections": report.rejection_reasons,
                        "semantic_scores": report.semantic_scores,
                    }
                )
            result[session_id] = reports
    finally:
        if isinstance(store, QdrantBackedWorldStore):
            store.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
