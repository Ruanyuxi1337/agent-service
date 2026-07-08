import json
import logging
import asyncio
import time
from typing import AsyncGenerator, Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.config import settings
from app.models import MessageModel, TaskModel, ToolCallModel, EvaluationModel
from app.tools import tool_registry
from app.redis import DistributedLock

logger = logging.getLogger("agent_service")

# Background evaluation task queue
eval_queue: asyncio.Queue = asyncio.Queue()


async def run_evaluation_worker(session_factory) -> None:
    """
    Background worker that runs evaluations sequentially or with limited concurrency.
    Retrieves tasks from eval_queue, calculates evaluation metrics, and persists to DB.
    """
    logger.info("RAG Evaluation worker started.")
    while True:
        try:
            task_id, query_context, final_answer = await eval_queue.get()
            logger.info(f"Starting evaluation for task {task_id}")

            # Simulated semantic evaluation (word overlap analysis)
            query_words = set(query_context.lower().split())
            answer_words = set(final_answer.lower().split())

            intersection = query_words.intersection(answer_words)
            # Simple retrieval recall score calculation
            score = len(intersection) / len(query_words) if query_words else 1.0

            # Connect to DB and save evaluation
            async with session_factory() as session:
                eval_record = EvaluationModel(
                    task_id=task_id,
                    metric_name="rag_semantic_overlap",
                    score=score,
                    details={
                        "query_context_words": list(query_words),
                        "intersection_words": list(intersection),
                        "evaluated_at": time.time(),
                    },
                )
                session.add(eval_record)
                await session.commit()
                logger.info(
                    f"Saved evaluation for task {task_id} with score {score:.2f}"
                )

        except asyncio.CancelledError:
            logger.info("Evaluation worker cancelled.")
            break
        except Exception as e:
            logger.error(f"Error in evaluation worker: {str(e)}", exc_info=True)
        finally:
            eval_queue.task_done()


