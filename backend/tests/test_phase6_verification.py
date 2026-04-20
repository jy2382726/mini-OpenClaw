"""Phase 6 验证 — 通过中间件直接调用验证 5 个场景。

6.1 标记正确嵌入 content
6.2 已标记消息被跳过（无嵌套省略）
6.3 归档文件保存原始数据
6.4 归档写入失败时降级
6.5 旧格式消息（无标记）仍被正常处理
"""

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from graph.middleware import (
    TOOL_OUTPUT_BUDGETS,
    ToolOutputBudgetMiddleware,
    _COMPRESSED_MARKER,
)


def _make_middleware(
    context_window: int = 131072,
    safe_ratio: float = 0.25,
    pressure_ratio: float = 0.45,
    base_dir: str | None = None,
) -> ToolOutputBudgetMiddleware:
    return ToolOutputBudgetMiddleware(
        context_window=context_window,
        safe_ratio=safe_ratio,
        pressure_ratio=pressure_ratio,
        base_dir=base_dir,
    )


def _padding_tokens(target_tokens: int) -> str:
    return "P" * (target_tokens * 4)


def _tc(tc_id: str, tool_name: str = "terminal") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": tc_id, "name": tool_name, "args": {"command": "ls"}}],
    )


def _tr(tc_id: str, content: str, tool_name: str = "terminal", msg_id: str | None = None) -> ToolMessage:
    return ToolMessage(
        content=content, name=tool_name,
        tool_call_id=tc_id, id=msg_id or f"msg_{tc_id}",
    )


# ---------- 6.1 标记正确嵌入 content ----------
class TestMarkerEmbedding:

    @pytest.mark.asyncio
    async def test_compressed_marker_in_content(self, tmp_path):
        """6.1: 超预算工具输出被压缩后，content 以 _COMPRESSED_MARKER 开头。"""
        # context_window=50000, safe=12500, pressure=22500
        # 保护最近 3 组 → 需 4 组使第 1 组不被保护
        mw = _make_middleware(context_window=50000, base_dir=str(tmp_path))
        over_budget = "X" * (TOOL_OUTPUT_BUDGETS["terminal"] * 4 + 2000)  # ~10000 chars
        padding = _padding_tokens(15000)

        state = {
            "messages": [
                HumanMessage(content=padding),
                _tc("tc1"), _tr("tc1", over_budget, msg_id="g4_earliest"),
                _tc("tc2"), _tr("tc2", "OK", msg_id="g3"),
                _tc("tc3"), _tr("tc3", "OK", msg_id="g2"),
                _tc("tc4"), _tr("tc4", "OK", msg_id="g1"),
            ]
        }

        result = await mw.abefore_model(state, None)
        assert result is not None, "中间件应返回修改后的消息"

        tm = [m for m in result["messages"] if isinstance(m, ToolMessage)][0]
        assert tm.id == "g4_earliest"
        assert tm.content.startswith(_COMPRESSED_MARKER), (
            f"content 应以 {_COMPRESSED_MARKER!r} 开头，实际为 {tm.content[:80]!r}"
        )

    @pytest.mark.asyncio
    async def test_marker_format_components(self, tmp_path):
        """6.1: 标记格式为 <!-- compressed:{method}:{length}:{path} -->。"""
        original_length = TOOL_OUTPUT_BUDGETS["terminal"] * 4 + 2000
        mw = _make_middleware(context_window=50000, base_dir=str(tmp_path))
        padding = _padding_tokens(15000)

        state = {
            "messages": [
                HumanMessage(content=padding),
                _tc("tc1"), _tr("tc1", "X" * original_length, msg_id="g4"),
                _tc("tc2"), _tr("tc2", "OK", msg_id="g3"),
                _tc("tc3"), _tr("tc3", "OK", msg_id="g2"),
                _tc("tc4"), _tr("tc4", "OK", msg_id="g1"),
            ]
        }

        result = await mw.abefore_model(state, None)
        tm = [m for m in result["messages"] if isinstance(m, ToolMessage)][0]

        marker_line = tm.content.split("\n")[0]
        assert marker_line.startswith(_COMPRESSED_MARKER)
        assert marker_line.endswith(" -->")

        inner = marker_line[len(_COMPRESSED_MARKER):-len(" -->")]
        parts = inner.split(":")
        assert len(parts) == 3, f"标记应有 3 部分，实际: {parts}"
        method, length_str, path = parts
        assert method in ("truncated", "archived")
        assert length_str.isdigit()
        assert int(length_str) == original_length
        assert "archive" in path or path == "none"


