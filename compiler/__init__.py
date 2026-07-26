"""小说世界编译器：把整本 TXT 自动编译成 WorldPackage。

对应 plan 第十二步。流水线：
    TXT 导入
    → 文本清洗 / 编码探测
    → 章节切分 (确定性)
    → 场景切分 (启发式，可被 LLM 修正)
    → 实体 / 关系 / 事件 / 规则抽取 (LLM)
    → 实体消歧 (跨章节)
    → 累积成 WorldState 快照
    → 发布 WorldPackage (JSON + 可选直接构建 WorldState)

开发顺序遵循 plan 第十二节的 A→B→C→D：
    A. 单场景编译   (SceneCompiler)
    B. 单章节编译   (ChapterCompiler，多场景 + 别名消歧)
    C. 单卷编译     (已完成：跨章状态/伏笔/目标演化)
    D. 全书编译     (已完成：全局身份、多时间线与分层快照)

当前已打通 A + B + C + D，用《第一狂妃》前几章做端到端验证，
产出的 WorldPackage 与手工版 examples/huarong_lane 可对照。
"""

from .text_loader import (
    load_novel,
    Chapter,
    Scene,
    split_chapters,
    split_scenes,
    clean_text,
)
from .extractors import (
    EntityExtractor,
    SceneExtraction,
    RawEntity,
    RawRelation,
    RawEvent,
    RawCharacterState,
    RawForeshadow,
    RawGoalEvolution,
    RawWorldRule,
)
from .scene_compiler import (
    EntityRegistry,
    SceneCompiler,
    SceneCompileResult,
    ChapterCompiler,
    ChapterCompileResult,
    PackageBuilder,
    StoryEvolutionAccumulator,
    VolumeCompiler,
    VolumeCompileResult,
    WorldPackage,
)
from .cli import compile_book, compile_novel
from .book_compiler import (
    BookCompiler,
    BookCompileResult,
    GlobalEntityResolver,
    HierarchicalSnapshot,
)
from .job_store import (
    ACTIVE_JOB_STATUSES,
    JOB_STATUSES,
    CompilationJob,
    CompilationJobConflict,
    CompilationJobError,
    CompilationJobNotFound,
    CompilationJobStore,
)
from .job_runner import (
    EXTRACTOR_PROMPT_VERSION,
    CacheAwareExtractor,
    CompilationJobManager,
    CompilationJobRunner,
    CompilationQualityGate,
)

__all__ = [
    # text loader
    "load_novel",
    "Chapter",
    "Scene",
    "split_chapters",
    "split_scenes",
    "clean_text",
    # extractors
    "EntityExtractor",
    "SceneExtraction",
    "RawEntity",
    "RawRelation",
    "RawEvent",
    "RawCharacterState",
    "RawForeshadow",
    "RawGoalEvolution",
    "RawWorldRule",
    # compilers
    "EntityRegistry",
    "SceneCompiler",
    "SceneCompileResult",
    "ChapterCompiler",
    "ChapterCompileResult",
    "PackageBuilder",
    "StoryEvolutionAccumulator",
    "VolumeCompiler",
    "VolumeCompileResult",
    "WorldPackage",
    "BookCompiler",
    "BookCompileResult",
    "GlobalEntityResolver",
    "HierarchicalSnapshot",
    # task system
    "ACTIVE_JOB_STATUSES",
    "JOB_STATUSES",
    "CompilationJob",
    "CompilationJobConflict",
    "CompilationJobError",
    "CompilationJobNotFound",
    "CompilationJobStore",
    "EXTRACTOR_PROMPT_VERSION",
    "CacheAwareExtractor",
    "CompilationJobManager",
    "CompilationJobRunner",
    "CompilationQualityGate",
    # entrypoint
    "compile_novel",
    "compile_book",
]
