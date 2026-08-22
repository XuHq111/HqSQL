"""指标语义层注册表单测：加载 / 校验 / 展开（无需 Milvus，可离线运行）

运行：cd HqSQL && python3 src/test/test_metric_registry.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from src.api import DB_PATH
from src.nl2sql_graph.services.db_adapter import SQLiteAdapter
from src.nl2sql_graph.services.metrics import MetricsRegistry, _REGISTRY_PATH


def main():
    reg = MetricsRegistry(_REGISTRY_PATH)
    names = [m.name for m in reg.metrics]
    print("注册表指标:", names)
    assert names == ["收入", "费用", "应收账款", "逾期金额"], names

    # 1) 静态校验：表/列存在性（口径必须跟库结构一致）
    problems = reg.validate(SQLiteAdapter(DB_PATH))
    print("静态校验问题:", problems)
    assert not problems, f"注册表存在结构性问题: {problems}"

    # 2) 别名检索（中英文、大小写）
    assert reg.lookup("收入").name == "收入"
    assert reg.lookup("revenue").name == "收入"
    assert reg.lookup("REVENUE").name == "收入"
    assert reg.lookup("应收账款").name == "应收账款"
    assert reg.lookup("AR").name == "应收账款"
    assert reg.lookup("逾期").name == "逾期金额"
    assert reg.lookup("不存在的指标") is None
    print("别名检索 OK")

    # 3) 展开：收入 × 日期(月)
    r = reg.expand("收入", ["日期"], "月")
    assert r["hit"] is True and not r["warnings"]
    sk = r["skeleton"]
    assert "SUM(t.Credit)" in sk and "strftime('%Y-%m', t.Transaction_DATE)" in sk
    assert "JOIN chart_of_accounts a" in sk and "t.Account = a.Account_name" in sk
    assert "a.Account_type = 'income'" in sk and "t.Transaction_TYPE = 'invoice'" in sk
    ids = [it["id"] for it in r["requirement_items"]]
    descs = [it["desc"] for it in r["requirement_items"]]
    assert ids == sorted(ids) and ids[0] == 1001
    assert any("聚合口径" in d for d in descs) and any("过滤口径" in d for d in descs)
    print("展开（收入×月）OK")

    # 4) 英文粒度别名 + 非法维度丢弃
    r2 = reg.expand("收入", ["日期", "不存在的维度"], "month")
    assert "strftime('%Y-%m'" in r2["skeleton"]
    assert any("不存在的维度" in w for w in r2["warnings"])
    r2b = reg.expand("收入", ["不存在的维度"], None)
    assert "GROUP BY" not in r2b["skeleton"]
    print("粒度别名 / 非法维度 OK")

    # 5) 未命中 / 停用回退
    assert reg.expand("不存在", [], None)["hit"] is False
    assert reg.expand("客户", [], None)["hit"] is False
    print("未命中回退 OK")

    # 6) 函数式值渲染（逾期口径 date('now') 原样输出）+ 应收口径不含 AR_paid（数据事实）
    r3 = reg.expand("逾期金额", ["客户"], None)
    assert "t.Due_DATE < date('now')" in r3["skeleton"], r3["skeleton"]
    assert "'accounts receivable (a/r)'" in r3["skeleton"] and "t.Transaction_TYPE = 'invoice'" in r3["skeleton"]
    assert "AR_paid" not in r3["skeleton"], "应收科目无 '--' 值，口径不应含 AR_paid"
    print("逾期口径（func:date('now')）OK")

    # 7) digest 可用于 LLM 上下文
    digest = reg.digest()
    assert "内置指标" in digest and "收入" in digest
    print("\n【全部通过】")


if __name__ == "__main__":
    main()