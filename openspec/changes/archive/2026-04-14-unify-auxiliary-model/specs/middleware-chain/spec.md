## MODIFIED Requirements

### Requirement: 自动对话摘要中间件

系统 SHALL 使用 LangChain 内置的 `SummarizationMiddleware`，配置为 `trigger=("tokens", 8000)` + `keep=("messages", 10)`，在 token 数超过阈值时自动生成结构化摘要替换旧消息。

摘要 MUST 包含四段结构：SESSION INTENT（会话意图）、SUMMARY（关键上下文和决策）、ARTIFACTS（文件变更记录）、NEXT STEPS（待办任务）。

摘要 MUST 使用辅助模型（通过 `create_auxiliary_llm()` 获取，默认 `qwen3.5-flash`）生成，temperature 为 0。

#### Scenario: Token 超阈值自动触发摘要

- **WHEN** 当前消息列表的 token 总数超过 8000
- **THEN** 系统自动使用辅助模型生成四段结构化摘要，替换超出 `keep` 范围的旧消息

#### Scenario: 最近 10 条消息始终保留

- **WHEN** 摘要触发后
- **THEN** 最新的 10 条消息 MUST 完整保留，不被摘要替换

#### Scenario: AI/Tool 消息配对保护

- **WHEN** 摘要的截断点落在一条 AIMessage（含 tool_calls）和对应的 ToolMessage 之间
- **THEN** 系统 MUST 将截断点移动到安全位置，确保 tool_calls 和 tool_result 的配对完整性

#### Scenario: Token 未超阈值不触发

- **WHEN** 当前消息列表的 token 总数未超过 8000
- **THEN** 消息列表保持不变，不执行摘要操作

#### Scenario: 辅助模型不可用时跳过摘要

- **WHEN** `create_auxiliary_llm()` 返回 `None`（无 API key）
- **THEN** 系统跳过 SummarizationMiddleware 的创建，中间件链中不包含摘要层
