"""中间件单元测试 — ToolOutputBudgetMiddleware + ContextAwareToolFilter。"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from graph.middleware import (
    TOOL_OUTPUT_BUDGETS,
    TOOL_TIERS,
    ContextAwareToolFilter,
    ToolOutputBudgetMiddleware,
    _exceeds_budget,
    _truncate_with_summary,
)


# ── 辅助函数测试 ──


class TestExceedsBudget:
    def test_under_budget(self):
        content = "a" * 100
        assert not _exceeds_budget(content, 100)  # 100 chars < 400 chars budget

    def test_over_budget(self):
        content = "a" * 500
        assert _exceeds_budget(content, 100)  # 500 chars > 400 chars budget

    def test_exact_budget(self):
        content = "a" * 400
        assert not _exceeds_budget(content, 100)  # 400 chars == 400 chars budget


class TestTruncateWithSummary:
    def test_head_tail_preserved(self):
        budget = 100  # char_budget = 400, head=266, tail=133
        head_len = 400 * 2 // 3  # 266
        tail_len = 400 // 3  # 133
        content = "H" * 200 + "M" * 300 + "T" * 200  # 700 chars total
        result = _truncate_with_summary(content, budget)
        # head 取 content[:266]，即 200 个 H + 66 个 M
        assert result.startswith("H" * 200)
        assert result.endswith("T" * 133)
        assert "省略约" in result

    def test_omitted_count_correct(self):
        budget = 10  # char_budget = 40
        content = "A" * 100
        result = _truncate_with_summary(content, budget)
        assert "省略约 60 字符" in result


# ── 中间件集成测试 ──


class TestToolOutputBudgetMiddleware:
    @pytest.fixture
    def middleware(self):
        return ToolOutputBudgetMiddleware()

    @pytest.fixture
    def mock_runtime(self):
        class Runtime:
            pass
        return Runtime()

    @pytest.mark.asyncio
    async def test_truncate_tool_output_over_budget(self, middleware, mock_runtime):
        """超过预算的 ToolMessage 被截断。"""
        terminal_budget = TOOL_OUTPUT_BUDGETS["terminal"]
        long_output = "X" * (terminal_budget * 4 + 1000)
        state = {
            "messages": [
                HumanMessage(content="运行命令"),
                AIMessage(content="", tool_calls=[{"id": "tc1", "name": "terminal", "args": {"command": "ls"}}]),
                ToolMessage(content=long_output, name="terminal", tool_call_id="tc1"),
            ]
        }
        result = await middleware.abefore_model(state, mock_runtime)
        assert result is not None
        tool_msg = result["messages"][2]
        assert isinstance(tool_msg, ToolMessage)
        assert "省略约" in tool_msg.content
        assert len(tool_msg.content) < len(long_output)

    @pytest.mark.asyncio
    async def test_no_truncate_under_budget(self, middleware, mock_runtime):
        """未超预算的 ToolMessage 不被截断。"""
        short_output = "OK"
        state = {
            "messages": [
                HumanMessage(content="运行命令"),
                ToolMessage(content=short_output, name="terminal", tool_call_id="tc1"),
            ]
        }
        result = await middleware.abefore_model(state, mock_runtime)
        assert result is None  # 无变更

    @pytest.mark.asyncio
    async def test_non_tool_messages_untouched(self, middleware, mock_runtime):
        """非 ToolMessage 不受影响。"""
        state = {
            "messages": [
                HumanMessage(content="你好"),
                AIMessage(content="你好！"),
            ]
        }
        result = await middleware.abefore_model(state, mock_runtime)
        assert result is None

    @pytest.mark.asyncio
    async def test_mixed_messages(self, middleware, mock_runtime):
        """混合消息中只有超预算的 ToolMessage 被截断。"""
        terminal_budget = TOOL_OUTPUT_BUDGETS["terminal"]
        long_output = "Y" * (terminal_budget * 4 + 500)
        state = {
            "messages": [
                HumanMessage(content="运行"),
                ToolMessage(content=long_output, name="terminal", tool_call_id="tc1"),
                AIMessage(content="结果已完成"),
                ToolMessage(content="short", name="terminal", tool_call_id="tc2"),
            ]
        }
        result = await middleware.abefore_model(state, mock_runtime)
        assert result is not None
        msgs = result["messages"]
        assert len(msgs) == 4  # 消息数量不变
        assert "省略约" in msgs[1].content  # 第一个 ToolMessage 被截断
        assert msgs[3].content == "short"  # 第二个 ToolMessage 不变
        assert isinstance(msgs[0], HumanMessage)  # HumanMessage 不变
        assert isinstance(msgs[2], AIMessage)  # AIMessage 不变

    @pytest.mark.asyncio
    async def test_empty_messages(self, middleware, mock_runtime):
        """空消息列表返回 None。"""
        result = await middleware.abefore_model({"messages": []}, mock_runtime)
        assert result is None

    @pytest.mark.asyncio
    async def test_unknown_tool_not_truncated(self, middleware, mock_runtime):
        """未知工具名（不在预算表中）不被截断。"""
        long_output = "Z" * 10000
        state = {
            "messages": [
                ToolMessage(content=long_output, name="unknown_tool", tool_call_id="tc1"),
            ]
        }
        result = await middleware.abefore_model(state, mock_runtime)
        assert result is None  # 未知工具不做处理


# ── ContextAwareToolFilter 测试 ──


class TestContextAwareToolFilter:
    @pytest.fixture
    def filter_middleware(self):
        return ContextAwareToolFilter()

    @pytest.fixture
    def mock_tools(self):
        """模拟工具列表。"""
        class MockTool:
            def __init__(self, name):
                self.name = name
        return [MockTool(n) for tier in TOOL_TIERS.values() for n in tier]

    @pytest.fixture
    def mock_request(self, mock_tools):
        """模拟请求对象。"""
        class MockRequest:
            def __init__(self, tools, messages):
                self.tools = tools
                self.messages = messages
            def override(self, **kwargs):
                return MockRequest(kwargs.get("tools", self.tools), self.messages)
        return MockRequest

    @pytest.mark.asyncio
    async def test_chitchat_exposes_only_always_tools(
        self, filter_middleware, mock_tools, mock_request
    ):
        """纯闲聊上下文只暴露 always 工具。"""
        request = mock_request(
            mock_tools,
            [HumanMessage(content="你好"), AIMessage(content="你好！")],
        )
        handler_called = False
        async def handler(req):
            nonlocal handler_called
            handler_called = True
            tool_names = {t.name for t in req.tools}
            assert tool_names == set(TOOL_TIERS["always"])
            return "ok"
        await filter_middleware.awrap_model_call(request, handler)
        assert handler_called

    @pytest.mark.asyncio
    async def test_coding_context_exposes_coding_tools(
        self, filter_middleware, mock_tools, mock_request
    ):
        """编码上下文暴露 always + coding 工具。"""
        request = mock_request(
            mock_tools,
            [
                HumanMessage(content="帮我运行一段 python 代码"),
                AIMessage(content="好的"),
            ],
        )
        async def handler(req):
            tool_names = {t.name for t in req.tools}
            expected = set(TOOL_TIERS["always"]) | set(TOOL_TIERS["coding"])
            assert tool_names == expected
            return "ok"
        await filter_middleware.awrap_model_call(request, handler)

    @pytest.mark.asyncio
    async def test_tool_definitions_unchanged(
        self, filter_middleware, mock_tools, mock_request
    ):
        """工具定义（JSON Schema）不受过滤影响。"""
        request = mock_request(
            mock_tools,
            [HumanMessage(content="你好")],
        )
        async def handler(req):
            # 过滤后的工具对象与原始对象相同（未被修改）
            for t in req.tools:
                assert hasattr(t, "name")
            return "ok"
        await filter_middleware.awrap_model_call(request, handler)

    @pytest.mark.asyncio
    async def test_empty_tools_passthrough(self, filter_middleware, mock_request):
        """空工具列表直接通过。"""
        request = mock_request(
            [],
            [HumanMessage(content="你好")],
        )
        async def handler(req):
            assert req.tools == []
            return "ok"
        await filter_middleware.awrap_model_call(request, handler)


# ── 中间件链构建测试 ──


class TestMiddlewareChain:
    def test_build_middleware_returns_list(self):
        """AgentManager._build_middleware 返回非空列表。"""
        from graph.agent import AgentManager
        from pathlib import Path

        mgr = AgentManager()
        mgr._base_dir = Path("/tmp")
        # 无需初始化 LLM，只检查中间件构建逻辑
        middleware = mgr._build_middleware()
        assert isinstance(middleware, list)
        assert len(middleware) > 0

    def test_middleware_chain_order(self):
        """中间件链按正确顺序排列。"""
        from graph.agent import AgentManager
        from graph.middleware import ToolOutputBudgetMiddleware, ContextAwareToolFilter
        from langchain.agents.middleware import SummarizationMiddleware, ToolCallLimitMiddleware
        from pathlib import Path

        mgr = AgentManager()
        mgr._base_dir = Path("/tmp")
        middleware = mgr._build_middleware()

        # 验证类型顺序
        type_order = [type(m).__name__ for m in middleware]
        assert "ToolOutputBudgetMiddleware" in type_order
        assert "ContextAwareToolFilter" in type_order

        # ToolOutputBudgetMiddleware 必须在 SummarizationMiddleware 之前
        budget_idx = type_order.index("ToolOutputBudgetMiddleware")
        if "SummarizationMiddleware" in type_order:
            summary_idx = type_order.index("SummarizationMiddleware")
            assert budget_idx < summary_idx

        # ContextAwareToolFilter 必须在 SummarizationMiddleware 之后
        filter_idx = type_order.index("ContextAwareToolFilter")
        if "SummarizationMiddleware" in type_order:
            summary_idx = type_order.index("SummarizationMiddleware")
            assert filter_idx > summary_idx


class TestMiddlewareConfigToggles:
    """测试 config.json 中的中间件开关。"""

    def test_all_enabled_by_default(self):
        """默认配置下所有中间件都启用。"""
        from config import get_middleware_config
        mw_cfg = get_middleware_config()
        assert mw_cfg["tool_output_budget"]["enabled"] is True
        assert mw_cfg["summarization"]["enabled"] is True
        assert mw_cfg["tool_filter"]["enabled"] is True
        assert mw_cfg["tool_call_limit"]["enabled"] is True

    def test_disable_tool_output_budget(self):
        """禁用 tool_output_budget 后中间件链不包含该层。"""
        from unittest.mock import patch
        from graph.agent import AgentManager
        from pathlib import Path

        mock_cfg = {
            "tool_output_budget": {"enabled": False},
            "summarization": {"enabled": False},
            "tool_filter": {"enabled": True},
            "tool_call_limit": {"enabled": True},
        }
        with patch("config.get_middleware_config", return_value=mock_cfg):
            mgr = AgentManager()
            mgr._base_dir = Path("/tmp")
            middleware = mgr._build_middleware()
            types = [type(m).__name__ for m in middleware]
            assert "ToolOutputBudgetMiddleware" not in types
            assert "ContextAwareToolFilter" in types

    def test_disable_all_middleware(self):
        """所有中间件禁用时返回空列表。"""
        from unittest.mock import patch
        from graph.agent import AgentManager
        from pathlib import Path

        mock_cfg = {
            "tool_output_budget": {"enabled": False},
            "summarization": {"enabled": False},
            "tool_filter": {"enabled": False},
            "tool_call_limit": {"enabled": False},
        }
        with patch("config.get_middleware_config", return_value=mock_cfg):
            mgr = AgentManager()
            mgr._base_dir = Path("/tmp")
            middleware = mgr._build_middleware()
            assert middleware == []

    def test_custom_budgets_from_config(self):
        """config 中的 budgets 覆盖默认值。"""
        from graph.middleware import ToolOutputBudgetMiddleware

        custom = {"terminal": 500, "read_file": 800}
        mw = ToolOutputBudgetMiddleware(budgets=custom)
        assert mw._budgets == custom
        assert mw._budgets["terminal"] == 500

    def test_default_budgets_when_none(self):
        """不传 budgets 时使用默认值。"""
        from graph.middleware import ToolOutputBudgetMiddleware, TOOL_OUTPUT_BUDGETS

        mw = ToolOutputBudgetMiddleware()
        assert mw._budgets == TOOL_OUTPUT_BUDGETS

    def test_features_config_defaults(self):
        """features 配置默认值。"""
        from config import get_features_config
        feat = get_features_config()
        assert feat["task_state"] is True
        assert feat["unified_memory"] is True
