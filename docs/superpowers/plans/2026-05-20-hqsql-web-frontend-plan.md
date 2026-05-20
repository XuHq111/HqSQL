# HqSQL Web 前端改造 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 HqSQL CLI 引擎改造为 Web 对话应用，业务人员通过浏览器输入自然语言查询，获得 SQL + 表格结果。

**Architecture:** 在现有 LangGraph 引擎层上增加 FastAPI HTTP/SSE 接口层和 HTMX 前端页面。引擎层通过新增 DB 适配器抽象解耦数据库依赖，clarify 节点的 interrupt() 改为回调注入。

**Tech Stack:** FastAPI + SSE（后端），HTMX + Tabulator.js CDN（前端），SQLite（当前数据源）

---

## 文件职责总览

| 文件 | 职责 |
|------|------|
| `src/nl2sql_graph/services/db_adapter.py` (新) | 数据库适配器抽象基类 + SQLite 实现 |
| `src/nl2sql_graph/nodes/execute_sql.py` (改) | 从硬编码路径改为适配器执行 |
| `src/nl2sql_graph/nodes/fix_agent.py` (改) | PRAGMA 改为 adapter.get_columns() |
| `src/nl2sql_graph/nodes/lookup_values.py` (改) | SQLite 直连改为适配器 |
| `src/nl2sql_graph/graph_builder.py` (改) | build_graph 增加 db_adapter / on_clarify_ask 参数 |
| `src/nl2sql_graph/nodes/clarify.py` (改) | interrupt() 替换为回调函数 |
| `src/api.py` (新) | FastAPI 应用：3个端点 + SSE 管理 + 会话字典 |
| `static/chat.html` (新) | 单页前端：HTMX 对话 + Tabulator 表格 |

---

### Task 1: 创建数据库适配层

**Files:**
- Create: `src/nl2sql_graph/services/db_adapter.py`
- (无测试文件，适配器通过后续节点集成隐式验证)

- [ ] **Step 1: 编写适配器文件**

```python
"""数据库适配器抽象层 — 解耦执行引擎与具体数据库"""
from abc import ABC, abstractmethod
import sqlite3
from contextlib import closing


class BaseDBAdapter(ABC):
    """数据库适配器抽象基类"""

    @abstractmethod
    def execute(self, sql: str) -> tuple:
        """执行 SELECT 语句，返回 (列名列表, 行数据列表)"""
        ...

    @abstractmethod
    def get_columns(self, table_name: str) -> list[str]:
        """获取表的实际列名列表（供 fix_agent 列名校正用）"""
        ...

    @abstractmethod
    def table_exists(self, table_name: str) -> bool:
        """检查表是否存在"""
        ...


class SQLiteAdapter(BaseDBAdapter):
    """SQLite 适配器"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def execute(self, sql: str) -> tuple:
        with closing(sqlite3.connect(self.db_path)) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            headers = [d[0] for d in cur.description] if cur.description else []
            return (headers, rows)

    def get_columns(self, table_name: str) -> list[str]:
        try:
            with closing(sqlite3.connect(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute(f"PRAGMA table_info('{table_name}')")
                return [r[1] for r in cur.fetchall()]
        except Exception:
            return []

    def table_exists(self, table_name: str) -> bool:
        try:
            with closing(sqlite3.connect(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT 1 FROM {table_name} LIMIT 0")
                return True
        except Exception:
            return False
```

- [ ] **Step 2: 提交**

```bash
git add src/nl2sql_graph/services/db_adapter.py
git commit -m "feat: add database adapter abstraction layer"
```

---

### Task 2: 改造 execute_sql.py 使用适配器

**Files:**
- Modify: `src/nl2sql_graph/nodes/execute_sql.py`

- [ ] **Step 1: 修改 execute_sql 函数签名和实现**

当前 `execute_sql(state: dict) -> dict` 内部硬编码 `_DB_PATH`。改为从 state 中获取 adapter 实例，或通过闭包注入。

最简方案：在 `graph_builder.py` 中通过 lambda 绑定 adapter，execute_sql 节点从闭包接收 adapter。

修改 `execute_sql.py`，将 `execute_sql` 改为接受 adapter 参数的工厂函数：

