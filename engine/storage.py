"""世界存储的后端无关契约。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Sequence

from world_schema import WorldEvent, WorldState

from .chapter_progression import (
    CampaignProgression,
    SessionLineage,
    SettlementReceipt,
    TransitionRequest,
    TransitionResult,
    UnlockGrant,
)
from .manuscript import ManuscriptPassage, ManuscriptRevision, WorldlineManuscript
from .persistence import MemoryRecord, SessionMetadata, TurnRecord


class WorldStore(Protocol):
    """SQLite 与 PostgreSQL 必须共同实现的公开接口。"""

    def create_session(
        self,
        state: WorldState,
        *,
        default_actor_id: str,
        world_package_id: str,
        session_id: Optional[str] = None,
        save_name: str = "华容巷世界线",
        book_id: str = "",
        entry_id: str = "",
        chapter_number: int = 0,
        entry_revision: int = 0,
    ) -> str:
        ...

    def get_state(self, session_id: str) -> Optional[WorldState]:
        ...

    def get_state_at_version(
        self,
        session_id: str,
        world_version: int,
    ) -> Optional[WorldState]:
        ...

    def get_metadata(
        self,
        session_id: str,
    ) -> Optional[SessionMetadata]:
        ...

    def list_sessions(self) -> List[SessionMetadata]:
        ...

    def rename_session(self, session_id: str, save_name: str) -> None:
        ...

    def delete_session(self, session_id: str) -> bool:
        ...

    def commit_turn(
        self,
        session_id: str,
        *,
        expected_version: int,
        new_state: WorldState,
        event: WorldEvent,
        player_input: str = "",
        turn_payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        ...

    def append_turn(
        self,
        session_id: str,
        *,
        expected_version: int,
        player_input: str,
        turn_payload: Dict[str, Any],
    ) -> None:
        ...

    def list_events(self, session_id: str) -> List[WorldEvent]:
        ...

    def list_turns(self, session_id: str) -> List[TurnRecord]:
        ...

    def ensure_session_lineage(self, session_id: str) -> SessionLineage:
        ...

    def get_session_lineage(
        self,
        session_id: str,
    ) -> Optional[SessionLineage]:
        ...

    def record_settlement_progression(
        self,
        session_id: str,
        *,
        settlement_event_id: str,
        settled_world_version: int,
        ending_id: str,
        ending_title: str,
        summary: str,
        reward_points: int,
        idempotency_key: str,
        unlocks: Sequence[UnlockGrant] = (),
    ) -> SettlementReceipt:
        ...

    def create_or_get_child_session(
        self,
        request: TransitionRequest,
    ) -> TransitionResult:
        ...

    def list_campaign_progression(
        self,
        campaign_id: str,
    ) -> CampaignProgression:
        ...

    def ensure_manuscript(self, session_id: str) -> WorldlineManuscript:
        ...

    def get_manuscript_for_session(
        self,
        session_id: str,
    ) -> Optional[WorldlineManuscript]:
        ...

    def reserve_manuscript_passage(
        self,
        session_id: str,
        source_event_ids: Sequence[str],
        *,
        generation_kind: str = "deterministic",
    ) -> ManuscriptPassage:
        ...

    def complete_manuscript_passage(
        self,
        passage_id: str,
        revision: ManuscriptRevision,
        *,
        expected_current_revision: Optional[int] = None,
    ) -> ManuscriptPassage:
        ...

    def fail_manuscript_passage(
        self,
        passage_id: str,
        error: str,
    ) -> ManuscriptPassage:
        ...

    def get_manuscript_passage(
        self,
        passage_id: str,
    ) -> Optional[ManuscriptPassage]:
        ...

    def list_manuscript_passages(
        self,
        session_id: str,
    ) -> List[ManuscriptPassage]:
        ...

    def list_campaign_manuscript_passages(
        self,
        session_id: str,
    ) -> List[ManuscriptPassage]:
        ...

    def list_manuscript_passage_revisions(
        self,
        passage_id: str,
    ) -> List[ManuscriptRevision]:
        ...

    def select_manuscript_passage_revision(
        self,
        passage_id: str,
        revision_number: int,
        *,
        expected_current_revision: Optional[int] = None,
    ) -> ManuscriptPassage:
        ...

    def record_character_memories(
        self,
        session_id: str,
        character_ids: List[str],
        *,
        source_event_id: str,
        world_version: int,
        content: str,
        importance: float = 0.6,
        memory_type: str = "episodic",
        evidence_event_ids: Optional[List[str]] = None,
        claim_fact_id: str = "",
        claim_belief: str = "",
        claim_confidence: float = 0.0,
        semantic_score: float = 0.0,
    ) -> List[str]:
        ...

    def search_character_memories(
        self,
        session_id: str,
        character_id: str,
        query: str,
        *,
        limit: int = 4,
    ) -> List[MemoryRecord]:
        ...

    def get_character_memories(
        self,
        memory_ids: List[str],
    ) -> List[MemoryRecord]:
        ...

    def list_character_memories(
        self,
        session_id: str,
        *,
        character_id: Optional[str] = None,
        memory_type: Optional[str] = None,
    ) -> List[MemoryRecord]:
        ...

    def delete_character_memories(
        self,
        session_id: str,
        *,
        memory_type: Optional[str] = None,
    ) -> int:
        ...

    def prune_character_memories(
        self,
        session_id: str,
        character_id: str,
        *,
        memory_type: str = "episodic",
        max_records: int = 500,
    ) -> int:
        ...

    def export_session(self, session_id: str) -> Dict[str, Any]:
        ...

    def import_session(
        self,
        backup: Dict[str, Any],
        *,
        save_name: Optional[str] = None,
    ) -> str:
        ...
