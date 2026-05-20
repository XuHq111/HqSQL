"""HqSQL Web API -- FastAPI + SSE 流式推送"""
import asyncio
import json
import uuid
import time
import threading
from contextlib import asynccontextmanager
from queue import Queue

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from pymilvus import Collection, connections

from nl2sql_graph.services.db_adapter import SQLiteAdapter
from nl2sql_graph.graph_builder import build_graph

DB_PATH = r"E:\sql数据集\accounting.sqlite"
STATIC_DIR = "static"

# SSE 事件缓冲区（session_id → Queue）
_event_queues: dict[str, Queue] = {}
# 会话状态存储（session_id → state dict）
_sessions: dict[str, dict] = {}
# 会话最后活跃时间
_session_last_active: dict[str, float] = {}
# 等待 clarify 响应的会话
_pending_clarify: dict[str, threading.Event] = {}

SESSION_TTL = 86400  # 24小时

# 全局单例
_collection = None
_db_adapter = None
_graph = None


def _make_initial_state(query: str, session_id: str) -> dict:
    return {
        "query": query,
        "raw_query": "",
        "skip_clarify": False,
        "clarify_phase": "init",
        "clarify_round": 0,
        "clarify_analysis": None,
        "clarify_user_response": "",
        "all_tables": [],
        "selected_names": [],
        "rerank_raw": "",
        "forced_names": [],
        "warnings": [],
        "prompt": "",
        "sql": "",
        "error": None,
        "sql_result": None,
        "sql_error": None,
        "retry_count": 0,
        "route": "",
        "lookup_context": "",
        "node_timings": {},
        "_clarify_callback": _create_clarify_callback(session_id),
        "_adapter": _db_adapter,
    }


def _emit_event(session_id: str, event_type: str, data: dict):
    """向指定 session 的 SSE 队列推送事件"""
    q = _event_queues.get(session_id)
    if q:
        q.put({"event": event_type, "data": json.dumps(data, ensure_ascii=False)})


def _create_clarify_callback(session_id: str):
    """创建 clarify 回调函数：将澄清问题推送给前端 SSE，等待用户回复"""
    def on_clarify(payload: dict) -> str:
        event = threading.Event()
        _pending_clarify[session_id] = event
        _emit_event(session_id, "clarify", {
            "enhanced_query": payload.get("enhanced_query", ""),
            "questions": payload.get("questions", []),
            "round": payload.get("round", 1),
        })
        # 等待用户通过 POST /api/chat 提交澄清回复
        event.wait(timeout=300)  # 5分钟超时
        _pending_clarify.pop(session_id, None)
        # 从 session state 中读取用户回复
        state = _sessions.get(session_id, {})
        return state.get("clarify_user_response", "")
    return on_clarify


def _run_graph(session_id: str, query: str):
    """在后台线程中运行 LangGraph 流水线，通过 SSE 推送进度"""
    try:
        state = _make_initial_state(query, session_id)
        _sessions[session_id] = state
        _session_last_active[session_id] = time.time()

        config = {"configurable": {"thread_id": session_id}}

        _emit_event(session_id, "progress", {
            "step": "recall", "detail": "正在理解查询并检索相关表结构..."
        })

        result = _graph.invoke(state, config)
        _sessions[session_id] = result

        sql = result.get("sql", "")
        sql_result = result.get("sql_result", "")
        sql_error = result.get("sql_error")
        timings = result.get("node_timings", {})

        if sql_error:
            _emit_event(session_id, "error", {
                "code": "EXEC_FAILED", "detail": sql_error, "sql": sql
            })
        else:
            columns = []
            rows = []
            if sql_result and sql_result != "(empty)":
                lines = sql_result.split("\n")
                if lines:
                    columns = lines[0].split(",")
                    rows = [line.split(",") for line in lines[1:]]
            total_time = sum(timings.values()) if timings else 0
            _emit_event(session_id, "result", {
                "sql": sql,
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "total_time": round(total_time, 2),
            })

    except Exception as e:
        _emit_event(session_id, "error", {
            "code": "INTERNAL", "detail": str(e)
        })


def _cleanup_expired_sessions():
    """清理过期会话"""
    now = time.time()
    expired = [
        sid for sid, ts in _session_last_active.items()
        if now - ts > SESSION_TTL
    ]
    for sid in expired:
        _sessions.pop(sid, None)
        _session_last_active.pop(sid, None)
        _event_queues.pop(sid, None)
        _pending_clarify.pop(sid, None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _collection, _db_adapter, _graph
    connections.connect(host='localhost', port='19530', db_name='HqSQL')
    _collection = Collection("tables")
    _collection.load()
    _db_adapter = SQLiteAdapter(DB_PATH)
    _graph = build_graph(_collection, _db_adapter)
    yield
    connections.disconnect("default")


app = FastAPI(lifespan=lifespan)

# 静态文件挂载
import os
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(f"{STATIC_DIR}/chat.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.post("/api/chat")
async def chat(
    session_id: str = Form(default=""),
    message: str = Form(default=""),
    clarify_response: str = Form(default=""),
):
    _cleanup_expired_sessions()

    if not session_id:
        session_id = str(uuid.uuid4())

    _session_last_active[session_id] = time.time()

    # 如果是澄清回复
    if clarify_response:
        state = _sessions.get(session_id, {})
        state["clarify_user_response"] = clarify_response
        event = _pending_clarify.get(session_id)
        if event:
            event.set()
        return JSONResponse({"session_id": session_id, "status": "clarify_response_received"})

    # 新查询
    if not message.strip():
        return JSONResponse({"error": "message 不能为空"}, status_code=400)

    if session_id not in _event_queues:
        _event_queues[session_id] = Queue()

    thread = threading.Thread(target=_run_graph, args=(session_id, message), daemon=True)
    thread.start()

    return JSONResponse({"session_id": session_id, "status": "processing"})


@app.get("/api/chat/stream")
async def chat_stream(session_id: str, request: Request):
    """SSE 端点：前端 EventSource 订阅"""
    if session_id not in _event_queues:
        _event_queues[session_id] = Queue()

    q = _event_queues[session_id]

    async def generate():
        while True:
            if await request.is_disconnected():
                break
            try:
                item = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: q.get(timeout=30)
                )
                yield f"event: {item['event']}\ndata: {item['data']}\n\n"
                if item["event"] in ("result", "error"):
                    break
            except Exception:
                yield f": keepalive\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
