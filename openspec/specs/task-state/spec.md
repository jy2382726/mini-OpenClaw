## Purpose

修改任务状态（TaskState）的实现方式，通过 state_schema 参数嵌入 Agent 状态，确保任务状态能够自动持久化和跨请求恢复，并在 checkpoint_only 模式下正常工作。

## Requirements

### Requirement: 任务状态通过 state_schema 嵌入

系统 MUST 通过 `create_agent` 的 `state_schema` 参数将 `TaskState` 嵌入 Agent 状态，与 `middleware` 同时使用（通过 `_resolve_schema` 自动合并）。

系统 MUST 在每次 agent.astream/ainvoke 调用时传入 `config={"configurable": {"thread_id": session_id}}`，激活 checkpointer 的状态持久化。

系统 MUST 在创建 TaskState 后将其写入 `AgentCustomState.task_state` 字段，而非仅作为局部变量使用。

当 `checkpoint_agent_input` 为 true 时，系统 MUST 确保在不传历史消息的场景下，TaskState 仍能通过 checkpoint 正确恢复。`_read_task_state()` 调用 `agent.aget_state()` 时，Agent 的 state MUST 已通过 `thread_id` 从 checkpoint 自动加载。

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

#### Scenario: checkpoint_only 模式下 TaskState 恢复正常

- **WHEN** `checkpoint_agent_input` 为 true，系统不传历史消息给 Agent，仅依赖 checkpoint 恢复
- **THEN** `_read_task_state()` MUST 仍能通过 `agent.aget_state()` 正确读取到之前持久化的 TaskState

### Requirement: 任务状态压缩保护

对话压缩时，TaskState MUST 作为独立结构保留，不参与摘要过程。

当 `checkpoint_agent_input` 为 true 时，SummarizationMiddleware 操作的是 checkpoint 恢复的消息列表，TaskState MUST 仍在压缩过程中完整保留。

#### Scenario: 摘要触发后任务状态完整保留

- **WHEN** SummarizationMiddleware 触发摘要，且当前有活跃的 TaskState
- **THEN** TaskState 的所有步骤、决策、artifacts 信息 MUST 完整保留在 Agent 状态中，不随旧消息一起被摘要替换

#### Scenario: checkpoint_only 模式下摘要不破坏 TaskState

- **WHEN** `checkpoint_agent_input` 为 true，SummarizationMiddleware 对 checkpoint 恢复的消息执行摘要
- **THEN** TaskState MUST 不受摘要操作影响，跨请求恢复后仍完整可用

### Requirement: SSE task_update 事件推送

系统 MUST 在 agent.py astream 方法中，当检测到 task_state 变更时，向 SSE 流推送 `task_update` 事件。

在 `stream_mode=["updates"]` 的 updates 分支中，当 node_data 包含非 null 的 `task_state` 字段时，MUST yield 一个事件：

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

推送时机 MUST 限制在以下关键节点：
- 任务首次创建时（`is_task_message` 检测通过，TaskState 首次写入 state）
- 步骤状态变更时（`update_step` 操作成功）
- 任务完成时（所有步骤变为 completed）

#### Scenario: task_state 变更时推送事件

- **WHEN** Agent 通过 update_task 工具更新了 TaskState，LangGraph updates 模式返回的 node_data 中包含 task_state 字段且值非 null
- **THEN** astream MUST yield `{"type": "task_update", "task_state": <完整 TaskState 对象>}` 事件

#### Scenario: 非 task_state 变更时不推送

- **WHEN** Agent 的更新仅涉及 messages 或其他 state 字段，不涉及 task_state
- **THEN** astream MUST NOT yield task_update 事件

#### Scenario: task_state 为 null 时不推送

- **WHEN** node_data 中 task_state 字段为 null（如任务被清除）
- **THEN** astream MUST NOT yield task_update 事件
