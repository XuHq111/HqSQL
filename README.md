# HqSQL — 基于 LangGraph 的 NL2SQL 引擎

将自然语言查询自动转化为 SQLite SQL，结合 Milvus 向量检索、多层规则约束和智能修复闭环，确保生成 SQL 的准确性和可执行性。

## 核心设计

```
用户 NL Query
     │
     ├─ Stage 0: 查询澄清（多轮对话消歧义）
     ├─ Stage 0.5: 指标语义层（NL→指标 映射 + 指标→口径 确定性展开）
     ├─ Stage 1: 向量召回 → LLM 重排序 → 程序化兜底
     ├─ Stage 1.5: 值发现（探查数据库枚举值注入 prompt）
     ├─ Stage 2: SQL 生成（三层规则动态注入 + 已核定指标口径硬约束）
     └─ Stage 3: SQL 执行 + fix_agent 自修复闭环
```


## 项目结构

```
HqSQL/
├── src/
│   ├── nl2sql_graph/
│   │   ├── state.py              # LangGraph 共享状态定义
│   │   ├── graph_builder.py      # 拓扑构建（10 节点状态图）
│   │   ├── nodes/
│   │   │   ├── clarify.py        # Stage 0: 查询澄清节点
│   │   │   ├── semantic_map.py   # Stage 0.5: NL→指标 映射节点
│   │   │   ├── metric_expand.py  # Stage 0.5: 指标→口径 展开节点
│   │   │   ├── recall.py         # Stage 1: Milvus 向量召回
│   │   │   ├── rerank.py         # Stage 1: LLM 重排序
│   │   │   ├── enforce.py        # Stage 1: 程序化强制补入
│   │   │   ├── lookup_values.py  # Stage 1.5: 值发现
│   │   │   ├── build_prompt.py   # Stage 2: 构建生成 prompt
│   │   │   ├── generate.py       # Stage 2: LLM 生成 SQL
│   │   │   ├── execute_sql.py    # Stage 3: SQLite 执行
│   │   │   ├── validate_result.py# Stage 3: 结果校验 + 路由
│   │   │   └── fix_agent.py      # Stage 3: 智能修复 Agent
│   │   ├── rules/
│   │   │   ├── dependencies.py   # 表依赖规则 + 关键词触发器
│   │   │   ├── sql_rules.py      # SQL 生成规则（三层架构）
│   │   │   ├── value_lookups.py  # 值发现关键词→SQL 配置
│   │   │   ├── metrics.json      # ★ 指标语义层注册表（业务口径配置）
│   │   │   └── clarify_context.py# 澄清阶段的领域上下文
│   │   ├── services/
│   │   │   ├── llm.py            # DeepSeek LLM 调用封装
│   │   │   ├── milvus.py         # Milvus 向量检索封装
│   │   │   └── metrics.py        # ★ 指标注册表加载/校验/展开
│   │   ├── skills/               # fix_agent 的 Markdown Skill
│   │   │   ├── fix_column.md     # 列名错误修复策略
│   │   │   ├── fix_empty.md      # 空结果排查策略
│   │   │   ├── fix_syntax.md     # 语法错误修复策略
│   │   │   └── fix_general.md    # 兜底修复策略
│   │   └── tools/                # Skill 执行的工具函数
│   ├── evaluate/
│   │   └── eval_recall.py        # 召回层准确率评估脚本
│   ├── test/
│   │   ├── run_single.py         # 单条 NL 交互式测试
│   │   ├── test_batch.py         # 批量测试入口
│   │   └── test_recall.py        # 召回层单元测试
│   └── nl2sql.py                 # 批量查询入口脚本
├── table2milvus/                  # 离线数据准备（不入 git）
│   └── scripts/
│       ├── collection.py          # 创建 Milvus Collection
│       ├── tb2embedding.py        # 表元数据向量化入库
│       └── table_resource/
│           └── enhanced_descriptions.json  # 表增强描述
└── 架构全流程/                    # 架构文档
```

## 快速开始

### 环境要求

