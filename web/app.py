"""AI 快穿系统 Web 后端 (FastAPI)。

提供世界线存档 JSON API + 托管前端构建产物 (web/static)：
    GET  /                -> 托管 static/index.html (生产)
    POST /api/start       -> 从指定世界包新建会话
    GET  /api/session     -> 恢复一个已有会话
    POST /api/turn        -> 跑一回合，返回 TurnResult 序列化
    GET  /api/state       -> 查询持久化世界状态
    GET  /api/events      -> 查询持久化事件日志
    GET  /api/joint-plans -> 查询联合计划、等待与纠错状态
    GET  /api/saves       -> 列出全部存档
    PATCH/DELETE /api/saves/{id} -> 改名/删除存档
    GET  /api/saves/{id}/export  -> 下载完整存档备份
    POST /api/saves/import       -> 导入完整存档备份
    GET/PUT /api/creator/packages -> 创作者世界包管理

会话状态与事件日志持久化到 SQLite 或 PostgreSQL；浏览器刷新或服务
重启后仍可通过 session_id 恢复。默认使用项目根目录 data/world.sqlite3；
设置 WORLD_DATABASE_URL 后切换为 PostgreSQL + pgvector。

安全边界：
- TurnPipeline 构造不触发 LLM key 检查 (懒加载)，import engine 安全
- .run() 包 try/except，捕获 RuntimeError (无 key) 和网络异常
  -> 返回 status="error" + 中文文案，绝不崩 worker
- use_npc_agents 默认 True (前端复选框默认勾选)
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, conint


StrictNonNegativeBudget = conint(strict=True, ge=0, le=1_000_000)
StrictPositiveBudget = conint(strict=True, ge=1, le=1_000_000)

from world_schema import (
    NarrativeOutput,
    Operation,
    OperationKind,
    StatePatch,
    WorldEvent,
    WorldState,
)
from engine import (
    CORE_TOOL_PERMISSIONS,
    DeterministicManuscriptWriter,
    LLMManuscriptWriter,
    ManuscriptWriter,
    JointPlan,
    JointPlanExecutor,
    ManuscriptGenerationStatus,
    ManuscriptRevisionConflict,
    ManuscriptWriterError,
    NarrativePlannerError,
    PlanRuntimeStatus,
    PersistenceError,
    RealLLMNarrativePlanner,
    ReflectionSemanticJudge,
    SessionNotFound,
    StateVersionUnavailable,
    TurnPipeline,
    VersionConflict,
    check_manuscript_revision,
    commit_event,
    migrate_world_facts,
    WorldPackageConflict,
    WorldPackageError,
    WorldPackageNotFound,
    WorldPackageStore,
    WorldPackageValidationError,
    create_core_tool_registry,
    create_plan_runtime,
    create_world_store,
    commit_dialogue_perceptions,
    cursor_after_world_version,
    filter_compatible_memories,
    project_presentation_commands,
    ready_ability_ids,
    narrative_output_to_revision,
    record_event_memory,
    reflect_character_memories,
    validate_joint_plan,
)
from compiler import (
    EXTRACTOR_PROMPT_VERSION,
    CompilationJobConflict,
    CompilationJobNotFound,
    CompilationJobStore,
)
from compiler.benchmark import build_job_report
from web.auth import (
    AuthConflict,
    AuthError,
    AuthenticationError,
    AuthStore,
    AuthUser,
    PermissionDenied,
    SYSTEM_ACTOR,
    require_permission,
)
from web.player_view import build_player_view
from engine.chapter_catalog import (
    ChapterCatalogError,
    ChapterCatalogStore,
    ChapterEntryNotFound,
    ChapterEntryNotPublished,
)

from examples.huarong_lane import build_snapshot, build_world_package
from engine.chapter_progression import (
    SessionLineage,
    TransitionRequest,
    UnlockGrant,
)
from examples.huarong_lane.canonical_case import build_canonical_start_state
from examples.huarong_lane.canonical_ch6_10 import (
    build_canonical_ch5_start_state,
)
from examples.huarong_lane.canonical_case import FENGYUE_PAVILION
from examples.huarong_lane.scenario import NIGHT
from examples.secret_letter.package import (
    PACKAGE_ID as SECRET_LETTER_PACKAGE_ID,
    build_world_package_payload as build_secret_letter_package,
)
from examples.secret_letter.scenario import (
    PLAYER_ROUTE_DESTROY,
    PLAYER_ROUTE_EXPOSE,
    PLAYER_ROUTE_INTERCEPT,
    run_secret_letter_scene,
)

from engine.scene_controller import SceneMode
from engine.llm_telemetry import capture_llm_usage


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 内置华容巷世界的默认玩家角色
DEFAULT_ACTOR_ID = NIGHT
CANONICAL_CH1_PACKAGE_ID = "first_crazy_ch1_checkpoint"
CANONICAL_CH5_PACKAGE_ID = "first_crazy_ch5_checkpoint"

STATIC_DIR = Path(__file__).parent / "static"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_configured_database = Path(os.environ.get("WORLD_DB_PATH", "data/world.sqlite3"))
DATABASE_PATH = (
    _configured_database
    if _configured_database.is_absolute()
    else PROJECT_ROOT / _configured_database
)
_configured_world_dir = Path(os.environ.get("WORLD_PACKAGE_DIR", "worlds"))
WORLD_PACKAGE_DIR = (
    _configured_world_dir
    if _configured_world_dir.is_absolute()
    else PROJECT_ROOT / _configured_world_dir
)
_configured_compiler_database = Path(
    os.environ.get("COMPILER_DB_PATH", "data/compiler.sqlite3")
)
COMPILER_DATABASE_PATH = (
    _configured_compiler_database
    if _configured_compiler_database.is_absolute()
    else PROJECT_ROOT / _configured_compiler_database
)
NOVEL_DIRECTORY = (PROJECT_ROOT / "novels").resolve()
_configured_auth_database = Path(
    os.environ.get("AUTH_DB_PATH", "data/auth.sqlite3")
)
AUTH_DATABASE_PATH = (
    _configured_auth_database
    if _configured_auth_database.is_absolute()
    else PROJECT_ROOT / _configured_auth_database
)
API_CONTRACT_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# 会话存储
# ---------------------------------------------------------------------------


SESSIONS = create_world_store(sqlite_path=DATABASE_PATH)

_builtin_manifest = build_world_package()
_secret_letter_manifest = build_secret_letter_package()
PACKAGES = WorldPackageStore(
    WORLD_PACKAGE_DIR,
    builtins={
        "huarong_lane": {
            "package_id": "huarong_lane",
            "novel": _builtin_manifest["novel"],
            "scenario": "华容巷",
            "anchor": _builtin_manifest["anchor"],
            "default_actor_id": DEFAULT_ACTOR_ID,
            "source_chapters": _builtin_manifest["source_chapters"],
            "snapshot": build_snapshot().dict(),
            "revision": 1,
        },
        CANONICAL_CH1_PACKAGE_ID: {
            "package_id": CANONICAL_CH1_PACKAGE_ID,
            "novel": "第一狂妃：废柴三小姐",
            "scenario": "原著时间线 · 第1章检查点",
            "anchor": "华容巷冲突结束后，角色依据原著认知继续推进第2—5章",
            "default_actor_id": DEFAULT_ACTOR_ID,
            "source_chapters": [
                "第1章 华容巷",
                "第2章 那就脱！",
                "第3章 没身材没脸蛋的女人",
                "第4章 你也算香玉？",
                "第5章 狗仗人势的东西",
            ],
            "snapshot": build_canonical_start_state().dict(),
            "manifest": {
                "entry_kind": "canonical_checkpoint",
                "chapter_key": "first_crazy_ch1_5",
                "chapter_start": 1,
                "chapter_end": 5,
                "checkpoint_chapter": 1,
                "target_chapters": [2, 3, 4, 5],
                "canonical_case_id": "first_crazy_waste_third_lady_ch1_5",
                "evaluation_case_id": "first_crazy_waste_third_lady_ch1_5",
                "description": "原著第1章结束时的可执行世界状态，用于真实 LLM 自动复现第2—5章。",
                "mission": {
                    "title": "改写夜轻歌的命运",
                    "description": "在第 1 章冲突之后查清陷害真相，决定接下来的人生走向。",
                    "progress_flags": [
                        "canonical.lin_warning_done",
                        "canonical.entered_mystic_space",
                        "canonical.jiyue_revealed",
                        "canonical.entered_sansheng_spring",
                        "canonical.dantian_promise",
                        "canonical.returned_fengyue_pavilion",
                        "canonical.hall_summons_issued",
                    ],
                },
                "settlement": {
                    "ending_id": "first_crazy_ch1_checkpoint",
                    "title": "夜家大堂·命运转折",
                    "summary": "夜轻歌带着夜家传唤抵达大堂，第 1–5 章的主线已经闭合。",
                    "reason": "已满足夜家传唤与抵达夜家大堂的确定性终点。",
                    "pending_reason": "继续推进当前世界线，直到夜轻歌应传抵达夜家大堂。",
                    "required_flags": {"canonical.hall_summons_issued": True},
                    "actor_locations": {DEFAULT_ACTOR_ID: "loc_ye_clan_hall"},
                    "base_reward_points": 100,
                    "divergence_bonus_points": 50,
                },
                "next_chapter": {
                    "package_id": "first_crazy_ch5_checkpoint",
                    "title": "《第一狂妃》第 6–10 章",
                    "chapter_start": 6,
                    "chapter_end": 10,
                    "unlock_reason": "完成第 1–5 章世界线结算后解锁。",
                },
                "inheritance_policy_version": 1,
                "carryover_flag_paths": [
                    "canonical.lin_warning_done",
                    "canonical.entered_mystic_space",
                    "canonical.jiyue_revealed",
                    "canonical.entered_sansheng_spring",
                    "canonical.dantian_promise",
                    "canonical.returned_fengyue_pavilion",
                    "canonical.hall_summons_issued",
                ],
                "carryover_item_ids": [],
                "carryover_relation_ids": [],
                "carryover_fact_ids": [],
                "carryover_belief_ids": [],
            },
            "revision": 1,
        },
        CANONICAL_CH5_PACKAGE_ID: {
            "package_id": CANONICAL_CH5_PACKAGE_ID,
            "novel": "第一狂妃：废柴三小姐",
            "scenario": "原著时间线 · 第6—10章",
            "anchor": "夜家大堂对峙开始，拒绝下跪与拒婚，回守风月阁并谋划反击",
            "default_actor_id": DEFAULT_ACTOR_ID,
            "source_chapters": [
                "第6章 跪下！",
                "第7章 要嫁你去嫁",
                "第8章 不嫁！",
                "第9章 美人姐姐",
                "第10章 秦岚的目的",
            ],
            "snapshot": build_canonical_ch5_start_state().dict(),
            "manifest": {
                "entry_kind": "canonical_checkpoint",
                "chapter_key": "first_crazy_ch6_10",
                "chapter_start": 6,
                "chapter_end": 10,
                "checkpoint_chapter": 5,
                "target_chapters": [6, 7, 8, 9, 10],
                "canonical_case_id": "first_crazy_waste_third_lady_ch6_10",
                "evaluation_case_id": "first_crazy_waste_third_lady_ch6_10",
                "description": (
                    "原著第5章结束时的可执行世界状态：夜轻歌已抵达夜家大堂，"
                    "用于真实 LLM 推进第6—10章。"
                ),
                "mission": {
                    "title": "撑过大堂对峙并谋定反击",
                    "description": (
                        "在婚事算计与杀机环伺中保住自己，摸清敌人的下一步棋。"
                    ),
                    "progress_flags": [
                        "canonical.spirit_pressure_applied",
                        "canonical.kneel_refused",
                        "canonical.marriage_forced_proposed",
                        "canonical.refuse_marriage",
                        "canonical.jade_shown",
                        "canonical.jade_claim_dismissed",
                        "canonical.elder_arbitration_requested",
                        "canonical.jingjing_warning",
                        "canonical.identity_confrontation",
                        "canonical.qingqing_restrained",
                        "canonical.kill_order_issued",
                        "canonical.steam_bun_clue",
                        "canonical.counterattack_resolve",
                        "canonical.snow_marriage_scheme",
                    ],
                },
                "settlement": {
                    "ending_id": CANONICAL_CH5_PACKAGE_ID,
                    "title": "风月阁夜话·反击之始",
                    "summary": (
                        "夜轻歌识破婚局与杀令，确认童年旧事，决意主动反击；"
                        "秦岚的联姻私谋也已在暗处落子。第 6–10 章主线闭合。"
                    ),
                    "reason": "反击决意与暗中联姻棋局均已成立。",
                    "pending_reason": "继续推进对峙、退守与暗流，直到反击决意成形。",
                    "required_flags": {
                        "canonical.counterattack_resolve": True,
                        "canonical.snow_marriage_scheme": True,
                        "canonical.kill_order_issued": True,
                    },
                    "actor_locations": {DEFAULT_ACTOR_ID: FENGYUE_PAVILION},
                    "base_reward_points": 100,
                    "divergence_bonus_points": 50,
                },
                "next_chapter": {},
                "inheritance_policy_version": 1,
                "carryover_flag_paths": [
                    "canonical.lin_warning_done",
                    "canonical.entered_mystic_space",
                    "canonical.jiyue_revealed",
                    "canonical.entered_sansheng_spring",
                    "canonical.dantian_promise",
                    "canonical.returned_fengyue_pavilion",
                    "canonical.hall_summons_issued",
                    "canonical.spirit_pressure_applied",
                    "canonical.kneel_refused",
                    "canonical.marriage_forced_proposed",
                    "canonical.refuse_marriage",
                    "canonical.jade_shown",
                    "canonical.jade_claim_dismissed",
                    "canonical.elder_arbitration_requested",
                    "canonical.jingjing_warning",
                    "canonical.identity_confrontation",
                    "canonical.qingqing_restrained",
                    "canonical.kill_order_issued",
                    "canonical.steam_bun_clue",
                    "canonical.counterattack_resolve",
                    "canonical.snow_marriage_scheme",
                ],
                "carryover_item_ids": ["item_glazed_jade_pendant"],
                "carryover_relation_ids": [],
                "carryover_fact_ids": [],
                "carryover_belief_ids": [],
            },
            "revision": 1,
        },
        SECRET_LETTER_PACKAGE_ID: _secret_letter_manifest,
    },
)
COMPILATION_JOBS = CompilationJobStore(COMPILER_DATABASE_PATH)
CHAPTER_CATALOG = ChapterCatalogStore(DATABASE_PATH)
AUTH = AuthStore(AUTH_DATABASE_PATH)


def _initialise_builtin_chapter_catalog() -> None:
    """把整本原著正文导入目录，并发布已有 curated 起始快照。"""
    novel_path = PROJECT_ROOT / "novels/第一狂妃：废柴三小姐.txt"
    if not novel_path.exists():
        return
    try:
        CHAPTER_CATALOG.import_book(
            book_id="first_crazy",
            novel="第一狂妃：废柴三小姐",
            source_path=novel_path,
        )
        for chapter_number, package_id in (
            (1, CANONICAL_CH1_PACKAGE_ID),
            (6, CANONICAL_CH5_PACKAGE_ID),
        ):
            package = PACKAGES.get(package_id)
            entry = CHAPTER_CATALOG.get_entry(
                f"first_crazy:chapter:{chapter_number}"
            )
            if entry is None:
                continue
            manifest = dict(package.manifest or {})
            CHAPTER_CATALOG.publish_entry(
                entry.entry_id,
                package_id=package_id,
                snapshot_id=f"{package_id}:chapter-start",
                canonical=True,
                canonical_case_id=str(
                    manifest.get("canonical_case_id")
                    or manifest.get("evaluation_case_id")
                    or ""
                ),
                mission=dict(manifest.get("mission") or {}),
                identity="快穿者 · 夜轻歌",
                character_summary=[
                    item.display_name for item in package.snapshot.characters.values()
                ][:12],
                location_summary=[
                    item.display_name for item in package.snapshot.locations.values()
                ][:12],
                compiler_version="curated-v1",
            )
    except (ChapterCatalogError, WorldPackageError, OSError):
        # 目录缓存是可重建投影，不阻断已有 package 启动。
        return


_initialise_builtin_chapter_catalog()
_CURRENT_ACTOR: ContextVar[AuthUser] = ContextVar(
    "novelsim_current_actor",
    default=SYSTEM_ACTOR,
)

# TurnPipeline 全局单例 (构造不触发 key 检查，懒加载各 LLM 组件)
PIPELINE = TurnPipeline()

# Web 规划闭环复用与评测相同的受限工具和联合计划执行器。LLM 只生成
# JointPlan/ToolCall，状态写入仍由 ToolRegistry + AgentExecutionStateMachine 完成。
PLAN_TOOL_REGISTRY = create_core_tool_registry()
PLAN_EXECUTOR = JointPlanExecutor(PLAN_TOOL_REGISTRY)


def _create_web_narrative_planner(world_package_id: str):
    return RealLLMNarrativePlanner(
        PLAN_TOOL_REGISTRY,
        world_package_id=world_package_id,
        scenario_family="web_world_simulation",
    )


PLAN_PLANNER_FACTORY = _create_web_narrative_planner


def _manuscript_writer_mode() -> str:
    mode = os.environ.get(
        "NOVELSIM_MANUSCRIPT_WRITER",
        "reuse_narrative",
    ).strip().lower()
    if mode not in {"reuse_narrative", "deterministic", "llm"}:
        raise ManuscriptWriterError(
            "NOVELSIM_MANUSCRIPT_WRITER 必须是 "
            "reuse_narrative、deterministic 或 llm"
        )
    return mode


def _create_web_manuscript_writer(
    manuscript_id: str,
) -> Optional[ManuscriptWriter]:
    if _manuscript_writer_mode() == "llm":
        return LLMManuscriptWriter(manuscript_id=manuscript_id)
    return None


MANUSCRIPT_WRITER_FACTORY: Callable[
    [str], Optional[ManuscriptWriter]
] = _create_web_manuscript_writer


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------


class StartRequest(BaseModel):
    package_id: str = "huarong_lane"
    book_id: str = ""
    entry_id: str = ""
    save_name: str = ""


class TurnRequest(BaseModel):
    session_id: str
    text: str
    use_npc_agents: bool = True


class SettlementRequest(BaseModel):
    expected_version: Optional[int] = None
    ending_id: Optional[str] = None
    request_id: Optional[str] = None


class ChapterTransitionRequest(BaseModel):
    target_package_id: str
    target_entry_id: str = ""
    idempotency_key: str = Field(min_length=8, max_length=200)
    save_name: str = ""


class JointPlanGenerateRequest(BaseModel):
    session_id: str
    goal: str = "依据角色目标、已知事实和世界规则，推动下一段合理剧情。"
    actor_ids: List[str] = Field(default_factory=list)
    max_replans: int = Field(2, ge=0, le=5)
    auto_approve: bool = False


class JointPlanUpdateRequest(BaseModel):
    session_id: str
    plan: Dict[str, Any]


class JointPlanControlRequest(BaseModel):
    session_id: str


class JointPlanExecuteRequest(BaseModel):
    session_id: str
    run_to_completion: bool = False
    max_ticks: int = Field(12, ge=1, le=50)
    auto_replan: bool = True


class ManuscriptRetryRequest(BaseModel):
    session_id: str
    rewrite_ready: bool = False
    expected_revision: Optional[int] = Field(None, ge=0)


class ManuscriptRevisionSelectRequest(BaseModel):
    session_id: str
    revision_number: int = Field(..., ge=1)
    expected_revision: Optional[int] = Field(None, ge=0)


class SecretLetterRunRequest(BaseModel):
    mode: str = SceneMode.free.value
    route: str = "none"
    save_name: str = ""


class DemoRunRequest(BaseModel):
    case_id: str = "invalid_airplane"


class RenameSaveRequest(BaseModel):
    name: str


class ClearHistoryRequest(BaseModel):
    preserve_session_id: str = ""
    confirmation: str


class ImportSaveRequest(BaseModel):
    backup: Dict[str, Any]


class PackageDraftRequest(BaseModel):
    package: Dict[str, Any]
    expected_revision: Optional[int] = None


class PackageReviewRequest(BaseModel):
    target_status: str
    expected_revision: int
    note: str = ""


class CompilationJobRequest(BaseModel):
    novel_path: str
    package_id: str
    book_id: str = ""
    benchmark_id: str = ""
    novel_name: str = ""
    chapters: List[int] = Field(default_factory=list)
    timeline_plan: Dict[int, str] = Field(default_factory=dict)
    volume_plan: Dict[int, str] = Field(default_factory=dict)
    volume_size: int = 20
    model: str = ""
    max_llm_calls: int = 100
    expected_source_hash: str = ""
    auto_start: bool = True


class CompilationJobActionRequest(BaseModel):
    action: str
    additional_llm_calls: StrictNonNegativeBudget = 0


class CompilationJobBudgetRequest(BaseModel):
    additional_llm_calls: StrictPositiveBudget
    reason: str = Field(default="", max_length=500)


class AuthBootstrapRequest(BaseModel):
    username: str
    password: str


class AuthLoginRequest(BaseModel):
    username: str
    password: str


class AuthUserCreateRequest(BaseModel):
    username: str
    password: str
    roles: List[str] = Field(default_factory=list)


class AuthUserStatusRequest(BaseModel):
    active: bool


def _actor() -> AuthUser:
    return _CURRENT_ACTOR.get()


def _parse_utc(value: str):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _permission_error(
    permission: str,
    *,
    resource_type: str = "api",
    resource_id: str = "",
):
    actor = _actor()
    try:
        require_permission(actor, permission)
    except PermissionDenied as exc:
        AUTH.audit(
            actor,
            action=f"permission.{permission}",
            resource_type=resource_type,
            resource_id=resource_id,
            outcome="denied",
            detail={"error": str(exc)},
        )
        return JSONResponse(
            {"status": "forbidden", "error": str(exc)},
            status_code=403,
        )
    return None


def _resolve_novel_path(value: str) -> Path:
    raw = Path((value or "").strip())
    candidate = raw if raw.is_absolute() else NOVEL_DIRECTORY / raw
    resolved = candidate.resolve()
    if resolved != NOVEL_DIRECTORY and NOVEL_DIRECTORY not in resolved.parents:
        raise ValueError("小说文件必须位于 novels/ 目录内")
    if resolved.suffix.lower() != ".txt":
        raise ValueError("目前只支持 novels/ 下的 TXT 小说")
    if not resolved.is_file():
        raise ValueError(f"小说文件不存在: {resolved.name}")
    return resolved


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def state_to_dict(state: WorldState) -> dict:
    """WorldState -> 可 JSON 序列化的 dict (Pydantic 1.x)。"""
    return state.dict()


def serialize_turn(result) -> dict:
    """TurnResult -> 前端可消费的 JSON。

    把 action / narrative / npc_reactions / 新 state 都打包。
    失败状态 (rejected/parse_failed/...) 也带 error 文案。
    """
    payload: Dict[str, Any] = {
        "status": result.status,
        "error": result.error or "",
        "rule_reason": "",
        "rejection_code": "",
        "rejection_message": "",
        "rejection_details": {},
        "action": None,
        "narrative": None,
        "npc_reactions": list(result.npc_reactions or []),
        "state": state_to_dict(result.new_state) if result.new_state else None,
    }

    # action 摘要
    if result.action:
        payload["action"] = {
            "type": result.action.action_type.value,
            "actor": result.action.actor.actor_id,
            "targets": list(result.action.target_ids),
            "goal": result.action.declared_goal,
            "visibility": result.action.visibility,
        }

    # narrative
    if result.narrative:
        payload["narrative"] = {
            "narration": result.narrative.narration,
            "dialogues": [
                {
                    "speaker_id": d.speaker_id,
                    "line": d.line,
                    "tone": d.tone,
                    "to_id": d.to_id,
                }
                for d in result.narrative.dialogues
            ],
            "system_hints": list(result.narrative.system_hints),
            "viewpoint": result.narrative.viewpoint,
            "grounded_event_ids": list(
                result.narrative.grounded_event_ids
            ),
            "referenced_entity_ids": list(
                result.narrative.referenced_entity_ids
            ),
        }

    # 规则拒绝原因
    if result.rule_result and result.rule_result.why():
        payload["rule_reason"] = result.rule_result.why()
    intent_result = getattr(result, "intent_result", None)
    if intent_result is not None and intent_result.reason_code is not None:
        payload["rejection_code"] = intent_result.reason_code.value
        payload["rejection_message"] = intent_result.message
        payload["rejection_details"] = dict(intent_result.details)

    return payload


def serialize_history(records) -> list:
    """把持久化 TurnRecord 还原成 StoryFeed 使用的交替卡片列表。"""

    turns = []
    for record in records:
        turns.append({"player_input": record.player_input})
        result = dict(record.result)
        result.pop("state", None)
        turns.append(result)
    return turns


def _manuscript_projection_payload(passage, warning: str = "") -> Dict[str, Any]:
    if passage is None:
        return {
            "status": "failed",
            "passage_id": "",
            "revision": 0,
            "warning": warning,
        }
    return {
        "status": passage.generation_status.value,
        "passage_id": passage.passage_id,
        "revision": passage.current_revision,
        "warning": warning or passage.last_error,
    }


def _manuscript_revision_projection(revision, *, selected: bool) -> Dict[str, Any]:
    content = revision.passages[0] if revision.passages else None
    return {
        "revision_number": int(revision.revision_number),
        "revision_id": revision.revision_id,
        "parent_revision_id": revision.parent_revision_id,
        "source": revision.source.value,
        "writer_version": revision.writer_version,
        "title": content.title if content is not None else "",
        "paragraphs": list(content.paragraphs) if content is not None else [],
        "text": content.text if content is not None else "",
        "dialogues": [item.dict() for item in content.dialogues]
        if content is not None
        else [],
        "system_hints": list(content.system_hints)
        if content is not None
        else [],
        "viewpoint": content.viewpoint if content is not None else "third_person",
        "metadata": dict(revision.metadata or {}),
        "selected": selected,
    }


def _persist_manuscript_batch(
    session_id: str,
    events: Sequence[WorldEvent],
    state: WorldState,
    *,
    narrative: Optional[NarrativeOutput] = None,
    writer: Optional[ManuscriptWriter] = None,
    retry_ready_with_error: bool = False,
    rewrite_ready: bool = False,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """Project committed events into one stored passage without changing facts."""

    ordered = sorted(events, key=lambda item: item.new_version)
    reserved = None
    try:
        if rewrite_ready and expected_revision is None:
            raise ManuscriptWriterError("重写 ready 稿件必须提供当前 revision")
        if not ordered:
            raise ManuscriptWriterError("稿件批次没有已提交事件")
        if len({event.event_id for event in ordered}) != len(ordered):
            raise ManuscriptWriterError("稿件批次包含重复事件")
        manuscript = SESSIONS.ensure_manuscript(session_id)
        selected_writer = writer or MANUSCRIPT_WRITER_FACTORY(
            manuscript.manuscript_id
        )
        mode = _manuscript_writer_mode()
        if selected_writer is not None:
            kind = "llm"
        elif narrative is not None and mode == "reuse_narrative":
            kind = "narrative_output"
        else:
            kind = "deterministic"
        reserved = SESSIONS.reserve_manuscript_passage(
            session_id,
            [event.event_id for event in ordered],
            generation_kind=kind,
        )
        if rewrite_ready:
            if reserved.current_revision != expected_revision:
                raise ManuscriptRevisionConflict(
                    "稿件 revision 已变化，请刷新后重试"
                )
        elif (
            reserved.generation_status == ManuscriptGenerationStatus.ready
            and not (retry_ready_with_error and reserved.last_error)
        ):
            return _manuscript_projection_payload(reserved)

        writing_state = state
        validate_current_state = not rewrite_ready
        if rewrite_ready:
            historical_reader = getattr(
                SESSIONS,
                "get_state_at_version",
                None,
            )
            if callable(historical_reader):
                try:
                    historical_state = historical_reader(
                        session_id,
                        reserved.to_world_version,
                    )
                except StateVersionUnavailable:
                    # 迁移前的旧会话只有最新快照，保留原有弱化校验。
                    historical_state = None
                if historical_state is not None:
                    writing_state = historical_state
                    validate_current_state = True

        list_campaign = getattr(
            SESSIONS,
            "list_campaign_manuscript_passages",
            SESSIONS.list_manuscript_passages,
        )
        previous = next(
            (
                passage
                for passage in reversed(list_campaign(session_id))
                if passage.passage_id != reserved.passage_id
                and passage.manuscript_sequence < reserved.manuscript_sequence
                and passage.generation_status == ManuscriptGenerationStatus.ready
            ),
            None,
        )
        metadata = SESSIONS.get_metadata(session_id)
        chapter_number = metadata.chapter_number if metadata is not None else None
        fallback_warning = ""
        if selected_writer is not None:
            try:
                revision = selected_writer.write(
                    ordered,
                    writing_state,
                    chapter_number=chapter_number,
                    previous_passage=previous,
                )
            except Exception as exc:  # noqa: BLE001 - provider failure degrades safely
                fallback_warning = (
                    "稿件模型暂不可用，已使用安全写作器生成正文"
                )
                revision = DeterministicManuscriptWriter(
                    manuscript_id=manuscript.manuscript_id,
                    events_per_passage=len(ordered),
                ).write(
                    ordered,
                    writing_state,
                    chapter_number=chapter_number,
                    previous_passage=previous,
                )
                revision.metadata["fallback_from"] = "llm"
                revision.metadata["fallback_reason"] = type(exc).__name__
        elif narrative is not None and mode == "reuse_narrative":
            revision = narrative_output_to_revision(
                narrative,
                ordered,
                writing_state,
                chapter_number=chapter_number,
                previous_passage=previous,
                manuscript_id=manuscript.manuscript_id,
            )
        else:
            revision = DeterministicManuscriptWriter(
                manuscript_id=manuscript.manuscript_id,
                events_per_passage=len(ordered),
            ).write(
                ordered,
                writing_state,
                chapter_number=chapter_number,
                previous_passage=previous,
            )
        check = check_manuscript_revision(
            revision,
            ordered,
            writing_state,
            validate_current_state=validate_current_state,
        )
        if not check.valid:
            raise ManuscriptWriterError(check.why())
        completed = SESSIONS.complete_manuscript_passage(
            reserved.passage_id,
            revision,
            expected_current_revision=(
                expected_revision
                if rewrite_ready
                else reserved.current_revision
            ),
        )
        return _manuscript_projection_payload(completed, fallback_warning)
    except Exception as exc:  # noqa: BLE001 - derived projection must not roll back facts
        if isinstance(exc, ManuscriptRevisionConflict):
            raise
        warning = f"小说正文生成待重试: {type(exc).__name__}: {exc}"
        if reserved is not None:
            try:
                failed = SESSIONS.fail_manuscript_passage(
                    reserved.passage_id,
                    warning,
                )
                return _manuscript_projection_payload(failed, warning)
            except Exception as persist_exc:  # noqa: BLE001
                warning = (
                    f"{warning}; 记录稿件失败状态时发生 "
                    f"{type(persist_exc).__name__}: {persist_exc}"
                )
        return _manuscript_projection_payload(None, warning)


def _manifest_terminal_reached(
    state: WorldState,
    events: Optional[List[Any]],
    *,
    initial_state: Optional[WorldState],
    settlement_spec: Dict[str, Any],
) -> bool:
    required_flags = dict(settlement_spec.get("required_flags") or {})
    actor_locations = dict(settlement_spec.get("actor_locations") or {})

    def reached(flags: Dict[str, Any], locations: Dict[str, str]) -> bool:
        return all(flags.get(str(path)) == value for path, value in required_flags.items()) and all(
            locations.get(str(actor_id)) == str(location_id)
            for actor_id, location_id in actor_locations.items()
        )

    if reached(
        state.flags,
        {
            actor_id: actor.location_id
            for actor_id, actor in state.characters.items()
        },
    ):
        return True

    baseline = initial_state or state
    flags = dict(baseline.flags)
    locations = {
        actor_id: actor.location_id
        for actor_id, actor in baseline.characters.items()
    }
    if reached(flags, locations):
        return True
    for event in events or []:
        for operation in event.patch.operations:
            if operation.op == OperationKind.set_flag:
                flags[operation.path] = operation.value
            elif operation.op == OperationKind.move_character:
                locations[operation.target_id] = operation.location_id
            if reached(flags, locations):
                return True
    return False


def _canonical_terminal_reached(
    state: WorldState,
    events: Optional[List[Any]] = None,
    *,
    initial_state: Optional[WorldState] = None,
) -> bool:
    return _manifest_terminal_reached(
        state,
        events,
        initial_state=initial_state,
        settlement_spec={
            "required_flags": {"canonical.hall_summons_issued": True},
            "actor_locations": {DEFAULT_ACTOR_ID: "loc_ye_clan_hall"},
        },
    )


def _settlement_evaluation(
    state: WorldState,
    package,
    events: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Evaluate settlement from authoritative state only.

    ``ending.available`` is retained as a compatibility escape hatch for old
    saves, while the canonical checkpoint uses its deterministic terminal fact.
    """
    ending_id = str(
        state.flags.get("settlement.ending_id")
        or state.flags.get("ending.id")
        or package.package_id
    )
    settlement_spec = dict(package.manifest.get("settlement") or {})
    if settlement_spec:
        title = str(settlement_spec.get("title") or "世界线结局")
        summary = str(settlement_spec.get("summary") or package.anchor or "")
        terminal = _manifest_terminal_reached(
            state,
            events,
            initial_state=package.snapshot,
            settlement_spec=settlement_spec,
        )
        if terminal or state.flags.get("ending.available"):
            return {
                "ending_id": str(settlement_spec.get("ending_id") or ending_id),
                "title": title,
                "objective_satisfied": True,
                "reason": str(settlement_spec.get("reason") or "已满足确定性终点。"),
                "summary": summary,
                "can_settle": True,
            }
        return {
            "ending_id": str(settlement_spec.get("ending_id") or ending_id),
            "title": title,
            "objective_satisfied": False,
            "reason": str(
                settlement_spec.get("pending_reason")
                or "当前世界线尚未达到可结算结局。"
            ),
            "summary": str(
                settlement_spec.get("pending_summary")
                or "继续推进当前世界线，直到确定性终点成立。"
            ),
            "can_settle": False,
        }
    available = bool(state.flags.get("ending.available"))
    return {
        "ending_id": ending_id,
        "title": str(state.flags.get("ending.title") or "世界线结局"),
        "objective_satisfied": available,
        "reason": "世界包已标记结局可用。" if available else "当前世界线尚未达到可结算结局。",
        "summary": str(state.flags.get("ending.summary") or package.anchor or ""),
        "can_settle": available,
    }


