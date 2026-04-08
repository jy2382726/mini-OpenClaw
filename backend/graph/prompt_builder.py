"""Prompt Builder — Assemble system prompt from 6 Markdown files."""

from pathlib import Path

MAX_COMPONENT_LENGTH = 20000


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


RAG_GUIDANCE = """注意：长期记忆(MEMORY.md)已切换为RAG检索模式。
系统会根据用户的问题自动检索相关记忆片段并注入上下文。
如果检索到了相关记忆，它们会以"[记忆检索结果]"标记呈现在你的上下文中。"""


def build_system_prompt(base_dir: Path, rag_mode: bool = False) -> str:
    """Build the full system prompt by concatenating components in order.

    Order:
    1. SKILLS_SNAPSHOT.md — available skills listing
    2. workspace/SOUL.md — persona, tone, boundaries
    3. workspace/IDENTITY.md — name, style, emoji
    4. workspace/USER.md — user profile
    5. workspace/AGENTS.md — operation instructions & memory/skill protocols
    6. memory/MEMORY.md — cross-session long-term memory (skipped in RAG mode)

    When rag_mode=True, MEMORY.md is excluded and a RAG guidance note is appended.
    """
    components = [
        ("Skills Snapshot", base_dir / "SKILLS_SNAPSHOT.md"),
        ("Soul", base_dir / "workspace" / "SOUL.md"),
        ("Identity", base_dir / "workspace" / "IDENTITY.md"),
        ("User Profile", base_dir / "workspace" / "USER.md"),
        ("Agents Guide", base_dir / "workspace" / "AGENTS.md"),
    ]

    # Only include MEMORY.md when not in RAG mode
    if not rag_mode:
        components.append(
            ("Long-term Memory", base_dir / "memory" / "MEMORY.md")
        )

    parts: list[str] = []
    for label, path in components:
        content = _read_component(path)
        if content:
            parts.append(f"<!-- {label} -->\n{content}")

    # Append RAG guidance when in RAG mode
    if rag_mode:
        parts.append(f"<!-- RAG Mode -->\n{RAG_GUIDANCE}")

    return "\n\n".join(parts)
