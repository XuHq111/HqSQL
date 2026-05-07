import dashscope
from pymilvus import Collection, connections

connections.connect(host='localhost', port='19530', db_name='HqSQL')
collection = Collection("tables")
collection.load()

test_queries = [
    "How are my sales year to date compared to last year?",
    "in aug this year, what was our largest expense?",
    "What was my expense by products Last 12 months？",
    "今年至今的销售额与去年同期相比如何？",
    "今年8月我们最大的费用支出是什么？",
    "过去12个月按产品分类的费用是多少？",
    "what products are selling less than last month",
]

for query in test_queries:
    print("=" * 70)
    print(f"查询: {query}")

    resp = dashscope.MultiModalEmbedding.call(
        model="tongyi-embedding-vision-plus-2026-03-06",
        input=[{'text': query}],
        api_key="sk-fad59201f05146a5988bd4d78a04f3fa"
    )
    if resp.status_code != 200:
        print(f"  Embedding 失败: {resp.code} {resp.message}")
        continue

    vec = resp.output['embeddings'][0]['embedding']

    results = collection.search(
        data=[vec],
        anns_field="schema_vector",
        param={"metric_type": "COSINE", "params": {"nprobe": 16}},
        limit=5,
        output_fields=["table_name", "schema_text", "db_type"]
    )

    for i, hits in enumerate(results):
        for j, hit in enumerate(hits):
            print(f"\n--- 第{j+1}名 (distance={hit.distance:.4f}) ---")
            print(f"表名: {hit.entity.get('table_name')}")
            text = hit.entity.get('schema_text')
            print(f"schema_text (前200字): {text[:200]}...")
            print(f"schema_text 长度: {len(text)}")

connections.disconnect("default")
