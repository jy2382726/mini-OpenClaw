"""Prompt Builder — 三段式系统提示构建（Cache Zone 分离）。

Zone 1（极稳定层）：SOUL.md + IDENTITY.md + USER.md
Zone 2（低频变化层）：AGENTS.md + 精简技能摘要
Zone 3（高频变化层）：由 UnifiedMemoryRetriever / TaskState 按需注入

Zone 1+2 合并为 build_stable_prefix()，workspace 文件不变时输出逐字符一致。
Zone 3 不在此处生成，而是由 agent.py 在请求时动态注入。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

MAX_COMPONENT_LENGTH = 20000

# 确定性模板：每个区域使用固定占位符，确保相同输入产生相同输出
_TEMPLATE = """<!-- Zone 1: Stable -->
{soul}
{identity}
{user_profile}
<!-- Zone 2: Low-frequency -->
{agents_guide}
{skills_snapshot}"""


def _read_component(path: Path) -> str:
    """Read a file, truncating if it exceeds MAX_COMPONENT_LENGTH.

    Tries UTF-8 first, falls back to GBK, then latin-1 as last resort.
    This handles mixed-encoding files that may be written by the Agent.
    """
    if not path.exists():
        return ""
    raw = path.read_bytes()
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            content = raw.decode(enc)
            break
        except (UnicodeDecodeError, ValueError):
            continue
    else:
        content = raw.decode("utf-8", errors="replace")
    if len(content) > MAX_COMPONENT_LENGTH:
        content = content[:MAX_COMPONENT_LENGTH] + "\n...[truncated]"
    return content


def build_stable_prefix(
    base_dir: Path,
    skill_registry: Any = None,
) -> str:
    """构建 Zone 1 + Zone 2 稳定前缀。

    Args:
        base_dir: 项目根目录
        skill_registry: SkillRegistry 实例，用于生成精简技能摘要

    Returns:
        系统提示的稳定部分，workspace 文件不变时输出逐字符一致。
    """
    # Zone 1: 极稳定层
    soul = _read_component(base_dir / "workspace" / "SOUL.md")
    identity = _read_component(base_dir / "workspace" / "IDENTITY.md")
    user_profile = _read_component(base_dir / "workspace" / "USER.md")

    # Zone 2: 低频变化层
    agents_guide = _read_component(base_dir / "workspace" / "AGENTS.md")

    if skill_registry is not None:
        skills_snapshot = skill_registry.build_compact_snapshot()
    else:
        skills_snapshot = ""

    return _TEMPLATE.format(
        soul=soul,
        identity=identity,
        user_profile=user_profile,
        agents_guide=agents_guide,
        skills_snapshot=skills_snapshot,
    )


def build_system_prompt(base_dir: Path, rag_mode: bool = False) -> str:
    """向后兼容的入口：等价于 build_stable_prefix()。

    rag_mode 参数已无实际作用（MEMORY.md 全文注入已移除），
    保留签名以兼容现有调用方。
    """
    return build_stable_prefix(base_dir)


def build_dynamic_prefix(
    memory_context: str = "",
    task_state: str = "",
    has_active_steps: bool = False,
) -> str:
    """构建 Zone 3 动态前缀（每次请求实时更新）。

    Zone 3 的内容不在系统提示的稳定前缀中，而是由 agent.py 在请求时
    通过 SystemMessage 注入在当前用户消息之前。

    Args:
        memory_context: UnifiedMemoryRetriever.format_for_injection() 的输出
        task_state: 任务状态 Markdown（format_task_state 的输出）
        has_active_steps: 是否存在 in_progress 步骤，控制 update_task 指引注入

    Returns:
        Zone 3 动态内容字符串。所有参数为空时返回空字符串。
    """
    parts: list[str] = []
    if memory_context:
        parts.append(f"<!-- Zone 3: Dynamic -->\n{memory_context}")
    if task_state:
        parts.append(task_state)
    if has_active_steps:
        parts.append(_TASK_UPDATE_GUIDANCE)
    return "\n\n".join(parts)


_TASK_UPDATE_GUIDANCE = """<!-- Task Update Guidance -->
**任务状态更新指引**：你可以在完成关键操作后调用 `update_task` 工具更新任务进度。可用操作：
- `add_step` description="步骤描述" — 添加新步骤
- `update_step` step_index=N status="completed|in_progress|blocked" result_summary="结果" — 更新步骤状态
- `add_artifact` path="文件路径" — 记录已创建/修改的文件
- `add_blocker` description="阻塞描述" — 记录阻塞项
- `add_decision` description="决策描述" — 记录关键决策"""
