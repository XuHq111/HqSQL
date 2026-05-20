# HqSQL Web 前端改造设计

## 背景

HqSQL 当前是一个 CLI 形态的 NL2SQL 引擎，通过 `python src/nl2sql.py` 在终端运行预定义查询。需要将其改造为企业内部 Web 应用，让业务人员通过浏览器以对话方式查询数据库。

## 目标与约束

| 维度 | 决策 |
|------|------|
| 用户 | 企业内部业务人员（财务/运营） |
| 数据源 | 当前 SQLite，架构预留 MySQL/PostgreSQL 扩展 |
| 部署 | 纯内网，浏览器访问，无需登录认证 |
| 时间 | 1-2 周快速原型 |
| 前端技术 | HTMX + Tabulator.js（CDN 引入，零构建工具链） |
| 后端技术 | FastAPI + SSE 流式推送 |

## 整体架构

```
浏览器 (chat.html)
  │  HTTP + SSE
FastAPI (api.py)
  │  Python 调用
LangGraph 引擎层 (src/nl2sql_graph/)
  │  adapter 抽象
数据库 (SQLite / MySQL / PostgreSQL)
```

引擎层本身不嵌入 Web 框架，只在调用侧新增适配层和回调注入。

## 一、数据库适配层

新增 `src/nl2sql_graph/services/db_adapter.py`：

```python
class BaseDBAdapter(ABC):
    @abstractmethod
    def connect(self): ...
    @abstractmethod
    def execute(self, sql: str) -> tuple[list[str], list[list]]: ...
    @abstractmethod
    def get_columns(self, table_name: str) -> list[dict]: ...
    @abstractmethod
    def table_exists(self, table_name: str) -> bool: ...

class SQLiteAdapter(BaseDBAdapter):    # 现有逻辑迁移
class MySQLAdapter(BaseDBAdapter):     # 后续扩展
class PostgresAdapter(BaseDBAdapter):  # 后续扩展
```

### 对现有代码的影响

- `execute_sql.py`：从硬编码 SQLite 路径改为通过 adapter 实例执行
- `fix_agent.py`：`PRAGMA table_info()` 改为 `adapter.get_columns()` 抽象方法
- `graph_builder.py`：`build_graph(collection, db_adapter)` 多一个参数
- `lookup_values.py`：同样通过 adapter 执行 SELECT DISTINCT

## 二、clarify 节点 Web 化

当前 `clarify.py` 使用 LangGraph 的 `interrupt()` 暂停图执行等待终端输入。Web 形态改为回调函数注入：

- `graph_builder.py` 新增可选参数 `on_clarify_ask`
- `clarify.py` 将 `interrupt(payload)` 替换为 `on_clarify_ask(questions, enhanced_query)` 调用
- FastAPI 层提供回调实现：SSE 推送给前端 → 前端展示澄清卡片 → 用户填写后 POST 回来 → 回调返回

```
浏览器                    FastAPI                       LangGraph
  │  POST "卖得好的产品"    │                             │
  │────────────────────────→│  graph.invoke(state)        │
  │                         │────────────────────────────→│
  │  SSE: {clarify, q1, q2} │← 回调触发                 │
  │←────────────────────────│                             │
  │  POST 澄清回复           │  回调返回用户响应            │
  │────────────────────────→│────────────────────────────→│
  │  SSE: 进度 + 结果...     │  继续执行                   │
```

## 三、FastAPI 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/chat` | 发起查询 / 提交澄清回复 |
| `GET` | `/api/chat/stream` | SSE 进度订阅 |
| `GET` | `/api/chats/{id}` | 历史对话详情 |

### POST /api/chat 请求体

```json
{
  "session_id": "abc123",
  "message": "上个月收入多少？",
  "clarify_response": null
}
```

### SSE 事件类型

| event | payload | 前端行为 |
|-------|---------|---------|
| `progress` | `{step, detail}` | 展示进度提示 |
| `clarify` | `{questions, enhanced_query}` | 展示澄清卡片 |
| `result` | `{sql, columns, rows, timing}` | 渲染 SQL + Tabulator 表格 |
| `error` | `{code, detail}` | 展示错误提示 |

### 会话管理

- session 存储在内存 `dict[str, OverallState]`，key 为 UUID
- 24 小时超时自动清理
- 每个 session 绑定全局单例的 collection 和 db_adapter 引用
- 不做登录认证（纯内网）

## 四、前端页面

单文件 `static/chat.html`，CDN 引入 HTMX + Tabulator.js，零构建工具链。

### 交互流程

1. 用户输入查询 → POST 发送
2. SSE 推送进度事件 → 在对话区展示进度提示
3. 如果触发 clarify → 展示澄清卡片 → 用户填写 → POST 回复
4. 收到 result 事件 → JS 初始化 Tabulator 表格

### 页面布局

- 横幅标题栏：HqSQL · 智能数据查询
- 对话滚动区：用户气泡 + 进度条 + SQL 代码块 + 结果表格
- 底部输入区：文本输入框 + 发送按钮

### 表格功能

- 列排序（点击表头）
- 分页（Tabulator 内置）
- CSV/Excel 导出（Tabulator 内置按钮）

## 五、改造量估算

| 文件 | 类型 | 改动量 | 说明 |
|------|------|--------|------|
| `services/db_adapter.py` | 新增 | ~80行 | 数据库抽象层 |
| `nodes/execute_sql.py` | 修改 | ~10行 | 改用 adapter |
| `nodes/fix_agent.py` | 修改 | ~15行 | PRAGMA → get_columns() |
| `graph_builder.py` | 修改 | ~10行 | 新增参数 |
| `nodes/lookup_values.py` | 修改 | ~5行 | 改用 adapter |
| `nodes/clarify.py` | 修改 | ~15行 | interrupt → 回调 |
| `api.py` | 新增 | ~150行 | FastAPI + SSE |
| `static/chat.html` | 新增 | ~300行 | 单页前端 |

总计约 600 行新增/修改，核心 LangGraph 流水线逻辑不动。
