"""值发现节点 -- 根据NL关键词自动探查数据库中的实际枚举值"""
from ..rules.value_lookups import VALUE_LOOKUP_CONFIG


def make_lookup_values(adapter):
    """工厂函数：返回绑定 adapter 的 lookup_values 节点"""

    def lookup_values(state: dict) -> dict:
        nl = state.get("query", "")
        if not nl:
            return {"lookup_context": ""}

        nl_lower = nl.lower()
        results = []

        for cfg in VALUE_LOOKUP_CONFIG:
            if not any(kw.lower() in nl_lower for kw in cfg["keywords"]):
                continue
            try:
                headers, rows = adapter.execute(cfg["query"])
                if rows:
                    values = [r[0] for r in rows]
                    results.append(f"{cfg['label']}：{values}")
            except Exception:
                pass

        if results:
            context = "## 数据库实际值参考（运行时自动探查）\n" + "\n".join(
                f"- {r}" for r in results
            )
        else:
            context = ""

        return {"lookup_context": context}

    return lookup_values
