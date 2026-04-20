## Purpose

增量更新中间件链规范，新增第 6 层 `FilesystemFileSearchMiddleware`，更新工具输出预算和工具分类配置。

## MODIFIED Requirements

### Requirement: 四层中间件链架构

系统 SHALL 在 `AgentManager._build_middleware()` 中构建中间件链，按以下顺序执行：

1. **ToolOutputBudgetMiddleware** — 工具输出渐进式截断/归档
2. **SummarizationMiddleware** — 自动对话摘要（token 超阈值时触发）
3. **ContextAwareToolFilter** — 运行时工具过滤（基于技能上下文动态调整可用工具）
4. **ToolCallLimitMiddleware** — 工具调用限流（防止 Agent 无限循环调用工具）
5. **MemoryMiddleware** — 长期记忆管理（规划中，第 5 层已分配）
6. **FilesystemFileSearchMiddleware** — 文件搜索工具（自动注册 glob_search + grep_search）

每层通过 `config.json` 的 `middleware` 配置段独立开关。FilesystemFileSearchMiddleware 无外部依赖，无需条件注册。

#### Scenario: 六层中间件按序执行

- **WHEN** Agent 执行一次模型调用
- **THEN** 中间件按 截断 → 摘要 → 工具过滤 → 限流 → 记忆 → 文件搜索 的顺序依次执行

#### Scenario: 单层中间件可独立关闭

- **WHEN** `config.json` 中 `middleware.summarization.enabled` 设为 false
- **THEN** SummarizationMiddleware 不被加载，其他层正常工作

### Requirement: 工具输出渐进式截断（ToolOutputBudgetMiddleware）

系统 SHALL 提供 `ToolOutputBudgetMiddleware`，基于上下文窗口使用比例执行工具输出的渐进式压缩。

两个关键比例阈值 MUST 可配置：
- `safe_ratio`：安全水位（默认 0.25），低于此比例不压缩任何工具输出
- `pressure_ratio`：紧张水位（默认 0.45），高于此比例启用激进压缩

压缩策略：
| 级别 | 触发条件 | 行为 |
|------|---------|------|
| 0 | < safe_ratio | 不处理 |
| 1 | safe_ratio ~ pressure_ratio | 头尾截断（头 2/3 + 尾 1/3） |
| 2 | ≥ pressure_ratio | 短截断（头 1/2 + 尾 1/4） |

当前轮次工具输出保护：根据上下文压力动态调整保护范围（< safe: 全部保护, safe~pressure: 最近 3 组, ≥ pressure: 最近 1 组）。

`TOOL_OUTPUT_BUDGETS` SHALL 包含以下工具的输出预算配置：
- `glob_search`：1500 token（约 6000 字符）
- `grep_search`：2500 token（约 10000 字符）

#### Scenario: 上下文宽裕时不触发压缩

- **WHEN** 消息列表总 token 估算低于 safe_ratio
- **THEN** 系统 MUST 不对任何 ToolMessage 执行压缩

#### Scenario: 安全水位使用标准截断

- **WHEN** 上下文使用比例在 safe_ratio 和 pressure_ratio 之间
- **THEN** 系统 SHALL 对早期轮次的超预算 ToolMessage 执行头 2/3 + 尾 1/3 截断

#### Scenario: 紧张水位使用短截断

- **WHEN** 上下文使用比例超过 pressure_ratio
- **THEN** 系统 SHALL 仅保护最近 1 组工具输出，更早的输出使用头 1/2 + 尾 1/4 截断

#### Scenario: 工具输出自动归档

- **WHEN** 单条工具输出超过上下文窗口的 5%（`archive_ratio`）
- **THEN** 系统 SHALL 将完整输出归档到 `sessions/archive/tool_{tool_name}_{timestamp}.txt`，ToolMessage 中保留截断摘要和文件路径引用

### Requirement: 运行时工具过滤（ContextAwareToolFilter）

系统 SHALL 提供 `ContextAwareToolFilter`，在运行时根据当前技能上下文动态调整 Agent 可用的工具集。

该中间件在 SummarizationMiddleware 之后、ToolCallLimitMiddleware 之前执行，基于已加载技能的 `required_tools` 和当前对话上下文决定哪些工具应保持可用。

`TOOL_TIERS` SHALL 定义以下工具分类：
- `always` tier：始终可用的基础工具，包含 `read_file`、`glob_search`、`grep_search` 等
- `coding` tier：编程场景可用工具
- `general` tier：通用场景可用工具

`_has_coding_context` 方法 SHALL 识别搜索相关的复合关键词（如 "搜索文件"、"查找文件"、"搜索代码"、"查找代码"、"glob"、"grep"、"find file"、"search code"），确保搜索场景下工具正确可用。SHOULD 使用复合词匹配以避免单字误匹配（如"文件"匹配到"文件系统"等无关上下文）。

#### Scenario: 技能上下文激活相关工具

- **WHEN** Agent 加载了某个需要特定工具的技能
- **THEN** ContextAwareToolFilter SHALL 确保相关工具保持可用

#### Scenario: 文件搜索工具始终可用

- **WHEN** 对话上下文不包含编程相关关键词
- **THEN** glob_search 和 grep_search 仍 MUST 保持可用（归入 always tier）
