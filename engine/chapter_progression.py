"""Typed records for authoritative cross-chapter progression persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from world_schema import WorldEvent, WorldState


@dataclass(frozen=True)
class CampaignRecord:
    campaign_id: str
    root_session_id: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SettlementReceipt:
    settlement_id: str
    campaign_id: str
    session_id: str
    world_package_id: str
    settlement_event_id: str
    settled_world_version: int
    ending_id: str
    ending_title: str
    summary: str
    reward_points: int
    idempotency_key: str
    created_at: str


@dataclass(frozen=True)
class RewardLedgerRecord:
    ledger_id: str
    campaign_id: str
    settlement_id: str
    session_id: str
    points_delta: int
    reason: str
    created_at: str


@dataclass(frozen=True)
class UnlockGrant:
    unlock_key: str
    unlock_type: str = "world"
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UnlockRecord:
    unlock_id: str
    campaign_id: str
    source_settlement_id: str
    source_session_id: str
    unlock_key: str
    unlock_type: str
    payload: Dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class CarryoverManifest:
    manifest_id: str
    campaign_id: str
    parent_session_id: str
    child_session_id: str
    target_world_package_id: str
    source_settlement_id: str
    payload: Dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class SessionLineage:
    campaign_id: str
    session_id: str
    root_session_id: str
    parent_session_id: Optional[str]
    depth: int
    source_settlement_id: Optional[str]
    target_world_package_id: str
    created_at: str


@dataclass(frozen=True)
class TransitionRequest:
    parent_session_id: str
    target_world_package_id: str
    child_state: WorldState
    genesis_event: WorldEvent
    manifest: Dict[str, Any]
    default_actor_id: str
    idempotency_key: str
    target_book_id: str = ""
    target_entry_id: str = ""
    target_chapter_number: int = 0
    target_entry_revision: int = 0
    save_name: str = "新的世界线"
    child_session_id: Optional[str] = None


@dataclass(frozen=True)
class TransitionResult:
    transition_id: str
    idempotency_key: str
    parent_session_id: str
    target_world_package_id: str
    child_session_id: str
    settlement_id: str
    lineage: SessionLineage
    manifest: CarryoverManifest
    created: bool
    created_at: str


@dataclass(frozen=True)
class CampaignProgression:
    campaign: CampaignRecord
    lineage: Tuple[SessionLineage, ...]
    settlements: Tuple[SettlementReceipt, ...]
    rewards: Tuple[RewardLedgerRecord, ...]
    unlocks: Tuple[UnlockRecord, ...]
    manifests: Tuple[CarryoverManifest, ...]
    transitions: Tuple[TransitionResult, ...]