def _settlement_reward(state: WorldState, package, evaluation: Dict[str, Any], events: Optional[List[Any]] = None) -> int:
    """Return deterministic per-world settlement points; never accepts client values."""
    if not evaluation["objective_satisfied"]:
        return 0
    settlement_spec = dict(package.manifest.get("settlement") or {})
    points = int(settlement_spec.get("base_reward_points") or 100)
    divergence_bonus = int(
        settlement_spec.get("divergence_bonus_points") or 0
    )
    changed = any(
        getattr(event.patch.causal_evidence, "authority", "")
        == "player_action_with_npc_reactions"
        for event in (events or [])
    )
    points += divergence_bonus if changed else 0
    return points


def _settlement_projection(session_id: str, state: WorldState, metadata, package) -> Dict[str, Any]:
    events = SESSIONS.list_events(session_id)
    evaluation = _settlement_evaluation(state, package, events)
    settled = state.flags.get("settlement.status") == "settled"
    status = "settled" if settled else ("available" if evaluation["can_settle"] else "unavailable")
    reward_points = int(state.flags.get("settlement.reward_points") or 0)
    if not settled and evaluation["can_settle"]:
        reward_points = _settlement_reward(
            state, package, evaluation, events
        )
    next_spec = _chapter_next_spec(package)
    next_chapter = None
    inheritance_preview = []
    if next_spec:
        lineage = _session_lineage(session_id)
        unlock_key = f"world:{next_spec['package_id']}"
        try:
            target_package = PACKAGES.get(next_spec["package_id"])
        except WorldPackageNotFound:
            target_package = None
        unlocked = bool(lineage and unlock_key in _campaign_unlock_keys(
            lineage.campaign_id
        ))
        child_session_id = ""
        children = _campaign_children(lineage) if lineage is not None else []
        for child in children:
            if child.target_world_package_id == next_spec["package_id"]:
                child_session_id = child.session_id
        if not target_package:
            next_status = "unavailable"
            reason = "下一章世界线尚未开放。"
        elif settled:
            next_status = "created" if child_session_id else (
                "unlocked" if unlocked else "locked"
            )
            reason = {
                "created": "下一章世界线已经创建。",
                "unlocked": str(next_spec.get("unlock_reason") or "已完成本章结算，可进入下一章。"),
                "locked": "完成本世界线结算后解锁下一章。",
            }[next_status]
            inheritance_preview = _inheritance_plan(
                state, package, target_package
            )["summary"][:6]
        else:
            next_status = "locked"
            reason = "完成本世界线结算后解锁下一章。"
        next_chapter = {
            **next_spec,
            "status": next_status,
            "reason": reason,
            "child_session_id": child_session_id,
        }
    return {
        "status": status,
        "ending_id": str(state.flags.get("settlement.ending_id") or evaluation["ending_id"]),
        "ending_title": str(state.flags.get("settlement.title") or evaluation["title"]),
        "title": str(state.flags.get("settlement.title") or evaluation["title"]),
        "summary": str(state.flags.get("settlement.summary") or evaluation["summary"]),
        "reason": "已完成结算。" if settled else evaluation["reason"],
        "objective_satisfied": bool(evaluation["objective_satisfied"] or settled),
        "can_settle": bool(evaluation["can_settle"] and not settled),
        "reward_preview": {"reward_points": reward_points},
        "reward": {"reward_points": reward_points} if settled else None,
        "reward_points": reward_points,
        "reward_claimed": bool(state.flags.get("settlement.reward_claimed")) if settled else False,
        "settled_at": state.flags.get("settlement.settled_at") if settled else None,
        "settlement_version": state.version if settled else None,
        "world_version": state.version,
        "can_continue": status == "unavailable",
        "next_chapter": next_chapter,
        "inheritance_preview": inheritance_preview,
    }


def _repair_legacy_canonical_facts(
    session_id: str,
    state: WorldState,
    metadata,
) -> WorldState:
    """为旧版第 6–10 章快照补录后来加入的权威事实。"""
    if metadata.world_package_id != CANONICAL_CH5_PACKAGE_ID:
        return state
    required_ids = {
        "fact_qingqing_poisoned_tea",
        "fact_self_framing_sister",
        "fact_self_in_huarong_lane",
    }
    if required_ids.issubset(state.facts):
        return state
    source_facts = build_canonical_ch5_start_state().facts
    facts = {
        fact_id: source_facts[fact_id]
        for fact_id in required_ids
        if fact_id in source_facts and fact_id not in state.facts
    }
    if not facts:
        return state
    migrate_world_facts(
        SESSIONS,
        session_id,
        facts,
        migration_id="canonical_ch6_10_facts_v1",
    )
    repaired = SESSIONS.get_state(session_id)
    if repaired is None:
        raise SessionNotFound(f"会话不存在: {session_id}")
    return repaired


