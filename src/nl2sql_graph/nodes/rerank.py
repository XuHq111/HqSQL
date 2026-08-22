"""Stage 1: LLM 重排序筛选节点"""
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from ..services.llm import flash_model, get_short_description
from ..rules.dependencies import DEPENDENCY_RULES

_TEMPLATE = """## 任务：根据用户查询，从候选表中选择需要的表

## 重要：以下依赖规则是硬性约束，触发的表必须入选，不可绕过！

## 用户查询
{query}

## 候选表（按向量相似度排序，分数越高越相关）
{tables_text}

{dependency_rules}

## 输出要求

只输出需要的表名，每行一个（如 main.master_txn_table），不要任何解释。
已选中的表在后续生成 SQL 时可以用完整 schema，未选中的表不可用。"""

_prompt = ChatPromptTemplate.from_messages([("human", _TEMPLATE)])
_chain = _prompt | flash_model | StrOutputParser()


def rerank_tables(state: dict) -> dict:
    """LLM 根据表名+分数+简短描述+依赖规则，筛选真正需要的表"""
    # 上游 guard：召回为空时不盲猜，直接透传 error 由图终止
    if not state.get("all_tables"):
        return {
            "selected_names": [],
            "rerank_raw": "",
            "warnings": state.get("warnings", []),
        }
    # 构建候选表文本
    table_lines = []
    for i, t in enumerate(state["all_tables"]):
        short = get_short_description(t['schema'])
        table_lines.append(f"### {i+1}. {t['table_name']}（相似度: {t['score']}）")
        table_lines.append(short)
        table_lines.append("")

    response = _chain.invoke({
        "query": state["query"],
        "tables_text": "\n".join(table_lines),
        "dependency_rules": DEPENDENCY_RULES,
    })

    # 解析：提取所有 main.xxx 表名
    all_names = {t['table_name'] for t in state["all_tables"]}
    selected = []
    for line in response.strip().split('\n'):
        name = line.strip()
        if name in all_names and name not in selected:
            selected.append(name)

    warnings = state.get("warnings", [])
    # 防御：如果解析失败，回退到分数最高的前4张表
    if not selected:
        selected = [t['table_name'] for t in state["all_tables"][:4] if t['score'] > 0.5]
        warnings.append(f"Stage 1 解析失败，回退到: {selected}")

    return {
        "selected_names": selected,
        "rerank_raw": response,
        "warnings": warnings,
    }
