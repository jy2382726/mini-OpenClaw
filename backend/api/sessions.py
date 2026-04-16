"""Session CRUD API — list / create / rename / delete / raw messages / generate title.

数据源：SessionRepository（SQLite 元数据）+ CheckpointHistoryService（消息投影）。
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from graph.agent import agent_manager
from graph.checkpoint_history import CheckpointDebugViewService, CheckpointHistoryService
from graph.prompt_builder import build_system_prompt

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent


# ── Request models ──────────────────────────────────────────

class RenameRequest(BaseModel):
    title: str


# ── Endpoints ───────────────────────────────────────────────

@router.get("/sessions")
async def list_sessions():
    """List all sessions with title and metadata."""
    repo = await agent_manager.get_session_repo()
    sessions = await repo.list()
    return {"sessions": sessions}


@router.post("/sessions")
async def create_session():
    """Create a new empty session."""
    session_id = f"session-{uuid.uuid4().hex[:12]}"
    repo = await agent_manager.get_session_repo()
    meta = await repo.create(session_id)
    return meta


@router.put("/sessions/{session_id}")
async def rename_session(session_id: str, req: RenameRequest):
    """Rename an existing session."""
    repo = await agent_manager.get_session_repo()
    try:
        await repo.rename(session_id, req.title)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"id": session_id, "title": req.title}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session.

    软删除元数据 + 物理删除 checkpoint 线程。
    """
    repo = await agent_manager.get_session_repo()
    await repo.soft_delete(session_id)

    # 物理删除 checkpoint 线程
    checkpointer = await agent_manager._ensure_checkpointer()
    if hasattr(checkpointer, "adelete_thread"):
        await checkpointer.adelete_thread(session_id)

    return {"status": "deleted", "id": session_id}


@router.get("/sessions/{session_id}/messages")
async def get_raw_messages(session_id: str):
    """Get complete raw messages including system prompt (debug view)."""
    checkpointer = await agent_manager._ensure_checkpointer()
    service = CheckpointDebugViewService(checkpointer)
    result = await service.project(session_id, BASE_DIR)
    # 补充 title
    try:
        repo = await agent_manager.get_session_repo()
        meta = await repo.get(session_id)
        result["title"] = meta["title"] if meta else ""
    except Exception:
        pass
    result["session_id"] = session_id
    return result


@router.get("/sessions/{session_id}/history")
async def get_session_history(session_id: str):
    """Get conversation history for display (no system prompt, includes tool_calls)."""
    checkpointer = await agent_manager._ensure_checkpointer()
    service = CheckpointHistoryService(checkpointer)
    messages = await service.project(session_id)
    return {"session_id": session_id, "messages": messages}


@router.post("/sessions/{session_id}/generate-title")
async def generate_title(session_id: str):
    """Use auxiliary model to generate a short title from the first conversation turn."""
    checkpointer = await agent_manager._ensure_checkpointer()
    service = CheckpointHistoryService(checkpointer)
    messages = await service.project(session_id)

    if not messages:
        raise HTTPException(status_code=400, detail="No messages to generate title from")

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
        raise HTTPException(status_code=400, detail="No user message found")

    try:
        from langchain_core.messages import HumanMessage as HM

        from config import create_auxiliary_llm

        llm = create_auxiliary_llm()
        if llm is None:
            raise HTTPException(status_code=500, detail="辅助模型未配置，无法生成标题")

        prompt = (
            f"根据以下对话内容，生成一个不超过10个字的中文标题，只输出标题文本，不要加引号或标点。\n\n"
            f"用户: {first_user}\n"
            f"助手: {first_assistant}"
        )

        result = await llm.ainvoke([HM(content=prompt)])
        title = result.content.strip().strip('"\'""''')[:20]

        repo = await agent_manager.get_session_repo()
        try:
            await repo.rename(session_id, title)
        except FileNotFoundError:
            pass
        return {"session_id": session_id, "title": title}

    except Exception as e:
        # Fallback: use first few chars of user message
        fallback_title = first_user[:10].strip()
        repo = await agent_manager.get_session_repo()
        try:
            await repo.rename(session_id, fallback_title)
        except FileNotFoundError:
            pass
        return {"session_id": session_id, "title": fallback_title}


@router.post("/sessions/{session_id}/clear")
async def clear_session_messages(session_id: str):
    """Clear all messages in a session (like Claude Code /clear).

    删除 checkpoint 线程，下次对话从空状态开始。
    """
    checkpointer = await agent_manager._ensure_checkpointer()
    if hasattr(checkpointer, "adelete_thread"):
        await checkpointer.adelete_thread(session_id)

    return {"status": "cleared", "session_id": session_id}


@router.get("/sessions/{session_id}/task-state")
async def get_task_state(session_id: str):
    """从 checkpoint 读取 TaskState，用于前端恢复展示。

    返回格式: {"task_state": <TaskState 或 null>}
    """
    try:
        agent = agent_manager._build_agent()
        config = {"configurable": {"thread_id": session_id}}
        task_state = await agent_manager._read_task_state(agent, config)
        return {"task_state": task_state}
    except Exception:
        return {"task_state": None}
