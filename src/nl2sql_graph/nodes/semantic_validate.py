"""语义校验节点 -- 先验校验 SQL 是否覆盖所有需求项"""
import json
import re

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from ..services.llm import flash_model

_TEMPLATE = """## 用户原始查询
{query}

## 需求清单（SQL 必须逐条覆盖）
{requirements_text}

## 生成的 SQL
```sql
{sql}
```

## 任务
逐条检查需求清单中的每一项，判断 SQL 是否覆盖了该需求。

输出格式（严格JSON，不要用markdown代码块包裹）：
{{
  "checks": [
    {{"req_id": 1, "desc": "限定 businessID = 2", "covered": false, "reason": "WHERE 子句中未出现 businessID 过滤条件"}},
    {{"req_id": 2, "desc": "按客户汇总金额", "covered": true, "reason": "GROUP BY Customers + SUM(Credit)"}}
  ],
  "overall_pass": false,
  "gap_list": [
    {{"req_id": 1, "desc": "限定 businessID = 2", "suggestion": "在 WHERE 子句中添加 t.businessID = 2"}}
  ]
}}

判定标准（务必严格，宁可误报不可漏报）：
- covered=true：SQL 中能找到**逐字对应**的子句或表达式来满足该需求
- covered=false：凡是需求描述中的约束条件（数值、字段名、逻辑关系）在 SQL 中找不到对应实现的，一律判为 false
- 需求中提到的具体表名、字段名、条件值必须在 SQL 中明文字出现才算覆盖
- gap_list 仅包含 covered=false 的需求项，suggestion 给出具体修复建议
- overall_pass：所有需求 100% covered=true 才为 true"""

_prompt = ChatPromptTemplate.from_messages([("human", _TEMPLATE)])
_chain = _prompt | flash_model | StrOutputParser()


def validate_semantics(state: dict) -> dict:
    """先验语义校验：SQL 执行前检查需求覆盖度。

    需求清单为空时直接放行。JSON 解析失败时放行。
    fail 时递增 semantic_retry_count（上限由 graph 条件路由控制）。
    """
    requirements = state.get("requirement_items", [])
    if not requirements:
        return {"semantic_pass": True, "semantic_checks": [], "gap_list": []}

    requirements_text = "\n".join(
        f"- [{r.get('type', '?')}] {r.get('desc', '')}" for r in requirements
    )

    response = _chain.invoke({
        "query": state.get("query", ""),
        "requirements_text": requirements_text,
        "sql": state.get("sql", ""),
    })

    try:
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        return {"semantic_pass": True, "semantic_checks": [], "gap_list": []}

    passed = result.get("overall_pass", True)
    current_retries = state.get("semantic_retry_count", 0)

    return {
        "semantic_pass": passed,
        "semantic_checks": result.get("checks", []),
        "gap_list": result.get("gap_list", []),
        "semantic_retry_count": current_retries if passed else current_retries + 1,
    }
