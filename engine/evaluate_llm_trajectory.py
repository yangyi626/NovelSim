"""对一个真实存档执行分块 LLM 长轨迹质量评分。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Optional, Sequence

from dotenv import load_dotenv

from .llm_trajectory_eval import LLMTrajectoryEvaluator
from .qdrant_memory import QdrantBackedWorldStore
from .store_factory import create_world_store


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_dotenv(".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, help="待评分世界线 ID")
    parser.add_argument(
        "--sqlite",
        default=os.environ.get("WORLD_DB_PATH", "data/world.sqlite3"),
    )
    parser.add_argument("--chunk-size", type=int, default=12)
    parser.add_argument("--threshold", type=float, default=0.72)
    parser.add_argument("--minimum-dimension", type=float, default=0.6)
    args = parser.parse_args(argv)

    store = create_world_store(sqlite_path=Path(args.sqlite))
    try:
        final_state = store.get_state(args.session)
        if final_state is None:
            raise SystemExit(f"会话不存在: {args.session}")
        events = store.list_events(args.session)
        turns = store.list_turns(args.session)
        evaluator = LLMTrajectoryEvaluator(
            chunk_size=args.chunk_size,
            threshold=args.threshold,
            minimum_dimension=args.minimum_dimension,
        )
        report = evaluator.evaluate(
            events,
            final_state=final_state,
            turns=turns,
        )
        print(
            json.dumps(
                report.dict(),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if report.passed else 2
    finally:
        if isinstance(store, QdrantBackedWorldStore):
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
