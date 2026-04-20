## Why

Agent 当前缺少专用文件搜索工具，只能依赖 `terminal` 执行 `find`/`grep` 命令或手动猜测路径后用 `read_file` 读取。这导致文件定位效率低、搜索结果格式不一致、且无法利用 ripgrep 加速。LangChain 1.2 已内置 `FilesystemFileSearchMiddleware`，可直接复用，零维护成本。

## What Changes

- 新增 `glob_search` 工具：按文件名模式搜索（如 `**/*.py`），返回匹配路径列表
- 新增 `grep_search` 工具：按内容正则搜索，支持文件过滤、多种输出格式（路径/内容/计数）
- 在中间件链第 6 层注册 `FilesystemFileSearchMiddleware`（第 5 层已分配给 MemoryMiddleware）
- 在 `TOOL_OUTPUT_BUDGETS` 中为两个新工具配置输出预算
- 在 `TOOL_TIERS["always"]` 中归入两个新工具
- 在 `_has_coding_context` 中补充搜索相关关键词
- 在 `AGENTS.md` 中补充工具列表和文件搜索策略指引
- 在 `config.json` 中补充工具预算配置

## Capabilities

### New Capabilities

- `file-search-tools`: 文件搜索工具能力 — 通过 LangChain 内置中间件为 Agent 提供 glob_search（按名称）和 grep_search（按内容）两种文件检索工具

### Modified Capabilities

- `middleware-chain`: 中间件链新增第 6 层 FilesystemFileSearchMiddleware，更新工具输出预算和工具分类配置

## Impact

### 受影响文件

| 文件 | 改动类型 |
|------|---------|
| `backend/graph/agent.py` | 修改 — `_build_middleware` 新增第 6 层注册 |
| `backend/graph/middleware.py` | 修改 — BUDGETS / TIERS / 关键词 |
| `backend/workspace/AGENTS.md` | 修改 — 工具列表 + 搜索策略 |
| `backend/config.json` | 修改 — budgets 新增 |

### 不受影响

- `tools/__init__.py` — 中间件自动注册，无需修改 `get_all_tools`
- `tools/read_file_tool.py` — `lstrip("./")` 已自动处理路径格式差异，无需修改
- 前端 — 工具对前端透明，SSE 事件格式不变
- 不新增工具文件 — 直接复用 LangChain 内置实现

### 回滚方案

删除 `_build_middleware` 中的第 6 层注册即可完全回滚，无数据迁移、无 state 变更、无破坏性影响。
