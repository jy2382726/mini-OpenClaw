## Purpose

定义前端 TaskState 展示层，包括 TaskCard 对话流内嵌组件、TaskProgressPanel 输入框上方进度面板、前端状态管理和恢复 API 调用。

## Requirements

### Requirement: TaskCard 对话流内嵌组件

系统 SHALL 在对话流中、触发任务的用户消息之后，渲染一张 TaskCard 摘要卡片。TaskCard 通过 `taskTriggerMsgId` 精确定位插入位置，绑定到具体的触发消息。

TaskCard MUST 显示：
- 任务目标（goal 字段）
- 步骤完成计数标签（如 "2/4"）
- 步骤摘要（单行状态图标列表，描述截断至 max-w-[120px]）
- 产物路径列表

TaskCard 的视觉风格 MUST 与现有 ThoughtChain 卡片保持一致：`rounded-xl border border-black/[0.04] bg-white/50`。入场使用 `animate-fade-in-scale` 动画。

每次 `task_update` SSE 事件到达时，TaskCard MUST 原地更新内容（React props 变更触发重渲染），不产生新的 DOM 元素。

任务所有步骤完成后，TaskCard MUST 保留显示并带有 CheckCircle2 完成图标，不自动隐藏。

#### Scenario: 任务创建时 TaskCard 出现

- **WHEN** Agent 首次创建 TaskState，SSE 推送 task_update 事件
- **THEN** 前端 MUST 在对话流中、触发任务的用户消息之后渲染 TaskCard，显示任务目标和初始步骤

#### Scenario: 步骤状态变更时 TaskCard 更新

- **WHEN** Agent 通过 update_task 工具更新某个步骤的状态，SSE 推送 task_update 事件
- **THEN** TaskCard MUST 原地更新对应步骤的状态图标和完成计数，不重新创建卡片

#### Scenario: 任务完成后 TaskCard 保留

- **WHEN** 所有步骤状态变为 completed
- **THEN** TaskCard MUST 继续显示在对话流中，带 CheckCircle2 图标，不自动隐藏或折叠

### Requirement: TaskProgressPanel 输入框上方进度面板

系统 SHALL 在聊天输入框上方渲染 TaskProgressPanel 组件。

收起态 MUST 显示：
- 蓝色圆点指示器（`bg-[#002fa7]`）
- 任务目标名称
- 步骤完成计数（如 "2/4 步骤完成"）
- 迷你进度条（`w-16 h-1.5`）
- 展开箭头（ChevronDown）

展开态 MUST 显示（向上弹出）：
- 标题行 + 收起按钮（ChevronUp）
- 完整进度条（含百分比）
- 步骤列表（状态图标 + 描述 + 结果摘要）
- 产物路径列表

无活跃 TaskState 时（currentTaskState === null），TaskProgressPanel MUST 完全隐藏（不渲染）。

展开态使用 `animate-fade-in` 动画过渡。收起时直接切换，无动画过渡。

TaskProgressPanel 使用 `rounded-t-xl border border-black/[0.06] border-b-0` 样式尝试与下方输入框衔接，但输入框是独立的 `rounded-2xl` 容器，两者视觉上未形成完全的共享边框一体化效果。

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

#### Scenario: 任务完成后面板保持

- **WHEN** 所有步骤变为 completed
- **THEN** TaskProgressPanel MUST 保持当前展开/收起状态显示，进度条显示 100%

### Requirement: 前端 TaskState 状态管理

系统 SHALL 在 store 中新增 `currentTaskState` 状态字段，类型为 `TaskState | null`。

TaskState 接口 MUST 包含：session_id、goal、steps（TaskStep 数组）、artifacts、decisions、blockers。

TaskStep 接口 MUST 包含：description、status（"pending" | "in_progress" | "completed" | "blocked"）、可选 result_summary。

SSE 事件处理中 MUST 新增 `task_update` 事件类型处理：收到事件时更新 currentTaskState。

会话切换时 MUST 清空 currentTaskState 为 null。

新消息发送时 MUST 重置 `taskTriggerMsgId`，以便下次任务创建时绑定到新的触发消息。

#### Scenario: 收到 task_update 事件更新状态

- **WHEN** SSE 流中收到 `{"type": "task_update", "task_state": {...}}` 事件
- **THEN** store MUST 将 currentTaskState 更新为事件中的 task_state 值

#### Scenario: 会话切换清空状态

- **WHEN** 用户切换到另一个会话
- **THEN** currentTaskState MUST 被重置为 null

#### Scenario: 清空会话时清除 TaskState

- **WHEN** 用户清空当前会话
- **THEN** currentTaskState MUST 被重置为 null

### Requirement: TaskState 恢复 API 调用

系统 SHALL 在 `api.ts` 中提供 `fetchTaskState(sessionId)` 函数，调用 `GET /api/sessions/{id}/task-state`。

前端 MUST 在以下时机调用恢复 API：
- 切换到有对话历史的会话时（在 `apiGetSessionHistory` 返回有效消息后调用）

#### Scenario: 切换到有历史的会话时恢复

- **GIVEN** 目标会话有 checkpoint 数据
- **WHEN** 用户切换到一个有对话历史的会话
- **THEN** 前端 MUST 调用 `GET /api/sessions/{id}/task-state`，如果返回有效 TaskState 则更新 currentTaskState

#### Scenario: 会话无活跃任务时恢复返回 null

- **GIVEN** 该会话无活跃 TaskState
- **WHEN** 前端调用恢复 API
- **THEN** currentTaskState MUST 保持为 null，不显示任何任务组件

#### Scenario: 恢复 API 网络错误时静默降级

- **WHEN** 前端调用 `GET /api/sessions/{id}/task-state` 返回网络错误或超时
- **THEN** currentTaskState MUST 保持为 null，MUST NOT 弹出错误提示或阻塞 UI（`.catch(() => {})` 静默处理）

### Requirement: 步骤状态图标与颜色映射

系统 MUST 使用以下状态图标和颜色映射渲染步骤：

| 状态 | 图标 | 颜色 |
|------|------|------|
| pending | Circle（空心圆） | #999 |
| in_progress | Loader2（旋转加载） | #002fa7 |
| completed | CheckCircle2（绿色勾） | #2e7d32 |
| blocked | XCircle（红色叉） | #d32f2f |

in_progress 状态的步骤 MUST 高亮显示（font-weight: 500 + 浅蓝背景 `bg-[#f0f4ff]` + 蓝色文字 `#002fa7`）。

completed 步骤使用绿色图标 + 灰色文字（#999）。

#### Scenario: in_progress 步骤高亮

- **WHEN** 渲染一个 in_progress 状态的步骤
- **THEN** 步骤行 MUST 使用蓝色文字（#002fa7）、font-weight: 500、浅蓝背景（#f0f4ff）

#### Scenario: completed 步骤正常显示

- **WHEN** 渲染一个 completed 状态的步骤
- **THEN** 步骤行 MUST 使用绿色图标（CheckCircle2）和灰色文字（#999）
