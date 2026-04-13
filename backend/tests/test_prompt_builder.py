"""prompt_builder 测试：三段式缓存前缀。"""

import tempfile
from pathlib import Path

from graph.prompt_builder import build_stable_prefix, build_system_prompt, build_dynamic_prefix
from graph.skill_registry import SkillMeta, SkillRegistry


def _create_workspace(tmpdir: Path) -> Path:
    """创建包含 workspace 文件的临时目录。"""
    ws = tmpdir / "workspace"
    ws.mkdir()
    (ws / "SOUL.md").write_text("你是一个助手", encoding="utf-8")
    (ws / "IDENTITY.md").write_text("名字：Claw", encoding="utf-8")
    (ws / "USER.md").write_text("用户是开发者", encoding="utf-8")
    (ws / "AGENTS.md").write_text("操作指令", encoding="utf-8")
    return tmpdir


class TestBuildStablePrefix:
    def test_contains_zone_markers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _create_workspace(Path(tmpdir))
            result = build_stable_prefix(base)
            assert "<!-- Zone 1: Stable -->" in result
            assert "<!-- Zone 2: Low-frequency -->" in result

    def test_contains_workspace_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _create_workspace(Path(tmpdir))
            result = build_stable_prefix(base)
            assert "你是一个助手" in result
            assert "名字：Claw" in result
            assert "用户是开发者" in result
            assert "操作指令" in result

    def test_no_memory_md_injected(self):
        """Zone 1+2 不包含 MEMORY.md 全文。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _create_workspace(Path(tmpdir))
            mem = base / "memory"
            mem.mkdir()
            (mem / "MEMORY.md").write_text("重要记忆内容", encoding="utf-8")
            result = build_stable_prefix(base)
            assert "重要记忆内容" not in result

    def test_skill_registry_snapshot(self):
        """SkillRegistry 生成的精简摘要替代旧 XML 快照。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _create_workspace(Path(tmpdir))
            registry = SkillRegistry()
            registry.register(SkillMeta(name="test-skill", description="测试技能"))
            result = build_stable_prefix(base, skill_registry=registry)
            assert "test-skill" in result
            assert "测试技能" in result
            # 不应包含旧 XML 格式
            assert "<available_skills>" not in result

    def test_no_registry_produces_empty_skills(self):
        """无 SkillRegistry 时 skills_snapshot 为空。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _create_workspace(Path(tmpdir))
            result = build_stable_prefix(base, skill_registry=None)
            # 无 registry 时 skills 区域为空
            assert "<available_skills>" not in result

    def test_deterministic_output(self):
        """相同输入产生逐字符一致的输出。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _create_workspace(Path(tmpdir))
            registry = SkillRegistry()
            registry.register(SkillMeta(name="skill-a", description="技能A"))
            first = build_stable_prefix(base, skill_registry=registry)
            second = build_stable_prefix(base, skill_registry=registry)
            assert first == second

    def test_stable_prefix_unchanged_across_calls(self):
        """workspace 文件不变时，Zone 1+2 输出完全一致。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _create_workspace(Path(tmpdir))
            results = [build_stable_prefix(base) for _ in range(5)]
            assert all(r == results[0] for r in results)

    def test_empty_workspace_files(self):
        """workspace 文件不存在时不报错。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "workspace").mkdir()
            result = build_stable_prefix(base)
            assert isinstance(result, str)
            # 模板占位符都在，只是内容为空
            assert "<!-- Zone 1: Stable -->" in result

    def test_truncation_on_oversized_file(self):
        """超大文件被截断。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _create_workspace(Path(tmpdir))
            # SOUL.md 超过 20000 字符
            (base / "workspace" / "SOUL.md").write_text("x" * 25000, encoding="utf-8")
            result = build_stable_prefix(base)
            assert "...[truncated]" in result


class TestBackwardCompatibility:
    def test_build_system_prompt_delegates(self):
        """build_system_prompt() 向后兼容，等价于 build_stable_prefix()。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _create_workspace(Path(tmpdir))
            old_api = build_system_prompt(base)
            new_api = build_stable_prefix(base)
            assert old_api == new_api

    def test_build_system_prompt_rag_mode_ignored(self):
        """rag_mode 参数不再影响输出（MEMORY.md 不再注入）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _create_workspace(Path(tmpdir))
            mem = base / "memory"
            mem.mkdir()
            (mem / "MEMORY.md").write_text("记忆内容", encoding="utf-8")
            with_rag = build_system_prompt(base, rag_mode=True)
            without_rag = build_system_prompt(base, rag_mode=False)
            assert with_rag == without_rag
            assert "记忆内容" not in with_rag


class TestBuildDynamicPrefix:
    def test_empty_inputs_return_empty(self):
        assert build_dynamic_prefix() == ""

    def test_memory_context_only(self):
        result = build_dynamic_prefix(memory_context="[相关记忆]\n内容（来源: mem0）")
        assert "<!-- Zone 3: Dynamic -->" in result
        assert "[相关记忆]" in result

    def test_task_state_only(self):
        result = build_dynamic_prefix(task_state="## 当前任务\n- 步骤 1 ✅\n- 步骤 2 ⏳")
        assert "当前任务" in result
        assert "<!-- Zone 3: Dynamic -->" not in result

    def test_both_memory_and_task(self):
        result = build_dynamic_prefix(
            memory_context="[相关记忆]\n内容",
            task_state="## 当前任务\n- 步骤 1",
        )
        assert "<!-- Zone 3: Dynamic -->" in result
        assert "[相关记忆]" in result
        assert "当前任务" in result

    def test_deterministic_with_same_inputs(self):
        r1 = build_dynamic_prefix(memory_context="记忆", task_state="任务")
        r2 = build_dynamic_prefix(memory_context="记忆", task_state="任务")
        assert r1 == r2

    def test_guidance_injected_when_active_steps(self):
        """has_active_steps=True 时注入 update_task 指引。"""
        result = build_dynamic_prefix(
            task_state="## 当前任务\n**目标**: 测试",
            has_active_steps=True,
        )
        assert "update_task" in result
        assert "add_step" in result
        assert "update_step" in result
        assert "add_artifact" in result

    def test_no_guidance_without_active_steps(self):
        """has_active_steps=False 时不注入指引。"""
        result = build_dynamic_prefix(
            task_state="## 当前任务\n**目标**: 测试",
            has_active_steps=False,
        )
        assert "update_task" not in result

    def test_no_guidance_with_empty_task_state(self):
        """无 TaskState 时 has_active_steps=True 仍注入指引（调用方保证不会误传）。"""
        result = build_dynamic_prefix(has_active_steps=True)
        # build_dynamic_prefix 不防御 has_active_steps 与 task_state 的一致性，
        # 但 agent.py 中 _has_in_progress_steps 仅在 task_state_dict 有 in_progress 步骤时返回 True
        assert "update_task" in result

    def test_guidance_not_injected_by_default(self):
        """默认参数不注入指引。"""
        result = build_dynamic_prefix(task_state="## 当前任务")
        assert "update_task" not in result
