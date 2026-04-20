"""中间件单元测试 — ToolOutputBudgetMiddleware 渐进式压缩 + ContextAwareToolFilter。

测试覆盖：
- 5.1 渐进式逻辑（不处理 → 标准截断 → 短截断）
- 5.2 当前轮次保护机制
- 5.3 上下文窗口比例计算
- 5.4 归档能力
- 5.5 现有测试兼容性（辅助函数、ContextAwareToolFilter、中间件链）
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from graph.middleware import (
    TOOL_OUTPUT_BUDGETS,
    TOOL_TIERS,
    ContextAwareToolFilter,
    ToolOutputBudgetMiddleware,
    _COMPRESSED_MARKER,
    _exceeds_budget,
)


# ── 辅助工厂函数 ──


def _make_middleware(
    context_window: int = 131072,
    safe_ratio: float = 0.25,
    pressure_ratio: float = 0.45,
    base_dir: str | None = None,
) -> ToolOutputBudgetMiddleware:
    """构建测试用 ToolOutputBudgetMiddleware 实例。"""
    return ToolOutputBudgetMiddleware(
        context_window=context_window,
        safe_ratio=safe_ratio,
        pressure_ratio=pressure_ratio,
        base_dir=base_dir,
    )


class MockRuntime:
    """最小化的 mock runtime。"""
    pass


def _build_tool_call_msg(tc_id: str, tool_name: str = "terminal") -> AIMessage:
    """构建带 tool_calls 的 AIMessage。"""
    return AIMessage(
        content="",
        tool_calls=[{"id": tc_id, "name": tool_name, "args": {"command": "ls"}}],
    )


def _build_tool_result_msg(
    tc_id: str, content: str, tool_name: str = "terminal", msg_id: str | None = None
) -> ToolMessage:
    """构建 ToolMessage。"""
    return ToolMessage(
        content=content,
        name=tool_name,
        tool_call_id=tc_id,
        id=msg_id or f"msg_{tc_id}",
    )


def _padding_tokens(middleware: ToolOutputBudgetMiddleware, target_tokens: int) -> str:
    """生成填充字符串，使 _estimate_tokens 粗略返回 target_tokens。

    1 token ≈ 4 字符，所以需要 target_tokens * 4 个字符。
    """
    return "P" * (target_tokens * 4)


# ═══════════════════════════════════════════════════════════════
# 5.1 渐进式逻辑：不处理 → 标准截断 → 短截断
# ═══════════════════════════════════════════════════════════════


class TestProgressiveCompression:
    """渐进式压缩策略测试：验证三个水位级别的行为。"""

    @pytest.mark.asyncio
    async def test_level0_no_compression_when_safe(self):
        """Level 0：上下文 < safe_ratio 时不处理任何工具输出。"""
        # context_window=50000, safe=12500 tokens
        mw = _make_middleware(context_window=50000)
        # 10000 tokens（低于 safe_ratio 12500）
        padding = _padding_tokens(mw, 10000)
        over_budget = "X" * (TOOL_OUTPUT_BUDGETS["terminal"] * 4 + 1000)

        state = {
            "messages": [
                HumanMessage(content=padding),
                _build_tool_call_msg("tc1"),
                _build_tool_result_msg("tc1", over_budget),
            ]
        }
        result = await mw.abefore_model(state, MockRuntime())
        assert result is None, "上下文宽裕时不应压缩"

    @pytest.mark.asyncio
    async def test_level1_standard_truncation(self):
        """Level 1：safe_ratio ≤ 上下文 < pressure_ratio → 标准截断（保护 3 组）。

        需要至少 4 组工具调用，使第 4 组（最早）不在 3 组保护范围内。
        """
        # context_window=50000, safe=12500, pressure=22500
        # archive_threshold = 50000 * 0.05 * 4 = 10000 chars
        mw = _make_middleware(context_window=50000)
        over_budget = "A" * (TOOL_OUTPUT_BUDGETS["terminal"] * 4 + 2000)  # ~2500 tokens

        # padding + 4 组工具输出 ≈ 15000 + 2500 = 17500 tokens（Level 1 区间）
        padding = _padding_tokens(mw, 15000)

        state = {
            "messages": [
                HumanMessage(content=padding),
                # 第 4 组（最早，不在 3 组保护范围 → 被压缩）
                _build_tool_call_msg("tc1"),
                _build_tool_result_msg("tc1", over_budget, msg_id="g4_earliest"),
                # 第 3 组（受保护）
                _build_tool_call_msg("tc2"),
                _build_tool_result_msg("tc2", "OK", msg_id="g3"),
                # 第 2 组（受保护）
                _build_tool_call_msg("tc3"),
                _build_tool_result_msg("tc3", "OK", msg_id="g2"),
                # 第 1 组（受保护）
                _build_tool_call_msg("tc4"),
                _build_tool_result_msg("tc4", "OK", msg_id="g1"),
            ]
        }
        result = await mw.abefore_model(state, MockRuntime())
        assert result is not None, "上下文紧张时应触发压缩"

        msgs = result["messages"]
        # 最早组被截断
        assert msgs[2].id == "g4_earliest"
        assert "省略" in msgs[2].content
        # 受保护的 3 组不变
        assert msgs[4].content == "OK"
        assert msgs[6].content == "OK"
        assert msgs[8].content == "OK"

    @pytest.mark.asyncio
    async def test_level2_aggressive_truncation(self):
        """Level 2：上下文 ≥ pressure_ratio → 短截断（保护 1 组）。"""
        # context_window=50000, safe=12500, pressure=22500
        # archive_threshold = 50000 * 0.05 * 4 = 10000 chars
        # terminal budget = 2000 * 4 = 8000 chars（< archive_threshold）
        mw = _make_middleware(context_window=50000)
        over_budget = "B" * (TOOL_OUTPUT_BUDGETS["terminal"] * 4 + 2000)  # ~2500 tokens

        # padding + 2 组 ≈ 23000 + 2500 = 25500 tokens（超过 pressure 22500）
        padding = _padding_tokens(mw, 23000)

        # 两组：early 不保护（aggressive 只保护最近 1 组），latest 保护
        state = {
            "messages": [
                HumanMessage(content=padding),
                _build_tool_call_msg("tc_early"),
                _build_tool_result_msg("tc_early", over_budget, msg_id="early_id"),
                _build_tool_call_msg("tc_latest"),
                _build_tool_result_msg("tc_latest", over_budget, msg_id="latest_id"),
            ]
        }
        result = await mw.abefore_model(state, MockRuntime())
        assert result is not None

        msgs = result["messages"]
        # 早期工具输出被压缩
        early_msg = msgs[2]
        assert isinstance(early_msg, ToolMessage)
        assert early_msg.id == "early_id"
        assert "省略" in early_msg.content

        # 最新工具输出被保护（未压缩）
        latest_msg = msgs[4]
        assert isinstance(latest_msg, ToolMessage)
        assert latest_msg.id == "latest_id"
        assert latest_msg.content == over_budget

    @pytest.mark.asyncio
    async def test_non_tool_messages_never_touched(self):
        """非 ToolMessage 在任何水位都不受影响。"""
        mw = _make_middleware(context_window=50000)
        padding = _padding_tokens(mw, 30000)
        state = {
            "messages": [
                HumanMessage(content=padding),
                AIMessage(content="这是 AI 回复"),
            ]
        }
        result = await mw.abefore_model(state, MockRuntime())
        assert result is None

    @pytest.mark.asyncio
    async def test_unknown_tool_never_truncated(self):
        """未知工具名在任何水位都不被截断。"""
        mw = _make_middleware(context_window=50000)
        padding = _padding_tokens(mw, 30000)
        state = {
            "messages": [
                HumanMessage(content=padding),
                ToolMessage(content="X" * 10000, name="unknown_tool", tool_call_id="u1"),
            ]
        }
        result = await mw.abefore_model(state, MockRuntime())
        assert result is None, "未知工具名不在预算表中，不应被处理"


# ═══════════════════════════════════════════════════════════════
# 5.2 当前轮次保护机制
# ═══════════════════════════════════════════════════════════════


class TestCurrentTurnProtection:
    """验证最近 N 组工具输出不被压缩。"""

    @pytest.mark.asyncio
    async def test_protect_3_groups_in_safe_zone(self):
        """Level 1 保护最近 3 组工具输出，第 4 组被压缩。

        受保护组即使超预算也不被压缩（验证保护机制优先于预算检查）。
        """
        # context_window=80000, safe=20000, pressure=36000
        mw = _make_middleware(context_window=80000)
        over_budget = "Z" * (TOOL_OUTPUT_BUDGETS["terminal"] * 4 + 500)  # ~2500 tokens

        # padding + 4 组 over_budget ≈ 25000 + 10000 = 35000 tokens → Level 1
        padding = _padding_tokens(mw, 25000)

        state = {
            "messages": [
                HumanMessage(content=padding),
                # 第 4 组（最早，不在 3 组保护范围 → 被压缩）
                _build_tool_call_msg("tc1"),
                _build_tool_result_msg("tc1", over_budget, msg_id="g1"),
                # 第 3 组（受保护，即使超预算）
                _build_tool_call_msg("tc2"),
                _build_tool_result_msg("tc2", over_budget, msg_id="g2"),
                # 第 2 组（受保护）
                _build_tool_call_msg("tc3"),
                _build_tool_result_msg("tc3", over_budget, msg_id="g3"),
                # 第 1 组（受保护）
                _build_tool_call_msg("tc4"),
                _build_tool_result_msg("tc4", over_budget, msg_id="g4"),
            ]
        }
        result = await mw.abefore_model(state, MockRuntime())
        assert result is not None

        msgs = result["messages"]
        # 第 4 组（最早）被压缩
        assert "省略" in msgs[2].content
        # 第 3、2、1 组被保护（即使超预算）
        assert msgs[4].content == over_budget  # g2
        assert msgs[6].content == over_budget  # g3
        assert msgs[8].content == over_budget  # g4

    @pytest.mark.asyncio
    async def test_protect_1_group_in_pressure_zone(self):
        """Level 2 仅保护最近 1 组工具输出。"""
        # context_window=50000, pressure=22500
        mw = _make_middleware(context_window=50000)
        padding = _padding_tokens(mw, 23000)
        over_budget = "Y" * (TOOL_OUTPUT_BUDGETS["terminal"] * 4 + 500)

        state = {
            "messages": [
                HumanMessage(content=padding),
                # 第 3 组（应被压缩）
                _build_tool_call_msg("tc1"),
                _build_tool_result_msg("tc1", over_budget, msg_id="g1"),
                # 第 2 组（应被压缩）
                _build_tool_call_msg("tc2"),
                _build_tool_result_msg("tc2", over_budget, msg_id="g2"),
                # 第 1 组（受保护）
                _build_tool_call_msg("tc3"),
                _build_tool_result_msg("tc3", over_budget, msg_id="g3"),
            ]
        }
        result = await mw.abefore_model(state, MockRuntime())
        assert result is not None

        msgs = result["messages"]
        assert "省略" in msgs[2].content  # g1 被压缩
        assert "省略" in msgs[4].content  # g2 被压缩
        assert msgs[6].content == over_budget  # g3 受保护

    @pytest.mark.asyncio
    async def test_protect_zero_groups(self):
        """n=0 时保护集合为空（所有工具输出均可被压缩）。"""
        mw = _make_middleware(context_window=10000)
        protected = mw._get_protected_tool_ids(
            [
                _build_tool_call_msg("tc1"),
                _build_tool_result_msg("tc1", "out"),
            ],
            n=0,
        )
        assert protected == set()

    @pytest.mark.asyncio
    async def test_protect_more_groups_than_exist(self):
        """保护 N 组但消息中不足 N 组时，全部保护。"""
        mw = _make_middleware(context_window=10000)
        msgs = [
            _build_tool_call_msg("tc1"),
            _build_tool_result_msg("tc1", "out", msg_id="only_one"),
        ]
        protected = mw._get_protected_tool_ids(msgs, n=5)
        assert "only_one" in protected

    @pytest.mark.asyncio
    async def test_multi_tool_calls_in_one_group(self):
        """一组 AIMessage(tool_calls) 可含多个 tool_call，对应多个 ToolMessage。"""
        mw = _make_middleware(context_window=10000)

        # 一条 AIMessage 含 2 个 tool_calls → 同一组
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {"id": "tc_a", "name": "terminal", "args": {}},
                {"id": "tc_b", "name": "read_file", "args": {}},
            ],
        )
        msgs = [
            ai_msg,
            _build_tool_result_msg("tc_a", "out_a", msg_id="id_a"),
            _build_tool_result_msg("tc_b", "out_b", msg_id="id_b"),
        ]
        # 保护 1 组 → 两个 ToolMessage 都应被保护
        protected = mw._get_protected_tool_ids(msgs, n=1)
        assert "id_a" in protected
        assert "id_b" in protected


# ═══════════════════════════════════════════════════════════════
# 5.3 上下文窗口比例计算
# ═══════════════════════════════════════════════════════════════


class TestContextWindowRatio:
    """不同上下文窗口大小下的阈值自动适应。"""

    @pytest.mark.asyncio
    async def test_small_window_triggers_earlier(self):
        """小窗口（32K）在较少 token 时就触发压缩。"""
        # 32K 窗口: safe=8192, pressure=14745
        # archive_threshold = 32768 * 0.05 * 4 = 6553 chars
        # 需内容 > budget(8000 chars) 但 < archive_threshold → 不可能！
        # 32K 窗口下 terminal 预算的 chars(8000) > archive_threshold(6553)
        # 因此超预算的 terminal 输出会触发归档而非截断
        mw = _make_middleware(context_window=32768)
        over_budget = "S" * (TOOL_OUTPUT_BUDGETS["terminal"] * 4 + 500)  # ~2500 tokens

        # padding + 2 组 ≈ 13000 + 2500 = 15500 > pressure → Level 2
        padding = _padding_tokens(mw, 13000)

        state = {
            "messages": [
                HumanMessage(content=padding),
                _build_tool_call_msg("tc1"),
                _build_tool_result_msg("tc1", over_budget, msg_id="early"),
                _build_tool_call_msg("tc2"),
                _build_tool_result_msg("tc2", "OK", msg_id="latest"),
            ]
        }
        result = await mw.abefore_model(state, MockRuntime())
        assert result is not None, "32K 窗口应触发压缩"
        # 32K 窗口下超预算内容必然触发归档（archive_threshold < budget）
        assert "已归档至" in result["messages"][2].content

    @pytest.mark.asyncio
    async def test_large_window_stays_safe(self):
        """大窗口（1M）在同样 token 数下不触发。"""
        # 1M 窗口: safe=250000
        mw = _make_middleware(context_window=1000000)
        padding = _padding_tokens(mw, 9000)  # 远低于 safe

        state = {
            "messages": [
                HumanMessage(content=padding),
                _build_tool_call_msg("tc1"),
                _build_tool_result_msg("tc1", "X" * 10000),
            ]
        }
        result = await mw.abefore_model(state, MockRuntime())
        assert result is None, "1M 窗口 9000 tokens 应不触发"

    @pytest.mark.asyncio
    async def test_estimate_tokens_accuracy(self):
        """_estimate_tokens 粗略估算正确。"""
        mw = _make_middleware()
        # 1000 字符 ≈ 250 tokens
        msgs = [HumanMessage(content="A" * 1000)]
        assert mw._estimate_tokens(msgs) == 250

        # 多条消息累加
        msgs = [
            HumanMessage(content="A" * 400),  # 100 tokens
            AIMessage(content="B" * 800),     # 200 tokens
        ]
        assert mw._estimate_tokens(msgs) == 300

    @pytest.mark.asyncio
    async def test_custom_ratios(self):
        """自定义 safe_ratio/pressure_ratio 影响触发阈值。"""
        # 极低 safe_ratio=0.1，pressure_ratio=0.2
        # context_window=10000, safe=1000, pressure=2000
        mw = _make_middleware(context_window=10000, safe_ratio=0.1, pressure_ratio=0.2)
        over_budget = "C" * (TOOL_OUTPUT_BUDGETS["terminal"] * 4 + 500)  # ~2500 tokens

        # padding + over_budget ≈ 1100 + 2500 = 3600 > pressure=2000 → Level 2
        padding = _padding_tokens(mw, 1100)

        state = {
            "messages": [
                HumanMessage(content=padding),
                # 早期组（不在 1 组保护范围 → 被压缩）
                _build_tool_call_msg("tc1"),
                _build_tool_result_msg("tc1", over_budget, msg_id="early"),
                # 最新组（受保护）
                _build_tool_call_msg("tc2"),
                _build_tool_result_msg("tc2", "OK", msg_id="latest"),
            ]
        }
        result = await mw.abefore_model(state, MockRuntime())
        assert result is not None, "低 safe_ratio 应触发压缩"


# ═══════════════════════════════════════════════════════════════
# 5.4 归档能力
# ═══════════════════════════════════════════════════════════════


class TestArchiving:
    """超大输出归档 + 文件引用测试。"""

    @pytest.mark.asyncio
    async def test_archive_large_output(self, tmp_path):
        """单条输出超过 archive_ratio 触发归档。"""
        # context_window=50000, archive_threshold = 50000 * 0.05 * 4 = 10000 chars
        mw = _make_middleware(context_window=50000, base_dir=str(tmp_path))
        huge_output = "H" * 15000  # > archive_threshold, ~3750 tokens

        # padding + 2 组 ≈ 23000 + 3750 = 26750 > pressure=22500 → Level 2, protect 1
        padding = _padding_tokens(mw, 23000)

        state = {
            "messages": [
                HumanMessage(content=padding),
                # 早期组（不在 1 组保护范围 → 被归档）
                _build_tool_call_msg("tc1"),
                _build_tool_result_msg("tc1", huge_output, msg_id="archive_me"),
                # 最新组（受保护）
                _build_tool_call_msg("tc2"),
                _build_tool_result_msg("tc2", "OK", msg_id="latest"),
            ]
        }
        result = await mw.abefore_model(state, MockRuntime())
        assert result is not None

        tool_msg = result["messages"][2]
        assert isinstance(tool_msg, ToolMessage)
        assert tool_msg.id == "archive_me"
        # 内容应包含归档引用
        assert "已归档至" in tool_msg.content
        assert "sessions/archive/tool_terminal_" in tool_msg.content
        assert "可用 read_file 查看" in tool_msg.content

        # 归档文件存在
        archive_dir = tmp_path / "archive"
        assert archive_dir.exists()
        archive_files = list(archive_dir.glob("tool_terminal_*.txt"))
        assert len(archive_files) == 1
        assert archive_files[0].read_text(encoding="utf-8") == huge_output

    @pytest.mark.asyncio
    async def test_archive_priority_over_truncation(self, tmp_path):
        """归档优先于普通截断：内容同时超 budget 和超 archive_threshold 时触发归档。"""
        # context_window=10000, archive_threshold = 10000 * 0.05 * 4 = 2000 chars
        mw = _make_middleware(context_window=10000, base_dir=str(tmp_path))
        # 内容 = 8500 chars > budget(8000) 且 > archive_threshold(2000) → 归档
        huge_output = "A" * 8500  # ~2125 tokens

        # padding + 2 组 ≈ 5000 + 2000 = 7000 > pressure=4500 → Level 2, protect 1
        padding = _padding_tokens(mw, 5000)

        state = {
            "messages": [
                HumanMessage(content=padding),
                # 早期组（不在 1 组保护范围 → 被归档）
                _build_tool_call_msg("tc1"),
                _build_tool_result_msg("tc1", huge_output, msg_id="a1"),
                # 最新组（受保护）
                _build_tool_call_msg("tc2"),
                _build_tool_result_msg("tc2", "OK", msg_id="latest"),
            ]
        }
        result = await mw.abefore_model(state, MockRuntime())
        assert result is not None

        tool_msg = result["messages"][2]
        # 归档而非截断
        assert "已归档至" in tool_msg.content

    @pytest.mark.asyncio
    async def test_archive_file_content_recoverable(self, tmp_path):
        """归档文件可通过 read_file 恢复完整内容。"""
        mw = _make_middleware(context_window=50000, base_dir=str(tmp_path))
        # archive_threshold = 50000 * 0.05 * 4 = 10000 chars
        # 确保 > archive_threshold：每行约 7 chars，1000 行 ≈ 7000 chars + 5000 = 12000 chars
        original = ("Line %d\n" % 0) * 1000 + "X" * 5000  # ~12000 chars

        padding = _padding_tokens(mw, 23000)

        state = {
            "messages": [
                HumanMessage(content=padding),
                _build_tool_call_msg("tc1"),
                _build_tool_result_msg("tc1", original, msg_id="recover"),
                # 最新组（受保护）
                _build_tool_call_msg("tc2"),
                _build_tool_result_msg("tc2", "OK", msg_id="latest"),
            ]
        }
        result = await mw.abefore_model(state, MockRuntime())
        assert result is not None

        # 读取归档文件验证内容一致
        archive_files = list((tmp_path / "archive").glob("*.txt"))
        assert len(archive_files) == 1
        recovered = archive_files[0].read_text(encoding="utf-8")
        assert recovered == original

    @pytest.mark.asyncio
    async def test_no_archive_when_under_threshold(self, tmp_path):
        """内容低于 archive_threshold 时只截断不归档。"""
        # context_window=50000, archive_threshold = 50000 * 0.05 * 4 = 10000 chars
        mw = _make_middleware(context_window=50000, base_dir=str(tmp_path))
        # terminal budget = 8000 chars，内容 9000 chars > budget 但 < archive_threshold
        over_budget = "M" * 9000  # ~2250 tokens

        # padding + 2 组 ≈ 23000 + 2250 = 25250 > pressure → Level 2, protect 1
        padding = _padding_tokens(mw, 23000)

        state = {
            "messages": [
                HumanMessage(content=padding),
                # 早期组（不在保护范围 → 被截断）
                _build_tool_call_msg("tc1"),
                _build_tool_result_msg("tc1", over_budget, msg_id="no_archive"),
                # 最新组（受保护）
                _build_tool_call_msg("tc2"),
                _build_tool_result_msg("tc2", "OK", msg_id="latest"),
            ]
        }
        result = await mw.abefore_model(state, MockRuntime())
        assert result is not None

        tool_msg = result["messages"][2]
        # 应该是截断（truncated 策略），非归档摘要（archived 策略）
        assert _COMPRESSED_MARKER in tool_msg.content
        assert "truncated" in tool_msg.content
        assert "省略" in tool_msg.content

        # 新流程中所有超预算内容都会先归档原始数据
        archive_dir = tmp_path / "archive"
        assert archive_dir.exists()

    @pytest.mark.asyncio
    async def test_archive_filename_contains_session_id(self, tmp_path):
        """归档文件名包含 session_id。"""
        mw = _make_middleware(context_window=50000, base_dir=str(tmp_path))
        huge_output = "H" * 15000
        result = mw._archive_original(huge_output, "terminal", "sess-test123")
        assert "tool_terminal_sess-test123_" in result

        archive_files = list((tmp_path / "archive").glob("tool_terminal_sess-test123_*.txt"))
        assert len(archive_files) == 1

    @pytest.mark.asyncio
    async def test_archive_write_failure_returns_none(self, tmp_path):
        """归档写入失败时返回 None，不抛出异常。"""
        from unittest.mock import patch

        mw = _make_middleware(context_window=50000, base_dir=str(tmp_path))
        huge_output = "H" * 15000

        with patch("pathlib.Path.write_text", side_effect=OSError("磁盘满")):
            result = mw._archive_original(huge_output, "terminal", "sess-fail")

        assert result is None


# ═══════════════════════════════════════════════════════════════
# 辅助函数测试（保留）
# ═══════════════════════════════════════════════════════════════


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


class TestTruncatedContent:
    def setup_method(self):
        self.mw = ToolOutputBudgetMiddleware(context_window=131072)

    def test_truncated_has_marker(self):
        budget = 100
        content = "A" * 1000
        result = self.mw._make_truncated_content(content, budget, "truncate", None)
        assert result.startswith(_COMPRESSED_MARKER)
        assert "truncated" in result

    def test_truncated_head_tail_preserved(self):
        budget = 100  # char_budget = 400, head=266, tail=133
        content = "H" * 200 + "M" * 300 + "T" * 200  # 700 chars total
        result = self.mw._make_truncated_content(content, budget, "truncate", None)
        assert "H" * 200 in result
        assert "T" * 133 in result
        assert "省略" in result

    def test_truncated_archive_ref(self):
        budget = 10
        content = "A" * 100
        result = self.mw._make_truncated_content(
            content, budget, "truncate", "sessions/archive/test.txt"
        )
        assert "sessions/archive/test.txt" in result
        assert _COMPRESSED_MARKER in result

    def test_archived_content_structure(self):
        content = "X" * 50000
        result = self.mw._make_archived_content(
            content, "sessions/archive/big.txt", "truncate"
        )
        assert result.startswith(_COMPRESSED_MARKER)
        assert "archived" in result
        assert "sessions/archive/big.txt" in result
        assert "省略" in result

    def test_is_compressed_detects_marker(self):
        content = "<!-- compressed:truncated:1000:none -->\nsome text"
        assert ToolOutputBudgetMiddleware._is_compressed(content)

    def test_is_compressed_normal_content(self):
        assert not ToolOutputBudgetMiddleware._is_compressed("normal output")


# ═══════════════════════════════════════════════════════════════
# ContextAwareToolFilter 测试（保留）
# ═══════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════
# 中间件链构建测试（保留）
# ═══════════════════════════════════════════════════════════════


class TestMiddlewareChain:
    def test_build_middleware_returns_list(self):
        """AgentManager._build_middleware 返回非空列表。"""
        from graph.agent import AgentManager
        from pathlib import Path

        mgr = AgentManager()
        mgr._base_dir = Path("/tmp")
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

        type_order = [type(m).__name__ for m in middleware]
        assert "ToolOutputBudgetMiddleware" in type_order
        assert "ContextAwareToolFilter" in type_order

        budget_idx = type_order.index("ToolOutputBudgetMiddleware")
        if "SummarizationMiddleware" in type_order:
            summary_idx = type_order.index("SummarizationMiddleware")
            assert budget_idx < summary_idx

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
            "memory_middleware": {"enabled": False},
        }
        with patch("config.get_middleware_config", return_value=mock_cfg), \
             patch("config.get_context_window", return_value=131072):
            mgr = AgentManager()
            mgr._base_dir = Path("/tmp")
            middleware = mgr._build_middleware()
            # FilesystemFileSearchMiddleware 无 enabled 开关，始终注册
            from langchain.agents.middleware import FilesystemFileSearchMiddleware
            assert len(middleware) == 1
            assert isinstance(middleware[0], FilesystemFileSearchMiddleware)

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


# ═══════════════════════════════════════════════════════════════
# 归档文件级联清理 + GC 测试
# ═══════════════════════════════════════════════════════════════


class TestCleanupSessionArchives:
    """_cleanup_session_archives 测试。"""

    def test_deletes_matching_session_files(self, tmp_path, monkeypatch):
        """删除指定 session_id 的归档文件。"""
        from api.sessions import _cleanup_session_archives

        archive_dir = tmp_path / "sessions" / "archive"
        archive_dir.mkdir(parents=True)
        monkeypatch.setattr("api.sessions.ARCHIVE_DIR", archive_dir)

        # 创建目标 session 的文件和其他 session 的文件
        (archive_dir / "tool_terminal_sess-target_100.txt").write_text("data")
        (archive_dir / "tool_read_file_sess-target_101.txt").write_text("data")
        (archive_dir / "tool_terminal_sess-other_200.txt").write_text("data")

        _cleanup_session_archives("sess-target")

        assert not (archive_dir / "tool_terminal_sess-target_100.txt").exists()
        assert not (archive_dir / "tool_read_file_sess-target_101.txt").exists()
        assert (archive_dir / "tool_terminal_sess-other_200.txt").exists()

    def test_noop_when_archive_dir_missing(self, tmp_path, monkeypatch):
        """archive 目录不存在时静默返回。"""
        from api.sessions import _cleanup_session_archives

        monkeypatch.setattr("api.sessions.ARCHIVE_DIR", tmp_path / "nonexistent")
        _cleanup_session_archives("sess-any")  # 不应报错


class TestGcExpiredArchives:
    """_gc_expired_archives 测试。"""

    def test_deletes_old_files_keeps_recent(self, tmp_path, monkeypatch):
        """删除超期文件、保留未超期文件。"""
        import time
        from app import _gc_expired_archives

        archive_dir = tmp_path / "sessions" / "archive"
        archive_dir.mkdir(parents=True)
        monkeypatch.setattr("app.BASE_DIR", tmp_path)

        # 创建超期文件（8 天前）
        old_file = archive_dir / "tool_terminal_old_100.txt"
        old_file.write_text("old")
        import os
        old_mtime = time.time() - 8 * 86400
        os.utime(old_file, (old_mtime, old_mtime))

        # 创建未超期文件（1 天前）
        recent_file = archive_dir / "tool_terminal_recent_200.txt"
        recent_file.write_text("recent")

        _gc_expired_archives(max_age_days=7)

        assert not old_file.exists()
        assert recent_file.exists()

    def test_handles_nonexistent_dir(self, tmp_path, monkeypatch):
        """archive 目录不存在时静默返回。"""
        from app import _gc_expired_archives

        monkeypatch.setattr("app.BASE_DIR", tmp_path)
        _gc_expired_archives()  # 不应报错
