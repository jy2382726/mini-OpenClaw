"""Core Tools factory — returns all 7 tools for the Agent."""

from pathlib import Path
from typing import List

from langchain_core.tools import BaseTool

from .terminal_tool import create_terminal_tool
from .python_repl_tool import create_python_repl_tool
from .fetch_url_tool import create_fetch_url_tool
from .read_file_tool import create_read_file_tool
from .write_file_tool import create_write_file_tool
from .search_knowledge_tool import create_search_knowledge_tool
from .create_skill_version_tool import create_skill_version_tool


def get_all_tools(base_dir: Path) -> List[BaseTool]:
    """Create and return all 7 core tools, sandboxed to base_dir."""
    tools = [
        create_terminal_tool(base_dir),
        create_python_repl_tool(),
        create_fetch_url_tool(),
        create_read_file_tool(base_dir),
        create_write_file_tool(base_dir),
        create_search_knowledge_tool(base_dir),
        create_skill_version_tool(base_dir),
    ]

    # 条件注册 mem0 工具（仅当 mem0 启用时）
    try:
        from config import get_mem0_config
        mem0_cfg = get_mem0_config()
        if mem0_cfg.get("enabled"):
            from .mem0_tool import create_mem0_tools
            tools.extend(create_mem0_tools(base_dir))
    except Exception:
        pass  # mem0 未安装或配置读取失败，跳过

    return tools
