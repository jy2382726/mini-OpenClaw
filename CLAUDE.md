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
npm run dev -- --H 0.0.0.0 --port 3000
npm run lint && npm run build

# 一键启动
./scripts/start-macos-linux.sh
BACKEND_PORT=9000 FRONTEND_PORT=4000 ./scripts/start-macos-linux.sh
```

## 架构

**后端** (`backend/`): 分层架构
- `graph/` — Agent 核心（提示构建、会话管理、状态管理、中间件链）
- `tools/` — Agent 工具集（终端、文件操作、搜索、任务管理等，支持条件注册）
- `skills/` — 技能库（SKILL.md 驱动，自动发现）
- `workspace/` — 人设 Markdown
- `api/` — REST 路由

**前端** (`frontend/`):
- `src/components/` — UI 组件（chat、editor、layout）
- `src/lib/` — 状态管理 + API 客户端

## 关键设计模式

- **Markdown-as-config**: 人设/技能/记忆均为可读 Markdown 文件
- **多层系统提示动态拼接** + **SSE 事件流式传输**
- **Checkpoint 持久化** + **中间件链**（工具输出截断、自动摘要、运行时过滤、限流）
- **双记忆模式**（文件注入 vs RAG 向量检索）+ 可选长期记忆服务
- **技能自动发现**（SKILL.md 即注册）+ **LLM 适配器多后端切换**

## OpenSpec 工作流

### 核心纪律

1. **先读后做**: 执行 OpenSpec 命令前，先读取：
   - `openspec/config.yaml`（项目约束）
   - `openspec/specs/` 相关域规范（当前系统行为）
   - `openspec/changes/` 活跃变更（如存在）
2. **不猜测需求**: spec 中未明确定义的行为，问用户，不自行补充。
3. **out-of-scope 是红线**: proposal.md 标注为 out-of-scope 的功能，严禁实现。

### 分阶段交互

`/opsx:apply` 执行时严格按 Phase 推进，防止错上加错：
1. 每完成一个 Phase 停下来
2. 总结当前阶段变更（改了哪些文件、新增了什么）
3. 等待用户 review 确认后再继续下一 Phase
4. 发现偏差立即停止并说明问题

### 复用优先

- 优先使用已有组件和服务，不做推倒重来
- 新建文件前先搜索是否有可复用的模块
- 不创建重复的 utility 函数

### 命名一致性

- Change 用 domain-based naming（如 `user-auth`、`ai-chat`），不用 feature-level（如 `add-sidebar`、`fix-layout`）
- Spec 按 capability 平铺命名（如 `task-state`、`middleware-chain`、`checkpoint-projection`），同一 capability 的前后端改动放同一个 spec
- 组件 PascalCase、API 端点 kebab-case、数据库表 snake_case

### 代码标准

- 所有组件使用 TypeScript + 函数式组件
- 样式全部使用 Tailwind CSS，禁止内联 style
- 支持暗色模式（`dark:` 前缀）
- 所有图片使用 lazy loading

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
