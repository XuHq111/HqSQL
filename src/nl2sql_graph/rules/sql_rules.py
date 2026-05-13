"""SQL 生成规则 — 三层分层架构"""

# ============================================================
# Layer 1: 核心约束（始终注入，不可绕过）
# ============================================================
CORE_RULES = """
SQL 生成规则：
- 使用 SQLite 语法
- 所有表名带 main. 前缀
- 日期函数用 strftime() 或 date()，相对日期用 'now' 而非硬编码
- 金额字段（严格遵守）：收入/销售额筛选用 Credit，费用/支出筛选用 Debit。禁止用 Amount 做 WHERE 条件中的金额阈值过滤（Amount 是交易总金额，不等于费用或收入金额）
- 禁止使用 ? 占位符，直接写死具体值
"""

# ============================================================
# Layer 2: 条件规则（按查询特征关键词匹配，动态注入）
# ============================================================

# 条件规则文本 — key 为规则名，value 为规则文本
CONDITIONAL_RULES = {
    "revenue_expense": "- account_type 枚举（小写）：'income','other income','expenses','other expense','accounts receivable (a/p)','accounts payable (a/p)'。筛选收入/费用时 JOIN chart_of_accounts ON businessID + Account = Account_name，用 account_type 过滤",
    "multi_tenant": "- 多租户 businessID：根据用户查询语义判断。用户泛指【公司】【整体】【全部业务】【汇总】【不限业务线】时，不限定 businessID，汇总全部租户。用户明确提及特定业务编号时，才加对应 businessID 过滤。重要：当查询同时要求\"跨业务线计算指标+只展示某业务线客户\"时（如\"从全业务线发票中找高价值客户，但只保留二号业务线客户\"），指标计算部分（聚合/子查询/阈值）不加 businessID 过滤，仅在最终展示层通过 JOIN 客户表限定 businessID",
    "transaction_type": "- Transaction_TYPE 枚举（小写）：'invoice'（发票/收入类交易）、'bill'（账单，可能关联费用科目也可能不是）、'deposit'（存款）。判断客户是否有账单交易（Transaction_TYPE='bill'）时，不要额外加 Account_type 过滤，账单类型本身不等于费用科目",
    "product_category": "- 产品分类：仅当查询涉及 Product_Service_type 产品分类维度时，才 JOIN products 表。按产品分组统计直接用 master_txn_table.Product_Service GROUP BY",
    "employee_status": "- 员工过滤：查询涉及员工状态时，加 Deleted='no' 只取在职员工",
    "payment_method": "- 支付方式：查询涉及信用卡/支付方式时 JOIN payment_method 表",
}

# 关键词触发映射 — 格式与 DEPENDENCY_TRIGGERS 一致：[(keywords_list, rule_name), ...]
RULE_TRIGGERS = [
    (['sales', 'revenue', 'expense', 'income', 'profit', 'cost', 'selling', 'sell',
      '收入', '费用', '利润', '成本', '销售', '卖', '支出', '进账', '开销',
      'invoice', 'bill', 'deposit', 'debit', 'credit', '金额', '财务',
      'account_type', '科目', '会计'], 'revenue_expense'),
    (['公司', '整体', '全部', '汇总', '所有', '总览',
      'company', 'all', 'overall', 'total', '租户', 'business',
      '每个业务', '各个业务', '各业务'], 'multi_tenant'),
    (['Transaction_TYPE', 'transaction type', '交易类型', '发票', '账单',
      'invoice', 'bill', 'deposit'], 'transaction_type'),
    (['产品类型', '产品分类', 'product type', 'product category',
      'service type', '服务类型', 'Product_Service_type'], 'product_category'),
    (['员工', '在职', '离职', 'employee', 'staff', 'hire',
      '入职', '解雇', 'Deleted'], 'employee_status'),
    (['信用卡', 'credit card', 'visa', 'mastercard', '支付方式',
      'payment method', 'payment_method'], 'payment_method'),
]

# ============================================================
# Layer 3: 修复规则（仅在 fix_sql 节点注入）
# ============================================================
FIX_RULES = """
SQL 修复指引：
- 检查表名前缀：所有表名必须带 main. 前缀
- 检查占位符：禁止使用 ? 占位符，必须写死具体值
- 检查金额字段：费用/支出必须用 Debit 而非 Amount 做阈值过滤；收入/销售额用 Credit
- 检查 JOIN 条件：master_txn_table JOIN chart_of_accounts 必须同时匹配 businessID 和 Account
- 空结果排查：检查 WHERE 条件是否过严、JOIN 是否正确、日期范围是否合理
- 语法检查：确保是合法的 SQLite 语法，字段名拼写正确
"""


def match_rules(query: str, triggers=None, conditional_rules=None) -> str:
    """根据查询文本匹配条件规则，返回拼接后的规则文本（仅 Layer 2 条件规则，不含核心约束）。

    对每个 trigger 组，若 query 中命中任一关键词，则激活对应规则。
    返回所有命中规则的拼接文本（换行分隔）。
    """
    if not query:
        return ""
    if triggers is None:
        triggers = RULE_TRIGGERS
    if conditional_rules is None:
        conditional_rules = CONDITIONAL_RULES

    query_lower = query.lower()
    matched = []
    for keywords, rule_name in triggers:
        for kw in keywords:
            if kw.lower() in query_lower:
                rule_text = conditional_rules.get(rule_name, "")
                if rule_text and rule_text not in matched:
                    matched.append(rule_text)
                break  # 一个规则组命中一次即可
    return "\n".join(matched)


# ============================================================
# 向后兼容：完整规则 = Layer 1 + 全部 Layer 2
# ============================================================
SQL_RULES = CORE_RULES.strip() + "\n" + "\n".join(CONDITIONAL_RULES.values())