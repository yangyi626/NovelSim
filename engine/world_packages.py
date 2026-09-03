"""可编辑 WorldPackage 的本地仓库与完整性校验。

创作者后台编辑的是“世界模板”，不是正在运行的玩家存档。内置包只读，
创作者可克隆为 JSON 版本后修改；新开局再从选定模板复制一份干净快照。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from world_schema import ActionType, OperationKind, WorldState


PACKAGE_FORMAT = "ai-transmigration-world-package"
PACKAGE_FORMAT_VERSION = 1
PACKAGE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
REVIEW_STATUSES = {
    "draft",
    "pending_review",
    "approved",
    "published",
    "rejected",
}
REVIEW_TRANSITIONS = {
    "draft": {"pending_review"},
    "pending_review": {"draft", "approved", "rejected"},
    "approved": {"draft", "published"},
    "published": {"draft"},
    "rejected": {"draft", "pending_review"},
}
GOAL_STATUSES = {
    "active",
    "dormant",
    "achieved",
    "abandoned",
    "superseded",
    "expired",
}
GOAL_SCOPES = {"chapter", "arc", "timeline", "world", "book"}
RESERVED_PROGRESSION_FLAG_PREFIXES = (
    "settlement.",
    "reward.",
    "unlock.",
    "progression.",
    "campaign.",
    "lineage.",
    "inheritance.",
)


class WorldPackageError(RuntimeError):
    """世界包仓库操作失败。"""


class WorldPackageNotFound(WorldPackageError):
    """世界包不存在。"""


class WorldPackageConflict(WorldPackageError):
    """世界包修订版本冲突。"""


class WorldPackageValidationError(WorldPackageError):
    """世界包内容不满足运行时约束。"""

    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__("；".join(errors))


@dataclass(frozen=True)
class WorldPackageRecord:
    package_id: str
    novel: str
    scenario: str
    anchor: str
    default_actor_id: str
    source_chapters: List[Any]
    snapshot: WorldState
    manifest: Dict[str, Any]
    revision: int
    source: str
    created_at: str
    updated_at: str
    review_status: str = "draft"
    review_note: str = ""
    reviewed_at: str = ""
    published_at: str = ""

    @property
    def editable(self) -> bool:
        return self.source == "custom"

    def world_meta(self) -> Dict[str, Any]:
        return {
            "package_id": self.package_id,
            "novel": self.novel,
            "scenario": self.scenario,
            "anchor": self.anchor,
            "source_chapters": list(self.source_chapters),
        }

    def summary(self) -> Dict[str, Any]:
        return {
            "package_id": self.package_id,
            "novel": self.novel,
            "scenario": self.scenario,
            "anchor": self.anchor,
            "default_actor_id": self.default_actor_id,
            "source_chapters": list(self.source_chapters),
            "manifest": dict(self.manifest),
            "revision": self.revision,
            "source": self.source,
            "editable": self.editable,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "review_status": self.review_status,
            "review_note": self.review_note,
            "reviewed_at": self.reviewed_at,
            "published_at": self.published_at,
        }

    def payload(self) -> Dict[str, Any]:
        return {
            "format": PACKAGE_FORMAT,
            "format_version": PACKAGE_FORMAT_VERSION,
            **self.summary(),
            "snapshot": self.snapshot.dict(),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _manifest(state: WorldState) -> Dict[str, Any]:
    return {
        "character_count": len(state.characters),
        "item_count": len(state.items),
        "location_count": len(state.locations),
        "relation_count": len(state.relations),
        "world_rule_count": len(state.world_rules),
        "plot_arc_count": len(state.plot),
    }


def _check_id_map(
    errors: List[str],
    label: str,
    values: Dict[str, Any],
    id_field: str,
) -> None:
    for key, value in values.items():
        if getattr(value, id_field) != key:
            errors.append(
                f"{label}键 {key} 与 {id_field}={getattr(value, id_field)} 不一致"
            )


def validate_world_package_payload(
    payload: Dict[str, Any],
    *,
    expected_package_id: Optional[str] = None,
) -> WorldPackageRecord:
    """把后台草稿升级成严格 WorldPackage，并检查跨实体引用。"""

    errors: List[str] = []
    if not isinstance(payload, dict):
        raise WorldPackageValidationError(["世界包必须是 JSON 对象"])

    package_id = str(payload.get("package_id") or "").strip()
    if expected_package_id and package_id != expected_package_id:
        errors.append("请求路径中的世界包 ID 与内容不一致")
    if not PACKAGE_ID_PATTERN.fullmatch(package_id):
        errors.append(
            "世界包 ID 需为 3–64 位小写字母、数字、下划线或连字符，且以字母开头"
        )

    novel = str(payload.get("novel") or "").strip()
    scenario = str(payload.get("scenario") or "").strip()
    anchor = str(payload.get("anchor") or "").strip()
    default_actor_id = str(payload.get("default_actor_id") or "").strip()
    source_chapters = payload.get("source_chapters", [])
    if not novel:
        errors.append("小说名不能为空")
    if not scenario:
        errors.append("场景名不能为空")
    if not anchor:
        errors.append("介入锚点不能为空")
    if not isinstance(source_chapters, list):
        errors.append("source_chapters 必须是数组")
        source_chapters = []

    try:
        state = WorldState.parse_obj(payload.get("snapshot"))
    except Exception as exc:
        raise WorldPackageValidationError(
            errors + [f"世界状态 Schema 校验失败: {exc}"]
        ) from exc

    if state.version != 0:
        errors.append("世界模板版本必须为 0，运行后版本属于玩家存档")
    if default_actor_id not in state.characters:
        errors.append(f"默认玩家角色不存在: {default_actor_id}")
    if (
        state.current_scene_id
        and state.current_scene_id not in state.locations
    ):
        errors.append(f"当前场景不存在: {state.current_scene_id}")

    _check_id_map(errors, "角色", state.characters, "character_id")
    _check_id_map(errors, "物品", state.items, "item_id")
    _check_id_map(errors, "地点", state.locations, "location_id")
    _check_id_map(errors, "剧情线", state.plot, "arc_id")

    for character in state.characters.values():
        if (
            character.location_id
            and character.location_id not in state.locations
        ):
            errors.append(
                f"角色 {character.character_id} 引用了未知地点 "
                f"{character.location_id}"
            )
        for item_id in character.inventory:
            if item_id not in state.items:
                errors.append(
                    f"角色 {character.character_id} 持有未知物品 {item_id}"
                )

    for location in state.locations.values():
        if location.parent_id == location.location_id:
            errors.append(f"地点 {location.location_id} 不能以自身为父地点")
        elif (
            location.parent_id
            and location.parent_id not in state.locations
        ):
            errors.append(
                f"地点 {location.location_id} 引用了未知父地点 "
                f"{location.parent_id}"
            )
        visited = {location.location_id}
        parent_id = location.parent_id
        while parent_id and parent_id in state.locations:
            if parent_id in visited:
                errors.append(f"地点层级存在循环: {location.location_id}")
                break
            visited.add(parent_id)
            parent_id = state.locations[parent_id].parent_id

    for item in state.items.values():
        if item.owner_id and item.owner_id not in state.characters:
            errors.append(f"物品 {item.item_id} 引用了未知持有者 {item.owner_id}")
        if item.location_id and item.location_id not in state.locations:
            errors.append(f"物品 {item.item_id} 引用了未知地点 {item.location_id}")
        if item.owner_id and item.location_id:
            errors.append(
                f"物品 {item.item_id} 不能同时属于角色和放置在地点"
            )
        if item.quantity < 1:
            errors.append(f"物品 {item.item_id} 数量必须大于 0")

    relation_keys = set()
    for relation in state.relations:
        key = (relation.source_id, relation.target_id)
        if key in relation_keys:
            errors.append(f"存在重复关系: {key[0]} -> {key[1]}")
        relation_keys.add(key)
        if relation.source_id not in state.characters:
            errors.append(f"关系起点角色不存在: {relation.source_id}")
        if relation.target_id not in state.characters:
            errors.append(f"关系终点角色不存在: {relation.target_id}")

    for character_id, beliefs in state.beliefs.items():
        if character_id not in state.characters:
            errors.append(f"认知记录引用未知角色: {character_id}")
        fact_ids = [belief.fact_id.strip() for belief in beliefs]
        if any(not fact_id for fact_id in fact_ids):
            errors.append(f"角色 {character_id} 的事实 ID 不能为空")
        duplicates = {
            fact_id
            for fact_id in fact_ids
            if fact_id and fact_ids.count(fact_id) > 1
        }
        for fact_id in sorted(duplicates):
            errors.append(
                f"角色 {character_id} 对事实 {fact_id} 存在重复认知"
            )
        for belief in beliefs:
            if belief.source_type not in {
                "unknown",
                "observation",
                "hearsay",
                "inference",
                "secret",
            }:
                errors.append(
                    f"角色 {character_id} 对事实 {belief.fact_id} "
                    f"使用了未知来源类型 {belief.source_type}"
                )
            keywords = [keyword.strip() for keyword in belief.keywords]
            if len(keywords) != len(set(keywords)):
                errors.append(
                    f"角色 {character_id} 对事实 {belief.fact_id} "
                    "含重复关键词"
                )
    for character_id, psyche in state.character_psyches.items():
        if character_id not in state.characters:
            errors.append(f"角色心理引用未知角色: {character_id}")
        if psyche.character_id != character_id:
            errors.append(f"角色心理键与 character_id 不一致: {character_id}")
        goal_ids = [goal.goal_id for goal in psyche.goals]
        if len(goal_ids) != len(set(goal_ids)):
            errors.append(f"角色 {character_id} 的目标 ID 不能重复")
        for goal in psyche.goals:
            if goal.status not in GOAL_STATUSES:
                errors.append(
                    f"角色 {character_id} 的目标 {goal.goal_id} "
                    f"状态无效: {goal.status}"
                )
            if goal.scope not in GOAL_SCOPES:
                errors.append(
                    f"角色 {character_id} 的目标 {goal.goal_id} "
                    f"作用域无效: {goal.scope}"
                )
            for target_id in goal.target_ids:
                if target_id not in state.characters:
                    errors.append(
                        f"角色 {character_id} 的目标 {goal.goal_id} "
                        f"引用未知角色 {target_id}"
                    )
        plan_ids = [plan.plan_id for plan in psyche.plans]
        if len(plan_ids) != len(set(plan_ids)):
            errors.append(f"角色 {character_id} 的计划 ID 不能重复")
        for plan in psyche.plans:
            if plan.goal_id not in goal_ids:
                errors.append(
                    f"角色 {character_id} 的计划 {plan.plan_id} "
                    f"引用未知目标 {plan.goal_id}"
                )
            if plan.current_step < 0 or plan.current_step > len(plan.steps):
                errors.append(
                    f"角色 {character_id} 的计划 {plan.plan_id} "
                    "当前步骤超出范围"
                )
            if plan.status not in {
                "active", "paused", "completed", "abandoned"
            }:
                errors.append(
                    f"角色 {character_id} 的计划 {plan.plan_id} 状态无效"
                )

    rule_ids = [rule.rule_id for rule in state.world_rules]
    if len(rule_ids) != len(set(rule_ids)):
        errors.append("世界规则 ID 不能重复")

    _check_id_map(
        errors,
        "世界概念",
        state.world_concepts,
        "concept_id",
    )
    concept_ids = set(state.world_concepts)
    for concept in state.world_concepts.values():
        for pattern in concept.mention_patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                errors.append(
                    f"世界概念 {concept.concept_id} 的 mention_pattern "
                    f"无效: {exc}"
                )
    constraint_ids = [
        constraint.constraint_id
        for constraint in state.world_constraints
    ]
    if len(constraint_ids) != len(set(constraint_ids)):
        errors.append("世界约束 ID 不能重复")
    for constraint in state.world_constraints:
        allowed = set(constraint.allowed_concept_ids)
        forbidden = set(constraint.forbidden_concept_ids)
        for concept_id in sorted((allowed | forbidden) - concept_ids):
            errors.append(
                f"世界约束 {constraint.constraint_id} 引用未知概念 "
                f"{concept_id}"
            )
        for concept_id in sorted(allowed & forbidden):
            errors.append(
                f"世界约束 {constraint.constraint_id} 同时允许和禁止 "
                f"{concept_id}"
            )

    for character_id, capabilities in state.character_capabilities.items():
        if character_id not in state.characters:
            errors.append(f"能力记录引用未知角色: {character_id}")
        capability_ids = [
            capability.capability_id for capability in capabilities
        ]
        if len(capability_ids) != len(set(capability_ids)):
            errors.append(f"角色 {character_id} 的能力 ID 不能重复")

    known_entities = (
        set(state.characters)
        | set(state.items)
        | set(state.locations)
    )
    affordance_ids = []
    valid_action_types = {action.value for action in ActionType}
    for entity_id, affordances in state.entity_affordances.items():
        if entity_id not in known_entities:
            errors.append(f"Affordance 引用未知实体: {entity_id}")
        for affordance in affordances:
            affordance_ids.append(affordance.affordance_id)
            if affordance.entity_id != entity_id:
                errors.append(
                    f"Affordance 键与 entity_id 不一致: "
                    f"{affordance.affordance_id}"
                )
            if affordance.action_type not in valid_action_types:
                errors.append(
                    f"Affordance {affordance.affordance_id} 使用未知 Action "
                    f"{affordance.action_type}"
                )
            if (
                affordance.concept_id
                and affordance.concept_id not in concept_ids
            ):
                errors.append(
                    f"Affordance {affordance.affordance_id} 引用未知概念 "
                    f"{affordance.concept_id}"
                )
    if len(affordance_ids) != len(set(affordance_ids)):
        errors.append("Affordance ID 不能重复")

    valid_operation_kinds = {kind.value for kind in OperationKind}
    for action_type, policy in state.action_policies.items():
        if action_type != policy.action_type:
            errors.append(f"ActionPolicy 键与 action_type 不一致: {action_type}")
        if action_type not in valid_action_types:
            errors.append(f"ActionPolicy 使用未知 Action: {action_type}")
        unknown_operations = (
            set(policy.allowed_patch_operations) - valid_operation_kinds
        )
        for operation in sorted(unknown_operations):
            errors.append(
                f"ActionPolicy {action_type} 授权未知 Patch 操作 {operation}"
            )
        if len(policy.required_parameters) != len(
            set(policy.required_parameters)
        ):
            errors.append(f"ActionPolicy {action_type} 必填参数不能重复")

    ability_specs = state.flags.get("runtime.ability_specs", {})
    if isinstance(ability_specs, dict):
        for ability_id, spec in ability_specs.items():
            if not isinstance(spec, dict):
                continue
            completion_flag = str(spec.get("completion_flag") or "").lower()
            if any(
                completion_flag.startswith(prefix)
                for prefix in RESERVED_PROGRESSION_FLAG_PREFIXES
            ):
                errors.append(
                    f"Ability {ability_id} 不得写入系统保留字段 {completion_flag}"
                )
    dialogue_effects = state.flags.get("runtime.dialogue_effects", [])
    if isinstance(dialogue_effects, list):
        for index, effect in enumerate(dialogue_effects):
            if not isinstance(effect, dict):
                continue
            completion_flag = str(effect.get("completion_flag") or "").lower()
            if any(
                completion_flag.startswith(prefix)
                for prefix in RESERVED_PROGRESSION_FLAG_PREFIXES
            ):
                errors.append(
                    f"DialogueEffect {index} 不得写入系统保留字段 {completion_flag}"
                )

    try:
        revision = max(1, int(payload.get("revision") or 1))
    except (TypeError, ValueError):
        revision = 1
        errors.append("revision 必须是正整数")

    source = str(payload.get("source") or "custom")
    default_review_status = (
        "published" if source == "builtin" else "draft"
    )
    review_status = str(
        payload.get("review_status") or default_review_status
    )
    if review_status not in REVIEW_STATUSES:
        errors.append(f"未知审核状态: {review_status}")

    if errors:
        raise WorldPackageValidationError(errors)

    now = _now()
    provided_manifest = payload.get("manifest")
    manifest = (
        dict(provided_manifest)
        if isinstance(provided_manifest, dict)
        else {}
    )
    # 数量由严格解析后的状态重新计算；编译器和质量门禁元数据保留。
    manifest.update(_manifest(state))
    return WorldPackageRecord(
        package_id=package_id,
        novel=novel,
        scenario=scenario,
        anchor=anchor,
        default_actor_id=default_actor_id,
        source_chapters=list(source_chapters),
        snapshot=state,
        manifest=manifest,
        revision=revision,
        source=source,
        created_at=str(payload.get("created_at") or now),
        updated_at=str(payload.get("updated_at") or now),
        review_status=review_status,
        review_note=str(payload.get("review_note") or ""),
        reviewed_at=str(payload.get("reviewed_at") or ""),
        published_at=str(payload.get("published_at") or ""),
    )


class WorldPackageStore:
    """内置只读包 + worlds/*.json 可编辑包的统一仓库。"""

    def __init__(
        self,
        directory: Path,
        *,
        builtins: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.history_directory = self.directory / ".history"
        self.history_directory.mkdir(parents=True, exist_ok=True)
        self._builtins: Dict[str, WorldPackageRecord] = {}
        for package_id, payload in (builtins or {}).items():
            record = validate_world_package_payload(
                {**payload, "package_id": package_id, "source": "builtin"},
                expected_package_id=package_id,
            )
            self._builtins[package_id] = WorldPackageRecord(
                **{**record.__dict__, "source": "builtin"}
            )

    def _path(self, package_id: str) -> Path:
        if not PACKAGE_ID_PATTERN.fullmatch(package_id):
            raise WorldPackageValidationError(["世界包 ID 格式无效"])
        path = (self.directory / f"{package_id}.json").resolve()
        if path.parent != self.directory:
            raise WorldPackageValidationError(["世界包路径越界"])
        return path

    def _history_path(
        self,
        package_id: str,
        revision: int,
    ) -> Path:
        self._path(package_id)
        if revision < 1:
            raise WorldPackageValidationError(["修订号必须大于 0"])
        return self.history_directory / (
            f"{package_id}.r{revision}.json"
        )

    def _load_path(self, path: Path) -> WorldPackageRecord:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("format") != PACKAGE_FORMAT:
                payload = self._upgrade_compiler_package(payload)
            record = validate_world_package_payload(payload)
        except WorldPackageValidationError:
            raise
        except Exception as exc:
            raise WorldPackageError(f"读取世界包失败 {path.name}: {exc}") from exc
        return WorldPackageRecord(
            **{**record.__dict__, "source": "custom"}
        )

    @staticmethod
    def _upgrade_compiler_package(payload: Dict[str, Any]) -> Dict[str, Any]:
        """兼容 compiler.PackageBuilder 直接输出的旧格式。"""

        if not isinstance(payload, dict) or "snapshot" not in payload:
            return payload
        snapshot = payload.get("snapshot")
        characters = snapshot.get("characters", {}) if isinstance(snapshot, dict) else {}
        psyches = (
            snapshot.get("character_psyches", {})
            if isinstance(snapshot, dict)
            else {}
        )
        default_actor_id = ""
        for character_id, psyche in psyches.items():
            if isinstance(psyche, dict) and psyche.get("is_player"):
                default_actor_id = character_id
                break
        if not default_actor_id and characters:
            default_actor_id = next(iter(characters))

        manifest = payload.get("manifest")
        manifest = manifest if isinstance(manifest, dict) else {}
        novel = str(payload.get("novel") or "未命名小说")
        return {
            **payload,
            "format": PACKAGE_FORMAT,
            "format_version": PACKAGE_FORMAT_VERSION,
            "scenario": str(manifest.get("scenario") or novel),
            "anchor": str(manifest.get("anchor") or "待设置介入锚点"),
            "default_actor_id": default_actor_id,
            "revision": 1,
            "source": "custom",
        }

    def list_packages(self) -> List[WorldPackageRecord]:
        records = list(self._builtins.values())
        for path in sorted(self.directory.glob("*.json")):
            try:
                record = self._load_path(path)
            except WorldPackageError:
                continue
            if record.package_id not in self._builtins:
                records.append(record)
        return sorted(
            records,
            key=lambda item: (item.source != "builtin", item.updated_at),
            reverse=True,
        )

    def get(self, package_id: str) -> WorldPackageRecord:
        if package_id in self._builtins:
            return self._builtins[package_id]
        path = self._path(package_id)
        if not path.exists():
            raise WorldPackageNotFound(f"世界包不存在: {package_id}")
        return self._load_path(path)

    def exists(self, package_id: str) -> bool:
        try:
            self.get(package_id)
            return True
        except (WorldPackageNotFound, WorldPackageValidationError):
            return False

    def validate(self, payload: Dict[str, Any]) -> WorldPackageRecord:
        return validate_world_package_payload(payload)

    def clone(self, source_package_id: str) -> WorldPackageRecord:
        source = self.get(source_package_id)
        suffix = 2
        while self.exists(f"{source_package_id}_v{suffix}"):
            suffix += 1
        package_id = f"{source_package_id}_v{suffix}"
        payload = source.payload()
        payload.update(
            {
                "package_id": package_id,
                "revision": 1,
                "source": "custom",
                "created_at": _now(),
                "updated_at": _now(),
            }
        )
        return self.save(package_id, payload, create=True)

    def save(
        self,
        package_id: str,
        payload: Dict[str, Any],
        *,
        expected_revision: Optional[int] = None,
        create: bool = False,
    ) -> WorldPackageRecord:
        if package_id in self._builtins:
            raise WorldPackageError("内置世界包只读，请先另存为新版本")
        path = self._path(package_id)
        if create and path.exists():
            raise WorldPackageConflict(f"世界包已存在: {package_id}")

        previous = self._load_path(path) if path.exists() else None
        if expected_revision is not None:
            actual = previous.revision if previous else 0
            if actual != expected_revision:
                raise WorldPackageConflict(
                    f"世界包版本冲突: expected {expected_revision}, got {actual}"
                )

        record = validate_world_package_payload(
            {**payload, "package_id": package_id},
            expected_package_id=package_id,
        )
        now = _now()
        revision = (previous.revision + 1) if previous else 1
        saved = WorldPackageRecord(
            **{
                **record.__dict__,
                "revision": revision,
                "source": "custom",
                "created_at": previous.created_at if previous else now,
                "updated_at": now,
                "review_status": "draft",
                "review_note": "",
                "reviewed_at": "",
                "published_at": "",
            }
        )
        self._persist_record(path, saved)
        return saved

    def list_revisions(self, package_id: str) -> List[Dict[str, Any]]:
        current = self.get(package_id)
        if not current.editable:
            return [
                {
                    "revision": current.revision,
                    "review_status": current.review_status,
                    "updated_at": current.updated_at,
                    "review_note": current.review_note,
                }
            ]
        records: Dict[int, WorldPackageRecord] = {}
        for path in self.history_directory.glob(
            f"{package_id}.r*.json"
        ):
            try:
                record = self._load_path(path)
            except WorldPackageError:
                continue
            records[record.revision] = record
        records[current.revision] = current
        return [
            {
                "revision": record.revision,
                "review_status": record.review_status,
                "updated_at": record.updated_at,
                "review_note": record.review_note,
            }
            for record in sorted(
                records.values(),
                key=lambda item: item.revision,
                reverse=True,
            )
        ]

    def get_revision(
        self,
        package_id: str,
        revision: int,
    ) -> WorldPackageRecord:
        current = self.get(package_id)
        if current.revision == revision:
            return current
        path = self._history_path(package_id, revision)
        if not path.exists():
            raise WorldPackageNotFound(
                f"世界包 {package_id} 不存在修订 r{revision}"
            )
        return self._load_path(path)

    def diff_revisions(
        self,
        package_id: str,
        from_revision: int,
        to_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        before = self.get_revision(package_id, from_revision)
        after = (
            self.get(package_id)
            if to_revision is None
            else self.get_revision(package_id, to_revision)
        )
        changes: List[Dict[str, Any]] = []
        _diff_values(
            before.payload(),
            after.payload(),
            "",
            changes,
        )
        return {
            "package_id": package_id,
            "from_revision": before.revision,
            "to_revision": after.revision,
            "change_count": len(changes),
            "changes": changes[:500],
            "truncated": len(changes) > 500,
        }

    def transition_review(
        self,
        package_id: str,
        target_status: str,
        *,
        expected_revision: int,
        note: str = "",
    ) -> WorldPackageRecord:
        if target_status not in REVIEW_STATUSES:
            raise WorldPackageValidationError(
                [f"未知审核状态: {target_status}"]
            )
        current = self.get(package_id)
        if not current.editable:
            raise WorldPackageError("内置世界包不参与创作者审核流")
        if current.revision != expected_revision:
            raise WorldPackageConflict(
                "世界包版本冲突: "
                f"expected {expected_revision}, got {current.revision}"
            )
        allowed = REVIEW_TRANSITIONS.get(
            current.review_status,
            set(),
        )
        if target_status not in allowed:
            raise WorldPackageError(
                f"审核状态不能从 {current.review_status} "
                f"变更为 {target_status}"
            )
        now = _now()
        saved = WorldPackageRecord(
            **{
                **current.__dict__,
                "revision": current.revision + 1,
                "updated_at": now,
                "review_status": target_status,
                "review_note": note.strip(),
                "reviewed_at": (
                    now
                    if target_status in {"approved", "rejected"}
                    else current.reviewed_at
                ),
                "published_at": (
                    now
                    if target_status == "published"
                    else current.published_at
                ),
            }
        )
        self._persist_record(self._path(package_id), saved)
        return saved

    def _persist_record(
        self,
        path: Path,
        record: WorldPackageRecord,
    ) -> None:
        payload = json.dumps(
            record.payload(),
            ensure_ascii=False,
            indent=2,
        )
        temp_path = path.with_suffix(".json.tmp")
        history_path = self._history_path(
            record.package_id,
            record.revision,
        )
        history_temp = history_path.with_suffix(".json.tmp")
        try:
            temp_path.write_text(payload, encoding="utf-8")
            history_temp.write_text(payload, encoding="utf-8")
            temp_path.replace(path)
            history_temp.replace(history_path)
        except OSError as exc:
            raise WorldPackageError(f"保存世界包失败: {exc}") from exc


def _diff_values(
    before: Any,
    after: Any,
    path: str,
    changes: List[Dict[str, Any]],
) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            child_path = f"{path}.{key}" if path else key
            if key not in before:
                changes.append(
                    {
                        "path": child_path,
                        "type": "added",
                        "before": None,
                        "after": after[key],
                    }
                )
            elif key not in after:
                changes.append(
                    {
                        "path": child_path,
                        "type": "removed",
                        "before": before[key],
                        "after": None,
                    }
                )
            else:
                _diff_values(
                    before[key],
                    after[key],
                    child_path,
                    changes,
                )
        return
    if isinstance(before, list) and isinstance(after, list):
        if before != after:
            changes.append(
                {
                    "path": path,
                    "type": "changed",
                    "before": before,
                    "after": after,
                }
            )
        return
    if before != after:
        changes.append(
            {
                "path": path,
                "type": "changed",
                "before": before,
                "after": after,
            }
        )
