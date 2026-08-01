"""Deterministic trajectory quality filters before SFT dataset creation."""

from __future__ import annotations

from typing import Dict, Iterable, List

from pydantic import BaseModel, Field

from engine import GameTrajectory, replay_game_trajectory


class TrajectoryFilterConfig(BaseModel):
    require_replay_consistency: bool = True
    require_objective_success: bool = True
    reject_illegal_commit: bool = True
    reject_empty: bool = True
    max_illegal_proposals: int = Field(0, ge=0)

    class Config:
        extra = "forbid"


class RejectedTrajectory(BaseModel):
    episode_id: str
    reasons: List[str]

    class Config:
        extra = "forbid"


class TrajectoryFilterResult(BaseModel):
    accepted: List[GameTrajectory] = Field(default_factory=list)
    rejected: List[RejectedTrajectory] = Field(default_factory=list)
    reason_distribution: Dict[str, int] = Field(default_factory=dict)

    class Config:
        extra = "forbid"


def filter_trajectories(
    trajectories: Iterable[GameTrajectory],
    config: TrajectoryFilterConfig = TrajectoryFilterConfig(),
) -> TrajectoryFilterResult:
    accepted: List[GameTrajectory] = []
    rejected: List[RejectedTrajectory] = []
    reason_distribution: Dict[str, int] = {}
    for trajectory in trajectories:
        reasons: List[str] = []
        replay = replay_game_trajectory(trajectory)
        if config.require_replay_consistency and not replay.consistent:
            reasons.append("replay_inconsistent")
        if config.require_objective_success and not trajectory.objective_satisfied:
            reasons.append("objective_failed")
        illegal_commits = sum(
            int(step.failure.illegal_commit) for step in trajectory.steps
        )
        if config.reject_illegal_commit and illegal_commits:
            reasons.append("illegal_commit")
        illegal_proposals = sum(
            int(step.failure.illegal_proposal) for step in trajectory.steps
        )
        if illegal_proposals > config.max_illegal_proposals:
            reasons.append("illegal_proposal_limit")
        if config.reject_empty and not trajectory.steps:
            reasons.append("empty_trajectory")
        if reasons:
            rejected.append(RejectedTrajectory(
                episode_id=trajectory.episode_id,
                reasons=reasons,
            ))
            for reason in reasons:
                reason_distribution[reason] = (
                    reason_distribution.get(reason, 0) + 1
                )
        else:
            accepted.append(trajectory)
    return TrajectoryFilterResult(
        accepted=accepted,
        rejected=rejected,
        reason_distribution=dict(sorted(reason_distribution.items())),
    )

