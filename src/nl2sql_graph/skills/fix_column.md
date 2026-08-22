---
name: fix_column
description: 列名错误修复 — 用数据库实际列名替换 SQL 中不存在的列名
error_type: column_not_found
placeholders: [sql, query, actual_columns, wrong_col, suggested_col, requirement_context]
---

# 列名错误修复

## 原始查询（修复后的 SQL 必须满足，禁止改变业务语义）
{query}

## 待修复的 SQL（仅修正列名错误，保留全部过滤/JOIN/聚合/排序逻辑）
```sql
{sql}
```

## 诊断结果
SQL 使用了不存在的列名，数据库实际列名已列出。

{actual_columns}

错误列名 "{wrong_col}" 的建议替换: {suggested_col}

{requirement_context}

## 修复步骤
1. 找出 SQL 中所有不存在的列名（含子查询中的列），替换为上面对应的实际列名
2. 保持原有表、别名、JOIN、WHERE 过滤、GROUP BY、ORDER BY、LIMIT 等逻辑完全不变
3. 禁止将查询降级或简化为 `SELECT * FROM ...` 全表查询；禁止删除过滤/JOIN/聚合子句
4. 只输出修正后的完整 SQLite SQL，不要解释