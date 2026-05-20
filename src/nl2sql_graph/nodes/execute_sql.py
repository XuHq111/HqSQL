"""SQL 执行节点 -- 通过适配器在目标数据库上执行 state["sql"]"""
import re

from ..services.db_adapter import BaseDBAdapter

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
    upper = stripped.upper()
    # 允许 SELECT 或 WITH（CTE 公共表表达式）开头的查询
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
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


def make_execute_sql(adapter: BaseDBAdapter):
    """工厂函数：返回绑定 adapter 的 execute_sql 节点函数"""

    def execute_sql(state: dict) -> dict:
        raw = state.get("sql", "")
        sql = _clean_sql(raw) if raw else ""

        if not sql:
            return {"sql_error": "SQL 为空", "sql_result": None}

        if not _is_select_only(sql):
            return {"sql_error": "仅允许 SELECT 语句，检测到非查询操作，请重新生成纯 SELECT", "sql_result": None}

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
