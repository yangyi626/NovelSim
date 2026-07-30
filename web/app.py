"""AI 快穿系统 Web 后端 (FastAPI)。

提供世界线存档 JSON API + 托管前端构建产物 (web/static)：
    GET  /                -> 托管 static/index.html (生产)
    POST /api/start       -> 从指定世界包新建会话
    GET  /api/session     -> 恢复一个已有会话
    POST /api/turn        -> 跑一回合，返回 TurnResult 序列化
    GET  /api/state       -> 查询持久化世界状态
    GET  /api/events      -> 查询持久化事件日志
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

import os
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from world_schema import WorldState
from engine import (
    PersistenceError,
    ReflectionSemanticJudge,
    SessionNotFound,
    TurnPipeline,
    VersionConflict,
    WorldPackageConflict,
    WorldPackageError,
    WorldPackageNotFound,
    WorldPackageStore,
    WorldPackageValidationError,
    create_world_store,
    cursor_after_world_version,
    filter_compatible_memories,
    project_presentation_commands,
    record_event_memory,
    reflect_character_memories,
)
from compiler import (
    EXTRACTOR_PROMPT_VERSION,
    CompilationJobConflict,
    CompilationJobNotFound,
    CompilationJobStore,
)
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

from examples.huarong_lane import build_snapshot, build_world_package
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


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 内置华容巷世界的默认玩家角色
DEFAULT_ACTOR_ID = NIGHT

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
        SECRET_LETTER_PACKAGE_ID: _secret_letter_manifest,
    },
)
COMPILATION_JOBS = CompilationJobStore(COMPILER_DATABASE_PATH)
AUTH = AuthStore(AUTH_DATABASE_PATH)
_CURRENT_ACTOR: ContextVar[AuthUser] = ContextVar(
    "novelsim_current_actor",
    default=SYSTEM_ACTOR,
)

# TurnPipeline 全局单例 (构造不触发 key 检查，懒加载各 LLM 组件)
PIPELINE = TurnPipeline()


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------


class StartRequest(BaseModel):
    package_id: str = "huarong_lane"
    save_name: str = ""


class TurnRequest(BaseModel):
    session_id: str
    text: str
    use_npc_agents: bool = True


class SecretLetterRunRequest(BaseModel):
    mode: str = SceneMode.free.value
    route: str = "none"
    save_name: str = ""


class RenameSaveRequest(BaseModel):
    name: str


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
    benchmark_id: str = ""
    novel_name: str = ""
    chapters: List[int] = Field(default_factory=list)
    timeline_plan: Dict[int, str] = Field(default_factory=dict)
    volume_plan: Dict[int, str] = Field(default_factory=dict)
    volume_size: int = 20
    model: str = ""
    max_llm_calls: int = 100
    auto_start: bool = True


class CompilationJobActionRequest(BaseModel):
    action: str


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


def serialize_save(metadata) -> dict:
    return {
        "session_id": metadata.session_id,
        "name": metadata.save_name,
        "world_package_id": metadata.world_package_id,
        "default_actor": metadata.default_actor_id,
        "version": metadata.state_version,
        "created_at": metadata.created_at,
        "updated_at": metadata.updated_at,
    }


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
        "world_meta": package.world_meta(),
        "state": state_to_dict(state),
        "save": serialize_save(metadata),
        "turns": serialize_history(history),
        "resumed": resumed,
    }


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


@app.post("/api/start")
def api_start(req: Optional[StartRequest] = None):
    """从指定世界包创建一条干净世界线。"""

    request = req or StartRequest()
    try:
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
        try:
            persist_turn_memories(req.session_id, text, result)
        except PersistenceError as e:
            # 权威回合已经提交，派生索引失败不能把成功伪装成失败。
            memory_warning = f"长期记忆沉淀待重建: {e}"
    if memory_warning:
        response_payload["memory_warning"] = memory_warning

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
        else:
            return JSONResponse(
                {
                    "status": "invalid",
                    "error": "action 仅支持 pause、resume、cancel",
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
