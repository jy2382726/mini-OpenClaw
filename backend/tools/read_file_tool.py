"""ReadFileTool — sandboxed file reading within project directory."""

from pathlib import Path
from typing import Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


class ReadFileInput(BaseModel):
    file_path: str = Field(
        description="Relative path of the file to read (relative to project root)"
    )


class SandboxedReadFileTool(BaseTool):
    name: str = "read_file"
    description: str = (
        "Read the content of a local file. Path is relative to the project root. "
        "Use this to read SKILL.md files, MEMORY.md, configuration files, etc. "
        "Example: read_file('skills/get_weather/SKILL.md')"
    )
    args_schema: Type[BaseModel] = ReadFileInput
    root_dir: str = ""

    def _run(self, file_path: str) -> str:
        try:
            root = Path(self.root_dir)
            # Normalize path
            normalized = file_path.replace("\\", "/").lstrip("./")
            full_path = (root / normalized).resolve()

            # Sandbox check
            if not str(full_path).startswith(str(root.resolve())):
                return f"❌ Access denied: path escapes project root"

            if not full_path.exists():
                return f"❌ File not found: {file_path}"

            if not full_path.is_file():
                return f"❌ Not a file: {file_path}"

            content = full_path.read_text(encoding="utf-8")
            if len(content) > 10000:
                content = content[:10000] + "\n...[truncated]"
            return content

        except Exception as e:
            return f"❌ Error reading file: {str(e)}"


def create_read_file_tool(base_dir: Path) -> SandboxedReadFileTool:
    return SandboxedReadFileTool(root_dir=str(base_dir))
