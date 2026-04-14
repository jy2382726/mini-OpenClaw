"""CheckpointHistoryService — 从 checkpoint 投影到前端 UI DTO。

将 LangGraph 内部的 message 格式（HumanMessage、AIMessage、ToolMessage）
转换为前端消费的 DTO 格式（role/content/tool_calls）。

两个投影服务：
- CheckpointHistoryService：面向 /history 端点（UI 气泡展示）
- CheckpointDebugViewService：面向 /messages 端点（Raw Messages 调试视图）
"""

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from graph.prompt_builder import build_system_prompt


class CheckpointHistoryService:
    """从 checkpoint 读取最新 state，投影为前端 history DTO。

    前端 /history 端点期望的格式：
    [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "...", "tool_calls": [{"tool": "terminal", "input": "...", "output": "..."}]},
        ...
    ]

    tool_calls 格式为 {tool, input?, output?}（与 JSON 保存格式一致），
    而非 LangChain 的 {id, name, args} 格式。
    """

    def __init__(self, checkpointer) -> None:
        self._checkpointer = checkpointer

    async def project(self, thread_id: str) -> list[dict[str, Any]]:
        """从 checkpoint 投影消息列表。

        Args:
            thread_id: LangGraph 线程 ID（等于 session_id）

        Returns:
            前端 DTO 列表，无 checkpoint 时返回空列表。
        """
        messages = await self._load_messages(thread_id)
        if not messages:
            return []
        return self._project_to_dto(messages)

    async def _load_messages(self, thread_id: str) -> list:
        """从 checkpoint 加载最新 state 的 messages。"""
        config = {"configurable": {"thread_id": thread_id}}
        try:
            tuple_result = await self._checkpointer.aget_tuple(config)
            if tuple_result is None or tuple_result.checkpoint is None:
                return []
            channel_values = tuple_result.checkpoint.get("channel_values", {})
            if isinstance(channel_values, dict):
                return channel_values.get("messages", [])
            return []
        except Exception:
            return []

    def _project_to_dto(self, messages: list) -> list[dict[str, Any]]:
        """投影 LangChain messages → 前端 DTO 格式。

        处理规则：
        - HumanMessage → {"role": "user", "content": ...}
        - AIMessage → {"role": "assistant", "content": ..., "tool_calls": [...]}
          - tool_calls 挂接对应的 ToolMessage output
        - ToolMessage → 挂接到前一个 AIMessage 的 tool_calls 中
        - SystemMessage → 跳过（debug 视图单独处理）
        - 连续 AIMessage 作为独立 DTO 输出
        - 仅工具调用无文本的 AIMessage：content=""
        """
        result: list[dict[str, Any]] = []
        # 用于挂接 ToolMessage output 到对应 AIMessage 的 tool_calls
        pending_tool_calls: dict[str, dict[str, Any]] = {}

        for msg in messages:
            if isinstance(msg, SystemMessage):
                # 跳过 system prompt（由 DebugView 单独处理）
                continue
            elif isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                dto: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    dto["tool_calls"] = []
                    for tc in msg.tool_calls:
                        tc_dto: dict[str, Any] = {
                            "tool": tc["name"],
                            "input": _serialize_args(tc.get("args", {})),
                        }
                        # 暂存，等 ToolMessage 到来后填入 output
                        pending_tool_calls[tc["id"]] = tc_dto
                        dto["tool_calls"].append(tc_dto)
                result.append(dto)
            elif isinstance(msg, ToolMessage):
                # 挂接到对应 tool_call 的 output
                tc_id = msg.tool_call_id
                if tc_id in pending_tool_calls:
                    pending_tool_calls[tc_id]["output"] = (
                        msg.content if isinstance(msg.content, str) else str(msg.content)
                    )
                # 注意：ToolMessage 不作为独立 DTO 输出，
                # 而是挂接到对应 AIMessage 的 tool_calls.output 中

        return result


class CheckpointDebugViewService:
    """从 checkpoint 投影调试视图（Raw Messages）。

    组装 system_prompt + 投影消息列表，标注 is_approximation。
    用于 /messages 端点。
    """

    def __init__(self, checkpointer) -> None:
        self._checkpointer = checkpointer

    async def project(
        self, thread_id: str, base_dir: Path
    ) -> dict[str, Any]:
        """投影调试视图。

        Returns:
            {
                "title": "...",
                "messages": [{"role": "system", "content": "..."}, ...],
                "is_approximation": true
            }
        """
        history_service = CheckpointHistoryService(self._checkpointer)
        dto_messages = await history_service.project(thread_id)

        # 组装 system prompt（近似值，实际运行时可能有动态 Zone 3 内容）
        system_prompt = build_system_prompt(base_dir)

        all_messages = [{"role": "system", "content": system_prompt}] + dto_messages

        return {
            "title": "",  # 标题从 SessionRepository 获取，不由投影层负责
            "messages": all_messages,
            "is_approximation": True,
        }


def _serialize_args(args: Any) -> str:
    """将 tool_call args 序列化为字符串（与 JSON 保存格式一致）。"""
    if isinstance(args, str):
        return args
    if isinstance(args, dict):
        # 对于 terminal 等工具，args 通常只有一个参数如 {"command": "ls"}
        # 前端期望 input 是字符串
        if len(args) == 1:
            return str(list(args.values())[0])
        import json
        return json.dumps(args, ensure_ascii=False)
    return str(args)
