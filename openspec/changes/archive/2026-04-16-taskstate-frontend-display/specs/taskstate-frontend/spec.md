## ADDED Requirements

### Requirement: TaskCard 对话流内嵌组件

系统 SHALL 在对话流中、触发任务的用户消息之后，渲染一张 TaskCard 摘要卡片。

TaskCard MUST 显示：
- 任务目标（goal 字段）
- 步骤完成计数标签（如 "2/4"）
- 步骤摘要（单行状态图标列表）
- 产物路径列表

TaskCard 的视觉风格 MUST 与现有 ThoughtChain 卡片保持一致：`rounded-xl border border-black/[0.04] bg-white/50`。

每次 `task_update` SSE 事件到达时，TaskCard MUST 原地更新内容，不产生新的 DOM 元素。

任务所有步骤完成后，TaskCard MUST 保留显示并带有完成标记，不自动隐藏。

#### Scenario: 任务创建时 TaskCard 出现

- **WHEN** Agent 首次创建 TaskState，SSE 推送 task_update 事件
- **THEN** 前端 MUST 在对话流中、触发任务的用户消息之后渲染 TaskCard，显示任务目标和初始步骤

#### Scenario: 步骤状态变更时 TaskCard 更新

- **WHEN** Agent 通过 update_task 工具更新某个步骤的状态，SSE 推送 task_update 事件
- **THEN** TaskCard MUST 原地更新对应步骤的状态图标和完成计数，不重新创建卡片

#### Scenario: 任务完成后 TaskCard 保留

- **WHEN** 所有步骤状态变为 completed
- **THEN** TaskCard MUST 继续显示在对话流中，带完成标记（如 ✓ 图标），不自动隐藏或折叠

### Requirement: TaskProgressPanel 输入框上方进度面板

系统 SHALL 在聊天输入框正上方渲染 TaskProgressPanel 组件，与输入框共享边框形成视觉整体。

收起态 MUST 显示：
- 蓝色圆点指示器
- 任务目标名称
- 步骤完成计数（如 "2/4 步骤完成"）
- 迷你进度条
- 展开箭头

展开态 MUST 显示（向上弹出）：
- 标题行 + 收起按钮
- 完整进度条
- 步骤列表（状态图标 + 描述 + 结果摘要）
- 产物路径列表

无活跃 TaskState 时（currentTaskState === null），TaskProgressPanel MUST 完全隐藏（不渲染）。

展开/收起 MUST 使用 `animate-fade-in` 动画过渡。

#### Scenario: 无任务时面板隐藏

- **WHEN** currentTaskState 为 null
- **THEN** TaskProgressPanel MUST NOT 渲染任何 DOM 元素，不占用空间

#### Scenario: 任务创建时面板以收起态出现

- **WHEN** 首次收到 task_update 事件，currentTaskState 从 null 变为有效值
- **THEN** TaskProgressPanel MUST 以收起态出现，显示紧凑进度条

#### Scenario: 点击收起态展开详情

- **WHEN** 用户点击 TaskProgressPanel 收起态区域
- **THEN** 面板 MUST 向上弹出展开态，显示完整步骤列表和产物信息

#### Scenario: 点击展开态收起

- **WHEN** 用户点击 TaskProgressPanel 展开态的收起按钮
- **THEN** 面板 MUST 收起为紧凑进度条形态

#### Scenario: 任务完成后面板保持收起态

- **WHEN** 所有步骤变为 completed
- **THEN** TaskProgressPanel MUST 保持收起态显示，进度条显示 100%

### Requirement: 前端 TaskState 状态管理

系统 SHALL 在 store 中新增 `currentTaskState` 状态字段，类型为 `TaskState | null`。

TaskState 接口 MUST 包含：session_id、goal、steps（TaskStep 数组）、artifacts、decisions、blockers。

TaskStep 接口 MUST 包含：description、status（"pending" | "in_progress" | "completed" | "blocked"）、可选 result_summary。

SSE 事件处理中 MUST 新增 `task_update` 事件类型处理：收到事件时更新 currentTaskState。

会话切换时 MUST 清空 currentTaskState 为 null。

#### Scenario: 收到 task_update 事件更新状态

- **WHEN** SSE 流中收到 `{"type": "task_update", "task_state": {...}}` 事件
- **THEN** store MUST 将 currentTaskState 更新为事件中的 task_state 值

#### Scenario: 会话切换清空状态

- **WHEN** 用户切换到另一个会话
- **THEN** currentTaskState MUST 被重置为 null

### Requirement: TaskState 恢复 API 调用

系统 SHALL 在 `api.ts` 中提供 `fetchTaskState(sessionId)` 函数，调用 `GET /api/sessions/{id}/task-state`。

前端 MUST 在以下时机调用恢复 API：
- 切换到有对话历史的会话时

收到 SSE `task_update` 事件后不再需要调用恢复 API（SSE 事件已包含完整 task_state）。

#### Scenario: 切换到有历史的会话时恢复

- **WHEN** 用户切换到一个有对话历史的会话
- **THEN** 前端 MUST 调用 `GET /api/sessions/{id}/task-state`，如果返回有效 TaskState 则更新 currentTaskState

#### Scenario: 会话无活跃任务时恢复返回 null

- **WHEN** 前端调用恢复 API，该会话无活跃 TaskState
- **THEN** currentTaskState MUST 保持为 null，不显示任何任务组件

### Requirement: 步骤状态图标与颜色映射

系统 MUST 使用以下状态图标和颜色映射渲染步骤：

| 状态 | 图标 | 颜色 |
|------|------|------|
| pending | ⬜ 或空心圆 | #999 |
| in_progress | 🔄 或蓝色实心 | #002fa7 |
| completed | ✅ 或绿色勾 | #2e7d32 |
| blocked | ❌ 或红色叉 | #d32f2f |

in_progress 状态的步骤 MUST 高亮显示（加粗 + 浅蓝背景 `bg-[#f0f4ff]`）。

#### Scenario: in_progress 步骤高亮

- **WHEN** 渲染一个 in_progress 状态的步骤
- **THEN** 步骤行 MUST 使用蓝色文字（#002fa7）、font-weight: 500、浅蓝背景（#f0f4ff）

#### Scenario: completed 步骤正常显示

- **WHEN** 渲染一个 completed 状态的步骤
- **THEN** 步骤行 MUST 使用绿色图标（✅）和灰色文字（#999）
