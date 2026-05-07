"""NL2SQL v2 -- LangGraph 入口脚本

对比基线: nl2sql.py（原纯函数实现）
"""
from pymilvus import Collection, connections
from src.nl2sql_graph.graph_builder import build_graph
from src.nl2sql_graph.state import OverallState


def run_query(graph, collection, query: str) -> dict:
    state: OverallState = {
        "query": query,
        "all_tables": [],
        "selected_names": [],
        "rerank_raw": "",
        "forced_names": [],
        "warnings": [],
        "prompt": "",
        "sql": "",
        "error": None,
    }
    return graph.invoke(state)


def main():
    connections.connect(host='localhost', port='19530', db_name='HqSQL')
    collection = Collection("tables")
    collection.load()

    graph = build_graph(collection)

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

        result = run_query(graph, collection, query)

        for t in result["all_tables"]:
            print(f"  {t['table_name']:30s} score={t['score']:.4f}")

        print(f"\n  Stage 1 筛选结果: {result['selected_names']}")
        if result.get("forced_names"):
            print(f"  [ENFORCE] 依赖规则强制补入: {result['forced_names']}")
        if result.get("warnings"):
            print(f"  [WARN] {result['warnings']}")

        print(f"\nGenerated SQL:\n{result['sql']}\n")

    connections.disconnect("default")


if __name__ == '__main__':
    main()
