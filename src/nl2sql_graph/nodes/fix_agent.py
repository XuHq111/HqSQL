"""SQL 修复 Agent 节点 — 代码诊断 + Markdown Skill + ReAct 循环智能修复 SQL 错误

与 fix_sql.py 的区别：
- fix_sql.py：单次 LLM 调用修复（简单、直接）
- fix_agent.py：代码诊断 → 查数据库事实 → 选 Skill → 可选自动修复 → ReAct 循环（智能、多轮）
"""
import os
import re
import sqlite3
import difflib
from contextlib import closing
from ..services.llm import call_llm
from ..rules.sql_rules import CORE_RULES

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
}
_DEFAULT_SKILL = "fix_general.md"

# ============================================================
# 诊断函数
# ============================================================

def _diagnose(state: dict) -> tuple:
    """从 state 中提取 sql_error 并诊断错误类型。

    Returns:
        (error_type, error_detail)
        error_type: "column_not_found" | "syntax_error" | "empty_result" | "unknown"
        error_detail: 捕获的详细信息（列名、错误摘要等），可能为 None
    """
    error = state.get("sql_error")
    if not error:
        return ("empty_result", None)

    # 列名不存在
    m = re.search(r'no such column[:\s]+(\S+)', error, re.IGNORECASE)
    if m:
        return ("column_not_found", m.group(1))

    # 语法错误
    if re.search(r'(syntax error|near\s+"|unrecognized)', error, re.IGNORECASE):
        return ("syntax_error", error[:200])

    return ("unknown", error[:200])


