"""mem0 Agent 工具 — save_memory, search_memories, verify_memory。

让 Agent 在对话中主动读写结构化记忆。
"""

from pathlib import Path
from typing import Optional

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


class SaveMemoryInput(BaseModel):
    """保存记忆的输入参数。"""
    fact: str = Field(description="要记住的事实或规则（一句话概括）")
    memory_type: str = Field(
        description="记忆类型：user（用户偏好）、feedback（行为反馈）、project（项目上下文）、reference（外部引用）"
    )
    why: str = Field(default="", description="为什么记住这条（具体的事件或对话上下文）")
    how_to_apply: str = Field(default="", description="在什么场景下应该应用这条记忆")


class SearchMemoriesInput(BaseModel):
    """搜索记忆的输入参数。"""
    query: str = Field(description="搜索关键词或语义描述")
    limit: int = Field(default=5, description="最大返回数量")


class VerifyMemoryInput(BaseModel):
    """验证记忆的输入参数。"""
    memory_id: str = Field(description="要验证的记忆 ID")


def create_save_memory_tool(base_dir: Path) -> BaseTool:
    """创建 save_memory 工具。"""

    class SaveMemoryTool(BaseTool):
        name: str = "save_memory"
        description: str = (
            "保存一条结构化长期记忆。当对话中出现值得长期记住的信息时使用。"
            "必须提供 fact（事实内容）、memory_type（类型标签）、why（原因）、how_to_apply（适用场景）。"
            "类型说明：user=用户偏好、feedback=行为反馈、project=项目上下文、reference=外部引用。"
        )
        args_schema: type[BaseModel] = SaveMemoryInput
        _base_dir: Path = base_dir

        def _run(self, fact: str, memory_type: str, why: str = "", how_to_apply: str = "") -> str:
            try:
                from graph.mem0_manager import get_mem0_manager
                mgr = get_mem0_manager(self._base_dir)
                if not mgr.is_ready:
                    return "错误：mem0 记忆系统未启用"
                result = mgr.add_structured(
                    fact=fact,
                    memory_type=memory_type,
                    why=why,
                    how_to_apply=how_to_apply,
                )
                if result:
                    return f"已保存记忆：[{memory_type}] {fact}"
                return "保存失败"
            except Exception as e:
                return f"保存记忆失败: {e}"

    return SaveMemoryTool()


def create_search_memories_tool(base_dir: Path) -> BaseTool:
    """创建 search_memories 工具。"""

    class SearchMemoriesTool(BaseTool):
        name: str = "search_memories"
        description: str = (
            "搜索长期记忆库中的相关记忆。返回匹配的记忆列表，"
            "包含内容、类型、来源时间和置信度。"
        )
        args_schema: type[BaseModel] = SearchMemoriesInput
        _base_dir: Path = base_dir

        def _run(self, query: str, limit: int = 5) -> str:
            try:
                from graph.mem0_manager import get_mem0_manager
                from config import get_mem0_config
                mgr = get_mem0_manager(self._base_dir)
                if not mgr.is_ready:
                    return "错误：mem0 记忆系统未启用"
                cfg = get_mem0_config()
                results = mgr.search(query, user_id=cfg.get("user_id", "default"), limit=limit)
                if not results:
                    return "未找到相关记忆"
                lines = []
                for i, item in enumerate(results):
                    meta = item.get("metadata", {})
                    created = item.get("created_at") or meta.get("created_at", "N/A")
                    lines.append(
                        f"{i+1}. [{meta.get('memory_type', '?')}] {item.get('memory', '')}"
                        f" (置信度: {meta.get('confidence', 'N/A')}, "
                        f"记录于: {created[:10] if created != 'N/A' else 'N/A'})"
                    )
                return "\n".join(lines)
            except Exception as e:
                return f"搜索记忆失败: {e}"

    return SearchMemoriesTool()


def create_verify_memory_tool(base_dir: Path) -> BaseTool:
    """创建 verify_memory 工具。"""

    class VerifyMemoryTool(BaseTool):
        name: str = "verify_memory"
        description: str = (
            "验证某条记忆仍然有效。当你在对话中确认某条记忆中的信息仍然正确时使用。"
            "这会更新记忆的验证时间和置信度。"
        )
        args_schema: type[BaseModel] = VerifyMemoryInput
        _base_dir: Path = base_dir

        def _run(self, memory_id: str) -> str:
            try:
                from graph.mem0_manager import get_mem0_manager
                mgr = get_mem0_manager(self._base_dir)
                if not mgr.is_ready:
                    return "错误：mem0 记忆系统未启用"
                if mgr.verify_memory(memory_id):
                    return f"记忆 {memory_id} 已验证有效"
                return f"验证失败：记忆 {memory_id} 不存在或操作出错"
            except Exception as e:
                return f"验证记忆失败: {e}"

    return VerifyMemoryTool()


def create_mem0_tools(base_dir: Path) -> list[BaseTool]:
    """创建所有 mem0 相关工具。"""
    return [
        create_save_memory_tool(base_dir),
        create_search_memories_tool(base_dir),
        create_verify_memory_tool(base_dir),
    ]
