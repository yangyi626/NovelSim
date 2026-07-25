"""世界运行引擎 (内存版)。

本阶段不接数据库。所有状态用 Pydantic 模型在内存里流转，
验证"提交 Action -> 改 State -> 存 Event -> 重放 -> 状态一致"这条最小闭环。

后续接 PostgreSQL 时，apply_patch 的语义不变，只是持久化层换了。
"""

from .patch import apply_patch, PatchError
from .event import commit_event, replay_events
from .rules import RuleEngine, RuleCheckResult
from .action_parser import ActionParser, ParseError
from .transition import TransitionProposer
from .patch_validator import validate_patch, PatchCheckResult
from .narrative import NarrativeGenerator
from .narrative_consistency import check_narrative, NarrativeCheckResult
from .turn import TurnPipeline, TurnResult

__all__ = [
    "apply_patch",
    "PatchError",
    "commit_event",
    "replay_events",
    "RuleEngine",
    "RuleCheckResult",
    "ActionParser",
    "ParseError",
    "TransitionProposer",
    "validate_patch",
    "PatchCheckResult",
    "NarrativeGenerator",
    "check_narrative",
    "NarrativeCheckResult",
    "TurnPipeline",
    "TurnResult",
]