class ReActAgent:
    def __init__(
        self, session_id: str, db: AsyncSession, idempotency_key: Optional[str] = None
    ):
        self.session_id = session_id
        self.db = db
        self.idempotency_key = idempotency_key
        self.current_step = 0
        self.rag_context_collected: List[str] = []

    def _mock_llm_decide(
        self, user_prompt: str, history: List[Dict[str, str]], step: int
    ) -> Dict[str, Any]:
        """
        Mock LLM to return tool calls or final answer based on user prompt.
        Allows testing full ReAct cycle without actual API key.
        """
        prompt = user_prompt.lower()

        # Scenario: Read File
        if "read file" in prompt and step == 0:
            parts = user_prompt.split(":")
            filepath = (
                parts[1].strip()
                if len(parts) > 1
                else "/home/airc/agent-service/app/config.py"
            )
            return {
                "thought": f"The user wants me to read a file. I will use the 'read_file' tool for path '{filepath}'.",
                "tool_call": {"name": "read_file", "arguments": {"path": filepath}},
            }

        # Scenario: Execute command
        elif "run command" in prompt and step == 0:
            parts = user_prompt.split(":")
            cmd = parts[1].strip() if len(parts) > 1 else "ls"
            return {
                "thought": f"The user requested command execution. I will call 'execute_command' with command '{cmd}'.",
                "tool_call": {"name": "execute_command", "arguments": {"command": cmd}},
            }

        # Scenario: Web Search
        elif "search" in prompt and step == 0:
            parts = user_prompt.split("search")
            q = parts[1].strip() if len(parts) > 1 else "nanoclaw"
            return {
                "thought": f"The user asked to search. I will call 'web_search' with query '{q}'.",
                "tool_call": {"name": "web_search", "arguments": {"query": q}},
            }

        # Scenario: RAG Query
        elif "query rag" in prompt and step == 0:
            parts = user_prompt.split(":")
            q = parts[1].strip() if len(parts) > 1 else "postgres jsonb"
            return {
                "thought": f"The user asked to retrieve from RAG. I will call 'rag_query' with query '{q}'.",
                "tool_call": {"name": "rag_query", "arguments": {"query": q}},
            }

        # Multi-step loop scenario: run command followed by reading file
        elif "multistep" in prompt:
            if step == 0:
                return {
                    "thought": "This is a multistep request. First I will run a check on directory files.",
                    "tool_call": {
                        "name": "execute_command",
                        "arguments": {"command": "ls -l /home/airc/agent-service"},
                    },
                }
            elif step == 1:
                return {
                    "thought": "Directory checked. Now I will read the requirements file.",
                    "tool_call": {
                        "name": "read_file",
                        "arguments": {
                            "path": "/home/airc/agent-service/requirements.txt"
                        },
                    },
                }

        # Deadlock defense testing
        elif "trigger loop" in prompt:
            return {
                "thought": "LLM is stuck in an infinite loop calling tool...",
                "tool_call": {
                    "name": "web_search",
                    "arguments": {"query": "loop_trigger"},
                },
            }

        # Final answer decisions after tools execution
        if step > 0 or "hello" in prompt or "hi" in prompt:
            # Look at previous tool output in history
            tool_outputs = [h["content"] for h in history if h.get("role") == "tool"]
            context_summary = " ".join(tool_outputs)

            # Extract content from read_file or command execution if present
            if "read_file" in context_summary:
                ans = f"I've read the file requested. Content preview: {context_summary[:100]}..."
            elif "STDOUT" in context_summary:
                ans = (
                    f"Here is the command execution output: {context_summary[:200]}..."
                )
            elif "RAG Search Results" in context_summary:
                ans = f"RAG search completed: {context_summary[:200]}..."
            elif "Search Results" in context_summary:
                ans = f"Search completed: {context_summary[:200]}..."
            else:
                ans = "Hello! I am your agent service. How can I help you today?"

            return {
                "thought": "I have collected enough context and am ready to respond to the user.",
                "final_answer": ans,
            }

        # Fallback default decision if no conditions match
        return {
            "thought": "No specific tools required for this request. Answering directly.",
            "final_answer": "I am ready to assist. Please ask a specific query like 'run command: ls' or 'query rag: postgres'.",
        }

    async def execute_react_loop(
        self, task_id: str, user_message: str
    ) -> AsyncGenerator[str, None]:
        """
        Executes ReAct step-by-step. Yields SSE payloads.
        Protects against timeouts, client disconnect cancellations, and step limits.
        """
        # Distributed lock to protect this session from concurrent execution
        lock = DistributedLock(self.session_id)
        if not await lock.acquire():
            yield f"data: {json.dumps({'error': 'Concurrent request detected for this session. Locked.'})}\n\n"
            return

        # Query session history
        history_query = await self.db.execute(
            select(MessageModel)
            .filter_by(session_id=self.session_id)
            .order_by(MessageModel.created_at.asc())
        )
        messages_db = history_query.scalars().all()

        # Build memory history for mock LLM
        history: List[Dict[str, str]] = []
        for m in messages_db:
            history.append({"role": m.role, "content": m.content})

        # Add current user message
        history.append({"role": "user", "content": user_message})

        # Save user message to database
        user_msg_model = MessageModel(
            session_id=self.session_id, role="user", content=user_message
        )
        self.db.add(user_msg_model)
        await self.db.commit()

        # Update task status to running
        await self.db.execute(
            update(TaskModel).where(TaskModel.id == task_id).values(status="running")
        )
        await self.db.commit()

        start_time = time.time()
        final_answer = ""

        try:
            # ReAct Steps
            while self.current_step < settings.MAX_REACT_STEPS:
                # Check global execution timeout
                if time.time() - start_time > settings.GLOBAL_TIMEOUT:
                    raise asyncio.TimeoutError(
                        "Global agent execution timeout exceeded."
                    )

                # Step 1: LLM Decisions
                llm_response = self._mock_llm_decide(
                    user_message, history, self.current_step
                )

                # Yield thought
                thought = llm_response.get("thought", "")
                yield f"data: {json.dumps({'event': 'thought', 'thought': thought, 'step': self.current_step})}\n\n"
                await asyncio.sleep(0.1)  # tiny pause for stream readability

                # Case A: LLM returns final answer
                if "final_answer" in llm_response:
                    final_answer = llm_response["final_answer"]
                    yield f"data: {json.dumps({'event': 'final_answer', 'answer': final_answer})}\n\n"

                    # Store assistant message
                    assistant_msg = MessageModel(
                        session_id=self.session_id,
                        role="assistant",
                        content=final_answer,
                    )
                    self.db.add(assistant_msg)

                    # Update task status to completed
                    await self.db.execute(
                        update(TaskModel)
                        .where(TaskModel.id == task_id)
                        .values(
                            status="completed",
                            result={"answer": final_answer, "steps": self.current_step},
                        )
                    )
                    await self.db.commit()

                    # Queue RAG Evaluation task if RAG tools were used
                    if self.rag_context_collected:
                        rag_context = " ".join(self.rag_context_collected)
                        await eval_queue.put((task_id, rag_context, final_answer))
                    break

                # Case B: LLM returns tool call
                tool_call = llm_response.get("tool_call")
                if tool_call:
                    tool_name = tool_call["name"]
                    arguments = tool_call["arguments"]

                    yield f"data: {json.dumps({'event': 'tool_start', 'tool': tool_name, 'args': arguments})}\n\n"

                    # Record tool call in DB
                    tool_call_db = ToolCallModel(
                        task_id=task_id,
                        tool_name=tool_name,
                        arguments=arguments,
                        status="running",
                    )
                    self.db.add(tool_call_db)
                    await self.db.commit()
                    # Refresh to get DB ID
                    await self.db.refresh(tool_call_db)

                    tool_start_time = time.time()
                    tool_output = ""
                    tool_status = "success"

                    try:
                        # Find registered tool
                        if tool_name not in tool_registry:
                            raise ValueError(f"Tool '{tool_name}' is not registered.")

                        tool_obj = tool_registry[tool_name]
                        # Execute tool with timeout constraint
                        tool_output = await asyncio.wait_for(
                            tool_obj.execute(**arguments), timeout=settings.TOOL_TIMEOUT
                        )

                        # Collect rag context for evaluation
                        if tool_name == "rag_query" or tool_name == "web_search":
                            self.rag_context_collected.append(tool_output)

                    except asyncio.TimeoutError:
                        tool_output = f"Error: Tool '{tool_name}' execution timed out (Limit {settings.TOOL_TIMEOUT}s)."
                        tool_status = "failed"
                        logger.warning(f"Tool {tool_name} in task {task_id} timed out.")
                    except Exception as e:
                        tool_output = f"Error: {str(e)}"
                        tool_status = "failed"
                        logger.error(
                            f"Tool {tool_name} in task {task_id} failed: {str(e)}"
                        )

                    tool_duration = time.time() - tool_start_time

                    # Update tool call result in DB
                    tool_call_db.output = tool_output
                    tool_call_db.status = tool_status
                    tool_call_db.duration = tool_duration
                    await self.db.commit()

                    # Yield tool output
                    yield f"data: {json.dumps({'event': 'tool_end', 'tool': tool_name, 'output': tool_output, 'duration': tool_duration})}\n\n"

                    # Append tool result to history
                    history.append({"role": "tool", "content": tool_output})
                    self.current_step += 1

            else:
                # Max ReAct steps exceeded, dead-loop protection activated
                error_msg = f"Loop Protection: ReAct exceeded maximum steps ({settings.MAX_REACT_STEPS}). Execution terminated."
                yield f"data: {json.dumps({'event': 'error', 'error': error_msg})}\n\n"

                await self.db.execute(
                    update(TaskModel)
                    .where(TaskModel.id == task_id)
                    .values(status="failed", result={"error": error_msg})
                )
                await self.db.commit()

        except asyncio.CancelledError:
            # Client disconnected mid-SSE stream
            logger.info(
                f"SSE connection disconnected for task {task_id}. Cleaning up running tasks."
            )

            # Set task status to failed / cancelled
            await self.db.execute(
                update(TaskModel)
                .where(TaskModel.id == task_id)
                .values(
                    status="failed",
                    result={"error": "Client disconnected / stream cancelled"},
                )
            )
            await self.db.commit()

            # Propagate cancellation
            raise

        except Exception as e:
            error_msg = f"System Error: {str(e)}"
            logger.error(f"ReAct Loop Error: {error_msg}", exc_info=True)
            yield f"data: {json.dumps({'event': 'error', 'error': error_msg})}\n\n"

            await self.db.execute(
                update(TaskModel)
                .where(TaskModel.id == task_id)
                .values(status="failed", result={"error": error_msg})
            )
            await self.db.commit()

        finally:
            await lock.release()
