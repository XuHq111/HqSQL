"""Milvus 向量召回节点"""
from ..services import milvus


def recall_tables(state: dict, *, collection) -> dict:
    """Embed 用户查询 + Milvus 向量检索，返回全部 7 张表"""
    tables = milvus.recall_all_tables(collection, state["query"])
    return {"all_tables": tables}
