"""CheckpointHistoryService 投影层单元测试。

覆盖场景：
- 基本对话（Human + AI）
- AIMessage 的 tool_calls 挂接 ToolMessage output
- 连续 assistant 消息（独立 DTO）
- 仅工具调用无文本的 assistant 消息
- SystemMessage 跳过
- 空 checkpoint

运行方式：
    cd backend && source .venv/bin/activate
    python -m pytest tests/test_checkpoint_history.py -v -s
"""

import pytest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from graph.checkpoint_history import CheckpointHistoryService, CheckpointDebugViewService


# ── 辅助 ──

class MockCheckpoint:
    """模拟 checkpoint 的 channel_values。"""
    def __init__(self, messages):
        self.data = {
            "channel_values": {"messages": messages},
            "channel_versions": {},
            "versions_seen": {},
        }

    def get(self, key, default=None):
        return self.data.get(key, default)


class MockCheckpointTuple:
    """模拟 CheckpointTuple。"""
    def __init__(self, messages):
        self.checkpoint = MockCheckpoint(messages)
        self.config = {}
        self.metadata = {}
        self.parent_config = None
        self.pending_writes = []


class MockCheckpointer:
    """模拟 AsyncSqliteSaver，返回预设的 messages。"""
    def __init__(self, messages=None):
        self._messages = messages or []

    async def aget_tuple(self, config):
        if self._messages is None:
            return None
        return MockCheckpointTuple(self._messages)


class TestBasicConversationProjection:
    """基本对话投影：HumanMessage + AIMessage → role/content DTO。"""

    @pytest.mark.asyncio
    async def test_basic_user_assistant(self):
        """简单的 user/assistant 对话。"""
        messages = [
            HumanMessage(content="你好"),
            AIMessage(content="你好！有什么可以帮助你的？"),
        ]
        service = CheckpointHistoryService(MockCheckpointer(messages))
        result = await service.project("test-thread")

        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "你好"}
        assert result[1] == {"role": "assistant", "content": "你好！有什么可以帮助你的？"}

    @pytest.mark.asyncio
    async def test_multi_turn_conversation(self):
        """多轮对话。"""
        messages = [
            HumanMessage(content="第一轮"),
            AIMessage(content="第一轮回复"),
            HumanMessage(content="第二轮"),
            AIMessage(content="第二轮回复"),
        ]
        service = CheckpointHistoryService(MockCheckpointer(messages))
        result = await service.project("test-thread")

        assert len(result) == 4
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "user"
        assert result[3]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_system_message_skipped(self):
        """SystemMessage 在投影中被跳过。"""
        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="你好"),
            AIMessage(content="你好！"),
        ]
        service = CheckpointHistoryService(MockCheckpointer(messages))
        result = await service.project("test-thread")

        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"


class TestToolCallProjection:
    """AIMessage 的 tool_calls 挂接 ToolMessage output。"""

    @pytest.mark.asyncio
    async def test_tool_call_with_output(self):
        """工具调用 + ToolMessage 挂接 output。"""
        messages = [
            HumanMessage(content="运行 ls"),
            AIMessage(
                content="",
                tool_calls=[{"id": "tc1", "name": "terminal", "args": {"command": "ls"}}],
            ),
            ToolMessage(content="file1.txt\nfile2.txt", name="terminal", tool_call_id="tc1"),
            AIMessage(content="当前目录有两个文件"),
        ]
        service = CheckpointHistoryService(MockCheckpointer(messages))
        result = await service.project("test-thread")

        # user + AI(tool_call) + AI(reply) = 3
        assert len(result) == 3

        # 第二个是带 tool_calls 的 assistant
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == ""
        assert len(result[1]["tool_calls"]) == 1
        tc = result[1]["tool_calls"][0]
        assert tc["tool"] == "terminal"
        assert tc["input"] == "ls"
        assert tc["output"] == "file1.txt\nfile2.txt"

        # 第三个是最终回复
        assert result[2]["content"] == "当前目录有两个文件"

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_in_one_message(self):
        """一条 AIMessage 包含多个 tool_calls，每个都有对应 ToolMessage。"""
        messages = [
            HumanMessage(content="运行两个命令"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "tc1", "name": "terminal", "args": {"command": "ls"}},
                    {"id": "tc2", "name": "terminal", "args": {"command": "pwd"}},
                ],
            ),
            ToolMessage(content="file1.txt", name="terminal", tool_call_id="tc1"),
            ToolMessage(content="/home/user", name="terminal", tool_call_id="tc2"),
            AIMessage(content="两个命令都完成了"),
        ]
        service = CheckpointHistoryService(MockCheckpointer(messages))
        result = await service.project("test-thread")

        assert len(result) == 3
        assert len(result[1]["tool_calls"]) == 2
        assert result[1]["tool_calls"][0]["output"] == "file1.txt"
        assert result[1]["tool_calls"][1]["output"] == "/home/user"

    @pytest.mark.asyncio
    async def test_tool_call_without_tool_message(self):
        """tool_call 没有对应 ToolMessage（异常场景）：output 字段不存在。"""
        messages = [
            HumanMessage(content="测试"),
            AIMessage(
                content="",
                tool_calls=[{"id": "tc1", "name": "terminal", "args": {"command": "ls"}}],
            ),
            # 没有 ToolMessage
            AIMessage(content="继续"),
        ]
        service = CheckpointHistoryService(MockCheckpointer(messages))
        result = await service.project("test-thread")

        assert len(result) == 3
        tc = result[1]["tool_calls"][0]
        assert "output" not in tc  # 没有 ToolMessage 就没有 output


