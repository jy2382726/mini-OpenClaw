"""TaskState 单元测试。"""

from graph.task_state import (
    AgentCustomState,
    StepStatus,
    TaskStep,
    TaskState,
    create_task_state,
    format_task_state,
    is_task_message,
)


class TestIsTaskMessage:
    def test_chinese_task_verbs(self):
        assert is_task_message("帮我创建一个新项目") is True
        assert is_task_message("实现用户认证功能") is True
        assert is_task_message("修复登录页面的 bug") is True
        assert is_task_message("重构数据库层") is True
        assert is_task_message("优化查询性能") is True

    def test_english_task_verbs(self):
        assert is_task_message("Create a new REST API endpoint") is True
        assert is_task_message("build the dashboard") is True
        assert is_task_message("implement caching layer") is True
        assert is_task_message("fix the memory leak") is True

    def test_non_task_messages(self):
        assert is_task_message("你好") is False
        assert is_task_message("今天天气怎么样") is False
        assert is_task_message("谢谢") is False
        assert is_task_message("what is 2+2") is False

    def test_edge_cases(self):
        assert is_task_message("") is False
        assert is_task_message("请帮我设计一个系统") is True
        assert is_task_message("add a new column to the table") is True


class TestCreateTaskState:
    def test_initial_state(self):
        state = create_task_state("sess-1", "实现用户认证")
        assert state["session_id"] == "sess-1"
        assert state["goal"] == "实现用户认证"
        assert state["steps"] == []
        assert state["artifacts"] == []
        assert state["decisions"] == []
        assert state["blockers"] == []

    def test_goal_preserved(self):
        """create_task_state 不截断 goal，截断由调用方负责。"""
        long_goal = "x" * 500
        state = create_task_state("sess-1", long_goal)
        assert state["goal"] == long_goal


class TestFormatTaskState:
    def test_empty_steps(self):
        state = create_task_state("sess-1", "创建项目")
        md = format_task_state(state)
        assert "创建项目" in md
        assert "当前任务" in md

    def test_with_steps(self):
        state = create_task_state("sess-1", "实现认证系统")
        state["steps"] = [
            {"description": "设计数据库模型", "status": "completed", "result_summary": "完成"},
            {"description": "实现 API 端点", "status": "in_progress"},
            {"description": "编写测试", "status": "pending"},
        ]
        md = format_task_state(state)
        assert "设计数据库模型" in md
        assert "✅" in md
        assert "🔄" in md
        assert "⬜" in md

    def test_with_artifacts(self):
        state = create_task_state("sess-1", "创建项目")
        state["artifacts"] = ["backend/app.py", "backend/models.py"]
        md = format_task_state(state)
        assert "backend/app.py" in md
        assert "已创建文件" in md

    def test_with_blockers(self):
        state = create_task_state("sess-1", "部署服务")
        state["blockers"] = ["等待 API key 审批"]
        md = format_task_state(state)
        assert "等待 API key 审批" in md
        assert "阻塞项" in md

    def test_step_with_result_summary(self):
        state = create_task_state("sess-1", "任务")
        state["steps"] = [
            {"description": "步骤 1", "status": "completed", "result_summary": "成功创建文件"},
        ]
        md = format_task_state(state)
        assert "成功创建文件" in md


class TestStepStatus:
    def test_all_statuses(self):
        assert StepStatus.PENDING == "pending"
        assert StepStatus.IN_PROGRESS == "in_progress"
        assert StepStatus.COMPLETED == "completed"
        assert StepStatus.BLOCKED == "blocked"


class TestAgentCustomState:
    def test_type_annotations(self):
        assert "task_state" in AgentCustomState.__annotations__
        assert "context_type" in AgentCustomState.__annotations__

    def test_optional_fields(self):
        """AgentCustomState 的字段都是可选的。"""
        state: AgentCustomState = {}
        assert state.get("task_state") is None


