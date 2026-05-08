"""LangGraph 拓扑构建 & 编译"""
import time
from langgraph.graph import StateGraph, START, END
from .state import OverallState
from .nodes import recall, rerank, enforce, build_prompt, generate
from .nodes import execute_sql as execute_node
from .nodes import validate_result as validate_node
from .nodes import fix_sql as fix_node


def _timed(name, fn):
    """包装节点函数，记录耗时到 state["node_timings"]"""
    def wrapper(state):
        t0 = time.perf_counter()
        result = fn(state)
        elapsed = round(time.perf_counter() - t0, 3)
        # 将本节点耗时写入 state 的 node_timings，同时透传 result 中的其他 state 更新
        if isinstance(result, dict):
            timings = state.get("node_timings", {})
            timings[name] = elapsed
            result["node_timings"] = timings
        print(f"  [{name}] {elapsed:.2f}s")
        return result
    return wrapper


def build_graph(collection):
    """构建并编译 NL2SQL 状态图"""
    builder = StateGraph(OverallState)

    # 原有 5 个节点
    recall_fn = lambda state: recall.recall_tables(state, collection=collection)
    builder.add_node("recall_tables", _timed("recall_tables", recall_fn))
    builder.add_node("rerank_tables", _timed("rerank_tables", rerank.rerank_tables))
    builder.add_node("enforce_rules", _timed("enforce_rules", enforce.enforce_rules))
    builder.add_node("build_prompt", _timed("build_prompt", build_prompt.build_prompt))
    builder.add_node("generate_sql", _timed("generate_sql", generate.generate_sql))

    # Phase 2: 新增 3 个节点
    builder.add_node("execute_sql", _timed("execute_sql", execute_node.execute_sql))
    builder.add_node("validate_result", _timed("validate_result", validate_node.validate_result))
    builder.add_node("fix_sql", _timed("fix_sql", fix_node.fix_sql))

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