# ---------- 6.2 已标记消息被跳过 ----------
class TestIdempotentSkip:

    @pytest.mark.asyncio
    async def test_no_double_compression(self, tmp_path):
        """6.2: 已标记消息在第二轮不被重复压缩（省略次数不增加）。"""
        mw = _make_middleware(context_window=50000, base_dir=str(tmp_path))
        over_budget = "X" * (TOOL_OUTPUT_BUDGETS["terminal"] * 4 + 2000)
        padding = _padding_tokens(15000)

        # 第一轮：4 组，第 4 组被压缩
        state1 = {
            "messages": [
                HumanMessage(content=padding),
                _tc("tc1"), _tr("tc1", over_budget, msg_id="g4"),
                _tc("tc2"), _tr("tc2", "OK", msg_id="g3"),
                _tc("tc3"), _tr("tc3", "OK", msg_id="g2"),
                _tc("tc4"), _tr("tc4", "OK", msg_id="g1"),
            ]
        }
        result1 = await mw.abefore_model(state1, None)
        assert result1 is not None
        tm1 = [m for m in result1["messages"] if isinstance(m, ToolMessage)][0]
        assert tm1.content.startswith(_COMPRESSED_MARKER)
        first_omit_count = tm1.content.count("[省略")

        # 第二轮：保留第一轮结果，加更多消息使再次触发压缩
        more_padding = _padding_tokens(15000)
        state2 = {
            "messages": result1["messages"] + [
                HumanMessage(content=more_padding),
                _tc("tc5"), _tr("tc5", "Y" * (TOOL_OUTPUT_BUDGETS["terminal"] * 4 + 2000), msg_id="g_new"),
                _tc("tc6"), _tr("tc6", "OK", msg_id="g_protect_2"),
                _tc("tc7"), _tr("tc7", "OK", msg_id="g_protect_1"),
            ]
        }
        result2 = await mw.abefore_model(state2, None)
        assert result2 is not None

        # 找到第一轮的已压缩消息
        tm1_r2 = [m for m in result2["messages"] if isinstance(m, ToolMessage) and m.id == "g4"][0]
        second_omit_count = tm1_r2.content.count("[省略")
        assert second_omit_count == first_omit_count, (
            f"第二轮后省略次数 {second_omit_count} != 第一轮 {first_omit_count}，存在嵌套压缩"
        )

    @pytest.mark.asyncio
    async def test_compressed_message_skipped(self, tmp_path):
        """6.2: 已压缩消息在处理循环中被 continue 跳过，原样保留。"""
        mw = _make_middleware(context_window=50000, base_dir=str(tmp_path))

        compressed_content = (
            "<!-- compressed:truncated:10000:none -->\n"
            "head\n...[省略 8000 字符]...\ntail"
        )
        over_budget = "Y" * (TOOL_OUTPUT_BUDGETS["terminal"] * 4 + 2000)
        padding = _padding_tokens(15000)

        # 5 组：g5 已压缩（跳过）, g4 超预算（被压缩）, g3-g1 受保护
        state = {
            "messages": [
                HumanMessage(content=padding),
                _tc("tc0"), ToolMessage(content=compressed_content, name="terminal", tool_call_id="tc0", id="tm_compressed"),
                _tc("tc1"), _tr("tc1", over_budget, msg_id="g4"),
                _tc("tc2"), _tr("tc2", "OK", msg_id="g3"),
                _tc("tc3"), _tr("tc3", "OK", msg_id="g2"),
                _tc("tc4"), _tr("tc4", "OK", msg_id="g1"),
            ]
        }

        result = await mw.abefore_model(state, None)
        assert result is not None

        tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        # 已压缩的消息应原样保留
        compressed_tm = [m for m in tool_msgs if m.id == "tm_compressed"][0]
        assert compressed_tm.content == compressed_content, "已压缩消息应原样保留"


