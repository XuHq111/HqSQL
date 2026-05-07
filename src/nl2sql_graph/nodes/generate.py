"""Stage 2: LLM SQL 生成节点"""
from ..services.llm import call_llm


def generate_sql(state: dict) -> dict:
    """调用 LLM 根据 Stage 2 prompt 生成 SQL"""
    sql = call_llm(state["prompt"])
    return {"sql": sql}
