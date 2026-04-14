## MODIFIED Requirements

### Requirement: 自动对话摘要中间件

系统 SHALL 使用 LangChain 内置的 `SummarizationMiddleware`，配置为 `trigger=("tokens", 8000)` + `keep=("messages", 10)`，在 token 数超过阈值时自动生成结构化摘要替换旧消息。

摘要 MUST 包含四段结构：SESSION INTENT（会话意图）、SUMMARY（关键上下文和决策）、ARTIFACTS（文件变更记录）、NEXT STEPS（待办任务）。

摘要 MUST 使用轻量 LLM（如 qwen-turbo）生成，temperature 为 0。

当 `checkpoint_agent_input` 为 true 时，SummarizationMiddleware 操作的消息列表来自 checkpoint 恢复而非 session_manager 传入。中间件执行摘要后，修改结果 MUST 通过 checkpoint 自动持久化，下一轮对话时 Agent 看到的是摘要后的消息列表。

当 `compressed_context` 注入逻辑（session_manager 的 `load_session_for_agent` 中的 COMPRESSED_CONTEXT_PREFIX 注入）废弃后，历史压缩 MUST 完全由 SummarizationMiddleware 接管，不再依赖 JSON 文件中的 `compressed_context` 字段。

#### Scenario: Token 超阈值自动触发摘要

- **WHEN** 当前消息列表的 token 总数超过 8000
- **THEN** 系统自动生成四段结构化摘要，替换超出 `keep` 范围的旧消息

#### Scenario: 最近 10 条消息始终保留

- **WHEN** 摘要触发后
- **THEN** 最新的 10 条消息 MUST 完整保留，不被摘要替换

#### Scenario: AI/Tool 消息配对保护

- **WHEN** 摘要的截断点落在一条 AIMessage（含 tool_calls）和对应的 ToolMessage 之间
- **THEN** 系统 MUST 将截断点移动到安全位置，确保 tool_calls 和 tool_result 的配对完整性

#### Scenario: Token 未超阈值不触发

- **WHEN** 当前消息列表的 token 总数未超过 8000
- **THEN** 消息列表保持不变，不执行摘要操作

#### Scenario: checkpoint_only 模式下摘要状态持久化

- **WHEN** `checkpoint_agent_input` 为 true，SummarizationMiddleware 触发摘要并修改了消息列表
- **THEN** 修改后的消息列表 MUST 通过 checkpoint 自动持久化，下一轮对话时 Agent 看到的消息 MUST 包含摘要结果

#### Scenario: compressed_context 废弃后摘要完整接管

- **WHEN** `compressed_context` 注入逻辑被移除，且 `checkpoint_agent_input` 为 true
- **THEN** 历史压缩 MUST 完全由 SummarizationMiddleware 管理，不再有其他压缩来源
