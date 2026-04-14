"""POST /api/sessions/{session_id}/compress — Compress conversation history.

⚠️ DEPRECATED: 此模块已被 LangChain SummarizationMiddleware 自动摘要替代。
中间件在每次模型调用前自动检查 token 数并触发结构化摘要，
无需前端手动触发。此接口保留作为备用回退，后续版本将移除。
"""

import traceback
from typing import Any

from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage

from graph.session_manager import session_manager

router = APIRouter()


async def _generate_summary(messages: list[dict[str, Any]]) -> str:
    """Use auxiliary model to generate a compressed summary of messages."""
    from config import create_auxiliary_llm

    llm = create_auxiliary_llm()
    if llm is None:
        raise RuntimeError("辅助模型未配置，无法生成摘要")

    # Format messages for summary
    formatted = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if content:
            formatted.append(f"{role}: {content[:500]}")

    conversation_text = "\n".join(formatted)

    prompt = (
        "请将以下对话历史压缩为简洁的中文摘要，保留关键信息、决策和结论。"
        "摘要不超过500字。只输出摘要内容，不要添加额外说明。\n\n"
        f"{conversation_text}"
    )

    result = await llm.ainvoke([HumanMessage(content=prompt)])
    return result.content.strip()


@router.post("/sessions/{session_id}/compress")
async def compress_session(session_id: str) -> dict[str, Any]:
    """Compress the first 50% of conversation history into a summary."""
    messages = session_manager.load_session(session_id)
    if len(messages) < 4:
        raise HTTPException(
            status_code=400,
            detail="Not enough messages to compress (need at least 4)",
        )

    # Take the first 50% of messages
    num_to_remove = max(4, len(messages) // 2)

    messages_to_compress = messages[:num_to_remove]

    try:
        summary = await _generate_summary(messages_to_compress)
        session_manager.compress_history(session_id, summary, num_to_remove)
        remaining = len(messages) - num_to_remove
        return {
            "archived_count": num_to_remove,
            "remaining_count": remaining,
        }
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Compression failed: {str(e)}")
