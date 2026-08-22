"""Milvus 交互服务：Embedding + 向量检索"""
import sys as _sys, os as _os
_config_dir = _os.path.normpath(_os.path.join(_os.path.dirname(__file__), '..', '..', '..', '环境配置'))
if _config_dir not in _sys.path:
    _sys.path.insert(0, _config_dir)
from 环境配置.api_keys import DASHSCOPE_API_KEY

import dashscope
from pymilvus import Collection

# 使用 DashScope 文本 embedding 模型（1024 维）
EMBEDDING_MODEL = "qwen3.7-text-embedding"


def embed_query(query: str) -> list:
    """将用户查询转为 1024 维向量"""
    resp = dashscope.TextEmbedding.call(
        model=EMBEDDING_MODEL,
        input=[query],
        api_key=DASHSCOPE_API_KEY
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Embedding 失败: {resp.code} {resp.message}")
    return resp.output['embeddings'][0]['embedding']


def search_tables(collection: Collection, query_vec: list, limit: int = 7) -> list:
    """Milvus 向量检索，返回表名+分数+完整 schema"""
    results = collection.search(
        data=[query_vec],
        anns_field="schema_vector",
        param={"metric_type": "COSINE", "params": {"nprobe": 16}},
        limit=limit,
        output_fields=["table_name", "schema_text"]
    )

    tables = []
    for hits in results:
        for hit in hits:
            tables.append({
                "table_name": hit.entity.get('table_name'),
                "score": round(hit.distance, 4),
                "schema": hit.entity.get('schema_text')
            })
    return tables


def recall_all_tables(collection: Collection, query: str) -> list:
    """端到端：query → embed → search → 返回带分数的表列表"""
    vec = embed_query(query)
    return search_tables(collection, vec, limit=7)