```python
"""SQL 执行节点 -- 通过适配器在目标数据库上执行 state["sql"]"""
import re

_FORBIDDEN_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
    "REPLACE", "TRUNCATE", "ATTACH", "DETACH", "PRAGMA",
    "GRANT", "REVOKE", "BEGIN", "COMMIT", "ROLLBACK",
]

_FORBIDDEN_RE = re.compile(
    r'\b(' + '|'.join(_FORBIDDEN_KEYWORDS) + r')\b',
    re.IGNORECASE
)


def _is_select_only(sql: str) -> bool:
    stripped = sql.strip()
    if not stripped.upper().startswith("SELECT"):
        return False
    return _FORBIDDEN_RE.search(stripped) is None


def _clean_sql(sql: str) -> str:
    s = sql.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if len(lines) >= 3:
            s = "\n".join(lines[1:-1]).strip()
        else:
            s = s.strip("`").strip()
    return s


def make_execute_sql(adapter):
    """工厂函数：返回绑定 adapter 的 execute_sql 节点函数"""

    def execute_sql(state: dict) -> dict:
        raw = state.get("sql", "")
        sql = _clean_sql(raw) if raw else ""

        if not sql:
            return {"sql_error": "SQL 为空", "sql_result": None}

        if not _is_select_only(sql):
            return {"sql_error": "仅允许 SELECT 语句", "sql_result": None}

        try:
            headers, rows = adapter.execute(sql)
            if rows:
                result_lines = [",".join(str(v) for v in row) for row in rows[:20]]
                sql_result = "\n".join([",".join(headers)] + result_lines)
            else:
                sql_result = "(empty)"
            return {"sql_result": sql_result, "sql_error": None}
        except Exception as e:
            return {"sql_result": None, "sql_error": str(e)}

    return execute_sql
```

说明：原文件中 `_DB_PATH`、`sqlite3` 导入、`closing` 导入全部移除。

- [ ] **Step 2: 提交**

```bash
git add src/nl2sql_graph/nodes/execute_sql.py
git commit -m "refactor: make execute_sql adapter-driven via factory function"
```

---

### Task 3: 改造 fix_agent.py 使用适配器

**Files:**
- Modify: `src/nl2sql_graph/nodes/fix_agent.py`

- [ ] **Step 1: 修改 _gather_facts 函数**

当前 `_gather_facts` 内部 `with closing(sqlite3.connect(_DB_PATH))` 硬编码连接。改为使用 state 中传入的 adapter。

修改 `_gather_facts` 接受 adapter 参数：

```python
# 修改前 (行 79-128)
def _gather_facts(state: dict, error_type: str, error_detail: str | None) -> dict:
    ...
    with closing(sqlite3.connect(_DB_PATH)) as conn:
        cur = conn.cursor()
        for table_name in selected_names:
            raw_name = table_name.replace("main.", "", 1) if table_name.startswith("main.") else table_name
            try:
                cur.execute(f"PRAGMA table_info('{raw_name}')")
                rows = cur.fetchall()
                cols = [r[1] for r in rows]
            except Exception:
                cols = []
            columns_by_table[table_name] = cols
            all_columns.extend(cols)
    ...
```

改为：

```python
def _gather_facts(state: dict, error_type: str, error_detail: str | None, adapter) -> dict:
    ...
    selected_names = state.get("selected_names", [])
    for table_name in selected_names:
        raw_name = table_name.replace("main.", "", 1) if table_name.startswith("main.") else table_name
        cols = adapter.get_columns(raw_name)
        columns_by_table[table_name] = cols
        all_columns.extend(cols)
    ...
```

同时修改 `fix_agent` 主函数，从 state 的 `node_context` 或闭包获取 adapter。最简方案：state 中增加 `_adapter` 字段（内部使用，前端不感知）。

修改 `fix_agent(state: dict) -> dict` 内部调用 `_gather_facts` 处，传入 `state.get("_adapter")`：

```python
def fix_agent(state: dict) -> dict:
    adapter = state.get("_adapter")
    ...
    # 语义缺口路径不变
    # Step 3: 收集数据库事实
    facts = _gather_facts(state, error_type, error_detail, adapter)
    ...
    # ReAct 循环中重新获取事实时也传入 adapter
    facts = {**facts, **_gather_facts(state, new_type, new_detail, adapter)}