# ---------- 6.3 归档文件保存原始数据 ----------
class TestArchiveOriginalData:

    @pytest.mark.asyncio
    async def test_archive_contains_original(self, tmp_path):
        """6.3: 归档文件内容应与原始内容完全一致。"""
        mw = _make_middleware(context_window=50000, base_dir=str(tmp_path))
        # archive_threshold = 50000 * 0.05 * 4 = 10000 chars
        # 需要超预算 + 超过 archive_threshold
        original = "START_" + "数据块ABCDEFGHIJ" * 5000 + "_END"  # ~70000 chars > 10000
        padding = _padding_tokens(15000)

        state = {
            "messages": [
                HumanMessage(content=padding),
                _tc("tc1"), _tr("tc1", original, msg_id="g4"),
                _tc("tc2"), _tr("tc2", "OK", msg_id="g3"),
                _tc("tc3"), _tr("tc3", "OK", msg_id="g2"),
                _tc("tc4"), _tr("tc4", "OK", msg_id="g1"),
            ]
        }

        result = await mw.abefore_model(state, None)
        assert result is not None

        archive_dir = tmp_path / "archive"
        assert archive_dir.exists(), "归档目录应存在"

        archive_files = list(archive_dir.glob("*.txt"))
        assert len(archive_files) >= 1, "应至少有一个归档文件"

        archived_content = archive_files[0].read_text(encoding="utf-8")
        assert archived_content == original, (
            f"归档内容长度 {len(archived_content)} != 原始长度 {len(original)}"
        )

    @pytest.mark.asyncio
    async def test_archive_not_truncated(self, tmp_path):
        """6.3: 归档文件包含完整头尾（非截断内容）。"""
        mw = _make_middleware(context_window=50000, base_dir=str(tmp_path))
        original = "START_MARKER" + "M" * 80000 + "END_MARKER"  # ~80000 chars
        padding = _padding_tokens(15000)

        state = {
            "messages": [
                HumanMessage(content=padding),
                _tc("tc1"), _tr("tc1", original, msg_id="g4"),
                _tc("tc2"), _tr("tc2", "OK", msg_id="g3"),
                _tc("tc3"), _tr("tc3", "OK", msg_id="g2"),
                _tc("tc4"), _tr("tc4", "OK", msg_id="g1"),
            ]
        }

        result = await mw.abefore_model(state, None)

        archive_dir = tmp_path / "archive"
        archived = list(archive_dir.glob("*.txt"))[0].read_text(encoding="utf-8")

        assert archived.startswith("START_MARKER"), "归档应包含完整头部"
        assert archived.endswith("END_MARKER"), "归档应包含完整尾部"
        assert len(archived) == len(original), (
            f"归档长度 {len(archived)} != 原始 {len(original)}"
        )


# ---------- 6.4 归档写入失败降级 ----------
class TestArchiveFailureDegradation:

    @pytest.mark.asyncio
    async def test_graceful_degradation_on_archive_failure(self, tmp_path):
        """6.4: 归档写入失败后仍能正常截断，标记中路径为 none。"""
        mw = _make_middleware(context_window=50000, base_dir=str(tmp_path))
        over_budget = "X" * (TOOL_OUTPUT_BUDGETS["terminal"] * 4 + 2000)
        padding = _padding_tokens(15000)

        state = {
            "messages": [
                HumanMessage(content=padding),
                _tc("tc1"), _tr("tc1", over_budget, msg_id="g4"),
                _tc("tc2"), _tr("tc2", "OK", msg_id="g3"),
                _tc("tc3"), _tr("tc3", "OK", msg_id="g2"),
                _tc("tc4"), _tr("tc4", "OK", msg_id="g1"),
            ]
        }

        with patch.object(mw, "_archive_original", return_value=None):
            result = await mw.abefore_model(state, None)

        assert result is not None, "归档失败不应阻止压缩"

        tm = [m for m in result["messages"] if isinstance(m, ToolMessage)][0]
        assert tm.content.startswith(_COMPRESSED_MARKER)

        marker_line = tm.content.split("\n")[0]
        assert ":none -->" in marker_line, f"归档失败时路径应为 none，实际: {marker_line}"

    @pytest.mark.asyncio
    async def test_no_exception_on_write_failure(self, tmp_path):
        """6.4: _archive_original 写入失败时返回 None，不抛异常。"""
        mw = _make_middleware(context_window=50000, base_dir=str(tmp_path))

        with patch("pathlib.Path.write_text", side_effect=OSError("磁盘满")):
            result = mw._archive_original("测试内容", "terminal", "test-session")
            assert result is None, "写入失败应返回 None"