def _session_lineage(session_id: str) -> Optional[SessionLineage]:
    """只读取已有谱系，不因展示或查询普通存档而创建旅程记录。"""

    get_lineage = getattr(SESSIONS, "get_session_lineage", None)
    if not callable(get_lineage):
        return None
    try:
        return get_lineage(session_id)
    except (PersistenceError, AttributeError):
        return None


def _campaign_unlock_keys(campaign_id: str) -> set:
    listing = getattr(SESSIONS, "list_campaign_progression", None)
    if not callable(listing):
        return set()
    try:
        progression = listing(campaign_id)
    except PersistenceError:
        return set()
    return {item.unlock_key for item in progression.unlocks}


def _campaign_children(lineage: SessionLineage) -> List[SessionLineage]:
    listing = getattr(SESSIONS, "list_campaign_progression", None)
    if not callable(listing):
        return []
    try:
        progression = listing(lineage.campaign_id)
    except PersistenceError:
        return []
    return [
        item
        for item in progression.lineage
        if item.parent_session_id == lineage.session_id
    ]


def _chapter_next_spec(package) -> Dict[str, Any]:
    spec = dict(package.manifest.get("next_chapter") or {})
    if not spec.get("package_id"):
        return {}
    return spec


def _inheritance_plan(
    parent_state: WorldState,
    parent_package,
    child_package,
) -> Dict[str, Any]:
    """Build the whitelist carry-over plan between two published checkpoints.

    Flags are carried exactly; items transfer only when the child snapshot
    re-declares them; anything else is recorded honestly as omitted so the
    manifest stays an auditable contract.
    """

    policy_version = int(
        parent_package.manifest.get("inheritance_policy_version") or 1
    )
    flag_paths = [
        str(path)
        for path in (parent_package.manifest.get("carryover_flag_paths") or [])
        if str(path).strip()
    ]
    operations: List[Operation] = []
    entries: List[Dict[str, Any]] = []
    for path in flag_paths:
        value = parent_state.flags.get(path)
        if value in (None, False):
            entries.append(
                {"kind": "flag", "path": path, "applied": False,
                 "reason": "父世界线未确认该持久事实"}
            )
            continue
        operations.append(
            Operation(op=OperationKind.set_flag, path=path, value=value)
        )
        entries.append(
            {"kind": "flag", "path": path, "applied": True, "value": value}
        )
    actor_id = child_package.default_actor_id
    for item_id in parent_package.manifest.get("carryover_item_ids") or []:
        source_item = parent_state.items.get(str(item_id))
        target_item = child_package.snapshot.items.get(str(item_id))
        if source_item is None or target_item is None:
            entries.append(
                {"kind": "item", "item_id": str(item_id), "applied": False,
                 "reason": "目标章节快照未声明该物品"}
            )
            continue
        operations.append(
            Operation(
                op=OperationKind.transfer_item,
                item_id=str(item_id),
                target_id=actor_id,
                reason="inheritance:previous_chapter",
            )
        )
        entries.append(
            {"kind": "item", "item_id": str(item_id), "applied": True}
        )
    omitted_kinds = {
        "carryover_relation_ids": "relation",
        "carryover_fact_ids": "fact",
        "carryover_belief_ids": "belief",
    }
    for field_name, kind in omitted_kinds.items():
        for entry_id in parent_package.manifest.get(field_name) or []:
            entries.append(
                {"kind": kind, "entry_id": str(entry_id), "applied": False,
                 "reason": "继承策略 v1 暂不搬运该类型"}
            )
    settlement_flags = {
        path: parent_state.flags.get(path)
        for path in (
            "settlement.ending_id",
            "settlement.title",
            "settlement.reward_points",
        )
        if parent_state.flags.get(path) is not None
    }
    inherited_summary_flags = {
        f"inheritance.prev_{key.split('.', 1)[1]}": value
        for key, value in settlement_flags.items()
    }
    for path, value in inherited_summary_flags.items():
        operations.append(
            Operation(op=OperationKind.set_flag, path=path, value=value)
        )
    summary = [
        {"title": entry["kind"], "text": entry.get("path") or entry.get(
            "entry_id"
        ) or entry.get("item_id", "")}
        for entry in entries
        if entry["applied"]
    ]
    return {
        "policy_version": policy_version,
        "operations": operations,
        "entries": entries,
        "summary": summary,
        "inherited_flag_paths": [
            operation.path
            for operation in operations
            if operation.op == OperationKind.set_flag
        ],
    }


def _planning_settlement_response(
    session_id: str,
    state: WorldState,
    metadata,
    package,
):
    settlement = _settlement_projection(session_id, state, metadata, package)
    if settlement["status"] == "unavailable":
        return None
    settled = settlement["status"] == "settled"
    return JSONResponse(
        {
            "status": "settled" if settled else "settlement_required",
            "error": (
                "当前世界线已完成结算，不能继续本章节自动演化。"
                if settled
                else "当前世界线已抵达终点，请先完成结算。"
            ),
            "settlement": settlement,
            "state_version": state.version,
            "world_version": state.version,
        },
        status_code=409,
    )


def _save_projection(metadata) -> dict:
    state = SESSIONS.get_state(metadata.session_id)
    package = PACKAGES.get(metadata.world_package_id)
    settlement = (
        _settlement_projection(metadata.session_id, state, metadata, package)
        if state is not None
        else None
    )
    lineage = _session_lineage(metadata.session_id)
    manifest = dict(package.manifest or {})
    chapter_label = ""
    if manifest.get("entry_kind") == "canonical_checkpoint":
        targets = [int(item) for item in (manifest.get("target_chapters") or [])]
        if targets:
            chapter_label = f"第 {min(targets)}–{max(targets)} 章"
    projection = {
        "session_id": metadata.session_id,
        "name": metadata.save_name,
        "world_package_id": metadata.world_package_id,
        "default_actor": metadata.default_actor_id,
        "version": metadata.state_version,
        "created_at": metadata.created_at,
        "updated_at": metadata.updated_at,
        "status": "settled" if settlement and settlement["status"] == "settled" else "active",
        "settlement_status": settlement["status"] if settlement else "unavailable",
        "ending_id": settlement["ending_id"] if settlement else "",
        "ending_title": settlement["ending_title"] if settlement else "",
        "settled_at": settlement["settled_at"] if settlement else None,
        "reward_points": settlement["reward_points"] if settlement else 0,
        "campaign_id": lineage.campaign_id if lineage is not None else "",
        "root_session_id": lineage.root_session_id if lineage is not None else "",
        "parent_session_id": (
            lineage.parent_session_id if lineage is not None else ""
        ),
        "lineage_depth": lineage.depth if lineage is not None else 0,
        "depth": lineage.depth if lineage is not None else 0,
        "chapter_label": chapter_label,
        "book_id": metadata.book_id,
        "entry_id": metadata.entry_id,
        "chapter_number": metadata.chapter_number,
        "entry_revision": metadata.entry_revision,
    }
    if settlement is not None:
        projection["chapter_access"] = [
            {
                "package_id": item.get("package_id"),
                "status": item.get("status"),
                "reason": item.get("reason"),
                "child_session_id": item.get("child_session_id"),
            }
            for item in [settlement.get("next_chapter") or {}]
            if item.get("package_id")
        ]
    return projection


def serialize_save(metadata) -> dict:
    return _save_projection(metadata)


def retrieve_npc_memories(
    session_id: str,
    state: WorldState,
    query: str,
) -> Dict[str, list]:
    """为每个自主 NPC 检索其私有长期记忆。"""

    context: Dict[str, list] = {}
    for character_id, psyche in state.character_psyches.items():
        if psyche.is_player:
            continue
        memories = SESSIONS.search_character_memories(
            session_id,
            character_id,
            query,
            limit=8,
        )
        memories = filter_compatible_memories(
            memories,
            state,
            character_id,
        )
        if memories:
            context[character_id] = [
                (
                    f"[反思，主张置信度 {item.claim_confidence:.2f}，"
                    f"证据一致性 {item.semantic_score:.2f}] "
                    f"{item.content}"
                    if item.memory_type == "reflection"
                    else f"[经历] {item.content}"
                )
                for item in memories[:4]
            ]
    return context


def persist_turn_memories(
    session_id: str,
    player_input: str,
    result,
) -> None:
    """把已提交事件投影为参与角色的情景记忆。"""

    if result.event is None or result.new_state is None:
        return
    narration = (
        result.narrative.narration
        if result.narrative and result.narrative.narration
        else ""
    )
    record_event_memory(
        SESSIONS,
        session_id,
        result.new_state,
        result.event,
        player_input=player_input,
        narration=narration,
    )
    reflections_enabled = os.environ.get(
        "MEMORY_REFLECTIONS_ENABLED",
        "true",
    ).strip().lower() not in {"0", "false", "no", "off"}
    try:
        interval = int(os.environ.get("MEMORY_REFLECTION_INTERVAL", "5"))
        min_episodes = int(
            os.environ.get("MEMORY_REFLECTION_MIN_EPISODES", "3")
        )
        semantic_threshold = float(
            os.environ.get(
                "MEMORY_REFLECTION_SEMANTIC_THRESHOLD",
                "0.72",
            )
        )
    except ValueError as exc:
        raise PersistenceError(
            "反思记忆间隔、最小情景数或语义门槛配置无效"
        ) from exc
    if (
        not reflections_enabled
        or interval < 1
        or result.new_state.version % interval != 0
    ):
        return
    semantic_judge_enabled = os.environ.get(
        "MEMORY_REFLECTION_SEMANTIC_JUDGE_ENABLED",
        "true",
    ).strip().lower() not in {"0", "false", "no", "off"}
    semantic_judge = (
        ReflectionSemanticJudge()
        if semantic_judge_enabled
        else None
    )
    for character_id, psyche in result.new_state.character_psyches.items():
        if psyche.is_player:
            continue
        reflect_character_memories(
            SESSIONS,
            session_id,
            result.new_state,
            character_id,
            semantic_judge=semantic_judge,
            semantic_threshold=semantic_threshold,
            min_new_episodes=max(2, min_episodes),
        )


def serialize_session(session_id: str, *, resumed: bool = True) -> dict:
    """读取一条完整世界线并生成前端启动载荷。"""

    metadata = SESSIONS.get_metadata(session_id)
    state = SESSIONS.get_state(session_id)
    history = SESSIONS.list_turns(session_id)
    if metadata is None or state is None:
        raise SessionNotFound(f"会话不存在: {session_id}")
    package = PACKAGES.get(metadata.world_package_id)
    return {
        "status": "ok",
        "session_id": session_id,
        "default_actor": metadata.default_actor_id,
        "world_meta": {
            **package.world_meta(),
            "book_id": metadata.book_id,
            "entry_id": metadata.entry_id,
            "chapter_number": metadata.chapter_number,
            "entry_revision": metadata.entry_revision,
        },
        "state": state_to_dict(state),
        "save": serialize_save(metadata),
        "turns": serialize_history(history),
        "resumed": resumed,
    }


def _joint_plan_store_supported() -> bool:
    return all(
        callable(getattr(SESSIONS, name, None))
        for name in (
            "save_joint_plan_runtime",
            "get_joint_plan_runtime",
            "list_joint_plan_runtimes",
        )
    )


def _planning_permissions(actor_ids: List[str]) -> Dict[str, set]:
    return {
        actor_id: set(CORE_TOOL_PERMISSIONS)
        for actor_id in actor_ids
    }


def _select_planning_actors(
    state: WorldState,
    default_actor_id: str,
    requested_actor_ids: List[str],
) -> List[str]:
    requested = list(
        dict.fromkeys(
            actor_id.strip()
            for actor_id in requested_actor_ids
            if actor_id.strip()
        )
    )
    if requested:
        missing = [actor_id for actor_id in requested if actor_id not in state.characters]
        if missing:
            raise ValueError("规划角色不存在: " + "、".join(missing))
        dead = [
            actor_id
            for actor_id in requested
            if not state.characters[actor_id].is_alive
        ]
        if dead:
            raise ValueError("死亡角色不能参与规划: " + "、".join(dead))
        return requested[:4]

    driver = state.characters.get(default_actor_id)
    if driver is None or not driver.is_alive:
        driver = next(
            (character for character in state.characters.values() if character.is_alive),
            None,
        )
    if driver is None:
        raise ValueError("当前世界没有可参与规划的存活角色")

    candidates = []
    for actor_id, character in state.characters.items():
        if not character.is_alive:
            continue
        psyche = state.character_psyches.get(actor_id)
        active_goals = [] if psyche is None else [
            goal
            for goal in psyche.goals
            if not goal.achieved
            and getattr(goal, "status", "active") == "active"
            and _planning_goal_is_activated(goal, driver.location_id, state)
        ]
        same_scene = character.location_id == driver.location_id
        if actor_id != driver.character_id and not active_goals:
            continue
        if actor_id != driver.character_id and not same_scene:
            active_goals = [
                goal
                for goal in active_goals
                if list(
                    getattr(goal, "activation_target_location_ids", []) or []
                )
            ]
            if not active_goals:
                continue
        priority = max((goal.priority for goal in active_goals), default=0.0)
        candidates.append(
            (
                0 if actor_id == driver.character_id else (1 if same_scene else 2),
                -priority,
                actor_id,
            )
        )

    candidates.sort()
    ready_drivers = [
        item for item in candidates if ready_ability_ids(state, item[2])
    ]
    if ready_drivers:
        return [ready_drivers[0][2]]

    selected = [driver.character_id]
    for item in candidates:
        if item[0] >= 2:
            continue
        if item[2] not in selected and len(selected) < 3:
            selected.append(item[2])
    return selected


def _planning_goal_is_activated(
    goal,
    driver_location_id: Optional[str],
    state: WorldState,
) -> bool:
    locations = list(
        getattr(goal, "activation_target_location_ids", []) or []
    )
    if locations and driver_location_id not in locations:
        return False
    required_flags = dict(getattr(goal, "activation_flags", {}) or {})
    return all(
        state.flags.get(str(key)) == value
        for key, value in required_flags.items()
    )


def _serialize_joint_plan(plan, runtime) -> Dict[str, Any]:
    actor_chains = []
    for actor_id, chain in plan.actor_chains.items():
        pointer = runtime.actor_step_pointers.get(actor_id, 0)
        steps = []
        completed = set(runtime.completed_steps.get(actor_id, []))
        for index, step in enumerate(chain.steps):
            if step.step_id in completed:
                step_status = "completed"
            elif index == pointer and actor_id in runtime.blocked_reasons:
                step_status = "blocked"
            elif index == pointer:
                step_status = "ready"
            else:
                step_status = "pending"
            payload = step.dict()
            payload["status"] = step_status
            steps.append(payload)
        actor_chains.append(
            {
                "actor_id": actor_id,
                "current_step": pointer,
                "blocked_reason": runtime.blocked_reasons.get(actor_id, ""),
                "steps": steps,
            }
        )
    return {
        "plan_id": plan.plan_id,
        "goal_id": plan.goal_id,
        "goal": str(plan.metadata.get("beat_goal") or plan.goal_id),
        "revision": plan.revision,
        "base_world_version": plan.base_world_version,
        "observed_world_version": runtime.observed_world_version,
        "status": runtime.status.value,
        "replan_count": runtime.replan_count,
        "max_replans": runtime.max_replans,
        "stale_reasons": list(runtime.stale_reasons),
        "deadlock_cycle": list(runtime.deadlock_cycle),
        "actor_chains": actor_chains,
        "raw_plan": plan.dict(),
        "editable": runtime.status == PlanRuntimeStatus.draft,
    }


def _load_joint_plan(session_id: str, plan_id: str):
    if not _joint_plan_store_supported():
        raise PersistenceError("当前存储后端不支持联合计划运行时")
    pair = SESSIONS.get_joint_plan_runtime(session_id, plan_id)
    if pair is None:
        raise SessionNotFound(f"联合计划不存在: {plan_id}")
    return pair


def serialize_presentation_snapshot(state: WorldState) -> dict:
    """将字典型权威状态转换为 Unity JsonUtility 可解析的数组结构。"""

    return {
        "timeline_id": state.timeline_id,
        "state_version": state.version,
        "current_scene_id": state.current_scene_id or "",
        "last_sequence": cursor_after_world_version(state.version),
        "characters": [
            {
                "character_id": character.character_id,
                "display_name": character.display_name,
                "location_id": character.location_id or "",
                "is_alive": character.is_alive,
                "inventory": list(character.inventory),
            }
            for _, character in sorted(state.characters.items())
        ],
        "items": [
            {
                "item_id": item.item_id,
                "display_name": item.display_name,
                "owner_id": item.owner_id or "",
                "location_id": item.location_id or "",
                "quantity": item.quantity,
                "accessible": item.accessible,
                "destroyed": bool(item.attrs.get("destroyed")),
            }
            for _, item in sorted(state.items.items())
        ],
        "alliances": [
            {
                "alliance_id": alliance.alliance_id,
                "member_ids": list(alliance.member_ids),
                "goal_key": alliance.goal_key,
                "status": alliance.status,
            }
            for _, alliance in sorted(state.alliances.items())
        ],
    }


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------


app = FastAPI(
    title="NovelSim API",
    version=API_CONTRACT_VERSION,
    description=(
        "AI 世界运行、小说编译和创作者治理 API。"
        "主版本内保持已发布字段向后兼容。"
    ),
)


@app.middleware("http")
async def api_contract_and_auth(request: Request, call_next):
    """为创作者控制面统一执行 Bearer 身份校验。"""

    path = request.url.path
    protected = (
        path.startswith("/api/creator")
        or path.startswith("/api/admin")
        or path == "/api/auth/me"
    )
    actor = SYSTEM_ACTOR
    if protected:
        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return JSONResponse(
                {
                    "status": "unauthorized",
                    "error": "创作者接口需要 Bearer 访问令牌",
                },
                status_code=401,
                headers={"X-NovelSim-Contract": API_CONTRACT_VERSION},
            )
        try:
            actor = AUTH.resolve_token(token.strip())
            base_permission = (
                "users.manage"
                if path.startswith("/api/admin")
                else "creator.read"
            )
            require_permission(actor, base_permission)
        except AuthenticationError as exc:
            return JSONResponse(
                {"status": "unauthorized", "error": str(exc)},
                status_code=401,
                headers={"X-NovelSim-Contract": API_CONTRACT_VERSION},
            )
        except PermissionDenied as exc:
            AUTH.audit(
                actor,
                action="request.access",
                resource_type="api",
                resource_id=path,
                outcome="denied",
                detail={"method": request.method, "error": str(exc)},
            )
            return JSONResponse(
                {"status": "forbidden", "error": str(exc)},
                status_code=403,
                headers={"X-NovelSim-Contract": API_CONTRACT_VERSION},
            )

    context_token = _CURRENT_ACTOR.set(actor)
    try:
        response = await call_next(request)
    finally:
        _CURRENT_ACTOR.reset(context_token)
    response.headers["X-NovelSim-Contract"] = API_CONTRACT_VERSION
    return response


@app.on_event("shutdown")
def close_storage_clients() -> None:
    """释放 Qdrant Local Mode 文件锁；SQLite 本身无需常驻连接。"""

    closer = getattr(SESSIONS, "close", None)
    if callable(closer):
        closer()


# 静态资源 (前端构建产物里的 js/css)
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


