"""SQL 执行节点 — 在 accounting.sqlite 上执行 state["sql"]"""
import re
import sqlite3
from contextlib import closing

_DB_PATH = r"E:\sql数据集\accounting.sqlite"

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


def execute_sql(state: dict) -> dict:
    raw = state.get("sql", "")
    sql = _clean_sql(raw) if raw else ""

    if not sql:
        return {"sql_error": "SQL 为空", "sql_result": None}

    if not _is_select_only(sql):
        return {"sql_error": "仅允许 SELECT 语句，检测到非查询操作，请重新生成纯 SELECT", "sql_result": None}

    try:
        with closing(sqlite3.connect(_DB_PATH)) as conn:
            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()
            if rows:
                headers = [d[0] for d in cursor.description]
                result_lines = [",".join(str(v) for v in row) for row in rows[:20]]
                sql_result = "\n".join([",".join(headers)] + result_lines)
            else:
                sql_result = "(empty)"
        return {"sql_result": sql_result, "sql_error": None}
    except Exception as e:
        return {"sql_result": None, "sql_error": str(e)}
