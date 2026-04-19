"""SkillRegistry — 遵循 Agent Skills 标准的技能注册表。

解析 SKILL.md frontmatter 中的 name、description、metadata 字段，
构建多维度索引（触发词、分类），支持分级注入策略。

关键设计：metadata 全部可选。当技能仅提供 name + description 时，
通过启发式规则从 description 内容自动推断 invocation_auto、
trigger_patterns 等属性，确保旧格式技能无缝接入。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class SkillMeta:
    """遵循 Agent Skills 标准的技能元数据。

    metadata 字段全部可选。缺少 metadata 时通过 description
    中的关键词启发式推断自动调用权限和触发词。
    """

    name: str
    description: str
    metadata: dict[str, str] = field(default_factory=dict)
    location: str = ""

    @property
    def is_auto_invocable(self) -> bool:
        """是否允许 Agent 自动调用。

        推断优先级：
        1. metadata.invocation_auto 显式设置 → 使用该值
        2. description 包含"当...时，立即使用"等触发句式 → 自动调用
        3. description 包含 "Use when asked" 等被动句式 → 手动触发
        4. 默认 → 自动调用（保持与旧版 skills_scanner 行为一致）
        """
        explicit = self.metadata.get("invocation_auto")
        if explicit is not None:
            return explicit == "true"

        # 从 description 推断：包含明确触发指令的视为可自动调用
        desc_lower = self.description.lower()
        # 中文触发句式
        if re.search(r"当.*时.{0,4}使用|立即使用|自动触发", desc_lower):
            return True
        # 英文被动句式 → 手动触发（如 skill-benchmark 的 "Use when asked to evaluate"）
        if re.search(r"use when asked\s+to", desc_lower):
            return False
        # 默认：自动调用
        return True

    @property
    def trigger_patterns(self) -> list[str]:
        """获取触发词模式列表。

        推断优先级：
        1. metadata.trigger_patterns 显式设置
        2. 从 description 中的中文引号内容提取关键词
        """
        raw = self.metadata.get("trigger_patterns", "")
        if raw:
            return [p.strip() for p in raw.split(",") if p.strip()]

        # 从 description 中提取引号内的关键词（ASCII " " 和 Unicode ""「」）
        patterns: list[str] = []
        for match in re.finditer(r'["\u201c\u300c](.+?)["\u201d\u300d]', self.description):
            keyword = match.group(1).strip()
            if 1 <= len(keyword) <= 10:
                patterns.append(keyword)
        return patterns

    @property
    def categories(self) -> list[str]:
        """获取分类标签。"""
        raw = self.metadata.get("categories", "")
        if raw:
            return [c.strip() for c in raw.split(",") if c.strip()]
        return []

    @property
    def inject_system_prompt(self) -> bool:
        """是否需要完整注入到系统提示（Level 3）。"""
        return self.metadata.get("inject_system_prompt", "false") == "true"


class SkillRegistry:
    """技能注册表：管理 Agent Skills 标准元数据。

    支持：
    - 多维度索引（触发词、分类）
    - 分级注入（隐藏/索引/按需/预加载）
    - 精简快照生成（用于系统提示 Zone 2）
    - 旧格式兼容（无 metadata 时自动推断）
    """

    def __init__(self) -> None:
        self.skills: dict[str, SkillMeta] = {}
        self._index_by_trigger: dict[str, str] = {}
        self._index_by_category: dict[str, list[str]] = {}

    def register(self, skill: SkillMeta) -> None:
        """注册技能，构建多维度索引。"""
        self.skills[skill.name] = skill
        for pattern in skill.trigger_patterns:
            self._index_by_trigger[pattern] = skill.name
        for cat in skill.categories:
            self._index_by_category.setdefault(cat, []).append(skill.name)

    def get_auto_invocable_skills(self) -> list[SkillMeta]:
        """获取 Agent 可自动调用的技能（用于系统提示注入 Level 1）。"""
        return [s for s in self.skills.values() if s.is_auto_invocable]

    def get_preload_skills(self) -> list[SkillMeta]:
        """获取需要完整注入系统提示的技能（Level 3 预加载）。"""
        return [s for s in self.skills.values() if s.inject_system_prompt]

    def find_by_trigger(self, user_message: str) -> list[SkillMeta]:
        """根据用户消息匹配可能触发的技能。"""
        matches = []
        seen: set[str] = set()
        for pattern, skill_name in self._index_by_trigger.items():
            if skill_name not in seen and re.search(re.escape(pattern), user_message, re.IGNORECASE):
                matches.append(self.skills[skill_name])
                seen.add(skill_name)
        return matches

    def find_by_category(self, category: str) -> list[SkillMeta]:
        """按分类检索技能。"""
        names = self._index_by_category.get(category, [])
        return [self.skills[n] for n in names if n in self.skills]

    def build_compact_snapshot(self) -> str:
        """构建精简快照（用于系统提示 Zone 2）。

        仅列出可自动调用技能的名称和描述。
        有触发词时追加 [触发: 关键词1/关键词2]，最多 3 个。
        """
        lines = ["## 可用技能（按需读取 SKILL.md 获取详情）"]
        for skill in self.get_auto_invocable_skills():
            line = f"- {skill.name}: {skill.description}"
            if skill.trigger_patterns:
                triggers = "/".join(skill.trigger_patterns[:3])
                line += f" [触发: {triggers}]"
            lines.append(line)
        return "\n".join(lines)

    @classmethod
    def discover(cls, skills_dir: Path) -> SkillRegistry:
        """扫描 skills 目录，解析每个 SKILL.md 的 frontmatter，构建注册表。

        仅扫描 skills/<name>/SKILL.md 一级结构，忽略 versions/、assets/ 等子目录。
        兼容旧格式：无 metadata 时通过启发式推断自动调用权限和触发词。
        """
        registry = cls()

        if not skills_dir.exists():
            return registry

        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                content = skill_md.read_text(encoding="utf-8")
                meta = _parse_frontmatter(content)
                if meta is None:
                    continue

                rel_path = f"./backend/skills/{skill_md.parent.name}/SKILL.md"
                skill = SkillMeta(
                    name=meta.get("name", skill_md.parent.name),
                    description=meta.get("description", ""),
                    metadata=meta.get("metadata") or {},
                    location=rel_path,
                )
                registry.register(skill)
            except Exception as e:
                print(f"⚠️ 解析技能 {skill_md} 失败: {e}")

        print(f"📋 SkillRegistry: {len(registry.skills)} 个技能已注册 "
              f"({len(registry.get_auto_invocable_skills())} 个可自动调用)")
        return registry


def _parse_frontmatter(content: str) -> dict | None:
    """解析 Markdown 文件的 YAML frontmatter。"""
    if not content.startswith("---"):
        idx = content.find("\n---")
        if idx == -1:
            return None
        content = content[idx + 1:]

    parts = content.split("---", 2)
    if len(parts) < 3:
        return None

    yaml_str = parts[1].strip()
    if not yaml_str:
        return None

    result = yaml.safe_load(yaml_str)
    if not isinstance(result, dict):
        return None
    return result
