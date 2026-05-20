"""会话日志模块 -- 以 JSON 格式记录完整流水线链路"""
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
    """单次会话的 JSON 日志记录器"""

    def __init__(self, session_id: str, nl_query: str):
        self.session_id = session_id
        self.start_time = time.time()
        self.start_ts = datetime.now().isoformat()
        self.nodes: list[dict] = []
        self.llm_calls: list[dict] = []
        self._node_seq = 0
        self._llm_seq = 0

        # 按日期建子目录
        date_dir = datetime.now().strftime("%Y-%m-%d")
        self._dir = os.path.join(LOG_DIR, date_dir)
        os.makedirs(self._dir, exist_ok=True)

        self._path = os.path.join(self._dir, f"{session_id}.json")
        self._lock = threading.Lock()

        # 写入骨架
        self._write_skeleton(nl_query)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def log_node_start(self, name: str, state_snapshot: dict):
        """节点开始前调用。记录 seq + 入参快照。"""
        with self._lock:
            self._node_seq += 1
            entry = {
                "seq": self._node_seq,
                "name": name,
                "input": _sanitize_state(state_snapshot),
                "elapsed": None,
                "output": None,
                "error": None,
            }
            self.nodes.append(entry)
        return self._node_seq

    def log_node_end(self, seq: int, output: dict, elapsed: float, error: str = None):
        """节点结束后调用。补写耗时 + 输出。"""
        with self._lock:
            for entry in self.nodes:
                if entry["seq"] == seq:
                    entry["output"] = _sanitize_state(output) if isinstance(output, dict) else str(output)
                    entry["elapsed"] = round(elapsed, 3)
                    if error:
                        entry["error"] = error
                    break
        self._flush()

    def log_llm(self, model: str, node: str, prompt: str, response: str, elapsed: float):
        """记录一次 LLM 调用。"""
        with self._lock:
            self._llm_seq += 1
            self.llm_calls.append({
                "seq": self._llm_seq,
                "model": model,
                "node": node,
                "elapsed": round(elapsed, 3),
                "prompt": prompt,
                "response": response,
            })
        self._flush()

    def finalize(self, sql: str = "", sql_result: str = "", sql_error: str = ""):
        """会话结束，写入完整 JSON。"""
        end_time = time.time()
        total = round(end_time - self.start_time, 3)

        node_timings = {}
        for n in self.nodes:
            if n["elapsed"] is not None:
                node_timings[n["name"]] = round(n["elapsed"], 3)

        doc = {
            "session_id": self.session_id,
            "start_time": self.start_ts,
            "end_time": datetime.now().isoformat(),
            "total_elapsed": total,
            "nl_query": self._nl_query,
            "nodes": self.nodes,
            "llm_calls": self.llm_calls,
            "result": {
                "sql": sql,
                "sql_result": sql_result,
                "sql_error": sql_error,
            },
            "summary": {
                "total_time": total,
                "node_count": len(self.nodes),
                "llm_call_count": len(self.llm_calls),
                "node_timings": node_timings,
            },
        }

        with self._lock:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)

        # 从全局注册表移除
        with _lock:
            _loggers.pop(self.session_id, None)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _write_skeleton(self, nl_query: str):
        self._nl_query = nl_query

    def _flush(self):
        """增量写入当前 JSON 快照（方便实时查看）。"""
        try:
            doc = {
                "session_id": self.session_id,
                "start_time": self.start_ts,
                "nl_query": self._nl_query,
                "nodes": self.nodes,
                "llm_calls": self.llm_calls,
            }
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


# ------------------------------------------------------------------
# 全局 API（供节点和 graph_builder 使用）
# ------------------------------------------------------------------

def get_logger(state: dict) -> SessionLogger | None:
    """从 state 中获取当前会话的 logger。返回 None 表示无日志（CLI 模式）。"""
    session_id = state.get("_clarify_session")
    if not session_id:
        return None
    with _lock:
        return _loggers.get(session_id)


def create_logger(session_id: str, nl_query: str) -> SessionLogger:
    """创建并注册会话日志器。"""
    logger = SessionLogger(session_id, nl_query)
    with _lock:
        _loggers[session_id] = logger
    return logger


def _sanitize_state(d: dict) -> dict:
    """精简 state 快照 -- 移除不可序列化的字段和大体积数据。"""
    skip_keys = {"_clarify_session", "all_tables", "prompt", "sql", "sql_result"}
    out = {}
    for k, v in d.items():
        if k in skip_keys:
            continue
        if isinstance(v, (str, int, float, bool, type(None), list, dict)):
            out[k] = v
    return out