```

同时移除文件顶部的 `import sqlite3`、`from contextlib import closing`、`_DB_PATH = ...`。

- [ ] **Step 2: 检查 _test_execute 函数**

`_test_execute` 委托给 `_execute_sql_node`（即 `execute_sql` 节点），execute_sql 已改为工厂模式，所以 `_test_execute` 中的 `_execute_sql_node({"sql": sql})` 调用需保持一致。由于 `_execute_sql_node` 是 `make_execute_sql(adapter)` 返回的闭包，调用方式不变，内部 adapter 已注入。

当前导入行 `from .execute_sql import execute_sql as _execute_sql_node` 保持不变（`make_execute_sql` 在 graph_builder 中调用，导入的是工厂函数）。需要调整：改为在 graph_builder 中将已绑定的 execute_sql 函数注入 fix_agent。最简方案：fix_agent 也改为工厂函数。

将 `fix_agent` 改为 `make_fix_agent(adapter, execute_sql_node)` 工厂函数：

```python
def make_fix_agent(adapter, execute_sql_node):
    """工厂函数，返回绑定 adapter 和 execute_sql 的 fix_agent 节点"""
    
    def _test_execute(sql: str) -> tuple:
        result = execute_sql_node({"sql": sql})
        if result.get("sql_error"):
            return (False, result["sql_error"])
        return (True, None)
    
    def fix_agent(state: dict) -> dict:
        # ... 同原逻辑，但使用闭包中的 adapter 和 _test_execute
        ...
    
    return fix_agent
```

完整改造后 `fix_agent.py` 中：
- 移除 `import sqlite3`、`from contextlib import closing`、`_DB_PATH`
- `_gather_facts` 增加 adapter 参数
- `fix_agent` 改为 `make_fix_agent(adapter, execute_sql_node)` 工厂函数
- `_test_execute` 移入工厂闭包内

- [ ] **Step 3: 提交**

```bash
git add src/nl2sql_graph/nodes/fix_agent.py
git commit -m "refactor: make fix_agent adapter-driven via factory function"
```

---

### Task 4: 改造 lookup_values.py 使用适配器

**Files:**
- Modify: `src/nl2sql_graph/nodes/lookup_values.py`

- [ ] **Step 1: 改为工厂函数**

当前硬编码 `_DB_PATH` 和 `sqlite3.connect`。改为工厂函数：

```python
"""值发现节点 -- 根据NL关键词自动探查数据库中的实际枚举值"""
from ..rules.value_lookups import VALUE_LOOKUP_CONFIG


def make_lookup_values(adapter):
    """工厂函数：返回绑定 adapter 的 lookup_values 节点"""

    def lookup_values(state: dict) -> dict:
        nl = state.get("query", "")
        if not nl:
            return {"lookup_context": ""}

        nl_lower = nl.lower()
        results = []

        for cfg in VALUE_LOOKUP_CONFIG:
            if not any(kw.lower() in nl_lower for kw in cfg["keywords"]):
                continue
            try:
                headers, rows = adapter.execute(cfg["query"])
                if rows:
                    values = [r[0] for r in rows]
                    results.append(f"{cfg['label']}：{values}")
            except Exception:
                pass

        if results:
            context = "## 数据库实际值参考（运行时自动探查）\n" + "\n".join(
                f"- {r}" for r in results
            )
        else:
            context = ""

        return {"lookup_context": context}

    return lookup_values
```

- [ ] **Step 2: 提交**

```bash
git add src/nl2sql_graph/nodes/lookup_values.py
git commit -m "refactor: make lookup_values adapter-driven via factory function"
```

---

### Task 5: 改造 clarify.py 使用回调

**Files:**
- Modify: `src/nl2sql_graph/nodes/clarify.py`

- [ ] **Step 1: 将 interrupt() 替换为回调**

当前 `ask_user` 阶段的 `interrupt(payload)` 改为调用 state 中注入的回调函数。

最简方案：通过 state 中的 `_clarify_callback` 字段传递回调。clarify 节点检测到该字段存在且非 ask_user 阶段时，直接 skip。

修改 `clarify_query` 函数，在 `phase == "ask_user"` 分支中：

原代码（行 102-135）：
```python
elif phase == "ask_user":
    ...
    from langgraph.types import interrupt
    user_input = interrupt(payload)
    ...
```

改为：
```python
elif phase == "ask_user":
    round_num = state.get("clarify_round", 1)
    analysis = state.get("clarify_analysis", {})

    if round_num > _MAX_CLARIFY_ROUNDS:
        enhanced = analysis.get("enhanced_query", state.get("raw_query", ""))
        return {"query": enhanced, "clarify_phase": "confirmed"}

    callback = state.get("_clarify_callback")
    if callback is None:
        # 无回调 → 直接确认（兼容 CLI 模式 / skip_clarify）
        return {
            "query": analysis.get("enhanced_query", state.get("raw_query", "")),
            "clarify_phase": "confirmed",
        }

    payload = {
        "enhanced_query": analysis.get("enhanced_query", ""),
        "questions": analysis.get("questions", []),
        "round": round_num,
    }

    user_input = callback(payload)

    if not user_input.strip():
        return {
            "query": analysis.get("enhanced_query", state.get("raw_query", "")),
            "clarify_phase": "confirmed",
        }

    return {
        "clarify_phase": "process_response",
        "clarify_user_response": user_input,
    }
