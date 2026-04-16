## Why

后端 TaskState 管理已完整实现（task_state.py + update_task_tool.py），支持自动检测任务性消息、创建/更新步骤、持久化到 checkpoint。但 TaskState 仅作为 SystemMessage 注入 Agent 上下文指导其自身跟踪进度，前端完全没有对应的 UI 组件。用户无法直观看到任务目标、步骤进度和产物信息。

## What Changes

- 新增 SSE 事件类型 `task_update`，在任务创建、步骤变更、任务完成时推送 TaskState 到前端
- 新增前端组件 TaskCard：对话流内嵌任务摘要卡片，跟随触发任务的用户消息显示
- 新增前端组件 TaskProgressPanel：输入框上方的可折叠进度面板，收起态显示紧凑进度条，展开态显示完整步骤列表
- 新增后端 API 端点 `GET /sessions/{id}/task-state`：用于页面刷新/会话切换时恢复 TaskState
- 修改后端 agent.py astream 方法：在 updates 模式中检测 task_state 变更并推送 SSE 事件
- 前端 store 新增 currentTaskState 状态字段，SSE 处理中新增 task_update 事件类型

## Capabilities

### New Capabilities
- `taskstate-frontend`: 前端 TaskState 展示层——包含 TaskCard/TaskProgressPanel 组件、store 状态管理、SSE 事件处理、恢复 API 调用

### Modified Capabilities
- `task-state`: 新增 SSE 事件推送需求——agent.py astream 需在 updates 模式中检测 task_state 变更并 yield task_update 事件
- `checkpoint-projection`: 新增 task-state 恢复端点——`GET /sessions/{id}/task-state` 从 checkpoint 读取 TaskState 供前端恢复

## Impact

- `backend/graph/agent.py` — astream 方法新增 task_state 变更检测逻辑
- `backend/api/` — 新增 task-state 恢复端点（可放在 sessions 路由或新建路由）
- `frontend/src/lib/store.tsx` — 新增 currentTaskState 状态、task_update SSE 事件处理
- `frontend/src/lib/api.ts` — 新增 fetchTaskState API 调用
- `frontend/src/components/chat/` — 新增 TaskCard.tsx、TaskProgressPanel.tsx 组件
- `frontend/src/app/` — ChatPage 布局调整，集成 TaskProgressPanel
