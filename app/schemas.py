from datetime import datetime
from typing import List, Optional, Any
from pydantic import BaseModel, Field


class MessageCreate(BaseModel):
    role: str = Field(..., description="Role of the sender: system, user, assistant")
    content: str = Field(..., description="Content of the message")


class MessageResponse(BaseModel):
    id: int
    session_id: str
    role: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


class SessionCreate(BaseModel):
    id: str = Field(..., description="Unique Session Identifier")
    meta: Optional[dict] = Field(default=None, description="Optional metadata")


class SessionResponse(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    meta: Optional[dict]
    messages: List[MessageResponse] = []

    class Config:
        from_attributes = True


class ChatRequest(BaseModel):
    message: str = Field(..., description="User prompt")
    idempotency_key: Optional[str] = Field(
        default=None, description="Client side unique request ID for deduplication"
    )


class ToolCallResponse(BaseModel):
    id: int
    task_id: str
    tool_name: str
    arguments: Any
    output: Optional[str]
    status: str
    duration: Optional[float]
    created_at: datetime

    class Config:
        from_attributes = True


class EvaluationResponse(BaseModel):
    id: int
    task_id: str
    metric_name: str
    score: float
    details: Optional[dict]
    created_at: datetime

    class Config:
        from_attributes = True


class TaskResponse(BaseModel):
    id: str
    session_id: str
    status: str
    result: Optional[dict]
    created_at: datetime
    tool_calls: List[ToolCallResponse] = []
    evaluations: List[EvaluationResponse] = []

    class Config:
        from_attributes = True
