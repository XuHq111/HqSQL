"""SQL 修正节点 — LLM 根据错误信息修正 SQL"""
from ..services.llm import call_llm
from ..rules.sql_rules import SQL_RULES


_FIX_TEMPLATE = """## 用户原始查询
{query}

## 上一轮生成的 SQL（执行失败）
{sql}

## 执行报错
{error}

## 相关表结构
{schema_context}

## SQL 生成规则
{sql_rules}

## 修复任务
以上 SQL 执行时 {error_desc}，请修正。只输出修正后的 SQLite SQL，不要解释。"""


def _clean_md(sql: str) -> str:
    s = sql.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if len(lines) >= 3:
            s = "\n".join(lines[1:-1]).strip()
        else:
            s = s.strip("`").strip()
    return s


def fix_sql(state: dict) -> dict:
    error = state.get("sql_error")
    sql = state.get("sql", "")

    error_desc = "报错如上" if error else "返回了空结果，请检查 WHERE 条件和 JOIN 逻辑"

    selected = [t for t in state["all_tables"] if t["table_name"] in state["selected_names"]]
    name_order = {n: i for i, n in enumerate(state["selected_names"])}
    selected.sort(key=lambda t: name_order[t["table_name"]])
    schema_context = "\n\n".join(
        f"### {t['table_name']}\n{t['schema']}" for t in selected
    )

    fix_prompt = _FIX_TEMPLATE.format(
        query=state["query"],
        sql=sql,
        error=error or "空结果",
        error_desc=error_desc,
        schema_context=schema_context,
        sql_rules=SQL_RULES,
    )

    try:
        fixed_sql = call_llm(fix_prompt)
        fixed_sql = _clean_md(fixed_sql)
    except Exception as e:
        # LLM 调用失败：保留原 SQL，让重试循环自然耗尽
        return {"sql": sql}

    augmented_prompt = (
        state["prompt"]
        + "\n\n## 上一轮 SQL 执行失败，已修正\n"
        + f"### 原 SQL（错误）\n```\n{sql}\n```\n"
        + f"### 执行错误\n{error or '空结果'}\n"
        + f"### 修正后 SQL\n```\n{fixed_sql}\n```\n"
        + "请基于修正版本和完整规则重新生成最终 SQL。只输出 SQL。"
    )

    return {
        "sql": fixed_sql,
        "prompt": augmented_prompt,
    }
