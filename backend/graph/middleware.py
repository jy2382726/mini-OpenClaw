"""Agent 中间件集 — 渐进式工具输出压缩、运行时工具过滤、上下文感知摘要。"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from langchain.agents.middleware import SummarizationMiddleware
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import RemoveMessage, SystemMessage, ToolMessage
from langgraph.config import get_config

logger = logging.getLogger(__name__)

# 内置中文摘要提示词（8 节结构）
DEFAULT_SUMMARY_PROMPT_ZH = """<role>
会话摘要助手
</role>

<primary_objective>
你的唯一任务是从对话历史中提取最高质量、最相关的上下文信息。
</primary_objective>

<instructions>
对话历史将被你提取的上下文信息替换。请确保提取的信息是对整体目标最重要的内容。
请使用以下章节结构组织摘要，每个章节作为检查清单，有相关内容则填写，无则注明"无"：

## 会话意图
用户的主要目标或请求是什么？整体上要完成什么任务？简明扼要但足够理解整个会话的目的。

## 关键决策
记录对话中的重要选择、结论或策略。包括决策背后的推理。记录被否决的方案及其原因。

## 工具调用
记录已执行的工具调用及其结果摘要。包括终端命令、文件操作、API 调用等。

## 文件产物
创建、修改或访问了哪些文件？列出具体文件路径并简要描述每个文件的变更内容。

## 错误修复
遇到并解决了哪些错误或问题？记录错误原因和修复方法。

## 用户消息
用户在对话中传达的关键信息、偏好或反馈。

## 当前进展
当前任务完成到什么程度？哪些步骤已完成，哪些进行中？

## 后续步骤
还有哪些具体任务需要完成以实现会话意图？下一步应该做什么？

</instructions>

请仔细阅读完整对话历史，提取最重要和相关的上下文信息以替换对话历史。
仅输出提取的上下文内容，不要包含任何额外信息或前后说明。

