"""场景编译器 + 章节编译器 + 世界包构建器。

把 extractors 产出的 SceneExtraction 累积成 WorldState：
    SceneExtraction (噪声容忍)
    → 实体消歧 (跨场景/跨章节 alias -> canonical_id 稳定化)
    → 升级成 world_schema 的 Character/Item/Location/Relation
    → 事件 patch 草稿 -> 严格 Operation (经 patch_validator 校验)
    → 累积进 WorldState
    → 产出 WorldPackage (JSON + 可直接 build_snapshot())

对应 plan 第十二步 A (单场景) + B (单章节)。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from world_schema import (
    Character,
    CharacterBelief,
    CharacterRelation,
    Item,
    Location,
    Operation,
    OperationKind,
    PlotArc,
    Rule,
    StatePatch,
    WorldRule,
    WorldState,
)
from world_schema.models import Belief
from engine.patch_validator import validate_patch

from .extractors import (
    RawEntity,
    RawEvent,
    RawRelation,
    RawWorldRule,
    SceneExtraction,
)
from .text_loader import Chapter, Scene


# ---------------------------------------------------------------------------
# 实体消歧注册表
# ---------------------------------------------------------------------------


@dataclass
class EntityRegistry:
    """跨场景/跨章节的实体消歧表。

    维护 alias -> canonical_id 的映射。新实体第一次出现时分配稳定 id，
    之后所有别名都指向它。这是"全局实体消歧"的基础 (plan 第十二步 D)。
    """

    # canonical_id -> 累积的实体信息
    characters: Dict[str, Character] = field(default_factory=dict)
    items: Dict[str, Item] = field(default_factory=dict)
    locations: Dict[str, Location] = field(default_factory=dict)
    # alias/raw_name -> canonical_id (快速查表)
    alias_index: Dict[str, str] = field(default_factory=dict)

    def known_entities(self) -> Dict[str, str]:
        """返回 alias -> canonical_id，喂给抽取器做消歧提示。"""
        return dict(self.alias_index)

    def resolve_or_register_character(self, raw: RawEntity) -> str:
        """返回该角色的稳定 id。已存在则复用，否则新建。"""
        # 1. 显式指定
        if raw.canonical_id and raw.canonical_id in self.characters:
            self._merge_character(self.characters[raw.canonical_id], raw)
            self._index_aliases(raw.canonical_id, raw)
            return raw.canonical_id
        # 2. 别名命中
        for name in [raw.raw_name, *raw.aliases]:
            if name and name in self.alias_index:
                cid = self.alias_index[name]
                self._merge_character(self.characters[cid], raw)
                self._index_aliases(cid, raw)
                return cid
        # 3. 新建
        cid = raw.canonical_id or self._mint_id("char", raw.raw_name)
        ch = Character(
            character_id=cid,
            display_name=raw.raw_name,
            aliases=list(raw.aliases),
            identity_tags=list(raw.identity_tags),
            attrs={"description": raw.description} if raw.description else {},
        )
        self.characters[cid] = ch
        self._index_aliases(cid, raw)
        return cid

    def resolve_or_register_item(self, raw: RawEntity) -> str:
        if raw.canonical_id and raw.canonical_id in self.items:
            return raw.canonical_id
        for name in [raw.raw_name, *raw.aliases]:
            if name and name in self.alias_index:
                return self.alias_index[name]
        iid = raw.canonical_id or self._mint_id("item", raw.raw_name)
        self.items[iid] = Item(
            item_id=iid,
            display_name=raw.raw_name,
            attrs={"description": raw.description} if raw.description else {},
        )
        self._index_aliases(iid, raw)
        return iid

    def resolve_or_register_location(self, raw: RawEntity) -> str:
        if raw.canonical_id and raw.canonical_id in self.locations:
            return raw.canonical_id
        for name in [raw.raw_name, *raw.aliases]:
            if name and name in self.alias_index:
                return self.alias_index[name]
        lid = raw.canonical_id or self._mint_id("loc", raw.raw_name)
        self.locations[lid] = Location(
            location_id=lid,
            display_name=raw.raw_name,
            attrs={"description": raw.description} if raw.description else {},
        )
        self._index_aliases(lid, raw)
        return lid

    def resolve_name(self, name: str) -> Optional[str]:
        """仅查表，不新建。用于把事件 actor_names 映射到 id。"""
        return self.alias_index.get(name)

    # ---- 内部 ----

    def _mint_id(self, prefix: str, name: str) -> str:
        slug = _slug(name) or "x"
        base = f"{prefix}_{slug}"
        cid = base
        n = 2
        existing = self._bucket_for_prefix(prefix)
        while cid in existing:
            cid = f"{base}_{n}"
            n += 1
        return cid

    def _bucket_for_prefix(self, prefix: str):
        if prefix == "char":
            return self.characters
        if prefix == "item":
            return self.items
        return self.locations

    def _index_aliases(self, cid: str, raw: RawEntity) -> None:
        for name in [raw.raw_name, *raw.aliases]:
            if name and name not in self.alias_index:
                self.alias_index[name] = cid

    def _merge_character(self, base: Character, raw: RawEntity) -> None:
        """把新抽取信息合并进已有角色 (补别名/标签/描述)。"""
        for a in raw.aliases:
            if a and a not in base.aliases:
                base.aliases.append(a)
        for t in raw.identity_tags:
            if t and t not in base.identity_tags:
                base.identity_tags.append(t)
        if raw.description and not base.attrs.get("description"):
            base.attrs["description"] = raw.description


def _slug(name: str) -> str:
    """把中文名转成 ascii id 段。优先用拼音近似失败时回落 hash。"""
    s = re.sub(r"[^\w]", "", (name or "").strip())
    if s and re.match(r"[A-Za-z]", s):
        return s.lower()[:16]
    # 中文等非 ascii：用稳定短 hash
    return f"h{abs(hash(name)) % 100000:05d}"


# ---------------------------------------------------------------------------
# 关系累积
# ---------------------------------------------------------------------------


def accumulate_relations(
    registry: EntityRegistry,
    raw_relations: List[RawRelation],
) -> List[CharacterRelation]:
    """把抽取的关系合并进 registry，返回更新后的关系列表。"""
    out: List[CharacterRelation] = []
    for rr in raw_relations:
        src = registry.resolve_name(rr.source_name)
        tgt = registry.resolve_name(rr.target_name)
        if not src or not tgt:
            continue
        rel = _find_relation(registry, src, tgt)
        if rel is None:
            rel = CharacterRelation(source_id=src, target_id=tgt)
            registry._relations.append(rel)  # type: ignore[attr-defined]
        if rr.public_relation and not rel.public_relation:
            rel.public_relation = rr.public_relation
        if rr.private_relation and not rel.private_relation:
            rel.private_relation = rr.private_relation
        for dim, val in rr.dimensions.items():
            if hasattr(rel.dimensions, dim):
                # 取置信度加权平均，避免后写覆盖
                cur = getattr(rel.dimensions, dim) or 0.0
                w = max(0.1, min(1.0, rr.confidence))
                merged = cur * (1 - w) + _clamp(val, dim) * w
                setattr(rel.dimensions, dim, merged)
        out.append(rel)
    return out


def _find_relation(registry: EntityRegistry, src: str, tgt: str):
    rels = getattr(registry, "_relations", None)
    if rels is None:
        setattr(registry, "_relations", [])
        rels = registry._relations  # type: ignore[attr-defined]
    for r in rels:
        if r.source_id == src and r.target_id == tgt:
            return r
    return None


def _clamp(val: float, dim: str) -> float:
    non_negative = {"fear", "hostility"}
    if dim in non_negative:
        return max(0.0, min(1.0, val))
    return max(-1.0, min(1.0, val))


# ---------------------------------------------------------------------------
# 事件草稿 -> 严格 Operation (校验后)
# ---------------------------------------------------------------------------


def draft_ops_to_patch(
    draft_ops: List[Dict],
    registry: EntityRegistry,
    state: WorldState,
) -> StatePatch:
    """把事件草稿 op (含 name 引用) 转成严格 StatePatch。

    - name 字段 (actor_names 风格) 解析成 id
    - 非法 op / 未知实体 -> 丢弃 (草稿是噪声容忍的)
    - 最后过 patch_validator，丢违规项
    """
    ops: List[Operation] = []
    for d in draft_ops:
        if not isinstance(d, dict):
            continue
        op_val = d.get("op")
        try:
            kind = OperationKind(op_val)
        except (ValueError, KeyError):
            continue
        resolved = _resolve_op_refs(d, registry)
        try:
            ops.append(Operation(op=kind, **resolved))
        except (ValidationError, TypeError):
            continue
    patch = StatePatch(operations=ops)
    check = validate_patch(state, patch)
    if check.valid:
        return patch
    # 丢违规 op
    bad = {v.op_index for v in check.violations}
    kept = [op for i, op in enumerate(ops) if i not in bad]
    return StatePatch(operations=kept)


_OP_NAME_FIELDS = ("target_id", "source_id", "actor_id")


def _resolve_op_refs(d: Dict, registry: EntityRegistry) -> Dict:
    """把 op dict 里的 name 引用解析成 id。"""
    out: Dict = {}
    for k, v in d.items():
        if k == "op":
            continue
        if k in _OP_NAME_FIELDS and isinstance(v, str):
            resolved = registry.resolve_name(v)
            out[k] = resolved or v  # 解析失败保留原值 (后续校验会丢)
        elif k == "item_id" and isinstance(v, str):
            resolved = registry.resolve_name(v)
            out[k] = resolved or v
        elif k == "location_id" and isinstance(v, str):
            resolved = registry.resolve_name(v)
            out[k] = resolved or v
        else:
            out[k] = v
    # belief 字段如果是字符串，转成枚举
    if "belief" in out and isinstance(out["belief"], str):
        try:
            out["belief"] = Belief(out["belief"])
        except ValueError:
            out.pop("belief", None)
    return out


# ---------------------------------------------------------------------------
# SceneCompiler: 单场景 -> SceneExtraction -> 增量更新 registry
# ---------------------------------------------------------------------------


@dataclass
class SceneCompileResult:
    scene_id: str
    extraction: SceneExtraction
    applied_patch: StatePatch = field(default_factory=StatePatch)
    new_character_ids: List[str] = field(default_factory=list)
    new_item_ids: List[str] = field(default_factory=list)
    new_location_ids: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class SceneCompiler:
    """把一个 SceneExtraction 编译进 registry + state。

    单场景编译 (plan 第十二步 A)。
    """

    def compile(
        self,
        extraction: SceneExtraction,
        registry: EntityRegistry,
        state: WorldState,
    ) -> SceneCompileResult:
        result = SceneCompileResult(scene_id=extraction.scene_id, extraction=extraction)

        # 1. 实体注册 + 同步进 state
        for ent in extraction.entities:
            cid = self._register_entity(ent, registry, state, result)

        # 2. 关系累积
        accumulate_relations(registry, extraction.relations)
        self._sync_relations(registry, state)

        # 3. 世界规则
        for wr in extraction.world_rules:
            self._add_world_rule(wr, state)

        # 4. 事件 -> patch (草稿转严格)
        all_draft: List[Dict] = []
        for ev in sorted(extraction.events, key=lambda e: e.order):
            all_draft.extend(ev.patch_operations)
        patch = draft_ops_to_patch(all_draft, registry, state)
        result.applied_patch = patch
        return result

    # ---- 内部 ----

    def _register_entity(
        self, ent: RawEntity, registry: EntityRegistry,
        state: WorldState, result: SceneCompileResult,
    ) -> str:
        et = (ent.entity_type or "character").lower()
        if et == "character":
            cid = registry.resolve_or_register_character(ent)
            if cid not in state.characters:
                state.characters[cid] = registry.characters[cid]
                result.new_character_ids.append(cid)
            return cid
        if et == "item":
            iid = registry.resolve_or_register_item(ent)
            if iid not in state.items:
                state.items[iid] = registry.items[iid]
                result.new_item_ids.append(iid)
            return iid
        # location
        lid = registry.resolve_or_register_location(ent)
        if lid not in state.locations:
            state.locations[lid] = registry.locations[lid]
            result.new_location_ids.append(lid)
        return lid

    def _sync_relations(self, registry: EntityRegistry, state: WorldState) -> None:
        rels = getattr(registry, "_relations", [])
        state.relations = list(rels)

    def _add_world_rule(self, raw: RawWorldRule, state: WorldState) -> None:
        # 去重: 同 category+statement 不重复加
        for wr in state.world_rules:
            if wr.category == raw.category and wr.statement == raw.statement:
                return
        rid = f"rule_{len(state.world_rules) + 1:03d}"
        state.world_rules.append(WorldRule(
            rule_id=rid, category=raw.category, statement=raw.statement,
        ))


# ---------------------------------------------------------------------------
# ChapterCompiler: 单章节 (多场景) 编译
# ---------------------------------------------------------------------------


@dataclass
class ChapterCompileResult:
    chapter_index: int
    scene_results: List[SceneCompileResult] = field(default_factory=list)
    combined_patch: StatePatch = field(default_factory=StatePatch)
    extraction_count: int = 0
    warnings: List[str] = field(default_factory=list)


class ChapterCompiler:
    """单章节编译 (plan 第十二步 B)：多场景 + 跨场景实体消歧。

    extractor 可注入 (便于 mock 测试)。
    """

    def __init__(self, extractor=None, scene_compiler: Optional[SceneCompiler] = None):
        self._extractor = extractor
        self._scene_compiler = scene_compiler or SceneCompiler()

    def _get_extractor(self):
        if self._extractor is None:
            from .extractors import EntityExtractor
            self._extractor = EntityExtractor()
        return self._extractor

    def compile(
        self,
        chapter: Chapter,
        registry: EntityRegistry,
        state: WorldState,
        *,
        max_scenes: Optional[int] = None,
    ) -> ChapterCompileResult:
        result = ChapterCompileResult(chapter_index=chapter.index)
        scenes = split_scenes(chapter)
        if max_scenes:
            scenes = scenes[:max_scenes]
        extractor = self._get_extractor()
        known = registry.known_entities()

        combined_ops: List[Operation] = []
        for sc in scenes:
            extraction = extractor.extract(
                sc.text, scene_id=sc.scene_id,
                known_entities=known,
                chapter_hint=chapter.heading,
            )
            if extraction is None:
                result.warnings.append(
                    f"{sc.scene_id}: 抽取失败 ({getattr(extractor,'last_error','')})")
                continue
            result.extraction_count += 1
            sr = self._scene_compiler.compile(extraction, registry, state)
            result.scene_results.append(sr)
            combined_ops.extend(sr.applied_patch.operations)
            # 抽完一个场景，刷新已知实体表供下一场景消歧
            known = registry.known_entities()

        result.combined_patch = StatePatch(
            operations=combined_ops,
            notes=f"chapter {chapter.index}: {len(result.scene_results)} scenes",
        )
        return result


def split_scenes(chapter: Chapter):
    """延迟导入避免循环 (text_loader 的 split_scenes)。"""
    from .text_loader import split_scenes as _split
    return _split(chapter)


# ---------------------------------------------------------------------------
# PackageBuilder: 累积结果 -> WorldPackage (JSON + WorldState)
# ---------------------------------------------------------------------------


@dataclass
class WorldPackage:
    """编译产物。既可序列化成 JSON，也能直接拿到 WorldState。"""

    package_id: str
    novel: str
    source_chapters: List[int]
    snapshot: WorldState
    manifest: Dict

    def to_json(self) -> str:
        return json.dumps({
            "package_id": self.package_id,
            "novel": self.novel,
            "source_chapters": self.source_chapters,
            "manifest": self.manifest,
            "snapshot": self.snapshot.dict(),
        }, ensure_ascii=False, indent=2)

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())


class PackageBuilder:
    """把 registry + state 封装成 WorldPackage。"""

    def build(
        self,
        package_id: str,
        novel: str,
        source_chapters: List[int],
        state: WorldState,
        registry: EntityRegistry,
    ) -> WorldPackage:
        manifest = {
            "character_count": len(state.characters),
            "item_count": len(state.items),
            "location_count": len(state.locations),
            "relation_count": len(state.relations),
            "world_rule_count": len(state.world_rules),
            "characters": [
                {"id": c.character_id, "name": c.display_name, "aliases": c.aliases}
                for c in state.characters.values()
            ],
            "alias_index_size": len(registry.alias_index),
        }
        return WorldPackage(
            package_id=package_id,
            novel=novel,
            source_chapters=source_chapters,
            snapshot=state,
            manifest=manifest,
        )
