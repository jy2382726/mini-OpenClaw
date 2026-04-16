"""POST /sessions/{session_id}/summarize API 端点测试。

覆盖场景：
- 成功摘要 → 200 + {summarized: true, ...}
- 消息数不足 → 200 + {summarized: false}
- checkpoint 不存在 → 400
- 并发冲突 → 409
- 辅助 LLM 不可用 → 503
- 内部错误 → 500

运行方式：
    cd backend && source .venv/bin/activate
    python -m pytest tests/test_summarize_api.py -v -s
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.compress import summarize_session


# ── 测试类 ──


class TestSummarizeSuccess:
    """成功摘要。"""

    @pytest.mark.asyncio
    async def test_successful_summarize(self):
        """消息数充足 → 200 + summarized=true。"""
        with patch("api.compress.agent_manager") as mock_mgr:
            mock_mgr.summarize_checkpoint = AsyncMock(return_value={
                "summarized": True,
                "summarized_count": 8,
                "preserved_count": 10,
            })

            result = await summarize_session("test-session")

            assert result["summarized"] is True
            assert result["summarized_count"] == 8
            assert result["preserved_count"] == 10
            mock_mgr.summarize_checkpoint.assert_awaited_once_with("test-session")

    @pytest.mark.asyncio
    async def test_insufficient_messages(self):
        """消息数不足 → 200 + summarized=false。"""
        with patch("api.compress.agent_manager") as mock_mgr:
            mock_mgr.summarize_checkpoint = AsyncMock(return_value={
                "summarized": False,
                "reason": "消息数不足，无需摘要",
                "summarized_count": 0,
                "preserved_count": 5,
            })

            result = await summarize_session("small-session")

            assert result["summarized"] is False
            assert result["preserved_count"] == 5


class TestSummarizeErrors:
    """错误场景。"""

    @pytest.mark.asyncio
    async def test_checkpoint_not_found_400(self):
        """checkpoint 不存在 → 400。"""
        with patch("api.compress.agent_manager") as mock_mgr:
            mock_mgr.summarize_checkpoint = AsyncMock(
                side_effect=ValueError("该会话无可用消息（checkpoint 不存在）")
            )

            with pytest.raises(HTTPException) as exc_info:
                await summarize_session("missing-session")

            assert exc_info.value.status_code == 400
            assert "checkpoint" in exc_info.value.detail or "无可用消息" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_concurrent_conflict_409(self):
        """并发冲突 → 409。"""
        with patch("api.compress.agent_manager") as mock_mgr:
            mock_mgr.summarize_checkpoint = AsyncMock(
                side_effect=asyncio.TimeoutError("该会话正在摘要中")
            )

            with pytest.raises(HTTPException) as exc_info:
                await summarize_session("concurrent-session")

            assert exc_info.value.status_code == 409
            assert "摘要中" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_llm_unavailable_503(self):
        """辅助 LLM 不可用 → 503。"""
        with patch("api.compress.agent_manager") as mock_mgr:
            mock_mgr.summarize_checkpoint = AsyncMock(
                side_effect=RuntimeError("辅助模型未配置，无法生成摘要")
            )

            with pytest.raises(HTTPException) as exc_info:
                await summarize_session("no-llm-session")

            assert exc_info.value.status_code == 503
            assert "辅助模型" in exc_info.value.detail or "LLM" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_internal_error_500(self):
        """未知异常 → 500。"""
        with patch("api.compress.agent_manager") as mock_mgr:
            mock_mgr.summarize_checkpoint = AsyncMock(
                side_effect=Exception("Unexpected database error")
            )

            with pytest.raises(HTTPException) as exc_info:
                await summarize_session("error-session")

            assert exc_info.value.status_code == 500
            assert "摘要失败" in exc_info.value.detail