def _diagnose_from_error(error: str) -> tuple:
    """从错误字符串直接诊断（用于 ReAct 循环中重新诊断）。

    Args:
        error: 错误字符串（如 SQLite 异常消息）

    Returns:
        (error_type, error_detail)
    """
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
    """查询 SQLite 获取表结构事实，为修复提供准确信息。

    - 对所有 selected 表执行 PRAGMA table_info 获取真实列名
    - 对 column_not_found 错误，执行模糊匹配尝试自动修复

    Args:
        state: 全局状态
        error_type: 错误类型
        error_detail: 错误详情（如错误的列名）

    Returns:
        facts dict，包含 columns_by_table、actual_columns 等字段
    """
    facts: dict = {}
    selected_names = state.get("selected_names", [])

    columns_by_table: dict[str, list] = {}
    all_columns: list[str] = []

    with closing(sqlite3.connect(_DB_PATH)) as conn:
        cur = conn.cursor()
        for table_name in selected_names:
            # 去掉 "main." 前缀以执行 PRAGMA
            raw_name = table_name.replace("main.", "", 1) if table_name.startswith("main.") else table_name
            try:
                cur.execute(f"PRAGMA table_info('{raw_name}')")
                rows = cur.fetchall()
                cols = [r[1] for r in rows]  # r[1] 是列名
            except Exception:
                cols = []
            columns_by_table[table_name] = cols
            all_columns.extend(cols)

    facts["columns_by_table"] = columns_by_table

    # 构建可读的实际列名文本
    lines = []
    for tname, cols in columns_by_table.items():
        lines.append(f"表 {tname} 的实际列名：{', '.join(cols)}")
    facts["actual_columns"] = "\n".join(lines)

    # 列名错误：模糊匹配 → 自动修复
    facts["wrong_col"] = ""
    facts["suggested_col"] = ""
    facts["auto_fix_sql"] = None

    if error_type == "column_not_found" and error_detail:
        wrong_col = error_detail
        # 去掉表别名前缀（如 t1.Credit → Credit）
        col_name = wrong_col.split(".")[-1] if "." in wrong_col else wrong_col
        facts["wrong_col"] = col_name

        # 去重后做模糊匹配
        unique_cols = list(dict.fromkeys(all_columns))  # 保序去重
        matches = difflib.get_close_matches(col_name, unique_cols, n=1, cutoff=0.6)
        if matches:
            suggested = matches[0]
            facts["suggested_col"] = suggested
            # 正则替换 SQL 中的错误列名
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
    """读取 Markdown Skill 文件内容。

    Args:
        filename: Skill 文件名（如 "fix_column.md"）

    Returns:
        Skill 文件的完整文本内容
    """
    path = os.path.join(_SKILL_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ============================================================
# Schema 上下文构建
# ============================================================

def _build_schema_context(state: dict) -> str:
    """从 state 中提取已选表的 DDL schema，按选中顺序排列。

    Returns:
        格式化的表结构文本
    """
    selected = [t for t in state.get("all_tables", []) if t["table_name"] in state.get("selected_names", [])]
    name_order = {n: i for i, n in enumerate(state.get("selected_names", []))}
    selected.sort(key=lambda t: name_order.get(t["table_name"], 999))
    return "\n\n".join(
        f"### {t['table_name']}\n{t['schema']}" for t in selected
    )


# ============================================================
# Prompt 构建
# ============================================================

def _build_prompt(state: dict, skill_md: str, facts: dict, history: str = "") -> str:
    """构建发送给 LLM 的修复 prompt。

    将 skill.md 中的占位符填充为实际值，如有历史记录则前置。

    Args:
        state: 全局状态
        skill_md: Skill 文件内容（含 {placeholder} 占位符）
        facts: 事实字典（_gather_facts 的返回值）
        history: 前几轮修复的历史记录文本，空字符串表示第一轮

    Returns:
        完整的 prompt 字符串
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
    """去除 LLM 输出中可能包裹的 Markdown 代码围栏标记。

    Args:
        sql: LLM 原始输出

    Returns:
        清理后的纯 SQL 文本
    """
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
    """在 SQLite 上测试执行 SQL。

    Args:
        sql: 待测试的 SQL 语句

    Returns:
        (True, None) 如果执行成功
        (False, error_string) 如果执行失败
    """
    if not sql or not sql.strip():
        return (False, "SQL 为空")

    try:
        with closing(sqlite3.connect(_DB_PATH)) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            cur.fetchall()
        return (True, None)
    except Exception as e:
        return (False, str(e))


# ============================================================
# 返回值构建
# ============================================================

def _build_return(state: dict, fixed_sql: str, facts: dict | None = None) -> dict:
    """构建节点返回值，包含修正后的 SQL 和增强 prompt。

    增强 prompt 会追加到原 prompt 后面，供下游 generate_sql 节点使用。

    Args:
        state: 全局状态
        fixed_sql: 修正后的 SQL
        facts: 事实字典（当前未使用，保留以兼容调用方）

    Returns:
        {"sql": fixed_sql, "prompt": augmented_prompt}
    """
    sql = state.get("sql", "")
    error = state.get("sql_error", "")

    augmented_prompt = (
        state.get("prompt", "")
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


# ============================================================
# 主入口：fix_agent
# ============================================================

def fix_agent(state: dict) -> dict:
    """SQL 修复 Agent 节点 — 智能诊断并修复 SQL 错误。

    流程：
    1. 诊断错误类型（正则，零 LLM 调用）
    2. 收集数据库事实（PRAGMA，零 LLM 调用）
    3. 选择对应的 Markdown Skill 文件
    4. 高置信度列名错误 → 直接自动修复（零 LLM 调用）
    5. 启动 ReAct 循环（最多 2 轮）：
       a. 读取 skill.md + 填充占位符 → prompt
       b. LLM 生成修复 SQL
       c. 在 SQLite 上测试执行
       d. 成功 → 返回
       e. 失败 → 重新诊断新错误类型
          - 同类型 → 累积历史，继续用原 skill
          - 不同类型 → 切换 skill，重建 prompt

    Args:
        state: 全局状态字典（OverallState 的运行时表示）

    Returns:
        {"sql": fixed_sql, "prompt": augmented_prompt} 用于后续 generate_sql 节点
        如果 LLM 调用失败，返回 {"sql": state.get("sql", "")}
    """
    # Step 1: 诊断错误类型
    error_type, error_detail = _diagnose(state)

    # Step 2: 收集数据库事实
    facts = _gather_facts(state, error_type, error_detail)

    # Step 3: 选择 Skill 文件
    skill_path = _SKILL_MAP.get(error_type, _DEFAULT_SKILL)

    # Step 4: 高置信度列名自动修复（零 LLM 调用）
    if error_type == "column_not_found" and facts.get("auto_fix_sql"):
        return _build_return(state, facts["auto_fix_sql"], facts)

    # Step 5: ReAct 循环
    history = ""
    current_skill_md = _load_skill(skill_path)
    facts["current_error_type"] = error_type
    facts["error_detail"] = error_detail
    fixed_sql = state.get("sql", "")

    for attempt in range(_MAX_INNER_RETRIES):
        prompt = _build_prompt(state, current_skill_md, facts, history)

        try:
            fixed_sql = call_llm(prompt)
            fixed_sql = _clean_md(fixed_sql)
        except Exception:
            break  # LLM 调用失败，返回当前状态

        test_ok, test_error = _test_execute(fixed_sql)
        if test_ok:
            return _build_return(state, fixed_sql, facts)

        # 重新诊断
        new_type, new_detail = _diagnose_from_error(test_error)
        history += (
            f"## 第{attempt + 1}轮修复尝试\n"
            f"修正SQL:\n```\n{fixed_sql}\n```\n"
            f"新错误: {test_error}\n"
        )

        if new_type == facts["current_error_type"]:
            # 同类型错误，保留当前 skill 继续尝试
            facts["error_detail"] = new_detail
        else:
            # 不同类型错误 → 切换 skill
            facts["current_error_type"] = new_type
            facts["error_detail"] = new_detail
            new_path = _SKILL_MAP.get(new_type, _DEFAULT_SKILL)
            current_skill_md = _load_skill(new_path)
            history += f"(错误类型变为 {new_type}，已切换修复策略)\n"
            facts = {**facts, **_gather_facts(state, new_type, new_detail)}

    # 循环耗尽：返回最后一轮的修复结果
    return _build_return(state, fixed_sql, facts)
