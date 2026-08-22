"""SQL 修复 Agent 节点 -- 代码诊断 + Markdown Skill + ReAct 循环智能修复 SQL 错误"""
import os
import re
import difflib

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from ..services.llm import plus_model
from ..rules.sql_rules import CORE_RULES
# ============================================================
# 常量
# ============================================================
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

def _gather_facts(state: dict, error_type: str, error_detail: str | None, adapter) -> dict:
    """通过适配器获取表结构事实，为修复提供准确信息。"""
    facts: dict = {}
    selected_names = state.get("selected_names", [])

    columns_by_table: dict[str, list] = {}
    all_columns: list[str] = []

    for table_name in selected_names:
        raw_name = table_name.replace("main.", "", 1) if table_name.startswith("main.") else table_name
        cols = adapter.get_columns(raw_name)
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
        "query": state.get("query") or "",
        "sql": state.get("sql") or "",
        "error": state.get("sql_error") or "",
        "schema_context": _build_schema_context(state),
        "lookup_context": state.get("lookup_context") or "",
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
# 返回值构建
# ============================================================

def _build_return(fixed_sql: str, fix_source: str = "syntax") -> dict:
    """构建节点返回值，直接输出修正后的 SQL + 修复来源标记。"""
    return {"sql": fixed_sql, "fix_source": fix_source}


# ============================================================
# 修复结果 schema 复检（程序化，零 LLM 成本）
# ============================================================

_SQL_TABLE_RE = re.compile(
    r'\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_.]*)(?:\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*))?',
    re.IGNORECASE,
)
_SQL_QUALIFIED_COL_RE = re.compile(
    r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\b'
)


def _norm_table(name: str) -> str:
    """规范化表名：去掉 main. 前缀（SQLite 默认库前缀不影响校验）"""
    return name[5:] if name.startswith("main.") else name


def _extract_tables(sql: str) -> list:
    """提取 SQL 中的 (表名, 别名) 列表"""
    out = []
    for m in _SQL_TABLE_RE.finditer(sql or ""):
        name, alias = m.group(1), m.group(2)
        out.append((name, alias))
    return out


def _schema_check(sql: str, adapter, original_sql: str) -> list:
    """复检修复后的 SQL：
    1. 不得丢失原 SQL 中的表（防止降级为 SELECT * 全表查询）
    2. 引用的表和限定列名必须真实存在
    返回问题列表（空列表 = 通过）
    """
    if not sql:
        return ["修复产物为空的 SQL"]
    issues = []

    new_tables = _extract_tables(sql)
    new_table_names = {t for t, _ in new_tables}

    # 1. 表集不丢失
    for orig in {_norm_table(t) for t, _ in _extract_tables(original_sql)}:
        if orig not in new_table_names and orig not in {_norm_table(t) for t in new_table_names}:
            issues.append(f"丢失了原 SQL 中的表 {orig}")

    # 2. 表存在性
    for t in new_table_names:
        try:
            if not adapter.table_exists(_norm_table(t)):
                issues.append(f"表不存在: {t}")
        except Exception as e:
            issues.append(f"表存在性检查失败: {t} ({e})")

    # 3. 限定列名（alias.column）必须存在于对应表中
    aliases = {}
    for name, alias in new_tables:
        aliases[alias or name] = name
    for m in _SQL_QUALIFIED_COL_RE.finditer(sql):
        a, col = m.groups()
        tbl = aliases.get(a)
        if tbl:
            try:
                cols = adapter.get_columns(_norm_table(tbl))
                if col not in cols:
                    issues.append(f"列不存在: {a}.{col}")
            except Exception:
                pass

    return issues


# ============================================================
# 主入口：make_fix_agent 工厂函数
# ============================================================

def make_fix_agent(adapter, execute_sql_node):
    """工厂函数，返回绑定 adapter 和 execute_sql 的 fix_agent 节点"""

    def _test_execute(sql: str) -> tuple:
        result = execute_sql_node({"sql": sql})
        if result.get("sql_error"):
            return (False, result["sql_error"])
        return (True, None)

    def _verify_fixed(sql: str, original_sql: str) -> list:
        """修复验收：可执行 + schema 复检（表不丢失、列真实存在）"""
        issues = _schema_check(sql, adapter, original_sql)
        if issues:
            return issues
        ok, err = _test_execute(sql)
        if not ok:
            return [f"执行失败: {err}"]
        return []

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
                return _build_return(state.get("sql") or "", "semantic_gap")
            # 复检：补全不得引入坏 SQL；不过关则放弃本次修复，保持原 SQL（让上层如实报错）
            original_sql = state.get("sql") or ""
            if not _verify_fixed(fixed_sql, original_sql):
                return _build_return(fixed_sql, "semantic_gap")
            return _build_return(original_sql, "semantic_gap")

        # Step 3: 收集数据库事实
        facts = _gather_facts(state, error_type, error_detail, adapter)

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

            # 修复验收：schema 复检（表不丢失、列真实存在）+ 可执行
            verify_issues = _verify_fixed(fixed_sql, state.get("sql") or "")
            if not verify_issues:
                return _build_return(fixed_sql, "syntax")

            # 执行测试用于下一轮诊断（schema 问题也能反映到错误信息）
            test_ok, test_error = _test_execute(fixed_sql)
            history += (
                f"## 第{attempt + 1}轮修复尝试\n"
                f"修正SQL:\n```\n{fixed_sql}\n```\n"
                f"校验未通过: {'; '.join(verify_issues)}\n"
                f"新错误: {test_error or '（可执行但 schema 复检未通过）'}\n"
            )

            # 重新诊断并切换修复策略
            new_type, new_detail = _diagnose_from_error(test_error)
            if new_type == facts["current_error_type"]:
                facts["error_detail"] = new_detail
            else:
                facts["current_error_type"] = new_type
                facts["error_detail"] = new_detail
                new_path = _SKILL_MAP.get(new_type, _DEFAULT_SKILL)
                current_skill_md = _load_skill(new_path)
                history += f"(错误类型变为 {new_type}，已切换修复策略)\n"
                facts = {**facts, **_gather_facts(state, new_type, new_detail, adapter)}

            if new_type == facts["current_error_type"]:
                facts["error_detail"] = new_detail
            else:
                facts["current_error_type"] = new_type
                facts["error_detail"] = new_detail
                new_path = _SKILL_MAP.get(new_type, _DEFAULT_SKILL)
                current_skill_md = _load_skill(new_path)
                history += f"(错误类型变为 {new_type}，已切换修复策略)\n"
                facts = {**facts, **_gather_facts(state, new_type, new_detail, adapter)}

        return _build_return(fixed_sql, "syntax")

    return fix_agent
