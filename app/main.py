import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database import init_db, async_session_factory
from app.redis import redis_manager
from app.utils import setup_logging
from app.middleware import AgentServiceMiddleware
from app.routers import router as agent_router
from app.agent import run_evaluation_worker

logger = logging.getLogger("agent_service")

# Setup logging before FastAPI initializes
setup_logging()

# Background task reference to prevent GC
background_eval_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global background_eval_task
    # Startup lifecycle
    logger.info("Starting up Agent Service...")

    # 1. Initialize databases
    await init_db()

    # 2. Initialize Redis client
    redis_manager.initialize()

    # 3. Start background worker task
    background_eval_task = asyncio.create_task(
        run_evaluation_worker(async_session_factory)
    )

    yield

    # Shutdown lifecycle
    logger.info("Shutting down Agent Service...")

    # 1. Cancel background evaluation worker
    if background_eval_task:
        background_eval_task.cancel()
        try:
            await background_eval_task
        except asyncio.CancelledError:
            logger.info("Evaluation worker shutdown complete.")

    # 2. Close Redis client pool
    await redis_manager.close()
    logger.info("Agent Service shutdown complete.")


app = FastAPI(
    title="Async Agent Service Framework",
    description="Production-grade agent service supporting async multi-turn ReAct reasoning, tool execution, SSE, caching, Redis locking, and custom RAG evaluation.",
    version="1.0.0",
    lifespan=lifespan,
)

# Register custom middleware
app.add_middleware(AgentServiceMiddleware)

# Include main routers
app.include_router(agent_router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    """Simple API status checker."""
    return {"status": "healthy", "service": "agent-service", "version": "1.0.0"}
