"""MemoryMiddleware — 记忆检索/注入/写入中间件。

封装记忆全生命周期管理，通过 3 个 hook 实现：
- abefore_agent: 检索相关记忆 → state["memory_context"]
- awrap_model_call: state → request.override(system_message=...)
- aafter_agent: 后台写入 MemoryBuffer + Mem0Manager
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain.agents.middleware import AgentMiddleware, ModelRequest
from typing import TypedDict, NotRequired


class MemoryMiddlewareState(TypedDict, total=False):
    """记忆中间件 graph state 扩展。"""

    memory_context: NotRequired[str]


class MemoryMiddleware(AgentMiddleware):
    """记忆检索/注入/写入中间件（第 5 层）。"""

    state_schema = MemoryMiddlewareState

    def __init__(
        self,
        base_dir: Path,
        config: dict[str, Any],
        write_executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self._base_dir = base_dir
        self._config = config
        self._write_executor = write_executor
        self._injection_mode = config.get("injection_mode", "system_prompt")
        self._retriever = None  # 延迟初始化

    # ── 工具方法 ──────────────────────────────────────────────

    @staticmethod
    def _extract_latest_user_message(messages: list) -> HumanMessage | None:
        """从 messages 末尾向前查找最新 HumanMessage。"""
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                return msg
        return None

    @staticmethod
    def _extract_last_exchange(messages: list) -> tuple[str, str] | None:
        """提取最后一轮用户-助手对话文本。

        Returns:
            (user_text, assistant_text) 或 None（无完整对话时）。
        """
        user_text = None
        assistant_text = None

        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and assistant_text is None:
                content = getattr(msg, "content", "")
                if content:
                    assistant_text = content
            elif isinstance(msg, HumanMessage) and user_text is None:
                content = getattr(msg, "content", "")
                if content:
                    user_text = content

            if user_text is not None and assistant_text is not None:
                return (user_text, assistant_text)

        return None

    # ── Hook: abefore_agent — 记忆检索 ────────────────────────

    async def abefore_agent(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        """每轮对话前检索相关记忆。

        检查配置开关 → 延迟初始化 retriever → 异步检索 → 发送 retrieval 事件 → 返回 state 更新。
        """
        # 2.1 检查配置开关
        if not self._config.get("enabled", True):
            return None
        from config import get_features_config
        features = get_features_config()
        if not features.get("unified_memory", True):
            return None

        # 2.1 无用户消息时跳过
        messages = state.get("messages", [])
        user_msg = self._extract_latest_user_message(messages)
        if user_msg is None:
            return None

        query = getattr(user_msg, "content", "")

        # 2.2 延迟初始化 UnifiedMemoryRetriever
        if self._retriever is None:
            from graph.unified_memory import get_unified_retriever
            self._retriever = get_unified_retriever(self._base_dir)

        # 2.3 异步检索
        results = await self._retriever.retrieve_async(query)
        memory_context = ""
        if results:
            memory_context = self._retriever.format_for_injection(results)
            # 2.4 通过 stream_writer 发送 retrieval 事件
            runtime.stream_writer({
                "type": "retrieval",
                "query": query,
                "results": results,
            })

        # 2.5 / 2.6 双注入模式返回
        if self._injection_mode == "system_message" and memory_context:
            # system_message 模式：构造 SystemMessage 插入 messages
            from graph.prompt_builder import build_dynamic_prefix
            dynamic_prefix = build_dynamic_prefix(memory_context=memory_context)
            new_messages = list(messages)
            # 找到最后一条用户消息的位置，在其之前插入
            insert_pos = len(new_messages)
            for i in range(len(new_messages) - 1, -1, -1):
                if isinstance(new_messages[i], HumanMessage):
                    insert_pos = i
                    break
            new_messages.insert(insert_pos, SystemMessage(content=dynamic_prefix))
            return {"messages": new_messages, "memory_context": memory_context}

        # system_prompt 模式（默认）：仅返回 memory_context
        return {"memory_context": memory_context}

    # ── Hook: awrap_model_call — 记忆注入 ──────────────────────

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable,
    ) -> Any:
        """将记忆上下文注入系统提示（system_prompt 模式）。

        system_message 模式下已在 abefore_agent 注入，此处直接调用 handler。
        """
        # 3.1 system_message 模式：已在 abefore_agent 处理
        if self._injection_mode == "system_message":
            return await handler(request)

        # 3.2 system_prompt 模式：从 state 读取 memory_context
        memory_context = request.state.get("memory_context", "")
        if not memory_context:
            return await handler(request)

        # 3.3 拼接记忆上下文到 system_message 末尾
        current_content = request.system_message.content if request.system_message else ""
        new_content = current_content + f"\n\n<!-- Zone 3: Dynamic -->\n{memory_context}"
        new_system_message = SystemMessage(content=new_content)

        # 3.4 通过 override 创建新请求
        new_request = request.override(system_message=new_system_message)
        return await handler(new_request)

    # ── Hook: aafter_agent — 记忆写入 ──────────────────────────

    async def aafter_agent(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        """对话结束后将对话写入记忆缓冲区（后台线程）。"""
        # 4.1 检查 mem0 配置和 write_executor
        from config import get_mem0_config
        mem0_cfg = get_mem0_config()
        if not (mem0_cfg.get("enabled") and mem0_cfg.get("auto_extract")):
            return None
        if self._write_executor is None:
            return None

        # 4.2 提取最后一轮对话
        messages = state.get("messages", [])
        exchange = self._extract_last_exchange(messages)
        if exchange is None:
            return None

        user_text, assistant_text = exchange
        # 4.3 提交后台写入任务
        self._schedule_write(user_text, assistant_text, mem0_cfg)
        return None

    def _schedule_write(self, user_message: str, assistant_message: str, mem0_cfg: dict) -> None:
        """通过 write_executor 提交后台写入任务。"""
        base_dir = self._base_dir

        def _background_write() -> None:
            try:
                from graph.memory_buffer import get_memory_buffer
                from graph.mem0_manager import get_mem0_manager

                buffer = get_memory_buffer(base_dir)
                buffer.add_turn(user_message, assistant_message, "default")

                should_flush = buffer.check_immediate_trigger(user_message)
                if not should_flush:
                    should_flush = buffer.should_flush()

                if should_flush:
                    turns = buffer.flush()
                    if turns:
                        mgr = get_mem0_manager(base_dir)
                        mgr.batch_add(turns, user_id=mem0_cfg.get("user_id", "default"))
                        print(f"🧠 mem0 后台写入完成（{len(turns)} 轮对话）")
            except Exception as e:
                print(f"⚠️ mem0 后台写入失败: {e}")

        self._write_executor.submit(_background_write)