<messages>
待摘要的对话：
{messages}
</messages>"""

# 压缩标记前缀 — 已处理的 ToolMessage 以此开头，跳过重复压缩
_COMPRESSED_MARKER = "<!-- compressed:"

# 各工具类型的 token 预算（1 token ≈ 4 字符）
TOOL_OUTPUT_BUDGETS: dict[str, int] = {
    "terminal": 2000,
    "python_repl": 1500,
    "fetch_url": 3000,
    "read_file": 2000,
    "search_knowledge": 1000,
    "glob_search": 1500,
    "grep_search": 2500,
}

# 工具分类 tier — 供 ContextAwareToolFilter 使用
TOOL_TIERS: dict[str, list[str]] = {
    "always": ["read_file", "search_knowledge", "glob_search", "grep_search"],
    "coding": ["terminal", "python_repl", "write_file"],
    "web": ["fetch_url"],
    "memory": ["save_memory", "search_memories"],
    "admin": ["create_skill_version"],
}


class ToolOutputBudgetMiddleware(AgentMiddleware):
    """渐进式工具输出压缩：基于上下文窗口比例，仅在压力增大时压缩早期输出。

    压缩策略决策树：
    1. 已用 token < 窗口 × safe_ratio  → 不处理
    2. safe_ratio ≤ 已用 < pressure_ratio → 标准截断（保护最近 3 组）
    3. 已用 ≥ pressure_ratio → 短截断（仅保护最近 1 组）
    4. 单条 > archive_ratio → 归档到文件 + 摘要引用
    """

    ARCHIVE_RATIO = 0.05  # 单条输出超过窗口 5% 触发归档

    def __init__(
        self,
        budgets: dict[str, int] | None = None,
        context_window: int = 131072,
        safe_ratio: float = 0.25,
        pressure_ratio: float = 0.45,
        base_dir: str | Path | None = None,
    ):
        self._budgets = budgets if budgets is not None else TOOL_OUTPUT_BUDGETS
        self._context_window = context_window
        self._safe_ratio = safe_ratio
        self._pressure_ratio = pressure_ratio
        self._base_dir = Path(base_dir) if base_dir else Path("sessions")

    @staticmethod
    def _is_compressed(content: str) -> bool:
        """检测 content 是否已被压缩处理。"""
        return isinstance(content, str) and content.startswith(_COMPRESSED_MARKER)

    @staticmethod
    def _make_marker(method: str, original_length: int, archive_path: str | None) -> str:
        """生成压缩标记头。"""
        path = archive_path or "none"
        return f"{_COMPRESSED_MARKER}{method}:{original_length}:{path} -->"

    def _estimate_tokens(self, messages: list) -> int:
        """粗略估算消息列表总 token 数（1 token ≈ 4 字符）。"""
        total_chars = 0
        for msg in messages:
            content = getattr(msg, "content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                # 多模态消息格式
                for part in content:
                    if isinstance(part, str):
                        total_chars += len(part)
                    elif isinstance(part, dict) and isinstance(part.get("text"), str):
                        total_chars += len(part["text"])
        return total_chars // 4

    def _get_protected_tool_ids(self, messages: list, n: int) -> set[str]:
        """识别最近 N 组工具输出的 ToolMessage ID，这些消息不被压缩。

        「一组工具输出」= 一条 AIMessage(tool_calls) 及其对应的所有 ToolMessage。
        从消息列表末尾向前扫描，每遇到一条 AIMessage(tool_calls) 计为一组。
        """
        if n <= 0:
            return set()

        protected_ids: set[str] = set()
        groups_found = 0

        for msg in reversed(messages):
            # AIMessage(tool_calls) 标记一组工具调用的起点
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                groups_found += 1
                if groups_found > n:
                    break
            # 属于已计组内的 ToolMessage 被保护
            elif isinstance(msg, ToolMessage) and groups_found < n:
                protected_ids.add(msg.id)

        return protected_ids

    def _archive_original(self, content: str, tool_name: str, session_id: str) -> str | None:
        """将原始内容保存到归档文件，返回文件相对路径。写入失败返回 None。"""
        archive_dir = self._base_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)

        timestamp = int(time.time())
        filename = f"tool_{tool_name}_{session_id}_{timestamp}.txt"
        filepath = archive_dir / filename

        try:
            filepath.write_text(content, encoding="utf-8")
            return f"sessions/archive/{filename}"
        except (OSError, PermissionError, IOError):
            logger.warning("归档写入失败 %s", filepath)
            return None

    def _make_archived_content(
        self, original: str, archive_path: str | None, strategy: str
    ) -> str:
        """生成 archived 策略的替换内容（用于超大输出）。"""
        marker = self._make_marker("archived", len(original), archive_path)
        head_budget = 500 * 4
        tail_budget = 200 * 4
        ref = (
            f"[完整输出({len(original)}字符)已归档至 {archive_path}，可用 read_file 查看]"
            if archive_path
            else ""
        )

        head = original[:head_budget]
        tail = original[-tail_budget:] if tail_budget > 0 else ""
        omitted = len(original) - head_budget - tail_budget

        return f"{marker}\n{ref}\n{head}\n...[省略 {omitted} 字符]...\n{tail}"

    def _make_truncated_content(
        self, original: str, budget: int, strategy: str, archive_path: str | None
    ) -> str:
        """生成 truncated 策略的替换内容（用于中等输出）。"""
        marker = self._make_marker("truncated", len(original), archive_path)
        char_budget = budget * 4
        ref = (
            f"[原始输出({len(original)}字符)已归档至 {archive_path}]"
            if archive_path
            else ""
        )

        if strategy == "truncate":
            head_len = char_budget * 2 // 3
            tail_len = char_budget // 3
        else:  # aggressive
            head_len = char_budget // 2
            tail_len = char_budget // 4

        head = original[:head_len]
        tail = original[-tail_len:] if tail_len > 0 else ""
        omitted = len(original) - head_len - tail_len

        return f"{marker}\n{ref}\n{head}\n...[省略 {omitted} 字符]...\n{tail}"

    async def abefore_model(self, state, runtime):
        messages = state.get("messages", [])
        if not messages:
            return None

        # 从 graph config 获取 session_id（thread_id）
        try:
            config = get_config()
            session_id = config.get("configurable", {}).get("thread_id", "unknown")
        except RuntimeError:
            session_id = "unknown"

        total_tokens = self._estimate_tokens(messages)
        safe = int(self._context_window * self._safe_ratio)
        pressure = int(self._context_window * self._pressure_ratio)
        archive_threshold = int(self._context_window * self.ARCHIVE_RATIO)

        # Level 0: 上下文宽裕，不处理
        if total_tokens < safe:
            return None

        # 确定保护范围和压缩策略
        if total_tokens < pressure:
            protect_recent = 3  # 保护最近 3 组工具输出
            strategy = "truncate"
        else:
            protect_recent = 1  # 仅保护最近 1 组
            strategy = "aggressive"

        protected_ids = self._get_protected_tool_ids(messages, protect_recent)

        processed = []
        changed = False

        for msg in messages:
            if (
                isinstance(msg, ToolMessage)
                and msg.name in self._budgets
                and msg.id not in protected_ids
            ):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)

                # Step 1: 幂等检测 — 已处理过的消息直接跳过
                if self._is_compressed(content):
                    processed.append(msg)
                    continue

                # Step 2: 预算检测 — 未超预算的不处理
                if not _exceeds_budget(content, self._budgets[msg.name]):
                    processed.append(msg)
                    continue

                # Step 3: 保全原始数据 — 先归档，再压缩
                archive_path = self._archive_original(content, msg.name, session_id)

                # Step 4: 选择压缩策略
                if len(content) > archive_threshold * 4:
                    new_content = self._make_archived_content(content, archive_path, strategy)
                else:
                    new_content = self._make_truncated_content(
                        content, self._budgets[msg.name], strategy, archive_path
                    )

                msg = msg.model_copy(update={"content": new_content})
                changed = True

            processed.append(msg)

        if changed:
            compressed_count = sum(
                1 for m in processed
                if isinstance(m, ToolMessage) and self._is_compressed(
                    m.content if isinstance(m.content, str) else str(m.content)
                )
            )
            logger.info(
                "工具输出压缩: %d 条消息已处理, 策略=%s, 保护=%d组",
                compressed_count, strategy, protect_recent,
            )
            return {"messages": processed}
        return None


def _exceeds_budget(content: str, budget: int) -> bool:
    """检查内容是否超过 token 预算（1 token ≈ 4 字符）。"""
    return len(content) > budget * 4


class ContextAwareToolFilter(AgentMiddleware):
    """运行时工具过滤：根据对话上下文动态裁剪可用工具。

    工具定义始终完整（保护 KV-cache），运行时只暴露相关子集。
    分析最近 N 条消息的关键词，判断需要的工具 tier。
    """

    def __init__(self, context_window: int = 6):
        self._context_window = context_window

    def wrap_model_call(self, request, handler):
        """同步版工具过滤。"""
        tools = getattr(request, "tools", None) or []
        messages = getattr(request, "messages", [])

        if not tools or not messages:
            return handler(request)

        filtered = self._filter_tools(tools, messages)
        request = request.override(tools=filtered)
        return handler(request)

    async def awrap_model_call(self, request, handler):
        """异步版工具过滤。"""
        tools = getattr(request, "tools", None) or []
        messages = getattr(request, "messages", [])

        if not tools or not messages:
            return await handler(request)

        filtered = self._filter_tools(tools, messages)
        request = request.override(tools=filtered)
        return await handler(request)

    def _filter_tools(self, tools: list, messages: list) -> list:
        """根据上下文关键词过滤工具列表。"""
        needed_tiers = ["always"]

        recent_text = self._extract_recent_text(messages)
        if self._has_coding_context(recent_text):
            needed_tiers.append("coding")
        if self._has_web_context(recent_text):
            needed_tiers.append("web")
        if self._has_memory_context(recent_text):
            needed_tiers.append("memory")
        if self._has_admin_context(recent_text):
            needed_tiers.append("admin")

        allowed_names: set[str] = set()
        for tier in needed_tiers:
            allowed_names.update(TOOL_TIERS.get(tier, []))

        filtered = [t for t in tools if t.name in allowed_names]
        if not filtered:
            # 安全网：至少保留 always 工具
            filtered = [t for t in tools if t.name in TOOL_TIERS.get("always", [])]
        return filtered

    def _extract_recent_text(self, messages: list) -> str:
        """提取最近 N 条消息的文本内容。"""
        recent = messages[-self._context_window :] if len(messages) > self._context_window else messages
        parts = []
        for m in recent:
            content = getattr(m, "content", None)
            if content and isinstance(content, str):
                parts.append(content)
        return " ".join(parts).lower()

    @staticmethod
    def _has_coding_context(text: str) -> bool:
        keywords = [
            "代码", "函数", "编辑文件", "终端", "运行", "python", "terminal", "code", "script", "exec",
            "搜索文件", "查找文件", "搜索代码", "查找代码", "glob", "grep", "find file", "search code",
        ]
        return any(kw in text for kw in keywords)

    @staticmethod
    def _has_web_context(text: str) -> bool:
        keywords = ["网址", "网页", "链接", "fetch", "url", "http", "api", "请求"]
        return any(kw in text for kw in keywords)

    @staticmethod
    def _has_memory_context(text: str) -> bool:
        keywords = ["记忆", "保存", "回忆", "记住", "memory", "memo", "存储"]
        return any(kw in text for kw in keywords)

    @staticmethod
    def _has_admin_context(text: str) -> bool:
        keywords = ["技能管理", "skill", "创建技能", "benchmark", "评估技能"]
        return any(kw in text for kw in keywords)


class ContextAwareSummarizationMiddleware(SummarizationMiddleware):
    """摘要中间件子类：在摘要过程中保护 SystemMessage 不被压缩。

    父类 abefore_model 返回格式：
    [RemoveMessage(REMOVE_ALL_MESSAGES), HumanMessage(summary), ...preserved]
    SystemMessage 在 insert_pos=2 处重新注入。
    """

    async def abefore_model(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        messages = state["messages"]

        # 提取所有 SystemMessage
        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        if not system_msgs:
            return await super().abefore_model(state, runtime)

        # 构造不含 SystemMessage 的过滤 state
        filtered_state = {**state, "messages": [m for m in messages if not isinstance(m, SystemMessage)]}

        # 调用父类摘要逻辑
        result = await super().abefore_model(filtered_state, runtime)
        if result is None:
            return None

        # 重新注入 SystemMessage（insert_pos=2：RemoveMessage[0] + summary[1] 之后）
        new_messages = result["messages"]
        insert_pos = 2
        new_messages = new_messages[:insert_pos] + system_msgs + new_messages[insert_pos:]

        return {"messages": new_messages}
