"""AI 快穿系统 Web 后端 (FastAPI)。

提供 3 个 JSON 端点 + 托管前端构建产物 (web/static)：
    GET  /                -> 托管 static/index.html (生产)
    POST /api/start       -> 新建会话，返回初始世界状态
    POST /api/turn        -> 跑一回合，返回 TurnResult 序列化

会话状态存在内存里 (session_store: session_id -> WorldState)。
每次回合把新 state 存回，刷新页面 = 新开局。

安全边界：
- TurnPipeline 构造不触发 LLM key 检查 (懒加载)，import engine 安全
- .run() 包 try/except，捕获 RuntimeError (无 key) 和网络异常
  -> 返回 status="error" + 中文文案，绝不崩 worker
- use_npc_agents 默认 True (前端复选框默认勾选)
"""

from __future__ import annotations

import os
import secrets
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from world_schema import WorldState
from engine import TurnPipeline

from examples.huarong_lane import build_snapshot, build_world_package
from examples.huarong_lane.scenario import NIGHT


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 玩家固定扮演夜轻歌 (华容巷世界)
DEFAULT_ACTOR_ID = NIGHT
WORLD_META = {
    "novel": "第一狂妃：废柴三小姐",
    "scenario": "华容巷",
    "anchor": "夜轻歌被诬通奸、当众受辱",
}

STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# 会话存储 (内存版，线程安全)
# ---------------------------------------------------------------------------


class SessionStore:
    """session_id -> WorldState。线程锁保护。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: Dict[str, WorldState] = {}

    def new(self) -> str:
        sid = secrets.token_hex(8)
        with self._lock:
            # 极小概率碰撞，重试
            while sid in self._sessions:
                sid = secrets.token_hex(8)
            self._sessions[sid] = build_snapshot()
        return sid

    def get(self, sid: str) -> Optional[WorldState]:
        with self._lock:
            return self._sessions.get(sid)

    def put(self, sid: str, state: WorldState) -> None:
        with self._lock:
            self._sessions[sid] = state


SESSIONS = SessionStore()

# TurnPipeline 全局单例 (构造不触发 key 检查，懒加载各 LLM 组件)
PIPELINE = TurnPipeline()


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------


class TurnRequest(BaseModel):
    session_id: str
    text: str
    use_npc_agents: bool = True


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


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------


app = FastAPI(title="AI 快穿系统 Web", version="0.1.0")

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
def api_start():
    """新建会话。"""
    try:
        sid = SESSIONS.new()
    except Exception as e:  # noqa: BLE001
        return JSONResponse(
            {"status": "error", "error": f"世界初始化失败: {e}"},
            status_code=500,
        )
    state = SESSIONS.get(sid)
    return {
        "status": "ok",
        "session_id": sid,
        "default_actor": DEFAULT_ACTOR_ID,
        "world_meta": WORLD_META,
        "state": state_to_dict(state),
    }


@app.post("/api/turn")
def api_turn(req: TurnRequest):
    """跑一回合。返回序列化 TurnResult。

    任何异常 (无 key / 网络 / LLM) 都包装成 status="error"，
    绝不让 worker 崩溃。
    """
    state = SESSIONS.get(req.session_id)
    if state is None:
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

    try:
        result = PIPELINE.run(
            text,
            state,
            DEFAULT_ACTOR_ID,
            use_llm_proposer=True,
            use_narrative=True,
            use_npc_agents=req.use_npc_agents,
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

    # 成功提交 -> 把新 state 存回 session
    if result.new_state is not None:
        SESSIONS.put(req.session_id, result.new_state)

    return serialize_turn(result)


@app.get("/api/state")
def api_state(session: str = ""):
    """查当前会话状态 (调试用)。"""
    if not session:
        return {"sessions": "提供 ?session=<id> 查具体会话"}
    state = SESSIONS.get(session)
    if state is None:
        return JSONResponse(
            {"error": "会话不存在"}, status_code=404,
        )
    return state_to_dict(state)
