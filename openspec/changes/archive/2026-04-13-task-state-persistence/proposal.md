## Why

当前 TaskState 在每次请求中作为局部变量创建，格式化为 Markdown 后注入 SystemMessage，请求结束即丢弃。`AgentCustomState` 和 `InMemorySaver()` 虽已传入 `create_agent`，但因未传入 `thread_id` 且未将 TaskState 写入 state 字段，持久化实际不生效。这导致多步任务（如"帮我重构整个模块"）无法跨请求追踪进度，Agent 无法感知上一步完成了什么。

## What Changes

- 为 `agent.astream()` / `agent.ainvoke()` 的 LangGraph 调用传入 `config={"configurable": {"thread_id": session_id}}`，激活 checkpointer 持久化
- 将创建的 TaskState 写入 `AgentCustomState.task_state` 字段，而非仅作为局部变量
- 后续请求优先从 checkpoint 恢复已有 TaskState，仅在无活跃任务时创建新的
- 为 Agent 提供 `update_task` 工具，允许 Agent 更新步骤状态、添加产物和阻塞项
- 通过系统提示指引 Agent 在完成工具调用后主动更新任务状态

## Capabilities

### New Capabilities

（无新增能力域）

### Modified Capabilities

- `task-state`: 补充 TaskState 跨请求持久化的 thread_id 传递机制、Agent 主动更新任务状态的工具和提示指引

## Impact

- `backend/graph/agent.py` — 传入 thread_id、读取/写入 AgentCustomState.task_state
- `backend/graph/task_state.py` — 新增 `update_task` 工具定义
- `backend/graph/prompt_builder.py` — 可选：在 Zone 3 中加入任务状态更新指引
- `backend/api/chat.py` — 可能需要传递 session_id 到 agent 调用链
- `backend/config.json` — `features.task_state` 开关已存在，无需新增
