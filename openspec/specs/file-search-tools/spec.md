## Purpose

定义通过 LangChain 内置 `FilesystemFileSearchMiddleware` 为 Agent 提供的文件搜索工具能力，包含 glob_search（按文件名模式搜索）和 grep_search（按内容正则搜索）两个工具。

## ADDED Requirements

### Requirement: glob_search 文件名模式搜索

系统 SHALL 提供 `glob_search` 工具，按文件名模式搜索工作目录下的文件路径。

工具参数：
- `pattern`（必填）：glob 模式字符串（如 `**/*.py`、`src/**/*.ts`）
- `path`（可选）：搜索根目录，默认为工作目录

返回值：匹配文件的相对路径列表（带 `/` 前缀的虚拟路径格式）。

工具 MUST 通过 `FilesystemFileSearchMiddleware` 的 `self.tools` 自动注册，无需修改 `get_all_tools()`。

#### Scenario: 按扩展名搜索文件

- **WHEN** Agent 调用 `glob_search(pattern="**/*.py")`
- **THEN** 系统 SHALL 返回工作目录下所有 `.py` 文件的路径列表

#### Scenario: 按目录前缀搜索文件

- **WHEN** Agent 调用 `glob_search(pattern="backend/graph/**/*.py")`
- **THEN** 系统 SHALL 返回 `backend/graph/` 目录下所有 `.py` 文件的路径列表

#### Scenario: 无匹配结果

- **WHEN** Agent 调用 `glob_search(pattern="**/*.xyz")` 且无匹配文件
- **THEN** 系统 SHALL 返回空结果列表

### Requirement: grep_search 文件内容正则搜索

系统 SHALL 提供 `grep_search` 工具，按内容正则表达式搜索工作目录下的文件。

工具参数：
- `pattern`（必填）：正则表达式字符串
- `include`（可选）：文件名过滤模式（如 `*.py`）
- `output_mode`（可选）：输出格式，支持 `files_with_matches`、`content`、`count`
- `path`（可选）：搜索根目录，默认为工作目录

底层实现：优先使用 ripgrep（`rg` 命令）加速搜索，ripgrep 不可用时自动 fallback 到 Python `re` 模块。

#### Scenario: 按关键词搜索并返回文件路径

- **WHEN** Agent 调用 `grep_search(pattern="TODO", include="*.py", output_mode="files_with_matches")`
- **THEN** 系统 SHALL 返回包含 "TODO" 的所有 `.py` 文件路径列表

#### Scenario: 按正则搜索并返回匹配内容

- **WHEN** Agent 调用 `grep_search(pattern="class \\w+Middleware", output_mode="content")`
- **THEN** 系统 SHALL 返回匹配行的文件路径、行号和匹配内容

#### Scenario: ripgrep 不可用时自动降级

- **WHEN** 系统未安装 ripgrep
- **THEN** 系统 SHALL 使用 Python `re` 模块执行搜索，功能完整但性能较低

#### Scenario: 搜索结果过大时被截断

- **WHEN** grep_search 返回结果超过 `TOOL_OUTPUT_BUDGETS` 中配置的预算
- **THEN** ToolOutputBudgetMiddleware SHALL 截断结果，保留预算范围内的内容

### Requirement: 搜索结果路径格式兼容

glob_search 和 grep_search 返回的文件路径使用 `/` 前缀的虚拟路径格式（如 `/backend/graph/agent.py`）。

Agent 后续调用 `read_file` 读取搜索结果中的文件时，系统 SHALL 依赖 `read_file_tool.py` 中已有的 `lstrip("./")` 自动处理路径格式差异，无需额外转换。

#### Scenario: 搜索结果直接用于 read_file

- **WHEN** Agent 调用 `glob_search` 获取路径 `/backend/graph/agent.py`，然后调用 `read_file(file_path="/backend/graph/agent.py")`
- **THEN** read_file SHALL 自动通过 `lstrip("./")` 去除前导 `/`，正确读取文件

### Requirement: 路径安全验证

FilesystemFileSearchMiddleware SHALL 对搜索路径进行安全验证，阻止目录遍历攻击。

- MUST 阻止包含 `..` 的路径
- MUST 阻止包含 `~` 的路径
- 搜索范围 MUST 限制在工作目录内

#### Scenario: 阻止目录遍历

- **WHEN** 搜索路径中包含 `..` 或 `~`
- **THEN** 系统 SHALL 拒绝执行搜索并返回错误信息
