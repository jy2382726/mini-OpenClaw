"""AgentManager.summarize_checkpoint() 单元测试。

覆盖场景：
- 成功摘要：消息数 > keep_count 时正确切分、调用 LLM、写回
- 消息数不足：≤ keep_count 时返回 {summarized: false}
- checkpoint 不存在：返回 ValueError
- AI/Tool 配对保护：切割点落在 ToolMessage 时向前调整
- 并发锁：同一 session 重复请求被拒绝
- 辅助 LLM 不可用：返回 RuntimeError

运行方式：
    cd backend && source .venv/bin/activate
    python -m pytest tests/test_summarize_checkpoint.py -v -s
"""

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from graph.agent import AgentManager


# ── Mock 工具 ──


class MockSnapshot:
    """模拟 agent.aget_state() 返回的 StateSnapshot。"""

    def __init__(self, messages=None):
        self.values = {"messages": messages} if messages is not None else None


class MockAgent:
    """模拟 LangGraph agent 对象。"""

    def __init__(self, messages=None, snapshot=None):
        self._messages = messages
        self._snapshot = snapshot or MockSnapshot(messages)
        self.updated_state = None  # 记录最后一次 aupdate_state 的参数

    async def aget_state(self, config):
        return self._snapshot

    async def aupdate_state(self, config, values, *, as_node=None):
        self.updated_state = {"config": config, "values": values, "as_node": as_node}


class MockLLM:
    """模拟辅助 LLM。"""

    def __init__(self, response="## SUMMARY\nTest summary content"):
        self._response = response

    async def ainvoke(self, messages):
        class Result:
            content = self._response

        return Result()


# ── 辅助函数 ──


def _make_messages(count: int) -> list:
    """生成 N 条交替的 HumanMessage / AIMessage。"""
    msgs = []
    for i in range(count):
        if i % 2 == 0:
            msgs.append(HumanMessage(content=f"User message {i}"))
        else:
            msgs.append(AIMessage(content=f"Assistant reply {i}"))
    return msgs


async def _noop_ensure_checkpointer(self):
    """Mock _ensure_checkpointer 的 async 替身。"""
    pass


def _make_manager(agent_messages=None, snapshot=None) -> AgentManager:
    """构造一个 mock 了内部方法的 AgentManager。"""
    mgr = AgentManager()
    mgr._base_dir = None  # 防止真实文件操作

    mock_agent = MockAgent(messages=agent_messages, snapshot=snapshot)

    mgr._build_agent = lambda: mock_agent  # type: ignore[assignment]
    mgr._ensure_checkpointer = lambda: _noop_ensure_checkpointer(mgr)  # type: ignore[assignment]
    mgr._create_summary_llm = lambda: MockLLM()  # type: ignore[assignment]

    return mgr, mock_agent


# ── 测试类 ──


class TestSuccessfulSummarize:
    """消息数 > keep_count 时成功执行摘要。"""

    @pytest.mark.asyncio
    async def test_basic_summarize(self):
        """15 条消息，keep_count=10 → 摘要 5 条，保留 10 条。"""
        messages = _make_messages(15)
        mgr, mock_agent = _make_manager(agent_messages=messages)

        result = await mgr.summarize_checkpoint("test-session", keep_count=10)

        assert result["summarized"] is True
        assert result["summarized_count"] == 5
        assert result["preserved_count"] == 10

        # 验证写回了 checkpoint
        assert mock_agent.updated_state is not None
        assert mock_agent.updated_state["as_node"] == "model"
        written_messages = mock_agent.updated_state["values"]["messages"]
        # 1 条摘要 + 10 条保留 = 11 条
        assert len(written_messages) == 11
        # 第一条是摘要 HumanMessage
        assert isinstance(written_messages[0], HumanMessage)
        assert "Here is a summary" in written_messages[0].content
        assert written_messages[0].additional_kwargs.get("lc_source") == "summarization"

    @pytest.mark.asyncio
    async def test_custom_keep_count(self):
        """自定义 keep_count=5。"""
        messages = _make_messages(12)
        mgr, _ = _make_manager(agent_messages=messages)

        result = await mgr.summarize_checkpoint("test-session", keep_count=5)

        assert result["summarized"] is True
        assert result["summarized_count"] == 7
        assert result["preserved_count"] == 5


class TestInsufficientMessages:
    """消息数 ≤ keep_count 时不执行摘要。"""

    @pytest.mark.asyncio
    async def test_exactly_keep_count(self):
        """10 条消息，keep_count=10 → 不摘要。"""
        messages = _make_messages(10)
        mgr, mock_agent = _make_manager(agent_messages=messages)

        result = await mgr.summarize_checkpoint("test-session", keep_count=10)

        assert result["summarized"] is False
        assert result["preserved_count"] == 10
        # 不应写回 checkpoint
        assert mock_agent.updated_state is None

    @pytest.mark.asyncio
    async def test_fewer_than_keep_count(self):
        """5 条消息，keep_count=10 → 不摘要。"""
        messages = _make_messages(5)
        mgr, mock_agent = _make_manager(agent_messages=messages)

        result = await mgr.summarize_checkpoint("test-session", keep_count=10)

        assert result["summarized"] is False
        assert result["preserved_count"] == 5
        assert mock_agent.updated_state is None


