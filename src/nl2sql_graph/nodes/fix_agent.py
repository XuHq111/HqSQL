"""SQL 修复 Agent 节点 -- 代码诊断 + Markdown Skill + ReAct 循环智能修复 SQL 错误"""
import os
import re
import sqlite3
import difflib
from contextlib import closing

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from ..services.llm import plus_model
from ..rules.sql_rules import CORE_RULES
from .execute_sql import execute_sql as _execute_sql_node

# ============================================================
# 常量
# ============================================================
_DB_PATH = r"E:\sql数据集\accounting.sqlite"
_MAX_INNER_RETRIES = 2

_SKILL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills")
_SKILL_MAP = {
    "column_not_found": "fix_column.md",
    "empty_result":     "fix_empty.md",
    "syntax_error":     "fix_syntax.md",
    "semantic_gap":     "fix_semantic.md",
}
_DEFAULT_SKILL = "fix_general.md"

# ============================================================
# 管道：接收已构建好的 prompt，调用 plus 模型
# ============================================================
_prompt = ChatPromptTemplate.from_messages([("human", "{text}")])
_chain = _prompt | plus_model | StrOutputParser()


# ============================================================
# 诊断函数
# ============================================================

def _diagnose(state: dict) -> tuple:
    """从 state 中提取 sql_error 或 gap_list 并诊断类型。"""
    gap_list = state.get("gap_list")
    if gap_list:
        return ("semantic_gap", gap_list)

    error = state.get("sql_error")
    if not error:
        return ("empty_result", None)

    m = re.search(r'no such column[:\s]+(\S+)', error, re.IGNORECASE)
    if m:
        return ("column_not_found", m.group(1))

    if re.search(r'(syntax error|near\s+"|unrecognized)', error, re.IGNORECASE):
        return ("syntax_error", error[:200])

    return ("unknown", error[:200])


def _diagnose_from_error(error: str) -> tuple:
    """从错误字符串直接诊断（用于 ReAct 循环中重新诊断）。"""
    if not error:
        return ("empty_result", None)

    m = re.search(r'no such column[:\s]+(\S+)', error, re.IGNORECASE)
    if m:
        return ("column_not_found", m.group(1))

    if re.search(r'(syntax error|near\s+"|unrecognized)', error, re.IGNORECASE):
        return ("syntax_error", error[:200])

    return ("unknown", error[:200])


# ============================================================
# 事实收集函数
# ============================================================

def _gather_facts(state: dict, error_type: str, error_detail: str | None) -> dict:
    """查询 SQLite 获取表结构事实，为修复提供准确信息。"""
    facts: dict = {}
    selected_names = state.get("selected_names", [])

    columns_by_table: dict[str, list] = {}
    all_columns: list[str] = []

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

    facts["columns_by_table"] = columns_by_table

    lines = []
    for tname, cols in columns_by_table.items():
        lines.append(f"表 {tname} 的实际列名：{', '.join(cols)}")
    facts["actual_columns"] = "\n".join(lines)

    facts["wrong_col"] = ""
    facts["suggested_col"] = ""
    facts["auto_fix_sql"] = None
    facts["gap_list"] = error_detail if error_type == "semantic_gap" else []

    if error_type == "column_not_found" and error_detail:
        wrong_col = error_detail
        col_name = wrong_col.split(".")[-1] if "." in wrong_col else wrong_col
        facts["wrong_col"] = col_name

        unique_cols = list(dict.fromkeys(all_columns))
        matches = difflib.get_close_matches(col_name, unique_cols, n=1, cutoff=0.6)
        if matches:
            suggested = matches[0]
            facts["suggested_col"] = suggested
            sql = state.get("sql", "")
            pattern = r'\b' + re.escape(col_name) + r'\b'
            fixed_sql = re.sub(pattern, suggested, sql)
            facts["auto_fix_sql"] = fixed_sql
        else:
            facts["suggested_col"] = "(未找到相似列名，请检查表结构)"

    return facts


# ============================================================
# Skill 加载
# ============================================================

