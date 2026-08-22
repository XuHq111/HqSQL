---
name: fix_general
description: 通用修复兜底 — 注入核心约束和表结构，请求 LLM 修正未知类型错误
error_type: unknown
placeholders: [sql, query, core_rules, schema_context, lookup_context, actual_columns, requirement_context]
---

# SQL 修复

## 原始查询（修复后的 SQL 必须满足，禁止改变业务语义）
{query}

## 待修复的 SQL（保留全部过滤/JOIN/聚合/排序逻辑，禁止降级为全表查询）
```sql
{sql}
```

## 核心约束
{core_rules}

## 相关表结构
{schema_context}

{lookup_context}
{actual_columns}

{requirement_context}

## 任务
以上 SQL 执行失败，请根据错误信息和表结构修正。修复时务必保留对原始查询需求清单中每一条的覆盖，保留原有表、过滤、JOIN、聚合逻辑，禁止将查询降级或简化为 `SELECT * FROM ...` 全表查询。只输出 SQLite SQL。