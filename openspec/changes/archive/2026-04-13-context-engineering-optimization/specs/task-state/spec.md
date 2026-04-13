## ADDED Requirements

### Requirement: 任务状态数据结构

系统 SHALL 定义 `TaskState` 数据结构，包含：`session_id`（会话标识）、`goal`（用户原始目标）、`steps`（步骤列表）、`artifacts`（已创建/修改的文件路径）、`decisions`（关键决策记录）、`blockers`（当前阻塞项）。

每个 `TaskStep` MUST 包含：`description`（步骤描述）、`status`（pending/in_progress/completed/blocked）、`result_summary`（完成后的简要结果，可选）。

#### Scenario: 初始化任务状态

- **WHEN** 用户发送包含任务性动词的消息（如"帮我做"、"创建"、"实现"）
- **THEN** 系统自动创建 TaskState，`goal` 设为用户消息的语义摘要，`steps` 初始为空列表

#### Scenario: 更新步骤状态

- **WHEN** Agent 完成一个工具调用，且当前存在活跃的 TaskState
- **THEN** 系统 MUST 允许 Agent 更新对应步骤的 status 为 `completed` 并填写 `result_summary`

### Requirement: 任务状态通过 state_schema 嵌入

系统 MUST 通过 `create_agent` 的 `state_schema` 参数将 `TaskState` 嵌入 Agent 状态，与 `middleware` 同时使用（通过 `_resolve_schema` 自动合并）。

#### Scenario: TaskState 自动持久化

- **WHEN** Agent 在一次请求中更新了 TaskState 的某个步骤状态
- **THEN** TaskState MUST 通过 checkpointer 自动持久化，下次请求时自动恢复

#### Scenario: state_schema 与 middleware 不冲突

- **WHEN** 同时配置了 `state_schema=AgentCustomState` 和 `middleware=[...]` 参数
- **THEN** 两者 MUST 正常工作，`_resolve_schema` 自动合并两者的 schema 定义

### Requirement: 任务状态注入为上下文

当存在活跃的 TaskState 且包含步骤时，系统 MUST 在每次请求中将任务状态格式化为 Markdown，注入在当前用户消息之前。

#### Scenario: 任务状态格式化注入

- **WHEN** 当前会话有活跃 TaskState，包含 2 个步骤（1 个 completed，1 个 in_progress）
- **THEN** 系统在用户消息前注入格式化的任务状态文本，包含目标、步骤列表和状态图标

#### Scenario: 无活跃任务时不注入

- **WHEN** 当前会话没有活跃的 TaskState
- **THEN** 不注入任何任务状态文本

### Requirement: 任务状态压缩保护

对话压缩时，TaskState MUST 作为独立结构保留，不参与摘要过程。

#### Scenario: 摘要触发后任务状态完整保留

- **WHEN** SummarizationMiddleware 触发摘要，且当前有活跃的 TaskState
- **THEN** TaskState 的所有步骤、决策、artifacts 信息 MUST 完整保留在 Agent 状态中，不随旧消息一起被摘要替换