class TestSummarizeGoal:
    """测试 AgentManager._summarize_goal 的降级行为。"""

    def test_fallback_when_no_llm(self):
        """summary_llm 返回 None 时回退到截断。"""
        from unittest.mock import patch, MagicMock
        from graph.agent import AgentManager

        mgr = AgentManager()
        with patch.object(mgr, "_create_summary_llm", return_value=None):
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                mgr._summarize_goal("帮我实现一个完整的用户认证系统，包括登录注册和权限管理")
            )
            # 回退到截断，消息 < 200 字符所以完整保留
            assert "认证系统" in result

    def test_fallback_on_llm_error(self):
        """LLM 调用异常时回退到截断。"""
        from unittest.mock import patch, MagicMock
        from graph.agent import AgentManager

        mgr = AgentManager()
        mock_llm = MagicMock()
        mock_llm.ainvoke.side_effect = Exception("API error")

        with patch.object(mgr, "_create_summary_llm", return_value=mock_llm):
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                mgr._summarize_goal("修复登录页面的 bug")
            )
            assert "修复登录页面" in result

    def test_llm_summary_used(self):
        """LLM 返回有效摘要时使用摘要结果。"""
        from unittest.mock import patch, MagicMock
        from graph.agent import AgentManager

        mgr = AgentManager()
        mock_llm = MagicMock()
        mock_result = MagicMock()
        mock_result.content = "实现用户认证模块"
        mock_llm.ainvoke = MagicMock(return_value=mock_result)

        # ainvoke 需要 coroutine
        import asyncio
        async def _fake_ainvoke(*args, **kwargs):
            return mock_result
        mock_llm.ainvoke = _fake_ainvoke

        with patch.object(mgr, "_create_summary_llm", return_value=mock_llm):
            result = asyncio.get_event_loop().run_until_complete(
                mgr._summarize_goal("帮我实现一个完整的用户认证系统，包括登录注册和权限管理")
            )
            assert result == "实现用户认证模块"

    def test_fallback_truncates_long_message(self):
        """无 LLM 时长消息截断到 200 字符。"""
        from unittest.mock import patch
        from graph.agent import AgentManager

        mgr = AgentManager()
        long_msg = "帮我创建项目" + "详细描述" * 100
        with patch.object(mgr, "_create_summary_llm", return_value=None):
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                mgr._summarize_goal(long_msg)
            )
            assert len(result) <= 200