- Python 3.10+
- Milvus 向量数据库
- DeepSeek API Key（全部 LLM 调用，官方 API 为 OpenAI 兼容格式）
- DashScope API Key（仅用于 embedding，DeepSeek 官方 API 不提供 embedding 接口）
- SQLite 数据库文件

### 安装依赖

```bash
pip install pymilvus dashscope langgraph openai
```

### 配置 API Key

在项目根目录创建 `环境配置/api_keys.py`（已加入 .gitignore）：

```python
DASHSCOPE_API_KEY = "your-dashscope-api-key"   # 仅用于 embedding
DEEPSEEK_API_KEY = "your-deepseek-api-key"     # 用于 LLM 调用（deepseek-v4-flash）
```

### 数据准备（离线，首次执行）

1. 准备表增强描述 JSON 文件（描述每张表的角色、字段含义、JOIN 条件）
2. 创建 Milvus Collection 并向量化入库：

```bash
python table2milvus/scripts/collection.py
python table2milvus/scripts/tb2embedding.py
```

### 运行

```bash
# 单条交互式测试
python src/test/run_single.py

# 批量测试
python src/test/test_batch.py

# 召回层准确率评估
python src/evaluate/eval_recall.py
```




## 指标语义层（Stage 0.5）

把"业务口径"从 prompt 中沉淀为结构化配置（`src/nl2sql_graph/rules/metrics.json`），由两个节点以两段式执行：

1. **semantic_map**（LLM 轻量映射）：判断查询是否命中注册表指标，输出 `{metric, dims, granule, extra_filters}`；未命中/解析失败自动回退通用流程，不阻塞查询
2. **metric_expand**（确定性展开，不调 LLM）：把口径展开为 SQL 骨架 + 硬性需求条目（id 1001+），注入生成 prompt 的「已核定指标口径」段，并进入 semantic_validate / fix_agent 修复闭环——口径被改判时会被检出并自动修复

### 配置指标（改口径不需要改代码）

每个指标含：`name`（含 aliases 中英文别名）、`measure`（金额列 + 聚合）、`joins`（科目大类等口径 JOIN）、`filters`（口径过滤条件；`func:` 前缀按 SQL 表达式输出，如 `func:date('now')`）、`dims`（维度白名单，含 `date_granules` 的维度支持 日/月/年 聚合）。

```bash
# 查看注册表与校验状态
curl http://localhost:8000/api/metrics

# 修改 metrics.json 后热加载（无需重启服务）
curl -X POST http://localhost:8000/api/metrics/reload
```

已建档指标：收入（income 类目 + invoice 交易，贷方 Credit 合计）、费用（expenses 类目 + bill 交易，借方 Debit 合计）、应收账款（accounts receivable (a/r) 科目 invoice 交易的 Open_balance 未清余额合计）、逾期金额（应收口径 + 到期日 `Due_DATE` 早于今天）。

> ⚠️ 数据事实（查库核实）：应收科目 16.7 万行全部 `AR_paid='paid'`，该字段在应收科目上无 `'--'` 值，故应收口径刻意不含 AR_paid 过滤。逾期口径同理：数据集止于 2023 年，"到期日早于今天"恒成立，逾期金额 ≈ 应收账款余额，属口径语义而非缺陷。若业务上需要"逾期 N 天"，请在 filters 中改用 `func:julianday('now') - julianday(t.Due_DATE) > N` 这类表达式。

### 测试

```bash
# 注册表单测（无需 Milvus，离线可跑）
python src/test/test_metric_registry.py

# 指标层端到端（命中 + 回退两条路径，需 Milvus）
python src/test/test_metric_e2e.py
```

## 设计原则

- **知识分层**：描述只管"是什么"（enhanced_descriptions），规则管"怎么查"（dependencies/sql_rules），值发现管"值是多少"（lookup_values），指标层管"口径是什么"（metrics.json），职责分离
- **渐进增强**：LLM 语义判断为主，程序化规则兜底，修复闭环确保最终可执行
- **安全性**：仅允许 SELECT，非查询语句前置拦截；金额字段语义硬约束（Credit/Debit ≠ Amount）
- **可观测性**：每个节点记录耗时，重试计数，告警不阻塞流程
