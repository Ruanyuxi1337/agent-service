import pytest
import json
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "agent-service",
        "version": "1.0.0",
    }


@pytest.mark.asyncio
async def test_session_lifecycle(client: AsyncClient):
    # 1. Create session
    session_id = "test_sess_001"
    response = await client.post(
        "/api/v1/sessions", json={"id": session_id, "meta": {"env": "test"}}
    )
    assert response.status_code == 201
    data = response.json()
    assert data["id"] == session_id
    assert data["meta"] == {"env": "test"}

    # 2. Retrieve session
    response = await client.get(f"/api/v1/sessions/{session_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == session_id

    # 3. Retrieve non-existent session
    response = await client.get("/api/v1/sessions/non_existent_session")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_chat_sse_stream(client: AsyncClient):
    session_id = "test_sess_stream"
    # Create session first
    await client.post("/api/v1/sessions", json={"id": session_id})

    # Call stream chat with mock command trigger
    response = await client.post(
        f"/api/v1/sessions/{session_id}/chat",
        json={"message": "run command: echo hello", "idempotency_key": "idemp_001"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

    # Consume the SSE stream
    events = []
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            event_data = json.loads(line[6:])
            events.append(event_data)

    # Verify event types are emitted
    assert len(events) > 0
    assert events[0]["event"] == "task_created"
    task_id = events[0]["task_id"]

    # Verify thought is emitted
    thoughts = [e for e in events if e.get("event") == "thought"]
    assert len(thoughts) > 0

    # Verify tool starts
    tool_starts = [e for e in events if e.get("event") == "tool_start"]
    assert len(tool_starts) > 0
    assert tool_starts[0]["tool"] == "execute_command"

    # Verify final answer is emitted
    answers = [e for e in events if e.get("event") == "final_answer"]
    assert len(answers) > 0
    assert (
        "hello" in answers[0]["answer"].lower()
        or "output" in answers[0]["answer"].lower()
        or "execution" in answers[0]["answer"].lower()
    )

    # Verify task state in database out-of-band
    response = await client.get(f"/api/v1/tasks/{task_id}")
    assert response.status_code == 200
    task_data = response.json()
    assert task_data["status"] == "completed"
    assert len(task_data["tool_calls"]) > 0
    assert task_data["tool_calls"][0]["tool_name"] == "execute_command"


@pytest.mark.asyncio
async def test_idempotency_deduplication(client: AsyncClient):
    session_id = "test_sess_idemp"
    await client.post("/api/v1/sessions", json={"id": session_id})

    # First request
    response1 = await client.post(
        f"/api/v1/sessions/{session_id}/chat",
        json={"message": "hello", "idempotency_key": "same_key_123"},
    )
    assert response1.status_code == 200

    # Second request with the same idempotency key (must be rejected with 409)
    response2 = await client.post(
        f"/api/v1/sessions/{session_id}/chat",
        json={"message": "hello", "idempotency_key": "same_key_123"},
    )
    assert response2.status_code == 409
    assert "Duplicate request" in response2.json()["detail"]
