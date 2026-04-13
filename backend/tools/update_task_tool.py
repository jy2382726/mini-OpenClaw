"""update_task 工具 — Agent 主动更新任务状态。

允许 Agent 在执行过程中更新 TaskState 的步骤、产物、决策和阻塞项。
通过 InjectedState 读取当前 state，通过 Command(update=...) 写回更新，
LangGraph 自动触发 SqliteSaver 持久化。
"""

from copy import deepcopy
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command
from pydantic import BaseModel, Field


class UpdateTaskArgs(BaseModel):
    """update_task 工具参数。"""

    action: str = Field(
        description=(
            "操作类型：add_step（添加步骤）、update_step（更新步骤状态）、"
            "add_artifact（记录产物文件）、add_blocker（记录阻塞项）、"
            "add_decision（记录关键决策）"
        )
    )
    description: str = Field(
        default="",
        description="步骤/阻塞项/决策的描述文本（add_step / add_blocker / add_decision 使用）",
    )
    step_index: int = Field(
        default=-1,
        description="要更新的步骤索引，从 0 开始（仅 update_step 使用）",
    )
    status: str = Field(
        default="",
        description="步骤状态：pending / in_progress / completed / blocked（仅 update_step 使用）",
    )
    result_summary: str = Field(
        default="",
        description="步骤结果摘要（仅 update_step 使用）",
    )
    path: str = Field(
        default="",
        description="产物文件路径（仅 add_artifact 使用）",
    )


@tool(args_schema=UpdateTaskArgs)
def update_task(
    action: str,
    state: Annotated[dict[str, Any], InjectedState()],
    runtime: ToolRuntime,
    description: str = "",
    step_index: int = -1,
    status: str = "",
    result_summary: str = "",
    path: str = "",
) -> Command:
    """更新当前任务状态。可用于：添加新步骤、更新步骤状态、
    记录已创建的文件、记录阻塞项、记录关键决策。
    请在完成关键操作后调用此工具更新任务进度。"""
    from graph.task_state import apply_task_update

    tool_msg = ToolMessage(
        content="",
        tool_call_id=runtime.tool_call_id or "",
    )

    task_state = state.get("task_state")
    if task_state is None:
        tool_msg.content = "⚠️ 当前无活跃任务，无法更新任务状态"
        return Command(update={"messages": [tool_msg]})

    # 深拷贝避免就地修改 state（LangGraph 要求返回新的 state）
    updated = deepcopy(task_state)
    result_msg = apply_task_update(
        updated,
        action,
        description=description,
        step_index=step_index,
        status=status,
        result_summary=result_summary,
        path=path,
    )

    tool_msg.content = result_msg

    # 如果操作失败（result 以 ❌ 开头），不更新 task_state，仅返回错误消息
    if result_msg.startswith("❌"):
        return Command(update={"messages": [tool_msg]})

    # 成功：写回 task_state + ToolMessage，触发 checkpointer 持久化
    return Command(
        update={"task_state": updated, "messages": [tool_msg]},
    )


def create_update_task_tool():
    """创建 update_task 工具实例。"""
    return update_task
