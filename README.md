# agent-service ⚡

生产级异步 AI Agent 服务，基于 **FastAPI + SQLAlchemy 2.0 + Redis + Docker** 构建。
将 ReAct 多轮推理能力封装成标准 HTTP/SSE 服务，支持工具调用追踪、状态持久化、RAG 评估和 CI/CD 自动检查。

---

## 项目目标

把已有的 Agent 核心能力（ReAct 循环、工具注册、流式输出、记忆与 RAG）服务化：

- 通过标准 REST + SSE 接口对外暴露 Agent 能力
- 全链路状态可追踪（会话 / 任务 / 工具调用 / 评估记录均持久化）
- 具备死循环防护、超时控制、并发安全、幂等去重
- 一键 Docker 本地启动，GitHub Actions CI/CD 自动验证

---

## 系统架构

```
                   +-------------------+
                   |   Client / CLI    |
                   +---------+---------+
                             | HTTP / SSE
                             v
                   +-------------------+
                   |     FastAPI       |
                   | Middleware:       |
                   |  - 滑动窗口限流   |
                   |  - 幂等 key 去重  |
                   |  - 结构化日志     |
                   +----+--------+-----+
                        |        |
          +-------------v--+  +--v------------------+
          | Redis          |  | SQLAlchemy 2.0 Async|
          | - 分布式锁      |  | - AsyncSession      |
          | - ZSet 限流     |  | - PostgreSQL/SQLite |
          | - 幂等 key TTL  |  +---------------------+
          +----------------+
                             |
               [ReAct Async Execution Loop]
               ┌─────────────────────────────────────┐
               │ User Input                          │
               │   → Thought (LLM Decide)            │
               │   → Tool Call (asyncio.wait_for)    │
               │   → Tool Output → History           │
               │   → 循环直到 final_answer 或超步    │
               │ Final Answer                        │
               │   → DB 写入 → eval_queue 入队       │
               └─────────────────────────────────────┘
                             |
               [Background RAG Evaluator Worker]
               asyncio.Queue → 词频交集 Recall → EvaluationModel
```

---

## 核心模块说明

| 模块 | 文件 | 职责 |
|------|------|------|
| 生命周期管理 | `app/main.py` | lifespan 启动 DB/Redis，后台 eval worker |
| 配置 | `app/config.py` | Pydantic Settings，读取环境变量 |
| 数据库 | `app/database.py` | 异步 SQLAlchemy engine + session factory |
| Redis | `app/redis.py` | 分布式锁、滑动窗口限流、幂等 key |
| ORM 模型 | `app/models.py` | Session/Message/Task/ToolCall/Memory/Evaluation |
| Pydantic 模型 | `app/schemas.py` | 请求/响应序列化，ConfigDict V2 风格 |
| Agent 核心 | `app/agent.py` | ReAct 循环，SSE 生成器，eval_queue worker |
| 工具集 | `app/tools.py` | read_file / execute_command / web_search / rag_query |
| 中间件 | `app/middleware.py` | 限流、请求耗时日志 |
| 路由 | `app/routers.py` | REST endpoints + SSE chat 流式接口 |
| 测试 | `tests/` | pytest-asyncio，HTTPX AsyncClient，Mock LLM |

---

## API 设计说明

```
POST   /sessions                      创建会话
GET    /sessions/{session_id}         查询会话与消息历史
POST   /sessions/{session_id}/chat    发起推理（SSE 流式返回）
GET    /tasks/{task_id}               查询任务状态及工具调用记录
GET    /tasks/{task_id}/evaluations   查询 RAG 评估指标
GET    /healthz                       健康检查
GET    /docs                          OpenAPI Swagger UI
```

### SSE 事件流格式

```
data: {"event": "task_created", "task_id": "task_abc123"}
data: {"event": "thought",      "thought": "...", "step": 0}
data: {"event": "tool_start",   "tool": "rag_query", "args": {...}}
data: {"event": "tool_end",     "tool": "rag_query", "output": "...", "duration": 0.52}
data: {"event": "final_answer", "answer": "..."}
data: {"event": "error",        "error": "..."}
```

---

## 数据库设计说明

使用 SQLAlchemy 2.0 ORM + Alembic 迁移，默认 SQLite（本地），生产切换 PostgreSQL。