```

其他 phase 分支保持不变。

- [ ] **Step 2: 提交**

```bash
git add src/nl2sql_graph/nodes/clarify.py
git commit -m "refactor: replace interrupt() with callback injection in clarify node"
```

---

### Task 6: 改造 graph_builder.py

**Files:**
- Modify: `src/nl2sql_graph/graph_builder.py`

- [ ] **Step 1: 修改 build_graph 签名和节点构建逻辑**

`build_graph` 新增 `db_adapter` 和 `on_clarify_ask` 参数。使用工厂函数创建各节点实例。

```python
"""LangGraph 拓扑构建 & 编译"""
import time
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from .state import OverallState
from .nodes import clarify as clarify_node
from .nodes import recall, rerank, enforce, build_prompt, generate
from .nodes.execute_sql import make_execute_sql
from .nodes.validate_result import validate_result
from .nodes.fix_agent import make_fix_agent
from .nodes.lookup_values import make_lookup_values
from .nodes import semantic_validate as semantic_node


def _timed(name, fn):
    """包装节点函数，记录耗时"""
    def wrapper(state):
        t0 = time.perf_counter()
        result = fn(state)
        elapsed = round(time.perf_counter() - t0, 3)
        if isinstance(result, dict):
            timings = dict(state.get("node_timings", {}))
            key = name
            n = 2
            while key in timings:
                key = f"{name}#{n}"
                n += 1
            timings[key] = elapsed
            result["node_timings"] = timings
        print(f"  [{name}] {elapsed:.2f}s")
        return result
    return wrapper


def build_graph(collection, db_adapter, on_clarify_ask=None):
    """构建并编译 NL2SQL 状态图。

    Args:
        collection: Milvus Collection 实例
        db_adapter: BaseDBAdapter 实现（SQLiteAdapter 等）
        on_clarify_ask: 可选回调 (payload: dict) -> str
            传入时：clarify 节点触发回调获取用户响应（Web 模式）
            未传入：clarify 节点直接确认放行（CLI/批量模式）
    """
    # 工厂创建适配器节点
    execute_sql_node = make_execute_sql(db_adapter)
    lookup_values_node = make_lookup_values(db_adapter)
    fix_agent_node = make_fix_agent(db_adapter, execute_sql_node)

    builder = StateGraph(OverallState)

    # Stage 0: 查询澄清
    builder.add_node("clarify_query", _timed("clarify_query", clarify_node.clarify_query))

    # Stage 1: 召回 + 筛选
    recall_fn = lambda state: recall.recall_tables(state, collection=collection)
    builder.add_node("recall_tables", _timed("recall_tables", recall_fn))
    builder.add_node("rerank_tables", _timed("rerank_tables", rerank.rerank_tables))
    builder.add_node("enforce_rules", _timed("enforce_rules", enforce.enforce_rules))
    builder.add_node("build_prompt", _timed("build_prompt", build_prompt.build_prompt))
    builder.add_node("generate_sql", _timed("generate_sql", generate.generate_sql))

    # Stage 1.5: 值发现
    builder.add_node("lookup_values", _timed("lookup_values", lookup_values_node))

    # Stage 2.5: 语义校验
    builder.add_node("semantic_validate", _timed("semantic_validate", semantic_node.validate_semantics))

    # Stage 3: 执行 + 修复
    builder.add_node("execute_sql", _timed("execute_sql", execute_sql_node))
    builder.add_node("validate_result", _timed("validate_result", validate_result))
    builder.add_node("fix_agent", _timed("fix_agent", fix_agent_node))

    # --- 边 ---
    builder.add_edge(START, "clarify_query")
    builder.add_conditional_edges(
        "clarify_query",
        lambda state: state.get("clarify_phase", "init"),
        {
            "init": "clarify_query",
            "ask_user": "clarify_query",
            "process_response": "clarify_query",
            "confirmed": "recall_tables",
            "skipped": "recall_tables",
        }
    )

    builder.add_edge("recall_tables", "rerank_tables")
    builder.add_edge("rerank_tables", "enforce_rules")
    builder.add_edge("enforce_rules", "lookup_values")
    builder.add_edge("lookup_values", "build_prompt")
    builder.add_edge("build_prompt", "generate_sql")

    builder.add_edge("generate_sql", "semantic_validate")
    builder.add_conditional_edges(
        "semantic_validate",
        lambda state: (
            "pass" if state.get("semantic_pass", True) or state.get("semantic_retry_count", 0) >= 1
            else "fail"
        ),
        {"pass": "execute_sql", "fail": "fix_agent"}
    )

    builder.add_edge("execute_sql", "validate_result")
    builder.add_conditional_edges(
        "validate_result",
        lambda state: state["route"],
        {"end": END, "retry": "fix_agent"}
    )

    builder.add_conditional_edges(
        "fix_agent",
        lambda state: "generate_sql" if state.get("fix_source") == "semantic_gap" else "execute_sql",
        {"generate_sql": "generate_sql", "execute_sql": "execute_sql"}
    )

    return builder.compile(checkpointer=InMemorySaver())