class TestMissingCheckpoint:
    """checkpoint 不存在或消息为空。"""

    @pytest.mark.asyncio
    async def test_no_checkpoint(self):
        """snapshot 为 None → ValueError。"""
        mgr, _ = _make_manager()
        mgr._build_agent = lambda: MockAgent(snapshot=MockSnapshot(messages=None))  # type: ignore[assignment]
        # MockSnapshot(messages=None) → .values = None

        with pytest.raises(ValueError, match="无可用消息"):
            await mgr.summarize_checkpoint("missing-session")

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        """snapshot 存在但 messages 为空列表。"""
        mgr, _ = _make_manager()
        mgr._build_agent = lambda: MockAgent(snapshot=MockSnapshot(messages=[]))  # type: ignore[assignment]

        with pytest.raises(ValueError, match="无可用消息"):
            await mgr.summarize_checkpoint("empty-session")


class TestAIToolPairProtection:
    """AI/Tool 消息配对保护。"""

    def test_split_on_tool_message(self):
        """切割点落在 ToolMessage → 向前调整到对应 AIMessage。"""
        mgr = AgentManager()

        messages = [
            HumanMessage(content="msg 0"),
            AIMessage(content="", tool_calls=[{"id": "tc_1", "name": "terminal", "args": {"cmd": "ls"}}]),
            ToolMessage(content="file.txt", tool_call_id="tc_1", name="terminal"),
            HumanMessage(content="msg 3"),
            AIMessage(content="reply 4"),
        ]

        # split_idx=2 落在 ToolMessage 上
        adjusted = mgr._protect_ai_tool_pairs(messages, 2)
        # 应调整到 AIMessage(index=1) 之前 → split_idx=1
        assert adjusted == 1

    def test_split_on_normal_message(self):
        """切割点不在 ToolMessage → 不调整。"""
        mgr = AgentManager()

        messages = [
            HumanMessage(content="msg 0"),
            AIMessage(content="reply 1"),
            HumanMessage(content="msg 2"),
            AIMessage(content="reply 3"),
        ]

        adjusted = mgr._protect_ai_tool_pairs(messages, 2)
        assert adjusted == 2

    def test_split_at_boundary(self):
        """split_idx=0 或 len → 不调整。"""
        mgr = AgentManager()
        messages = _make_messages(5)

        assert mgr._protect_ai_tool_pairs(messages, 0) == 0
        assert mgr._protect_ai_tool_pairs(messages, 5) == 5

    @pytest.mark.asyncio
    async def test_full_flow_with_tool_pair(self):
        """完整流程：15 条消息中含 AI/Tool 配对，确保摘要范围正确。"""
        messages = _make_messages(12)
        # 在 index 2 插入 AIMessage + ToolMessage 配对
        messages.insert(2, AIMessage(content="", tool_calls=[{"id": "tc_x", "name": "read_file", "args": {}}]))
        messages.insert(3, ToolMessage(content="file content", tool_call_id="tc_x", name="read_file"))
        # 总共 14 条消息，keep_count=10 → split_idx=4
        # messages[4] 是普通 HumanMessage → 不触发配对保护

        mgr, mock_agent = _make_manager(agent_messages=messages)
        result = await mgr.summarize_checkpoint("tool-session", keep_count=10)

        assert result["summarized"] is True
        assert result["summarized_count"] == 4
        assert result["preserved_count"] == 10


class TestConcurrencyLock:
    """并发安全锁。"""

    @pytest.mark.asyncio
    async def test_concurrent_request_rejected(self):
        """同一 session 并发请求 → 第二个被拒绝。"""
        messages = _make_messages(20)
        mgr, _ = _make_manager(agent_messages=messages)

        # 手动获取锁模拟第一个请求正在执行
        lock = mgr._get_summarize_lock("concurrent-session")
        await lock.acquire()

        try:
            with pytest.raises(asyncio.TimeoutError, match="正在摘要中"):
                await mgr.summarize_checkpoint("concurrent-session")
        finally:
            lock.release()

    @pytest.mark.asyncio
    async def test_different_sessions_independent(self):
        """不同 session 的锁互不影响。"""
        mgr1, _ = _make_manager(agent_messages=_make_messages(20))
        mgr2, _ = _make_manager(agent_messages=_make_messages(20))

        # 两个不同 session 应能同时执行（不同锁）
        lock1 = mgr1._get_summarize_lock("session-a")
        await lock1.acquire()

        try:
            # session-b 不受 session-a 影响
            result = await mgr2.summarize_checkpoint("session-b")
            assert result["summarized"] is True
        finally:
            lock1.release()


class TestLLMUnavailable:
    """辅助 LLM 不可用。"""

    @pytest.mark.asyncio
    async def test_no_summary_llm(self):
        """_create_summary_llm 返回 None → RuntimeError。"""
        messages = _make_messages(20)
        mgr, _ = _make_manager(agent_messages=messages)
        mgr._create_summary_llm = lambda: None  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="辅助模型未配置"):
            await mgr.summarize_checkpoint("no-llm-session")
