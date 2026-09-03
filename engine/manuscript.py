"""动态小说稿件领域模型。

稿件层只保存已提交事件的可读投影和可审计的结构化事实声明。它不修改
``WorldState``，也不把自然语言当作新的权威事实来源。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, root_validator, validator

from world_schema import DialogueLine, OperationKind


class StrictManuscriptModel(BaseModel):
    class Config:
        extra = "forbid"
        anystr_strip_whitespace = True


class ManuscriptSource(str, Enum):
    deterministic = "deterministic"
    narrative_output = "narrative_output"
    llm = "llm"
    legacy = "legacy"


class ManuscriptGenerationStatus(str, Enum):
    pending = "pending"
    ready = "ready"
    failed = "failed"


class FactClaimKind(str, Enum):
    flag_equals = "flag_equals"
    attr_equals = "attr_equals"
    value_delta = "value_delta"
    character_at = "character_at"
    character_alive = "character_alive"
    item_owner = "item_owner"
    item_destroyed = "item_destroyed"
    relation_delta = "relation_delta"
    belief_equals = "belief_equals"
    identity_tags = "identity_tags"
    plot_stage = "plot_stage"
    fact_exists = "fact_exists"
    operation = "operation"


class FactClaim(StrictManuscriptModel):
    claim_id: str
    kind: FactClaimKind
    source_event_ids: List[str] = Field(default_factory=list)
    subject_id: Optional[str] = None
    object_id: Optional[str] = None
    path: str = ""
    value: Any = None
    delta: Optional[float] = None
    dimension: Optional[str] = None
    operation_kind: Optional[OperationKind] = None
    statement: str = ""

    @validator("claim_id")
    def _claim_id_not_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("claim_id cannot be blank")
        return value

    @validator("source_event_ids")
    def _claim_sources_are_unique(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError("fact claim requires at least one source event")
        if len(value) != len(set(value)):
            raise ValueError("fact claim source_event_ids must be unique")
        return value


class ManuscriptPassage(StrictManuscriptModel):
    """稳定 passage 身份及当前正文投影。

    存储层可以先创建 ``pending`` 记录，完成后再填充正文和修订字段。writer
    产出的 passage 始终是 ``ready``。
    """

    passage_id: str
    manuscript_id: str = ""
    session_id: str = ""
    chapter_number: Optional[int] = None
    entry_id: str = ""
    entry_revision: int = 0
    manuscript_sequence: int = 0
    title: str = ""
    paragraphs: List[str] = Field(default_factory=list)
    text: str = ""
    source_event_ids: List[str] = Field(default_factory=list)
    source_fingerprint: str = ""
    from_world_version: int = 0
    to_world_version: int = 0
    referenced_entity_ids: List[str] = Field(default_factory=list)
    dialogues: List[DialogueLine] = Field(default_factory=list)
    system_hints: List[str] = Field(default_factory=list)
    viewpoint: str = "third_person"
    fact_claims: List[FactClaim] = Field(default_factory=list)
    continuity_summary: str = ""
    writer_version: str = ""
    input_hash: str = ""
    generation_kind: ManuscriptSource = ManuscriptSource.deterministic
    generation_status: ManuscriptGenerationStatus = ManuscriptGenerationStatus.ready
    current_revision: int = 0
    last_error: str = ""
    created_at: str = ""
    updated_at: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @validator("passage_id")
    def _passage_id_not_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("passage_id cannot be blank")
        return value

    @validator("source_event_ids")
    def _passage_sources_are_unique(cls, value: List[str]) -> List[str]:
        if len(value) != len(set(value)):
            raise ValueError("passage source_event_ids must be unique")
        return value

    @validator("referenced_entity_ids")
    def _passage_entities_are_unique(cls, value: List[str]) -> List[str]:
        return list(dict.fromkeys(value))

    @root_validator
    def _normalize_content_and_status(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        paragraphs = [str(item).strip() for item in values.get("paragraphs") or [] if str(item).strip()]
        text = str(values.get("text") or "").strip()
        if not paragraphs and text:
            paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
        if paragraphs and not text:
            text = "\n\n".join(paragraphs)
        values["paragraphs"] = paragraphs
        values["text"] = text

        status = values.get("generation_status")
        if status in {
            ManuscriptGenerationStatus.ready,
            ManuscriptGenerationStatus.ready.value,
        }:
            if not values.get("source_event_ids"):
                raise ValueError("ready passage requires source_event_ids")
            if not text:
                raise ValueError("ready passage requires text")
        if values.get("to_world_version", 0) < values.get("from_world_version", 0):
            raise ValueError("passage world version range is reversed")
        return values


class ManuscriptRevision(StrictManuscriptModel):
    """一次不可变的 writer 输出，可包含一个或多个 passage。"""

    revision_id: str
    manuscript_id: str
    timeline_id: str
    revision_number: int = Field(ge=1)
    parent_revision_id: Optional[str] = None
    source: ManuscriptSource = ManuscriptSource.deterministic
    passages: List[ManuscriptPassage] = Field(default_factory=list)
    source_event_ids: List[str] = Field(default_factory=list)
    writer_version: str = ""
    input_hash: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @root_validator
    def _sources_match_passages(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        flattened = [
            event_id
            for passage in values.get("passages") or []
            for event_id in passage.source_event_ids
        ]
        declared = values.get("source_event_ids") or []
        if not declared:
            values["source_event_ids"] = flattened
        elif declared != flattened:
            raise ValueError("revision source_event_ids must match passage order")
        if len(flattened) != len(set(flattened)):
            raise ValueError("one source event cannot appear in multiple passages")
        return values

    @property
    def full_text(self) -> str:
        return "\n\n".join(passage.text for passage in self.passages)


class WorldlineManuscript(StrictManuscriptModel):
    manuscript_id: str
    timeline_id: str = ""
    campaign_id: str = ""
    root_session_id: str = ""
    title: str = ""
    status: str = "active"
    current_revision: int = 0
    total_passages: int = 0
    revisions: List[ManuscriptRevision] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @root_validator
    def _revision_history_is_ordered(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        manuscript_id = values.get("manuscript_id")
        timeline_id = values.get("timeline_id")
        revisions = values.get("revisions") or []
        numbers = [revision.revision_number for revision in revisions]
        if numbers and numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("manuscript revisions must be consecutively numbered")
        for revision in revisions:
            if revision.manuscript_id != manuscript_id:
                raise ValueError("revision manuscript_id does not match manuscript")
            if timeline_id and revision.timeline_id != timeline_id:
                raise ValueError("revision timeline_id does not match manuscript")
        if revisions:
            values["current_revision"] = revisions[-1].revision_number
        return values

    @property
    def latest_revision(self) -> Optional[ManuscriptRevision]:
        return self.revisions[-1] if self.revisions else None

    def with_revision(self, revision: ManuscriptRevision) -> "WorldlineManuscript":
        return self.copy(update={"revisions": [*self.revisions, revision]}, deep=True)
