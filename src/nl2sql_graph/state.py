"""NL2SQL LangGraph 共享状态定义"""
from typing import TypedDict, List, Optional


class RecallItem(TypedDict, total=False):
    """Milvus 召回的单条表记录"""
    table_name: str       # "main.master_txn_table"
    score: float          # COSINE 相似度分数
    schema: str           # 完整 enhanced description 文本


class OverallState(TypedDict):
    """LangGraph 全局共享状态，所有节点通过读写此状态通信"""

    # === 输入 ===
    query: str                              # 用户自然语言查询

    # === Milvus 召回结果 ===
    all_tables: List[RecallItem]            # 召回的全部表（带分数+schema）

    # === Stage 1: 表重排序筛选 ===
    selected_names: List[str]               # LLM 选出的表名
    rerank_raw: str                         # LLM 原始响应（调试用）
    forced_names: List[str]
    # 依赖规则强插的表名

    # === Stage 2: Prompt 构建与 SQL 生成 ===
    prompt: str                             # Stage 2 最终 prompt
    sql: str                                # 生成的 SQL

    # === 异常 / 日志 ===
    error: Optional[str]                    # 异常信息
    warnings: List[str]                     # 非致命告警
