"""Milvus 交互服务：Embedding + 向量检索"""
import dashscope
from pymilvus import Collection

from .llm import API_KEY


def embed_query(query: str) -> list:
    """将用户查询转为 1152 维向量"""
    resp = dashscope.MultiModalEmbedding.call(
        model="tongyi-embedding-vision-plus-2026-03-06",
        input=[{'text': query}],
        api_key=API_KEY
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
