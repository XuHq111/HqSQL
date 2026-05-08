"""Stage 2 Prompt 组装节点"""
from ..rules.dependencies import DEPENDENCY_RULES
from ..rules.sql_rules import CORE_RULES, RULE_TRIGGERS, CONDITIONAL_RULES, match_rules


def build_prompt(state: dict) -> dict:
    """仅用筛选后表的完整 schema + 依赖规则 + SQL 规则，构建 Stage 2 prompt"""
    selected_tables = [t for t in state["all_tables"] if t['table_name'] in state["selected_names"]]
    # 保持原始召回顺序
    name_order = {name: i for i, name in enumerate(state["selected_names"])}
    selected_tables.sort(key=lambda t: name_order[t['table_name']])

    lines = ["## 用户查询\n", state["query"], "\n"]

    lookup = state.get("lookup_context", "")
    if lookup:
        lines.append(lookup)
        lines.append("")

    lines.append(f"## 相关表（共{len(selected_tables)}张）\n")

    for i, t in enumerate(selected_tables):
        lines.append(f"### 表{i+1}: {t['table_name']}")
        lines.append(t['schema'])
        lines.append("")

    lines.append(DEPENDENCY_RULES)
    # 动态注入 SQL 规则：Layer1 核心约束 + Layer2 条件规则（按查询关键词匹配）
    rules_text = CORE_RULES.strip()
    matched = match_rules(state["query"], RULE_TRIGGERS, CONDITIONAL_RULES)
    if matched:
        rules_text += "\n" + matched
    lines.append(rules_text)
    lines.append("## 任务\n")
    lines.append("根据上述表结构和依赖规则生成 SQLite SQL 语句。只输出 SQL，不要解释。")

    return {"prompt": "\n".join(lines)}
