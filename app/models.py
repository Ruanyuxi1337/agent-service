from datetime import datetime
from typing import List, Optional
from sqlalchemy import String, DateTime, ForeignKey, Float, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    meta: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    messages: Mapped[List["MessageModel"]] = relationship(
        "MessageModel",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    tasks: Mapped[List["TaskModel"]] = relationship(
        "TaskModel",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    memories: Mapped[List["MemoryModel"]] = relationship(
        "MemoryModel",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class MessageModel(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(32))  # system, user, assistant, tool
    content: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped["SessionModel"] = relationship(
        "SessionModel", back_populates="messages"
    )


class TaskModel(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default="pending"
    )  # pending, running, completed, failed
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped["SessionModel"] = relationship(
        "SessionModel", back_populates="tasks"
    )
    tool_calls: Mapped[List["ToolCallModel"]] = relationship(
        "ToolCallModel",
        back_populates="task",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    evaluations: Mapped[List["EvaluationModel"]] = relationship(
        "EvaluationModel",
        back_populates="task",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ToolCallModel(Base):
    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    tool_name: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict] = mapped_column(JSON)
    output: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String(32))  # running, success, failed
    duration: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )  # execution time in seconds
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task: Mapped["TaskModel"] = relationship("TaskModel", back_populates="tool_calls")


class MemoryModel(Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped["SessionModel"] = relationship(
        "SessionModel", back_populates="memories"
    )


class EvaluationModel(Base):
    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    metric_name: Mapped[str] = mapped_column(
        String(64)
    )  # e.g., retrieval_precision, response_similarity
    score: Mapped[float] = mapped_column(Float)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task: Mapped["TaskModel"] = relationship("TaskModel", back_populates="evaluations")
