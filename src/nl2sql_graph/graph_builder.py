"""LangGraph 拓扑构建 & 编译"""
import time
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from .state import OverallState
from .nodes import clarify as clarify_node
from .nodes import recall, rerank, enforce, build_prompt, generate
from .nodes import semantic_map as semantic_map_node
from .nodes import metric_expand as metric_expand_node
from .nodes.execute_sql import make_execute_sql
from .nodes.validate_result import validate_result
from .nodes.fix_agent import make_fix_agent
from .nodes.lookup_values import make_lookup_values
from .nodes import semantic_validate as semantic_node

# 全局回调注册表：session_id → clarify 回调函数
# 由 api.py 在 graph.invoke() 前注册，clarify 节点通过 state["_clarify_session"] 查找
_clarify_callbacks: dict = {}


def _build_timed(on_progress):
    """节点包装工厂：记录耗时 + 日志 + 触发节点级进度回调"""
    def _timed(name, fn):
        def wrapper(state):
            if on_progress:
                try:
                    on_progress(name, state.get("_clarify_session"))
                except Exception:
                    pass
            # 设置线程局部变量（供 services/llm.py 日志使用）
            session_id = state.get("_clarify_session")
            from .services.llm import _current_node, _current_session
            _current_node.name = name
            _current_session.id = session_id

            # 会话日志：记录节点入参（在 fn 调用前快照）
            from .services.logger import get_logger
            logger = get_logger(state)
            input_snapshot = dict(state) if logger else None
            seq = None
            if logger:
                seq = logger.log_node_start(name)

            t0 = time.perf_counter()
            error_msg = None
            try:
                result = fn(state)
            except Exception as e:
                error_msg = str(e)
                result = {"error": error_msg}

            elapsed = round(time.perf_counter() - t0, 3)

            if isinstance(result, dict):
                timings = dict(state.get("node_timings", {}))
                key = name
                n = 2
                while key in timings:
                    key = f"{name}#{n}"
                    n += 1
                timings[key] = elapsed
                result["node_timings"] = timings

            if logger and seq:
                logger.log_node_end(seq, name, input_snapshot, result, elapsed, error_msg)

            print(f"  [{name}] {elapsed:.2f}s")
            return result
        return wrapper
    return _timed


def build_graph(collection, db_adapter, on_clarify_ask=None, on_progress=None):
    """构建并编译 NL2SQL 状态图。

    Args:
        collection: Milvus Collection 实例
        db_adapter: BaseDBAdapter 实现（SQLiteAdapter 等）
        on_clarify_ask: 可选回调 (payload: dict) -> str
            传入时：clarify 节点触发回调获取用户响应（Web 模式）
            未传入：clarify 节点直接确认放行（CLI/批量模式）
        on_progress: 可选回调 (node_name: str, session_id: str|None) -> None
            每个节点执行前触发，用于前端实时展示进度
    """
    timed = _build_timed(on_progress)
    # 工厂创建适配器节点
    execute_sql_node = make_execute_sql(db_adapter)
    lookup_values_node = make_lookup_values(db_adapter)
    fix_agent_node = make_fix_agent(db_adapter, execute_sql_node)

    builder = StateGraph(OverallState)

    # Stage 0: 查询澄清
    builder.add_node("clarify_query", timed("clarify_query", clarify_node.clarify_query))

    # Stage 0.5: 指标语义层（两段式：NL→指标 映射 + 指标→口径 展开）
    builder.add_node("semantic_map", timed("semantic_map", semantic_map_node.semantic_map))
    builder.add_node("metric_expand", timed("metric_expand", metric_expand_node.metric_expand))

    # Stage 1: 原有节点
    recall_fn = lambda state: recall.recall_tables(state, collection=collection)
    builder.add_node("recall_tables", timed("recall_tables", recall_fn))
    builder.add_node("rerank_tables", timed("rerank_tables", rerank.rerank_tables))
    builder.add_node("enforce_rules", timed("enforce_rules", enforce.enforce_rules))
    builder.add_node("build_prompt", timed("build_prompt", build_prompt.build_prompt))
    builder.add_node("generate_sql", timed("generate_sql", generate.generate_sql))

    # Stage 1.5: 值发现
    builder.add_node("lookup_values", timed("lookup_values", lookup_values_node))

    # Stage 2.5: 语义校验
    builder.add_node("semantic_validate", timed("semantic_validate", semantic_node.validate_semantics))

    # Stage 3: 执行 + 修复（使用工厂创建的节点）
    builder.add_node("execute_sql", timed("execute_sql", execute_sql_node))
    builder.add_node("validate_result", timed("validate_result", validate_result))
    builder.add_node("fix_agent", timed("fix_agent", fix_agent_node))

    # --- 边 ---
    builder.add_edge(START, "clarify_query")
    builder.add_conditional_edges(
        "clarify_query",
        lambda state: state.get("clarify_phase", "init"),
        {
            "init": "clarify_query",
            "ask_user": "clarify_query",
            "process_response": "clarify_query",
            "confirmed": "semantic_map",
            "skipped": "semantic_map",
        }
    )

    # Stage 0.5: 指标映射与展开（不命中指标时 metric_expand 置空值，管线照常继续）
    builder.add_edge("semantic_map", "metric_expand")
    builder.add_edge("metric_expand", "recall_tables")

    builder.add_edge("recall_tables", "rerank_tables")

    # 上游 guard：召回为空（embedding 失败等）时直接终止，不再盲猜生成
    builder.add_conditional_edges(
        "rerank_tables",
        lambda state: "ok" if state.get("all_tables") else "fail",
        {"ok": "enforce_rules", "fail": END},
    )

    builder.add_edge("enforce_rules", "lookup_values")
    builder.add_edge("lookup_values", "build_prompt")
    builder.add_edge("build_prompt", "generate_sql")

    builder.add_edge("generate_sql", "semantic_validate")
    builder.add_conditional_edges(
        "semantic_validate",
        lambda state: (
            "pass" if state.get("semantic_pass", True) or state.get("semantic_retry_count", 0) >= 1
            else "fail"
        ),
        {
            "pass": "execute_sql",
            "fail": "fix_agent",
        }
    )

    builder.add_edge("execute_sql", "validate_result")

    builder.add_conditional_edges(
        "validate_result",
        lambda state: state["route"],
        {
            "end": END,
            "retry": "fix_agent",
        }
    )

    builder.add_conditional_edges(
        "fix_agent",
        lambda state: "generate_sql" if state.get("fix_source") == "semantic_gap" else "execute_sql",
        {
            "generate_sql": "generate_sql",
            "execute_sql": "execute_sql",
        }
    )

    return builder.compile(checkpointer=InMemorySaver())
