"""Milvus 向量召回节点"""
from ..services import milvus


def recall_tables(state: dict, *, collection) -> dict:
    """Embed 用户查询 + Milvus 向量检索，返回全部 7 张表

    召回失败（embedding 服务/额度问题等）时明确写入 error，
    由上游 guard 终止流水线，避免空候选进入后续盲猜生成。
    """
    try:
        tables = milvus.recall_all_tables(collection, state["query"])
    except Exception as e:
        return {
            "all_tables": [],
            "error": f"表召回失败：{e}（请检查 Milvus 服务与 embedding 接口/额度）",
        }
    if not tables:
        return {
            "all_tables": [],
            "error": "表召回失败：向量检索未返回任何表（请检查 Milvus 中的数据与查询向量）",
        }
    return {"all_tables": tables}