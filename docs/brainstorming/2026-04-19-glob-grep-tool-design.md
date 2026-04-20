# glob/grep 工具设计优化方案

> 为 Agent 新增 `glob_search` / `grep_search` 文件检索工具。直接复用 LangChain 1.2.12 内置的 `FilesystemFileSearchMiddleware`，无需自行实现。

---

## 一、行业命名规范调研

| 项目 | glob 工具名 | grep 工具名 | 参数风格 | 底层引擎 |
|------|------------|------------|---------|---------|
| Claude Code | `Glob` | `Grep` | pattern / path / glob / output_mode / -A/-B/-C / head_limit | ripgrep |
| OpenCode | `glob` | `grep` | pattern / path / glob / output_mode / head_limit | ripgrep |
| LangChain 1.2 | `glob_search` | `grep_search` | pattern / path / include / output_mode | ripgrep + Python fallback |
| OpenAI Codex | 无（用 shell 替代） | 无（用 shell 替代） | — | — |

**关键发现**：
1. 参数命名高度统一：`pattern`（搜索模式）、`path`（路径）、`glob/include`（文件过滤）、`output_mode`（输出格式）
2. ripgrep 是事实标准搜索引擎
3. LangChain 1.2.12 已内置完整实现（`FilesystemFileSearchMiddleware`），与本项目 `create_agent` 中间件体系一致

---

## 二、LangChain 内置方案分析

### 2.1 `FilesystemFileSearchMiddleware` 概览

源码路径：`langchain/agents/middleware/file_search.py`

工具参数对比：

**glob_search**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `pattern` | str | 是 | glob 模式，如 `**/*.py` |
| `path` | str | 否 | 搜索目录，默认 `/`（即 root_path） |

**grep_search**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `pattern` | str | 是 | 正则表达式 |
| `path` | str | 否 | 搜索目录 |
| `include` | str | 否 | 文件过滤，如 `*.py`、`*.{ts,tsx}`（支持 brace 展开） |
| `output_mode` | Literal | 否 | `files_with_matches`（默认）/ `content` / `count` |

### 2.2 内置方案的优势

| 优势 | 说明 |
|------|------|
| **ripgrep 加速** | 优先调用 `rg --json`，性能远超纯 Python；不可用时自动 fallback 到 Python `re` |
| **多输出模式** | `files_with_matches`（仅路径）/ `content`（file:line:content）/ `count`（file:count） |
| **路径安全** | `_validate_and_resolve_path` 禁止 `..` 和 `~`，确保不逃逸 root_path |
| **文件大小限制** | `max_file_size_mb` 参数，跳过超大文件（默认 10MB） |
| **brace 展开** | 支持 `*.{py,pyi}` 等复杂 glob 模式 |
| **中间件原生集成** | 与 `create_agent()` 的 middleware 参数一致，可直接挂载 |
| **零维护成本** | 框架团队维护，bug 修复和性能优化自动获得 |

### 2.3 内置方案的局限与本项目适配

| 局限 | 影响 | 解决方案 |
|------|------|---------|
| 工具名是闭包内部定义（`glob_search`/`grep_search`） | 无影响，名称与行业惯例一致 | — |
| ripgrep 当前未安装 | 自动 fallback 到 Python 搜索，功能完整但性能较低 | 可选安装 `ripgrep` |
| 无 `context` 上下文行参数 | `output_mode="content"` 返回匹配行但不含前后文 | 可接受的限制：Agent 可用 `read_file` 查看上下文 |
| 无结果数量限制 | 大项目可能返回大量匹配 | 通过 `TOOL_OUTPUT_BUDGETS` 中间件截断控制 |
| 路径使用 `/` 前缀（虚拟路径风格） | 返回 `/backend/tools/xxx.py` 格式，与 `read_file` 的 `backend/tools/xxx.py` 不同 | 需在 `read_file` 描述中说明路径格式，或在集成层去除前导 `/` |

---

## 三、集成方案

### 3.1 集成方式选择

`FilesystemFileSearchMiddleware` 提供两种集成路径：

**路径 A：作为中间件挂载（推荐）**

将搜索中间件与其他中间件一起传给 `create_agent`：

```python
# agent.py _build_middleware() 新增
from langchain.agents.middleware import FilesystemFileSearchMiddleware

middleware.append(
    FilesystemFileSearchMiddleware(
        root_path=str(self._base_dir),
        use_ripgrep=True,
        max_file_size_mb=10,
    )
)
```

