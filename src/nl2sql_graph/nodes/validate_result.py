"""结果校验节点 — 检查执行结果，写路由信号"""
RETRY_LIMIT = 3


def validate_result(state: dict) -> dict:
    sql_error = state.get("sql_error")
    retry_count = state.get("retry_count", 0)
    sql_result = state.get("sql_result")

    # 成功条件：无错误 + 有结果 + 非空
    ok = sql_error is None and sql_result and sql_result != "(empty)"

    if ok:
        return {"route": "end"}

    if retry_count < RETRY_LIMIT:
        return {"route": "retry", "retry_count": retry_count + 1}

    # 超过重试上限
    warnings = list(state.get("warnings", []))
    reason = sql_error or "空结果"
    warnings.append(f"SQL 修复失败（重试{RETRY_LIMIT}次），最后原因: {reason}")
    return {"route": "end", "warnings": warnings}
