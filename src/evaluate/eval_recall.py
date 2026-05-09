"""召回评估脚本：直接调用 recall → rerank → enforce 三个节点，对比 ground truth 计算召回成功率"""

import json
import math
import os
import sys
import time
import traceback

import pandas as pd
from pymilvus import Collection, connections

# 确保 src 在 sys.path 中，使 from src.xxx import 能工作
_proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

from src.nl2sql_graph.nodes.recall import recall_tables
from src.nl2sql_graph.nodes.rerank import rerank_tables
from src.nl2sql_graph.nodes.enforce import enforce_rules

# ─── 路径常量 ───────────────────────────────────────────────
EXCEL_PATH = r"E:\sql数据集\测试集\SQL查询清单.xlsx"
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_results.json")
MILVUS_HOST = "localhost"
MILVUS_PORT = "19530"
MILVUS_DB = "HqSQL"
COLLECTION_NAME = "tables"


def load_test_data(path: str) -> list[dict]:
    """加载 Excel 测试集，返回 [{id, level, nl, tables_raw, tables_normalized}]"""
    df = pd.read_excel(path)
    records = []
    for i in range(len(df)):
        # 跳过 tables 列为 NaN 或空的行
        if pd.isna(df.iloc[i, 2]) or str(df.iloc[i, 2]).strip() == "":
            continue

        raw_tables = str(df.iloc[i, 2]).strip()
        # ground truth 表名没有 "main." 前缀，统一加前缀再比较
        table_list = [t.strip() for t in raw_tables.split(",") if t.strip()]
        normalized = {"main." + t if not t.startswith("main.") else t for t in table_list}

        records.append({
            "id": int(df.iloc[i, 0]),
            "level": str(df.iloc[i, 4]).strip(),
            "nl": str(df.iloc[i, 3]).strip(),
            "tables_raw": raw_tables,
            "tables_normalized": normalized,
        })
    return records


def evaluate() -> None:
    # ── 1. 加载数据 ──
    print("加载测试数据...")
    records = load_test_data(EXCEL_PATH)
    total = len(records)
    print(f"共 {total} 条测试用例\n")

    # ── 2. 连接 Milvus ──
    print("连接 Milvus...")
    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT, db_name=MILVUS_DB)
    collection = Collection(COLLECTION_NAME)
    collection.load()
    print("Milvus 就绪\n")

    try:
        _run_evaluation(records, collection, total)
    finally:
        # 保证无论如何都断开 Milvus 连接
        connections.disconnect("default")
        print("Milvus 连接已断开")


def _run_evaluation(records: list[dict], collection, total: int) -> None:
    results = []
    success_count = 0
    fail_count = 0

    # 按难度分组
    level_stats: dict[str, dict] = {}

    for idx, rec in enumerate(records, start=1):
        nl = rec["nl"]
        gt = rec["tables_normalized"]
        level = rec["level"]

        try:
            # 初始化状态
            state: dict = {"query": nl, "warnings": []}

            # 3a. 向量召回
            t0 = time.perf_counter()
            recall_out = recall_tables(state, collection=collection)
            t_recall = time.perf_counter() - t0
            state.update(recall_out)

            # 3b. LLM 重排序筛选
            t0 = time.perf_counter()
            rerank_out = rerank_tables(state)
            t_rerank = time.perf_counter() - t0
            state.update(rerank_out)

            # 3c. 依赖规则强制补入
            t0 = time.perf_counter()
            enforce_out = enforce_rules(state)
            t_enforce = time.perf_counter() - t0
            state.update(enforce_out)

            selected = set(state["selected_names"])  # enforce 已合并 forced
            forced = set(state.get("forced_names", []))

            # 判定：ground truth 必须是 selected 的子集
            missing = sorted(gt - selected)
            # extra：选了但 ground truth 里没有的表（仅用于信息展示）
            extra = sorted(selected - gt)

            passed = len(missing) == 0
            if passed:
                success_count += 1
            else:
                fail_count += 1

            # 难度分组统计
            if level not in level_stats:
                level_stats[level] = {"total": 0, "success": 0}
            level_stats[level]["total"] += 1
            if passed:
                level_stats[level]["success"] += 1

            result_item = {
                "id": rec["id"],
                "level": level,
                "query": nl,
                "ground_truth": sorted(gt),
                "selected": sorted(selected),
                "forced": sorted(forced),
                "missing": missing,
                "extra": extra,
                "passed": passed,
                "timings": {"recall": round(t_recall, 3), "rerank": round(t_rerank, 3), "enforce": round(t_enforce, 3)},
                "warnings": state.get("warnings", []),
            }
            results.append(result_item)

            # ── 终端输出 ──
            print(f"[{idx}/{total}] ({level}) {nl[:60]}")
            print(f"  Ground Truth: {', '.join(sorted(gt))}")
            print(f"  Selected:      {', '.join(sorted(selected))}")
            if forced:
                print(f"  [ENFORCE] 强制补入: {', '.join(sorted(forced))}")
            if missing:
                print(f"  Missing:       {', '.join(missing)}")
            if extra:
                print(f"  Extra:         {', '.join(extra)}")
            # 展示 rerank 回退等警告
            wlist = state.get("warnings", [])
            if wlist:
                for w in wlist:
                    print(f"  [WARNING] {w}")
            status = "PASS" if passed else "FAIL"
            print(f"  Result: {status}")
            print()

        except Exception:
            fail_count += 1
            if level not in level_stats:
                level_stats[level] = {"total": 0, "success": 0}
            level_stats[level]["total"] += 1

            error_msg = traceback.format_exc()
            result_item = {
                "id": rec["id"],
                "level": level,
                "query": nl,
                "ground_truth": sorted(gt),
                "selected": [],
                "forced": [],
                "missing": sorted(gt),
                "extra": [],
                "passed": False,
                "timings": {},
                "error": error_msg,
            }
            results.append(result_item)

            print(f"[{idx}/{total}] ({level}) {nl[:60]}")
            print(f"  [ERROR] 处理异常，跳过本条: {error_msg.splitlines()[-1]}")
            print()

    # ── 4. 汇总 ──
    summary_lines = [
        "=" * 60,
        f"总评: {total}条, 成功 {success_count}条, 失败 {fail_count}条",
        f"成功率: {(success_count / total * 100) if total > 0 else 0:.1f}%",
        "",
        "按难度:",
    ]
    for lvl in sorted(level_stats.keys()):
        st = level_stats[lvl]
        rate = st["success"] / st["total"] * 100 if st["total"] > 0 else 0
        summary_lines.append(f"  {lvl}: {st['total']}条, 成功 {st['success']}条, 成功率 {rate:.1f}%")

    summary = "\n".join(summary_lines)
    print(summary)

    # ── 5. 写入 JSON ──
    output = {
        "summary": {
            "total": total,
            "success": success_count,
            "fail": fail_count,
            "success_rate": round(success_count / total * 100, 1) if total > 0 else 0,
            "level_stats": level_stats,
        },
        "results": results,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n结果已写入 {OUTPUT_PATH}")


if __name__ == "__main__":
    evaluate()