def _sqlite_readiness(path: Path, required_tables: Set[str]) -> str:
    if not path.is_file():
        return "missing"
    conn = None
    try:
        conn = sqlite3.connect(
            str(path),
            timeout=1,
            uri=False,
        )
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity.lower() != "ok":
            return "integrity_error"
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if not required_tables.issubset(tables):
            return "schema_incomplete"
        return "ok"
    except (OSError, sqlite3.Error):
        return "unavailable"
    finally:
        if conn is not None:
            conn.close()


def _readiness_report() -> Dict[str, Any]:
    checks = {
        "world_database": _sqlite_readiness(
            DATABASE_PATH,
            {
                "world_sessions",
                "world_events",
                "world_turns",
                "book_catalog",
                "chapter_content_cache",
                "chapter_entries",
            },
        ),
        "compiler_database": _sqlite_readiness(
            COMPILER_DATABASE_PATH,
            {"compiler_jobs", "compiler_job_chapters"},
        ),
        "auth_database": _sqlite_readiness(
            AUTH_DATABASE_PATH,
            {"auth_users", "auth_tokens", "audit_events"},
        ),
        "world_package_directory": (
            "ok" if WORLD_PACKAGE_DIR.is_dir() else "missing"
        ),
    }
    return {
        "status": "ok" if all(value == "ok" for value in checks.values()) else "not_ready",
        "checks": checks,
    }


@app.get("/health")
def api_health():
    """轻量存活检查，不访问数据库或外部服务。"""
    return {
        "status": "ok",
        "service": "novelsim",
        "contract_version": API_CONTRACT_VERSION,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready")
def api_ready():
    """只读检查本地权威存储和必要目录是否可用。"""
    report = _readiness_report()
    if report["status"] != "ok":
        return JSONResponse(report, status_code=503)
    return report


@app.get("/api/meta/contract")
def api_contract():
    return {
        "status": "ok",
        "contract_version": API_CONTRACT_VERSION,
        "stability": "stable",
        "compatibility": {
            "guarantee": (
                "1.x 内不删除既有路径、请求字段或响应字段；"
                "新增字段保持可忽略"
            ),
            "breaking_change": "破坏性变更必须提升主版本",
        },
        "authoritative_state": "SQLite WorldState + WorldEvent",
        "compiler_control_plane": "SQLite lease queue + external worker",
        "authentication": "Bearer token",
    }


@app.post("/api/auth/bootstrap")
def api_auth_bootstrap(req: AuthBootstrapRequest):
    """仅在空账户库中允许一次性创建首个管理员。"""

    try:
        user = AUTH.bootstrap_admin(req.username, req.password)
        token = AUTH.issue_token(user)
        AUTH.audit(
            user,
            action="auth.bootstrap",
            resource_type="user",
            resource_id=user.user_id,
        )
    except AuthConflict as exc:
        return JSONResponse(
            {"status": "conflict", "error": str(exc)},
            status_code=409,
        )
    except AuthError as exc:
        return JSONResponse(
            {"status": "invalid", "error": str(exc)},
            status_code=422,
        )
    return {"status": "ok", "token": token, "user": user.payload()}


@app.post("/api/auth/login")
def api_auth_login(req: AuthLoginRequest):
    try:
        user = AUTH.authenticate(req.username, req.password)
        token = AUTH.issue_token(user)
        AUTH.audit(
            user,
            action="auth.login",
            resource_type="user",
            resource_id=user.user_id,
        )
    except AuthenticationError as exc:
        return JSONResponse(
            {"status": "unauthorized", "error": str(exc)},
            status_code=401,
        )
    return {"status": "ok", "token": token, "user": user.payload()}


@app.get("/api/auth/me")
def api_auth_me():
    return {"status": "ok", "user": _actor().payload()}


@app.get("/api/admin/users")
def api_admin_users():
    return {
        "status": "ok",
        "users": [user.payload() for user in AUTH.list_users()],
    }


@app.post("/api/admin/users")
def api_admin_create_user(req: AuthUserCreateRequest):
    actor = _actor()
    try:
        user = AUTH.create_user(
            username=req.username,
            password=req.password,
            roles=req.roles,
        )
        AUTH.audit(
            actor,
            action="user.create",
            resource_type="user",
            resource_id=user.user_id,
            detail={"username": user.username, "roles": sorted(user.roles)},
        )
    except AuthConflict as exc:
        return JSONResponse(
            {"status": "conflict", "error": str(exc)},
            status_code=409,
        )
    except AuthError as exc:
        return JSONResponse(
            {"status": "invalid", "error": str(exc)},
            status_code=422,
        )
    return {"status": "ok", "user": user.payload()}


@app.patch("/api/admin/users/{user_id}")
def api_admin_update_user(
    user_id: str,
    req: AuthUserStatusRequest,
):
    actor = _actor()
    if actor.user_id == user_id and not req.active:
        return JSONResponse(
            {
                "status": "conflict",
                "error": "不能停用当前登录账户",
            },
            status_code=409,
        )
    try:
        user = AUTH.set_active(user_id, req.active)
        AUTH.audit(
            actor,
            action="user.activate" if req.active else "user.deactivate",
            resource_type="user",
            resource_id=user_id,
        )
    except AuthenticationError as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)},
            status_code=404,
        )
    return {"status": "ok", "user": user.payload()}


@app.get("/")
def index():
    """托管前端入口。static 不存在时返回开发提示。"""
    index_html = STATIC_DIR / "index.html"
    if index_html.exists():
        return FileResponse(index_html)
    return JSONResponse(
        {
            "error": "前端未构建",
            "hint": "请先 cd web/frontend && npm install && npm run build，"
                    "或开发模式运行 npm run dev (端口 5173)。",
        },
        status_code=503,
    )


@app.get("/api/worlds")
def api_world_catalog():
    """列出试玩端可启动的内置或已发布世界，不暴露完整状态快照。"""

    try:
        packages = [
            package
            for package in PACKAGES.list_packages()
            if package.source == "builtin"
            or package.review_status == "published"
        ]
    except WorldPackageError as exc:
        return JSONResponse(
            {"status": "error", "error": f"读取世界目录失败: {exc}"},
            status_code=500,
        )
    return {
        "status": "ok",
        "worlds": [package.summary() for package in packages],
    }


@app.get("/api/books")
def api_book_catalog():
    """列出已经导入的小说目录。"""
    try:
        return {
            "status": "ok",
            "books": [book.payload() for book in CHAPTER_CATALOG.list_books()],
        }
    except ChapterCatalogError as exc:
        return JSONResponse(
            {"status": "error", "error": f"读取小说目录失败: {exc}"},
            status_code=500,
        )


@app.get("/api/books/{book_id}/chapters")
def api_book_chapters(book_id: str, include_content: bool = False):
    """列出一本小说的章节入口；正文仅在明确请求时返回。"""
    try:
        entries = CHAPTER_CATALOG.list_entries(
            book_id,
            include_content=include_content,
        )
        return {
            "status": "ok",
            "book_id": book_id,
            "chapters": [entry.payload(include_content=include_content) for entry in entries],
        }
    except ChapterCatalogError as exc:
        return JSONResponse(
            {"status": "error", "error": f"读取章节目录失败: {exc}"},
            status_code=404,
        )


@app.post("/api/start")
def api_start(req: Optional[StartRequest] = None):
    """从章节入口或旧版世界包创建一条干净根世界线。"""

    request = req or StartRequest()
    try:
        if request.entry_id:
            entry = CHAPTER_CATALOG.require_published(request.entry_id)
            package = PACKAGES.get(entry.package_id)
            state = package.snapshot.copy(deep=True)
            sid = SESSIONS.create_session(
                state,
                default_actor_id=package.default_actor_id,
                world_package_id=package.package_id,
                save_name=(
                    request.save_name.strip()
                    or f"第{entry.chapter_number}章·{entry.title}世界线"
                ),
                book_id=entry.book_id,
                entry_id=entry.entry_id,
                chapter_number=entry.chapter_number,
                entry_revision=entry.revision,
            )
            return serialize_session(sid, resumed=False)

        package = PACKAGES.get(request.package_id)
        state = package.snapshot.copy(deep=True)
        sid = SESSIONS.create_session(
            state,
            default_actor_id=package.default_actor_id,
            world_package_id=package.package_id,
            save_name=(
                request.save_name.strip()
                or f"{package.scenario}世界线"
            ),
        )
        return serialize_session(sid, resumed=False)
    except (ChapterEntryNotFound, ChapterEntryNotPublished) as e:
        return JSONResponse(
            {"status": "error", "error": str(e)},
            status_code=404 if isinstance(e, ChapterEntryNotFound) else 409,
        )
    except WorldPackageNotFound as e:
        return JSONResponse(
            {"status": "error", "error": str(e)},
            status_code=404,
        )
    except WorldPackageError as e:
        return JSONResponse(
            {"status": "error", "error": str(e)},
            status_code=400,
        )
    except Exception as e:  # noqa: BLE001
        return JSONResponse(
            {"status": "error", "error": f"世界初始化失败: {e}"},
            status_code=500,
        )


@app.post("/api/scenes/secret-letter/runs")
async def api_run_secret_letter_scene(req: SecretLetterRunRequest):
    """在独立权威会话中运行一条可回放的原创玩家路线。

    玩家干预与后续 NPC 自主步骤全部经过 ToolRegistry、
    AgentExecutionStateMachine 和 SQLite 乐观锁提交。该入口不调用 LLM，
    适合 Unity/浏览器演示、确定性 E2E 和断线恢复。
    """

    routes = {
        "none": None,
        PLAYER_ROUTE_DESTROY: PLAYER_ROUTE_DESTROY,
        PLAYER_ROUTE_INTERCEPT: PLAYER_ROUTE_INTERCEPT,
        PLAYER_ROUTE_EXPOSE: PLAYER_ROUTE_EXPOSE,
    }
    route = req.route.strip().lower()
    if route not in routes:
        return JSONResponse(
            {
                "status": "invalid",
                "error": (
                    "route 仅支持 none、destroy_letter、"
                    "intercept_letter、expose_truth"
                ),
            },
            status_code=422,
        )
    try:
        mode = SceneMode(req.mode.strip().lower())
    except ValueError:
        return JSONResponse(
            {
                "status": "invalid",
                "error": "mode 仅支持 free 或 script",
            },
            status_code=422,
        )

    package = PACKAGES.get(SECRET_LETTER_PACKAGE_ID)
    state = package.snapshot.copy(deep=True)
    session_id = SESSIONS.create_session(
        state,
        default_actor_id=package.default_actor_id,
        world_package_id=package.package_id,
        save_name=(
            req.save_name.strip()
            or f"密信疑云·{route}世界线"
        ),
    )
    try:
        run = await run_secret_letter_scene(
            mode=mode,
            player_route=routes[route],
            initial_state=state,
            store=SESSIONS,
            session_id=session_id,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {
                "status": "error",
                "session_id": session_id,
                "error": (
                    "密信场景运行失败；已保留可诊断会话: "
                    f"{type(exc).__name__}: {exc}"
                ),
            },
            status_code=500,
        )

    memory_record_count = 0
    memory_warning = ""
    try:
        for outcome in run.outcomes:
            if outcome.event is None:
                continue
            memory_record_count += record_event_memory(
                SESSIONS,
                session_id,
                outcome.new_state,
                outcome.event,
                player_input=f"tool:{outcome.result.tool_name}",
            )
    except PersistenceError as exc:
        # 长期记忆是可重建投影；权威事件已经提交，不能伪装成回合失败。
        memory_warning = f"长期记忆投影待重建: {exc}"

    session_payload = serialize_session(session_id, resumed=False)
    return {
        "status": run.summary.status.value,
        "session_id": session_id,
        "default_actor": session_payload["default_actor"],
        "world_meta": session_payload["world_meta"],
        "save": session_payload["save"],
        "resumed": False,
        "world_package_id": package.package_id,
        "route": route,
        "mode": mode.value,
        "ending": run.ending,
        "objective_satisfied": run.summary.objective_satisfied,
        "state": state_to_dict(run.state),
        "summary": run.summary.dict(),
        "tool_results": [
            outcome.result.dict()
            for outcome in run.outcomes
        ],
        "trace_ids": [
            outcome.trace.trace_id
            for outcome in run.outcomes
        ],
        "memory_record_count": memory_record_count,
        "memory_warning": memory_warning,
        "presentation_cursor": cursor_after_world_version(
            run.state.version
        ),
    }


DEMO_CASES = {
    "invalid_airplane": {
        "title": "非法行动 · 世界规则拦截",
        "description": "验证不存在的现代交通工具不会被写入古代世界。",
        "player_input": "夜轻歌开飞机离开华容巷",
        "expected": "WORLD_CONCEPT_UNAVAILABLE，世界版本保持 v0",
    },
    "valid_intervention": {
        "title": "合法行动 · 权威状态提交",
        "description": "玩家取得并销毁密信，所有工具调用经校验后原子提交。",
        "player_input": "我抢先取得密信并将它销毁",
        "expected": "合法工具链提交，密信被销毁且世界版本递增",
        "route": PLAYER_ROUTE_DESTROY,
    },
    "multi_agent": {
        "title": "多 Agent · 信息传播与结盟",
        "description": "无玩家干预，守卫、管家和盟友依据有限认知自主协作。",
        "player_input": "观察 NPC 如何处理门房出现的密信",
        "expected": "证据逐跳传播，并在共同事实与信任阈值满足后结盟",
        "route": "none",
    },
}


def _demo_scene_turn(case_id: str, scene_payload: Dict[str, Any]) -> dict:
    """把确定性场景运行结果投影成 StoryFeed 可恢复的单回合。"""

    summary = scene_payload["summary"]
    state = scene_payload["state"]
    tool_sequence = list(summary.get("tool_sequence") or [])
    npc_reactions = list(dict.fromkeys(
        step.get("actor_id")
        for step in (summary.get("steps") or [])
        if step.get("actor_id")
        and step.get("actor_id") != scene_payload["default_actor"]
    ))
    hints = [
        f"确定性工具链: {' → '.join(tool_sequence) or '无提交动作'}",
        f"权威世界版本: v{summary.get('initial_version', 0)} → v{summary.get('final_version', 0)}",
        f"信息传播记录: {len(state.get('propagation_history') or [])} 条",
        f"已形成联盟: {len(state.get('alliances') or {})} 个",
    ]
    if summary.get("ending_reason"):
        hints.append(f"结束原因: {summary['ending_reason']}")
    ending_narration = {
        "letter_destroyed": "玩家成功取得并销毁密信，原定的信息传播链被中断。",
        "player_intercepted": "玩家截获密信并改变了守卫的原定行动路线。",
        "truth_exposed": "密信内容被公开，相关角色获得了可追溯的事实证据。",
        "defenders_allied": "守卫发现密信后逐跳传递证据，管家与盟友最终形成防卫联盟。",
    }.get(scene_payload["ending"], scene_payload["ending"])
    return {
        "status": "committed",
        "error": "",
        "rule_reason": "",
        "rejection_code": "",
        "rejection_message": "",
        "rejection_details": {},
        "action": {
            "type": "deterministic_showcase",
            "actor": scene_payload["default_actor"],
            "targets": [],
            "goal": DEMO_CASES[case_id]["expected"],
            "visibility": "overt",
        },
        "narrative": {
            "narration": ending_narration,
            "dialogues": [],
            "system_hints": hints,
        },
        "npc_reactions": npc_reactions if case_id == "multi_agent" else [],
    }


def _demo_metadata(case_id: str, payload: Dict[str, Any]) -> dict:
    config = DEMO_CASES[case_id]
    state = payload["state"]
    summary = payload.get("summary") or {}
    return {
        "case_id": case_id,
        "title": config["title"],
        "description": config["description"],
        "player_input": config["player_input"],
        "expected": config["expected"],
        "requires_api_key": False,
        "evidence": {
            "world_version": state.get("version", 0),
            "tool_calls": len(summary.get("tool_sequence") or []),
            "propagation_count": len(state.get("propagation_history") or []),
            "alliance_count": len(state.get("alliances") or {}),
            "objective_satisfied": summary.get("objective_satisfied"),
        },
    }


@app.post("/api/demo/runs")
async def api_run_demo(req: DemoRunRequest):
    """运行无需 API Key 的求职演示，并返回标准会话载荷。"""

    case_id = req.case_id.strip().lower()
    if case_id not in DEMO_CASES:
        return JSONResponse(
            {
                "status": "invalid",
                "error": f"未知演示案例: {case_id}",
                "available_cases": sorted(DEMO_CASES),
            },
            status_code=422,
        )

    if case_id == "invalid_airplane":
        started = api_start(
            StartRequest(
                package_id="huarong_lane",
                save_name="Demo·非法飞机规则拦截",
            )
        )
        if not isinstance(started, dict) or started.get("status") == "error":
            return started
        session_id = started["session_id"]
        turn = api_turn(
            TurnRequest(
                session_id=session_id,
                text=DEMO_CASES[case_id]["player_input"],
                use_npc_agents=False,
            )
        )
        if not isinstance(turn, dict) or turn.get("status") != "rejected":
            return JSONResponse(
                {
                    "status": "error",
                    "error": "非法行动演示未被规则引擎拒绝",
                    "session_id": session_id,
                },
                status_code=500,
            )
        payload = serialize_session(session_id, resumed=False)
        payload["demo"] = _demo_metadata(case_id, payload)
        return payload

    scene_payload = await api_run_secret_letter_scene(
        SecretLetterRunRequest(
            mode=SceneMode.free.value,
            route=DEMO_CASES[case_id]["route"],
            save_name=f"Demo·{DEMO_CASES[case_id]['title']}",
        )
    )
    if not isinstance(scene_payload, dict):
        return scene_payload
    if scene_payload.get("status") != "completed":
        return JSONResponse(scene_payload, status_code=500)

    turn_payload = _demo_scene_turn(case_id, scene_payload)
    try:
        SESSIONS.append_turn(
            scene_payload["session_id"],
            expected_version=scene_payload["state"]["version"],
            player_input=DEMO_CASES[case_id]["player_input"],
            turn_payload=turn_payload,
        )
    except (PersistenceError, SessionNotFound, VersionConflict) as exc:
        return JSONResponse(
            {
                "status": "error",
                "error": f"保存演示回合失败: {exc}",
                "session_id": scene_payload["session_id"],
            },
            status_code=500,
        )

    payload = serialize_session(scene_payload["session_id"], resumed=False)
    payload["demo"] = _demo_metadata(case_id, scene_payload)
    return payload


@app.get("/api/session")
def api_session(session: str = ""):
    """恢复已有会话，返回与 /api/start 同构的启动数据。"""
    if not session:
        return JSONResponse(
            {"status": "error", "error": "请提供 ?session=<id>"},
            status_code=400,
        )
    try:
        return serialize_session(session)
    except SessionNotFound:
        return JSONResponse(
            {"status": "error", "error": "会话不存在或已过期"},
            status_code=404,
        )
    except (PersistenceError, WorldPackageError) as e:
        return JSONResponse(
            {"status": "error", "error": f"读取会话失败: {e}"},
            status_code=500,
        )


