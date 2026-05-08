"""值发现节点 — 根据NL关键词自动探查数据库中的实际枚举值"""
import sqlite3
from contextlib import closing
from ..rules.value_lookups import VALUE_LOOKUP_CONFIG

_DB_PATH = r"E:\sql数据集\accounting.sqlite"


def lookup_values(state: dict) -> dict:
    """匹配NL关键词，执行对应SQL查询，将发现的枚举值注入后续prompt"""
    nl = state.get("query", "")
    if not nl:
        return {"lookup_context": ""}

    nl_lower = nl.lower()
    results = []

    with closing(sqlite3.connect(_DB_PATH)) as conn:
        cur = conn.cursor()
        for cfg in VALUE_LOOKUP_CONFIG:
            if not any(kw.lower() in nl_lower for kw in cfg["keywords"]):
                continue
            try:
                cur.execute(cfg["query"])
                rows = cur.fetchall()
                if rows:
                    values = [r[0] for r in rows]
                    results.append(f"{cfg['label']}：{values}")
            except Exception:
                pass

    if results:
        context = "## 数据库实际值参考（运行时自动探查）\n" + "\n".join(f"- {r}" for r in results)
    else:
        context = ""

    return {"lookup_context": context}
