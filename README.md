# agent-service ⚡

Production-grade asynchronous AI Agent ReAct routing service built with **Python**, **FastAPI**, **Redis**, **SQLAlchemy 2.0**, and **Docker**. It encapsulates a state-of-the-art multi-turn reasoning and tool calling engine designed for commercial scalability.

---

## 🏗️ System Architecture

```
                       +-------------------+
                       |    Client/CLI     |
                       +---------+---------+
                                 |  HTTP / SSE (Stream Flow)
                                 v
                       +-------------------+
                       |      FastAPI      | (Middleware: Sliding Rate Limiter, Logs, Idempotency)
                       +----+---------+----+
                            |         |
           +----------------v----+    +----------------v----+
           |  Redis Cache & Lock |    | SQLAlchemy 2.0 Engine |
           |  (Concurrency Mutex)|    +---------+-----------+
           +---------------------+              |
                                                v
                                      +---------------------+
                                      | PostgreSQL / SQLite | (Relational schema: Message, Task, Eval)
                                      +---------------------+

       [Async ReAct Execution Context]
       ┌─────────────────────────────────────────────────────────────┐
       │                                                             │
       │  User Input -> Thought -> Decide Tool -> Execute (timeout)   │
       │       ^                                  │                  │
       │       └──────────────────────────────────┘                  │
       │                                                             │
       │  Final Answer -> Outbox Write -> Queue Background RAG Eval   │
       └─────────────────────────────────────────────────────────────┘
```

---

## 🌟 Core Features

1. **Async & Concurrency Mutex**: Uses Python's native `asyncio` for multi-turn conversational agents. Implements distributed locks in Redis to prevent parallel operations on the same session state.
2. **ReAct & Tooling Loop Protection**:
   - **Timeout Constraints**: Individual tool calls are wrapped inside an asynchronous `wait_for` timer to prevent thread hang ups.
   - **HALLUCINATION SHIELD**: Restricts the maximum number of reasoning steps (`MAX_REACT_STEPS=10`). Automatically terminates and logs failed runs.
   - **Cancellation Propagation**: Captures `asyncio.CancelledError` on client disconnection (e.g., SSE connection drops), sending immediate termination signals to upstream LLM providers and releasing slot quotas.
3. **SSE (Server-Sent Events) Stream**: Streams agent thoughts, tool invocation, duration, and final answers in real-time.
4. **State Persistence**: Supports PostgreSQL and SQLite dynamically. Relational models cover Sessions, Messages, Tasks, Tool Calls, and Evaluation histories.
5. **Observed RAG Evaluation**: Async background queue computes semantic word intersections recall indexes:
   $$Recall = \frac{|QueryContextTokens \cap AnswerTokens|}{|QueryContextTokens|}$$

---

## 📂 Code Layout

```
├── app/
│   ├── main.py        # Lifespan manager, starts background evaluator worker
│   ├── config.py      # Pydantic Settings for environment management
│   ├── database.py    # Async SQLAlchemy connection engine
│   ├── redis.py       # Distributed locks & dynamic rate-limiter
│   ├── models.py      # SQLAlchemy 2.0 ORM schemas
│   ├── schemas.py     # Pydantic V2 serialization models
│   ├── agent.py       # ReAct execution and evaluator queue worker
│   ├── tools.py       # Built-in async tools (read, execute, rag)
│   ├── middleware.py  # Rate limiter middleware, duration logging
│   └── routers.py     # REST API and SSE endpoint routes
├── tests/
│   ├── test_api.py    # HTTP client integration tests
│   └── test_agent.py  # Mocked LLM tool execution tests
└── Dockerfile         # Multi-stage production build
```

---

## 🛠️ Installation & Launch

### Prerequisites
- Python 3.14+ or Docker Compose

### Fast Bootstrap (PostgreSQL + Redis + FastAPI App)

```bash
docker-compose up --build -d
docker-compose ps
```
The server will start at `http://localhost:8000`. Swagger API docs are available at `http://localhost:8000/docs`.

### Local Manual Testing

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run Quality Gates
venv/bin/ruff check app
venv/bin/mypy --ignore-missing-imports app
venv/bin/pytest tests
```
