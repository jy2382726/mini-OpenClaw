"""task-state-persistence 测试 — TaskState 持久化与 Agent 更新。"""

import asyncio
import tempfile
from pathlib import Path

from graph.agent import AgentManager


def _create_workspace(base: Path) -> Path:
    ws = base / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "SOUL.md").write_text("你是助手", encoding="utf-8")
    (ws / "IDENTITY.md").write_text("名字：Claw", encoding="utf-8")
    (ws / "USER.md").write_text("用户是开发者", encoding="utf-8")
    (ws / "AGENTS.md").write_text("操作指令", encoding="utf-8")
    skills = base / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    return base


async def _setup_mgr(tmpdir: str) -> AgentManager:
    """创建并初始化 AgentManager（含异步 checkpointer 初始化）。"""
    mgr = AgentManager()
    base = _create_workspace(Path(tmpdir))
    mgr.initialize(base)
    await mgr._ensure_checkpointer()
    return mgr


class TestThreadIdPassing:
    """Phase 1: thread_id 传递与 checkpoint 激活。"""

    def test_astream_config_contains_thread_id(self):
        """astream 调用 agent.astream 时传入 thread_id。"""
        import inspect
        from graph.agent import AgentManager

        source = inspect.getsource(AgentManager.astream)
        assert '"thread_id"' in source or "thread_id" in source

    def test_ainvoke_config_contains_thread_id(self):
        """ainvoke 调用 agent.ainvoke 时传入 thread_id。"""
        import inspect
        from graph.agent import AgentManager

        source = inspect.getsource(AgentManager.ainvoke)
        assert '"thread_id"' in source or "thread_id" in source

    def test_agent_builds_with_checkpointer(self):
        """_build_agent 创建带有 AsyncSqliteSaver 的 agent。"""
        async def _test():
            with tempfile.TemporaryDirectory() as tmpdir:
                mgr = await _setup_mgr(tmpdir)
                agent = mgr._build_agent()
                assert agent is not None
        asyncio.run(_test())


