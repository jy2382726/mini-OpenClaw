## Context

后端 TaskState 管理已完整实现：
- `task_state.py` 定义了 TaskState/TaskStep TypedDict，提供 `is_task_message()` 检测、`apply_task_update()` 更新、`format_task_state()` 格式化
- `update_task_tool.py` 通过 `Command(update={"task_state": updated, "messages": [tool_msg]})` 更新 LangGraph 状态
- agent.py astream 中已实现 TaskState 创建/恢复逻辑（`_read_task_state()` + `is_task_message()` 检测）
- TaskState 通过 `AgentCustomState.task_state` 持久化到 SQLite checkpoint

前端现状：SSE 事件流支持 token/tool_start/tool_end/rag_hit/thought 等事件，但无 task_update 类型。store.tsx 管理 messages/toolCalls 等状态，但无 TaskState 相关字段。UI 组件有 ThoughtChain（工具调用卡片）和 Mem0Card（记忆卡片），可复用其视觉风格。

## Goals / Non-Goals

**Goals:**
- 在对话流中内嵌 TaskCard，紧跟触发任务的用户消息，显示任务目标、步骤状态和产物
- 在输入框上方新增 TaskProgressPanel，收起态紧凑进度条，展开态完整步骤列表
- 通过 SSE task_update 事件实时推送 TaskState 变更到前端
- 支持页面刷新/会话切换时从 checkpoint 恢复 TaskState

**Non-Goals:**
- 不做任务编辑 UI（前端只读展示）
- 不做任务历史（只展示当前活跃任务）
- 不做多任务并行（单 session 单活跃 TaskState）
- 不做子任务/层级嵌套

## Decisions

### 决策 1：SSE 事件推送而非轮询

**选择**：在 agent.py astream 的 updates 模式中检测 task_state 变更，yield task_update 事件

**理由**：SSE 是项目已有的事件推送机制，无需引入新的通信模式。TaskState 变更频率低（仅在工具调用时），推送效率远优于轮询。

**备选方案**：前端定时轮询 `/sessions/{id}/task-state`。缺点：浪费资源，延迟高，与现有架构不一致。

### 决策 2：双区域联动展示

**选择**：对话流内嵌 TaskCard + 输入框上方 TaskProgressPanel

**理由**：TaskCard 保留对话上下文关联（用户能看到哪个消息触发了任务），TaskProgressPanel 提供全局可见的实时进度（不被新消息推走）。两者共享同一 currentTaskState 数据源，无需同步——都是被动响应 store 状态变更。

**备选方案**：仅用对话流内嵌卡片（方案 B）。缺点：滚动后看不到进度。

### 决策 3：关键节点推送而非每次变更

**选择**：仅在任务创建、步骤状态变更、任务完成时推送 task_update

**理由**：减少 SSE 事件数量，降低前端渲染频率。update_task 工具可能被频繁调用（如连续 add_step），无需每次都推送。

### 决策 4：agent.py updates 模式检测 task_state

**选择**：在 `astream(stream_mode=["updates"])` 的 updates 分支中，检测 node_data 是否包含 task_state 字段

**理由**：LangGraph 的 Command(update={task_state: ...}) 处理后，updates 模式返回的 node_data 会包含非 messages 字段。这是最直接的检测方式。

**备选方案**：在 tools node 的 messages 更新中，同步调用 `aget_state()` 读取最新 task_state。缺点：额外的 checkpoint 读取开销。

### 决策 5：恢复端点复用 aget_state

**选择**：`GET /sessions/{id}/task-state` 通过 `agent.aget_state(config)` 从 checkpoint 读取

**理由**：TaskState 持久化在 checkpoint 中，aget_state 是标准读取 API。无需额外存储。

### 数据流

```
Agent 调用 update_task 工具
  → update_task_tool.py 返回 Command(update={task_state, messages})
  → LangGraph 处理 Command 更新 state
  → agent.py astream updates 模式检测到 task_state 变更
  → yield {"type": "task_update", "task_state": {...}}
  → SSE 推送到前端
  → 前端 store 更新 currentTaskState
  → TaskCard + TaskProgressPanel 响应式更新
```

## Risks / Trade-offs

- [LangGraph updates 模式是否包含 task_state] → 需在 Phase 1 验证：在 agent.py updates 分支中打印 node_data 确认 task_state 字段存在。如果不存在，回退到 aget_state 方案
- [双区域展示的一致性] → 两者共享同一个 store.currentTaskState，React 响应式更新保证一致，无额外同步逻辑
- [SSE 事件丢失] → 页面刷新/会话切换时通过恢复 API 补偿，不依赖事件完整性
- [TaskProgressPanel 占用纵向空间] → 收起态仅一行（~32px），展开态向上弹出，不推动对话内容
