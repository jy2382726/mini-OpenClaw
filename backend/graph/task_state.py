"""TaskState — 任务状态数据结构与格式化。

提供多步任务追踪能力：当用户发送包含任务性动词的消息时，
系统自动创建 TaskState，跟踪目标、步骤、产物、决策和阻塞项。

通过 state_schema 嵌入 Agent 状态，与 middleware 同时使用。
TaskState 作为独立结构保留，不参与 SummarizationMiddleware 的摘要过程。
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from typing import TypedDict, NotRequired


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


# 任务性动词模式（中文 + 英文），用于自动检测
_TASK_VERB_PATTERNS = [
    # 中文
    r"帮我[做写创构建开设计实配部]",
    r"创建",
    r"实现",
    r"开发",
    r"构建",
    r"设计",
    r"配置",
    r"部署",
    r"修复",
    r"重构",
    r"优化",
    r"添加",
    r"完成",
    r"编写",
    r"安装",
    r"设置",
    r"处理",
    r"解决",
    # 英文
    r"\b(create|build|implement|develop|design|deploy|fix|refactor|"
    r"optimize|add|setup|configure|write|install|solve)\b",
]


class TaskStep(TypedDict):
    """任务步骤。"""

    description: str
    status: str  # StepStatus value
    result_summary: NotRequired[str]


class TaskState(TypedDict):
    """任务状态。"""

    session_id: str
    goal: str
    steps: list[dict[str, Any]]
    artifacts: list[str]
    decisions: list[str]
    blockers: list[str]


class AgentCustomState(TypedDict, total=False):
    """Agent 自定义状态，通过 state_schema 嵌入。"""

    task_state: dict | None
    context_type: str


def is_task_message(message: str) -> bool:
    """检测用户消息是否包含任务性动词。"""
    msg_lower = message.lower()
    return any(re.search(p, msg_lower) for p in _TASK_VERB_PATTERNS)


def create_task_state(session_id: str, goal: str) -> TaskState:
    """创建初始 TaskState。"""
    return TaskState(
        session_id=session_id,
        goal=goal,
        steps=[],
        artifacts=[],
        decisions=[],
        blockers=[],
    )


def format_task_state(state: TaskState) -> str:
    """将 TaskState 格式化为 Markdown，用于 SystemMessage 注入。

    格式：目标 + 步骤列表（含状态图标）+ 产物列表 + 阻塞项。
    """
    parts: list[str] = []

    # 目标
    parts.append(f"## 当前任务\n**目标**: {state['goal']}")

    # 步骤列表
    steps = state.get("steps", [])
    if steps:
        parts.append("\n**步骤**:")
        for i, step in enumerate(steps, 1):
            icon = _status_icon(step.get("status", "pending"))
            desc = step.get("description", "")
            line = f"{i}. {icon} {desc}"
            summary = step.get("result_summary")
            if summary:
                line += f" → {summary}"
            parts.append(line)

    # 产物
    artifacts = state.get("artifacts", [])
    if artifacts:
        parts.append(f"\n**已创建文件**: {', '.join(artifacts)}")

    # 阻塞项
    blockers = state.get("blockers", [])
    if blockers:
        parts.append("\n**阻塞项**:")
        for b in blockers:
            parts.append(f"- ⚠️ {b}")

    return "\n".join(parts)


def _status_icon(status: str) -> str:
    """状态图标映射。"""
    return {
        StepStatus.PENDING: "⬜",
        StepStatus.IN_PROGRESS: "🔄",
        StepStatus.COMPLETED: "✅",
        StepStatus.BLOCKED: "🚫",
    }.get(status, "⬜")


# ── update_task 操作 ──────────────────────────────────────────

_VALID_ACTIONS = {"add_step", "update_step", "add_artifact", "add_blocker", "add_decision"}


def apply_task_update(task_state: TaskState, action: str, **kwargs) -> str:
    """对 TaskState 执行指定操作，返回操作结果描述。

    Args:
        task_state: 待更新的 TaskState（就地修改）。
        action: 操作类型，支持 add_step / update_step / add_artifact / add_blocker / add_decision。
        **kwargs: 各操作所需参数。

    Returns:
        操作结果描述字符串。
    """
    if action not in _VALID_ACTIONS:
        return f"❌ 未知操作 '{action}'，可用: {', '.join(sorted(_VALID_ACTIONS))}"

    if action == "add_step":
        return _add_step(task_state, **kwargs)
    elif action == "update_step":
        return _update_step(task_state, **kwargs)
    elif action == "add_artifact":
        return _add_artifact(task_state, **kwargs)
    elif action == "add_blocker":
        return _add_blocker(task_state, **kwargs)
    elif action == "add_decision":
        return _add_decision(task_state, **kwargs)

    return ""  # unreachable


def _add_step(task_state: TaskState, *, description: str = "", **_kw) -> str:
    """添加新步骤到 steps 列表。"""
    desc = description.strip()
    if not desc:
        return "❌ add_step 需要 description 参数"

    task_state.setdefault("steps", []).append({
        "description": desc,
        "status": StepStatus.IN_PROGRESS,
    })
    step_idx = len(task_state["steps"])
    return f"✅ 步骤 {step_idx} 已添加: {desc}"


def _update_step(
    task_state: TaskState,
    *,
    step_index: int = -1,
    status: str = "",
    result_summary: str = "",
    **_kw,
) -> str:
    """更新已有步骤的 status 和/或 result_summary。"""
    steps = task_state.get("steps", [])
    if not steps:
        return "❌ 当前无步骤可更新"

    idx = step_index
    if idx < 0 or idx >= len(steps):
        return f"❌ step_index={idx} 越界，当前共 {len(steps)} 个步骤（索引 0-{len(steps)-1}）"

    step = steps[idx]
    if status:
        valid = {s.value for s in StepStatus}
        if status not in valid:
            return f"❌ 无效 status '{status}'，可用: {', '.join(sorted(valid))}"
        step["status"] = status
    if result_summary:
        step["result_summary"] = result_summary

    summary_part = f" → {result_summary}" if result_summary else ""
    return f"✅ 步骤 {idx + 1} 已更新: {step['description']} [{step['status']}{summary_part}]"


def _add_artifact(task_state: TaskState, *, path: str = "", **_kw) -> str:
    """添加已创建/修改的文件路径到 artifacts。"""
    p = path.strip()
    if not p:
        return "❌ add_artifact 需要 path 参数"
    task_state.setdefault("artifacts", []).append(p)
    return f"✅ 已记录产物: {p}"


def _add_blocker(task_state: TaskState, *, description: str = "", **_kw) -> str:
    """添加阻塞项到 blockers。"""
    desc = description.strip()
    if not desc:
        return "❌ add_blocker 需要 description 参数"
    task_state.setdefault("blockers", []).append(desc)
    return f"✅ 已记录阻塞项: {desc}"


def _add_decision(task_state: TaskState, *, description: str = "", **_kw) -> str:
    """添加关键决策记录到 decisions。"""
    desc = description.strip()
    if not desc:
        return "❌ add_decision 需要 description 参数"
    task_state.setdefault("decisions", []).append(desc)
    return f"✅ 已记录决策: {desc}"