中间件注册后，`glob_search` 和 `grep_search` 工具自动注入到 Agent，无需手动添加到 tools 列表。

**路径 B：仅提取工具实例**

只取工具，不使用中间件特性：

```python
from langchain.agents.middleware import FilesystemFileSearchMiddleware

fs_middleware = FilesystemFileSearchMiddleware(root_path=str(base_dir))
tools.extend(fs_middleware.tools)  # [glob_search, grep_search]
```

**选择路径 A**，理由：
1. 与现有的 `ToolOutputBudgetMiddleware`、`SummarizationMiddleware` 中间件链风格一致
2. 工具自动注册，无需手动管理 tools 列表
3. 框架升级时自动获得新特性

### 3.2 工具注册（`tools/__init__.py`）

**无需改动**。路径 A 通过中间件自动注册工具，不需要修改 `get_all_tools`。

### 3.3 中间件注册（`agent.py`）

在 `_build_middleware` 方法中新增第 5 层中间件：

```python
def _build_middleware(self) -> list:
    # ... 第 1-4 层不变 ...

    # 第 5 层：文件搜索工具（glob_search + grep_search）
    from langchain.agents.middleware import FilesystemFileSearchMiddleware
    middleware.append(
        FilesystemFileSearchMiddleware(
            root_path=str(self._base_dir),
            use_ripgrep=True,
            max_file_size_mb=10,
        )
    )

    return middleware
```

位置说明：放在中间件链最后，因为文件搜索是工具层能力，不影响其他中间件的执行顺序。

### 3.4 工具输出预算（`middleware.py`）

在 `TOOL_OUTPUT_BUDGETS` 中新增：

```python
TOOL_OUTPUT_BUDGETS: dict[str, int] = {
    "terminal": 2000,
    "python_repl": 1500,
    "fetch_url": 3000,
    "read_file": 2000,
    "search_knowledge": 1000,
    "glob_search": 1500,     # 新增：路径列表
    "grep_search": 2500,     # 新增：匹配结果（含行号+内容）
}
```

### 3.5 工具过滤 tier（`middleware.py`）

在 `TOOL_TIERS` 中归入 `always` 分类：

```python
TOOL_TIERS: dict[str, list[str]] = {
    "always": ["read_file", "search_knowledge", "glob_search", "grep_search"],
    "coding": ["terminal", "python_repl", "write_file"],
    "web": ["fetch_url"],
    "memory": ["save_memory", "search_memories"],
    "admin": ["create_skill_version"],
}
```

放入 `always` 而非 `coding`，理由与 `read_file` 一致：文件搜索是 Agent 的基础能力，任何场景都可能需要，且搜索和读取是连续操作。

### 3.6 ContextAwareToolFilter 关键词（`middleware.py`）

```python
@staticmethod
def _has_coding_context(text: str) -> bool:
    keywords = [
        "代码", "函数", "编辑文件", "终端", "运行", "python", "terminal",
        "code", "script", "exec",
        "搜索文件", "查找文件", "搜索代码", "查找代码", "glob", "grep",
        "find file", "search code",
    ]
    return any(kw in text for kw in keywords)
```

### 3.7 路径格式适配

LangChain 的 `glob_search`/`grep_search` 返回 `/` 前缀的虚拟路径（如 `/backend/tools/terminal_tool.py`），而本项目 `read_file` 使用相对路径（如 `backend/tools/terminal_tool.py`）。

**处理方式**：在 `read_file` 的 description 中补充说明：

```python
class SandboxedReadFileTool(BaseTool):
    description: str = (
        "Read the content of a local file. Path is relative to the project root. "
        "Use this to read SKILL.md files, MEMORY.md, configuration files, etc. "
        "Note: if path comes from glob_search/grep_search results (with leading '/'), "
        "remove the leading '/' before passing to this tool. "
        "Example: read_file('skills/get_weather/SKILL.md')"
    )
```

不需要在代码层做路径转换，通过工具描述引导 Agent 自行处理即可（Agent 足够智能，能理解路径格式差异）。

---

## 四、AGENTS.md 提示词调整

当前 `workspace/AGENTS.md` 的工具使用规范（第 29-37 行）缺少 `glob_search` 和 `grep_search`，需要两处调整。

### 4.1 工具列表新增

在现有 `read_file` 之前插入搜索工具（搜索是读取的前置操作）：

