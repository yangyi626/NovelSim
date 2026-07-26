"""把本地 SQLite 世界线迁移到 PostgreSQL + pgvector。

用法：
    python -m engine.migrate_storage \
        --sqlite data/world.sqlite3 \
        --postgres-url postgresql://user:pass@localhost/transmigration
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .memory_projection import rebuild_session_memories
from .persistence import SQLiteWorldStore
from .postgres_persistence import PostgresWorldStore


def migrate(sqlite_path: Path, postgres_url: str) -> dict:
    source = SQLiteWorldStore(sqlite_path)
    target = PostgresWorldStore(postgres_url)
    migrated = {}
    for metadata in reversed(source.list_sessions()):
        backup = source.export_session(metadata.session_id)
        target_session_id = target.import_session(
            backup,
            save_name=metadata.save_name,
        )
        memory_count = rebuild_session_memories(
            target,
            target_session_id,
        )
        migrated[metadata.session_id] = {
            "target_session_id": target_session_id,
            "memory_records": memory_count,
        }
    return migrated


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="迁移 SQLite 世界线到 PostgreSQL + pgvector"
    )
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=Path("data/world.sqlite3"),
        help="SQLite 数据库路径",
    )
    parser.add_argument(
        "--postgres-url",
        default=os.environ.get("WORLD_DATABASE_URL", ""),
        help="目标 PostgreSQL URL；默认读取 WORLD_DATABASE_URL",
    )
    args = parser.parse_args(argv)
    if not args.postgres_url:
        parser.error(
            "请提供 --postgres-url 或设置 WORLD_DATABASE_URL"
        )
    result = migrate(args.sqlite.resolve(), args.postgres_url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