@app.get("/api/player-view")
def api_player_view(session: str = ""):
    """把权威事件日志投影为玩家剧情，并提供独立的原著对照。"""

    if not session:
        return JSONResponse(
            {"status": "error", "error": "请提供 ?session=<id>"},
            status_code=400,
        )
    try:
        metadata = SESSIONS.get_metadata(session)
        state = SESSIONS.get_state(session)
        if metadata is None or state is None:
            raise SessionNotFound(f"会话不存在: {session}")
        package = PACKAGES.get(metadata.world_package_id)
        source_chapters = package.source_chapters
        manifest = package.manifest
        if metadata.entry_id:
            entry = CHAPTER_CATALOG.get_entry(
                metadata.entry_id,
                include_content=True,
            )
            if entry is not None:
                source_chapters = [
                    {
                        "index": entry.chapter_number,
                        "raw_number": entry.raw_number,
                        "title": entry.title,
                        "heading": entry.payload()["label"],
                        "content": entry.content,
                        "paragraphs": list(entry.paragraphs or []),
                    }
                ]
                manifest = {
                    **manifest,
                    "book_id": entry.book_id,
                    "entry_id": entry.entry_id,
                    "chapter_start": entry.chapter_start,
                    "chapter_end": entry.chapter_end,
                    "canonical_case_id": entry.canonical_case_id,
                    "evaluation_case_id": entry.canonical_case_id,
                }
        manuscript = SESSIONS.get_manuscript_for_session(session)
        list_campaign = getattr(
            SESSIONS,
            "list_campaign_manuscript_passages",
            SESSIONS.list_manuscript_passages,
        )
        passages = list_campaign(session)
        return build_player_view(
            project_root=PROJECT_ROOT,
            package_id=metadata.world_package_id,
            state=state,
            events=SESSIONS.list_events(session),
            source_chapters=source_chapters,
            manifest=manifest,
            manuscript=manuscript,
            passages=passages,
        )
    except SessionNotFound:
        return JSONResponse(
            {"status": "error", "error": "会话不存在或已过期"},
            status_code=404,
        )
    except (PersistenceError, WorldPackageError, OSError, ValueError) as exc:
        return JSONResponse(
            {"status": "error", "error": f"读取玩家剧情失败: {exc}"},
            status_code=500,
        )


def _player_entity_name(state: WorldState, entity_id: str) -> str:
    entity_id = str(entity_id or "")
    character = state.characters.get(entity_id)
    if character is not None:
        return character.display_name
    location = state.locations.get(entity_id)
    if location is not None:
        return location.display_name
    item = state.items.get(entity_id)
    if item is not None:
        return item.display_name
    return entity_id


def _player_mission(state: WorldState, package) -> Dict[str, Any]:
    mission_spec = dict(package.manifest.get("mission") or {})
    if mission_spec:
        progress_flags = [
            str(path) for path in mission_spec.get("progress_flags", [])
            if str(path).strip()
        ]
        completed = sum(bool(state.flags.get(path)) for path in progress_flags)
        progress = (
            int(round(completed * 100 / len(progress_flags)))
            if progress_flags
            else int(mission_spec.get("initial_progress") or 10)
        )
        return {
            "title": str(mission_spec.get("title") or "当前主线"),
            "description": str(
                mission_spec.get("description")
                or package.anchor
                or "在当前世界中找到属于你的道路。"
            ),
            "progress": progress,
        }

    active = [
        arc for arc in state.plot.values()
        if not arc.completed and getattr(arc, "stage", "active") == "active"
    ]
    if active:
        arc = active[0]
        title = arc.title or "当前主线"
        description = f"推进「{title}」，并在关键时刻夺回主动权。"
        progress = 100 if arc.completed else 20
    elif package.package_id == CANONICAL_CH1_PACKAGE_ID:
        title = "改写夜轻歌的命运"
        description = "在第 1 章冲突之后查清陷害真相，决定接下来的人生走向。"
        progress = 20
    else:
        title = "完成这一段世界线"
        description = package.anchor or "在当前世界中找到属于你的道路。"
        progress = 10
    if state.flags.get("canonical.returned_fengyue_pavilion"):
        progress = max(progress, 65)
    if state.flags.get("canonical.hall_summons_issued"):
        progress = max(progress, 80)
    return {"title": title, "description": description, "progress": progress}


def _player_suggestions(state: WorldState, default_actor_id: str) -> List[Dict[str, str]]:
    actor = state.characters.get(default_actor_id)
    if actor is None:
        return []
    suggestions: List[Dict[str, str]] = [
        {"label": "观察眼前的局势", "action": "我先观察周围的人和环境，寻找有用的线索。"},
        {"label": "询问在场的人", "action": "我询问在场的人，刚才到底发生了什么。"},
    ]
    present = [
        character for character in state.characters.values()
        if character.character_id != default_actor_id
        and character.is_alive
        and character.location_id == actor.location_id
    ]
    if present:
        name = present[0].display_name
        suggestions.insert(
            0,
            {"label": f"追问{name}", "action": f"我冷静地追问{name}，要求她说明刚才发生的事。"},
        )
    nearby_items = [
        item.display_name for item in state.items.values()
        if item.location_id == actor.location_id and item.accessible
    ]
    if nearby_items:
        suggestions.append(
            {"label": "检查身边的物品", "action": "我检查身边可以接触到的物品，寻找能够证明真相的线索。"},
        )
    suggestions.append(
        {"label": "暂时保持沉默", "action": "我暂时不表态，先观察每个人的反应。"},
    )
    return suggestions[:5]


def _player_relation_view(state: WorldState, default_actor_id: str) -> List[Dict[str, Any]]:
    result = []
    for relation in state.relations:
        if relation.source_id != default_actor_id and relation.target_id != default_actor_id:
            continue
        other_id = relation.target_id if relation.source_id == default_actor_id else relation.source_id
        other = state.characters.get(other_id)
        if other is None:
            continue
        dimensions = relation.dimensions
        trust = float(getattr(dimensions, "trust", 0.0))
        hostility = float(getattr(dimensions, "hostility", 0.0))
        if trust >= 0.45:
            trend = "较为信任"
        elif hostility >= 0.55 or trust <= -0.45:
            trend = "保持戒备"
        elif trust > 0:
            trend = "愿意观察"
        else:
            trend = "关系紧张"
        result.append({
            "character_id": other_id,
            "name": other.display_name,
            "public_relation": relation.public_relation,
            "trend": trend,
        })
    return result[:8]


def _player_memory_echoes(state: WorldState, events: List[Any]) -> List[Dict[str, str]]:
    echoes = []
    for event in reversed(events):
        actor_names = [
            _player_entity_name(state, item)
            for item in list(event.actor_ids) + list(event.target_ids)
            if item in state.characters
        ]
        if not actor_names:
            continue
        names = list(dict.fromkeys(actor_names))
        echoes.append({
            "id": event.event_id,
            "npc_name": names[0],
            "text": f"{names[0]}经历了这次事件，并会依据它重新判断你。",
            "source_event_id": event.event_id,
            "world_version": event.new_version,
        })
        if len(echoes) >= 3:
            break
    return echoes


def _build_player_dashboard(session_id: str) -> Dict[str, Any]:
    metadata = SESSIONS.get_metadata(session_id)
    state = SESSIONS.get_state(session_id)
    if metadata is None or state is None:
        raise SessionNotFound(f"会话不存在: {session_id}")
    package = PACKAGES.get(metadata.world_package_id)
    events = SESSIONS.list_events(session_id)
    turns = SESSIONS.list_turns(session_id)
    actor = state.characters.get(metadata.default_actor_id)
    mission = _player_mission(state, package)
    current_location = (
        state.locations.get(actor.location_id) if actor is not None else None
    )
    present = [
        {
            "character_id": character.character_id,
            "name": character.display_name,
            "identity": list(character.identity_tags[:3]),
            "is_player": character.character_id == metadata.default_actor_id,
        }
        for character in state.characters.values()
        if character.is_alive
        and actor is not None
        and character.location_id == actor.location_id
    ]
    latest = turns[-1].result if turns else None
    settlement_projection = _settlement_projection(session_id, state, metadata, package)
    canonical_changes = []
    if metadata.world_package_id == CANONICAL_CH1_PACKAGE_ID:
        player_events = [
            event for event in events
            if getattr(event.patch.causal_evidence, "authority", "")
            == "player_action_with_npc_reactions"
        ]
        canonical_changes = [
            {
                "status": "因你改变",
                "summary": event.summary or "你的行动改变了原本的剧情走向。",
                "world_version": event.new_version,
            }
            for event in player_events[-5:]
        ]
    return {
        "schema_version": "player_dashboard.v1",
        "session_id": session_id,
        "world_version": state.version,
        "world_time": state.world_time,
        "story_stage": state.flags.get("canonical.checkpoint_chapter")
        and f"第 {state.flags['canonical.checkpoint_chapter']} 章"
        or "当前剧情",
        "identity": actor.display_name if actor is not None else "命运介入者",
        "mission": mission["description"],
        "mission_title": mission["title"],
        "mission_progress": {"percent": mission["progress"]},
        "current_scene": {
            "id": state.current_scene_id or "",
            "name": current_location.display_name if current_location else "未知地点",
        },
        "present_characters": present,
        "relations": _player_relation_view(state, metadata.default_actor_id),
        "context_choices": _player_suggestions(state, metadata.default_actor_id),
        "suggested_actions": _player_suggestions(state, metadata.default_actor_id),
        "npc_memory_echoes": _player_memory_echoes(state, events),
        "recent_world_changes": [
            {
                "summary": event.summary or "世界线发生了一次变化。",
                "world_version": event.new_version,
            }
            for event in events[-3:]
        ],
        "canonical_changes": canonical_changes,
        "settlement": settlement_projection,
        "can_settle": settlement_projection["can_settle"],
        "latest_turn": latest,
        "save": serialize_save(metadata),
    }


@app.get("/api/world-runs/{session_id}/dashboard")
def api_world_run_dashboard(session_id: str):
    """返回普通玩家可见的世界线聚合视图，不暴露完整 Agent 内在状态。"""
    try:
        return {"status": "ok", "dashboard": _build_player_dashboard(session_id)}
    except SessionNotFound:
        return JSONResponse(
            {"status": "error", "error": "会话不存在或已过期"},
            status_code=404,
        )
    except (PersistenceError, WorldPackageError, ValueError) as exc:
        return JSONResponse(
            {"status": "error", "error": f"读取世界线面板失败: {exc}"},
            status_code=500,
        )


def _settlement_response(session_id: str, state: WorldState, metadata, package, *, event_id: str = "") -> dict:
    settlement = _settlement_projection(session_id, state, metadata, package)
    return {
        "status": settlement["status"],
        "session_id": session_id,
        "ending": {
            "ending_id": settlement["ending_id"],
            "title": settlement["ending_title"],
            "summary": settlement["summary"],
            "objective_satisfied": settlement["objective_satisfied"],
        },
        "settlement": settlement,
        "reward": settlement.get("reward") or settlement.get("reward_preview"),
        "event_id": event_id,
        "state_version": state.version,
        "world_version": state.version,
        "dashboard": _build_player_dashboard(session_id),
    }


@app.get("/api/world-runs/{session_id}/settlement")
def api_world_run_settlement(session_id: str):
    """Return a deterministic, player-safe settlement preview or history."""
    try:
        metadata = SESSIONS.get_metadata(session_id)
        state = SESSIONS.get_state(session_id)
        if metadata is None or state is None:
            raise SessionNotFound(f"会话不存在: {session_id}")
        package = PACKAGES.get(metadata.world_package_id)
        return _settlement_response(session_id, state, metadata, package)
    except SessionNotFound:
        return JSONResponse({"status": "error", "error": "会话不存在或已过期"}, status_code=404)
    except (PersistenceError, WorldPackageError, ValueError) as exc:
        return JSONResponse({"status": "error", "error": f"读取结算失败: {exc}"}, status_code=500)


def _legacy_settlement_event(events: List[Any]) -> Optional[Any]:
    for event in reversed(events):
        if event.event_type == "settlement.claimed":
            return event
    return None


def _backfill_settlement_progression(
    session_id: str,
    state: WorldState,
    metadata,
    package,
) -> None:
    """Rebuild authoritative progression rows for pre-existing saves.

    Older settles only wrote state flags/events. This projects the claimed
    settlement into campaign/receipt/reward/unlock rows once, keyed by the
    same deterministic idempotency scheme as fresh settles.
    """

    record_progression = getattr(
        SESSIONS, "record_settlement_progression", None
    )
    if not callable(record_progression):
        return
    lineage = _session_lineage(session_id)
    unlock_key = (
        f"world:{_chapter_next_spec(package).get('package_id')}"
        if _chapter_next_spec(package)
        else ""
    )
    try:
        campaign_unlocks = (
            _campaign_unlock_keys(lineage.campaign_id)
            if lineage is not None
            else set()
        )
    except Exception:  # noqa: BLE001 - compatibility probe
        campaign_unlocks = set()
    if lineage is not None and unlock_key and unlock_key in campaign_unlocks:
        return
    events = SESSIONS.list_events(session_id)
    event = _legacy_settlement_event(events)
    if event is None:
        return
    next_spec = _chapter_next_spec(package)
    unlocks = []
    if next_spec:
        unlocks.append(
            UnlockGrant(
                unlock_key=f"world:{next_spec['package_id']}",
                unlock_type="world",
                payload={
                    "title": str(next_spec.get("title") or ""),
                    "chapter_start": next_spec.get("chapter_start"),
                    "chapter_end": next_spec.get("chapter_end"),
                },
            )
        )
    record_progression(
        session_id,
        settlement_event_id=event.event_id,
        settled_world_version=int(event.new_version),
        ending_id=str(state.flags.get("settlement.ending_id") or package.package_id),
        ending_title=str(state.flags.get("settlement.title") or "世界线结局"),
        summary=str(state.flags.get("settlement.summary") or ""),
        reward_points=int(state.flags.get("settlement.reward_points") or 0),
        idempotency_key=(
            f"settlement:{session_id}:"
            f"{state.flags.get('settlement.ending_id') or package.package_id}"
        ),
        unlocks=unlocks,
    )


def _settle_world_run(session_id: str, req: SettlementRequest):
    try:
        metadata = SESSIONS.get_metadata(session_id)
        state = SESSIONS.get_state(session_id)
        if metadata is None or state is None:
            raise SessionNotFound(f"会话不存在: {session_id}")
        package = PACKAGES.get(metadata.world_package_id)
        existing = _settlement_projection(session_id, state, metadata, package)
        if existing["status"] == "settled":
            try:
                _backfill_settlement_progression(
                    session_id, state, metadata, package
                )
            except PersistenceError:
                pass
            return _settlement_response(session_id, state, metadata, package)
        events = SESSIONS.list_events(session_id)
        evaluation = _settlement_evaluation(state, package, events)
        if req.ending_id and req.ending_id != evaluation["ending_id"]:
            return JSONResponse(
                {"status": "invalid", "error": "ending_id 与权威结局不匹配"},
                status_code=422,
            )
        if not evaluation["can_settle"]:
            return JSONResponse(
                {
                    "status": "unavailable",
                    "error": evaluation["reason"],
                    "settlement": _settlement_projection(session_id, state, metadata, package),
                    "state_version": state.version,
                },
                status_code=409,
            )
        if req.expected_version is not None and req.expected_version != state.version:
            return JSONResponse(
                {
                    "status": "conflict",
                    "error": f"世界版本冲突: expected {req.expected_version}, got {state.version}",
                    "state_version": state.version,
                },
                status_code=409,
            )
        reward_points = _settlement_reward(
            state, package, evaluation, events
        )
        settled_at = datetime.now(timezone.utc).isoformat()
        patch = StatePatch(
            operations=[
                Operation(op=OperationKind.set_flag, path="settlement.status", value="settled"),
                Operation(op=OperationKind.set_flag, path="settlement.ending_id", value=evaluation["ending_id"]),
                Operation(op=OperationKind.set_flag, path="settlement.title", value=evaluation["title"]),
                Operation(op=OperationKind.set_flag, path="settlement.summary", value=evaluation["summary"]),
                Operation(op=OperationKind.set_flag, path="settlement.settled_at", value=settled_at),
                Operation(op=OperationKind.set_flag, path="settlement.reward_points", value=reward_points),
                Operation(op=OperationKind.set_flag, path="settlement.reward_claimed", value=True),
            ],
            notes="后端确定性结算；奖励由权威状态计算",
        )
        event, new_state = commit_event(
            state,
            action_id="settlement_claim",
            event_type="settlement.claimed",
            patch=patch,
            actor_ids=[metadata.default_actor_id],
            expected_version=state.version,
            event_id=f"settlement_{uuid4().hex}",
            summary=evaluation["summary"],
        )
        payload = {
            "status": "settled",
            "ending": evaluation,
            "settlement": {
                "status": "settled",
                "ending_id": evaluation["ending_id"],
                "title": evaluation["title"],
                "summary": evaluation["summary"],
                "objective_satisfied": True,
                "reward_points": reward_points,
                "reward_claimed": True,
                "settled_at": settled_at,
            },
        }
        SESSIONS.commit_turn(
            session_id,
            expected_version=state.version,
            new_state=new_state,
            event=event,
            player_input="完成结算",
            turn_payload=payload,
        )
        record_progression = getattr(
            SESSIONS, "record_settlement_progression", None
        )
        if callable(record_progression):
            next_spec = _chapter_next_spec(package)
            unlocks = []
            if next_spec and evaluation["objective_satisfied"]:
                unlocks.append(
                    UnlockGrant(
                        unlock_key=f"world:{next_spec['package_id']}",
                        unlock_type="world",
                        payload={
                            "title": str(next_spec.get("title") or ""),
                            "chapter_start": next_spec.get("chapter_start"),
                            "chapter_end": next_spec.get("chapter_end"),
                        },
                    )
                )
            record_progression(
                session_id,
                settlement_event_id=event.event_id,
                settled_world_version=new_state.version,
                ending_id=str(evaluation["ending_id"]),
                ending_title=evaluation["title"],
                summary=evaluation["summary"],
                reward_points=int(reward_points),
                idempotency_key=(
                    f"settlement:{session_id}:{evaluation['ending_id']}"
                ),
                unlocks=unlocks,
            )
        latest = SESSIONS.get_state(session_id)
        return _settlement_response(session_id, latest, metadata, package, event_id=event.event_id)
    except VersionConflict as exc:
        latest = SESSIONS.get_state(session_id)
        return JSONResponse(
            {"status": "conflict", "error": str(exc), "state_version": latest.version if latest else None},
            status_code=409,
        )
    except SessionNotFound:
        return JSONResponse({"status": "error", "error": "会话不存在或已过期"}, status_code=404)
    except (PersistenceError, WorldPackageError, ValueError) as exc:
        return JSONResponse({"status": "error", "error": f"结算失败: {exc}"}, status_code=500)


@app.post("/api/world-runs/{session_id}/settle")
def api_settle_world_run(session_id: str, req: SettlementRequest):
    return _settle_world_run(session_id, req)


@app.post("/api/world-runs/{session_id}/settlement")
def api_settlement_alias(session_id: str, req: SettlementRequest):
    return _settle_world_run(session_id, req)


@app.get("/api/world-runs/{session_id}/settlements")
def api_world_run_settlements(session_id: str):
    return api_world_run_settlement(session_id)


@app.post("/api/world-runs/{session_id}/settlements")
def api_world_run_settlements_post(session_id: str, req: SettlementRequest):
    return _settle_world_run(session_id, req)


def _transition_state_or_error(parent_session_id: str):
    metadata = SESSIONS.get_metadata(parent_session_id)
    state = SESSIONS.get_state(parent_session_id)
    if metadata is None or state is None:
        raise SessionNotFound(f"会话不存在: {parent_session_id}")
    package = PACKAGES.get(metadata.world_package_id)
    return metadata, state, package


