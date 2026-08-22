---
name: fix_syntax
description: 语法错误修复 — 根据 SQLite 错误信息定位并修正语法问题
error_type: syntax_error
placeholders: [sql, query, error, core_rules, schema_context, requirement_context]
---

# 语法错误修复

## 原始查询（修复后的 SQL 必须满足，禁止改变业务语义）
{query}

## 待修复的 SQL（仅修正语法问题，保留全部过滤/JOIN/聚合/排序逻辑）
```sql
{sql}
```

## 诊断结果
SQL 有语法错误。

## 修复步骤
1. 根据错误信息定位问题
2. 确保是合法的 SQLite 语法
3. 检查表名前缀（main.）
4. 检查 JOIN 语法

## 错误信息
{error}

## 核心约束
{core_rules}

## 相关表结构
{schema_context}

{requirement_context}

## 任务
只输出修正后的 SQLite SQL，不要解释。修复语法错误时不得删除或简化现有的业务逻辑，禁止将查询降级为 `SELECT * FROM ...` 全表查询。