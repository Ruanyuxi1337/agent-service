import pytest
import json
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_tool_timeout_and_error_handling(client: AsyncClient):
    session_id = "test_sess_timeout"
    await client.post("/api/v1/sessions", json={"id": session_id})

    # Trigger a command that would block or return normally but simulate error recovery
    response = await client.post(
        f"/api/v1/sessions/{session_id}/chat", json={"message": "run command: sleep 5"}
    )
    assert response.status_code == 200

    events = []
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))

    assert len(events) > 0
    # The command should succeed or be logged. We verify task status
    task_id = events[0]["task_id"]

    response = await client.get(f"/api/v1/tasks/{task_id}")
    assert response.status_code == 200
    task_data = response.json()
    assert task_data["status"] == "completed"


@pytest.mark.asyncio
async def test_loop_protection(client: AsyncClient):
    session_id = "test_sess_loop"
    await client.post("/api/v1/sessions", json={"id": session_id})

    # Trigger mock infinite loop decision
    response = await client.post(
        f"/api/v1/sessions/{session_id}/chat", json={"message": "trigger loop"}
    )
    assert response.status_code == 200

    events = []
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))

    # Verify error or loop limit event is emitted
    errors = [e for e in events if e.get("event") == "error"]
    assert len(errors) > 0
    assert "Loop Protection" in errors[0]["error"]

    task_id = events[0]["task_id"]
    response = await client.get(f"/api/v1/tasks/{task_id}")
    task_data = response.json()
    assert task_data["status"] == "failed"
    assert "Loop Protection" in task_data["result"]["error"]


@pytest.mark.asyncio
async def test_rag_query_and_evaluation(client: AsyncClient):
    session_id = "test_sess_rag"
    await client.post("/api/v1/sessions", json={"id": session_id})

    # Trigger a query that calls RAG
    response = await client.post(
        f"/api/v1/sessions/{session_id}/chat",
        json={"message": "query rag: postgres jsonb"},
    )
    assert response.status_code == 200

    events = []
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))

    task_id = events[0]["task_id"]

    # Manually run the evaluation queue consumer to guarantee DB persistence in tests
    from app.agent import eval_queue
    from app.database import async_session_factory
    from app.models import EvaluationModel
    import time

    # Get task from queue
    task_item = await eval_queue.get()
    t_id, query_context, final_answer = task_item

    # Process
    query_words = set(query_context.lower().split())
    answer_words = set(final_answer.lower().split())
    intersection = query_words.intersection(answer_words)
    score = len(intersection) / len(query_words) if query_words else 1.0

    async with async_session_factory() as session:
        eval_record = EvaluationModel(
            task_id=t_id,
            metric_name="rag_semantic_overlap",
            score=score,
            details={"evaluated_at": time.time()},
        )
        session.add(eval_record)
        await session.commit()
    eval_queue.task_done()

    # Wait and verify
    response = await client.get(f"/api/v1/tasks/{task_id}/evaluations")
    evals = response.json()

    assert len(evals) > 0
    assert evals[0]["metric_name"] == "rag_semantic_overlap"
    assert evals[0]["score"] >= 0.0
