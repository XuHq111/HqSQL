"""会话日志模块 -- JSONL 格式，每行一个 JSON 对象对应一个节点或事件"""
import json
import os
import time
import threading
from datetime import datetime

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "logs")

# 全局注册表：session_id → SessionLogger
_loggers: dict = {}
_lock = threading.Lock()


class SessionLogger:
    """单次会话的 JSONL 日志记录器。每行一个 JSON 对象。"""

    def __init__(self, session_id: str, nl_query: str):
        self.session_id = session_id
        self._nl_query = nl_query
        self._start_time = time.time()
        self._seq = 0
        self._llm_buffer: list[dict] = []   # 当前节点的 LLM 调用缓存
        self._lock = threading.Lock()

        date_dir = datetime.now().strftime("%Y-%m-%d")
        self._dir = os.path.join(LOG_DIR, date_dir)
        os.makedirs(self._dir, exist_ok=True)
        self._path = os.path.join(self._dir, f"{session_id}.jsonl")

        # 写入首行：会话开始
        self._write_line({
            "type": "session_start",
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
            "nl_query": nl_query,
        })

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def log_node_start(self, name: str):
        """节点开始前调用。清空 LLM 缓冲区，记录 seq。"""
        self._seq += 1
        self._llm_buffer = []
        return self._seq

    def log_node_end(self, seq: int, name: str, input_state: dict, output: dict, elapsed: float, error: str = None):
        """节点结束后调用。写入一行完整的节点 JSON。"""
        line = {
            "type": "node",
            "seq": seq,
            "timestamp": datetime.now().isoformat(),
            "name": name,
            "elapsed": round(elapsed, 3),
            "input": _sanitize(input_state),
            "output": _sanitize(output) if isinstance(output, dict) else str(output),
            "llm_calls": self._llm_buffer,
        }
        if error:
            line["error"] = error
        self._write_line(line)

    def log_llm(self, model: str, node: str, prompt: str, response: str, elapsed: float):
        """记录一次 LLM 调用（缓存到当前节点）。"""
        self._llm_buffer.append({
            "model": model,
            "node": node,
            "elapsed": round(elapsed, 3),
            "prompt": prompt[:4000],
            "response": response[:4000],
        })

    def finalize(self, sql: str = "", sql_result: str = "", sql_error: str = ""):
        """会话结束，写入末行。"""
        total = round(time.time() - self._start_time, 3)
        self._write_line({
            "type": "session_end",
            "timestamp": datetime.now().isoformat(),
            "total_elapsed": total,
            "node_count": self._seq,
            "sql": sql,
            "sql_result": sql_result[:5000],
            "sql_error": sql_error,
        })
        with _lock:
            _loggers.pop(self.session_id, None)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _write_line(self, obj: dict):
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------
# 全局 API
# ------------------------------------------------------------------

def get_logger(state: dict) -> SessionLogger | None:
    session_id = state.get("_clarify_session")
    if not session_id:
        return None
    with _lock:
        return _loggers.get(session_id)


def create_logger(session_id: str, nl_query: str) -> SessionLogger:
    logger = SessionLogger(session_id, nl_query)
    with _lock:
        _loggers[session_id] = logger
    return logger


# ------------------------------------------------------------------
# 内部工具
# ------------------------------------------------------------------

_SKIP_KEYS = {"_clarify_session", "all_tables", "prompt", "sql", "sql_result", "node_timings"}


def _sanitize(d: dict) -> dict:
    """精简 state 快照：移除不可序列化字段和大体积数据。"""
    if not isinstance(d, dict):
        return {}
    out = {}
    for k, v in d.items():
        if k in _SKIP_KEYS:
            continue
        if isinstance(v, (str, int, float, bool, type(None), list, dict)):
            out[k] = v
    return out
