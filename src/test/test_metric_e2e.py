"""指标语义层端到端测试：命中与回退两条路径（需 Milvus 与 LLM/embedding 配额）

命中用例：口径硬约束必须出现在生成的 SQL 中；
回退用例：未命中指标时管线仍正常生成 SQL。

运行：cd HqSQL && python3 src/test/test_metric_e2e.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from pymilvus import Collection, connections

from src.api import DB_PATH
from src.nl2sql_graph.graph_builder import build_graph
from src.nl2sql_graph.services.db_adapter import SQLiteAdapter
from src.nl2sql_graph.services import metrics as metric_service


def _initial_state(query: str) -> dict:
    return {
        "query": query,
        "raw_query": query,
        "skip_clarify": True,
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
        "requirement_items": [],
        "semantic_pass": True,
        "semantic_checks": [],
        "gap_list": [],
        "fix_source": "",
        "semantic_retry_count": 0,
    }


def run_case(graph, query: str, tag: str) -> dict:
    print("=" * 70)
    print(f"[{tag}] {query}")
    t0 = time.time()
    result = graph.invoke(_initial_state(query), {"configurable": {"thread_id": f"metric-e2e-{tag}-{int(t0 * 1000)}"}})
    print(f"  总耗时 {time.time() - t0:.1f}s | metric_hit={result.get('metric_hit')} | "
          f"warnings={result.get('warnings')}")
    timings = result.get("node_timings", {})
    if timings:
        for name, sec in timings.items():
            print(f"    {name:22s} {sec:7.2f}s")
    return result


def main():
    adapter = SQLiteAdapter(DB_PATH)
    metric_service.init_registry(adapter)  # 注册表校验先行（问题会打印）
    problems = metric_service.get_registry().problems
    assert not problems, f"注册表校验失败: {problems}"

    connections.connect(host='localhost', port='19530', db_name='HqSQL')
    collection = Collection("tables")
    collection.load()
    graph = build_graph(collection, adapter)

    # ---- 命中用例：收入 × 月份 ----
    r = run_case(graph, "统计 2022 年每个月的收入总额", "命中-收入")
    sql = (r.get("sql") or "").strip()
    assert r.get("metric_hit") is True, f"期望命中指标，实际 metric_hit={r.get('metric_hit')}，warnings={r.get('warnings')}"
    assert r.get("sql_error") is None, f"SQL 执行失败: {r.get('sql_error')}"
    # 口径硬约束必须出现在 SQL 里
    assert "'invoice'" in sql, f"缺少口径过滤 invoice: {sql}"
    assert "Account_type" in sql, f"缺少科目大类 JOIN 过滤: {sql}"
    assert "strftime('%Y-%m'" in sql, f"缺少按月份聚合: {sql}"
    # 口径需求条目必须保留在 requirement_items（供语义校验/修复闭环）
    descs = [it.get("desc", "") for it in r.get("requirement_items", [])]
    assert any("聚合口径" in d for d in descs), f"口径条目丢失: {descs}"
    assert any("Account_type" in d for d in descs)
    total = sum(v for k, v in r.get("node_timings", {}).items())
    print(f"  [断言通过] metric_hit、口径过滤、月粒度聚合、需求条目齐全（总耗时 {total:.1f}s）")

    # ---- 回退用例：未命中指标，通用流程 ----
    r2 = run_case(graph, "公司有多少员工", "回退-员工")
    assert r2.get("metric_hit") is False, "员工查询不应命中指标层"
    assert (r2.get("sql") or "").strip(), "回退路径应正常生成 SQL"
    assert r2.get("sql_error") is None, f"回退路径执行失败: {r2.get('sql_error')}"
    print("  [断言通过] 未命中时管线正常生成并执行 SQL")

    connections.disconnect("default")
    print("\n【端到端全部通过】命中 + 回退两条路径均正常")


if __name__ == "__main__":
    main()