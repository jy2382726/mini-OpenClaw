## Purpose

定义 Agent 的四层中间件链架构：工具输出截断（ToolOutputBudgetMiddleware）、自动对话摘要（SummarizationMiddleware）、运行时工具过滤（ContextAwareToolFilter）、工具调用限流（ToolCallLimitMiddleware），以及手动摘要与自动摘要的衔接机制。

## Requirements

### Requirement: 四层中间件链架构

系统 SHALL 在 `AgentManager._build_middleware()` 中构建四层中间件链，按以下顺序执行：

1. **ToolOutputBudgetMiddleware** — 工具输出渐进式截断/归档
2. **SummarizationMiddleware** — 自动对话摘要（token 超阈值时触发）
3. **ContextAwareToolFilter** — 运行时工具过滤（基于技能上下文动态调整可用工具）
4. **ToolCallLimitMiddleware** — 工具调用限流（防止 Agent 无限循环调用工具）

每层通过 `config.json` 的 `middleware` 配置段独立开关。

#### Scenario: 四层中间件按序执行

- **WHEN** Agent 执行一次模型调用
- **THEN** 中间件按 截断 → 摘要 → 工具过滤 → 限流 的顺序依次执行

#### Scenario: 单层中间件可独立关闭

- **WHEN** `config.json` 中 `middleware.summarization.enabled` 设为 false
- **THEN** SummarizationMiddleware 不被加载，其他三层正常工作

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

系统 SHALL 使用 LangChain 内置的 `SummarizationMiddleware`，配置为 `trigger=("tokens", trigger_tokens)` + `keep=("messages", 10)`，其中 `trigger_tokens = int(context_window * 0.6)`。

摘要 MUST 包含四段结构：SESSION INTENT（会话意图）、SUMMARY（关键上下文和决策）、ARTIFACTS（文件变更记录）、NEXT STEPS（待办任务）。

摘要 MUST 使用辅助 LLM 生成（通过 `create_auxiliary_llm()` 工厂函数创建），temperature 为 0。

系统始终使用 checkpoint 模式。SummarizationMiddleware 操作的消息列表来自 checkpoint 恢复。中间件执行摘要后，修改结果通过 checkpoint 自动持久化，下一轮对话时 Agent 看到的是摘要后的消息列表。

#### Scenario: Token 超阈值自动触发摘要

- **WHEN** 当前消息列表的 token 总数超过 `context_window * 0.6`
- **THEN** 系统自动生成四段结构化摘要，替换超出 `keep` 范围的旧消息

#### Scenario: 最近 10 条消息始终保留

- **WHEN** 摘要触发后
- **THEN** 最新的 10 条消息 MUST 完整保留，不被摘要替换

#### Scenario: Token 未超阈值不触发

- **WHEN** 当前消息列表的 token 总数未超过 `context_window * 0.6`
- **THEN** 消息列表保持不变，不执行摘要操作

#### Scenario: 摘要状态通过 checkpoint 自动持久化

- **WHEN** SummarizationMiddleware 触发摘要并修改了消息列表
- **THEN** 修改后的消息列表 MUST 通过 checkpoint 自动持久化，下一轮对话时 Agent 看到的消息 MUST 包含摘要结果

### Requirement: 运行时工具过滤（ContextAwareToolFilter）

系统 SHALL 提供 `ContextAwareToolFilter`，在运行时根据当前技能上下文动态调整 Agent 可用的工具集。

该中间件在 SummarizationMiddleware 之后、ToolCallLimitMiddleware 之前执行，基于已加载技能的 `required_tools` 和当前对话上下文决定哪些工具应保持可用。

#### Scenario: 技能上下文激活相关工具

- **WHEN** Agent 加载了某个需要特定工具的技能
- **THEN** ContextAwareToolFilter SHALL 确保相关工具保持可用

### Requirement: 工具调用限流（ToolCallLimitMiddleware）

系统 SHALL 提供 `ToolCallLimitMiddleware`，防止单次对话中 Agent 无限循环调用工具。

该中间件在中间件链的最外层执行，当工具调用次数超过配置的阈值时，阻止后续工具调用并返回提示信息。

#### Scenario: 工具调用超过阈值被阻止

- **WHEN** Agent 在单次对话中的工具调用次数超过配置的阈值
- **THEN** 系统 MUST 阻止后续工具调用，返回提示信息引导 Agent 直接回答用户

### Requirement: 手动摘要与自动摘要衔接

手动摘要（`POST /api/sessions/{id}/summarize`）MUST 复用 `SummarizationMiddleware.DEFAULT_SUMMARY_PROMPT` 生成摘要，确保自动和手动摘要的输出格式一致。手动摘要的消息替换逻辑（保留最近 10 条、AI/Tool 配对保护）MUST 与 `SummarizationMiddleware` 的策略对齐。

历史压缩完全由 SummarizationMiddleware（自动）和 `summarize_checkpoint()` 方法（手动）管理。

#### Scenario: 手动摘要与自动摘要 Prompt 一致

- **WHEN** 用户通过前端按钮触发手动摘要
- **THEN** 摘要 MUST 使用 `DEFAULT_SUMMARY_PROMPT` 生成，输出格式与自动摘要完全一致

#### Scenario: 摘要生成失败时不破坏消息列表

- **WHEN** 辅助 LLM 调用超时或返回无效摘要
- **THEN** 系统 MUST 保留原始消息列表不变，MUST NOT 用空内容或错误信息替换消息
