# 空结果诊断

## 诊断结果
SQL 语法正确但返回 0 行数据。

## 排查步骤
1. 检查 WHERE 条件是否过严（日期范围、金额阈值、状态过滤）
2. 检查 JOIN 条件是否正确（ON 子句是否同时匹配 businessID 和关联字段）
3. 检查是否错误过滤了数据（如 Credit > 0 过滤掉了实际有收入的记录）
4. 考虑放宽条件：先不加 WHERE 跑一次确认表里有数据，再逐步加条件
5. 只输出修正后的 SQLite SQL，不要解释

## 相关表结构
{schema_context}

{lookup_context}
{actual_columns}
