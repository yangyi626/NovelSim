"""pytest 共享 fixture。"""

import pytest

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
