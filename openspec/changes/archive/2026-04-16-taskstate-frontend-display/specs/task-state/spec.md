## ADDED Requirements

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
