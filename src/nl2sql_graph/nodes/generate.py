"""Stage 2: LLM SQL 生成节点"""
import json
import re

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from ..services.llm import plus_model

_prompt = ChatPromptTemplate.from_messages([("human", "{prompt}")])
_chain = _prompt | plus_model | StrOutputParser()


def generate_sql(state: dict) -> dict:
    """调用 LLM 根据 Stage 2 prompt 生成 SQL。

    LLM 输出为 JSON 格式 {"requirements": [...], "sql": "..."}，
    解析后分别写入 sql 和 requirement_items 字段。
    JSON 解析失败时兜底：将原始文本当 SQL 使用。
    """
    raw = _chain.invoke({"prompt": state["prompt"]})

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        parsed = json.loads(cleaned)
        return {
            "sql": parsed["sql"],
            "requirement_items": parsed.get("requirements", []),
        }
    except (json.JSONDecodeError, KeyError, TypeError):
        return {
            "sql": raw,
            "requirement_items": [],
        }
