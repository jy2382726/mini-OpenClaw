## 1. 后端：SSE task_update 事件推送

- [x] 1.1 在 `backend/graph/agent.py` 的 `astream` 方法 updates 分支中，新增 task_state 变更检测：当 node_data 包含非 null 的 `task_state` 字段时，yield `{"type": "task_update", "task_state": node_data["task_state"]}`
- [x] 1.2 验证 LangGraph `astream(stream_mode=["updates"])` 返回的 update 中确实包含 `task_state` 字段（通过日志打印 node_data 确认）。如果验证不通过，回退到在 tools node 更新时调用 `aget_state()` 读取 task_state 的方案

## 2. 后端：TaskState 恢复 API

- [x] 2.1 在 `backend/api/` 目录下新增 `GET /api/sessions/{session_id}/task-state` 端点，通过 `agent.aget_state(config)` 从 checkpoint 读取 task_state 并返回 `{"task_state": <TaskState 或 null>}`

## 3. 前端：状态管理

- [x] 3.1 在 `frontend/src/lib/store.tsx` 中新增 `TaskStep` 和 `TaskState` TypeScript 接口定义
- [x] 3.2 在 store 中新增 `currentTaskState: TaskState | null` 状态字段，默认值 null
- [x] 3.3 在 SSE 事件处理逻辑中新增 `task_update` 事件类型处理：收到事件时更新 `currentTaskState`
- [x] 3.4 在会话切换逻辑中清空 `currentTaskState` 为 null
- [x] 3.5 在 `frontend/src/lib/api.ts` 中新增 `fetchTaskState(sessionId)` 函数，调用 `GET /api/sessions/{id}/task-state`
- [x] 3.6 在切换到有对话历史的会话时调用 `fetchTaskState` 恢复 TaskState

## 4. 前端：TaskCard 组件

- [x] 4.1 创建 `frontend/src/components/chat/TaskCard.tsx` 组件，接收 `taskState: TaskState` props
- [x] 4.2 实现 TaskCard 视觉：任务目标 + 完成计数标签 + 步骤摘要行 + 产物路径列表，复用 ThoughtChain 卡片风格
- [x] 4.3 实现 in_progress 步骤高亮样式（蓝色文字 + 浅蓝背景）
- [x] 4.4 在 MessageList 中，将 TaskCard 渲染在触发任务的用户消息之后，通过 SSE 事件携带的 session_id 与消息关联

## 5. 前端：TaskProgressPanel 组件

- [x] 5.1 创建 `frontend/src/components/chat/TaskProgressPanel.tsx` 组件，接收 `taskState: TaskState | null` props
- [x] 5.2 实现收起态：蓝色圆点 + 任务目标 + "N/M 步骤完成" + 迷你进度条 + 展开箭头
- [x] 5.3 实现展开态：标题行 + 完整进度条 + 步骤列表（状态图标 + 描述 + 结果摘要）+ 产物列表 + 收起按钮
- [x] 5.4 实现展开/收起切换逻辑，使用 `animate-fade-in` 动画
- [x] 5.5 当 `taskState === null` 时完全不渲染（不占空间）
- [x] 5.6 在 ChatPage 布局中，将 TaskProgressPanel 放置在 ChatInput 正上方，共享边框

## 6. 测试验证

- [x] 6.1 后端验证：发送任务性消息 → 确认 SSE 流中包含 task_update 事件 → 确认恢复 API 返回正确 TaskState
- [x] 6.2 前端验证：TaskCard 在对话流中正确出现并实时更新 → TaskProgressPanel 收起/展开切换正常 → 页面刷新后 TaskState 恢复 → 会话切换时状态正确清空