class TestApplyTaskUpdate:
    """测试 apply_task_update 五种操作及边界条件。"""

    def _make_task(self) -> dict:
        return create_task_state("sess-1", "测试任务")

    # ── add_step ──

    def test_add_step_success(self):
        from graph.task_state import apply_task_update
        task = self._make_task()
        result = apply_task_update(task, "add_step", description="创建数据库模型")
        assert result.startswith("✅")
        assert len(task["steps"]) == 1
        assert task["steps"][0]["description"] == "创建数据库模型"
        assert task["steps"][0]["status"] == "in_progress"

    def test_add_step_empty_description(self):
        from graph.task_state import apply_task_update
        task = self._make_task()
        result = apply_task_update(task, "add_step", description="")
        assert result.startswith("❌")
        assert len(task["steps"]) == 0

    def test_add_step_whitespace_description(self):
        from graph.task_state import apply_task_update
        task = self._make_task()
        result = apply_task_update(task, "add_step", description="   ")
        assert result.startswith("❌")

    def test_add_step_multiple(self):
        from graph.task_state import apply_task_update
        task = self._make_task()
        apply_task_update(task, "add_step", description="步骤一")
        apply_task_update(task, "add_step", description="步骤二")
        assert len(task["steps"]) == 2
        # 第二步返回 "步骤 2"
        result = apply_task_update(task, "add_step", description="步骤三")
        assert "3" in result

    # ── update_step ──

    def test_update_step_status(self):
        from graph.task_state import apply_task_update
        task = self._make_task()
        apply_task_update(task, "add_step", description="创建模型")
        result = apply_task_update(task, "update_step", step_index=0, status="completed")
        assert result.startswith("✅")
        assert task["steps"][0]["status"] == "completed"

    def test_update_step_with_summary(self):
        from graph.task_state import apply_task_update
        task = self._make_task()
        apply_task_update(task, "add_step", description="设计API")
        result = apply_task_update(
            task, "update_step",
            step_index=0, status="completed", result_summary="API设计完成"
        )
        assert "API设计完成" in result
        assert task["steps"][0]["result_summary"] == "API设计完成"

    def test_update_step_invalid_index(self):
        from graph.task_state import apply_task_update
        task = self._make_task()
        apply_task_update(task, "add_step", description="步骤一")
        result = apply_task_update(task, "update_step", step_index=5, status="completed")
        assert result.startswith("❌")
        assert "越界" in result

    def test_update_step_negative_index(self):
        from graph.task_state import apply_task_update
        task = self._make_task()
        result = apply_task_update(task, "update_step", step_index=-1, status="completed")
        assert result.startswith("❌")

    def test_update_step_empty_steps(self):
        from graph.task_state import apply_task_update
        task = self._make_task()
        result = apply_task_update(task, "update_step", step_index=0, status="completed")
        assert result.startswith("❌")
        assert "无步骤" in result

    def test_update_step_invalid_status(self):
        from graph.task_state import apply_task_update
        task = self._make_task()
        apply_task_update(task, "add_step", description="步骤一")
        result = apply_task_update(task, "update_step", step_index=0, status="invalid")
        assert result.startswith("❌")
        assert "无效 status" in result

    # ── add_artifact ──

    def test_add_artifact_success(self):
        from graph.task_state import apply_task_update
        task = self._make_task()
        result = apply_task_update(task, "add_artifact", path="backend/models.py")
        assert result.startswith("✅")
        assert "backend/models.py" in task["artifacts"]

    def test_add_artifact_empty_path(self):
        from graph.task_state import apply_task_update
        task = self._make_task()
        result = apply_task_update(task, "add_artifact", path="")
        assert result.startswith("❌")

    def test_add_artifact_multiple(self):
        from graph.task_state import apply_task_update
        task = self._make_task()
        apply_task_update(task, "add_artifact", path="backend/models.py")
        apply_task_update(task, "add_artifact", path="backend/api/routes.py")
        assert len(task["artifacts"]) == 2

    # ── add_blocker ──

    def test_add_blocker_success(self):
        from graph.task_state import apply_task_update
        task = self._make_task()
        result = apply_task_update(task, "add_blocker", description="等待权限审批")
        assert result.startswith("✅")
        assert "等待权限审批" in task["blockers"]

    def test_add_blocker_empty_description(self):
        from graph.task_state import apply_task_update
        task = self._make_task()
        result = apply_task_update(task, "add_blocker", description="")
        assert result.startswith("❌")

    # ── add_decision ──

    def test_add_decision_success(self):
        from graph.task_state import apply_task_update
        task = self._make_task()
        result = apply_task_update(task, "add_decision", description="使用 PostgreSQL")
        assert result.startswith("✅")
        assert "使用 PostgreSQL" in task["decisions"]

    def test_add_decision_empty_description(self):
        from graph.task_state import apply_task_update
        task = self._make_task()
        result = apply_task_update(task, "add_decision", description="")
        assert result.startswith("❌")

    # ── invalid action ──

    def test_unknown_action(self):
        from graph.task_state import apply_task_update
        task = self._make_task()
        result = apply_task_update(task, "delete_all")
        assert result.startswith("❌")
        assert "未知操作" in result

    # ── 综合场景 ──

    def test_full_workflow(self):
        """模拟完整工作流：添加步骤 → 完成 → 记录产物 → 记录决策 → 添加阻塞项。"""
        from graph.task_state import apply_task_update
        task = self._make_task()

        # 1. 添加步骤
        apply_task_update(task, "add_step", description="设计数据库模型")
        apply_task_update(task, "add_step", description="实现 API 端点")
        assert len(task["steps"]) == 2

        # 2. 完成第一步
        apply_task_update(
            task, "update_step",
            step_index=0, status="completed", result_summary="模型已创建"
        )
        assert task["steps"][0]["status"] == "completed"
        assert task["steps"][0]["result_summary"] == "模型已创建"

        # 3. 记录产物
        apply_task_update(task, "add_artifact", path="backend/models.py")
        assert task["artifacts"] == ["backend/models.py"]

        # 4. 记录决策
        apply_task_update(task, "add_decision", description="选择 SQLite 作为开发数据库")
        assert len(task["decisions"]) == 1

        # 5. 添加阻塞项
        apply_task_update(task, "add_blocker", description="等待测试环境部署")
        assert len(task["blockers"]) == 1
