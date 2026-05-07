"""LangGraph 拓扑构建 & 编译"""
from langgraph.graph import StateGraph, START, END
from .state import OverallState
from .nodes import recall, rerank, enforce, build_prompt, generate
from .nodes import execute_sql as execute_node
from .nodes import validate_result as validate_node
from .nodes import fix_sql as fix_node


def build_graph(collection) -> StateGraph:
    """构建并编译 NL2SQL 状态图"""
    builder = StateGraph(OverallState)

    # 原有 5 个节点
    builder.add_node("recall_tables", lambda state: recall.recall_tables(state, collection=collection))
    builder.add_node("rerank_tables", rerank.rerank_tables)
    builder.add_node("enforce_rules", enforce.enforce_rules)
    builder.add_node("build_prompt", build_prompt.build_prompt)
    builder.add_node("generate_sql", generate.generate_sql)

    # Phase 2: 新增 3 个节点
    builder.add_node("execute_sql", execute_node.execute_sql)
    builder.add_node("validate_result", validate_node.validate_result)
    builder.add_node("fix_sql", fix_node.fix_sql)

    # 原有线性边
    builder.add_edge(START, "recall_tables")
    builder.add_edge("recall_tables", "rerank_tables")
    builder.add_edge("rerank_tables", "enforce_rules")
    builder.add_edge("enforce_rules", "build_prompt")
    builder.add_edge("build_prompt", "generate_sql")

    # Phase 2: 扩展管道
    builder.add_edge("generate_sql", "execute_sql")
    builder.add_edge("execute_sql", "validate_result")

    # 条件路由：validate_result.route → END 或 fix_sql
    builder.add_conditional_edges(
        "validate_result",
        lambda state: state["route"],
        {
            "end": END,
            "retry": "fix_sql",
        }
    )

    # fix_sql 修正后回到 generate_sql 重跑完整 Stage 2
    builder.add_edge("fix_sql", "generate_sql")

    return builder.compile()
