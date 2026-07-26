"""世界运行引擎。

状态推演保持纯内存、纯函数语义；已提交的 WorldState / WorldEvent 可通过
SQLiteWorldStore 持久化。后续迁移 PostgreSQL 时，Turn Pipeline 无需改动。
"""

from .patch import apply_patch, PatchError
from .event import commit_event, replay_events
from .rules import RuleEngine, RuleCheckResult
from .action_parser import ActionParser, ParseError
from .transition import TransitionProposer
from .patch_validator import validate_patch, PatchCheckResult
from .narrative import NarrativeGenerator
from .narrative_consistency import check_narrative, NarrativeCheckResult
from .character_agent import CharacterAgent, candidate_to_action
from .agent_scheduler import (
    CharacterScheduler,
    AgentScheduleResult,
    NPCReaction,
    merge_patches,
)
from .turn import TurnPipeline, TurnResult
from .persistence import (
    MemoryRecord,
    PersistenceError,
    SessionMetadata,
    SessionNotFound,
    SQLiteWorldStore,
    TurnRecord,
    VersionConflict,
)
from .embeddings import (
    CachedMemoryEmbedder,
    EmbeddingError,
    MemoryEmbedder,
    OpenAICompatibleEmbedder,
    memory_embedder_from_env,
)
from .memory_retrieval_eval import (
    QueryRetrievalResult,
    RetrievalBenchmark,
    RetrievalDocument,
    RetrievalQuery,
    RetrievalReport,
    evaluate_retrieval,
    evaluate_store,
    load_retrieval_benchmark,
    seed_retrieval_benchmark,
)
from .postgres_persistence import PostgresWorldStore
from .qdrant_memory import (
    HybridRetrievalWeights,
    QdrantBackedWorldStore,
    QdrantMemoryHit,
    QdrantMemoryIndex,
)
from .store_factory import create_world_store
from .storage import WorldStore
from .memory_projection import (
    event_memory_characters,
    event_memory_content,
    rebuild_session_memories,
    record_event_memory,
)
from .reflection_memory import (
    ReflectionCandidate,
    ReflectionGenerator,
    ReflectionReport,
    filter_compatible_memories,
    memory_conflicts_with_state,
    reflect_character_memories,
    reflection_source_id,
)
from .trajectory_eval import (
    TrajectoryReport,
    TrajectoryViolation,
    evaluate_trajectory,
)
from .world_packages import (
    WorldPackageConflict,
    WorldPackageError,
    WorldPackageNotFound,
    WorldPackageRecord,
    WorldPackageStore,
    WorldPackageValidationError,
    validate_world_package_payload,
)

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
    "CharacterAgent",
    "candidate_to_action",
    "CharacterScheduler",
    "AgentScheduleResult",
    "NPCReaction",
    "merge_patches",
    "TurnPipeline",
    "TurnResult",
    "MemoryRecord",
    "PersistenceError",
    "SessionMetadata",
    "SessionNotFound",
    "SQLiteWorldStore",
    "TurnRecord",
    "VersionConflict",
    "EmbeddingError",
    "CachedMemoryEmbedder",
    "MemoryEmbedder",
    "OpenAICompatibleEmbedder",
    "memory_embedder_from_env",
    "QueryRetrievalResult",
    "RetrievalBenchmark",
    "RetrievalDocument",
    "RetrievalQuery",
    "RetrievalReport",
    "evaluate_retrieval",
    "evaluate_store",
    "load_retrieval_benchmark",
    "seed_retrieval_benchmark",
    "PostgresWorldStore",
    "HybridRetrievalWeights",
    "QdrantBackedWorldStore",
    "QdrantMemoryHit",
    "QdrantMemoryIndex",
    "create_world_store",
    "WorldStore",
    "event_memory_characters",
    "event_memory_content",
    "rebuild_session_memories",
    "record_event_memory",
    "ReflectionCandidate",
    "ReflectionGenerator",
    "ReflectionReport",
    "filter_compatible_memories",
    "memory_conflicts_with_state",
    "reflect_character_memories",
    "reflection_source_id",
    "TrajectoryReport",
    "TrajectoryViolation",
    "evaluate_trajectory",
    "WorldPackageConflict",
    "WorldPackageError",
    "WorldPackageNotFound",
    "WorldPackageRecord",
    "WorldPackageStore",
    "WorldPackageValidationError",
    "validate_world_package_payload",
]