@app.post("/api/world-runs/{session_id}/transitions")
def api_create_chapter_transition(
    session_id: str,
    req: ChapterTransitionRequest,
):
    """Idempotently unlock and create the next chapter's child worldline."""

    try:
        metadata, parent_state, parent_package = _transition_state_or_error(
            session_id
        )
        projection = _settlement_projection(
            session_id, parent_state, metadata, parent_package
        )
        if projection["status"] != "settled":
            status = (
                "settlement_required"
                if projection["status"] == "available"
                else "invalid"
            )
            return JSONResponse(
                {
                    "status": status,
                    "error": (
                        "当前世界线尚未完成结算，不能进入下一章。"
                        if status == "settlement_required"
                        else "当前世界线还不满足进入下一章的条件。"
                    ),
                    "settlement": projection,
                },
                status_code=409 if status == "settlement_required" else 422,
            )
        target_id = req.target_package_id.strip()
        next_spec = _chapter_next_spec(parent_package)
        if not next_spec:
            return JSONResponse(
                {"status": "unavailable", "error": "本世界包未声明后续章节。"},
                status_code=422,
            )
        target_entry = None
        if req.target_entry_id:
            try:
                target_entry = CHAPTER_CATALOG.require_published(req.target_entry_id)
            except (ChapterEntryNotFound, ChapterEntryNotPublished) as exc:
                return JSONResponse(
                    {"status": "unavailable", "error": str(exc)},
                    status_code=404 if isinstance(exc, ChapterEntryNotFound) else 409,
                )
            if target_entry.package_id != target_id:
                return JSONResponse(
                    {
                        "status": "invalid",
                        "error": "目标章节入口没有绑定请求的世界包。",
                    },
                    status_code=422,
                )
        if target_id != str(next_spec.get("package_id")):
            return JSONResponse(
                {
                    "status": "invalid",
                    "error": "目标世界包不是本章节声明的下一章。",
                    "expected_package_id": str(next_spec.get("package_id")),
                },
                status_code=422,
            )
        try:
            child_package = PACKAGES.get(target_id)
        except WorldPackageNotFound:
            return JSONResponse(
                {
                    "status": "unavailable",
                    "error": "下一章世界包尚未发布。",
                },
                status_code=409,
            )
        lineage = _session_lineage(session_id)
        unlock_key = f"world:{target_id}"
        durable_unlocked = bool(
            lineage is not None
            and unlock_key in _campaign_unlock_keys(lineage.campaign_id)
        )
        if not durable_unlocked:
            return JSONResponse(
                {
                    "status": "locked",
                    "error": "结算回执尚未确认下一章解锁。",
                    "next_chapter": projection["next_chapter"],
                },
                status_code=409,
            )

        plan = _inheritance_plan(
            parent_state, parent_package, child_package
        )
        child_state = child_package.snapshot.copy(deep=True)
        child_state.timeline_id = f"timeline_{uuid4().hex[:16]}"
        inherited_labels: List[str] = []
        for operation in plan["operations"]:
            if operation.op == OperationKind.set_flag:
                inherited_labels.append(operation.path)
            elif operation.item_id:
                inherited_labels.append(f"物品 {operation.item_id}")
        genesis_event, child_state = commit_event(
            child_state,
            action_id="chapter_inheritance",
            event_type="chapter.inherited",
            patch=StatePatch(operations=list(plan["operations"])),
            actor_ids=[child_package.default_actor_id],
            expected_version=child_state.version,
            summary=(
                "从上一章世界线继承持久事实：" + "、".join(inherited_labels)
                if inherited_labels
                else "开启下一章世界线；上一章没有需要继承的持久事实。"
            ),
        )
        manifest_payload = {
            "policy_version": plan["policy_version"],
            "entries": plan["entries"],
            "inherited_flag_paths": plan["inherited_flag_paths"],
            "parent_world_package_id": metadata.world_package_id,
            "parent_settlement_ending_id": parent_state.flags.get(
                "settlement.ending_id"
            ),
            "parent_settlement_reward_points": parent_state.flags.get(
                "settlement.reward_points"
            ),
        }
        request = TransitionRequest(
            parent_session_id=session_id,
            target_world_package_id=target_id,
            child_state=child_state,
            target_book_id=(target_entry.book_id if target_entry else metadata.book_id),
            target_entry_id=(target_entry.entry_id if target_entry else ""),
            target_chapter_number=(target_entry.chapter_number if target_entry else 0),
            target_entry_revision=(target_entry.revision if target_entry else 0),
            genesis_event=genesis_event,
            manifest=manifest_payload,
            default_actor_id=child_package.default_actor_id,
            idempotency_key=req.idempotency_key.strip(),
            save_name=req.save_name.strip() or f"{child_package.scenario}世界线",
        )
        result = SESSIONS.create_or_get_child_session(request)
        reused = not result.created
        return {
            "status": "ok",
            "transition": {
                "created": result.created,
                "reused": reused,
                "child_session_id": result.child_session_id,
                "target_package_id": result.target_world_package_id,
                "parent_session_id": result.parent_session_id,
                "campaign_id": result.lineage.campaign_id,
                "depth": result.lineage.depth,
            },
            "inheritance_summary": plan["summary"],
            "inheritance_entries": plan["entries"],
            "dashboard": _build_player_dashboard(result.child_session_id),
            "parent_settlement": projection,
        }
    except SessionNotFound as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=404)
    except PersistenceError as exc:
        message = str(exc)
        if "幂等键已用于其他父会话" in message or "其他" in message:
            return JSONResponse(
                {"status": "conflict", "error": message}, status_code=409
            )
        return JSONResponse(
            {"status": "error", "error": f"创建下一章世界线失败: {exc}"},
            status_code=500,
        )
    except (WorldPackageError, ValueError) as exc:
        return JSONResponse(
            {"status": "invalid", "error": str(exc)}, status_code=422
        )


@app.get("/api/world-runs/{session_id}/lineage")
def api_world_run_lineage(session_id: str):
    """Return the campaign lineage chain around one world run."""

    try:
        lineage = _session_lineage(session_id)
        if lineage is None:
            raise SessionNotFound(f"会话不存在: {session_id}")
        listing = getattr(SESSIONS, "list_campaign_progression", None)
        progression = listing(lineage.campaign_id) if callable(listing) else None
        chain = []
        settlements_by_session = {}
        rewards_total = 0
        if progression is not None:
            for receipt in progression.settlements:
                settlements_by_session[receipt.session_id] = receipt
            rewards_total = sum(
                item.points_delta for item in progression.rewards
            )
            for member in progression.lineage:
                receipt = settlements_by_session.get(member.session_id)
                chain.append(
                    {
                        "session_id": member.session_id,
                        "depth": member.depth,
                        "parent_session_id": member.parent_session_id,
                        "root_session_id": member.root_session_id,
                        "world_package_id": member.target_world_package_id,
                        "settled": receipt is not None,
                        "ending_title": receipt.ending_title if receipt else "",
                        "reward_points": receipt.reward_points if receipt else 0,
                    }
                )
        return {
            "status": "ok",
            "campaign_id": lineage.campaign_id,
            "session_id": session_id,
            "root_session_id": lineage.root_session_id,
            "depth": lineage.depth,
            "reward_points_total": rewards_total,
            "chain": sorted(chain, key=lambda item: (item["depth"],)),
        }
    except SessionNotFound as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=404)
    except PersistenceError as exc:
        return JSONResponse(
            {"status": "error", "error": f"读取世界线谱系失败: {exc}"},
            status_code=500,
        )


@app.get("/api/campaigns/{campaign_id}/progression")
def api_campaign_progression(campaign_id: str):
    listing = getattr(SESSIONS, "list_campaign_progression", None)
    if not callable(listing):
        return JSONResponse(
            {"status": "unsupported", "error": "当前存储后端不支持章节旅程"},
            status_code=501,
        )
    try:
        progression = listing(campaign_id)
        saves = {
            item.session_id: serialize_save(item)
            for item in SESSIONS.list_sessions()
        }
        chain = [
            {
                **saves.get(member.session_id, {}),
                "session_id": member.session_id,
                "depth": member.depth,
                "parent_session_id": member.parent_session_id,
                "world_package_id": member.target_world_package_id,
            }
            for member in progression.lineage
        ]
        return {
            "status": "ok",
            "campaign_id": campaign_id,
            "chain": sorted(chain, key=lambda item: item["depth"]),
            "total_reward_points": sum(
                item.points_delta for item in progression.rewards
            ),
            "transitions": [
                {
                    "parent_session_id": item.parent_session_id,
                    "child_session_id": item.child_session_id,
                    "target_package_id": item.target_world_package_id,
                    "created_at": item.created_at,
                }
                for item in progression.transitions
            ],
        }
    except PersistenceError as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)}, status_code=404
        )


@app.post("/api/turn")
def api_turn(req: TurnRequest):
    """跑一回合。返回序列化 TurnResult。

    任何异常 (无 key / 网络 / LLM) 都包装成 status="error"，
    绝不让 worker 崩溃。
    """
    try:
        state = SESSIONS.get_state(req.session_id)
        metadata = SESSIONS.get_metadata(req.session_id)
    except PersistenceError as e:
        return JSONResponse(
            {"status": "error", "error": f"读取会话失败: {e}"},
            status_code=500,
        )
    if state is None or metadata is None:
        return JSONResponse(
            {"status": "error", "error": "会话不存在或已过期，请重新开局"},
            status_code=404,
        )

    try:
        package = PACKAGES.get(metadata.world_package_id)
        projection = _settlement_projection(
            req.session_id, state, metadata, package
        )
        if projection["status"] == "settled":
            return JSONResponse(
                {
                    "status": "settled",
                    "error": "当前世界线已完成结算，不能继续本章节行动。",
                    "settlement": projection,
                },
                status_code=409,
            )
        if projection["status"] == "available":
            return JSONResponse(
                {
                    "status": "settlement_required",
                    "error": "当前世界线已抵达终点，请先完成结算。",
                    "settlement": projection,
                },
                status_code=409,
            )
    except (WorldPackageError, ValueError):
        pass

    text = (req.text or "").strip()
    if not text:
        return JSONResponse(
            {"status": "error", "error": "请输入你想做的事"},
            status_code=400,
        )

    npc_memory_context: Dict[str, list] = {}
    memory_warning = ""
    if req.use_npc_agents:
        try:
            npc_memory_context = retrieve_npc_memories(
                req.session_id,
                state,
                text,
            )
        except PersistenceError as e:
            # 记忆是可重建投影；检索异常时降级为无长期记忆，不能阻塞回合。
            memory_warning = f"长期记忆检索已降级: {e}"

    try:
        result = PIPELINE.run(
            text,
            state,
            metadata.default_actor_id,
            use_llm_proposer=True,
            use_narrative=True,
            use_npc_agents=req.use_npc_agents,
            npc_memory_context=npc_memory_context,
        )
    except RuntimeError as e:
        # 通常是 LLM_API_KEY 未设置
        return {
            "status": "error",
            "error": f"LLM 调用失败: {e}",
        }
    except Exception as e:  # noqa: BLE001  网络/超时/解析等
        return {
            "status": "error",
            "error": f"回合执行异常: {type(e).__name__}: {e}",
        }

    response_payload = serialize_turn(result)
    # A normal turn response always carries the authoritative world snapshot.
    # Rejected/failed turns intentionally have no ``new_state`` because they
    # commit no event, but clients must keep the current persisted version
    # rather than interpreting JSON ``null`` as an empty state object.
    if response_payload.get("state") is None:
        response_payload["state"] = state_to_dict(state)

    committed_state = result.new_state or state
    response_payload["action_interpretation"] = (
        result.action.declared_goal
        if result.action is not None and result.action.declared_goal
        else text
    )
    response_payload["outcome"] = {
        "status": result.status,
        "label": {
            "committed": "行动已发生",
            "rejected": "行动未能发生",
            "parse_failed": "没有理解这次行动",
            "narrate_failed": "行动已发生，叙事稍后补全",
        }.get(result.status, "本轮已处理"),
        "message": (
            result.error
            or response_payload.get("rejection_message")
            or "你的选择已经写入这条世界线。"
        ),
    }
    response_payload["world_progress"] = {
        "from_version": state.version,
        "to_version": committed_state.version,
        "world_time": committed_state.world_time,
        "advanced": committed_state.version != state.version,
    }
    response_payload["mission_changes"] = []
    response_payload["relation_changes"] = []
    response_payload["memory_echoes"] = []
    response_payload["canonical_changes"] = []
    history_payload = dict(response_payload)
    history_payload.pop("state", None)

    # 已产生世界事件 -> 原子保存新状态、事件日志与剧情记录。
    # narrate_failed 也要保存，因为状态提交已经是正式发生的事实。
    try:
        if result.new_state is not None and result.event is not None:
            SESSIONS.commit_turn(
                req.session_id,
                expected_version=state.version,
                new_state=result.new_state,
                event=result.event,
                player_input=text,
                turn_payload=history_payload,
            )
        else:
            SESSIONS.append_turn(
                req.session_id,
                expected_version=state.version,
                player_input=text,
                turn_payload=history_payload,
            )
    except VersionConflict as e:
        latest = SESSIONS.get_state(req.session_id)
        return JSONResponse(
            {
                "status": "conflict",
                "error": str(e),
                "state": state_to_dict(latest) if latest else None,
            },
            status_code=409,
        )
    except PersistenceError as e:
        return JSONResponse(
            {"status": "error", "error": f"保存回合失败: {e}"},
            status_code=500,
        )

    if result.new_state is not None and result.event is not None:
        response_payload["manuscript"] = _persist_manuscript_batch(
            req.session_id,
            [result.event],
            result.new_state,
            narrative=result.narrative,
        )
        try:
            persist_turn_memories(req.session_id, text, result)
        except PersistenceError as e:
            # 权威回合已经提交，派生索引失败不能把成功伪装成失败。
            memory_warning = f"长期记忆沉淀待重建: {e}"
    if memory_warning:
        response_payload["memory_warning"] = memory_warning

    try:
        dashboard = _build_player_dashboard(req.session_id)
        response_payload["mission_changes"] = [{
            "title": dashboard["mission_title"],
            "progress": dashboard["mission_progress"],
        }]
        response_payload["relation_changes"] = dashboard["relations"]
        response_payload["memory_echoes"] = dashboard["npc_memory_echoes"]
        response_payload["canonical_changes"] = dashboard["canonical_changes"]
        response_payload["suggested_actions"] = dashboard["suggested_actions"]
    except (PersistenceError, WorldPackageError, SessionNotFound):
        # The authoritative turn is already committed; the player projection
        # can be rebuilt on the next dashboard request.
        pass

    return response_payload


@app.get("/api/saves")
def api_saves():
    """列出全部世界线存档。"""
    try:
        saves = SESSIONS.list_sessions()
    except PersistenceError as e:
        return JSONResponse(
            {"status": "error", "error": f"读取存档失败: {e}"},
            status_code=500,
        )
    return {"status": "ok", "saves": [serialize_save(item) for item in saves]}


@app.patch("/api/saves/{session_id}")
def api_rename_save(session_id: str, req: RenameSaveRequest):
    """修改世界线存档名。"""
    try:
        SESSIONS.rename_session(session_id, req.name)
        metadata = SESSIONS.get_metadata(session_id)
    except SessionNotFound as e:
        return JSONResponse(
            {"status": "error", "error": str(e)},
            status_code=404,
        )
    except PersistenceError as e:
        return JSONResponse(
            {"status": "error", "error": str(e)},
            status_code=400,
        )
    return {"status": "ok", "save": serialize_save(metadata)}


@app.get("/api/saves/{session_id}/export")
def api_export_save(session_id: str):
    """下载包含状态、事件日志和剧情历史的完整 JSON 备份。"""

    try:
        backup = SESSIONS.export_session(session_id)
    except SessionNotFound as e:
        return JSONResponse(
            {"status": "error", "error": str(e)},
            status_code=404,
        )
    except PersistenceError as e:
        return JSONResponse(
            {"status": "error", "error": f"导出存档失败: {e}"},
            status_code=500,
        )
    return JSONResponse(
        backup,
        headers={
            "Content-Disposition": (
                f'attachment; filename="world-save-{session_id}.json"'
            )
        },
    )


@app.post("/api/saves/import")
def api_import_save(req: ImportSaveRequest):
    """校验并导入完整 JSON 备份；导入后直接载入新世界线。"""

    try:
        world_package_id = req.backup.get("save", {}).get("world_package_id")
        if not PACKAGES.exists(str(world_package_id or "")):
            raise PersistenceError(f"当前不支持世界包: {world_package_id}")
        session_id = SESSIONS.import_session(req.backup)
        return serialize_session(session_id)
    except PersistenceError as e:
        return JSONResponse(
            {"status": "error", "error": f"导入存档失败: {e}"},
            status_code=400,
        )


@app.delete("/api/saves/{session_id}")
def api_delete_save(session_id: str):
    """删除世界线及其事件、剧情历史。"""
    try:
        deleted = SESSIONS.delete_session(session_id)
    except PersistenceError as e:
        return JSONResponse(
            {"status": "error", "error": f"删除存档失败: {e}"},
            status_code=500,
        )
    if not deleted:
        return JSONResponse(
            {"status": "error", "error": "存档不存在"},
            status_code=404,
        )
    return {"status": "ok", "deleted": session_id}


CLEAR_HISTORY_CONFIRMATION = "清空历史世界线"


@app.post("/api/saves/clear-history")
def api_clear_history(req: ClearHistoryRequest):
    """清理历史世界线，默认保留当前会话；不触碰世界包或编译数据。"""
    permission = _permission_error("creator.write", resource_type="world_sessions")
    if permission:
        return permission

    preserve_id = (req.preserve_session_id or "").strip()
    try:
        candidates = SESSIONS.list_sessions()
    except PersistenceError as exc:
        AUTH.audit(
            _actor(),
            action="saves.clear_history",
            resource_type="world_sessions",
            outcome="failed",
            detail={"preserve_session_id": preserve_id, "error": str(exc)},
        )
        return JSONResponse(
            {"status": "error", "error": f"读取存档失败: {exc}"},
            status_code=500,
        )

    candidate_ids = [
        str(item.session_id)
        for item in candidates
        if str(item.session_id) != preserve_id
    ]
    if req.confirmation != CLEAR_HISTORY_CONFIRMATION:
        AUTH.audit(
            _actor(),
            action="saves.clear_history",
            resource_type="world_sessions",
            outcome="denied",
            detail={
                "preserve_session_id": preserve_id,
                "candidate_count": len(candidate_ids),
                "deleted_count": 0,
                "reason": "confirmation_mismatch",
            },
        )
        return JSONResponse(
            {
                "status": "error",
                "error": f"请输入确认短语：{CLEAR_HISTORY_CONFIRMATION}",
            },
            status_code=422,
        )

    deleted_ids: List[str] = []
    failures: List[Dict[str, str]] = []
    for session_id in candidate_ids:
        try:
            if SESSIONS.delete_session(session_id):
                deleted_ids.append(session_id)
        except PersistenceError as exc:
            failures.append({"session_id": session_id, "error": str(exc)})

    preserved = ""
    if preserve_id:
        try:
            if any(str(item.session_id) == preserve_id for item in SESSIONS.list_sessions()):
                preserved = preserve_id
        except PersistenceError:
            preserved = preserve_id
    result_status = "partial" if failures else "ok"
    payload = {
        "status": result_status,
        "candidate_count": len(candidate_ids),
        "deleted_count": len(deleted_ids),
        "deleted_session_ids": deleted_ids,
        "preserved_session_id": preserved,
        "failed_count": len(failures),
        "failures": failures,
        "scope": "world_sessions_and_cascaded_session_data",
        "excluded": ["world_packages", "chapter_catalog", "compiler_data"],
    }
    AUTH.audit(
        _actor(),
        action="saves.clear_history",
        resource_type="world_sessions",
        outcome=result_status,
        detail={
            "preserve_session_id": preserved,
            "candidate_count": len(candidate_ids),
            "deleted_count": len(deleted_ids),
            "failed_count": len(failures),
        },
    )
    return JSONResponse(payload, status_code=207 if failures else 200)


