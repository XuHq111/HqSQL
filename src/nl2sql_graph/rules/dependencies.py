"""表依赖规则 -- 三层防护的根基"""

DEPENDENCY_RULES = """
表依赖规则（硬性约束，不可违反，优先级高于相似度分数）：
1. 查询涉及 收入/费用/利润/成本 且使用了 master_txn_table → 必须包含 chart_of_accounts，用 account_type 过滤
2. 查询涉及 客户详细信息（地址、余额、全名等）→ 必须包含 customers
3. 查询涉及 供应商详细信息（地址、余额等）→ 必须包含 vendors
4. 查询涉及 支付方式是否信用卡 → 必须包含 payment_method
5. 查询涉及 员工是否在职 → 必须包含 employees 并过滤 Deleted='no'
6. 按产品/服务维度分组统计 → 直接用 master_txn_table.Product_Service GROUP BY，不需要 products 表
7. 仅当需要 Product_Service_type 产品分类维度时，才包含 products 表
"""

# (触发关键词列表, 强制包含的表)
DEPENDENCY_TRIGGERS = [
    (['sales', 'revenue', 'expense', 'income', 'profit', 'cost', 'selling', 'sell',
      '收入', '费用', '利润', '成本', '销售', '卖', '支出',
      'invoice', 'bill', 'deposit', 'debit', 'credit'], 'main.chart_of_accounts'),
    (['customer address', 'customer balance', 'customer full name', 'customer city',
      'customer state', 'billing address', 'shipping address',
      '客户地址', '客户余额', '客户全名', '客户城市', '客户信息',
      'billing_city', 'billing_state', 'shipping_city', '客户详情'], 'main.customers'),
    (['vendor address', 'vendor balance', 'vendor city', 'vendor state',
      '供应商地址', '供应商余额', '供应商信息', '供应商详情',
      'billing address', 'billing city'], 'main.vendors'),
    (['credit card', '信用卡', 'visa', 'mastercard', '支付方式是否信用卡',
      'payment method is credit'], 'main.payment_method'),
    (['employee', 'staff', 'hire', 'billing rate', '员工', '在职', '离职',
      'employee_name', 'employee_id', 'deleted'], 'main.employees'),
    (['product type', 'product category', 'service type', '产品类型', '产品分类',
      'product_service_type'], 'main.products'),
]