class TestConsecutiveAssistantMessages:
    """连续 assistant 消息：作为独立 DTO 对象输出。"""

    @pytest.mark.asyncio
    async def test_consecutive_ai_messages(self):
        """两个连续 AIMessage 各自作为独立 DTO。"""
        messages = [
            HumanMessage(content="执行"),
            AIMessage(content="第一步完成"),
            AIMessage(content="第二步完成"),
        ]
        service = CheckpointHistoryService(MockCheckpointer(messages))
        result = await service.project("test-thread")

        assert len(result) == 3
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "第一步完成"
        assert result[2]["role"] == "assistant"
        assert result[2]["content"] == "第二步完成"

    @pytest.mark.asyncio
    async def test_mixed_consecutive_ai_with_tool_calls(self):
        """连续 AI 消息夹杂工具调用。"""
        messages = [
            HumanMessage(content="运行两个命令"),
            AIMessage(
                content="",
                tool_calls=[{"id": "tc1", "name": "terminal", "args": {"command": "ls"}}],
            ),
            ToolMessage(content="file1.txt", name="terminal", tool_call_id="tc1"),
            AIMessage(content="第一个命令完成，继续..."),
            AIMessage(
                content="",
                tool_calls=[{"id": "tc2", "name": "terminal", "args": {"command": "pwd"}}],
            ),
            ToolMessage(content="/home/user", name="terminal", tool_call_id="tc2"),
            AIMessage(content="两个命令都执行完毕"),
        ]
        service = CheckpointHistoryService(MockCheckpointer(messages))
        result = await service.project("test-thread")

        # user + AI(tc1) + AI(text) + AI(tc2) + AI(text) = 5
        assert len(result) == 5
        assert result[1]["content"] == ""
        assert len(result[1]["tool_calls"]) == 1
        assert result[2]["content"] == "第一个命令完成，继续..."
        assert result[3]["content"] == ""
        assert len(result[3]["tool_calls"]) == 1
        assert result[4]["content"] == "两个命令都执行完毕"


class TestToolCallOnlyNoText:
    """仅工具调用无文本的 assistant 消息。"""

    @pytest.mark.asyncio
    async def test_empty_content_with_tool_calls(self):
        """content="" + tool_calls → 保留空 content。"""
        messages = [
            HumanMessage(content="读取文件"),
            AIMessage(
                content="",
                tool_calls=[{"id": "tc1", "name": "read_file", "args": {"path": "test.txt"}}],
            ),
            ToolMessage(content="文件内容...", name="read_file", tool_call_id="tc1"),
        ]
        service = CheckpointHistoryService(MockCheckpointer(messages))
        result = await service.project("test-thread")

        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == ""
        assert len(result[1]["tool_calls"]) == 1


class TestEmptyCheckpoint:
    """空 checkpoint 场景。"""

    @pytest.mark.asyncio
    async def test_no_checkpoint(self):
        """无 checkpoint 返回空列表。"""
        service = CheckpointHistoryService(MockCheckpointer(None))
        result = await service.project("nonexistent-thread")
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        """checkpoint 存在但 messages 为空。"""
        service = CheckpointHistoryService(MockCheckpointer([]))
        result = await service.project("test-thread")
        assert result == []


class TestDebugViewProjection:
    """CheckpointDebugViewService 测试。"""

    @pytest.mark.asyncio
    async def test_debug_view_includes_system_prompt(self):
        """调试视图包含 system prompt + 投影消息。"""
        messages = [
            SystemMessage(content="System prompt from agent"),
            HumanMessage(content="你好"),
            AIMessage(content="你好！"),
        ]
        checkpointer = MockCheckpointer(messages)
        service = CheckpointDebugViewService(checkpointer)

        # 使用 mock 避免真实文件系统依赖
        from unittest.mock import patch
        with patch("graph.checkpoint_history.build_system_prompt", return_value="mocked system prompt"):
            result = await service.project("test-thread", base_dir="/tmp")

        assert result["is_approximation"] is True
        assert result["messages"][0]["role"] == "system"
        assert result["messages"][0]["content"] == "mocked system prompt"
        assert len(result["messages"]) == 3  # system + user + assistant

    @pytest.mark.asyncio
    async def test_debug_view_empty_checkpoint(self):
        """空 checkpoint 只返回 system prompt。"""
        checkpointer = MockCheckpointer(None)
        service = CheckpointDebugViewService(checkpointer)

        from unittest.mock import patch
        with patch("graph.checkpoint_history.build_system_prompt", return_value="system"):
            result = await service.project("test-thread", base_dir="/tmp")

        assert len(result["messages"]) == 1  # 仅 system prompt
        assert result["is_approximation"] is True