class TestTaskStateRecovery:
    """Phase 2: TaskState 写入、恢复、追加。"""

    def test_read_task_state_returns_none_on_empty(self):
        """无 checkpoint 时 _read_task_state 返回 None。"""
        async def _test():
            with tempfile.TemporaryDirectory() as tmpdir:
                mgr = await _setup_mgr(tmpdir)
                agent = mgr._build_agent()
                config = {"configurable": {"thread_id": "test-session"}}
                result = await mgr._read_task_state(agent, config)
                assert result is None
        asyncio.run(_test())

    def test_write_then_read_task_state(self):
        """写入 TaskState 后可从 checkpoint 恢复。"""
        from graph.task_state import create_task_state

        async def _test():
            with tempfile.TemporaryDirectory() as tmpdir:
                mgr = await _setup_mgr(tmpdir)
                agent = mgr._build_agent()
                config = {"configurable": {"thread_id": "test-session"}}

                task = create_task_state(session_id="test-session", goal="实现认证")
                task["steps"].append({"description": "设计数据库", "status": "completed"})
                await mgr._write_task_state(agent, config, task)

                # 恢复
                recovered = await mgr._read_task_state(agent, config)
                assert recovered is not None
                assert recovered["goal"] == "实现认证"
                assert len(recovered["steps"]) == 1
                assert recovered["steps"][0]["description"] == "设计数据库"
        asyncio.run(_test())

    def test_read_task_state_ignores_empty_dict(self):
        """空 dict 或无 goal 的 dict 不被视为有效 TaskState。"""
        async def _test():
            with tempfile.TemporaryDirectory() as tmpdir:
                mgr = await _setup_mgr(tmpdir)
                agent = mgr._build_agent()
                config = {"configurable": {"thread_id": "test-session"}}

                # 写入空 dict
                await mgr._write_task_state(agent, config, {})
                result = await mgr._read_task_state(agent, config)
                assert result is None
        asyncio.run(_test())

    def test_append_step_to_existing_task(self):
        """新任务性消息追加步骤到已有 TaskState。"""
        from graph.task_state import create_task_state

        async def _test():
            with tempfile.TemporaryDirectory() as tmpdir:
                mgr = await _setup_mgr(tmpdir)
                agent = mgr._build_agent()
                config = {"configurable": {"thread_id": "test-session"}}

                # 先创建一个 TaskState
                task = create_task_state(session_id="test-session", goal="实现认证")
                task["steps"].append({"description": "设计数据库", "status": "completed"})
                await mgr._write_task_state(agent, config, task)

                # 模拟追加逻辑
                recovered = await mgr._read_task_state(agent, config)
                assert recovered is not None
                # 追加新步骤
                recovered["steps"].append({
                    "description": "编写测试",
                    "status": "in_progress",
                })
                await mgr._write_task_state(agent, config, recovered)

                # 验证
                final = await mgr._read_task_state(agent, config)
                assert final is not None
                assert len(final["steps"]) == 2
                assert final["steps"][1]["description"] == "编写测试"
        asyncio.run(_test())

    def test_different_sessions_isolated(self):
        """不同 session 的 TaskState 互相隔离。"""
        from graph.task_state import create_task_state

        async def _test():
            with tempfile.TemporaryDirectory() as tmpdir:
                mgr = await _setup_mgr(tmpdir)
                agent = mgr._build_agent()

                config_a = {"configurable": {"thread_id": "session-a"}}
                config_b = {"configurable": {"thread_id": "session-b"}}

                task_a = create_task_state(session_id="session-a", goal="任务A")
                await mgr._write_task_state(agent, config_a, task_a)

                # session_b 应无 TaskState
                assert await mgr._read_task_state(agent, config_b) is None
                # session_a 应有
                assert await mgr._read_task_state(agent, config_a) is not None
        asyncio.run(_test())

    def test_cross_request_recovery_with_shared_checkpointer(self):
        """跨请求恢复：不同 _build_agent 调用间共享 checkpointer，TaskState 不丢失。"""
        from graph.task_state import create_task_state

        async def _test():
            with tempfile.TemporaryDirectory() as tmpdir:
                mgr = await _setup_mgr(tmpdir)
                config = {"configurable": {"thread_id": "cross-request"}}

                # 请求 1：构建 agent，写入 TaskState
                agent_1 = mgr._build_agent()
                task = create_task_state(session_id="cross-request", goal="跨请求测试")
                task["steps"].append({"description": "步骤一", "status": "completed"})
                await mgr._write_task_state(agent_1, config, task)

                # 请求 2：构建新 agent（模拟新请求），应能恢复
                agent_2 = mgr._build_agent()
                recovered = await mgr._read_task_state(agent_2, config)

                assert recovered is not None
                assert recovered["goal"] == "跨请求测试"
                assert len(recovered["steps"]) == 1
                assert recovered["steps"][0]["description"] == "步骤一"
        asyncio.run(_test())

    def test_sqlite_file_persists_across_restart(self):
        """SQLite 文件持久化：模拟进程重启后新建 AgentManager 可恢复 TaskState。"""
        from graph.task_state import create_task_state

        async def _test():
            with tempfile.TemporaryDirectory() as tmpdir:
                base = _create_workspace(Path(tmpdir))
                config = {"configurable": {"thread_id": "restart-test"}}

                # 进程 1：写入 TaskState
                mgr_1 = AgentManager()
                mgr_1.initialize(base)
                await mgr_1._ensure_checkpointer()
                agent_1 = mgr_1._build_agent()
                task = create_task_state(session_id="restart-test", goal="重启恢复测试")
                await mgr_1._write_task_state(agent_1, config, task)

                # 进程 2：新建 AgentManager（模拟重启），从同一 SQLite 文件恢复
                mgr_2 = AgentManager()
                mgr_2.initialize(base)
                await mgr_2._ensure_checkpointer()
                agent_2 = mgr_2._build_agent()
                recovered = await mgr_2._read_task_state(agent_2, config)

                assert recovered is not None
                assert recovered["goal"] == "重启恢复测试"
        asyncio.run(_test())
