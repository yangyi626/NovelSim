"""为 Playwright 启动隔离的 NovelSim 测试服务。"""

from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

runtime = Path(tempfile.mkdtemp(prefix="novelsim-e2e-"))
os.environ["WORLD_DB_PATH"] = str(runtime / "world.sqlite3")
os.environ["COMPILER_DB_PATH"] = str(runtime / "compiler.sqlite3")
os.environ["AUTH_DB_PATH"] = str(runtime / "auth.sqlite3")
os.environ["WORLD_PACKAGE_DIR"] = str(runtime / "worlds")
os.environ["MEMORY_VECTOR_BACKEND"] = "fts5"
os.environ["QDRANT_PATH"] = str(runtime / "qdrant")
os.environ["COMPILER_QUALITY_GATE_ENABLED"] = "false"

import uvicorn  # noqa: E402

app_module = importlib.import_module("web.app")


app_module.AUTH.bootstrap_admin("admin", "e2e-password")
for username, roles in [
    ("creator", {"creator"}),
    ("reviewer", {"reviewer"}),
    ("publisher", {"publisher"}),
]:
    app_module.AUTH.create_user(
        username=username,
        password="e2e-password",
        roles=roles,
    )

try:
    uvicorn.run(app_module.app, host="127.0.0.1", port=8876)
finally:
    closer = getattr(app_module.SESSIONS, "close", None)
    if callable(closer):
        closer()
    shutil.rmtree(runtime, ignore_errors=True)
