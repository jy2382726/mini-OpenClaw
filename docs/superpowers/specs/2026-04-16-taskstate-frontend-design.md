# TaskState 前端展示设计

**日期**: 2026-04-16
**状态**: 待实施

---

## 1. 背景

后端 `task_state.py` 已实现完整的任务状态管理：自动检测任务性消息 → 创建 TaskState（goal/steps/artifacts/decisions/blockers）→ Agent 通过 `update_task` 工具更新进度 → 持久化到 checkpoint。

但目前 TaskState 仅作为 SystemMessage 注入 Agent 上下文，前端完全没有对应的展示。用户无法直观看到任务目标和步骤进度。

## 2. 设计方案

### 2.1 双区域联动

**区域一：对话流内嵌任务摘要卡片**
- 紧跟在触发任务的用户消息之后，类似 ThoughtChain 工具调用卡片
- 显示：任务目标、步骤摘要（一行状态图标 + 描述）、产物列表
- 复用现有卡片风格：`rounded-xl border border-black/[0.04] bg-white/50`
- 每次 `task_update` SSE 事件到达时原地更新

**区域二：输入框上方进度面板**
- 位置：聊天输入框正上方，与输入框共享边框，形成视觉整体
- 收起态：一条紧凑进度条（蓝色圆点 + 任务名 + "N/M 步骤完成" + 迷你进度条 + 展开箭头）
- 展开态：向上弹出完整步骤列表 + 进度条 + 产物信息
- 无活跃任务时完全隐藏，不占空间

### 2.2 数据流

```
Agent 调用 update_task 工具
  → update_task_tool.py 返回 Command(update={task_state, messages})
  → LangGraph 处理 Command 更新 state
  → agent.py astream 在 updates 模式中检测到 task_state 变更
  → yield {"type": "task_update", "task_state": {...}}
  → SSE 推送到前端
  → 前端更新 store 中的 currentTaskState
  → TaskProgressBar + TaskCard 组件响应式更新
```

### 2.3 SSE 事件格式

后端新增事件类型：

```json
{
  "type": "task_update",
  "task_state": {
    "session_id": "default",
    "goal": "实现用户认证系统",
    "steps": [
      {"description": "JWT 认证中间件", "status": "completed"},
      {"description": "创建登录注册端点", "status": "in_progress", "result_summary": "POST /api/auth/login 已实现"},
      {"description": "前端登录页面", "status": "pending"},
      {"description": "联调测试", "status": "pending"}
    ],
    "artifacts": ["backend/api/auth.py", "frontend/src/pages/Login.tsx"],
    "decisions": [],
    "blockers": []
  }
}
```

推送时机（关键节点）：
- 任务创建时（`is_task_message` 检测通过，TaskState 首次创建）
- 步骤状态变更时（`update_step` 操作成功）
- 任务完成时（所有步骤变为 completed）

### 2.4 前端组件结构

```
ChatPage
├── MessageList
│   ├── MessageBubble (用户消息)
│   ├── TaskCard ← 新组件，对话流内嵌摘要
│   │   ├── 任务标题 + 完成计数标签
│   │   ├── 步骤摘要（单行：状态图标列表）
│   │   └── 产物路径列表
│   ├── MessageBubble (助手消息)
│   └── ...
├── TaskProgressPanel ← 新组件，输入框上方
│   ├── 收起态：进度条（圆点 + 标题 + N/M + 进度条 + ▲）
│   └── 展开态：
│       ├── 标题 + 收起按钮
│       ├── 进度条
│       ├── 完整步骤列表（状态图标 + 描述 + 结果摘要）
│       └── 产物路径列表
└── ChatInput
```

### 2.5 组件样式

与现有设计风格对齐：

| 属性 | 值 |
|------|-----|
| 卡片圆角 | `rounded-xl` (12px) |
| 边框 | `border border-black/[0.04]` |
| 卡片背景 | `bg-white/50` |
| 标题字号 | `text-[12px] font-semibold` |
| 步骤字号 | `text-[12px]` |
| 产物字号 | `text-[11px]` |
| 状态颜色 | pending=#999, in_progress=#002fa7, completed=#2e7d32, blocked=#d32f2f |
| 进度面板背景 | `bg-white`，与输入框共享边框 |
| 展开动画 | `animate-fade-in` (复用现有) |

### 2.6 状态管理

在 `store.tsx` 中新增：

```typescript
interface TaskStep {
  description: string;
  status: "pending" | "in_progress" | "completed" | "blocked";
  result_summary?: string;
}

interface TaskState {
  session_id: string;
  goal: string;
  steps: TaskStep[];
  artifacts: string[];
  decisions: string[];
  blockers: string[];
}

// store 新增字段
currentTaskState: TaskState | null;
```

SSE 流处理中新增 `task_update` 事件类型处理：
- 收到事件时更新 `currentTaskState`
- 会话切换时清空

### 2.7 后端改动

**agent.py** `astream` 方法中，在 `mode == "updates"` 分支新增检测：

```python
# 检测 task_state 变更
if "task_state" in node_data and node_data["task_state"] is not None:
    yield {
        "type": "task_update",
        "task_state": node_data["task_state"],
    }
```

注意：需要验证 LangGraph `astream(stream_mode=["updates"])` 返回的 update 中是否包含 `task_state` 字段。验证方法：`update_task` 工具返回 `Command(update={"task_state": updated, ...})`，LangGraph 处理 Command 时会在对应 node 的 update 中包含非 messages 字段。如果验证不通过，备选方案：在 `astream` 的 updates 分支中，检测到 `tools` node 的 messages 更新时，同步读取 `agent.aget_state()` 获取最新 task_state。

### 2.8 任务状态恢复 API

**端点**：`GET /api/sessions/{session_id}/task-state`

用途：页面刷新、会话切换时从 checkpoint 恢复 TaskState，避免页面加载时丢失进度。

```python
@router.get("/sessions/{session_id}/task-state")
async def get_task_state(session_id: str):
    agent = agent_manager._build_agent()
    config = {"configurable": {"thread_id": session_id}}
    snapshot = await agent.aget_state(config)
    task_state = snapshot.values.get("task_state") if snapshot and snapshot.values else None
    return {"task_state": task_state}
```

前端调用时机：
- 切换到有对话历史的会话时，调用此端点检查是否有活跃 TaskState
- 收到 SSE `task_update` 事件后不再需要调用

### 2.9 无任务时行为

- `currentTaskState === null` 时，TaskProgressPanel 完全隐藏（不渲染）
- 对话流中不显示 TaskCard
- 任务所有步骤完成后，TaskCard 保留显示（带 ✅ 完成标记），TaskProgressPanel 保持收起态

### 2.10 边界场景

| 场景 | 行为 |
|------|------|
| 页面刷新/会话切换 | 调用 `GET /sessions/{id}/task-state` 恢复 |
| 多轮对话任务追加 | steps 列表追加新步骤，TaskCard 和 TaskProgressPanel 同步更新 |
| SummarizationMiddleware 触发 | task_state 不参与摘要（独立于 messages），不受影响 |
| 会话被 clear | task_state 随 checkpoint 一起清理，前端隐藏进度面板和卡片 |

## 3. 不做的事

- **不做任务编辑功能**：前端只展示，不提供手动修改步骤的 UI
- **不做任务历史**：只展示当前活跃任务，已完成的不保留在进度面板
- **不做多任务并行**：当前设计是单任务（一个 session 一个活跃 TaskState），不处理多任务场景
- **不做子任务/层级**：steps 是扁平列表，不嵌套
