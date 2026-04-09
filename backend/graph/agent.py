"""AgentManager — Core Agent using LangChain create_agent API with DashScope Qwen."""

import os
import threading
from pathlib import Path
from typing import Any, AsyncGenerator

from langchain_core.messages import HumanMessage, AIMessage

from config import get_rag_mode, load_config, get_mem0_config
from graph.prompt_builder import build_system_prompt
from graph.session_manager import session_manager, COMPRESSED_CONTEXT_PREFIX
from tools import get_all_tools


class AgentManager:
    """Manages the Agent lifecycle: initialization, streaming, invocation."""

    def __init__(self) -> None:
        self._base_dir: Path | None = None
        self._tools: list = []
        self._llm = None

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
        print(f"🤖 Agent initialized with {len(self._tools)} tools (model: {model})")

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

        rag_mode = get_rag_mode()
        system_prompt = build_system_prompt(self._base_dir, rag_mode=rag_mode)

        agent = create_agent(
            model=self._llm,
            tools=self._tools,
            system_prompt=system_prompt,
        )
        return agent

    # Maximum number of history messages to send to the agent.
    # Prevents context overflow which causes DeepSeek to stop calling tools.
    MAX_HISTORY_MESSAGES = 20

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

        Truncates history to MAX_HISTORY_MESSAGES to prevent context overflow.
        Injects a tool-calling reminder when conversation history is long,
        placed close to the user message where the model pays most attention.
        """
        # Truncate history to prevent context overflow
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
        self, message: str, history: list[dict[str, Any]]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream agent response with token-level and node-level events.

        Yields events:
          {"type": "retrieval", "query": "...", "results": [...]}  (RAG mode only)
          {"type": "token", "content": "..."}
          {"type": "tool_start", "tool": "...", "input": "..."}
          {"type": "tool_end", "tool": "...", "output": "..."}
          {"type": "done", "content": "..."}
        """
        # RAG retrieval: inject memory context if enabled
        # 通过统一检索接口获取记忆（支持 legacy/mem0/hybrid 三种模式）
        rag_mode = get_rag_mode()
        rag_context = ""
        if rag_mode and self._base_dir:
            from graph.memory_retriever import get_retriever

            retriever = get_retriever(self._base_dir)
            if retriever:
                results = retriever.retrieve(message)
                if results:
                    yield {
                        "type": "retrieval",
                        "query": message,
                        "results": results,
                    }
                    rag_context = retriever.format_context(results)

        agent = self._build_agent()

        # Build messages with optional RAG context appended to history
        augmented_history = list(history)
        if rag_context:
            augmented_history.append(
                {"role": "assistant", "content": rag_context}
            )
        messages = self._build_messages(message, augmented_history)

        full_response = ""
        tools_just_finished = False

        async for event in agent.astream(
            {"messages": messages},
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

        thread = threading.Thread(target=_background_write, daemon=True)
        thread.start()

    async def ainvoke(self, message: str, session_id: str) -> str:
        """Non-streaming invocation (fallback)."""
        history = session_manager.load_session_for_agent(session_id)
        agent = self._build_agent()
        messages = self._build_messages(message, history)
        result = await agent.ainvoke({"messages": messages})

        final_messages = result.get("messages", [])
        for msg in reversed(final_messages):
            if hasattr(msg, "content") and msg.type == "ai" and msg.content:
                response = msg.content
                session_manager.save_message(session_id, "user", message)
                session_manager.save_message(session_id, "assistant", response)
                return response
        return "No response generated."


agent_manager = AgentManager()
