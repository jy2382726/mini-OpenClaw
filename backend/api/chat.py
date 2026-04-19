"""POST /api/chat — SSE streaming chat with Agent."""

import json
import logging
import traceback
from typing import AsyncGenerator

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from graph.agent import agent_manager

router = APIRouter()
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    stream: bool = True


async def _generate_title(session_id: str) -> str | None:
    """Generate a title for a session using DashScope Qwen. Returns title or None."""
    try:
        # 从 checkpoint 投影读取消息
        from graph.checkpoint_history import CheckpointHistoryService

        checkpointer = await agent_manager._ensure_checkpointer()
        service = CheckpointHistoryService(checkpointer)
        messages = await service.project(session_id)

        first_user = ""
        first_assistant = ""
        for msg in messages:
            if msg["role"] == "user" and not first_user:
                first_user = msg["content"][:200]
            elif msg["role"] == "assistant" and not first_assistant:
                first_assistant = msg["content"][:200]
            if first_user and first_assistant:
                break

        if not first_user:
            return None

        from langchain_core.messages import HumanMessage as HM

        from config import create_auxiliary_llm

        llm = create_auxiliary_llm()
        if llm is None:
            return None

        prompt = (
            f"根据以下对话内容，生成一个不超过10个字的中文标题，只输出标题文本，不要加引号或标点。\n\n"
            f"用户: {first_user}\n"
            f"助手: {first_assistant}"
        )

        result = await llm.ainvoke([HM(content=prompt)])
        title = result.content.strip().strip('"\'""''')[:20]
        # 更新 SQLite 元数据
        try:
            repo = await agent_manager.get_session_repo()
            await repo.rename(session_id, title)
        except (FileNotFoundError, Exception):
            pass
        return title
    except Exception:
        traceback.print_exc()
        return None


async def event_generator(message: str, session_id: str) -> AsyncGenerator[dict, None]:
    """Generate SSE events from agent stream.

    Tracks multiple response segments — each time the agent finishes
    tool calls and starts generating new text, a new_response event is
    emitted and a new segment begins. Each segment is saved as a
    separate assistant message in the session history.

    Conversation is persisted via checkpoint (AsyncSqliteSaver).
    On stream interruption, checkpoint already has the last completed
    node's snapshot, sufficient for context recovery.
    """
    segments: list[dict] = []
    current_segment: dict = {"content": "", "tool_calls": []}
    conversation_saved = False
    stream_error: Exception | None = None

    try:
        # touch 会话元数据（bootstrap_if_missing + 更新 updated_at）
        try:
            repo = await agent_manager.get_session_repo()
            await repo.bootstrap_if_missing(session_id)
            await repo.touch(session_id)
        except Exception as e:
            logger.warning("会话元数据操作失败 session_id=%s: %s", session_id, e)

        # 判断是否首条消息：通过 checkpoint 检查是否已有消息
        from graph.checkpoint_history import CheckpointHistoryService

        checkpointer = await agent_manager._ensure_checkpointer()
        history_service = CheckpointHistoryService(checkpointer)
        existing_messages = await history_service.project(session_id)
        is_first_message = len(existing_messages) == 0

        async for event in agent_manager.astream(message, [], session_id=session_id):
            event_type = event.get("type", "unknown")

            if event_type == "retrieval":
                yield {
                    "event": "retrieval",
                    "data": json.dumps(
                        {"query": event["query"], "results": event["results"]},
                        ensure_ascii=False,
                    ),
                }

            elif event_type == "token":
                current_segment["content"] += event["content"]
                yield {
                    "event": "token",
                    "data": json.dumps({"content": event["content"]}, ensure_ascii=False),
                }

            elif event_type == "new_response":
                segments.append(current_segment)
                current_segment = {"content": "", "tool_calls": []}
                yield {
                    "event": "new_response",
                    "data": json.dumps({}, ensure_ascii=False),
                }

            elif event_type == "tool_start":
                current_segment["tool_calls"].append({
                    "tool": event["tool"],
                    "input": event.get("input", ""),
                })
                yield {
                    "event": "tool_start",
                    "data": json.dumps(
                        {"tool": event["tool"], "input": event["input"]},
                        ensure_ascii=False,
                    ),
                }

            elif event_type == "tool_end":
                for tc in reversed(current_segment["tool_calls"]):
                    if tc["tool"] == event["tool"] and "output" not in tc:
                        tc["output"] = event["output"]
                        break
                yield {
                    "event": "tool_end",
                    "data": json.dumps(
                        {"tool": event["tool"], "output": event["output"]},
                        ensure_ascii=False,
                    ),
                }

            elif event_type == "task_update":
                yield {
                    "event": "task_update",
                    "data": json.dumps(
                        {"task_state": event["task_state"]},
                        ensure_ascii=False,
                    ),
                }

            elif event_type == "done":
                segments.append(current_segment)
                conversation_saved = True

                yield {
                    "event": "done",
                    "data": json.dumps(
                        {"content": event["content"], "session_id": session_id},
                        ensure_ascii=False,
                    ),
                }

                if is_first_message:
                    title = await _generate_title(session_id)
                    if title:
                        yield {
                            "event": "title",
                            "data": json.dumps(
                                {"session_id": session_id, "title": title},
                                ensure_ascii=False,
                            ),
                        }

    except Exception as e:
        traceback.print_exc()
        stream_error = e

    finally:
        # 流式中断时不再保存 JSON——checkpoint 已有最后完成 node 的快照
        pass

    # Yield error event outside finally (cannot yield inside finally)
    if stream_error is not None:
        yield {
            "event": "error",
            "data": json.dumps(
                {"error": "An error occurred during response generation."},
                ensure_ascii=False,
            ),
        }


@router.post("/chat")
async def chat(request: ChatRequest):
    if request.stream:
        # Use EventSourceResponse without ping to avoid connection issues
        # The connection should close naturally when the generator ends
        return EventSourceResponse(
            event_generator(request.message, request.session_id),
        )
    # Non-streaming fallback
    result = await agent_manager.ainvoke(request.message, request.session_id)
    return {"reply": result}
