"""无需 LLM 的长轨迹一致性评测与发布门禁。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from world_schema import WorldEvent, WorldState

from .event import state_hash
from .patch import apply_patch
from .patch_validator import validate_patch


@dataclass(frozen=True)
class TrajectoryViolation:
    event_id: str
    world_version: int
    code: str
    message: str


@dataclass
class TrajectoryReport:
    event_count: int = 0
    initial_version: int = 0
    final_version: int = 0
    final_state_hash: str = ""
    violations: List[TrajectoryViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.violations

    def summary(self) -> str:
        if self.passed:
            return (
                f"PASS: {self.event_count} events, "
                f"version {self.initial_version}->{self.final_version}"
            )
        return (
            f"FAIL: {len(self.violations)} violations in "
            f"{self.event_count} events"
        )


def evaluate_trajectory(
    initial_state: WorldState,
    events: List[WorldEvent],
    *,
    expected_final_state: Optional[WorldState] = None,
) -> TrajectoryReport:
    """逐事件检查版本链、实体引用、角色存活和 Patch 合法性。"""

    state = initial_state.copy(deep=True)
    report = TrajectoryReport(
        event_count=len(events),
        initial_version=state.version,
        final_version=state.version,
    )

    for event in events:
        if (
            event.previous_version != state.version
            or event.new_version != state.version + 1
        ):
            report.violations.append(
                TrajectoryViolation(
                    event_id=event.event_id,
                    world_version=event.new_version,
                    code="version_chain",
                    message=(
                        f"expected {state.version}->{state.version + 1}, "
                        f"got {event.previous_version}->{event.new_version}"
                    ),
                )
            )
            break

        for character_id in event.actor_ids:
            if character_id not in state.characters:
                report.violations.append(
                    TrajectoryViolation(
                        event_id=event.event_id,
                        world_version=event.new_version,
                        code="unknown_character",
                        message=f"事件引用未知角色: {character_id}",
                    )
                )
        known_targets = (
            set(state.characters)
            | set(state.items)
            | set(state.locations)
            | set(state.plot)
            | set(state.facts)
            | set(state.alliances)
        )
        for target_id in event.target_ids:
            if target_id not in known_targets:
                report.violations.append(
                    TrajectoryViolation(
                        event_id=event.event_id,
                        world_version=event.new_version,
                        code="unknown_target",
                        message=f"事件引用未知目标: {target_id}",
                    )
                )
        for character_id in event.actor_ids:
            character = state.characters.get(character_id)
            if character is not None and not character.is_alive:
                report.violations.append(
                    TrajectoryViolation(
                        event_id=event.event_id,
                        world_version=event.new_version,
                        code="dead_actor",
                        message=f"死亡角色仍在行动: {character_id}",
                    )
                )

        patch_check = validate_patch(state, event.patch)
        if not patch_check.valid:
            report.violations.append(
                TrajectoryViolation(
                    event_id=event.event_id,
                    world_version=event.new_version,
                    code="invalid_patch",
                    message=patch_check.why(),
                )
            )
            break
        try:
            state = apply_patch(state, event.patch)
            state.version = event.new_version
            # 强制重新走一遍 Schema，捕捉长局累积出的非法状态。
            state = WorldState.parse_obj(state.dict())
        except Exception as exc:  # noqa: BLE001
            report.violations.append(
                TrajectoryViolation(
                    event_id=event.event_id,
                    world_version=event.new_version,
                    code="state_apply",
                    message=str(exc),
                )
            )
            break

    report.final_version = state.version
    report.final_state_hash = state_hash(state)
    if expected_final_state is not None:
        expected_hash = state_hash(expected_final_state)
        if (
            expected_final_state.version != state.version
            or expected_hash != report.final_state_hash
        ):
            report.violations.append(
                TrajectoryViolation(
                    event_id=events[-1].event_id if events else "",
                    world_version=state.version,
                    code="final_state_mismatch",
                    message=(
                        "回放终态与权威快照不一致: "
                        f"expected v{expected_final_state.version}/"
                        f"{expected_hash[:12]}, got v{state.version}/"
                        f"{report.final_state_hash[:12]}"
                    ),
                )
            )
    return report
