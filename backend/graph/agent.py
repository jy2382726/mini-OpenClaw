"""AgentManager — Core Agent using LangChain create_agent API with DashScope Qwen."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, AsyncGenerator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from config import load_config, get_mem0_config, get_features_config, create_auxiliary_llm
from graph.prompt_builder import build_stable_prefix, build_dynamic_prefix
from graph.session_manager import session_manager
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
        self._session_repo = None  # SessionRepository（与 checkpointer 共享连接）
        self._db_path: str | None = None  # SQLite 数据库路径（懒加载）
        self._summarize_locks: dict[str, asyncio.Lock] = {}  # 按 session_id 粒度的摘要并发锁

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
        """懒加载 AsyncSqliteSaver（需要异步创建 aiosqlite 连接）。

        同时初始化 sessions 元数据表，供 SessionRepository 使用。
        """
        if self._checkpointer is None and self._db_path:
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from graph.session_repository import SessionRepository

            conn = await aiosqlite.connect(self._db_path)
            self._checkpointer = AsyncSqliteSaver(conn)
            # 初始化 sessions 元数据表
            self._session_repo = SessionRepository(conn)
            await self._session_repo.initialize()
        return self._checkpointer

    async def get_session_repo(self):
        """获取 SessionRepository 实例（确保 checkpointer 已初始化）。"""
        await self._ensure_checkpointer()
        return self._session_repo

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
        所有阈值基于 context_window 比例计算，切换模型后自动适应。
        """
        from config import get_middleware_config, get_context_window
        from graph.middleware import ToolOutputBudgetMiddleware, ContextAwareToolFilter
        from langchain.agents.middleware import SummarizationMiddleware, ToolCallLimitMiddleware

        mw_cfg = get_middleware_config()
        context_window = get_context_window()
        middleware = []

        # 第 1 层：渐进式工具输出压缩
        if mw_cfg.get("tool_output_budget", {}).get("enabled", True):
            tob_cfg = mw_cfg.get("tool_output_budget", {})
            budgets = tob_cfg.get("budgets")
            middleware.append(ToolOutputBudgetMiddleware(
                budgets=budgets,
                context_window=context_window,
                safe_ratio=tob_cfg.get("safe_ratio", 0.25),
                pressure_ratio=tob_cfg.get("pressure_ratio", 0.45),
                base_dir=self._base_dir / "sessions" if self._base_dir else None,
            ))

        # 第 2 层：自动摘要（使用轻量模型）
        if mw_cfg.get("summarization", {}).get("enabled", True):
            summary_llm = self._create_summary_llm()
            if summary_llm:
                sum_cfg = mw_cfg.get("summarization", {})
                # trigger_tokens 联动上下文窗口比例（默认 60%）
                trigger_tokens = int(context_window * 0.6)
                middleware.append(
                    SummarizationMiddleware(
                        model=summary_llm,
                        trigger=("tokens", trigger_tokens),
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
        """创建用于摘要的辅助 LLM 实例。

        委托给统一的 create_auxiliary_llm() 工厂函数。
        """
        return create_auxiliary_llm()

    def _get_summarize_lock(self, session_id: str) -> asyncio.Lock:
        """获取指定会话的摘要并发锁（按 session_id 粒度）。"""
        if session_id not in self._summarize_locks:
            self._summarize_locks[session_id] = asyncio.Lock()
        return self._summarize_locks[session_id]

    async def summarize_checkpoint(
        self, session_id: str, keep_count: int = 10
    ) -> dict[str, Any]:
        """从 checkpoint 读取消息，对早期消息生成结构化摘要并写回。

        Args:
            session_id: 会话 ID
            keep_count: 保留最近 N 条消息不被摘要（默认 10）

        Returns:
            {"summarized": bool, "summarized_count": int, "preserved_count": int}

        Raises:
            ValueError: checkpoint 不存在或消息数不足
            RuntimeError: 辅助 LLM 不可用
            asyncio.TimeoutError: 并发冲突（已在摘要中）
        """
        # Task 1.4: 并发安全锁
        lock = self._get_summarize_lock(session_id)
        if lock.locked():
            raise asyncio.TimeoutError("该会话正在摘要中，请稍后再试")

        async with lock:
            await self._ensure_checkpointer()
            agent = self._build_agent()
            config = {"configurable": {"thread_id": session_id}}

            # 1. 从 checkpoint 读取消息
            snapshot = await agent.aget_state(config)
            if not snapshot or not snapshot.values:
                raise ValueError("该会话无可用消息（checkpoint 不存在）")

            messages = snapshot.values.get("messages", [])
            if not messages:
                raise ValueError("该会话无可用消息")

            # 2. 判断是否需要摘要
            if len(messages) <= keep_count:
                return {
                    "summarized": False,
                    "reason": "消息数不足，无需摘要",
                    "summarized_count": 0,
                    "preserved_count": len(messages),
                }

            # 3. 切分消息，AI/Tool 配对保护
            split_idx = len(messages) - keep_count
            split_idx = self._protect_ai_tool_pairs(messages, split_idx)

            to_summarize = messages[:split_idx]
            preserved = messages[split_idx:]

            # 4. 调用辅助 LLM 生成摘要
            summary_llm = self._create_summary_llm()
            if summary_llm is None:
                raise RuntimeError("辅助模型未配置，无法生成摘要")

            summary_text = await self._generate_checkpoint_summary(summary_llm, to_summarize)

            # 5. 构造新消息列表
            summary_message = HumanMessage(
                content=f"Here is a summary of the conversation to date:\n\n{summary_text}",
                additional_kwargs={"lc_source": "summarization"},
            )
            new_messages = [summary_message] + list(preserved)

            # 6. 写回 checkpoint
            await agent.aupdate_state(config, {"messages": new_messages}, as_node="model")

            return {
                "summarized": True,
                "summarized_count": len(to_summarize),
                "preserved_count": len(preserved),
            }

    def _protect_ai_tool_pairs(self, messages: list, split_idx: int) -> int:
        """AI/Tool 消息配对保护：确保切割点不切断 AIMessage-ToolMessage 配对。

        如果 split_idx 落在 ToolMessage 上，向前查找到包含对应 tool_calls
        的 AIMessage，将整个配对纳入摘要范围。
        """
        if split_idx <= 0 or split_idx >= len(messages):
            return split_idx

        msg_at_split = messages[split_idx]

        # 如果切割点落在 ToolMessage 上，向前查找对应 AIMessage
        if isinstance(msg_at_split, ToolMessage):
            tool_id = getattr(msg_at_split, "tool_call_id", None)
            if tool_id:
                for i in range(split_idx - 1, -1, -1):
                    candidate = messages[i]
                    if isinstance(candidate, AIMessage):
                        tool_calls = getattr(candidate, "tool_calls", [])
                        if any(tc.get("id") == tool_id for tc in tool_calls):
                            # 将 AIMessage 及其所有 ToolMessage 纳入摘要
                            split_idx = i
                            break

        return split_idx

    async def _generate_checkpoint_summary(self, llm, messages: list) -> str:
        """使用辅助 LLM 和 DEFAULT_SUMMARY_PROMPT 生成结构化摘要。"""
        from langchain.agents.middleware.summarization import DEFAULT_SUMMARY_PROMPT
        from langchain_core.messages import get_buffer_string

        formatted = get_buffer_string(messages)
        prompt = DEFAULT_SUMMARY_PROMPT.format(messages=formatted).rstrip()

        result = await llm.ainvoke([HumanMessage(content=prompt)])
        return result.content.strip() if hasattr(result, "content") else str(result).strip()

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

    async def astream(
        self, message: str, history: list[dict[str, Any]] | None = None, session_id: str = "default"
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
        should_push_task_update = False  # 是否需要在 SSE 流中推送初始 task_update
        if self._base_dir and features.get("task_state", True):
            from graph.task_state import is_task_message, create_task_state, format_task_state

            # 预构建 agent 以便读取 checkpoint（复用下方 _build_agent 结果）
            # 注意：这里先构建 agent 读取状态，下方不再重复构建
            agent = self._build_agent()
            thread_config = {"configurable": {"thread_id": session_id}}

            # 从 checkpoint 恢复已有 TaskState
            existing_task = await self._read_task_state(agent, thread_config)

            # 判断已有任务是否有活跃步骤（in_progress/pending/blocked）
            # 无步骤或全部 completed → 视为"无活跃任务"，可创建新任务
            existing_has_active = False
            if existing_task:
                steps = existing_task.get("steps", [])
                existing_has_active = any(
                    s.get("status") in ("in_progress", "pending", "blocked")
                    for s in steps
                )

            if existing_task and existing_has_active:
                # 已有活跃 TaskState
                if is_task_message(message):
                    # 新任务性消息 → 追加步骤（Task 2.3）
                    goal = await self._summarize_goal(message)
                    existing_task["steps"].append({
                        "description": goal,
                        "status": "in_progress",
                    })
                    should_push_task_update = True  # 步骤追加时推送
                task_state_dict = existing_task
                task_state_md = format_task_state(existing_task)
            elif is_task_message(message):
                # 无活跃 TaskState + 任务性消息 → 创建新的（Task 2.1）
                goal = await self._summarize_goal(message)
                task_state_dict = create_task_state(
                    session_id=session_id,
                    goal=goal,
                )
                # 自动添加初始步骤（与已有任务追加步骤逻辑一致）
                task_state_dict["steps"].append({
                    "description": goal,
                    "status": "in_progress",
                })
                task_state_md = format_task_state(task_state_dict)
                should_push_task_update = True  # 首次创建时推送

            # 将 TaskState 写入 AgentCustomState（Task 2.1）
            if task_state_dict is not None:
                await self._write_task_state(agent, thread_config, task_state_dict)

            # 推送初始 task_update 事件（任务创建或步骤追加）
            if should_push_task_update and task_state_dict is not None:
                yield {"type": "task_update", "task_state": task_state_dict}
        else:
            agent = self._build_agent()
            thread_config = {"configurable": {"thread_id": session_id}}

        # Build messages: checkpoint 自动恢复历史，仅传当前 user message
        messages = [HumanMessage(content=message)]

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
                        # TaskState 变更检测：推送 task_update SSE 事件
                        if isinstance(node_data, dict) and node_data.get("task_state") is not None:
                            yield {
                                "type": "task_update",
                                "task_state": node_data["task_state"],
                            }

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

        # 流结束时自动完成活跃任务步骤
        if task_state_dict is not None:
            has_active = any(
                s.get("status") == "in_progress"
                for s in task_state_dict.get("steps", [])
            )
            if has_active:
                for step in task_state_dict.get("steps", []):
                    if step.get("status") == "in_progress":
                        step["status"] = "completed"
                # 写回 checkpoint 并推送最终状态
                await self._write_task_state(agent, thread_config, task_state_dict)
                yield {"type": "task_update", "task_state": task_state_dict}

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

        features = get_features_config()

        # 统一记忆检索（与 astream 保持一致）
        rag_context = ""
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

            # 判断已有任务是否有活跃步骤
            existing_has_active = False
            if existing_task:
                steps = existing_task.get("steps", [])
                existing_has_active = any(
                    s.get("status") in ("in_progress", "pending", "blocked")
                    for s in steps
                )

            if existing_task and existing_has_active:
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
                task_state_dict["steps"].append({
                    "description": goal,
                    "status": "in_progress",
                })
                task_state_md = format_task_state(task_state_dict)

            if task_state_dict is not None:
                await self._write_task_state(agent, thread_config, task_state_dict)
        else:
            agent = self._build_agent()
            thread_config = {"configurable": {"thread_id": session_id}}

        # checkpoint 自动恢复历史，仅传当前 user message
        messages = [HumanMessage(content=message)]

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
                return msg.content
        return "No response generated."


def _has_in_progress_steps(task_state: dict | None) -> bool:
    """判断 TaskState 中是否有 in_progress 步骤，用于控制 update_task 指引注入。"""
    if task_state is None:
        return False
    steps = task_state.get("steps", [])
    return any(s.get("status") == "in_progress" for s in steps)


agent_manager = AgentManager()
