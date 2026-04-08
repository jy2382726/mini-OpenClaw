# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 语言要求

**全程使用中文。** 所有回复、代码注释、文档编写、提交信息（commit message）均使用中文。例外：代码中的变量名、函数名、类名等标识符保持英文。

## Project Overview

mini-OpenClaw is a lightweight AI Agent dialogue system with tool calling, skill expansion, long-term memory, and RAG retrieval. It serves as a development reference for building AI Agent systems.

**Tech stack**: Python FastAPI backend + Next.js 14 frontend. Agent powered by LangChain/LangGraph with DashScope Qwen LLM. RAG uses LlamaIndex with DashScope/OpenAI embeddings. Communication via SSE.

## Build & Run Commands

### Backend (from `backend/`)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app:app --reload --host 0.0.0.0 --port 8002
```

### Frontend (from `frontend/`)
```bash
npm install
npm run dev -- --host 0.0.0.0 --port 3000
npm run lint
npm run build
```

### One-click startup
```bash
./scripts/start-macos-linux.sh          # Linux/macOS
BACKEND_PORT=9000 FRONTEND_PORT=4000 ./scripts/start-macos-linux.sh  # custom ports
```

## Architecture

### Backend (`backend/`)

- **`app.py`** — FastAPI entry point. On startup: scans skills, initializes agent manager, builds RAG memory index. All API routers mounted under `/api`.
- **`config.py`** — JSON-based config persistence (`config.json`). Manages LLM, embedding, RAG, and compression settings. API keys stored here (`.env` as fallback via env vars).
- **`graph/agent.py`** — `AgentManager` singleton. Initializes LLM (ChatOpenAI for DashScope compatible mode), loads tools, handles streaming invocation via LangGraph.
- **`graph/prompt_builder.py`** — Builds 6-layer system prompt from workspace Markdown files (SOUL → IDENTITY → USER → AGENTS → MEMORY → skills).
- **`graph/session_manager.py`** — Session persistence to `sessions/` directory as JSON. Handles history compression.
- **`graph/memory_indexer.py`** — RAG vector index using LlamaIndex over `memory/MEMORY.md`. Rebuilds index on startup.
- **`api/`** — REST routes: `chat.py` (SSE streaming), `sessions.py`, `files.py`, `compress.py`, `config_api.py`, `tokens.py`, `eval_api.py`, `skills_api.py`.
- **`tools/`** — Sandboxed agent tools: terminal, python_repl, fetch_url, read_file, search_knowledge, skills_scanner. Tools are auto-registered via `tools/__init__.py`.
- **`workspace/`** — Agent personality config as Markdown: `SOUL.md`, `IDENTITY.md`, `USER.md`, `AGENTS.md`.
- **`skills/`** — Extensible skill library. Each skill is a subdirectory with a `SKILL.md` file (frontmatter: name + description). Auto-scanned at startup.

### Frontend (`frontend/`)

- **`src/app/`** — Next.js App Router pages.
- **`src/components/chat/`** — Chat UI components (message bubbles, tool call visualization, input).
- **`src/components/editor/`** — Monaco Editor integration for editing config files in-browser.
- **`src/components/layout/`** — Layout shell (sidebar, settings panel).
- **`src/lib/`** — State management (Zustand or React context) and API client for backend.

## Key Design Patterns

- **Markdown-as-config**: All agent personality, skills, and memory are human-readable Markdown files, not database records.
- **6-layer system prompt**: Dynamically assembled from workspace files + active skills on each request. See `prompt_builder.py`.
- **Dual memory modes**: Traditional file injection (MEMORY.md into prompt) vs RAG vector retrieval (LlamaIndex). Toggled via config.
- **SSE event stream**: Chat responses stream as typed events (token, tool_start, tool_end, rag_hit, etc.) — frontend renders them progressively.
- **Skill auto-discovery**: Drop a `SKILL.md` in `skills/<name>/`, restart backend, agent learns the skill automatically.
- **LLM provider flexibility**: Uses ChatOpenAI adapter, so any OpenAI-compatible API works (DashScope, DeepSeek, OpenAI). Configured in `config.json` or env vars.
