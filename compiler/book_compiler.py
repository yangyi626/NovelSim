"""小说编译器 D：全书消歧、多时间线与分层快照。

这一层不负责线程、SQLite 或 HTTP。它只把章节按全书范围持续编译，并产出：

- 稳定的全局人物身份与跨时间线出现记录；
- 章节、卷、全书三级 WorldState 快照；
- 可交给真实 LLM 长轨迹评分器的合成事件序列；
- 可写入 WorldPackage manifest 的 D 阶段元数据。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from engine.event import state_hash
from world_schema import StatePatch, WorldEvent, WorldState

from .extractors import RawEntity, SceneExtraction
from .scene_compiler import (
    ChapterCompileResult,
    ChapterCompiler,
    EntityRegistry,
    StoryEvolutionAccumulator,
)
from .text_loader import Chapter


def _normalise_identity(value: str) -> str:
    return re.sub(r"[\W_]+", "", (value or "").strip().lower())


def _global_character_id(identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"char_global_{digest}"


@dataclass
class GlobalEntityResolver:
    """在整本书范围内维护身份键和别名映射。

    ``EntityRegistry`` 继续负责正式实体注册；本类在抽取进入注册表前写入
    canonical_id，并保留同一人物在不同时间线/肉身中的出现记录。
    """

    identity_index: Dict[str, str] = field(default_factory=dict)
    alias_index: Dict[str, str] = field(default_factory=dict)
    ambiguous_aliases: Dict[str, List[str]] = field(default_factory=dict)

    def prepare(
        self,
        extraction: SceneExtraction,
        registry: EntityRegistry,
        *,
        chapter_index: int,
        timeline_id: str,
    ) -> SceneExtraction:
        for raw in extraction.entities:
            if (raw.entity_type or "character").lower() != "character":
                continue
            self._resolve_character(raw, registry)
            if not raw.timeline_id:
                raw.timeline_id = timeline_id
        return extraction

    def _resolve_character(
        self,
        raw: RawEntity,
        registry: EntityRegistry,
    ) -> None:
        if raw.canonical_id and raw.canonical_id in registry.characters:
            canonical_id = raw.canonical_id
        else:
            identity_key = _normalise_identity(raw.global_identity)
            canonical_id = self.identity_index.get(identity_key, "") if identity_key else ""
            alias_candidates = set()
            if not identity_key:
                alias_candidates = {
                    self.alias_index[key]
                    for name in [raw.raw_name, *raw.aliases]
                    for key in [_normalise_identity(name)]
                    if key and key in self.alias_index
                }
                if len(alias_candidates) == 1:
                    canonical_id = next(iter(alias_candidates))
            if not canonical_id and not identity_key:
                registry_match = next(
                    (
                        registry.resolve_name(name)
                        for name in [raw.raw_name, *raw.aliases]
                        if registry.resolve_name(name) in registry.characters
                    ),
                    None,
                )
                canonical_id = registry_match or ""
            if not canonical_id:
                material = identity_key or _normalise_identity(raw.raw_name)
                canonical_id = _global_character_id(material or raw.raw_name)
            raw.canonical_id = canonical_id
            if identity_key:
                existing = self.identity_index.get(identity_key)
                if existing and existing != canonical_id:
                    self.ambiguous_aliases.setdefault(identity_key, [])
                    for item in (existing, canonical_id):
                        if item not in self.ambiguous_aliases[identity_key]:
                            self.ambiguous_aliases[identity_key].append(item)
                else:
                    self.identity_index[identity_key] = canonical_id

        for name in [raw.raw_name, *raw.aliases]:
            key = _normalise_identity(name)
            if not key:
                continue
            existing = self.alias_index.get(key)
            if existing and existing != canonical_id:
                values = self.ambiguous_aliases.setdefault(key, [])
                for item in (existing, canonical_id):
                    if item not in values:
                        values.append(item)
                continue
            self.alias_index[key] = canonical_id

    def record_occurrences(
        self,
        extraction: SceneExtraction,
        registry: EntityRegistry,
        state: WorldState,
        *,
        chapter_index: int,
        timeline_id: str,
        volume_id: str,
    ) -> None:
        for raw in extraction.entities:
            if (raw.entity_type or "character").lower() != "character":
                continue
            character_id = raw.canonical_id or registry.resolve_name(raw.raw_name)
            character = state.characters.get(character_id or "")
            if character is None:
                continue
            occurrences = character.attrs.setdefault("book_occurrences", [])
            occurrence = {
                "chapter": chapter_index,
                "timeline_id": raw.timeline_id or timeline_id,
                "volume_id": volume_id,
                "name": raw.raw_name,
                "aliases": list(raw.aliases),
                "global_identity": raw.global_identity,
                "incarnation": raw.incarnation,
                "evidence": raw.evidence,
            }
            signature = (
                occurrence["chapter"],
                occurrence["timeline_id"],
                occurrence["name"],
                occurrence["incarnation"],
            )
            if not any(
                (
                    item.get("chapter"),
                    item.get("timeline_id"),
                    item.get("name"),
                    item.get("incarnation"),
                )
                == signature
                for item in occurrences
                if isinstance(item, dict)
            ):
                occurrences.append(occurrence)
            if raw.global_identity:
                character.attrs["global_identity"] = raw.global_identity
            if raw.incarnation:
                incarnations = character.attrs.setdefault("incarnations", [])
                if raw.incarnation not in incarnations:
                    incarnations.append(raw.incarnation)


@dataclass
class HierarchicalSnapshot:
    snapshot_id: str
    level: str
    chapter_start: int
    chapter_end: int
    volume_id: str
    timeline_ids: List[str]
    state: WorldState

    def metadata(self) -> Dict:
        return {
            "snapshot_id": self.snapshot_id,
            "level": self.level,
            "chapter_start": self.chapter_start,
            "chapter_end": self.chapter_end,
            "volume_id": self.volume_id,
            "timeline_ids": list(self.timeline_ids),
            "state_hash": state_hash(self.state),
            "character_count": len(self.state.characters),
            "plot_count": len(self.state.plot),
        }


@dataclass
class BookCompileResult:
    chapter_results: List[ChapterCompileResult] = field(default_factory=list)
    source_chapters: List[int] = field(default_factory=list)
    snapshots: List[HierarchicalSnapshot] = field(default_factory=list)
    trajectory_events: List[WorldEvent] = field(default_factory=list)
    timelines: Dict[str, List[int]] = field(default_factory=dict)
    volumes: Dict[str, List[int]] = field(default_factory=dict)
    character_state_updates: int = 0
    foreshadow_updates: int = 0
    goal_updates: int = 0
    warnings: List[str] = field(default_factory=list)
    interrupted: str = ""
    global_identity_count: int = 0
    ambiguous_alias_count: int = 0

    def manifest(self) -> Dict:
        return {
            "stage": "D",
            "source_chapters": list(self.source_chapters),
            "timelines": {
                key: list(value) for key, value in self.timelines.items()
            },
            "volumes": {
                key: list(value) for key, value in self.volumes.items()
            },
            "snapshots": [item.metadata() for item in self.snapshots],
            "global_identity_count": self.global_identity_count,
            "ambiguous_alias_count": self.ambiguous_alias_count,
            "character_state_updates": self.character_state_updates,
            "foreshadow_updates": self.foreshadow_updates,
            "goal_updates": self.goal_updates,
            "trajectory_event_count": len(self.trajectory_events),
            "warnings": list(self.warnings),
            "interrupted": self.interrupted,
        }


class BookCompiler:
    """全书编译运行单元。

    ``timeline_plan`` 是 ``chapter -> timeline_id``；未指定章节归入
    ``timeline_root``。卷边界默认按固定章节数划分，也可传入
    ``volume_plan`` 精确指定。
    """

    def __init__(
        self,
        extractor=None,
        *,
        chapter_compiler: Optional[ChapterCompiler] = None,
        entity_resolver: Optional[GlobalEntityResolver] = None,
        evolution_accumulator: Optional[StoryEvolutionAccumulator] = None,
        volume_size: int = 20,
    ):
        self._chapter_compiler = chapter_compiler or ChapterCompiler(
            extractor=extractor,
        )
        self._resolver = entity_resolver or GlobalEntityResolver()
        self._evolution = (
            evolution_accumulator or StoryEvolutionAccumulator()
        )
        self.volume_size = max(1, int(volume_size))

    def compile(
        self,
        chapters: List[Chapter],
        registry: EntityRegistry,
        state: WorldState,
        *,
        timeline_plan: Optional[Dict[int, str]] = None,
        volume_plan: Optional[Dict[int, str]] = None,
        max_scenes_per_chapter: Optional[int] = None,
        stop_requested: Optional[Callable[[], str]] = None,
        on_chapter_started: Optional[Callable[[Chapter], None]] = None,
        on_chapter_completed: Optional[
            Callable[[Chapter, ChapterCompileResult, HierarchicalSnapshot], None]
        ] = None,
    ) -> BookCompileResult:
        result = BookCompileResult()
        timeline_plan = timeline_plan or {}
        volume_plan = volume_plan or {}
        ordered = sorted(chapters, key=lambda item: item.index)
        event_version = 0
        chapter_timeline_ids: Dict[int, List[str]] = {}

        for position, chapter in enumerate(ordered):
            stop_reason = stop_requested() if stop_requested else ""
            if stop_reason:
                result.interrupted = stop_reason
                break
            timeline_id = str(
                timeline_plan.get(chapter.index) or "timeline_root"
            )
            volume_id = str(
                volume_plan.get(chapter.index)
                or f"volume_{position // self.volume_size + 1:04d}"
            )
            result.timelines.setdefault(timeline_id, []).append(chapter.index)
            result.volumes.setdefault(volume_id, []).append(chapter.index)
            state.flags["compiler.current_timeline"] = timeline_id
            state.flags["compiler.current_volume"] = volume_id
            if on_chapter_started:
                on_chapter_started(chapter)

            def prepare(extraction: SceneExtraction) -> SceneExtraction:
                return self._resolver.prepare(
                    extraction,
                    registry,
                    chapter_index=chapter.index,
                    timeline_id=timeline_id,
                )

            chapter_result = self._chapter_compiler.compile(
                chapter,
                registry,
                state,
                max_scenes=max_scenes_per_chapter,
                extraction_hook=prepare,
            )
            result.chapter_results.append(chapter_result)
            result.source_chapters.append(chapter.index)
            result.warnings.extend(chapter_result.warnings)

            for scene_result in chapter_result.scene_results:
                extraction = scene_result.extraction
                detected_timelines = {
                    entity.timeline_id
                    for entity in extraction.entities
                    if entity.timeline_id
                }
                if detected_timelines:
                    chapter_timeline_ids.setdefault(
                        chapter.index,
                        [],
                    )
                    for detected in sorted(detected_timelines):
                        if detected not in chapter_timeline_ids[chapter.index]:
                            chapter_timeline_ids[chapter.index].append(
                                detected
                            )
                character_count, foreshadow_count, goal_count, warnings = (
                    self._evolution.apply(
                        chapter.index,
                        extraction,
                        registry,
                        state,
                    )
                )
                result.character_state_updates += character_count
                result.foreshadow_updates += foreshadow_count
                result.goal_updates += goal_count
                result.warnings.extend(warnings)
                self._resolver.record_occurrences(
                    extraction,
                    registry,
                    state,
                    chapter_index=chapter.index,
                    timeline_id=timeline_id,
                    volume_id=volume_id,
                )
                event_version += 1
                result.trajectory_events.append(
                    self._trajectory_event(
                        extraction,
                        scene_result.applied_patch,
                        registry,
                        event_version,
                        timeline_id,
                        chapter.index,
                    )
                )

            effective_timelines = chapter_timeline_ids.get(
                chapter.index,
                [timeline_id],
            )
            if chapter.index not in timeline_plan:
                root_chapters = result.timelines.get(timeline_id, [])
                if chapter.index in root_chapters:
                    root_chapters.remove(chapter.index)
                if not root_chapters:
                    result.timelines.pop(timeline_id, None)
                for detected in effective_timelines:
                    chapters_for_timeline = result.timelines.setdefault(
                        detected,
                        [],
                    )
                    if chapter.index not in chapters_for_timeline:
                        chapters_for_timeline.append(chapter.index)

            chapter_snapshot = HierarchicalSnapshot(
                snapshot_id=f"chapter_{chapter.index:06d}",
                level="chapter",
                chapter_start=chapter.index,
                chapter_end=chapter.index,
                volume_id=volume_id,
                timeline_ids=list(effective_timelines),
                state=state.copy(deep=True),
            )
            result.snapshots.append(chapter_snapshot)
            if on_chapter_completed:
                on_chapter_completed(
                    chapter,
                    chapter_result,
                    chapter_snapshot,
                )

            next_volume = (
                str(
                    volume_plan.get(ordered[position + 1].index)
                    or f"volume_{(position + 1) // self.volume_size + 1:04d}"
                )
                if position + 1 < len(ordered)
                else ""
            )
            if not next_volume or next_volume != volume_id:
                volume_chapters = result.volumes[volume_id]
                result.snapshots.append(
                    HierarchicalSnapshot(
                        snapshot_id=volume_id,
                        level="volume",
                        chapter_start=min(volume_chapters),
                        chapter_end=max(volume_chapters),
                        volume_id=volume_id,
                        timeline_ids=sorted(
                            {
                                detected
                                for index in volume_chapters
                                for detected in chapter_timeline_ids.get(
                                    index,
                                    [
                                        str(
                                            timeline_plan.get(index)
                                            or "timeline_root"
                                        )
                                    ],
                                )
                            }
                        ),
                        state=state.copy(deep=True),
                    )
                )

        if result.source_chapters and not result.interrupted:
            result.snapshots.append(
                HierarchicalSnapshot(
                    snapshot_id="book_full",
                    level="book",
                    chapter_start=min(result.source_chapters),
                    chapter_end=max(result.source_chapters),
                    volume_id="",
                    timeline_ids=sorted(result.timelines),
                    state=state.copy(deep=True),
                )
            )
        result.global_identity_count = len(self._resolver.identity_index)
        result.ambiguous_alias_count = len(
            self._resolver.ambiguous_aliases
        )
        state.flags["compiler.timelines"] = sorted(result.timelines)
        state.flags["compiler.volumes"] = sorted(result.volumes)
        return result

    @staticmethod
    def _trajectory_event(
        extraction: SceneExtraction,
        patch: StatePatch,
        registry: EntityRegistry,
        version: int,
        timeline_id: str,
        chapter_index: int,
    ) -> WorldEvent:
        raw_events = extraction.events
        actor_ids = list(
            dict.fromkeys(
                entity_id
                for raw in raw_events
                for name in raw.actor_names
                for entity_id in [registry.resolve_name(name)]
                if entity_id
            )
        )
        target_ids = list(
            dict.fromkeys(
                entity_id
                for raw in raw_events
                for name in raw.target_names
                for entity_id in [registry.resolve_name(name)]
                if entity_id
            )
        )
        summary = extraction.summary or "；".join(
            raw.summary for raw in raw_events if raw.summary
        )
        return WorldEvent(
            event_id=f"compile_{extraction.scene_id}",
            event_type="compiled_narrative",
            actor_ids=actor_ids,
            target_ids=target_ids,
            patch=patch,
            previous_version=version - 1,
            new_version=version,
            summary=summary,
            timeline_id=timeline_id,
            chapter_index=chapter_index,
        )
