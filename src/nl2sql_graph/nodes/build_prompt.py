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

    # 指标语义层：命中指标时注入已核定口径（硬约束）
    metric_ctx = state.get("metric_context", "")
    if metric_ctx:
        lines.append("## 已核定指标口径（硬约束，不得修改或删除）\n")
        lines.append(metric_ctx)
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
    lines.append("""请分两步完成：

**第一步：需求分解**
仔细阅读用户查询，将其拆解为可逐条核对的原子需求。每条需求包含：
- id: 序号
- desc: 需求描述（用中文，直接可对照 SQL 检查）
- type: 类型（filter | join | aggregate | subquery | having | order_limit | case_when | column）

**第二步：SQL 生成**
对照需求清单逐条实现，确保每一条需求在 SQL 中都有对应的子句。
若存在「## 已核定指标口径（硬约束，不得修改或删除）」一节：SELECT 的聚合列与聚合函数、JOIN 关系、口径过滤条件必须与口径完全一致，只允许在其基础上补充时间范围、排序、限量等额外条件。

输出格式（严格遵守，不要用 markdown 代码块包裹 JSON）：
{
  "requirements": [
    {"id": 1, "desc": "限定 businessID = 2", "type": "filter"},
    {"id": 2, "desc": "仅筛选交易类型为发票的记录", "type": "filter"}
  ],
  "sql": "SELECT ..."
}

注意：
- requirements 数组必须完整，不遗漏用户查询中的任何需求
- sql 中的每一段（WHERE/JOIN/GROUP BY/HAVING/ORDER BY/LIMIT/子查询/CASE WHEN）都应对应 requirements 中的一条或多条
- 只输出上述 JSON，不要额外解释""")

    return {"prompt": "\n".join(lines)}
