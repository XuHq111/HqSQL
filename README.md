# HqSQL — 基于 LangGraph 的 NL2SQL 引擎

将自然语言查询自动转化为 SQLite SQL，结合 Milvus 向量检索、多层规则约束和智能修复闭环，确保生成 SQL 的准确性和可执行性。

## 核心设计

```
用户 NL Query
     │
     ├─ Stage 0: 查询澄清（多轮对话消歧义）
     ├─ Stage 1: 向量召回 → LLM 重排序 → 程序化兜底
     ├─ Stage 1.5: 值发现（探查数据库枚举值注入 prompt）
     ├─ Stage 2: SQL 生成（三层规则动态注入）
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
│   │   │   └── clarify_context.py# 澄清阶段的领域上下文
│   │   ├── services/
│   │   │   ├── llm.py            # DashScope LLM 调用封装
│   │   │   └── milvus.py         # Milvus 向量检索封装
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
- DashScope API Key（阿里云通义千问）
- SQLite 数据库文件

### 安装依赖

```bash
pip install pymilvus dashscope langgraph
```

### 配置 API Key

在项目根目录创建 `环境配置/api_keys.py`（已加入 .gitignore）：

```python
DASHSCOPE_API_KEY = "your-dashscope-api-key"
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




## 设计原则

- **知识分层**：描述只管"是什么"（enhanced_descriptions），规则管"怎么查"（dependencies/sql_rules），值发现管"值是多少"（lookup_values），职责分离
- **渐进增强**：LLM 语义判断为主，程序化规则兜底，修复闭环确保最终可执行
- **安全性**：仅允许 SELECT，非查询语句前置拦截；金额字段语义硬约束（Credit/Debit ≠ Amount）
- **可观测性**：每个节点记录耗时，重试计数，告警不阻塞流程
