# glob/grep 工具集成提案

> 基于 `2026-04-19-glob-grep-tool-design.md` 设计文档，经代码审核验证后生成的实施提案。
> 审核修正：路径格式差异为伪问题（read_file_tool.py:30 已自动处理）；中间件层级调整为第 6 层。

---

## 实施步骤

### Step 1：注册 FilesystemFileSearchMiddleware（agent.py）

在 `_build_middleware()` 方法中新增第 6 层：

```python
# 第 6 层：文件搜索工具（glob_search + grep_search）
if self._base_dir:
    from langchain.agents.middleware import FilesystemFileSearchMiddleware
    middleware.append(
        FilesystemFileSearchMiddleware(
            root_path=str(self._base_dir),
            use_ripgrep=True,
            max_file_size_mb=10,
        )
    )
```

**文件**：`backend/graph/agent.py`（`_build_middleware` 方法）
**验证**：启动后端，发送消息触发 Agent，确认无 import 或注册报错

### Step 2：工具输出预算（middleware.py）

在 `TOOL_OUTPUT_BUDGETS` 中新增 2 个工具：

```python
"glob_search": 1500,     # 路径列表
"grep_search": 2500,     # 匹配结果（含行号+内容）
```

**文件**：`backend/graph/middleware.py`
**验证**：`grep_search` 返回大量结果时确认截断生效

### Step 3：工具过滤 tier（middleware.py）

在 `TOOL_TIERS["always"]` 中新增：

```python
"always": ["read_file", "search_knowledge", "glob_search", "grep_search"],
```

**文件**：`backend/graph/middleware.py`
**验证**：发送非编程类消息时，确认 glob_search/grep_search 仍在可用工具列表中

### Step 4：编码上下文关键词（middleware.py）

在 `_has_coding_context` 中补充搜索关键词：

```python
"搜索文件", "查找文件", "搜索代码", "查找代码", "glob", "grep",
"find file", "search code",
```

**文件**：`backend/graph/middleware.py`
**验证**：发送"帮我搜索代码中所有 TODO"时确认 coding 上下文被识别

### Step 5：AGENTS.md 提示词更新

**5a. 工具列表新增**（在 `read_file` 之前插入）：

```markdown
4. **glob_search**: 按文件名模式搜索文件（如 `**/*.py`），返回匹配的文件路径列表
5. **grep_search**: 按内容搜索文件（支持正则、文件过滤、多种输出格式）
```

后续工具编号顺延。

**5b. 新增文件搜索策略**（工具列表之后、工具调用铁律之前）：

```markdown
### 文件搜索策略

- **已知文件路径**：直接使用 `read_file` 读取
- **不确定文件路径**：先用 `glob_search` 按文件名模式定位文件，再用 `read_file` 读取
- **搜索代码内容**：使用 `grep_search`
  - 只想知道哪些文件包含某关键词：`grep_search("pattern", output_mode="files_with_matches")`
  - 需要查看匹配行：`grep_search("pattern", output_mode="content")`
  - 限定文件类型：`grep_search("pattern", include="*.py")`
```

**注意**：不需要"去掉前导 `/`"的提示。`read_file` 内部 `lstrip("./")` 已自动处理路径格式。

**文件**：`backend/workspace/AGENTS.md`
**验证**：重启后端，发送搜索类消息，确认 Agent 正确使用 glob_search/grep_search

### Step 6：config.json 预算配置

在 `middleware.tool_output_budget.budgets` 中新增：

```json
"glob_search": 1500,
"grep_search": 2500
```

**文件**：`backend/config.json`
**验证**：配置加载无报错

---

## 改动范围总览

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `backend/graph/agent.py` | 修改 | `_build_middleware` 新增第 6 层 |
| `backend/graph/middleware.py` | 修改 | BUDGETS + TIERS + 关键词 |
| `backend/workspace/AGENTS.md` | 修改 | 工具列表 + 搜索策略 |
| `backend/config.json` | 修改 | budgets 新增 2 个工具 |

### 不改动的部分

| 组件 | 原因 |
|------|------|
| `tools/__init__.py` | 中间件自动注册工具 |
| `tools/read_file_tool.py` | 路径格式已由 `lstrip("./")` 自动处理，无需修改 |
| 新增工具文件 | 直接复用 LangChain 内置实现 |
| 前端 | 工具对前端透明 |