# ---------------------------------------------------------------------------
# 创作者后台 API
# ---------------------------------------------------------------------------


@app.post("/api/creator/compiler/jobs")
def api_create_compilation_job(req: CompilationJobRequest):
    """创建全书/选章编译任务，由独立 Worker 从 SQLite 队列领取。"""

    denied = _permission_error("compiler.manage", resource_type="compiler_job")
    if denied:
        return denied
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", req.package_id):
        return JSONResponse(
            {
                "status": "invalid",
                "error": "package_id 格式无效",
            },
            status_code=422,
        )
    try:
        novel_path = _resolve_novel_path(req.novel_path)
    except ValueError as exc:
        return JSONResponse(
            {"status": "invalid", "error": str(exc)},
            status_code=422,
        )
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", req.book_id.strip()):
        return JSONResponse(
            {
                "status": "invalid",
                "error": "book_id 格式无效且不能为空",
            },
            status_code=422,
        )
    if req.expected_source_hash:
        expected_hash = req.expected_source_hash.strip().lower()
        actual_hash = hashlib.sha256(novel_path.read_bytes()).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            return JSONResponse(
                {
                    "status": "invalid",
                    "error": "expected_source_hash 格式无效",
                },
                status_code=422,
            )
        if expected_hash != actual_hash:
            return JSONResponse(
                {
                    "status": "conflict",
                    "error": "小说源文件指纹与请求不一致",
                },
                status_code=409,
            )
    chapters = sorted(
        {
            int(chapter)
            for chapter in req.chapters
            if int(chapter) > 0
        }
    )
    job = COMPILATION_JOBS.create_job(
        package_id=req.package_id,
        novel_path=str(novel_path),
        book_id=req.book_id,
        benchmark_id=req.benchmark_id,
        novel_name=req.novel_name.strip(),
        chapters=chapters,
        timeline_plan=req.timeline_plan,
        volume_plan=req.volume_plan,
        volume_size=max(1, req.volume_size),
        prompt_version=EXTRACTOR_PROMPT_VERSION,
        model=req.model.strip(),
        max_llm_calls=max(0, req.max_llm_calls),
    )
    if not req.auto_start:
        job = COMPILATION_JOBS.request_pause(job.job_id)
    AUTH.audit(
        _actor(),
        action="compiler_job.create",
        resource_type="compiler_job",
        resource_id=job.job_id,
        detail={
            "package_id": job.package_id,
            "novel_path": novel_path.name,
            "auto_start": req.auto_start,
            "max_llm_calls": job.max_llm_calls,
        },
    )
    return {
        "status": "ok",
        "job": job.payload(),
    }


@app.get("/api/creator/compiler/jobs")
def api_list_compilation_jobs(limit: int = 100):
    """返回最近编译任务和稳定进度。"""

    return {
        "status": "ok",
        "jobs": [
            job.payload(include_plan=False)
            for job in COMPILATION_JOBS.list_jobs(limit=limit)
        ],
    }


@app.get("/api/creator/compiler/jobs/{job_id}/report")
def api_compilation_job_report(job_id: str):
    """返回单个编译任务的实时派生统计。"""

    try:
        job = COMPILATION_JOBS.get_job(job_id)
        chapters = COMPILATION_JOBS.list_chapters(job_id)
        snapshots = COMPILATION_JOBS.list_snapshots(job_id)
    except CompilationJobNotFound as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=404)
    now = datetime.now(timezone.utc)
    report = build_job_report(job, chapters, now=now)
    report.update(
        {
            "job": job.payload(),
            "chapter_count": len(chapters),
            "snapshot_count": len(snapshots),
            "worker_active": COMPILATION_JOBS.is_worker_active(job_id),
            "reported_at": now.isoformat(),
        }
    )
    return {"status": "ok", "report": report}


@app.get("/api/creator/compiler/jobs/{job_id}")
def api_get_compilation_job(job_id: str):
    """返回任务、逐章进度和分层快照元数据。"""

    try:
        job = COMPILATION_JOBS.get_job(job_id)
        chapters = COMPILATION_JOBS.list_chapters(job_id)
        snapshots = COMPILATION_JOBS.list_snapshots(job_id)
    except CompilationJobNotFound as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)},
            status_code=404,
        )
    return {
        "status": "ok",
        "job": job.payload(),
        "chapters": chapters,
        "snapshots": snapshots,
        "worker_active": COMPILATION_JOBS.is_worker_active(job_id),
    }


@app.post("/api/creator/compiler/jobs/{job_id}/actions")
def api_control_compilation_job(
    job_id: str,
    req: CompilationJobActionRequest,
):
    """协作式暂停、继续或取消编译任务。"""

    denied = _permission_error(
        "compiler.manage",
        resource_type="compiler_job",
        resource_id=job_id,
    )
    if denied:
        return denied
    action = req.action.strip().lower()
    try:
        if action == "pause":
            job = COMPILATION_JOBS.request_pause(job_id)
        elif action == "cancel":
            job = COMPILATION_JOBS.request_cancel(job_id)
        elif action == "resume":
            job = COMPILATION_JOBS.resume(job_id)
        elif action == "retry":
            job = COMPILATION_JOBS.retry_failed(job_id)
        elif action == "extend_budget":
            if req.additional_llm_calls <= 0:
                return JSONResponse(
                    {"status": "invalid", "error": "additional_llm_calls 必须为正整数"},
                    status_code=422,
                )
            job = COMPILATION_JOBS.increase_llm_budget(
                job_id,
                req.additional_llm_calls,
            )
        else:
            return JSONResponse(
                {
                    "status": "invalid",
                    "error": "action 仅支持 pause、resume、retry、extend_budget、cancel",
                },
                status_code=422,
            )
    except CompilationJobNotFound as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)},
            status_code=404,
        )
    except CompilationJobConflict as exc:
        return JSONResponse(
            {"status": "conflict", "error": str(exc)},
            status_code=409,
        )
    AUTH.audit(
        _actor(),
        action=f"compiler_job.{action}",
        resource_type="compiler_job",
        resource_id=job_id,
        detail={"status": job.status},
    )
    return {"status": "ok", "job": job.payload()}


@app.post("/api/creator/compiler/jobs/{job_id}/budget")
def api_extend_compilation_budget(
    job_id: str,
    req: CompilationJobBudgetRequest,
):
    """为可继续任务追加预算；不会自动恢复或启动任务。"""

    denied = _permission_error(
        "compiler.manage",
        resource_type="compiler_job",
        resource_id=job_id,
    )
    if denied:
        return denied
    try:
        job = COMPILATION_JOBS.increase_llm_budget(
            job_id,
            req.additional_llm_calls,
        )
    except CompilationJobNotFound as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=404)
    except (CompilationJobConflict, ValueError) as exc:
        return JSONResponse({"status": "conflict", "error": str(exc)}, status_code=409)
    AUTH.audit(
        _actor(),
        action="compiler_job.budget_update",
        resource_type="compiler_job",
        resource_id=job_id,
        detail={
            "additional_llm_calls": req.additional_llm_calls,
            "max_llm_calls": job.max_llm_calls,
            "llm_calls_used": job.llm_calls_used,
            "reason": req.reason,
        },
    )
    return {"status": "ok", "job": job.payload()}


@app.get("/api/creator/packages")
def api_creator_packages():
    """列出内置和创作者保存的世界包。"""

    try:
        packages = PACKAGES.list_packages()
    except WorldPackageError as e:
        return JSONResponse(
            {"status": "error", "error": f"读取世界包失败: {e}"},
            status_code=500,
        )
    return {
        "status": "ok",
        "packages": [package.summary() for package in packages],
    }


@app.get("/api/creator/packages/{package_id}")
def api_creator_package(package_id: str):
    """读取一个可供编辑的完整世界包。"""

    try:
        package = PACKAGES.get(package_id)
    except WorldPackageNotFound as e:
        return JSONResponse(
            {"status": "error", "error": str(e)},
            status_code=404,
        )
    except WorldPackageError as e:
        return JSONResponse(
            {"status": "error", "error": str(e)},
            status_code=400,
        )
    return {"status": "ok", "package": package.payload()}


@app.post("/api/creator/packages/validate")
def api_creator_validate(req: PackageDraftRequest):
    """只校验草稿，不写入磁盘。"""

    denied = _permission_error("creator.write", resource_type="world_package")
    if denied:
        return denied
    try:
        package = PACKAGES.validate(req.package)
    except WorldPackageValidationError as e:
        return JSONResponse(
            {
                "status": "invalid",
                "error": "世界包校验失败",
                "errors": e.errors,
            },
            status_code=422,
        )
    return {
        "status": "ok",
        "valid": True,
        "manifest": package.manifest,
    }


@app.post("/api/creator/packages/{package_id}/clone")
def api_creator_clone(package_id: str):
    """把内置或已有世界包另存为新的可编辑版本。"""

    denied = _permission_error(
        "creator.write",
        resource_type="world_package",
        resource_id=package_id,
    )
    if denied:
        return denied
    try:
        package = PACKAGES.clone(package_id)
    except WorldPackageNotFound as e:
        return JSONResponse(
            {"status": "error", "error": str(e)},
            status_code=404,
        )
    except WorldPackageError as e:
        return JSONResponse(
            {"status": "error", "error": f"另存世界包失败: {e}"},
            status_code=400,
        )
    AUTH.audit(
        _actor(),
        action="world_package.clone",
        resource_type="world_package",
        resource_id=package.package_id,
        detail={"source_package_id": package_id},
    )
    return {"status": "ok", "package": package.payload()}


@app.put("/api/creator/packages/{package_id}")
def api_creator_save(package_id: str, req: PackageDraftRequest):
    """校验并保存可编辑世界包，使用 revision 防止覆盖他人修改。"""

    denied = _permission_error(
        "creator.write",
        resource_type="world_package",
        resource_id=package_id,
    )
    if denied:
        return denied
    try:
        package = PACKAGES.save(
            package_id,
            req.package,
            expected_revision=req.expected_revision,
        )
    except WorldPackageConflict as e:
        return JSONResponse(
            {"status": "conflict", "error": str(e)},
            status_code=409,
        )
    except WorldPackageValidationError as e:
        return JSONResponse(
            {
                "status": "invalid",
                "error": "世界包校验失败",
                "errors": e.errors,
            },
            status_code=422,
        )
    except WorldPackageError as e:
        return JSONResponse(
            {"status": "error", "error": str(e)},
            status_code=400,
        )
    AUTH.audit(
        _actor(),
        action="world_package.save",
        resource_type="world_package",
        resource_id=package_id,
        detail={"revision": package.revision},
    )
    return {"status": "ok", "package": package.payload()}


@app.get("/api/creator/packages/{package_id}/revisions")
def api_creator_revisions(package_id: str):
    """列出世界包的不可变修订历史。"""

    try:
        revisions = PACKAGES.list_revisions(package_id)
    except WorldPackageNotFound as e:
        return JSONResponse(
            {"status": "error", "error": str(e)},
            status_code=404,
        )
    except WorldPackageError as e:
        return JSONResponse(
            {"status": "error", "error": str(e)},
            status_code=400,
        )
    return {"status": "ok", "revisions": revisions}


@app.get("/api/creator/packages/{package_id}/diff")
def api_creator_diff(
    package_id: str,
    from_revision: int,
    to_revision: Optional[int] = None,
):
    """比较两个已保存修订，返回结构化字段差异。"""

    try:
        diff = PACKAGES.diff_revisions(
            package_id,
            from_revision,
            to_revision,
        )
    except WorldPackageNotFound as e:
        return JSONResponse(
            {"status": "error", "error": str(e)},
            status_code=404,
        )
    except WorldPackageError as e:
        return JSONResponse(
            {"status": "error", "error": str(e)},
            status_code=400,
        )
    return {"status": "ok", "diff": diff}


@app.post("/api/creator/packages/{package_id}/review")
def api_creator_review(
    package_id: str,
    req: PackageReviewRequest,
):
    """按受控状态机提交审核、批准、驳回或发布。"""

    permission = {
        "pending_review": "review.submit",
        "approved": "review.decide",
        "rejected": "review.decide",
        "published": "review.publish",
        "draft": "creator.write",
    }.get(req.target_status, "review.decide")
    denied = _permission_error(
        permission,
        resource_type="world_package",
        resource_id=package_id,
    )
    if denied:
        return denied
    try:
        package = PACKAGES.transition_review(
            package_id,
            req.target_status,
            expected_revision=req.expected_revision,
            note=req.note,
        )
    except WorldPackageConflict as e:
        return JSONResponse(
            {"status": "conflict", "error": str(e)},
            status_code=409,
        )
    except WorldPackageValidationError as e:
        return JSONResponse(
            {
                "status": "invalid",
                "error": "审核状态无效",
                "errors": e.errors,
            },
            status_code=422,
        )
    except WorldPackageNotFound as e:
        return JSONResponse(
            {"status": "error", "error": str(e)},
            status_code=404,
        )
    except WorldPackageError as e:
        return JSONResponse(
            {"status": "error", "error": str(e)},
            status_code=400,
        )
    AUTH.audit(
        _actor(),
        action=f"world_package.review.{req.target_status}",
        resource_type="world_package",
        resource_id=package_id,
        detail={
            "revision": package.revision,
            "note": req.note,
        },
    )
    return {"status": "ok", "package": package.payload()}


@app.get("/api/creator/audit")
def api_creator_audit(
    limit: int = 100,
    resource_type: str = "",
    resource_id: str = "",
):
    denied = _permission_error("audit.read", resource_type="audit")
    if denied:
        return denied
    return {
        "status": "ok",
        "events": AUTH.list_audit(
            limit=limit,
            resource_type=resource_type,
            resource_id=resource_id,
        ),
    }


@app.get("/api/state")
def api_state(session: str = ""):
    """查当前会话状态 (调试用)。"""
    if not session:
        return {"sessions": "提供 ?session=<id> 查具体会话"}
    try:
        state = SESSIONS.get_state(session)
    except PersistenceError as e:
        return JSONResponse(
            {"error": f"读取会话失败: {e}"}, status_code=500,
        )
    if state is None:
        return JSONResponse(
            {"error": "会话不存在"}, status_code=404,
        )
    return state_to_dict(state)


@app.get("/api/events")
def api_events(session: str = ""):
    """查询会话的持久化事件日志（调试/回放用）。"""
    if not session:
        return JSONResponse(
            {"error": "请提供 ?session=<id>"}, status_code=400,
        )
    try:
        metadata = SESSIONS.get_metadata(session)
        if metadata is None:
            return JSONResponse(
                {"error": "会话不存在"}, status_code=404,
            )
        events = SESSIONS.list_events(session)
    except PersistenceError as e:
        return JSONResponse(
            {"error": f"读取事件失败: {e}"}, status_code=500,
        )
    return {
        "session_id": session,
        "state_version": metadata.state_version,
        "events": [event.dict() for event in events],
    }


@app.post("/api/joint-plans/generate")
def api_generate_joint_plan(req: JointPlanGenerateRequest):
    """Ask the configured real LLM for an editable, non-executable draft."""

    if not _joint_plan_store_supported():
        return JSONResponse(
            {"status": "unsupported", "error": "当前存储后端不支持联合计划"},
            status_code=501,
        )
    try:
        metadata = SESSIONS.get_metadata(req.session_id)
        state = SESSIONS.get_state(req.session_id)
        if metadata is None or state is None:
            raise SessionNotFound("会话不存在")
        state = _repair_legacy_canonical_facts(
            req.session_id,
            state,
            metadata,
        )
        package = PACKAGES.get(metadata.world_package_id)
        settlement_response = _planning_settlement_response(
            req.session_id,
            state,
            metadata,
            package,
        )
        if settlement_response is not None:
            return settlement_response
        existing = SESSIONS.list_joint_plan_runtimes(req.session_id)
        unfinished_statuses = {
            PlanRuntimeStatus.draft,
            PlanRuntimeStatus.approved,
            PlanRuntimeStatus.active,
            PlanRuntimeStatus.stale,
            PlanRuntimeStatus.deadlocked,
        }
        unfinished = next(
            (
                (plan, runtime)
                for plan, runtime in existing
                if runtime.status in unfinished_statuses
            ),
            None,
        )
        if unfinished is not None:
            return JSONResponse(
                {
                    "status": "conflict",
                    "error": "当前仍有未结束规划，请先编辑、执行或处理该规划。",
                    "plan": _serialize_joint_plan(*unfinished),
                },
                status_code=409,
            )
        goal = (req.goal or "").strip()
        if not goal:
            return JSONResponse(
                {"status": "error", "error": "剧情推进目标不能为空"},
                status_code=400,
            )
        actor_ids = _select_planning_actors(
            state,
            metadata.default_actor_id,
            req.actor_ids,
        )
        recent_events = SESSIONS.list_events(req.session_id)[-12:]
        permissions = _planning_permissions(actor_ids)
        planner = PLAN_PLANNER_FACTORY(metadata.world_package_id)
        with capture_llm_usage() as usage:
            plan = planner.generate(
                state,
                actor_ids,
                beat_goal=goal,
                goal_id="web_beat_" + uuid4().hex[:12],
                permissions_by_actor=permissions,
                metadata={
                    "source": "web_planning_approval",
                    "recent_committed_events": [
                        {
                            "event_type": event.event_type,
                            "actor_ids": list(event.actor_ids),
                            "target_ids": list(event.target_ids),
                            "summary": event.summary,
                        }
                        for event in recent_events
                    ],
                },
            )
        runtime = create_plan_runtime(plan, max_replans=req.max_replans)
        runtime.status = (
            PlanRuntimeStatus.approved
            if req.auto_approve
            else PlanRuntimeStatus.draft
        )
        SESSIONS.save_joint_plan_runtime(req.session_id, plan, runtime)
        return {
            "status": "ok",
            "plan": _serialize_joint_plan(plan, runtime),
            "planner_traces": [item.dict() for item in planner.call_traces],
            "llm_usage": usage.summary().dict(),
            "llm_calls": [item.dict() for item in usage.calls],
            "auto_approved": req.auto_approve,
        }
    except SessionNotFound as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)},
            status_code=404,
        )
    except (ValueError, NarrativePlannerError) as exc:
        return JSONResponse(
            {"status": "error", "error": f"规划生成失败: {exc}"},
            status_code=422,
        )
    except PersistenceError as exc:
        return JSONResponse(
            {"status": "error", "error": f"规划保存失败: {exc}"},
            status_code=500,
        )
    except Exception as exc:  # noqa: BLE001 - provider/network boundary
        return JSONResponse(
            {
                "status": "error",
                "error": f"真实 LLM 规划异常: {type(exc).__name__}: {exc}",
            },
            status_code=502,
        )


@app.post("/api/joint-plans/abort-active")
def api_abort_active_joint_plans(req: JointPlanControlRequest):
    """Abort every unfinished plan without changing authoritative world state."""

    try:
        state = SESSIONS.get_state(req.session_id)
        metadata = SESSIONS.get_metadata(req.session_id)
        if state is None or metadata is None:
            raise SessionNotFound("会话不存在")
        loader = getattr(SESSIONS, "list_joint_plan_runtimes", None)
        if not callable(loader):
            return JSONResponse(
                {"status": "unsupported", "error": "当前存储后端不支持联合计划"},
                status_code=501,
            )
        terminal = {PlanRuntimeStatus.completed, PlanRuntimeStatus.aborted}
        aborted_plan_ids = []
        stored = loader(req.session_id)
        for plan, runtime in stored:
            if runtime.status in terminal:
                continue
            runtime.status = PlanRuntimeStatus.aborted
            runtime.last_trigger = "USER_ABORTED_FOR_REPLAN"
            runtime.stale_reasons = list(
                dict.fromkeys(
                    [*runtime.stale_reasons, "用户选择从当前权威世界状态重新规划"]
                )
            )
            SESSIONS.save_joint_plan_runtime(req.session_id, plan, runtime)
            aborted_plan_ids.append(plan.plan_id)
        refreshed = SESSIONS.list_joint_plan_runtimes(req.session_id)
        return {
            "status": "ok",
            "session_id": req.session_id,
            "world_version": state.version,
            "aborted_plan_ids": aborted_plan_ids,
            "plans": [
                _serialize_joint_plan(plan, runtime)
                for plan, runtime in refreshed
            ],
        }
    except SessionNotFound as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)},
            status_code=404,
        )
    except PersistenceError as exc:
        return JSONResponse(
            {"status": "error", "error": f"终止旧规划失败: {exc}"},
            status_code=500,
        )