def _load_skill(filename: str) -> str:
    """读取 Markdown Skill 文件内容。"""
    path = os.path.join(_SKILL_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ============================================================
# Schema 上下文构建
# ============================================================

def _build_schema_context(state: dict) -> str:
    """从 state 中提取已选表的 DDL schema，按选中顺序排列。"""
    selected = [t for t in state.get("all_tables", []) if t["table_name"] in state.get("selected_names", [])]
    name_order = {n: i for i, n in enumerate(state.get("selected_names", []))}
    selected.sort(key=lambda t: name_order.get(t["table_name"], 999))
    return "\n\n".join(
        f"### {t['table_name']}\n{t['schema']}" for t in selected
    )


# ============================================================
# 缺口清单文本构建
# ============================================================

def _build_gap_list_text(gap_list: list) -> str:
    """将缺口清单格式化为 LLM 可读文本。"""
    if not gap_list:
        return ""
    lines = ["以下需求项在当前 SQL 中缺失："]
    for g in gap_list:
        lines.append(f"- [需求{g.get('req_id', '?')}] {g.get('desc', '')}")
        if g.get("suggestion"):
            lines.append(f"  建议: {g.get('suggestion')}")
    return "\n".join(lines)


def _build_requirement_context(state: dict) -> str:
    """构建需求清单摘要，让修复 LLM 知道必须保留哪些语义。"""
    reqs = state.get("requirement_items", [])
    if not reqs:
        return ""
    lines = ["## 原始查询需求清单（修复时必须保留）"]
    for r in reqs:
        lines.append(f"- [{r.get('type', '?')}] {r.get('desc', '')}")
    return "\n".join(lines)


# ============================================================
# Prompt 构建
# ============================================================

def _build_prompt(state: dict, skill_md: str, facts: dict, history: str = "") -> str:
    """构建发送给 LLM 的修复 prompt。

    将 skill.md 中的占位符填充为实际值，如有历史记录则前置。
    """
    format_vars = {
        "query": state.get("query", ""),
        "sql": state.get("sql", ""),
        "error": state.get("sql_error", ""),
        "schema_context": _build_schema_context(state),
        "lookup_context": state.get("lookup_context", ""),
        "actual_columns": facts.get("actual_columns", ""),
        "wrong_col": facts.get("wrong_col", ""),
        "suggested_col": facts.get("suggested_col", ""),
        "core_rules": CORE_RULES.strip(),
        "gap_list_text": _build_gap_list_text(facts.get("gap_list", [])),
        "requirement_context": _build_requirement_context(state),
    }

    prompt = skill_md.format(**format_vars)

    if history:
        prompt = (
            "## 警告：以下为此前失败的修复尝试，请避免重复同样错误\n\n"
            + history
            + "\n---\n\n"
            + prompt
        )

    return prompt


# ============================================================
# SQL 清理
# ============================================================

def _clean_md(sql: str) -> str:
    """去除 LLM 输出中可能包裹的 Markdown 代码围栏标记。"""
    s = sql.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if len(lines) >= 3:
            s = "\n".join(lines[1:-1]).strip()
        else:
            s = s.strip("`").strip()
    return s


# ============================================================
# SQL 测试执行
# ============================================================

def _test_execute(sql: str) -> tuple:
    """委托 execute_sql 节点测试执行，复用其安全守卫（SELECT-only 检查）。"""
    result = _execute_sql_node({"sql": sql})
    if result.get("sql_error"):
        return (False, result["sql_error"])
    return (True, None)


# ============================================================
# 返回值构建
# ============================================================

def _build_return(fixed_sql: str, fix_source: str = "syntax") -> dict:
    """构建节点返回值，直接输出修正后的 SQL + 修复来源标记。"""
    return {"sql": fixed_sql, "fix_source": fix_source}


# ============================================================
# 主入口：fix_agent
# ============================================================

def fix_agent(state: dict) -> dict:
    """SQL 修复 Agent 节点 -- 智能诊断并修复 SQL 错误。

    流程：
    1. 诊断错误类型（语义缺口 > 语法错误 > 列名错误 > 空结果）
    2. 语义缺口路径：加载 fix_semantic.md，注入 gap_list，LLM 补全
    3. 语法错误路径：收集数据库事实 → 选 Skill → 自动修复 / ReAct 循环
    """
    # Step 1: 诊断错误类型
    error_type, error_detail = _diagnose(state)

    # Step 2: 语义缺口 -- 快速路径
    if error_type == "semantic_gap":
        facts = {"gap_list": error_detail}
        skill_md = _load_skill("fix_semantic.md")
        prompt = _build_prompt(state, skill_md, facts, "")
        try:
            fixed_sql = _chain.invoke({"text": prompt})
            fixed_sql = _clean_md(fixed_sql)
        except Exception:
            return _build_return(state.get("sql", ""), "semantic_gap")
        return _build_return(fixed_sql, "semantic_gap")

    # Step 3: 收集数据库事实
    facts = _gather_facts(state, error_type, error_detail)

    # Step 4: 选择 Skill 文件
    skill_path = _SKILL_MAP.get(error_type, _DEFAULT_SKILL)

    # Step 5: 高置信度列名自动修复（零 LLM 调用）
    if error_type == "column_not_found" and facts.get("auto_fix_sql"):
        return _build_return(facts["auto_fix_sql"], "syntax")

    # Step 6: ReAct 循环
    history = ""
    current_skill_md = _load_skill(skill_path)
    facts["current_error_type"] = error_type
    facts["error_detail"] = error_detail
    fixed_sql = state.get("sql", "")

    for attempt in range(_MAX_INNER_RETRIES):
        prompt = _build_prompt(state, current_skill_md, facts, history)

        try:
            fixed_sql = _chain.invoke({"text": prompt})
            fixed_sql = _clean_md(fixed_sql)
        except Exception:
            break

        test_ok, test_error = _test_execute(fixed_sql)
        if test_ok:
            return _build_return(fixed_sql, "syntax")

        # 重新诊断
        new_type, new_detail = _diagnose_from_error(test_error)
        history += (
            f"## 第{attempt + 1}轮修复尝试\n"
            f"修正SQL:\n```\n{fixed_sql}\n```\n"
            f"新错误: {test_error}\n"
        )

        if new_type == facts["current_error_type"]:
            facts["error_detail"] = new_detail
        else:
            facts["current_error_type"] = new_type
            facts["error_detail"] = new_detail
            new_path = _SKILL_MAP.get(new_type, _DEFAULT_SKILL)
            current_skill_md = _load_skill(new_path)
            history += f"(错误类型变为 {new_type}，已切换修复策略)\n"
            facts = {**facts, **_gather_facts(state, new_type, new_detail)}

    return _build_return(fixed_sql, "syntax")
