"""Stage 0.5a: NL → 指标/维度 映射节点（指标语义层第一段，轻量 LLM 调用）

判断用户查询是否命中注册表指标，输出结构化映射计划。
未命中/解析失败时不阻塞，由 metric_expand 回退通用流程。
"""
import json
import re

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from ..services.llm import flash_model
from ..services.metrics import get_registry

_prompt = ChatPromptTemplate.from_messages([("human", "{prompt}")])
_chain = _prompt | flash_model | StrOutputParser()

_MAP_TEMPLATE = """你是数据查询指标识别器。判断用户查询是否涉及注册表指标，输出 JSON 映射结果。

## 注册表（内置指标）
{digest}

## 用户查询
{query}

## 任务
1. 若查询明确要求统计/计算某个内置指标（或其一），metric 填该指标名称（须与注册表完全一致）；
   否则 metric 填 null。
2. dims：从该指标允许的维度中选出用户要求的分组维度（名称须与注册表一致）；
   没有分组要求则为空数组。
3. granule：用户要求按日/月/年汇总时填"日"/"月"/"年"，否则 null。
4. extra_filters：用户额外指定的筛选条件（如时间范围、客户名），自然语言描述数组，可为空。

只输出 JSON（不要用 markdown 代码块包裹）：
{{
  "metric": "收入" 或 null,
  "dims": ["日期"],
  "granule": "月" 或 null,
  "extra_filters": ["仅统计 2022 年"],
  "confidence": "high" | "medium" | "low"
}}"""


def _parse_json_safely(response: str) -> dict:
    try:
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        return json.loads(cleaned)
    except (json.JSONDecodeError, AttributeError):
        return {}


def semantic_map(state: dict) -> dict:
    registry = get_registry()
    query = state.get("query", "")
    raw = _chain.invoke({"prompt": _MAP_TEMPLATE.format(digest=registry.digest(), query=query)})
    parsed = _parse_json_safely(raw)

    dims = parsed.get("dims")
    if not isinstance(dims, list):
        dims = []
    plan = {
        "metric": parsed.get("metric"),
        "dims": dims,
        "granule": parsed.get("granule"),
        "extra_filters": parsed.get("extra_filters", []),
        "confidence": parsed.get("confidence", "low"),
    }

    # 确定性后校验：指标必须在注册表中且启用，否则回退通用流程
    warnings = []
    if plan["metric"]:
        metric = registry.lookup(plan["metric"])
        if metric is None:
            warnings.append(f"指标[{plan['metric']}]不在注册表中，按通用流程生成")
            plan = {"metric": None, "dims": [], "granule": None, "extra_filters": [], "confidence": "low"}
        elif not metric.enabled:
            warnings.append(f"指标[{plan['metric']}]已停用，按通用流程生成")
            plan = {"metric": None, "dims": [], "granule": None, "extra_filters": [], "confidence": "low"}

    return {
        "metric_plan": plan,
        "warnings": list(state.get("warnings", [])) + warnings,
    }