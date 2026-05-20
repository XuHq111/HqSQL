"""NL2SQL LangGraph 共享状态定义"""
from typing import TypedDict, List, Optional, Dict, Any


class RecallItem(TypedDict, total=False):
    """Milvus 召回的单条表记录"""
    table_name: str       # "main.master_txn_table"
    score: float          # COSINE 相似度分数
    schema: str           # 完整 enhanced description 文本


class OverallState(TypedDict):
    """LangGraph 全局共享状态，所有节点通过读写此状态通信"""

    # === 输入 ===
    query: str                              # 用户自然语言查询

    # Stage 0: 查询澄清
    raw_query: str                          # 用户原始 NL，澄清过程中始终保留
    skip_clarify: bool                      # 批量模式：跳过澄清全流程
    clarify_phase: str                      # 状态机阶段："init" | "ask_user" | "process_response" | "confirmed" | "skipped"
    clarify_round: int                      # 当前澄清轮数（1-based）
    clarify_analysis: Optional[Dict]        # LLM 结构化输出 {enhanced_query, questions[], domain_notes, confidence}
    clarify_user_response: str              # 用户的文本反馈

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

    sql_result: Optional[str]
    sql_error: Optional[str]
    retry_count: int
    route: str

    # === 语义校验 ===
    requirement_items: List[Dict]            # [{"id":1, "desc":"...", "type":"filter"}, ...]
    semantic_pass: bool                     # 先验语义校验是否通过
    semantic_checks: List[Dict]             # [{req_id, desc, covered: bool, reason}, ...]
    gap_list: List[Dict]                    # 缺口清单 [{req_id, desc, suggestion}, ...]
    fix_source: str                         # fix_agent 触发来源："syntax" | "semantic_gap"
    semantic_retry_count: int               # 语义修复尝试次数（上限1次，防死循环）

    # === 值发现（lookup_values节点） ===
    lookup_context: str                      # 运行时从数据库探查到的枚举值/实际值文本

    # === 性能追踪 ===
    node_timings: dict                       # 各节点耗时 {node_name: elapsed_seconds}

    # === 内部运行时依赖（由 Web/CLI 入口注入，不参与序列化） ===
    _clarify_callback: Optional[Any]          # Web 模式下的澄清回调函数
    _adapter: Optional[Any]                   # 数据库适配器实例
