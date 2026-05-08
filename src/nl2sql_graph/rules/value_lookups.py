"""值发现配置 — 根据NL关键词在数据库上跑轻量查询，查出枚举值/实际值注入prompt"""

# 每条配置: keywords (触发关键词列表) / query (执行的SQL) / label (注入prompt的标题)
VALUE_LOOKUP_CONFIG = [
    {
        "keywords": [
            "应收", "AR", "receivable", "应收账款", "accounts receivable",
        ],
        "query": (
            "SELECT DISTINCT Account FROM main.master_txn_table "
            "WHERE lower(Account) LIKE '%accounts receivable%'"
        ),
        "label": "应收账款(AR)对应的 Account 值",
    },
    {
        "keywords": [
            "应付", "AP", "payable", "应付账款", "accounts payable",
        ],
        "query": (
            "SELECT DISTINCT Account FROM main.master_txn_table "
            "WHERE lower(Account) LIKE '%accounts payable%'"
        ),
        "label": "应付账款(AP)对应的 Account 值",
    },
    {
        "keywords": [
            "收入", "费用", "利润", "income", "expense", "revenue", "profit",
            "cost", "科目", "account_type", "会计科目", "类别",
        ],
        "query": (
            "SELECT DISTINCT Account_type FROM main.chart_of_accounts "
            "ORDER BY Account_type"
        ),
        "label": "Account_type 枚举值（会计科目大类，筛选收入/费用时用此字段）",
    },
]
