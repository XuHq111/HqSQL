"""LangGraph 拓扑构建 & 编译"""
import time
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from .state import OverallState
from .nodes import clarify as clarify_node
from .nodes import recall, rerank, enforce, build_prompt, generate
from .nodes import execute_sql as execute_node
from .nodes import validate_result as validate_node
from .nodes import fix_sql as fix_node
from .nodes import lookup_values as lookup_node


def _timed(name, fn):
    """包装节点函数，记录耗时到 state["node_timings"]（重试时累加不覆盖）"""
    def wrapper(state):
        t0 = time.perf_counter()
        result = fn(state)
        elapsed = round(time.perf_counter() - t0, 3)
        if isinstance(result, dict):
            timings = dict(state.get("node_timings", {}))
            # 重试循环中同一节点可能多次执行，用 #N 后缀去重
            key = name
            n = 2
            while key in timings:
                key = f"{name}#{n}"
                n += 1
            timings[key] = elapsed
            result["node_timings"] = timings
        print(f"  [{name}] {elapsed:.2f}s")
        return result
    return wrapper


def build_graph(collection):
    """构建并编译 NL2SQL 状态图"""
    builder = StateGraph(OverallState)

    # Stage 0: 查询澄清（中断式多轮对话）
    builder.add_node("clarify_query", _timed("clarify_query", clarify_node.clarify_query))

    # Stage 1: 原有 5 个节点
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
    builder.add_node("lookup_values", _timed("lookup_values", lookup_node.lookup_values))

    # Stage 0 入口 + 条件路由
    builder.add_edge(START, "clarify_query")
    builder.add_conditional_edges(
        "clarify_query",
        lambda state: state.get("clarify_phase", "init"),
        {
            "init": "clarify_query",
            "ask_user": "clarify_query",
            "process_response": "clarify_query",
            "confirmed": "recall_tables",
            "skipped": "recall_tables",
        }
    )


    # Stage 1 线性边
    builder.add_edge("recall_tables", "rerank_tables")
    builder.add_edge("rerank_tables", "enforce_rules")
    builder.add_edge("enforce_rules", "lookup_values")
    builder.add_edge("lookup_values", "build_prompt")
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

    return builder.compile(checkpointer=InMemorySaver())
