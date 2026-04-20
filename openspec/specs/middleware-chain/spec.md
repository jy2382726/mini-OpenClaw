## Purpose

定义 Agent 的中间件链架构：工具输出截断（ToolOutputBudgetMiddleware）、自动对话摘要（SummarizationMiddleware）、运行时工具过滤（ContextAwareToolFilter）、工具调用限流（ToolCallLimitMiddleware）、长期记忆管理（MemoryMiddleware）、文件搜索（FilesystemFileSearchMiddleware），以及手动摘要与自动摘要的衔接机制。

## Requirements

### Requirement: 六层中间件链架构

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

### Requirement: 自动对话摘要中间件（SummarizationMiddleware）

系统 SHALL 使用 `ContextAwareSummarizationMiddleware`（继承自 `SummarizationMiddleware`）作为中间件链第 2 层。

触发阈值 MUST 支持两种配置模式，比例优先：
- `trigger_ratio`（默认 0.6）：上下文窗口的百分比，计算 `trigger_tokens = int(context_window * trigger_ratio)`
- `trigger_tokens`（可选）：绝对值，非 null 时覆盖比例计算结果

Trim 阈值 MUST 支持两种配置模式，比例优先：
- `trim_ratio`（默认 0.30）：上下文窗口的百分比，计算 `trim_tokens = int(context_window * trim_ratio)`
- `trim_tokens`（可选）：绝对值，非 null 时覆盖比例计算结果

`trim_tokens_to_summarize` 参数 MUST 传入计算的 trim 值，不使用 LangChain 默认值 4000。

摘要 MUST 使用自定义中文提示词，通过 `_load_summary_prompt()` 加载，加载优先级：
1. `config.json` 中 `summary_prompt_file` 指定的文件路径
2. `workspace/summary_prompt.md` 默认文件
3. 内置 `DEFAULT_SUMMARY_PROMPT_ZH` 常量兜底

保留最近 N 条消息（默认 10 条），N 值通过 `keep_messages` 配置。

`ContextAwareSummarizationMiddleware` MUST 在摘要前提取所有 SystemMessage，调用父类摘要逻辑处理非 System 消息，摘要完成后将 SystemMessage 重新注入结果。

摘要 MUST 使用辅助 LLM 生成（通过 `create_auxiliary_llm()` 工厂函数创建），temperature 为 0。

系统始终使用 checkpoint 模式。中间件操作的消息列表来自 checkpoint 恢复。中间件执行摘要后，修改结果通过 checkpoint 自动持久化，下一轮对话时 Agent 看到的是摘要后的消息列表。

#### Scenario: 比例模式触发摘要

- **WHEN** `trigger_ratio` 为 0.6 且上下文窗口为 131072 token
- **THEN** 触发阈值 MUST 为 `int(131072 * 0.6) = 78643` token

#### Scenario: 绝对值覆盖触发阈值

- **WHEN** `trigger_tokens` 配置为 8000 且 `trigger_ratio` 为 0.6
- **THEN** 触发阈值 MUST 为 8000 token（绝对值覆盖比例计算）

#### Scenario: Trim 联动上下文窗口

- **WHEN** `trim_ratio` 为 0.30 且上下文窗口为 131072 token
- **THEN** `trim_tokens_to_summarize` MUST 为 `int(131072 * 0.30) = 39321` token

#### Scenario: SystemMessage 保护

- **WHEN** 消息列表中包含 SystemMessage（Zone 3 动态内容）且摘要触发
- **THEN** SystemMessage MUST 不参与 LLM 摘要，摘要完成后 MUST 重新注入到结果消息列表中

#### Scenario: 自定义提示词加载

- **WHEN** `config.json` 指定 `summary_prompt_file` 为 `workspace/custom_prompt.md`
- **THEN** 摘要 MUST 使用该文件的提示词内容

#### Scenario: 无自定义提示词时使用内置中文提示词

- **WHEN** `summary_prompt_file` 为 null 且 `workspace/summary_prompt.md` 不存在
- **THEN** 摘要 MUST 使用 `DEFAULT_SUMMARY_PROMPT_ZH` 内置常量

#### Scenario: Token 未超阈值不触发

- **WHEN** 当前消息列表的 token 总数未超过触发阈值
- **THEN** 消息列表保持不变，不执行摘要操作

#### Scenario: 最近 10 条消息始终保留

- **WHEN** 摘要触发后
- **THEN** 最新的 10 条消息 MUST 完整保留，不被摘要替换

#### Scenario: 摘要状态通过 checkpoint 自动持久化

- **WHEN** 摘要中间件触发摘要并修改了消息列表
- **THEN** 修改后的消息列表 MUST 通过 checkpoint 自动持久化，下一轮对话时 Agent 看到的消息 MUST 包含摘要结果

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

### Requirement: 工具调用限流（ToolCallLimitMiddleware）

系统 SHALL 提供 `ToolCallLimitMiddleware`，防止单次对话中 Agent 无限循环调用工具。

该中间件在中间件链的最外层执行，当工具调用次数超过配置的阈值时，阻止后续工具调用并返回提示信息。

#### Scenario: 工具调用超过阈值被阻止

- **WHEN** Agent 在单次对话中的工具调用次数超过配置的阈值
- **THEN** 系统 MUST 阻止后续工具调用，返回提示信息引导 Agent 直接回答用户

### Requirement: 手动摘要与自动摘要衔接

手动摘要（`POST /api/sessions/{id}/summarize`）MUST 复用 `ContextAwareSummarizationMiddleware` 的提示词加载逻辑（`_load_summary_prompt`），确保手动和自动摘要的输出格式一致。

手动摘要的消息替换逻辑（保留最近 10 条、AI/Tool 配对保护）MUST 与 `SummarizationMiddleware` 的策略对齐。

#### Scenario: 手动摘要与自动摘要 Prompt 一致

- **WHEN** 用户通过前端按钮触发手动摘要
- **THEN** 摘要 MUST 使用 `_load_summary_prompt()` 加载的提示词生成，输出格式与自动摘要完全一致

#### Scenario: 摘要生成失败时不破坏消息列表

- **WHEN** 辅助 LLM 调用超时或返回无效摘要
- **THEN** 系统 MUST 保留原始消息列表不变，MUST NOT 用空内容或错误信息替换消息
