## Purpose

定义任务状态（TaskState）的实现方式，通过 state_schema 参数嵌入 Agent 状态，确保任务状态能够自动持久化和跨请求恢复。定义 SSE 事件推送时机，包括任务创建、步骤追加、步骤更新和流结束自动完成等场景。

## Requirements

### Requirement: 任务状态通过 state_schema 嵌入

系统 MUST 通过 `create_agent` 的 `state_schema` 参数将 `TaskState` 嵌入 Agent 状态（`AgentCustomState`），与 `middleware` 同时使用（通过 `_resolve_schema` 自动合并）。

系统 MUST 在每次 agent.astream/ainvoke 调用时传入 `config={"configurable": {"thread_id": session_id}}`，激活 checkpointer 的状态持久化。

系统 MUST 在创建 TaskState 后将其写入 `AgentCustomState.task_state` 字段，而非仅作为局部变量使用。

#### Scenario: TaskState 自动持久化

- **WHEN** Agent 在一次请求中通过 `update_task` 工具更新了 TaskState 的某个步骤状态
- **THEN** 更新后的 TaskState MUST 通过 checkpointer 自动持久化，下次同一 session 请求时自动恢复

#### Scenario: TaskState 跨请求恢复

- **WHEN** 用户在同一 session 中发送后续消息，且前一次请求中有活跃的 TaskState
- **THEN** 系统 MUST 从 checkpoint 恢复已有 TaskState，优先使用恢复的状态而非重新创建

#### Scenario: 新任务追加步骤

- **WHEN** 用户在有活跃 TaskState 的 session 中发送包含任务性动词的新消息
- **THEN** 系统 MUST 将新目标作为新步骤追加到已有 TaskState 的 steps 列表，而非覆盖整个 TaskState

#### Scenario: state_schema 与 middleware 不冲突

- **WHEN** 同时配置了 `state_schema=AgentCustomState` 和 `middleware=[...]` 参数
- **THEN** 两者 MUST 正常工作，`_resolve_schema` 自动合并两者的 schema 定义

### Requirement: 任务状态压缩保护

对话压缩时，TaskState MUST 作为独立结构保留，不参与摘要过程。

TaskState 存储在 `AgentCustomState.task_state` 字段中（不在 messages 列表中），SummarizationMiddleware 只操作 messages，不会影响 task_state。

#### Scenario: 摘要触发后任务状态完整保留

- **WHEN** SummarizationMiddleware 触发摘要，且当前有活跃的 TaskState
- **THEN** TaskState 的所有步骤、决策、artifacts 信息 MUST 完整保留在 Agent 状态中，不随旧消息一起被摘要替换

#### Scenario: 手动摘要不破坏 TaskState

- **WHEN** `summarize_checkpoint()` 对 checkpoint 消息执行摘要
- **THEN** TaskState MUST 不受摘要操作影响，跨请求恢复后仍完整可用

### Requirement: SSE task_update 事件推送

系统 MUST 在 agent.py astream 方法中，当检测到 task_state 变更时，向 SSE 流推送 `task_update` 事件。

推送 JSON 格式：

```json
{
  "type": "task_update",
  "task_state": {
    "session_id": "...",
    "goal": "...",
    "steps": [...],
    "artifacts": [...],
    "decisions": [...],
    "blockers": [...]
  }
}
```

推送时机 MUST 覆盖以下节点：
- **任务首次创建时**：`is_task_message` 检测通过，TaskState 首次写入 state
- **步骤追加时**：在有活跃 TaskState 的 session 中发送包含任务性动词的新消息，新步骤被追加到 steps 列表
- **步骤状态变更时**：`update_task` 工具操作成功（通过 `Command(update={...})` 触发 updates 分支检测）
- **流结束自动完成时**：流式响应结束后，系统自动将所有 in_progress 步骤标记为 completed

在 `stream_mode=["updates"]` 的 updates 分支中，当 node_data 包含非 null 的 `task_state` 字段时，MUST yield `task_update` 事件。

#### Scenario: 任务首次创建时推送事件

- **WHEN** Agent 检测到任务性动词，创建 TaskState 并写入 state
- **THEN** astream MUST yield `{"type": "task_update", "task_state": <完整 TaskState 对象>}` 事件

#### Scenario: 步骤追加时推送事件

- **WHEN** 已有活跃 TaskState 时用户发送新任务消息，系统追加新步骤
- **THEN** astream MUST yield `task_update` 事件，包含更新后的步骤列表

#### Scenario: update_tool 触发推送

- **WHEN** Agent 通过 update_task 工具更新了 TaskState，LangGraph updates 模式返回的 node_data 中包含 task_state 字段且值非 null
- **THEN** astream MUST yield `{"type": "task_update", "task_state": <完整 TaskState 对象>}` 事件

#### Scenario: 非 task_state 变更时不推送

- **WHEN** Agent 的更新仅涉及 messages 或其他 state 字段，不涉及 task_state
- **THEN** astream MUST NOT yield task_update 事件

#### Scenario: task_state 为 null 时不推送

- **WHEN** node_data 中 task_state 字段为 null
- **THEN** astream MUST NOT yield task_update 事件

### Requirement: 流结束自动完成 in_progress 步骤

系统 MUST 在 astream 流式响应结束后（Agent 完成当前轮次），自动将所有仍处于 `in_progress` 状态的步骤标记为 `completed`。

此行为确保即使 Agent 未显式调用 `update_task` 工具标记步骤完成，步骤也不会永远停留在 in_progress 状态。完成后 MUST 推送最终的 `task_update` 事件。

#### Scenario: Agent 未显式完成步骤时自动完成

- **WHEN** 流式响应结束，仍有步骤处于 in_progress 状态
- **THEN** 系统 MUST 将这些步骤自动标记为 completed，并推送包含最终状态的 task_update 事件

#### Scenario: 所有步骤已显式完成时不重复推送

- **WHEN** 流式响应结束，所有步骤已处于 completed 状态
- **THEN** 系统 MUST NOT 推送额外的 task_update 事件

### Requirement: checkpoint 恢复时 TaskState 格式异常处理

当 checkpoint 中的 `task_state` 字段为非法格式（如字符串而非字典）时，`_read_task_state()` MUST 通过 try/except 安全返回 None，MUST NOT 因解析失败而阻塞 Agent 执行。

返回 None 等效于重置 TaskState，后续逻辑视同无活跃 TaskState。

#### Scenario: 非法格式安全降级

- **WHEN** checkpoint 中的 task_state 字段为非法格式
- **THEN** `_read_task_state()` SHALL 返回 None，Agent 正常执行，不抛出异常
