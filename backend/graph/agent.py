"""AgentManager — Core Agent using LangChain create_agent API with DashScope Qwen."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, AsyncGenerator

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from config import load_config, get_mem0_config, get_features_config
from graph.prompt_builder import build_stable_prefix, build_dynamic_prefix
from graph.session_manager import session_manager, COMPRESSED_CONTEXT_PREFIX
from tools import get_all_tools


class AgentManager:
    """Manages the Agent lifecycle: initialization, streaming, invocation."""

    def __init__(self) -> None:
        self._base_dir: Path | None = None
        self._tools: list = []
        self._llm = None
        self._write_executor = ThreadPoolExecutor(max_workers=4)
        self._skill_registry = None  # 缓存 SkillRegistry 实例
        self._checkpointer = None  # 跨请求共享的 checkpointer
        self._db_path: str | None = None  # SQLite 数据库路径（懒加载）

    def initialize(self, base_dir: Path) -> None:
        """Initialize LLM (DashScope Qwen) and tools. Called once at startup."""
        self._base_dir = base_dir
        self._tools = get_all_tools(base_dir)

        # Load LLM config: config.json takes priority, .env as fallback
        config = load_config()
        llm_config = config.get("llm", {})
        model = llm_config.get("model") or os.getenv("DASHSCOPE_MODEL", "qwen3.5-plus")
        api_key = llm_config.get("api_key") or os.getenv("DASHSCOPE_API_KEY", "")
        api_base = llm_config.get("base_url") or os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        temperature = llm_config.get("temperature", 0.7)

        # Use langchain-openai for DashScope compatible mode
        from langchain_openai import ChatOpenAI

        self._llm = ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=api_base,
            temperature=temperature,
            streaming=True,
        )

        session_manager.initialize(base_dir)

        # SQLite 持久化路径：AsyncSqliteSaver 在首次异步调用时懒加载
        self._db_path = str(base_dir / "checkpoints.sqlite")

        print(f"🤖 Agent initialized with {len(self._tools)} tools (model: {model})")

    async def _ensure_checkpointer(self):
        """懒加载 AsyncSqliteSaver（需要异步创建 aiosqlite 连接）。"""
        if self._checkpointer is None and self._db_path:
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            conn = await aiosqlite.connect(self._db_path)
            self._checkpointer = AsyncSqliteSaver(conn)
        return self._checkpointer

    def _refresh_llm_if_needed(self):
        """Re-create LLM if config.json settings have changed since last init."""
        config = load_config()
        llm_config = config.get("llm", {})
        model = llm_config.get("model") or os.getenv("DASHSCOPE_MODEL", "qwen3.5-plus")
        api_key = llm_config.get("api_key") or os.getenv("DASHSCOPE_API_KEY", "")
        api_base = llm_config.get("base_url") or os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        temperature = llm_config.get("temperature", 0.7)

        config_sig = f"{model}|{api_key}|{api_base}|{temperature}"
        if getattr(self, "_config_sig", None) != config_sig:
            from langchain_openai import ChatOpenAI
            self._llm = ChatOpenAI(
                model=model, api_key=api_key, base_url=api_base,
                temperature=temperature, streaming=True,
            )
            self._config_sig = config_sig

    def _build_agent(self):
        """Build a fresh agent with current system prompt (re-reads files each time)."""
        from langchain.agents import create_agent

        assert self._base_dir is not None

        # Hot-reload LLM config if settings changed
        self._refresh_llm_if_needed()
        assert self._llm is not None

        # Zone 1+2 稳定前缀（Zone 3 动态记忆由 astream/ainvoke 注入）
        from graph.skill_registry import SkillRegistry
        # 缓存 SkillRegistry 实例，避免每次请求重新扫描磁盘
        if self._skill_registry is None:
            self._skill_registry = SkillRegistry.discover(self._base_dir / "skills")
        system_prompt = build_stable_prefix(self._base_dir, skill_registry=self._skill_registry)

        # 构建中间件链：截断 → 摘要 → 工具限流
        middleware = self._build_middleware()

        # TaskState 通过 state_schema 嵌入，与 middleware 同时使用
        from graph.task_state import AgentCustomState

        agent = create_agent(
            model=self._llm,
            tools=self._tools,
            system_prompt=system_prompt,
            middleware=middleware,
            state_schema=AgentCustomState,
            checkpointer=self._checkpointer,
        )
        return agent

    def _build_middleware(self) -> list:
        """构建四层中间件链：截断 → 摘要 → 工具过滤 → 限流。

        每层通过 config.json 的 middleware 配置段独立开关。
        """
        from config import get_middleware_config
        from graph.middleware import ToolOutputBudgetMiddleware, ContextAwareToolFilter
        from langchain.agents.middleware import SummarizationMiddleware, ToolCallLimitMiddleware

        mw_cfg = get_middleware_config()
        middleware = []

        # 第 1 层：前置截断超大工具输出
        if mw_cfg.get("tool_output_budget", {}).get("enabled", True):
            budgets = mw_cfg.get("tool_output_budget", {}).get("budgets")
            middleware.append(ToolOutputBudgetMiddleware(budgets=budgets))

        # 第 2 层：自动摘要（使用轻量模型）
        if mw_cfg.get("summarization", {}).get("enabled", True):
            summary_llm = self._create_summary_llm()
            if summary_llm:
                sum_cfg = mw_cfg.get("summarization", {})
                middleware.append(
                    SummarizationMiddleware(
                        model=summary_llm,
                        trigger=("tokens", sum_cfg.get("trigger_tokens", 8000)),
                        keep=("messages", sum_cfg.get("keep_messages", 10)),
                    )
                )

        # 第 3 层：运行时工具过滤
        if mw_cfg.get("tool_filter", {}).get("enabled", True):
            middleware.append(ContextAwareToolFilter())

        # 第 4 层：工具调用限流
        if mw_cfg.get("tool_call_limit", {}).get("enabled", True):
            limits = mw_cfg.get("tool_call_limit", {}).get("limits", {})
            limit_items = []
            for tool_name, run_limit in limits.items():
                limit_items.append(ToolCallLimitMiddleware(tool_name=tool_name, run_limit=run_limit))
            if not limit_items:
                # 默认值
                limit_items = [
                    ToolCallLimitMiddleware(tool_name="terminal", run_limit=10),
                    ToolCallLimitMiddleware(tool_name="python_repl", run_limit=5),
                ]
            middleware.extend(limit_items)

        return middleware

    def _create_summary_llm(self):
        """创建用于摘要的轻量 LLM 实例。

        预检查 API key 是否可用，避免懒初始化延迟报错。
        """
        from langchain_openai import ChatOpenAI

        config = load_config()
        summary_cfg = config.get("summary_model", {})
        model = summary_cfg.get("model", "qwen-turbo")

        # 复用主模型的 API 配置
        llm_config = config.get("llm", {})
        api_key = llm_config.get("api_key") or os.getenv("DASHSCOPE_API_KEY", "")
        api_base = llm_config.get("base_url") or os.getenv(
            "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        temperature = summary_cfg.get("temperature", 0)

        # 预检查：无 API key 时跳过摘要模型创建
        if not api_key:
            print("⚠️ 摘要模型跳过：未配置 API key，将跳过自动摘要")
            return None

        try:
            return ChatOpenAI(
                model=model,
                api_key=api_key,
                base_url=api_base,
                temperature=temperature,
            )
        except Exception as e:
            print(f"⚠️ 摘要模型创建失败（将跳过自动摘要）: {e}")
            return None

    async def _summarize_goal(self, message: str) -> str:
        """使用轻量 LLM 将用户消息总结为简洁的任务目标。

        降级策略：LLM 不可用或调用失败时，回退到 message[:200] 截断。
        """
        summary_llm = self._create_summary_llm()
        if summary_llm is None:
            return message[:200]

        try:
            prompt = (
                "将以下用户消息总结为一个简洁的任务目标描述（不超过50字，"
                "只保留核心意图，省略细节和代码片段）：\n\n"
                f"{message[:1000]}"
            )
            result = await summary_llm.ainvoke(prompt)
            goal = result.content.strip() if hasattr(result, "content") else str(result).strip()
            return goal[:200] if goal else message[:200]
        except Exception as e:
            print(f"⚠️ 任务目标摘要失败，回退到截断: {e}")
            return message[:200]

    async def _read_task_state(self, agent, config: dict) -> dict | None:
        """从 checkpoint 恢复已有 TaskState。

        Returns:
            TaskState 字典，无 checkpoint 或无 task_state 时返回 None。
        """
        try:
            snapshot = await agent.aget_state(config)
            if snapshot and snapshot.values:
                task = snapshot.values.get("task_state")
                if task and isinstance(task, dict) and task.get("goal"):
                    return task
        except Exception:
            # 无 checkpoint 或读取失败 → 首次请求，正常返回 None
            pass
        return None

    async def _write_task_state(self, agent, config: dict, task_state: dict) -> None:
        """将 TaskState 写入 AgentCustomState，触发 checkpointer 持久化。"""
        try:
            await agent.aupdate_state(config, {"task_state": task_state}, as_node="model")
        except Exception as e:
            print(f"⚠️ TaskState 写入 checkpoint 失败: {e}")

    # 安全上限：防止极端情况下历史消息爆炸。
    # 上下文管理已由 SummarizationMiddleware 接管，此值仅作为兜底保护。
    MAX_HISTORY_MESSAGES = 50

    # Tool-calling reminder injected as a user message when conversation is long.
    # Using HumanMessage (not AIMessage) so the model treats it as an instruction,
    # not as something it previously said.
    TOOL_REMINDER = (
        "[系统提醒] 请记住：你必须使用工具来完成任务。"
        "需要读取文件时调用 read_file，需要执行命令时调用 terminal，"
        "需要写入文件时调用 write_file。"
        "禁止在文本中描述操作而不实际调用工具。"
    )

    def _build_messages(self, user_message: str, history: list[dict[str, Any]]) -> list:
        """Convert session history + new message into LangChain messages.

        SummarizationMiddleware 在模型调用前自动管理上下文长度，
        此方法仅做消息格式转换、压缩上下文首条保护和安全上限兜底。
        """
        # 安全上限兜底
        truncated = list(history)
        if len(truncated) > self.MAX_HISTORY_MESSAGES:
            # Keep compressed context (first message if it exists) + recent messages
            first = truncated[0]
            if COMPRESSED_CONTEXT_PREFIX in first.get("content", ""):
                truncated = [first] + truncated[-(self.MAX_HISTORY_MESSAGES - 1):]
            else:
                truncated = truncated[-self.MAX_HISTORY_MESSAGES:]

        # Ensure truncated history doesn't start with assistant (except summary).
        # Drop leading assistant messages to maintain proper conversation structure.
        while (
            truncated
            and truncated[0].get("role") == "assistant"
            and COMPRESSED_CONTEXT_PREFIX not in truncated[0].get("content", "")
        ):
            truncated = truncated[1:]

        messages = []
        for msg in truncated:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))

        # Inject tool reminder as HumanMessage when conversation is long (>= 6 rounds).
        # Paired with an AIMessage acknowledgment to maintain proper alternation.
        if len(history) >= 12:
            messages.append(HumanMessage(content=self.TOOL_REMINDER))
            messages.append(AIMessage(content="明白，我会使用工具来执行操作。"))

        messages.append(HumanMessage(content=user_message))
        return messages

    async def astream(
        self, message: str, history: list[dict[str, Any]], session_id: str = "default"
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream agent response with token-level and node-level events.

        Yields events:
          {"type": "retrieval", "query": "...", "results": [...]}  (RAG mode only)
          {"type": "token", "content": "..."}
          {"type": "tool_start", "tool": "...", "input": "..."}
          {"type": "tool_end", "tool": "...", "output": "..."}
          {"type": "done", "content": "..."}
        """
        # 确保 checkpointer 已初始化（懒加载）
        await self._ensure_checkpointer()

        # 统一记忆检索：不再依赖 rag_mode 开关
        # Phase 7 移除 MEMORY.md 全文注入后，非 RAG 模式也需要记忆检索
        rag_context = ""
        features = get_features_config()
        if self._base_dir and features.get("unified_memory", True):
            from graph.unified_memory import get_unified_retriever

            retriever = get_unified_retriever(self._base_dir)
            results = await retriever.retrieve_async(message)
            if results:
                yield {
                    "type": "retrieval",
                    "query": message,
                    "results": results,
                }
                rag_context = retriever.format_for_injection(results)

        # 任务状态管理：恢复已有 TaskState 或创建新的
        task_state_dict: dict | None = None
        task_state_md = ""
        if self._base_dir and features.get("task_state", True):
            from graph.task_state import is_task_message, create_task_state, format_task_state

            # 预构建 agent 以便读取 checkpoint（复用下方 _build_agent 结果）
            # 注意：这里先构建 agent 读取状态，下方不再重复构建
            agent = self._build_agent()
            thread_config = {"configurable": {"thread_id": session_id}}

            # 从 checkpoint 恢复已有 TaskState
            existing_task = await self._read_task_state(agent, thread_config)

            if existing_task:
                # 已有活跃 TaskState
                if is_task_message(message):
                    # 新任务性消息 → 追加步骤（Task 2.3）
                    goal = await self._summarize_goal(message)
                    existing_task["steps"].append({
                        "description": goal,
                        "status": "in_progress",
                    })
                task_state_dict = existing_task
                task_state_md = format_task_state(existing_task)
            elif is_task_message(message):
                # 无活跃 TaskState + 任务性消息 → 创建新的（Task 2.1）
                goal = await self._summarize_goal(message)
                task_state_dict = create_task_state(
                    session_id=session_id,
                    goal=goal,
                )
                task_state_md = format_task_state(task_state_dict)

            # 将 TaskState 写入 AgentCustomState（Task 2.1）
            if task_state_dict is not None:
                await self._write_task_state(agent, thread_config, task_state_dict)
        else:
            agent = self._build_agent()
            thread_config = {"configurable": {"thread_id": session_id}}

        # Build messages from history (no longer augment with RAG as assistant message)
        messages = self._build_messages(message, history)

        # Zone 3: 动态内容注入为 SystemMessage，位于当前用户消息之前
        has_active_steps = _has_in_progress_steps(task_state_dict)
        dynamic_prefix = build_dynamic_prefix(
            memory_context=rag_context,
            task_state=task_state_md,
            has_active_steps=has_active_steps,
        )
        if dynamic_prefix:
            messages.insert(len(messages) - 1, SystemMessage(content=dynamic_prefix))

        full_response = ""
        tools_just_finished = False

        async for event in agent.astream(
            {"messages": messages},
            config=thread_config,
            stream_mode=["messages", "updates"],
        ):
            # event is a tuple of (stream_mode, data) when using multiple modes
            if isinstance(event, tuple):
                mode, data = event
            else:
                mode = "messages"
                data = event

            if mode == "messages":
                # Token-level streaming from LLM
                msg, metadata = data
                if hasattr(msg, "content") and msg.content:
                    if msg.type == "AIMessageChunk" or msg.type == "ai":
                        if msg.content and not getattr(msg, "tool_calls", None):
                            # If tools just finished, signal a new response segment
                            if tools_just_finished:
                                yield {"type": "new_response"}
                                tools_just_finished = False
                            full_response += msg.content
                            yield {"type": "token", "content": msg.content}

            elif mode == "updates":
                if isinstance(data, dict):
                    for node_name, node_data in data.items():
                        if node_name == "tools" and "messages" in node_data:
                            for tool_msg in node_data["messages"]:
                                if hasattr(tool_msg, "name"):
                                    yield {
                                        "type": "tool_end",
                                        "tool": tool_msg.name,
                                        "output": str(tool_msg.content)[:2000],
                                    }
                            # After all tool results, mark that tools finished
                            tools_just_finished = True
                        elif node_name == "model" and "messages" in node_data:
                            for agent_msg in node_data["messages"]:
                                if hasattr(agent_msg, "tool_calls") and agent_msg.tool_calls:
                                    for tc in agent_msg.tool_calls:
                                        yield {
                                            "type": "tool_start",
                                            "tool": tc["name"],
                                            "input": str(tc.get("args", ""))[:1000],
                                        }

        yield {"type": "done", "content": full_response}

        # 智能截流：将对话追加到缓冲区，由缓冲区判断是否触发 mem0 写入
        # 关键优化：mem0 的 add() 会调用 LLM 做事实提取（约 20-90 秒），
        # 因此将实际的 mem0 写入放到后台线程，不阻塞 SSE 响应流。
        if self._base_dir and full_response:
            mem0_cfg = get_mem0_config()
            if mem0_cfg.get("enabled") and mem0_cfg.get("auto_extract"):
                self._schedule_mem0_write(message, full_response, mem0_cfg)

    def _schedule_mem0_write(
        self, user_message: str, assistant_message: str, mem0_cfg: dict
    ) -> None:
        """在后台线程中执行 mem0 缓冲写入，不阻塞聊天响应。"""
        base_dir = self._base_dir
        assert base_dir is not None

        def _background_write() -> None:
            try:
                from graph.memory_buffer import get_memory_buffer
                from graph.mem0_manager import get_mem0_manager

                buffer = get_memory_buffer(base_dir)
                buffer.add_turn(user_message, assistant_message, "default")

                # 检查立即触发（显式指令/强烈纠正）
                should_flush = buffer.check_immediate_trigger(user_message)
                # 检查轮次/时间触发
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

    async def ainvoke(self, message: str, session_id: str) -> str:
        """Non-streaming invocation (fallback)."""
        # 确保 checkpointer 已初始化（懒加载）
        await self._ensure_checkpointer()

        history = session_manager.load_session_for_agent(session_id)

        # 统一记忆检索（与 astream 保持一致）
        rag_context = ""
        features = get_features_config()
        if self._base_dir and features.get("unified_memory", True):
            from graph.unified_memory import get_unified_retriever

            retriever = get_unified_retriever(self._base_dir)
            results = await retriever.retrieve_async(message)
            if results:
                rag_context = retriever.format_for_injection(results)

        # 任务状态管理（与 astream 保持一致）
        task_state_dict: dict | None = None
        task_state_md = ""
        if self._base_dir and features.get("task_state", True):
            from graph.task_state import is_task_message, create_task_state, format_task_state

            agent = self._build_agent()
            thread_config = {"configurable": {"thread_id": session_id}}

            existing_task = await self._read_task_state(agent, thread_config)

            if existing_task:
                if is_task_message(message):
                    goal = await self._summarize_goal(message)
                    existing_task["steps"].append({
                        "description": goal,
                        "status": "in_progress",
                    })
                task_state_dict = existing_task
                task_state_md = format_task_state(existing_task)
            elif is_task_message(message):
                goal = await self._summarize_goal(message)
                task_state_dict = create_task_state(session_id=session_id, goal=goal)
                task_state_md = format_task_state(task_state_dict)

            if task_state_dict is not None:
                await self._write_task_state(agent, thread_config, task_state_dict)
        else:
            agent = self._build_agent()
            thread_config = {"configurable": {"thread_id": session_id}}

        messages = self._build_messages(message, history)

        # Zone 3: 动态内容注入为 SystemMessage
        has_active_steps = _has_in_progress_steps(task_state_dict)
        dynamic_prefix = build_dynamic_prefix(
            memory_context=rag_context,
            task_state=task_state_md,
            has_active_steps=has_active_steps,
        )
        if dynamic_prefix:
            messages.insert(len(messages) - 1, SystemMessage(content=dynamic_prefix))

        result = await agent.ainvoke(
            {"messages": messages},
            config=thread_config,
        )

        final_messages = result.get("messages", [])
        for msg in reversed(final_messages):
            if hasattr(msg, "content") and msg.type == "ai" and msg.content:
                response = msg.content
                session_manager.save_message(session_id, "user", message)
                session_manager.save_message(session_id, "assistant", response)
                return response
        return "No response generated."


def _has_in_progress_steps(task_state: dict | None) -> bool:
    """判断 TaskState 中是否有 in_progress 步骤，用于控制 update_task 指引注入。"""
    if task_state is None:
        return False
    steps = task_state.get("steps", [])
    return any(s.get("status") == "in_progress" for s in steps)


agent_manager = AgentManager()