# ---------- 6.5 旧格式消息兼容 ----------
class TestOldFormatCompatibility:

    @pytest.mark.asyncio
    async def test_old_format_message_processed(self, tmp_path):
        """6.5: 无 _COMPRESSED_MARKER 前缀的消息被正常压缩处理。"""
        mw = _make_middleware(context_window=50000, base_dir=str(tmp_path))
        old_content = "普通的长工具输出 " * 2000  # ~70000 chars
        padding = _padding_tokens(15000)

        state = {
            "messages": [
                HumanMessage(content=padding),
                _tc("tc1"), _tr("tc1", old_content, msg_id="g4"),
                _tc("tc2"), _tr("tc2", "OK", msg_id="g3"),
                _tc("tc3"), _tr("tc3", "OK", msg_id="g2"),
                _tc("tc4"), _tr("tc4", "OK", msg_id="g1"),
            ]
        }

        result = await mw.abefore_model(state, None)
        assert result is not None

        tm = [m for m in result["messages"] if isinstance(m, ToolMessage)][0]
        assert tm.content.startswith(_COMPRESSED_MARKER), "旧格式消息应被处理并添加标记"

    @pytest.mark.asyncio
    async def test_short_old_format_untouched(self, tmp_path):
        """6.5: 未超预算的旧格式消息不被处理。"""
        mw = _make_middleware(context_window=50000, base_dir=str(tmp_path))
        short_content = "ls: total 42\ndir1\ndir2\n"
        padding = _padding_tokens(5000)  # 远低于 safe=12500

        state = {
            "messages": [
                HumanMessage(content=padding),
                _tc("tc1"), _tr("tc1", short_content),
            ]
        }

        result = await mw.abefore_model(state, None)
        assert result is None, "短消息不应触发压缩"

    @pytest.mark.asyncio
    async def test_mixed_old_and_compressed(self, tmp_path):
        """6.5: 混合场景：已有压缩消息 + 新的旧格式消息。"""
        mw = _make_middleware(context_window=50000, base_dir=str(tmp_path))

        compressed_msg = ToolMessage(
            content="<!-- compressed:truncated:10000:none -->\nhead\n...[省略 8000 字符]...\ntail",
            name="terminal", tool_call_id="tc0", id="tm_compressed",
        )
        over_budget = "Y" * (TOOL_OUTPUT_BUDGETS["terminal"] * 4 + 2000)
        padding = _padding_tokens(15000)

        # 5 组：g5 已压缩（跳过）, g4 超预算（被压缩）, g3-g1 受保护
        state = {
            "messages": [
                HumanMessage(content=padding),
                _tc("tc0"), compressed_msg,
                _tc("tc1"), _tr("tc1", over_budget, msg_id="g4"),
                _tc("tc2"), _tr("tc2", "OK", msg_id="g3"),
                _tc("tc3"), _tr("tc3", "OK", msg_id="g2"),
                _tc("tc4"), _tr("tc4", "OK", msg_id="g1"),
            ]
        }

        result = await mw.abefore_model(state, None)
        assert result is not None

        tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]

        # 已压缩消息应不变
        compressed_tm = [m for m in tool_msgs if m.id == "tm_compressed"][0]
        assert compressed_tm.content.count("[省略") == 1, "已压缩消息不应被再次压缩"

        # 旧的超预算消息应被压缩
        g4_tm = [m for m in tool_msgs if m.id == "g4"][0]
        assert g4_tm.content.startswith(_COMPRESSED_MARKER), "旧格式消息应被处理"
