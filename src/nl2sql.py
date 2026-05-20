"""NL2SQL v2 -- LangGraph 入口脚本"""

import uuid
from pymilvus import Collection, connections
from src.nl2sql_graph.graph_builder import build_graph
from src.nl2sql_graph.state import OverallState
from src.nl2sql_graph.services.db_adapter import SQLiteAdapter

DB_PATH = r"E:\sql数据集\accounting.sqlite"


def run_query(graph, query: str, db_adapter, skip_clarify: bool = True) -> dict:
    state: OverallState = {
        "query": query,
        "raw_query": "",
        "skip_clarify": skip_clarify,
        "clarify_phase": "init",
        "clarify_round": 0,
        "clarify_analysis": None,
        "clarify_user_response": "",
        "all_tables": [],
        "selected_names": [],
        "rerank_raw": "",
        "forced_names": [],
        "warnings": [],
        "prompt": "",
        "sql": "",
        "error": None,
        "sql_result": None,
        "sql_error": None,
        "retry_count": 0,
        "route": "",
        "lookup_context": "",
        "node_timings": {},
        "_clarify_callback": None,
        "_adapter": db_adapter,
    }
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    return graph.invoke(state, config)


def main():
    connections.connect(host='localhost', port='19530', db_name='HqSQL')
    collection = Collection("tables")
    collection.load()

    db_adapter = SQLiteAdapter(DB_PATH)
    graph = build_graph(collection, db_adapter)

    test_queries = [
        "How are my sales year to date compared to last year?",
        "in aug this year, what was our largest expense?",
        "What was my expense by products Last 12 months？",
        "what products are selling less than last month",
    ]

    for query in test_queries:
        print("=" * 70)
        print(f"Query: {query}")
        print("-" * 70)

        result = run_query(graph, query, db_adapter)

        for t in result["all_tables"]:
            print(f"  {t['table_name']:30s} score={t['score']:.4f}")

        print(f"\n  Stage 1 筛选结果: {result['selected_names']}")
        if result.get("forced_names"):
            print(f"  [ENFORCE] 依赖规则强制补入: {result['forced_names']}")
        if result.get("warnings"):
            print(f"  [WARN] {result['warnings']}")

        print(f"\nGenerated SQL:\n{result['sql']}\n")

        if result.get("sql_error"):
            print(f"  [SQL ERROR] {result['sql_error']}")
        if result.get("sql_result"):
            lines = result["sql_result"].split("\n")
            print(f"  结果（{len(lines)-1} 行）:")
            for line in lines[:6]:
                print(f"    {line}")

        timings = result.get("node_timings", {})
        if timings:
            total = sum(timings.values())
            print(f"\n  --- 节点耗时 ---")
            for name, sec in timings.items():
                pct = sec / total * 100 if total > 0 else 0
                print(f"  {name:25s} {sec:7.2f}s  ({pct:4.0f}%)")
            print(f"  {'总计':25s} {total:7.2f}s")

    connections.disconnect("default")


if __name__ == '__main__':
    main()