@app.put("/api/joint-plans/{plan_id}")
def api_update_joint_plan(plan_id: str, req: JointPlanUpdateRequest):
    """Replace an unapproved draft after full Schema and tool validation."""

    try:
        current_plan, current_runtime = _load_joint_plan(req.session_id, plan_id)
        state = SESSIONS.get_state(req.session_id)
        if state is None:
            raise SessionNotFound("会话不存在")
        if current_runtime.status != PlanRuntimeStatus.draft:
            return JSONResponse(
                {"status": "conflict", "error": "只有待审批草案可以修改"},
                status_code=409,
            )
        payload = dict(req.plan or {})
        if payload.get("plan_id") not in {None, "", plan_id}:
            raise ValueError("不能修改 plan_id")
        payload["plan_id"] = plan_id
        payload["base_world_version"] = current_plan.base_world_version
        edited = JointPlan.parse_obj(payload)
        permissions = _planning_permissions(list(edited.actor_chains))
        validate_joint_plan(
            edited,
            state,
            PLAN_TOOL_REGISTRY,
            permissions_by_actor=permissions,
            enforce_shared_scope=False,
        )
        runtime = create_plan_runtime(
            edited,
            max_replans=current_runtime.max_replans,
        )
        runtime.status = PlanRuntimeStatus.draft
        SESSIONS.save_joint_plan_runtime(req.session_id, edited, runtime)
        return {"status": "ok", "plan": _serialize_joint_plan(edited, runtime)}
    except SessionNotFound as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)},
            status_code=404,
        )
    except (ValueError, PersistenceError) as exc:
        return JSONResponse(
            {"status": "error", "error": f"规划修改失败: {exc}"},
            status_code=422,
        )


@app.post("/api/joint-plans/{plan_id}/approve")
def api_approve_joint_plan(plan_id: str, req: JointPlanControlRequest):
    """Freeze a reviewed draft against the current authoritative version."""

    try:
        plan, runtime = _load_joint_plan(req.session_id, plan_id)
        state = SESSIONS.get_state(req.session_id)
        if state is None:
            raise SessionNotFound("会话不存在")
        if runtime.status != PlanRuntimeStatus.draft:
            return JSONResponse(
                {"status": "conflict", "error": "该规划不处于待审批状态"},
                status_code=409,
            )
        if state.version != plan.base_world_version:
            return JSONResponse(
                {
                    "status": "conflict",
                    "error": (
                        "世界状态已变化，请重新生成规划："
                        f"plan=v{plan.base_world_version}, world=v{state.version}"
                    ),
                },
                status_code=409,
            )
        validate_joint_plan(
            plan,
            state,
            PLAN_TOOL_REGISTRY,
            permissions_by_actor=_planning_permissions(list(plan.actor_chains)),
            enforce_shared_scope=False,
        )
        runtime.status = PlanRuntimeStatus.approved
        SESSIONS.save_joint_plan_runtime(req.session_id, plan, runtime)
        return {"status": "ok", "plan": _serialize_joint_plan(plan, runtime)}
    except SessionNotFound as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)},
            status_code=404,
        )
    except (ValueError, PersistenceError) as exc:
        return JSONResponse(
            {"status": "error", "error": f"规划审批失败: {exc}"},
            status_code=422,
        )


@app.post("/api/joint-plans/{plan_id}/execute")
async def api_execute_joint_plan(plan_id: str, req: JointPlanExecuteRequest):
    """Execute one guarded tick or the approved plan to a terminal/block state."""

    try:
        plan, runtime = _load_joint_plan(req.session_id, plan_id)
        state = SESSIONS.get_state(req.session_id)
        metadata = SESSIONS.get_metadata(req.session_id)
        if state is None or metadata is None:
            raise SessionNotFound("会话不存在")
        state = _repair_legacy_canonical_facts(
            req.session_id,
            state,
            metadata,
        )
        package = PACKAGES.get(metadata.world_package_id)
        if runtime.status == PlanRuntimeStatus.completed:
            return {
                "status": "ok",
                "plan": _serialize_joint_plan(plan, runtime),
                "state": state_to_dict(state),
                "ticks": 0,
                "events": [],
            }
        settlement_response = _planning_settlement_response(
            req.session_id,
            state,
            metadata,
            package,
        )
        if settlement_response is not None:
            if runtime.status != PlanRuntimeStatus.aborted:
                runtime.status = PlanRuntimeStatus.aborted
                runtime.stale_reasons = ["settlement_required"]
                SESSIONS.save_joint_plan_runtime(req.session_id, plan, runtime)
            return settlement_response
        if runtime.status == PlanRuntimeStatus.draft:
            return JSONResponse(
                {"status": "conflict", "error": "规划尚未批准，不能执行"},
                status_code=409,
            )
        if runtime.status == PlanRuntimeStatus.aborted:
            return JSONResponse(
                {"status": "conflict", "error": "规划已终止，请生成新规划"},
                status_code=409,
            )
        permissions = _planning_permissions(list(plan.actor_chains))
        planner = (
            PLAN_PLANNER_FACTORY(metadata.world_package_id)
            if req.auto_replan
            else None
        )
        beat_goal = str(plan.metadata.get("beat_goal") or plan.goal_id)

        def replan_callback(request, latest_state):
            if planner is None:
                return None
            return planner.replan(
                request,
                latest_state,
                beat_goal=beat_goal,
                permissions_by_actor=_planning_permissions(
                    list(request.affected_actor_ids)
                ),
            )

        all_events = []
        outcomes = []
        ticks = 0
        tick_limit = req.max_ticks if req.run_to_completion else 1
        with capture_llm_usage() as usage:
            for _ in range(tick_limit):
                before = (
                    state.version,
                    dict(runtime.actor_step_pointers),
                    runtime.status.value,
                    runtime.replan_count,
                )
                result = await PLAN_EXECUTOR.tick(
                    plan,
                    runtime,
                    state,
                    permissions_by_actor=permissions,
                    replan=replan_callback if req.auto_replan else None,
                    store=SESSIONS,
                    session_id=req.session_id,
                )
                ticks += 1
                plan, runtime, state = result.plan, result.runtime, result.state
                state, perception_events = commit_dialogue_perceptions(
                    state,
                    result.events,
                    store=SESSIONS,
                    session_id=req.session_id,
                )
                all_events.extend(result.events)
                all_events.extend(perception_events)
                outcomes.extend(
                    {
                        "tool_name": item.result.tool_name,
                        "success": item.result.success,
                        "failure": (
                            item.result.failure.dict()
                            if item.result.failure is not None
                            else None
                        ),
                        "event_id": item.result.committed_event_id,
                        "world_version": item.result.world_version,
                    }
                    for item in result.outcomes
                )
                settlement = _settlement_projection(
                    req.session_id,
                    state,
                    metadata,
                    package,
                )
                if settlement["status"] != "unavailable":
                    runtime.status = PlanRuntimeStatus.aborted
                    runtime.stale_reasons = ["settlement_required"]
                    SESSIONS.save_joint_plan_runtime(req.session_id, plan, runtime)
                    break
                after = (
                    state.version,
                    dict(runtime.actor_step_pointers),
                    runtime.status.value,
                    runtime.replan_count,
                )
                if runtime.status in {
                    PlanRuntimeStatus.completed,
                    PlanRuntimeStatus.aborted,
                    PlanRuntimeStatus.stale,
                    PlanRuntimeStatus.deadlocked,
                }:
                    break
                if not req.run_to_completion or (
                    before == after and not result.replanned
                ):
                    break

        manuscript_projection = None
        if all_events:
            manuscript_projection = _persist_manuscript_batch(
                req.session_id,
                all_events,
                state,
            )
        memory_warning = ""
        for event in all_events:
            try:
                record_event_memory(
                    SESSIONS,
                    req.session_id,
                    state,
                    event,
                    player_input=f"联合规划：{beat_goal}",
                    narration=event.summary,
                )
            except PersistenceError as exc:
                memory_warning = f"长期记忆沉淀待重建: {exc}"
                break
        return {
            "status": "ok",
            "plan": _serialize_joint_plan(plan, runtime),
            "state": state_to_dict(state),
            "ticks": ticks,
            "events": [event.dict() for event in all_events],
            "outcomes": outcomes,
            "planner_traces": (
                [item.dict() for item in planner.call_traces]
                if planner is not None
                else []
            ),
            "llm_usage": usage.summary().dict(),
            "llm_calls": [item.dict() for item in usage.calls],
            "memory_warning": memory_warning,
            "manuscript": manuscript_projection,
        }
    except SessionNotFound as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)},
            status_code=404,
        )
    except (ValueError, PersistenceError) as exc:
        return JSONResponse(
            {"status": "error", "error": f"规划执行失败: {exc}"},
            status_code=422,
        )
    except Exception as exc:  # noqa: BLE001 - tool/provider boundary
        return JSONResponse(
            {
                "status": "error",
                "error": f"规划执行异常: {type(exc).__name__}: {exc}",
            },
            status_code=500,
        )


@app.get("/api/manuscript/passages/{passage_id}/revisions")
def api_list_manuscript_passage_revisions(
    passage_id: str,
    session: str = "",
):
    """Read immutable revision history for a passage in the current campaign."""

    try:
        if not session:
            return JSONResponse(
                {"status": "error", "error": "请提供 ?session=<id>"},
                status_code=400,
            )
        passage = SESSIONS.get_manuscript_passage(passage_id)
        if passage is None:
            raise SessionNotFound("稿件段落不存在")
        requester_lineage = SESSIONS.get_session_lineage(session)
        passage_lineage = SESSIONS.get_session_lineage(passage.session_id)
        if (
            requester_lineage is None
            or passage_lineage is None
            or requester_lineage.campaign_id != passage_lineage.campaign_id
        ):
            raise SessionNotFound("稿件段落不属于当前世界线")
        revisions = SESSIONS.list_manuscript_passage_revisions(passage_id)
        return {
            "status": "ok",
            "passage_id": passage_id,
            "current_revision": passage.current_revision,
            "revisions": [
                _manuscript_revision_projection(
                    revision,
                    selected=revision.revision_number == passage.current_revision,
                )
                for revision in revisions
            ],
        }
    except SessionNotFound as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)},
            status_code=404,
        )
    except (PersistenceError, ValueError) as exc:
        return JSONResponse(
            {"status": "error", "error": f"读取稿件版本失败: {exc}"},
            status_code=422,
        )


@app.post("/api/manuscript/passages/{passage_id}/select-revision")
def api_select_manuscript_passage_revision(
    passage_id: str,
    req: ManuscriptRevisionSelectRequest,
):
    """Switch the current pointer without deleting immutable revision history."""

    try:
        passage = SESSIONS.get_manuscript_passage(passage_id)
        if passage is None:
            raise SessionNotFound("稿件段落不存在")
        requester_lineage = SESSIONS.get_session_lineage(req.session_id)
        passage_lineage = SESSIONS.get_session_lineage(passage.session_id)
        if (
            requester_lineage is None
            or passage_lineage is None
            or requester_lineage.campaign_id != passage_lineage.campaign_id
        ):
            raise SessionNotFound("稿件段落不属于当前世界线")
        selected = SESSIONS.select_manuscript_passage_revision(
            passage_id,
            req.revision_number,
            expected_current_revision=req.expected_revision,
        )
        return {
            "status": "ok",
            "manuscript": _manuscript_projection_payload(selected),
        }
    except SessionNotFound as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)},
            status_code=404,
        )
    except ManuscriptRevisionConflict as exc:
        return JSONResponse(
            {"status": "conflict", "error": str(exc)},
            status_code=409,
        )
    except (PersistenceError, ValueError) as exc:
        return JSONResponse(
            {"status": "error", "error": f"切换稿件版本失败: {exc}"},
            status_code=422,
        )


@app.post("/api/manuscript/passages/{passage_id}/retry")
def api_retry_manuscript_passage(
    passage_id: str,
    req: ManuscriptRetryRequest,
):
    """Retry only the derived passage; committed events are never replayed."""

    try:
        passage = SESSIONS.get_manuscript_passage(passage_id)
        if passage is None:
            raise SessionNotFound("稿件段落不存在")
        requester_lineage = SESSIONS.get_session_lineage(req.session_id)
        passage_lineage = SESSIONS.get_session_lineage(passage.session_id)
        if (
            requester_lineage is None
            or passage_lineage is None
            or requester_lineage.campaign_id != passage_lineage.campaign_id
        ):
            raise SessionNotFound("稿件段落不属于当前世界线")
        source_session_id = passage.session_id
        state = SESSIONS.get_state(source_session_id)
        if state is None:
            raise SessionNotFound("稿件来源会话不存在")
        event_by_id = {
            event.event_id: event
            for event in SESSIONS.list_events(source_session_id)
        }
        events = [
            event_by_id[event_id]
            for event_id in passage.source_event_ids
            if event_id in event_by_id
        ]
        if len(events) != len(passage.source_event_ids):
            raise PersistenceError("稿件来源事件不完整，不能重试")
        projection = _persist_manuscript_batch(
            source_session_id,
            events,
            state,
            retry_ready_with_error=not req.rewrite_ready,
            rewrite_ready=req.rewrite_ready,
            expected_revision=req.expected_revision,
        )
        return {
            "status": "ok" if projection["status"] == "ready" else "failed",
            "manuscript": projection,
        }
    except SessionNotFound as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)},
            status_code=404,
        )
    except ManuscriptRevisionConflict as exc:
        return JSONResponse(
            {"status": "conflict", "error": str(exc)},
            status_code=409,
        )
    except (PersistenceError, ManuscriptWriterError, ValueError) as exc:
        return JSONResponse(
            {"status": "error", "error": f"稿件重试失败: {exc}"},
            status_code=422,
        )


@app.post("/api/manuscript/retry")
def api_retry_session_manuscript(req: ManuscriptRetryRequest):
    """Retry all pending or failed passages for one session."""

    try:
        state = SESSIONS.get_state(req.session_id)
        if state is None:
            raise SessionNotFound("会话不存在")
        event_by_id = {
            event.event_id: event
            for event in SESSIONS.list_events(req.session_id)
        }
        results = []
        for passage in SESSIONS.list_manuscript_passages(req.session_id):
            should_retry = (
                passage.generation_status
                in {
                    ManuscriptGenerationStatus.pending,
                    ManuscriptGenerationStatus.failed,
                }
                or bool(passage.last_error)
            )
            if not should_retry:
                continue
            events = [
                event_by_id[event_id]
                for event_id in passage.source_event_ids
                if event_id in event_by_id
            ]
            if len(events) != len(passage.source_event_ids):
                failed = SESSIONS.fail_manuscript_passage(
                    passage.passage_id,
                    "稿件来源事件不完整，不能重试",
                )
                results.append(_manuscript_projection_payload(failed))
                continue
            results.append(
                _persist_manuscript_batch(
                    req.session_id,
                    events,
                    state,
                    retry_ready_with_error=True,
                )
            )
        return {
            "status": "ok",
            "retried": len(results),
            "passages": results,
        }
    except SessionNotFound as exc:
        return JSONResponse(
            {"status": "error", "error": str(exc)},
            status_code=404,
        )
    except (PersistenceError, ManuscriptWriterError, ValueError) as exc:
        return JSONResponse(
            {"status": "error", "error": f"稿件重试失败: {exc}"},
            status_code=422,
        )


@app.get("/api/joint-plans")
def api_joint_plans(session: str = ""):
    """Expose persisted action chains and synchronization state for Web UI."""

    if not session:
        return JSONResponse(
            {"status": "error", "error": "请提供 ?session=<id>"},
            status_code=400,
        )
    try:
        metadata = SESSIONS.get_metadata(session)
        if metadata is None:
            return JSONResponse(
                {"status": "error", "error": "会话不存在"},
                status_code=404,
            )
        loader = getattr(SESSIONS, "list_joint_plan_runtimes", None)
        if not callable(loader):
            return {
                "status": "unsupported",
                "session_id": session,
                "plans": [],
            }
        stored = loader(session)
    except PersistenceError as exc:
        return JSONResponse(
            {"status": "error", "error": f"读取联合计划失败: {exc}"},
            status_code=500,
        )
    plans = [_serialize_joint_plan(plan, runtime) for plan, runtime in stored]
    return {
        "status": "ok",
        "session_id": session,
        "state_version": metadata.state_version,
        "plans": plans,
    }


@app.get("/api/presentation-snapshot")
def api_presentation_snapshot(session: str = ""):
    """Unity 重连时的权威表现快照。"""

    if not session:
        return JSONResponse(
            {"status": "error", "error": "请提供 ?session=<id>"},
            status_code=400,
        )
    try:
        state = SESSIONS.get_state(session)
    except PersistenceError as exc:
        return JSONResponse(
            {"status": "error", "error": f"读取会话失败: {exc}"},
            status_code=500,
        )
    if state is None:
        return JSONResponse(
            {"status": "error", "error": "会话不存在"},
            status_code=404,
        )
    return {
        "status": "ok",
        "session_id": session,
        "snapshot": serialize_presentation_snapshot(state),
    }


@app.get("/api/presentation-events")
def api_presentation_events(
    session: str = "",
    after_sequence: int = 0,
    limit: int = 100,
):
    """返回 after_sequence 之后的幂等表现命令。"""

    if not session:
        return JSONResponse(
            {"status": "error", "error": "请提供 ?session=<id>"},
            status_code=400,
        )
    if after_sequence < 0:
        return JSONResponse(
            {"status": "error", "error": "after_sequence 不能为负数"},
            status_code=400,
        )
    if not 1 <= limit <= 500:
        return JSONResponse(
            {"status": "error", "error": "limit 必须在 1 到 500 之间"},
            status_code=400,
        )
    try:
        metadata = SESSIONS.get_metadata(session)
        if metadata is None:
            return JSONResponse(
                {"status": "error", "error": "会话不存在"},
                status_code=404,
            )
        latest_sequence = cursor_after_world_version(
            metadata.state_version
        )
        if after_sequence > latest_sequence:
            return JSONResponse(
                {
                    "status": "reset_required",
                    "error": "客户端游标超过服务端世界版本，请重新拉取快照",
                    "session_id": session,
                    "latest_sequence": latest_sequence,
                },
                status_code=409,
            )
        commands, has_more = project_presentation_commands(
            SESSIONS.list_events(session),
            after_sequence=after_sequence,
            limit=limit,
        )
    except (PersistenceError, ValueError) as exc:
        return JSONResponse(
            {"status": "error", "error": f"读取表现事件失败: {exc}"},
            status_code=500,
        )
    return {
        "status": "ok",
        "session_id": session,
        "state_version": metadata.state_version,
        "after_sequence": after_sequence,
        "next_sequence": (
            commands[-1].sequence if commands else after_sequence
        ),
        "latest_sequence": latest_sequence,
        "has_more": has_more,
        "commands": [command.dict() for command in commands],
    }
