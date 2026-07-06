import uuid
import logging
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import redis.asyncio as aioredis

from app.database import get_db
from app.redis import get_redis, is_duplicate_request
from app.models import SessionModel, TaskModel, EvaluationModel
from app.schemas import (
    SessionCreate,
    SessionResponse,
    ChatRequest,
    TaskResponse,
    EvaluationResponse,
)
from app.agent import ReActAgent

logger = logging.getLogger("agent_service")
router = APIRouter()


@router.post("/sessions", response_model=SessionResponse, status_code=201)
async def create_session(
    session_data: SessionCreate, db: AsyncSession = Depends(get_db)
):
    """Create or retrieve a conversational session."""
    # Check if session already exists
    stmt = select(SessionModel).filter_by(id=session_data.id)
    result = await db.execute(stmt)
    existing_session = result.scalar_one_or_none()

    if existing_session:
        return existing_session

    new_session = SessionModel(id=session_data.id, meta=session_data.meta)
    db.add(new_session)
    await db.commit()
    await db.refresh(new_session)
    return new_session


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    """Retrieve session history and status."""
    stmt = select(SessionModel).filter_by(id=session_id)
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.post("/sessions/{session_id}/chat")
async def chat_stream(
    session_id: str,
    chat_req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    r: aioredis.Redis = Depends(get_redis),
):
    """
    Chat with the ReAct Agent. Streams SSE events (thought, tool_start, tool_end, final_answer).
    Supports client side idempotency checks.
    """
    # 1. Check Session Exists
    stmt = select(SessionModel).filter_by(id=session_id)
    res = await db.execute(stmt)
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=404, detail="Session not found. Create one first."
        )

    # 2. Idempotency control
    id_key = chat_req.idempotency_key
    if id_key:
        is_dup = await is_duplicate_request(id_key)
        if is_dup:
            raise HTTPException(status_code=409, detail="Duplicate request detected.")

    # 3. Create long-running Task record in DB
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    task = TaskModel(id=task_id, session_id=session_id, status="pending")
    db.add(task)
    await db.commit()

    agent = ReActAgent(session_id=session_id, db=db, idempotency_key=id_key)

    # 4. Stream Response using SSE
    async def event_generator():
        # Yield the initial task ID so the client can monitor execution out-of-band
        yield f"data: {json.dumps({'event': 'task_created', 'task_id': task_id})}\n\n"

        try:
            async for sse_event in agent.execute_react_loop(task_id, chat_req.message):
                yield sse_event
        except Exception as e:
            logger.error(f"SSE generator encountered exception: {str(e)}")
            yield f"data: {json.dumps({'event': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task_status(task_id: str, db: AsyncSession = Depends(get_db)):
    """Retrieve detailed state of a ReAct execution loop, including all tool calls."""
    stmt = select(TaskModel).filter_by(id=task_id)
    res = await db.execute(stmt)
    task = res.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/tasks/{task_id}/evaluations", response_model=List[EvaluationResponse])
async def get_task_evaluations(task_id: str, db: AsyncSession = Depends(get_db)):
    """Retrieve RAG evaluation metrics for a specific completed task."""
    stmt = select(EvaluationModel).filter_by(task_id=task_id)
    res = await db.execute(stmt)
    evals = res.scalars().all()
    return list(evals)
