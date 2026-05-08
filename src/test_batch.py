"""批量测试：读取 test_NL.json 前10条，跑 LangGraph NL2SQL"""
import json
import os
from pymilvus import Collection, connections
from src.nl2sql_graph.graph_builder import build_graph
from src.nl2sql_graph.state import OverallState

# 加载测试数据
proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
json_path = os.path.join(proj_root, "table2milvus", "scripts", "table_resource", "test_NL.json")
with open(json_path, 'r', encoding='utf-8') as f:
    all_queries = json.load(f)

test_queries = all_queries[:10]

# 连接 Milvus
connections.connect(host='localhost', port='19530', db_name='HqSQL')
collection = Collection("tables")
collection.load()
graph = build_graph(collection)

for item in test_queries:
    qid = item['id']
    query = item['Query']
    level = item['Level']

    print("=" * 70)
    print(f"[{qid}] (Level: {level}) {query}")
    print("-" * 70)

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
        "sql_result": None,
        "sql_error": None,
        "retry_count": 0,
        "route": "",
        "lookup_context": "",
        "node_timings": {},
    }
    result = graph.invoke(state)

    for t in result["all_tables"]:
        print(f"  {t['table_name']:30s} score={t['score']:.4f}")

    print(f"\n  Stage 1 筛选: {result['selected_names']}")
    if result.get("forced_names"):
        print(f"  [ENFORCE] 强制补入: {result['forced_names']}")
    if result.get("warnings"):
        print(f"  [WARN] {result['warnings']}")

    sql = result['sql']
    if sql.startswith('```'):
        lines = sql.split('\n')
        if len(lines) >= 3:
            sql = '\n'.join(lines[1:-1])
    print(f"\n{sql}\n")

    # Phase 2: 执行结果
    if result.get("sql_error"):
        print(f"  [SQL ERROR] {result['sql_error']}")
    if result.get("sql_result"):
        lines = result["sql_result"].split("\n")
        print(f"  结果（{len(lines)-1} 行）:")
        for line in lines[:6]:
            print(f"    {line}")
        if len(lines) > 6:
            print(f"    ...")
    retries = result.get("retry_count", 0)
    if retries > 0:
        print(f"  [RETRY] 共重试 {retries} 次")

    # 节点耗时汇总
    timings = result.get("node_timings", {})
    if timings:
        total = sum(timings.values())
        print(f"\n  --- 节点耗时 ---")
        for name, sec in timings.items():
            pct = sec / total * 100 if total > 0 else 0
            print(f"  {name:25s} {sec:7.2f}s  ({pct:4.0f}%)")
        print(f"  {'总计':25s} {total:7.2f}s")

connections.disconnect("default")
