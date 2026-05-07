"""LangGraph 拓扑构建 & 编译"""
from langgraph.graph import StateGraph, START, END
from .state import OverallState
from .nodes import recall, rerank, enforce, build_prompt, generate


def build_graph(collection) -> StateGraph:
    """构建并编译 NL2SQL 状态图"""
    builder = StateGraph(OverallState)

    # 注册节点：recall 节点需要 collection 参数，用闭包注入
    builder.add_node("recall_tables", lambda state: recall.recall_tables(state, collection=collection))
    builder.add_node("rerank_tables", rerank.rerank_tables)
    builder.add_node("enforce_rules", enforce.enforce_rules)
    builder.add_node("build_prompt", build_prompt.build_prompt)
    builder.add_node("generate_sql", generate.generate_sql)

    # 线性拓扑
    builder.add_edge(START, "recall_tables")
    builder.add_edge("recall_tables", "rerank_tables")
    builder.add_edge("rerank_tables", "enforce_rules")
    builder.add_edge("enforce_rules", "build_prompt")
    builder.add_edge("build_prompt", "generate_sql")
    builder.add_edge("generate_sql", END)

    return builder.compile()
