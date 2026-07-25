"""确定性占位 patch 生成器 (测试/兜底用)。

当不想调 LLM (TransitionProposer) 时，用这个基于 action_type 的规则映射
产出 patch。真实系统用 TransitionProposer；这里保留硬编码版用于:
- 确定性测试 (CI 可跑，不烧 token)
- LLM 不可用时的降级
"""

from __future__ import annotations

from world_schema import Action, Operation, OperationKind, StatePatch

# 避免循环 import，scenario 常量在调用时传入或硬编码
# 这里只做通用的 action_type -> patch 映射，小说特定逻辑放在 examples 里


def deterministic_patch(action: Action) -> StatePatch:
    """根据 action_type 产出确定性 patch (最小通用版)。

    小说特定的 patch 映射 (如华容巷的"拿外衫") 应在 examples 层覆盖。
    """
    # 默认: 纯观察/说话类行动不产生状态变化
    return StatePatch(operations=[])