```

- [ ] **Step 2: 提交**

```bash
git add src/nl2sql_graph/graph_builder.py
git commit -m "refactor: inject db_adapter and clarify callback into graph builder"
```

---

### Task 7: 创建 FastAPI 应用

**Files:**
- Create: `src/api.py`

- [ ] **Step 1: 安装依赖**

```bash
pip install fastapi uvicorn sse-starlette
```

- [ ] **Step 2: 编写 api.py**

```python
"""HqSQL Web API — FastAPI + SSE 流式推送"""
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
from nl2sql_graph.state import OverallState

DB_PATH = r"E:\sql数据集\accounting.sqlite"
STATIC_DIR = "static"

# SSE 事件缓冲区（session_id → Queue）
_event_queues: dict[str, Queue] = {}
# 会话状态存储（session_id → OverallState）
_sessions: dict[str, dict] = {}
# 会话最后活跃时间
_session_last_active: dict[str, float] = {}
# 等待 clarify 响应的会话 {session_id: threading.Event}
_pending_clarify: dict[str, threading.Event] = {}

SESSION_TTL = 86400  # 24小时

# 全局单例
_collection: Collection = None
_db_adapter: SQLiteAdapter = None
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

        # 推送进度
        _emit_event(session_id, "progress", {"step": "recall", "detail": "正在理解查询并检索相关表结构..."})

        result = _graph.invoke(state, config)
        _sessions[session_id] = result

        # 推送最终结果
        sql = result.get("sql", "")
        sql_result = result.get("sql_result", "")
        sql_error = result.get("sql_error")
        timings = result.get("node_timings", {})

        if sql_error:
            _emit_event(session_id, "error", {"code": "EXEC_FAILED", "detail": sql_error, "sql": sql})
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
        _emit_event(session_id, "error", {"code": "INTERNAL", "detail": str(e)})


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
    _graph = build_graph(_collection, _db_adapter, on_clarify_ask=None)  # 回调在 state 中动态注入
    yield
    connections.disconnect("default")


app = FastAPI(lifespan=lifespan)
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

    # 确保 SSE 队列存在
    if session_id not in _event_queues:
        _event_queues[session_id] = Queue()

    # 启动后台线程执行 LangGraph
    thread = threading.Thread(target=_run_graph, args=(session_id, message), daemon=True)
    thread.start()

    return JSONResponse({"session_id": session_id, "status": "processing"})


@app.get("/api/chat/stream")
async def chat_stream(session_id: str, request: Request):
    """SSE 端点：前端 EventSource 订阅"""

    # 确保队列存在
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
                # 超时发送 keepalive
                yield f": keepalive\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
