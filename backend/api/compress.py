"""会话消息压缩与摘要端点。

POST /api/sessions/{session_id}/summarize — 基于 checkpoint 的手动摘要（推荐）
POST /api/sessions/{session_id}/compress  — 旧 JSON 文件压缩（DEPRECATED）
"""

import asyncio
import traceback
from typing import Any

from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage

from graph.agent import agent_manager
from graph.session_manager import session_manager

router = APIRouter()


@router.post("/sessions/{session_id}/summarize")
async def summarize_session(session_id: str) -> dict[str, Any]:
    """基于 checkpoint 的手动摘要：保留最近 10 条消息，早期消息替换为结构化摘要。"""
    try:
        result = await agent_manager.summarize_checkpoint(session_id)
        return result
    except ValueError as e:
        # checkpoint 不存在或消息为空
        raise HTTPException(status_code=400, detail=str(e))
    except asyncio.TimeoutError:
        # 并发冲突：该会话正在摘要中
        raise HTTPException(status_code=409, detail="该会话正在摘要中，请稍后再试")
    except RuntimeError as e:
        # 辅助 LLM 不可用
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"摘要失败: {str(e)}")


# ---------------------------------------------------------------------------
# DEPRECATED: 以下端点操作 JSON 文件数据源，不操作 checkpoint。
# 请使用 POST /sessions/{session_id}/summarize 替代。
# ---------------------------------------------------------------------------

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


@router.post("/sessions/{session_id}/compress", deprecated=True)
async def compress_session(session_id: str) -> dict[str, Any]:
    """[DEPRECATED] 使用 POST /sessions/{session_id}/summarize 替代。

    压缩前 50% 的对话历史为摘要（基于 JSON 文件，不影响 checkpoint）。
    """
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
