"""单条 NL 查询测试"""
from pymilvus import Collection, connections
from src.nl2sql_graph.graph_builder import build_graph
from src.nl2sql_graph.state import OverallState

query = "按月看看每个员工干了多少活——处理了几笔交易、录了几条分录、总金额多少、平均每笔多大，还有借方贷方各多少，顺便带上人家工号和入职日期，只算正经有创建人的，按最新月份排，同一个月里谁处理的钱多谁排前面，总共看前30条。"

connections.connect(host='localhost', port='19530', db_name='HqSQL')
collection = Collection("tables")
collection.load()
graph = build_graph(collection)

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

# --- 输出 ---
for t in result["all_tables"]:
    print(f"  {t['table_name']:30s} score={t['score']:.4f}")

print(f"\nStage 1 selected: {result['selected_names']}")
if result.get("forced_names"):
    print(f"[ENFORCE] forced: {result['forced_names']}")
if result.get("warnings"):
    print(f"[WARN] {result['warnings']}")

sql = result['sql']
if sql.startswith('```'):
    lines = sql.split('\n')
    if len(lines) >= 3:
        sql = '\n'.join(lines[1:-1])
print(f"\n--- SQL ---\n{sql}\n")

if result.get("sql_error"):
    print(f"[SQL ERROR] {result['sql_error']}")
if result.get("sql_result"):
    lines = result["sql_result"].split("\n")
    print(f"Result ({len(lines)-1} rows):")
    for line in lines[:12]:
        print(f"  {line}")
    if len(lines) > 12:
        print(f"  ... ({len(lines)-1} total rows)")

retries = result.get("retry_count", 0)
if retries > 0:
    print(f"[RETRY] count: {retries}")

timings = result.get("node_timings", {})
if timings:
    total = sum(timings.values())
    print(f"\n--- Node timings ---")
    for name, sec in timings.items():
        pct = sec / total * 100 if total > 0 else 0
        print(f"  {name:25s} {sec:7.2f}s  ({pct:4.0f}%)")
    print(f"  {'Total':25s} {total:7.2f}s")

connections.disconnect("default")