```markdown
## 工具使用规范

1. **terminal**: 用于执行 Shell 命令，注意安全边界
2. **python_repl**: 用于计算、数据处理、脚本执行
3. **fetch_url**: 用于获取网页内容，返回清洗后的 Markdown
4. **glob_search**: 按文件名模式搜索文件（如 `**/*.py`），返回匹配的文件路径列表
5. **grep_search**: 按内容搜索文件（支持正则、文件过滤、多种输出格式）
6. **read_file**: 用于读取本地文件，是技能调用的第一步
7. **write_file**: 用于写入文件内容（仅限 skills/、workspace/、memory/ 目录）
8. **search_knowledge_base**: 用于在知识库中检索信息
9. **create_skill_version**: 用于为技能创建版本快照
```

### 4.2 搜索工具使用指引

在工具列表之后、工具调用铁律之前，新增一段搜索工具使用指引：

```markdown
### 文件搜索策略

- **已知文件路径**：直接使用 `read_file` 读取
- **不确定文件路径**：先用 `glob_search` 按文件名模式定位文件，再用 `read_file` 读取
- **搜索代码内容**：使用 `grep_search`
  - 只想知道哪些文件包含某关键词：`grep_search("pattern", output_mode="files_with_matches")`
  - 需要查看匹配行：`grep_search("pattern", output_mode="content")`
  - 限定文件类型：`grep_search("pattern", include="*.py")`
- **注意**：`glob_search`/`grep_search` 返回的路径以 `/` 开头，传给 `read_file` 时需去掉前导 `/`
```

### 4.3 改动范围补充

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `backend/workspace/AGENTS.md` | 修改 | 工具列表新增 2 个工具 + 新增文件搜索策略指引 |

---

## 五、改动范围总览

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `backend/graph/agent.py` | 修改 | `_build_middleware` 新增第 5 层 `FilesystemFileSearchMiddleware` |
| `backend/graph/middleware.py` | 修改 | `TOOL_OUTPUT_BUDGETS` 新增 2 个工具预算；`TOOL_TIERS["always"]` 新增 2 个工具；`_has_coding_context` 补充关键词 |
| `backend/tools/read_file_tool.py` | 修改 | description 补充路径格式说明 |
| `backend/workspace/AGENTS.md` | 修改 | 工具列表新增 2 个工具 + 新增文件搜索策略指引 |
| `backend/config.json` | 可选 | `tool_output_budget.budgets` 新增配置 |

### 不改动的部分

| 组件 | 原因 |
|------|------|
| `tools/__init__.py` | 中间件自动注册工具，无需修改 get_all_tools |
| `tools/terminal_tool.py` | 保持不变，复杂 shell 操作仍用 terminal |
| 新增工具文件 | **无需新增**，直接复用 LangChain 内置实现 |
| 前端 | 工具对前端透明，SSE 事件格式不变 |
| 条件注册机制 | 无需条件注册，`FilesystemFileSearchMiddleware` 无外部依赖 |

---

## 六、ripgrep 安装建议（可选）

ripgrep 未安装时，`FilesystemFileSearchMiddleware` 自动 fallback 到 Python `re` 搜索，功能完整但性能较低。

安装 ripgrep 可显著提升搜索速度（10-100 倍）：

```bash
# macOS
brew install ripgrep

# Ubuntu/Debian
apt-get install ripgrep

# 验证
rg --version
```

是否安装为可选操作，不影响功能完整性。

---

## 七、与 terminal 的能力边界

| 场景 | 用 glob_search/grep_search | 用 terminal |
|------|---------------------------|------------|
| 按名称找文件 | `glob_search("**/*.py")` | 不推荐 |
| 按内容搜代码 | `grep_search("def process_", include="*.py")` | 不推荐 |
| 查看哪些文件包含关键词 | `grep_search("import asyncio", output_mode="files_with_matches")` | 不推荐 |
| 统计匹配数量 | `grep_search("TODO", output_mode="count")` | 不推荐 |
| 搜索+管道处理 | — | `grep ... \| sort \| uniq -c` |
| 复杂组合查询 | — | `find . -name "*.py" -exec grep -l "..." {} \;` |
| 运行脚本/安装包 | — | `pip install xxx` / `python script.py` |
| 系统信息 | — | `ls -la` / `pwd` / `uname` |

原则：**简单搜索用专用工具，复杂操作用 terminal**。
