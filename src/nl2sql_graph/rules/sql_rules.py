"""SQL 生成规则"""

SQL_RULES = """
SQL 生成规则：
- 使用 SQLite 语法
- 日期函数用 strftime() 或 date()，相对日期用 'now' 而非硬编码
- 所有表名带 main. 前缀
- 多租户：所有查询加 businessID 过滤（可用 ? 占位符或假设 businessID=2）
- 金额字段：收入用 Credit，费用用 Debit
- Transaction_TYPE：收入='invoice'，费用='bill'，存款='deposit'
- account_type 枚举（小写）：'income','other income','expenses','other expense','accounts receivable (a/p)','accounts payable (a/p)'...
"""