```

- [ ] **Step 3: 提交**

```bash
git add src/api.py
git commit -m "feat: add FastAPI application with SSE streaming and session management"
```

---

### Task 8: 创建前端页面

**Files:**
- Create: `static/chat.html`

- [ ] **Step 1: 编写 chat.html**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HqSQL - 智能数据查询</title>
<script src="https://unpkg.com/htmx.org@2.0.4"></script>
<link href="https://unpkg.com/tabulator-tables@6.3.0/dist/css/tabulator.min.css" rel="stylesheet">
<script src="https://unpkg.com/tabulator-tables@6.3.0/dist/js/tabulator.min.js"></script>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family: -apple-system, "Microsoft YaHei", sans-serif; background:#f3f4f6; height:100vh; display:flex; flex-direction:column; }
  .header { background:#4f46e5; color:#fff; padding:12px 20px; font-size:18px; font-weight:600; }
  #chat-area { flex:1; overflow-y:auto; padding:16px 20px; display:flex; flex-direction:column; gap:12px; }
  .msg { max-width:85%; padding:10px 14px; border-radius:12px; font-size:14px; line-height:1.6; }
  .msg.user { align-self:flex-end; background:#4f46e5; color:#fff; border-radius:12px 12px 0 12px; }
  .msg.system { align-self:flex-start; background:#fff; border:1px solid #e5e7eb; border-radius:12px 12px 12px 0; }
  .msg.progress { align-self:flex-start; background:#f0f0ff; border-left:3px solid #818cf8; border-radius:4px; padding:8px 12px; font-size:13px; color:#4f46e5; }
  .msg.clarify { align-self:flex-start; background:#fff; border:1px solid #fbbf24; border-radius:12px 12px 12px 0; max-width:85%; }
  .msg.error { align-self:flex-start; background:#fef2f2; border:1px solid #fca5a5; color:#991b1b; }
  .sql-block { background:#1e1e1e; color:#d4d4d4; padding:10px 12px; border-radius:6px; font-family:monospace; font-size:12px; overflow-x:auto; margin-top:8px; white-space:pre-wrap; }
  .result-section { margin-top:12px; }
  .result-header { font-weight:600; font-size:13px; color:#6b7280; margin-bottom:6px; }
  .input-area { padding:12px 20px; background:#fff; border-top:1px solid #e5e7eb; display:flex; gap:10px; align-items:center; }
  .input-area input { flex:1; padding:10px 14px; border:1px solid #d1d5db; border-radius:8px; font-size:14px; outline:none; }
  .input-area input:focus { border-color:#4f46e5; }
  .input-area button { background:#4f46e5; color:#fff; border:none; padding:10px 24px; border-radius:8px; cursor:pointer; font-size:14px; }
  .input-area button:hover { background:#4338ca; }
  .input-area button:disabled { background:#9ca3af; cursor:not-allowed; }
  .tabulator { font-size:13px; }
  .clarify-form { margin-top:8px; display:flex; flex-direction:column; gap:8px; }
  .clarify-form input { padding:8px 10px; border:1px solid #d1d5db; border-radius:6px; font-size:13px; }
  .clarify-form button { align-self:flex-end; background:#f59e0b; color:#fff; border:none; padding:6px 16px; border-radius:6px; cursor:pointer; }
  .tip { font-size:11px; color:#9ca3af; margin-left:8px; }
</style>
</head>
<body>
<div class="header">HqSQL · 智能数据查询</div>
<div id="chat-area"></div>
<div class="input-area" id="input-area">
  <input type="text" id="query-input" placeholder="输入您的查询，例如：本月各部门的费用对比" autofocus
    hx-post="/api/chat"
    hx-trigger="keydown[keyCode==13]"
    hx-vals='js:{session_id: document.getElementById("session-id").value, message: document.getElementById("query-input").value, clarify_response: ""}'
    hx-on::after-request="handleSend(event)">
  <button id="send-btn"
    hx-post="/api/chat"
    hx-include="#query-input, #session-id"
    hx-vals='js:{message: document.getElementById("query-input").value, clarify_response: ""}'
    hx-on::after-request="handleSend(event)">
    发送
  </button>
  <span class="tip">Enter 发送</span>
</div>
<input type="hidden" id="session-id" value="">
<input type="hidden" id="pending-clarify" value="">

<script>
let currentSessionId = "";
let eventSource = null;
let tabulatorTable = null;

function appendChat(html) {
  document.getElementById("chat-area").insertAdjacentHTML("beforeend", html);
  document.getElementById("chat-area").scrollTop = document.getElementById("chat-area").scrollHeight;
}

function appendUserMsg(text) {
  appendChat(`<div class="msg user">${escapeHtml(text)}</div>`);
}

function appendSystemMsg(text) {
  appendChat(`<div class="msg system">${text}</div>`);
}

function appendProgress(detail) {
  appendChat(`<div class="msg progress">${detail}</div>`);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function connectSSE(sessionId) {
  if (eventSource) eventSource.close();
  eventSource = new EventSource(`/api/chat/stream?session_id=${sessionId}`);

  eventSource.addEventListener("progress", (e) => {
    const data = JSON.parse(e.data);
    const icon = data.step === "recall" ? "📋" : "⚡";
    appendProgress(`${icon} ${data.detail}`);
  });

  eventSource.addEventListener("clarify", (e) => {
    const data = JSON.parse(e.data);
    document.getElementById("pending-clarify").value = "1";
    const questionsHtml = data.questions.map((q, i) =>
      `<div>${i+1}. ${escapeHtml(q)}</div>`
    ).join("");
    appendChat(`
      <div class="msg clarify">
        <div style="font-weight:600;font-size:12px;color:#92400e;margin-bottom:6px;">🤔 帮您确认几个信息：</div>
        <div style="font-size:13px;margin-bottom:8px;">${questionsHtml}</div>
        <div style="font-size:11px;color:#9ca3af;margin-bottom:6px;">${escapeHtml(data.enhanced_query)}</div>
        <div class="clarify-form">
          <input type="text" id="clarify-input" placeholder="在此输入补充说明，或留空回车确认">
          <button onclick="submitClarify()">确认</button>
        </div>
      </div>
    `);
    document.getElementById("clarify-input")?.focus();
  });

  eventSource.addEventListener("result", (e) => {
    const data = JSON.parse(e.data);
    let html = '<div class="msg system">';

    // SQL 代码块
    if (data.sql) {
      html += `<div style="font-weight:600;font-size:12px;color:#6b7280;margin-bottom:4px;">生成的 SQL</div>`;
      html += `<div class="sql-block">${escapeHtml(data.sql)}</div>`;
    }

    // 结果表格
    if (data.columns && data.columns.length > 0) {
      const tableId = "result-table-" + Date.now();
      html += `<div class="result-section">`;
      html += `<div class="result-header">查询结果 <span style="color:#10b981;font-weight:normal">（${data.row_count} 行，${data.total_time}s）</span></div>`;
      html += `<div id="${tableId}" class="tabulator"></div>`;
      html += `</div>`;
      appendChat(html);

      // 初始化 Tabulator
      const tableData = data.rows.map(row => {
        const obj = {};
        data.columns.forEach((col, i) => { obj[col] = row[i] || ""; });
        return obj;
      });
      tabulatorTable = new Tabulator(`#${tableId}`, {
        data: tableData,
        layout: "fitColumns",
        pagination: "local",
        paginationSize: 15,
        columns: data.columns.map(col => ({ title: col, field: col, headerFilter: true })),
      });
    } else if (data.row_count === 0) {
      html += `<div style="margin-top:8px;color:#9ca3af;">查询结果为空</div>`;
      appendChat(html);
    } else {
      appendChat(html);
    }

    // 恢复输入区
    document.getElementById("send-btn").disabled = false;
    document.getElementById("query-input").disabled = false;
    document.getElementById("pending-clarify").value = "";
    eventSource.close();
  });

  eventSource.addEventListener("error", (e) => {
    const data = JSON.parse(e.data);
    let html = `<div class="msg system error">`;
    html += `<div style="font-weight:600;">⚠ 执行出错</div>`;
    html += `<div style="margin-top:4px;">${escapeHtml(data.detail || "未知错误")}</div>`;
    if (data.sql) {
      html += `<div style="font-weight:600;font-size:12px;color:#6b7280;margin:8px 0 4px;">最后生成的 SQL</div>`;
      html += `<div class="sql-block">${escapeHtml(data.sql)}</div>`;
    }
    html += `</div>`;
    appendChat(html);
    document.getElementById("send-btn").disabled = false;
    document.getElementById("query-input").disabled = false;
    document.getElementById("pending-clarify").value = "";
    eventSource.close();
  });

  eventSource.onerror = () => {
    // SSE 连接异常（服务器断线等），静默处理
    console.log("SSE connection closed");
  };
}

