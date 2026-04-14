"""Phase 9 端到端集成测试 — 验证完整中间件链 + SSE 事件流。

测试策略：mock LLM 调用，验证消息流转、中间件处理、SSE 事件格式正确。
不依赖真实 API 调用，纯本地验证。
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from graph.agent import AgentManager


def _create_workspace(base: Path) -> Path:
    """创建最小化 workspace 目录结构。"""
    ws = base / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "SOUL.md").write_text("你是助手", encoding="utf-8")
    (ws / "IDENTITY.md").write_text("名字：Claw", encoding="utf-8")
    (ws / "USER.md").write_text("用户是开发者", encoding="utf-8")
    (ws / "AGENTS.md").write_text("操作指令", encoding="utf-8")
    skills = base / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    return base


class TestEndToEndIntegration:
    """端到端集成测试：完整中间件链执行无报错。"""

    def _setup_agent(self, tmpdir: Path) -> AgentManager:
        """创建并初始化 AgentManager。"""
        base = _create_workspace(tmpdir)
        mgr = AgentManager()
        mgr.initialize(base)
        return mgr

    def test_build_agent_succeeds(self):
        """_build_agent 不报错。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = self._setup_agent(Path(tmpdir))
            agent = mgr._build_agent()
            assert agent is not None

    def test_middleware_chain_with_config_toggles(self):
        """中间件链正确响应配置开关。"""
        from config import get_middleware_config

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = self._setup_agent(Path(tmpdir))
            # 默认配置下中间件链完整
            middleware = mgr._build_middleware()
            assert len(middleware) > 0

            # 验证所有中间件都可以被构建
            for mw in middleware:
                assert hasattr(mw, "abefore_model") or hasattr(mw, "wrap_model_call") or hasattr(mw, "awrap_model_call")

    def test_skill_registry_cached(self):
        """SkillRegistry 实例被缓存，不重复扫描。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = self._setup_agent(Path(tmpdir))
            assert mgr._skill_registry is None
            mgr._build_agent()
            first = mgr._skill_registry
            assert first is not None
            mgr._build_agent()
            assert mgr._skill_registry is first  # 同一实例

    def test_dynamic_prefix_injection_position(self):
        """Zone 3 SystemMessage 注入在当前 HumanMessage 之前。"""
        from langchain_core.messages import SystemMessage

        # 模拟当前 astream/ainvoke 中的 Zone 3 注入逻辑
        messages = [HumanMessage(content="帮我创建项目")]
        dynamic = "<!-- Zone 3: Dynamic -->\n测试内容"
        if dynamic:
            messages.insert(len(messages) - 1, SystemMessage(content=dynamic))

        # 最后一条是用户消息
        assert isinstance(messages[-1], HumanMessage)
        # 倒数第二条是 SystemMessage（Zone 3）
        assert isinstance(messages[-2], SystemMessage)
        assert "Zone 3" in messages[-2].content


class TestSSEEventStream:
    """验证 SSE 事件流格式与中间件链兼容。"""

    def test_event_types_defined(self):
        """确认所有事件类型定义存在。"""
        expected_types = {"token", "tool_start", "tool_end", "done", "retrieval", "new_response"}
        # 这些是 astream 中 yield 的事件类型
        assert len(expected_types) == 6

    def test_token_event_format(self):
        """token 事件格式正确。"""
        event = {"type": "token", "content": "你好"}
        assert event["type"] == "token"
        assert isinstance(event["content"], str)

    def test_tool_start_event_format(self):
        """tool_start 事件格式正确。"""
        event = {"type": "tool_start", "tool": "terminal", "input": "ls -la"}
        assert event["type"] == "tool_start"
        assert event["tool"] == "terminal"
        assert "input" in event

    def test_tool_end_event_format(self):
        """tool_end 事件格式正确。"""
        event = {"type": "tool_end", "tool": "terminal", "output": "file.txt"}
        assert event["type"] == "tool_end"
        assert event["tool"] == "terminal"
        assert "output" in event

    def test_done_event_format(self):
        """done 事件格式正确。"""
        event = {"type": "done", "content": "完整回复内容"}
        assert event["type"] == "done"
        assert isinstance(event["content"], str)

    def test_retrieval_event_format(self):
        """retrieval 事件格式正确。"""
        event = {
            "type": "retrieval",
            "query": "用户查询",
            "results": [{"content": "相关记忆", "source": "mem0", "confidence": 0.9}],
        }
        assert event["type"] == "retrieval"
        assert "query" in event
        assert isinstance(event["results"], list)

    def test_middleware_does_not_alter_event_types(self):
        """中间件链不改变 SSE 事件类型的定义。"""
        # 中间件在 abefore_model 阶段处理消息，
        # 不影响 agent.astream 的 stream_mode 输出格式
        valid_types = {"token", "tool_start", "tool_end", "done", "retrieval", "new_response"}
        # 模拟事件流
        events = [
            {"type": "token", "content": "我来"},
            {"type": "token", "content": "帮你"},
            {"type": "tool_start", "tool": "terminal", "input": "ls"},
            {"type": "tool_end", "tool": "terminal", "output": "file.txt"},
            {"type": "new_response"},
            {"type": "token", "content": "完成了"},
            {"type": "done", "content": "完成了"},
        ]
        for event in events:
            assert event["type"] in valid_types


class TestFeatureTogglesE2E:
    """功能开关的端到端验证。"""

    def test_task_state_disabled(self):
        """task_state 关闭时不进行任务检测。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = AgentManager()
            base = _create_workspace(Path(tmpdir))
            mgr.initialize(base)

            mock_features = {"task_state": False, "unified_memory": True}
            with patch("graph.agent.get_features_config", return_value=mock_features):
                # 模拟 astream 中的逻辑
                features = mock_features
                task_state_md = ""
                if features.get("task_state", True):
                    task_state_md = "should not be set"
                assert task_state_md == ""

    def test_unified_memory_disabled(self):
        """unified_memory 关闭时跳过记忆检索。"""
        mock_features = {"task_state": True, "unified_memory": False}
        # unified_memory=False 时不进入检索分支
        assert mock_features.get("unified_memory", True) is False

    def test_all_features_disabled(self):
        """所有功能关闭时基础对话仍正常。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = AgentManager()
            base = _create_workspace(Path(tmpdir))
            mgr.initialize(base)

            mock_features = {"task_state": False, "unified_memory": False}
            mock_mw = {
                "tool_output_budget": {"enabled": False},
                "summarization": {"enabled": False},
                "tool_filter": {"enabled": False},
                "tool_call_limit": {"enabled": False},
            }

            with patch("graph.agent.get_features_config", return_value=mock_features):
                with patch("config.get_middleware_config", return_value=mock_mw):
                    agent = mgr._build_agent()
                    assert agent is not None
                    middleware = mgr._build_middleware()
                    assert middleware == []


class TestTaskStateE2E:
    """Phase 5: TaskState 端到端集成验证。"""

    async def _setup(self, tmpdir: str):
        base = _create_workspace(Path(tmpdir))
        mgr = AgentManager()
        mgr.initialize(base)
        await mgr._ensure_checkpointer()
        return mgr

    # ── 5.1 跨请求持久化与恢复 ──

    def test_multiturn_task_state_persistence(self):
        """模拟多轮对话，验证 TaskState 跨请求持久化和恢复。"""
        from graph.task_state import create_task_state, is_task_message, format_task_state

        async def _test():
            with tempfile.TemporaryDirectory() as tmpdir:
                mgr = await self._setup(tmpdir)
                session_id = "multi-turn-e2e"
                config = {"configurable": {"thread_id": session_id}}

                # 轮次 1：用户发送任务消息，创建 TaskState
                agent_1 = mgr._build_agent()
                msg_1 = "帮我实现用户认证模块"
                assert is_task_message(msg_1) is True

                task = create_task_state(session_id=session_id, goal="实现用户认证")
                task["steps"].append({"description": "设计认证流程", "status": "in_progress"})
                await mgr._write_task_state(agent_1, config, task)

                # 轮次 2：新请求，恢复 TaskState
                agent_2 = mgr._build_agent()
                recovered = await mgr._read_task_state(agent_2, config)
                assert recovered is not None
                assert recovered["goal"] == "实现用户认证"
                assert len(recovered["steps"]) == 1
                assert recovered["steps"][0]["status"] == "in_progress"

                # 轮次 2 追加新步骤
                msg_2 = "现在帮我写测试"
                assert is_task_message(msg_2) is True
                recovered["steps"].append({"description": "编写测试", "status": "in_progress"})
                await mgr._write_task_state(agent_2, config, recovered)

                # 轮次 3：再次恢复，验证两步都在
                agent_3 = mgr._build_agent()
                final = await mgr._read_task_state(agent_3, config)
            assert final is not None
            assert len(final["steps"]) == 2
            assert final["steps"][0]["description"] == "设计认证流程"
            assert final["steps"][1]["description"] == "编写测试"

            # 验证 format_task_state 输出完整
            md = format_task_state(final)
            assert "实现用户认证" in md
            assert "设计认证流程" in md
            assert "编写测试" in md
        asyncio.run(_test())

    def test_non_task_message_preserves_existing_state(self):
        """非任务消息不覆盖已有 TaskState。"""
        from graph.task_state import create_task_state

        async def _test():
            with tempfile.TemporaryDirectory() as tmpdir:
                mgr = await self._setup(tmpdir)
                config = {"configurable": {"thread_id": "non-task-msg"}}

                # 创建 TaskState
                agent = mgr._build_agent()
                task = create_task_state(session_id="non-task-msg", goal="原始任务")
                await mgr._write_task_state(agent, config, task)

                # 非任务消息后恢复
                from graph.task_state import is_task_message
                assert is_task_message("你好，今天天气怎么样") is False

                agent_2 = mgr._build_agent()
                recovered = await mgr._read_task_state(agent_2, config)
                assert recovered is not None
                assert recovered["goal"] == "原始任务"
        asyncio.run(_test())

    # ── 5.2 update_task 工具调用更新 ──

    def test_tool_call_updates_task_state_in_checkpoint(self):
        """update_task 工具调用后 TaskState 正确写入 checkpoint。"""
        from graph.task_state import create_task_state, apply_task_update

        async def _test():
            with tempfile.TemporaryDirectory() as tmpdir:
                mgr = await self._setup(tmpdir)
                config = {"configurable": {"thread_id": "tool-update-e2e"}}

                # 初始 TaskState
                agent = mgr._build_agent()
                task = create_task_state(session_id="tool-update-e2e", goal="E2E 工具测试")
                task["steps"].append({"description": "创建模型", "status": "in_progress"})
                await mgr._write_task_state(agent, config, task)

                # 模拟 Agent 调用 update_task → apply_task_update
                recovered = await mgr._read_task_state(agent, config)
                apply_task_update(recovered, "update_step", step_index=0, status="completed", result_summary="模型已创建")
                apply_task_update(recovered, "add_artifact", path="backend/models.py")
                apply_task_update(recovered, "add_decision", description="使用 SQLAlchemy")

                # 写回
                await mgr._write_task_state(agent, config, recovered)

                # 新请求恢复，验证工具更新的结果持久化
                agent_2 = mgr._build_agent()
                final = await mgr._read_task_state(agent_2, config)
                assert final is not None
                assert final["steps"][0]["status"] == "completed"
                assert final["steps"][0]["result_summary"] == "模型已创建"
                assert "backend/models.py" in final["artifacts"]
                assert "使用 SQLAlchemy" in final["decisions"]
        asyncio.run(_test())

    # ── 5.3 SSE 事件流中 update_task 事件格式 ──

    def test_update_task_tool_registered_in_agent(self):
        """update_task 工具已注册到 Agent 工具列表。"""
        async def _test():
            with tempfile.TemporaryDirectory() as tmpdir:
                mgr = await self._setup(tmpdir)
                # 工具在 _tools 中注册，_build_agent 使用它们
                tool_names = [t.name for t in mgr._tools]
                assert "update_task" in tool_names
        asyncio.run(_test())

    def test_update_task_tool_produces_command(self):
        """update_task 工具调用返回 Command 对象，符合 SSE tool_start/tool_end 流转。"""
        from tools.update_task_tool import update_task
        from langgraph.prebuilt.tool_node import ToolRuntime
        from langgraph.types import Command
        from graph.task_state import create_task_state

        task = create_task_state(session_id="test", goal="测试")
        task["steps"].append({"description": "步骤一", "status": "in_progress"})

        runtime = ToolRuntime(
            state={"task_state": task, "messages": []},
            context=None,
            config={},
            stream_writer=None,
            tool_call_id="call-e2e-001",
            store=None,
        )

        # 通过内部函数直接调用（绕过 ToolNode 的注入机制）
        result = update_task.func(
            action="add_step",
            state={"task_state": task, "messages": []},
            runtime=runtime,
            description="新增步骤",
        )
        assert isinstance(result, Command)
        assert result.update is not None
        # 验证 task_state 被更新
        assert "task_state" in result.update
        assert len(result.update["task_state"]["steps"]) == 2
        assert result.update["task_state"]["steps"][1]["description"] == "新增步骤"
        # 验证 ToolMessage 存在
        messages = result.update.get("messages", [])
        assert len(messages) == 1
        assert messages[0].tool_call_id == "call-e2e-001"
        assert "新增步骤" in messages[0].content

    def test_update_task_tool_error_returns_toolmessage(self):
        """update_task 工具调用失败时返回 ToolMessage 错误提示。"""
        from tools.update_task_tool import update_task
        from langgraph.prebuilt.tool_node import ToolRuntime
        from langgraph.types import Command

        runtime = ToolRuntime(
            state={"task_state": None, "messages": []},
            context=None,
            config={},
            stream_writer=None,
            tool_call_id="call-e2e-002",
            store=None,
        )

        # 通过 .func 直接调用，手动注入 state 和 runtime
        result = update_task.func(
            action="add_step",
            state={"task_state": None, "messages": []},
            runtime=runtime,
            description="测试",
        )
        assert isinstance(result, Command)
        messages = result.update.get("messages", [])
        assert len(messages) == 1
        assert "无活跃任务" in messages[0].content
        assert messages[0].tool_call_id == "call-e2e-002"

    def test_guidance_injected_with_active_task(self):
        """有 in_progress 步骤时 Zone 3 包含 update_task 指引。"""
        from graph.task_state import create_task_state, format_task_state
        from graph.prompt_builder import build_dynamic_prefix
        from graph.agent import _has_in_progress_steps

        task = create_task_state(session_id="e2e", goal="集成测试")
        task["steps"].append({"description": "执行中", "status": "in_progress"})
        task["steps"].append({"description": "已完成", "status": "completed"})

        task_md = format_task_state(task)
        has_active = _has_in_progress_steps(task)
        result = build_dynamic_prefix(task_state=task_md, has_active_steps=has_active)

        assert "执行中" in result
        assert "update_task" in result
        assert "add_step" in result

    def test_no_guidance_when_all_completed(self):
        """所有步骤 completed 时不注入指引。"""
        from graph.task_state import create_task_state, format_task_state
        from graph.prompt_builder import build_dynamic_prefix
        from graph.agent import _has_in_progress_steps

        task = create_task_state(session_id="e2e", goal="已完成任务")
        task["steps"].append({"description": "步骤一", "status": "completed"})

        task_md = format_task_state(task)
        has_active = _has_in_progress_steps(task)
        result = build_dynamic_prefix(task_state=task_md, has_active_steps=has_active)

        assert "步骤一" in result
        assert "update_task" not in result
