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
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
    filter_compatible_memories,
    record_event_memory,
    reflect_character_memories,
)

from examples.huarong_lane import build_snapshot, build_world_package
from examples.huarong_lane.scenario import NIGHT


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


# ---------------------------------------------------------------------------
# 会话存储
# ---------------------------------------------------------------------------


SESSIONS = create_world_store(sqlite_path=DATABASE_PATH)

_builtin_manifest = build_world_package()
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
        }
    },
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


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------


app = FastAPI(title="AI 快穿系统 Web", version="0.1.0")


@app.on_event("shutdown")
def close_storage_clients() -> None:
    """释放 Qdrant Local Mode 文件锁；SQLite 本身无需常驻连接。"""

    closer = getattr(SESSIONS, "close", None)
    if callable(closer):
        closer()


# 静态资源 (前端构建产物里的 js/css)
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


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
    return {"status": "ok", "package": package.payload()}


@app.put("/api/creator/packages/{package_id}")
def api_creator_save(package_id: str, req: PackageDraftRequest):
    """校验并保存可编辑世界包，使用 revision 防止覆盖他人修改。"""

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
    return {"status": "ok", "package": package.payload()}


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