```
sessions         会话表
  id (PK)        string(64)，客户端指定
  meta           JSON，扩展元数据
  created_at / updated_at

messages         消息历史表
  id (PK)        autoincrement
  session_id     FK → sessions，带索引
  role           system / user / assistant / tool
  content        text
  created_at

tasks            任务执行记录表
  id (PK)        string(64)，task_uuid
  session_id     FK → sessions，带索引
  status         pending / running / completed / failed
  result         JSON，最终答案或错误
  created_at

tool_calls       工具调用明细表
  id (PK)
  task_id        FK → tasks，带索引
  tool_name      string(64)
  arguments      JSON
  output         text，工具返回值
  status         running / success / failed
  duration       float，执行耗时（秒）
  created_at

memories         记忆表
  id (PK)
  session_id     FK → sessions
  content        text
  created_at

evaluations      RAG 评估记录表
  id (PK)
  task_id        FK → tasks，带索引
  metric_name    string(64)，如 rag_semantic_overlap
  score          float，0.0–1.0
  details        JSON，词集详情
  created_at
```

**SQLite vs PostgreSQL 边界**：本地开发用 SQLite（aiosqlite），生产环境切换 asyncpg 驱动，
通过 `DATABASE_URL` 环境变量控制，代码零改动。JSONB、全文检索、pgvector 在 PostgreSQL 下可直接启用。

---

## 缓存与任务状态设计

Redis 用于短期状态，PostgreSQL 用于持久化状态，职责严格分离：

| 数据 | 存储 | TTL | 说明 |
|------|------|-----|------|
| 幂等 key | Redis String | 300s | `idempotency:{key}` → 防重复提交 |
| 并发锁 | Redis String (SET NX) | 30s | `lock:session:{id}` → 同会话单并发 |
| 限流计数 | Redis ZSet | 60s 滑动窗口 | `rate:{ip}` → 每分钟 100 请求上限 |
| 任务状态 | PostgreSQL tasks | 永久 | pending/running/completed/failed |
| 工具调用 | PostgreSQL tool_calls | 永久 | 完整调用链路追踪 |
| RAG 评估 | PostgreSQL evaluations | 永久 | 可查询历史评估趋势 |

---

## Agent 执行流程

```
1. POST /sessions/{id}/chat
2. 幂等 key 检查（Redis）
3. 创建 TaskModel（DB，status=pending）
4. 获取分布式锁（Redis NX）
5. 加载会话消息历史（DB）
6. ReAct 循环（最多 MAX_REACT_STEPS=10 步）：
   a. LLM 决策 → Thought SSE
   b. 若 final_answer → 写 DB → eval_queue → break
   c. 若 tool_call：
      - 写 ToolCallModel（status=running）
      - asyncio.wait_for(tool.execute(), TOOL_TIMEOUT)
      - 更新 ToolCallModel（output/status/duration）
      - Tool Output SSE → 追加 history
      - step += 1
7. 超步 → 写 failed + Loop Protection 错误 SSE
8. CancelledError → 写 failed + 释放锁
9. finally → 释放分布式锁
10. 后台 eval_queue worker 异步计算 RAG Recall 并持久化
```

---

## 工具调用设计

工具通过装饰器注册到全局 `tool_registry`，统一接口：

```python
@register_tool("read_file", "Read text file. Args: path (str)")
async def read_file_tool(path: str) -> str: ...

@register_tool("execute_command", "Run bash command. Args: command (str)")
async def execute_command_tool(command: str) -> str: ...

@register_tool("web_search", "Search web. Args: query (str)")
async def web_search_tool(query: str) -> str: ...

@register_tool("rag_query", "Query knowledge base. Args: query (str)")
async def rag_query_tool(query: str) -> str: ...
```

每次调用均包裹在 `asyncio.wait_for(tool.execute(...), timeout=TOOL_TIMEOUT)` 中，
超时自动捕获并记录 `status=failed`，不影响主循环继续。

---

## 状态管理设计

- **会话状态**：`SessionModel` 持久化，消息历史追加写，不可变。
- **任务状态机**：`pending → running → completed / failed`，状态变更均在 DB 事务中完成。
- **并发安全**：同一 `session_id` 同时只允许一个 ReAct 循环持有分布式锁，第二个请求立即返回 `Concurrent request detected`。
- **取消传播**：客户端断开 SSE 连接时，FastAPI 传播 `asyncio.CancelledError`，Agent 捕获后回滚 Task 状态为 `failed`，释放 Redis 锁。

---

## 死循环防护策略

三重防护：

1. **最大步数限制**：`MAX_REACT_STEPS=10`（环境变量可调），超过后强制终止并输出 `Loop Protection` 错误事件。
2. **单次工具超时**：`TOOL_TIMEOUT=30s`，`asyncio.wait_for` 包裹，超时工具调用标记 `failed` 并继续下一步。
3. **全局超时**：`GLOBAL_TIMEOUT=120s`，ReAct 循环总耗时超限时抛出 `asyncio.TimeoutError`，整体终止。

