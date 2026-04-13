"""Agent 中间件集 — 工具输出截断、运行时工具过滤。"""

from __future__ import annotations

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
    """工具输出预算制：在摘要触发前先截断大输出。

    每次模型调用前检查所有 ToolMessage，对超过预设 token 预算的
    工具输出进行截断。截断保留头 2/3 和尾 1/3，中间插入省略标注。
    不改变消息数量和顺序。
    """

    def __init__(self, budgets: dict[str, int] | None = None):
        self._budgets = budgets if budgets is not None else TOOL_OUTPUT_BUDGETS

    async def abefore_model(self, state, runtime):
        messages = state.get("messages", [])
        if not messages:
            return None

        processed = []
        changed = False

        for msg in messages:
            if isinstance(msg, ToolMessage) and msg.name in self._budgets:
                budget = self._budgets[msg.name]
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                if _exceeds_budget(content, budget):
                    truncated = _truncate_with_summary(content, budget)
                    msg = msg.model_copy(update={"content": truncated})
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
