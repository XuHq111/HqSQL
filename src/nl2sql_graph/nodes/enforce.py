"""依赖规则程序化兜底节点"""
from ..rules.dependencies import DEPENDENCY_TRIGGERS


def enforce_rules(state: dict) -> dict:
    """关键词匹配检测，强制补入依赖规则触发的表"""
    query_lower = state["query"].lower()
    names = list(state["selected_names"])  # 不修改原列表
    forced = []

    for keywords, required_table in DEPENDENCY_TRIGGERS:
        if required_table in names:
            continue
        if any(kw in query_lower for kw in keywords):
            names.append(required_table)
            forced.append(required_table)

    return {
        "selected_names": names,
        "forced_names": forced,
    }
