## ADDED Requirements

### Requirement: TaskState 恢复端点

系统 MUST 提供 `GET /api/sessions/{session_id}/task-state` 端点，用于前端恢复 TaskState。

端点 MUST 通过 `agent.aget_state(config)` 从 checkpoint 读取 Agent 状态，提取 `task_state` 字段返回。

返回格式：

```json
{
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

当 checkpoint 不存在或 task_state 字段为空时，MUST 返回 `{"task_state": null}`。

#### Scenario: 有活跃 TaskState 时返回完整数据

- **WHEN** 前端请求 `GET /api/sessions/{id}/task-state`，且该会话有活跃 TaskState
- **THEN** 系统 MUST 返回完整的 TaskState 对象

#### Scenario: 无 TaskState 时返回 null

- **WHEN** 前端请求 `GET /api/sessions/{id}/task-state`，且该会话无活跃 TaskState
- **THEN** 系统 MUST 返回 `{"task_state": null}`，HTTP 状态码 200

#### Scenario: checkpoint 不存在时返回 null

- **WHEN** 前端请求 `GET /api/sessions/{id}/task-state`，但该会话无 checkpoint 数据
- **THEN** 系统 MUST 返回 `{"task_state": null}`，HTTP 状态码 200
