"""Agent 中间件集 — 渐进式工具输出压缩、运行时工具过滤。"""

from __future__ import annotations

import time
from pathlib import Path

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

# 各工具类型的 token 预算（1 token ≈ 4 字符）
TOOL_OUTPUT_BUDGETS: dict[str, int] = {
    "terminal": 2000,
    "python_repl": 1500,
    "fetch_url": 3000,
    "read_file": 2000,
    "search_knowledge": 1000,
}

# 工具分类 tier — 供 ContextAwareToolFilter 使用
TOOL_TIERS: dict[str, list[str]] = {
    "always": ["read_file", "search_knowledge"],
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

    def _compress(self, content: str, budget: int, strategy: str) -> str:
        """根据策略压缩内容。

        - "truncate": 标准截断（头 2/3 + 尾 1/3）
        - "aggressive": 短截断（头 1/2 + 尾 1/4）
        """
        char_budget = budget * 4

        if strategy == "truncate":
            head_len = char_budget * 2 // 3
            tail_len = char_budget // 3
        else:  # "aggressive"
            head_len = char_budget // 2
            tail_len = char_budget // 4

        head = content[:head_len]
        tail = content[-tail_len:] if tail_len > 0 else ""
        omitted = len(content) - head_len - tail_len
        return f"{head}\n...[省略约 {omitted} 字符]...\n{tail}"

    def _archive_output(self, content: str, tool_name: str) -> str:
        """归档超大输出到文件，返回摘要 + 文件路径引用。"""
        archive_dir = self._base_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)

        timestamp = int(time.time())
        filename = f"tool_{tool_name}_{timestamp}.txt"
        filepath = archive_dir / filename

        filepath.write_text(content, encoding="utf-8")

        # 生成截断摘要（约 500 token）
        summary_budget = 500 * 4
        if len(content) > summary_budget:
            summary = content[: summary_budget * 2 // 3] + "\n...[已截断]..."
        else:
            summary = content

        return f"[完整输出已归档到 sessions/archive/{filename}，可用 read_file 查看]\n{summary}"

    async def abefore_model(self, state, runtime):
        messages = state.get("messages", [])
        if not messages:
            return None

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

                # Level 3: 归档超大输出（优先于普通截断）
                if len(content) > archive_threshold * 4:
                    archived = self._archive_output(content, msg.name)
                    msg = msg.model_copy(update={"content": archived})
                    changed = True
                # Level 1/2: 按策略截断
                elif _exceeds_budget(content, self._budgets[msg.name]):
                    compressed = self._compress(content, self._budgets[msg.name], strategy)
                    msg = msg.model_copy(update={"content": compressed})
                    changed = True

            processed.append(msg)

        if changed:
            return {"messages": processed}
        return None


def _exceeds_budget(content: str, budget: int) -> bool:
    """检查内容是否超过 token 预算（1 token ≈ 4 字符）。"""
    return len(content) > budget * 4


def _truncate_with_summary(content: str, budget: int) -> str:
    """截断内容：保留头 2/3 + 尾 1/3，中间插入省略标注。"""
    char_budget = budget * 4
    head_len = char_budget * 2 // 3
    tail_len = char_budget // 3
    head = content[:head_len]
    tail = content[-tail_len:]
    omitted = len(content) - char_budget
    return f"{head}\n...[省略约 {omitted} 字符]...\n{tail}"


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
        keywords = ["代码", "函数", "编辑文件", "终端", "运行", "python", "terminal", "code", "script", "exec"]
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
