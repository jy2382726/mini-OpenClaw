"""SkillRegistry 单元测试。"""

import tempfile

import pytest
from pathlib import Path

from graph.skill_registry import SkillMeta, SkillRegistry


class TestSkillMetaInference:
    """测试无 metadata 时从 name + description 推断行为。"""

    def test_explicit_metadata_invocation_auto_true(self):
        meta = SkillMeta(name="test", description="desc", metadata={"invocation_auto": "true"})
        assert meta.is_auto_invocable is True

    def test_explicit_metadata_invocation_auto_false(self):
        meta = SkillMeta(name="test", description="desc", metadata={"invocation_auto": "false"})
        assert meta.is_auto_invocable is False

    def test_no_metadata_default_auto(self):
        """无 metadata 时默认可自动调用。"""
        meta = SkillMeta(name="test", description="简单技能")
        assert meta.is_auto_invocable is True

    def test_infer_auto_from_chinese_trigger_phrase(self):
        """description 包含'当...时使用'触发句式 → 自动调用。"""
        meta = SkillMeta(
            name="get-date",
            description='当用户询问"今天几号"时，立即使用此技能获取准确时间信息。',
        )
        assert meta.is_auto_invocable is True

    def test_infer_manual_from_passive_english(self):
        """description 包含 'Use when asked to' → 手动触发。"""
        meta = SkillMeta(
            name="skill-benchmark",
            description="Use when asked to evaluate whether a skill is effective.",
        )
        assert meta.is_auto_invocable is False

    def test_infer_trigger_patterns_from_quoted_keywords(self):
        """从 description 中文引号提取触发词。"""
        meta = SkillMeta(
            name="get-date",
            description='当用户询问"今天几号"、"现在几点"时使用。',
        )
        patterns = meta.trigger_patterns
        assert "今天几号" in patterns
        assert "现在几点" in patterns

    def test_no_trigger_patterns_when_no_quotes(self):
        """description 无引号时触发词为空。"""
        meta = SkillMeta(name="test", description="一个简单的技能")
        assert meta.trigger_patterns == []

    def test_explicit_trigger_patterns_override(self):
        """显式 metadata.trigger_patterns 优先于推断。"""
        meta = SkillMeta(
            name="test",
            description='当用户说"你好"时使用',
            metadata={"trigger_patterns": "hello,hi"},
        )
        assert meta.trigger_patterns == ["hello", "hi"]


class TestSkillRegistry:
    @pytest.fixture
    def registry(self):
        return SkillRegistry()

    def test_register_skill(self, registry):
        registry.register(SkillMeta(name="test", description="desc"))
        assert "test" in registry.skills

    def test_get_auto_invocable_skills(self, registry):
        registry.register(SkillMeta(name="auto", description="当...时使用"))
        registry.register(SkillMeta(name="manual", description="Use when asked to evaluate"))
        auto_skills = registry.get_auto_invocable_skills()
        assert len(auto_skills) == 1
        assert auto_skills[0].name == "auto"

    def test_find_by_trigger(self, registry):
        registry.register(SkillMeta(
            name="weather", description='获取天气',
            metadata={"trigger_patterns": "天气,气温"},
        ))
        matches = registry.find_by_trigger("今天天气怎么样")
        assert len(matches) == 1
        assert matches[0].name == "weather"

    def test_find_by_trigger_no_match(self, registry):
        registry.register(SkillMeta(name="test", description="desc"))
        matches = registry.find_by_trigger("帮我写代码")
        assert len(matches) == 0

    def test_find_by_category(self, registry):
        registry.register(SkillMeta(
            name="a", description="a", metadata={"categories": "utility"},
        ))
        registry.register(SkillMeta(
            name="b", description="b", metadata={"categories": "utility"},
        ))
        assert len(registry.find_by_category("utility")) == 2

    def test_build_compact_snapshot(self, registry):
        registry.register(SkillMeta(name="visible", description="可见技能"))
        registry.register(SkillMeta(
            name="hidden", description="Use when asked to evaluate",
        ))
        snapshot = registry.build_compact_snapshot()
        assert "visible" in snapshot
        assert "hidden" not in snapshot


class TestSkillRegistryDiscover:
    def test_discover_real_skills(self):
        """使用项目实际技能目录测试 discover（技能无 metadata）。"""
        skills_dir = Path(__file__).resolve().parent.parent / "skills"
        if not skills_dir.exists():
            pytest.skip("技能目录不存在")

        registry = SkillRegistry.discover(skills_dir)
        assert len(registry.skills) == 6

        assert "get_weather" in registry.skills
        assert "get-date" in registry.skills
        assert "dialogue-summarizer" in registry.skills
        assert "skill-benchmark" in registry.skills
        assert "skill-creator" in registry.skills
        assert "skill-creator-pro" in registry.skills

    def test_discover_inference_from_real_skills(self):
        """验证实际技能的推断结果与预期一致（无需修改 SKILL.md）。"""
        skills_dir = Path(__file__).resolve().parent.parent / "skills"
        if not skills_dir.exists():
            pytest.skip("技能目录不存在")

        registry = SkillRegistry.discover(skills_dir)
        auto_names = {s.name for s in registry.get_auto_invocable_skills()}

        # get_weather: 短描述无触发句式，但默认 auto=True
        assert "get_weather" in auto_names

        # get-date: description 含"当...时，立即使用" → auto
        assert "get-date" in auto_names

        # dialogue-summarizer: description 含"当...时，立即使用" → auto
        assert "dialogue-summarizer" in auto_names

        # skill-benchmark: description 含 "Use when asked to" → manual
        assert "skill-benchmark" not in auto_names

        # skill-creator: description 含 "Use when users want" → 默认 auto
        assert "skill-creator" in auto_names

        # skill-creator-pro: description 含 "Use when asked to" → manual
        assert "skill-creator-pro" not in auto_names

    def test_discover_trigger_inference(self):
        """验证从 description 引号中提取的触发词能正常匹配。"""
        skills_dir = Path(__file__).resolve().parent.parent / "skills"
        if not skills_dir.exists():
            pytest.skip("技能目录不存在")

        registry = SkillRegistry.discover(skills_dir)

        # get-date 的 description 中有"今天几号"、"现在几点"等引号内容
        matches = registry.find_by_trigger("今天几号")
        assert any(s.name == "get-date" for s in matches)

    def test_discover_nonexistent_dir(self):
        registry = SkillRegistry.discover(Path("/tmp/nonexistent_skills_xyz"))
        assert len(registry.skills) == 0

    def test_discover_simple_skill_no_metadata(self):
        """无 metadata 的简单技能仍可被正确注册和推断。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            simple = skills_dir / "simple-skill"
            simple.mkdir()
            (simple / "SKILL.md").write_text(
                "---\nname: simple-skill\ndescription: 简单技能\n---\n\n内容",
                encoding="utf-8",
            )

            registry = SkillRegistry.discover(skills_dir)
            assert len(registry.skills) == 1
            skill = registry.skills["simple-skill"]
            assert skill.is_auto_invocable is True  # 默认 auto
            assert skill.trigger_patterns == []  # 无引号，无触发词
