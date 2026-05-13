"""单条 NL 查询测试 -- 带 Stage 0 查询澄清交互"""
import uuid
from pymilvus import Collection, connections
from langgraph.types import Command
from src.nl2sql_graph.graph_builder import build_graph
from src.nl2sql_graph.state import OverallState

# query = "把交易明细拉出来，同时带上科目、客户、供应商、产品这四张维度表的信息——看看每笔交易什么日期、什么类型、多少钱、走的哪个科目、关联的客户是谁、供应商是谁、买了什么产品或服务，还有借贷方金额、未清余额、到期日、应收应付状态。用LEFT JOIN关联，哪怕某个维度信息缺失也能把交易本身带出来，但只保留有客户或者有供应商的交易，按交易日期倒序看最新的50条。"
query = """业务需求（二号业务线）：

高价值客户定义（看所有业务线的发票数据，不限本业务线）：

取客户名正常的发票，按客户汇总总金额。

若某客户的总金额 > 所有发票平均金额的2倍，即为高价值客户。

筛选与关联：

只保留客户资料表中属于“二号业务线”的客户（按客户名匹配），取出其计费州。

输出前30名：按发票总金额降序，取前30名客户，展示：

客户名称、所在州、发票总金额、发票张数、最大单笔金额

是否买过服务类产品（从交易关联产品表判断，不限交易类型）

客户类型：从无“账单”交易为“纯收入客户”，否则“有费用记录”"""

connections.connect(host='localhost', port='19530', db_name='HqSQL')
collection = Collection("tables")
collection.load()
graph = build_graph(collection)

config = {"configurable": {"thread_id": str(uuid.uuid4())}}

state: OverallState = {
    "query": query,
    # Stage 0 新增
    "raw_query": "",
    "skip_clarify": True,
    "clarify_phase": "init",
    "clarify_round": 0,
    "clarify_analysis": None,
    "clarify_user_response": "",
    # 原有字段
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

# --- 中断处理循环 ---
result = graph.invoke(state, config)

# LangGraph v1 返回 dict，中断信息在 __interrupt__ 键中
while isinstance(result, dict) and "__interrupt__" in result:
    for intr in result["__interrupt__"]:
        payload = intr.value
        print(f"\n{'='*60}")
        print(f"[Clarify 第{payload.get('round', '?')}轮] 建议增强查询:")
        print(f"  {payload.get('enhanced_query', '')}")
        questions = payload.get("questions", [])
        if questions:
            print(f"\n  澄清问题:")
            for i, q in enumerate(questions, 1):
                print(f"  Q{i}: {q}")
        print(f"\n  (直接回车=接受, 输入反馈=修改, 'skip'=跳过)")

    user_input = input("\n> ").strip()

    if user_input.lower() == 'skip':
        # 更新 skip_clarify=True，节点重执行时直接走 skipped 路径
        result = graph.invoke(
            Command(update={"skip_clarify": True}, resume="skip"),
            config,
        )
    else:
        result = graph.invoke(Command(resume=user_input), config)

final = result

# --- 输出 ---
print(f"\n{'='*60}")
print(f"最终查询: {final.get('query', query)[:120]}...")
print()

for t in final["all_tables"]:
    print(f"  {t['table_name']:30s} score={t['score']:.4f}")

print(f"\nStage 1 selected: {final['selected_names']}")
if final.get("forced_names"):
    print(f"[ENFORCE] forced: {final['forced_names']}")
if final.get("warnings"):
    print(f"[WARN] {final['warnings']}")

reqs = final.get("requirement_items", [])
if reqs:
    print(f"\n--- 需求分解 ({len(reqs)}项) ---")
    for r in reqs:
        print(f"  [{r.get('type', '?')}] {r.get('desc', '')}")
else:
    print("\n--- 需求分解: (空) ---")

sql = final['sql']
if sql.startswith('```'):
    lines = sql.split('\n')
    if len(lines) >= 3:
        sql = '\n'.join(lines[1:-1])
print(f"\n--- SQL ---\n{sql}\n")

if final.get("sql_error"):
    print(f"[SQL ERROR] {final['sql_error']}")
if final.get("sql_result"):
    lines = final["sql_result"].split("\n")
    print(f"Result ({len(lines)-1} rows):")
    for line in lines[:12]:
        print(f"  {line}")
    if len(lines) > 12:
        print(f"  ... ({len(lines)-1} total rows)")

retries = final.get("retry_count", 0)
if retries > 0:
    print(f"[RETRY] count: {retries}")

timings = final.get("node_timings", {})
if timings:
    total = sum(timings.values())
    print(f"\n--- Node timings ---")
    for name, sec in timings.items():
        pct = sec / total * 100 if total > 0 else 0
        print(f"  {name:25s} {sec:7.2f}s  ({pct:4.0f}%)")
    print(f"  {'Total':25s} {total:7.2f}s")

connections.disconnect("default")
