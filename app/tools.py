import subprocess
import os
import logging
import asyncio
from typing import Dict, Callable, Awaitable

logger = logging.getLogger("agent_service")


# Tool Registry
class Tool:
    def __init__(
        self, name: str, description: str, func: Callable[..., Awaitable[str]]
    ):
        self.name = name
        self.description = description
        self.func = func

    async def execute(self, *args, **kwargs) -> str:
        try:
            return await self.func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error executing tool {self.name}: {str(e)}", exc_info=True)
            return f"Error executing tool {self.name}: {str(e)}"


tool_registry: Dict[str, Tool] = {}


def register_tool(name: str, description: str):
    def decorator(func: Callable[..., Awaitable[str]]):
        tool = Tool(name, description, func)
        tool_registry[name] = tool
        return func

    return decorator


# --- Actual Tool Implementations ---


@register_tool(
    "read_file", "Read text content from an absolute file path. Arguments: path (str)"
)
async def read_file_tool(path: str) -> str:
    # Basic security verification: restrict paths under home
    if not path.startswith("/home/airc") and not path.startswith("./"):
        return "Access Denied: You can only read files within user home space (/home/airc)."

    if not os.path.exists(path):
        return f"File not found: {path}"

    try:
        # Wrap blocking IO in asyncio executor
        def read():
            with open(path, "r", encoding="utf-8") as f:
                return f.read(5000)  # limit output size to prevent overflow

        loop = asyncio.get_running_loop()
        content = await loop.run_in_executor(None, read)
        return content
    except Exception as e:
        return f"Error reading file: {str(e)}"


@register_tool("execute_command", "Run bash shell command. Arguments: command (str)")
async def execute_command_tool(command: str) -> str:
    # Basic protection against destructive commands
    blacklisted = ["rm -rf /", "mkfs", "dd ", "shutdown", "reboot", ":(){:|:&};:"]
    for item in blacklisted:
        if item in command:
            return f"Security Violation: Command '{command}' is blacklisted."

    try:
        # Use asyncio create_subprocess_shell for non-blocking process execution
        process = await asyncio.create_subprocess_shell(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        # Timeout handled outside by wait_for, but let's do a basic wait
        stdout, stderr = await process.communicate()
        out = stdout.decode().strip()
        err = stderr.decode().strip()

        result = []
        if out:
            result.append(f"STDOUT:\n{out}")
        if err:
            result.append(f"STDERR:\n{err}")

        return (
            "\n".join(result)
            if result
            else "Command executed successfully with no output."
        )
    except Exception as e:
        return f"Execution error: {str(e)}"


@register_tool(
    "web_search",
    "Search information online or fetch web content. Arguments: query (str)",
)
async def web_search_tool(query: str) -> str:
    # Simulate a robust web search with Mocked high-quality results
    await asyncio.sleep(1.0)  # Simulate network lag
    logger.info(f"Simulating web search for: {query}")

    if "openclaw" in query.lower() or "nanoclaw" in query.lower():
        return (
            "Search Results:\n"
            "1. NanoClaw (nanoclaw.dev): A secure, lightweight alternative to OpenClaw. "
            "It runs agent groups inside Docker containers and uses Bun + SQLite per session.\n"
            "2. OpenClaw: An open-source AI agent system with thousands of lines, and the older "
            "v1 structure which used single process and shared memory architecture."
        )
    elif "claude code" in query.lower():
        return (
            "Search Results:\n"
            "1. Claude Code CLI: Official command-line tool from Anthropic for repository-level coding tasks, "
            "agentic loops, and interactive terminal-based codebase operations.\n"
            "2. learn-claude-code: A tutorial repo detailing the mechanics of building a Claude Code-like agent "
            "from scratch, covering agent loop, permissions, and tool execution."
        )
    else:
        return f"Search results for '{query}': Found general information matching your query. No specific pages returned."


@register_tool(
    "rag_query", "Query local knowledge base for agent contexts. Arguments: query (str)"
)
async def rag_query_tool(query: str) -> str:
    # Simulated RAG system with search matching and scoring
    await asyncio.sleep(0.5)  # Simulate RAG embedding lookup

    knowledge_base = [
        {
            "text": "Python asyncio has an event loop running single-threaded. Concurrency is achieved via cooperative multitasking.",
            "source": "async_doc",
        },
        {
            "text": "FastAPI lifespan events allow setup and cleanup of database engines and Redis connection pools.",
            "source": "fastapi_doc",
        },
        {
            "text": "PostgreSQL JSONB columns are highly optimized for semi-structured data, indexing, and JSON-path queries.",
            "source": "postgres_doc",
        },
        {
            "text": "Redis streams provide log-like structures with consumer groups, while Pub/Sub is fire-and-forget messaging.",
            "source": "redis_doc",
        },
        {
            "text": "Alembic handles database migrations by auto-generating migration scripts from SQLAlchemy metadata.",
            "source": "alembic_doc",
        },
    ]

    results = []
    query_words = query.lower().split()

    for doc in knowledge_base:
        score = sum(1.0 for word in query_words if word in doc["text"].lower())
        if score > 0:
            # simple score normalization
            score = score / len(query_words)
            results.append((score, doc))

    # Sort by score descending
    results.sort(key=lambda x: x[0], reverse=True)

    if not results:
        return "RAG Search: No matching documents found in knowledge base."

    output_parts = ["RAG Search Results (mocked vector recall):"]
    for rank, (score, doc) in enumerate(results, 1):
        output_parts.append(
            f"[{rank}] Score: {score:.2f} | Source: {doc['source']}\nContent: {doc['text']}"
        )

    return "\n\n".join(output_parts)
