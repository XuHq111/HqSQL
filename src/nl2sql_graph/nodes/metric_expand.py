"""Stage 0.5b: 指标展开节点（指标语义层第二段，纯确定性，不调 LLM）

把 semantic_map 命中的指标展开为：
- metric_context：口径硬约束文本（注入 build_prompt）
- requirement_items：口径需求条目（id 1001+，进入 semantic_validate 与 fix_agent 闭环）
未命中时保持空值，管线按通用流程继续。
"""
from ..services.metrics import get_registry


def metric_expand(state: dict) -> dict:
    registry = get_registry()
    plan = state.get("metric_plan") or {}
    warnings = state.get("warnings", [])

    metric_name = plan.get("metric")
    if not metric_name:
        return {
            "metric_hit": False,
            "metric_context": "",
            "requirement_items": [],
            "warnings": warnings,
        }

    expanded = registry.expand(metric_name, plan.get("dims") or [], plan.get("granule"))
    if not expanded["hit"]:
        return {
            "metric_hit": False,
            "metric_context": "",
            "requirement_items": [],
            "warnings": list(warnings) + expanded["warnings"],
        }

    # 用户补充条件（时间范围等）作为口径外的提示附带注入，允许 LLM 叠加
    extra = plan.get("extra_filters") or []
    context = expanded["context"]
    if extra:
        context += "\n补充条件（用户要求，可与口径叠加）：" + "；".join(str(e) for e in extra)

    return {
        "metric_hit": True,
        "metric_context": context,
        "requirement_items": expanded["requirement_items"],
        "warnings": list(warnings) + expanded["warnings"],
    }