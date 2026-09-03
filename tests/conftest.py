"""pytest 共享 fixture。"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile

import pytest


# 许多 Web 测试会在模块导入时构造 ``web.app``。提前把它的默认持久化
# 路径隔离到一次性临时目录，避免导入阶段触碰开发者的真实 data/ 数据库。
_TEST_RUNTIME_DIR = Path(tempfile.mkdtemp(prefix="novelsim-pytest-"))
for _env_name, _filename in (
    ("WORLD_DB_PATH", "world.sqlite3"),
    ("COMPILER_DB_PATH", "compiler.sqlite3"),
    ("AUTH_DB_PATH", "auth.sqlite3"),
    ("WORLD_PACKAGE_DIR", "worlds"),
):
    os.environ[_env_name] = str(_TEST_RUNTIME_DIR / _filename)


def pytest_sessionfinish(session, exitstatus):
    """测试会话结束后删除导入阶段和全局 fixture 产生的临时数据。"""

    shutil.rmtree(_TEST_RUNTIME_DIR, ignore_errors=True)

from examples.huarong_lane import build_snapshot
from examples.huarong_lane.scenario import (
    LIN,
    NIGHT,
    OUTER_ROBE,
    QINGQING,
    SCENE_ID,
)


@pytest.fixture
def snapshot():
    """每次测试一份干净的锚点前快照。"""
    return build_snapshot()
