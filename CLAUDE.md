# CLAUDE.md

## 语言

**全程中文。** 回复、注释、文档、commit message 均用中文。例外：标识符保持英文。

## 项目

轻量级 AI Agent 对话系统，含工具调用、技能扩展、长期记忆、RAG 检索。

**技术栈**: Python FastAPI + Next.js 14 + LangChain/LangGraph + DashScope Qwen + LlamaIndex + SSE

## 构建 & 运行

```bash
# 后端 (backend/)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app:app --reload --host 0.0.0.0 --port 8002

# 前端 (frontend/)
npm install
npm run dev -- --host 0.0.0.0 --port 3000
npm run lint && npm run build

# 一键启动
./scripts/start-macos-linux.sh
BACKEND_PORT=9000 FRONTEND_PORT=4000 ./scripts/start-macos-linux.sh
```

## 架构

**后端** (`backend/`):
- `app.py` — FastAPI 入口，启动时扫描技能、初始化 Agent、构建 RAG 索引
- `config.py` — JSON 配置持久化（`config.json`），管理 LLM/Embedding/RAG/压缩设置
- `graph/agent.py` — AgentManager 单例，ChatOpenAI + LangGraph 流式调用
- `graph/prompt_builder.py` — 6 层系统提示拼接（SOUL→IDENTITY→USER→AGENTS→MEMORY→skills）
- `graph/session_manager.py` — 会话持久化（`sessions/` 目录 JSON）
- `graph/memory_indexer.py` — LlamaIndex RAG 向量索引
- `api/` — REST 路由：chat(SSE)、sessions、files、compress、config、tokens、eval、skills
- `tools/` — Agent 工具：terminal、python_repl、fetch_url、read_file、write_file、search_knowledge、mem0、skills_scanner、create_skill_version
- `workspace/` — 人设 Markdown：SOUL.md、IDENTITY.md、USER.md、AGENTS.md
- `skills/` — 技能库，每个子目录含 `SKILL.md`（frontmatter: name + description），启动时自动扫描

**前端** (`frontend/`):
- `src/app/` — Next.js App Router
- `src/components/chat/` — 聊天 UI（消息气泡、工具调用可视化、输入）
- `src/components/editor/` — Monaco Editor 配置编辑
- `src/components/layout/` — 布局（侧栏、设置面板）
- `src/lib/` — 状态管理 + API 客户端

## 关键设计模式

- **Markdown-as-config**: 人设/技能/记忆均为可读 Markdown 文件
- **6 层系统提示**: `prompt_builder.py` 动态拼接 workspace 文件 + 技能快照
- **双记忆模式**: MEMORY.md 文件注入 vs RAG 向量检索（LlamaIndex），通过配置切换
- **SSE 事件流**: 流式返回 token/tool_start/tool_end/rag_hit 等事件
- **技能自动发现**: 放置 `SKILL.md` 到 `skills/<name>/`，重启即生效
- **LLM 灵活切换**: ChatOpenAI 适配器，支持 DashScope/DeepSeek/OpenAI

## OpenSpec 工作流

### 核心纪律

1. **先读后做**: 执行 OpenSpec 命令前，先读取：
   - `openspec/config.yaml`（项目约束）
   - `openspec/specs/` 相关域规范（当前系统行为）
   - `openspec/changes/` 活跃变更（如存在）
2. **不猜测需求**: spec 中未明确定义的行为，问用户，不自行补充。
3. **out-of-scope 是红线**: proposal.md 标注为 out-of-scope 的功能，严禁实现。

### Apply 阶段规则

1. 每完成一个 tasks.md 中的 Phase，停下来。
2. 总结当前阶段变更（改了什么文件、为什么这么改）。
3. 等待用户 review 确认后，再继续下一 Phase。
4. 严禁一次性实现所有任务。

### 代码标准

- 所有组件使用 TypeScript + 函数式组件
- 样式全部使用 Tailwind CSS，禁止内联 style
- 支持暗色模式（`dark:` 前缀）
- 所有图片使用 lazy loading
- 组件文件名使用 PascalCase

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
|------|----------|
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
