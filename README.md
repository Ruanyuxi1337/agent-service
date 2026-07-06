# Production-Grade Async Agent Service

An asynchronous, production-ready AI Agent Service built with Python, FastAPI, SQLAlchemy 2.0, Redis, and Docker. It encapsulates a robust ReAct (Reasoning and Action) loop, dynamic tool calling execution, and structural session persistence.

## 🌟 Key Features

1. **Async & Concurrency Control**: Uses Python's native `asyncio` for multi-turn conversational agents. Implements distributed locks in Redis to prevent concurrent operations on the same session, and token bucket ZSET rate-limiting to prevent service abuse.
2. **ReAct & Tooling Loop**: Implements an extensible agent tool executor with active protection:
   - **Timeout Constraints**: Individual tool calls are wrapped inside an asynchronous `wait_for` timer to prevent blocking.
   - **Infinite Loop Protection**: Restricts the maximum number of reasoning steps. Automatically marks the task as failed and logs it if the limit is exceeded.
   - **Cancellation Propagation**: If the client disconnects (e.g. terminates the SSE stream), the backend catches `asyncio.CancelledError`, cancels active tool subtasks, releases session locks, and updates task records.
3. **SSE (Server-Sent Events) Stream**: Streams agent thoughts, tool start, tool output, duration, and final response in real-time.
4. **State Persistence (SQLAlchemy 2.0)**: Supports PostgreSQL and SQLite dynamically. Relational models cover Sessions, Messages, Tasks (agent executions), Tool Calls, Memories, and Evaluations. Includes Alembic database migrations.
5. **RAG Evaluation System**: Features an asynchronous background evaluation queue that calculates retrieval recall (semantic word overlaps) for responses generated using retrieval/search tools, saving structured evaluation scores back to the database.
6. **Robust OOTB Validation**: Built-in 100% passing testing suite leveraging `pytest-asyncio` with isolated test DB fixtures and fully mocked Redis pipelines. Fully compliant with Ruff linter, formating, and Mypy strict type checks.

---

## 🏗️ System Architecture

```
                       +-------------------+
                       |    Client/CLI     |
                       +---------+---------+
                                 |  HTTP POST / GET / SSE (Stream)
                                 v
                       +-------------------+
                       |      FastAPI      | (Middleware: Rate Limiting, JSON Logs, Idempotency)
                       +----+---------+----+
                            |         |
           +----------------v----+    +----------------v----+
           |  Redis Cache & Lock |    | SQLAlchemy 2.0 Engine |
           |  (Concurrency Lock) |    +---------+-----------+
           +---------------------+              |
                                                v
                                      +---------------------+
                                      | PostgreSQL / SQLite | (Sessions, Tasks, Tool Calls, Evaluations)
                                      +---------------------+

       [Agent Execution Context (ReAct Loop)]
       +-------------------------------------------------------------+
       |                                                             |
       |  User Prompt -> Decide Tool Call -> Execute Tool (timeout)  |
       |       ^                                  |                  |
       |       |------------------<---------------+                  |
       |                                                             |
       |  Final Answer -> Write DB -> Queue Background RAG Eval      |
       +-------------------------------------------------------------+
```

---

## 📂 Core Modules

- **`app/main.py`**: Lifespan manager, initializes DB tables/migrations, starts the background RAG evaluation worker, and wires middleware.
- **`app/config.py`**: Global environment variable settings using `pydantic-settings`.
- **`app/database.py`**: Async SQLAlchemy connection engine, session maker, and database dependency.
- **`app/redis.py`**: Redis client manager, sliding window rate-limiter, distributed locks using Lua script, and idempotency key checks.
- **`app/models.py`**: Relational database ORM schemas (SQLAlchemy Declarative).
- **`app/schemas.py`**: Serialization/Deserialization models (Pydantic V2).
- **`app/agent.py`**: The ReAct execution agent containing the loop logic, mock LLM selector, and background evaluation worker.
- **`app/tools.py`**: Built-in async tools (`read_file`, `execute_command`, `web_search`, `rag_query`).
- **`app/middleware.py`**: Global JSON logger, rate-limiter check, and execution duration tracker.
- **`app/routers.py`**: REST API and SSE endpoint routes.
- **`app/utils.py`**: Custom logging formatter outputting single-line JSON log formats to stdout.

---

## 📊 Database Schema Design

This project uses SQLAlchemy 2.0. Six main tables track execution states:

1. **`sessions`**: Root table tracking individual conversational threads.
   - `id` (PK, String)
   - `created_at` / `updated_at` (DateTime)
   - `meta` (JSONB / JSON)
2. **`messages`**: Multi-turn history.
   - `id` (PK, Auto-increment)
   - `session_id` (FK -> sessions.id, index)
   - `role` (String: system, user, assistant, tool)
   - `content` (Text)
   - `created_at` (DateTime)
