"""世界存储的后端无关契约。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

from world_schema import WorldEvent, WorldState

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
    ) -> str:
        ...

    def get_state(self, session_id: str) -> Optional[WorldState]:
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