async function handleSend(event) {
  const input = document.getElementById("query-input");
  const text = input.value.trim();
  if (!text) return;

  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      session_id: currentSessionId,
      message: text,
      clarify_response: "",
    }),
  });
  const result = await resp.json();
  currentSessionId = result.session_id;
  document.getElementById("session-id").value = currentSessionId;

  appendUserMsg(text);
  input.value = "";
  document.getElementById("send-btn").disabled = true;
  input.disabled = true;

  connectSSE(currentSessionId);
}

async function submitClarify() {
  const input = document.getElementById("clarify-input");
  const reply = input ? input.value.trim() : "";

  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      session_id: currentSessionId,
      message: "",
      clarify_response: reply,
    }),
  });

  // 关闭澄清表单，等待后续 SSE 事件
  const clarifyCard = document.querySelector(".msg.clarify");
  if (clarifyCard) {
    clarifyCard.innerHTML = `<div style="font-size:13px;color:#166534;">✅ 已提交${reply ? '：' + escapeHtml(reply) : '确认'}</div>`;
    clarifyCard.style.border = "1px solid #86efac";
    clarifyCard.style.background = "#f0fdf4";
  }
  document.getElementById("pending-clarify").value = "";
}

// 回车提交澄清
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && document.getElementById("pending-clarify").value === "1") {
    e.preventDefault();
    submitClarify();
  }
});
</script>
</body>
</html>
```

- [ ] **Step 2: 提交**

```bash
git add static/chat.html
git commit -m "feat: add chat frontend with HTMX + Tabulator.js"
```

---

### Task 9: 更新 nl2sql.py 兼容新接口

**Files:**
- Modify: `src/nl2sql.py`

- [ ] **Step 1: 适配 build_graph 新签名**

```python
"""NL2SQL v2 -- LangGraph 入口脚本（CLI 兼容）"""
import uuid
from pymilvus import Collection, connections
from src.nl2sql_graph.graph_builder import build_graph
from src.nl2sql_graph.state import OverallState
from src.nl2sql_graph.services.db_adapter import SQLiteAdapter