3. **`tasks`**: Tracks individual ReAct execution iterations.
   - `id` (PK, String)
   - `session_id` (FK -> sessions.id, index)
   - `status` (String: pending, running, completed, failed)
   - `result` (JSON: containing answer and total steps)
   - `created_at` (DateTime)
4. **`tool_calls`**: Structured records of each tool called during a task.
   - `id` (PK, Auto-increment)
   - `task_id` (FK -> tasks.id, index)
   - `tool_name` (String)
   - `arguments` (JSON)
   - `output` (Text)
   - `status` (String: running, success, failed)
   - `duration` (Float, execution time in seconds)
   - `created_at` (DateTime)
5. **`memories`**: Relational agent memories.
   - `id` (PK, Auto-increment)
   - `session_id` (FK -> sessions.id)
   - `content` (Text)
   - `created_at` (DateTime)
6. **`evaluations`**: Evaluation stats automatically triggered for RAG queries.
   - `id` (PK, Auto-increment)
   - `task_id` (FK -> tasks.id, index)
   - `metric_name` (String)
   - `score` (Float)
   - `details` (JSON)
   - `created_at` (DateTime)

---

## 🔑 Caching, Concurrency & Idempotency

- **Distributed Locks**: To prevent race conditions where a client submits multiple concurrent messages inside a single session, we enforce a session-based lock:
  ```python
  res = await self.redis.set(self.key, self.token, nx=True, px=self.ttl_ms)
  ```
  Unlocks utilize a safe Lua script script to assert that only the original lock creator can delete the key.
- **Idempotency checks**: Clients can send requests with an optional `X-Idempotency-Key` or JSON body `idempotency_key`. Redis checks:
  ```python
  is_new = await r.set(f"idempotency:{key}", "1", ex=ttl, nx=True)
  ```
  If `is_new` is false, FastAPI immediately throws a `409 Conflict`.
- **Rate limiting**: Middleware intercepts requests and calculates rolling-window API calls under 60 seconds per IP, returning `429 Too Many Requests` when limits are reached.

---

## 🌀 ReAct Stream & Protection Mechanics

When requesting `/api/v1/sessions/{session_id}/chat`, the server returns a `text/event-stream` SSE flow. The payload format is:

- **`task_created`**: Returns the `task_id` instantly.
- **`thought`**: Streams the reasoning thought-flow before tool invocation.
- **`tool_start`**: Streams the target tool name and arguments.
- **`tool_end`**: Emits tool execution outputs and execution time duration.
- **`final_answer`**: Emits the final synthesized LLM output response.
- **`error`**: Dispatched during timeouts, loop crashes, or system failures.

### Protective Guards

- **Tool Timeout**:
  ```python
  tool_output = await asyncio.wait_for(tool_obj.execute(**args), timeout=settings.TOOL_TIMEOUT)
  ```
- **Loop Protection**: If an agent starts call-loop cycles, the maximum ReAct step count `MAX_REACT_STEPS` intercepts:
  ```python
  if self.current_step >= settings.MAX_REACT_STEPS:
      raise LoopProtectionError()
  ```
- **Cancellation Propagation**: If the HTTP/SSE connection closes, the FastAPI framework raises a `CancelledError`. The backend gracefully updates the DB state of the task, releases the Redis lock, and cleans up sub-tasks.

---

## 🧪 RAG Evaluation Method

When an agent completes a task that invoked search or knowledge RAG tools (`rag_query` / `web_search`), it queues an evaluation payload in the asyncio background queue.
The sequential queue processor computes the semantic recall index based on word token intersections:
$$Recall = \frac{|QueryContextTokens \cap AnswerTokens|}{|QueryContextTokens|}$$
Scores are written to the database under the `evaluations` table, enabling observability of RAG factual alignment.

---

## 🛠️ Getting Started & Launch Instructions

### Prerequisites
- Python 3.14+ (or running via Docker Compose)
- Redis and PostgreSQL (or SQLite locally)

### Docker Compose Startup (Postgres & Redis & FastAPI App)

To boot up the complete microservice architecture locally:

```bash
# Clone the repository
git clone <repository_url>
cd agent-service

# Boot services
docker-compose up --build -d

# Check service status
docker-compose ps
```

The server will be reachable at `http://localhost:8000`. Swagger API docs are available at `http://localhost:8000/docs`.

### Local Manual Installation & Testing

```bash
# Install virtualenv and packages
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run lint checks
venv/bin/ruff check app

# Run Mypy Type checks
venv/bin/mypy --ignore-missing-imports app

# Run tests
venv/bin/pytest tests
```

---

## 🚀 CI/CD Pipeline

The project includes a Github Actions CI workflow (`.github/workflows/ci.yml`) triggering on pushes or PRs to `main`. The pipeline automatically executes:
1. Ruff Linter check.
2. Ruff Code Formatter verification.
3. Mypy static type verification.
4. Pytest test-suite execution (SQLite memory mode, fully mocked Redis connection).
