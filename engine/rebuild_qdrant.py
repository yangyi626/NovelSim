"""从权威记忆表重建 Qdrant 派生索引。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional, Sequence

from dotenv import load_dotenv

from .qdrant_memory import QdrantBackedWorldStore
from .store_factory import create_world_store


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite",
        default=os.environ.get("WORLD_DB_PATH", "data/world.sqlite3"),
        help="SQLite 文件路径（默认读取 WORLD_DB_PATH）",
    )
    parser.add_argument(
        "--session",
        help="只重建指定世界线；不传则重建全部存档",
    )
    args = parser.parse_args(argv)
    store = create_world_store(
        sqlite_path=Path(args.sqlite),
        memory_vector_backend="qdrant",
    )
    if not isinstance(store, QdrantBackedWorldStore):
        raise RuntimeError("当前配置未启用 Qdrant")
    try:
        written = store.rebuild_qdrant_index(args.session)
    finally:
        store.close()
    print(f"Qdrant 记忆索引重建完成，共写入 {written} 条。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