---

## 可观测性设计

- **结构化日志**：`logging` 输出到 stdout/stderr，格式包含 `task_id`、`tool_name`、耗时。
- **执行轨迹**：每个 ToolCall 记录 `arguments/output/status/duration` 到 DB，可通过 `GET /tasks/{id}` 查询完整链路。
- **SSE 实时流**：客户端实时接收 `thought/tool_start/tool_end/final_answer/error` 事件。
- **OpenAPI 文档**：`http://localhost:8000/docs` 自动生成。
- **健康检查**：`GET /healthz` 供 Docker/K8s 存活探针使用。

---

## RAG 评估方法

采用词频交集 Recall 作为轻量评估指标：

```
Recall = |QueryContextTokens ∩ AnswerTokens| / |QueryContextTokens|
```

实现：`rag_query` 或 `web_search` 工具被调用后，将输出内容推入 `eval_queue`；
Background Worker 从队列消费，计算 Recall 分数，写入 `evaluations` 表。

局限：此方法为词频级评估，生产环境可替换为 embedding 余弦相似度或 RAGAS 框架指标。

---

## 测试说明

```bash
source venv/bin/activate
pytest tests -v
```

测试覆盖（7 cases，均通过）：

| 测试 | 类型 | 验证点 |
|------|------|--------|
| 健康检查 | 集成 | /healthz 200 OK |
| 会话创建 | 集成 | POST /sessions 201 + DB |
| 会话查询 | 集成 | GET /sessions/{id} 200 |
| SSE 工具执行流 | E2E | run command → tool_start/tool_end/final_answer |
| 并发幂等去重 | 集成 | 相同 idempotency_key → 409 Conflict |
| 死循环防护 | E2E | trigger loop → Loop Protection error event |
| RAG 评估流 | E2E | query rag → evaluations 表写入 |

```bash
# 静态检查
ruff check app tests   # All checks passed
mypy --ignore-missing-imports app  # Success: no issues found in 12 source files
```

---

## Docker 启动说明

```bash
# 一键启动（PostgreSQL + Redis + FastAPI）
docker compose up --build -d

# 查看服务状态
docker compose ps

# 查看日志
docker compose logs -f app

# 停止
docker compose down -v
```

访问 `http://localhost:8000/docs` 查看 Swagger UI。

环境变量（在 `docker-compose.yml` 中配置）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | postgresql+asyncpg://... | DB 连接串 |
| `REDIS_URL` | redis://redis:6379/0 | Redis 连接串 |
| `LLM_API_KEY` | mock-key | LLM API Key |
| `MAX_REACT_STEPS` | 10 | ReAct 最大步数 |
| `TOOL_TIMEOUT` | 30.0 | 单工具超时（秒） |
| `GLOBAL_TIMEOUT` | 120.0 | 全局超时（秒） |

---

## CI/CD 说明

`.github/workflows/ci.yml` 在每次 push / PR 到 `main` 分支时自动触发：

```yaml
jobs:
  test:
    steps:
      - Checkout + Setup Python 3.14
      - pip install -r requirements.txt
      - ruff check app tests       # Lint
      - ruff format --check app tests  # Format
      - mypy --ignore-missing-imports app  # Type check
      - pytest tests               # 完整测试套件
```

所有检查均为阻塞性（任一失败则 CI 红色），不通过不允许合并。

---

## 失败 Case 分析

| 场景 | 现象 | 根因 | 处理方式 |
|------|------|------|----------|
| 工具超时 | tool_end `status=failed` | 网络慢 / 子进程阻塞 | `asyncio.wait_for` 超时捕获，记录 failed，继续循环 |
| LLM 死循环 | Loop Protection 错误 | LLM 反复调用同一工具 | MAX_REACT_STEPS 硬截止，task 标记 failed |
| 客户端断连 | CancelledError | SSE 连接中断 | 捕获后回滚 task 状态，释放 Redis 锁 |
| 并发重复请求 | 409 Conflict | 同 session 并发 | Redis NX 锁，第二个请求立即拒绝 |
| 重复幂等 key | 409 Conflict | 客户端重试 | Redis String TTL=300s 去重 |
| 工具路径越界 | Access Denied | 路径注入攻击 | read_file 工具做 /home/airc 前缀校验 |
| DB 连接耗尽 | 500 Internal Error | 高并发连接池满 | asyncpg 连接池配置 pool_size=20 |
| Pydantic 废弃警告 | DeprecationWarning | 旧式 class Config | 已迁移为 ConfigDict（本次修复）|

---

## GitHub 仓库

https://github.com/Ruanyuxi1337/agent-service
