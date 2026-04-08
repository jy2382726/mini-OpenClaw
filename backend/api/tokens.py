"""GET/POST /api/tokens — Token counting for sessions and files."""

from pathlib import Path
from typing import Any

import tiktoken
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from graph.session_manager import session_manager
from graph.prompt_builder import build_system_prompt

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent

# Cache the encoder instance
_encoder = tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    """Count tokens using cl100k_base encoding."""
    return len(_encoder.encode(text))


@router.get("/tokens/session/{session_id}")
async def get_session_token_count(session_id: str) -> dict[str, Any]:
    """Count tokens in a session: system prompt + all messages."""
    system_prompt = build_system_prompt(BASE_DIR)
    system_tokens = _count_tokens(system_prompt)

    messages = session_manager.load_session(session_id)
    message_tokens = 0
    for msg in messages:
        message_tokens += _count_tokens(msg.get("content", ""))

    return {
        "system_tokens": system_tokens,
        "message_tokens": message_tokens,
        "total_tokens": system_tokens + message_tokens,
    }


class FileTokenRequest(BaseModel):
    paths: list[str]


@router.post("/tokens/files")
async def get_file_token_counts(request: FileTokenRequest) -> dict[str, Any]:
    """Count tokens for a list of files."""
    results: list[dict[str, Any]] = []
    for rel_path in request.paths:
        normalized = rel_path.replace("\\", "/").lstrip("./")
        full_path = (BASE_DIR / normalized).resolve()
        if not str(full_path).startswith(str(BASE_DIR)):
            results.append({"path": rel_path, "tokens": 0})
            continue
        if not full_path.exists():
            results.append({"path": rel_path, "tokens": 0})
            continue
        try:
            content = full_path.read_text(encoding="utf-8")
            tokens = _count_tokens(content)
            results.append({"path": rel_path, "tokens": tokens})
        except Exception:
            results.append({"path": rel_path, "tokens": 0})

    return {"files": results}
