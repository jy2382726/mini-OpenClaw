## Context

Agent 当前通过 `terminal` 工具执行 `find`/`grep` 命令进行文件搜索，存在以下问题：
- 搜索结果格式不统一，依赖 LLM 构造正确的 shell 命令
- 无法利用 ripgrep 加速（terminal 有 30s 超时和 5000 字符截断）
- 搜索和读取是连续操作，但缺少专用工具链引导

LangChain 1.2 内置 `FilesystemFileSearchMiddleware`（`langchain/agents/middleware/file_search.py`），提供 `glob_search` + `grep_search` 两个工具，与项目 `create_agent` 中间件体系完全兼容。

当前中间件链为 4 层（截断→摘要→工具过滤→限流），记忆中间件重构后为 5 层。本变更在第 6 层追加。

## Goals / Non-Goals

**Goals:**

- 复用 LangChain 内置 `FilesystemFileSearchMiddleware`，为零维护成本地为 Agent 新增文件搜索能力
- 将搜索工具归入 `TOOL_TIERS["always"]`，确保任何场景下都可搜索
- 通过 `TOOL_OUTPUT_BUDGETS` 控制搜索结果体积，防止大量匹配占满上下文

**Non-Goals:**

- 不自行实现搜索工具（LangChain 已有完整实现，含 ripgrep 加速和 Python fallback）
- 不修改 `read_file` 工具（`lstrip("./")` 已自动处理路径格式差异）
- 不新增工具文件
- 不涉及前端改动

## Decisions

### 决策 1：使用中间件路径 A（作为中间件挂载）

**选择**：通过 `_build_middleware()` 将 `FilesystemFileSearchMiddleware` 作为第 6 层注册

**备选**：路径 B（仅提取 `fs_middleware.tools` 手动添加到 tools 列表）

**理由**：
- 与现有中间件链风格一致（4 层都是中间件注册）
- 工具通过 `self.tools` 自动注册，无需修改 `get_all_tools`
- 框架升级时自动获得 bug 修复和性能优化

### 决策 2：工具归入 `always` tier

**选择**：`glob_search` 和 `grep_search` 放入 `TOOL_TIERS["always"]`

**备选**：放入 `coding` tier（仅编程场景可用）

**理由**：
- 文件搜索是 Agent 的基础能力（搜索配置、查找文档、定位技能文件等场景均非编程类）
- 搜索和读取是连续操作，`read_file` 已在 `always` 中

### 决策 3：不做路径格式代码转换

**选择**：依赖 `read_file_tool.py:30` 的 `lstrip("./")` 自动处理

**备选**：在搜索结果中去除前导 `/`，或修改 `read_file` description

**理由**：
- 代码层面已完全处理（`file_path.replace("\\", "/").lstrip("./")`）
- 无需在工具描述中增加引导信息

### 组件层级图

```
AgentManager._build_agent()
  │
  ├─ tools: get_all_tools()
  │   ├─ terminal
  │   ├─ python_repl
  │   ├─ read_file
  │   ├─ write_file
  │   ├─ fetch_url
  │   ├─ search_knowledge
  │   ├─ mem0_tools (条件注册)
  │   └─ ...
  │
  └─ middleware: _build_middleware()
      ├─ 第 1 层：ToolOutputBudgetMiddleware    ← glob_search/grep_search 预算在此配置
      ├─ 第 2 层：SummarizationMiddleware
      ├─ 第 3 层：ContextAwareToolFilter       ← always tier 保证搜索工具始终可用
      ├─ 第 4 层：ToolCallLimitMiddleware
      ├─ 第 5 层：MemoryMiddleware (规划中)
      └─ 第 6 层：FilesystemFileSearchMiddleware ← 新增：自动注册 glob_search + grep_search
```

### 工具调用流

```
用户："帮我找所有包含 TODO 的 Python 文件"
  │
  ├─ ContextAwareToolFilter: glob_search/grep_search 在 always tier → 保持可用
  │
  ├─ Agent 选择 grep_search
  │   └─ grep_search("TODO", include="*.py", output_mode="files_with_matches")
  │       └─ FilesystemFileSearchMiddleware → ripgrep 或 Python fallback
  │           └─ 返回 "/backend/graph/agent.py\n/backend/graph/middleware.py\n..."
  │
  ├─ ToolOutputBudgetMiddleware: 结果 ≤ 1500*4 字符 → 不截断
  │
  └─ Agent 可能继续调用 read_file 读取具体文件
      └─ read_file("/backend/graph/agent.py") → lstrip("./") → "backend/graph/agent.py"
```

## Risks / Trade-offs

- **[ripgrep 未安装]** → 自动 fallback 到 Python `re` 搜索，功能完整但性能较低。可通过 `apt-get install ripgrep` 可选加速。
- **[大项目搜索结果过多]** → 通过 `TOOL_OUTPUT_BUDGETS` 截断控制。grep_search 预算 2500 token（约 10000 字符），足够覆盖常见搜索场景。
- **[中间件无 `enabled` 开关]** → `FilesystemFileSearchMiddleware` 无外部依赖，无需条件注册。如需移除，删除 `_build_middleware` 中对应的注册代码即可。