DB_PATH = r"E:\sql数据集\accounting.sqlite"


def run_query(graph, query: str, skip_clarify: bool = True) -> dict:
    state: OverallState = {
        "query": query,
        "raw_query": "",
        "skip_clarify": skip_clarify,
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
        "_clarify_callback": None,
        "_adapter": SQLiteAdapter(DB_PATH),
    }
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    return graph.invoke(state, config)


def main():
    connections.connect(host='localhost', port='19530', db_name='HqSQL')
    collection = Collection("tables")
    collection.load()

    db_adapter = SQLiteAdapter(DB_PATH)
    graph = build_graph(collection, db_adapter)  # 无回调 → clarify 直接确认

    test_queries = [
        "How are my sales year to date compared to last year?",
        "in aug this year, what was our largest expense?",
        "What was my expense by products Last 12 months？",
        "what products are selling less than last month",
    ]

    for query in test_queries:
        print("=" * 70)
        print(f"Query: {query}")
        print("-" * 70)

        result = run_query(graph, query)

        for t in result["all_tables"]:
            print(f"  {t['table_name']:30s} score={t['score']:.4f}")

        print(f"\n  Stage 1: {result['selected_names']}")
        if result.get("forced_names"):
            print(f"  [ENFORCE] {result['forced_names']}")
        if result.get("warnings"):
            print(f"  [WARN] {result['warnings']}")

        print(f"\nGenerated SQL:\n{result['sql']}\n")

        if result.get("sql_error"):
            print(f"  [SQL ERROR] {result['sql_error']}")
        if result.get("sql_result"):
            lines = result["sql_result"].split("\n")
            print(f"  Result ({len(lines)-1} rows):")
            for line in lines[:6]:
                print(f"    {line}")

        timings = result.get("node_timings", {})
        if timings:
            total = sum(timings.values())
            print(f"\n  --- Node Timings ---")
            for name, sec in timings.items():
                pct = sec / total * 100 if total > 0 else 0
                print(f"  {name:25s} {sec:7.2f}s  ({pct:4.0f}%)")
            print(f"  {'Total':25s} {total:7.2f}s")

    connections.disconnect("default")


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 提交**

```bash
git add src/nl2sql.py
git commit -m "refactor: update CLI entry point for new graph builder signature"
```

---

### Task 10: 集成验证

- [ ] **Step 1: 启动 FastAPI 服务**

```bash
cd E:\PyProjects\HqSQL
python -m uvicorn src.api:app --host 0.0.0.0 --port 8000
```

预期输出：
```
INFO:     Started server process [xxxxx]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

- [ ] **Step 2: 浏览器验证**

打开 `http://localhost:8000`，输入测试查询 "上个月收入多少？"

验证：
1. 页面正常加载，输入框可见
2. 发送查询后看到进度提示
3. SQL 代码块正确渲染
4. 结果表格可排序、分页
5. 澄清流程可交互（输入模糊查询如"卖得好的"）

- [ ] **Step 3: CLI 兼容性验证**

```bash
python src/nl2sql.py
```

预期：现有 CLI 测试查询正常运行，输出 SQL + 结果 + 耗时统计。

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "chore: integration verification complete"
```
