## Context

TaskState 已在 context-engineering-optimization 变更中实现了数据结构定义、任务动词检测和 Markdown 格式化注入。但持久化未生效：`AgentCustomState.task_state` 字段从未被写入，`InMemorySaver()` checkpointer 因未传入 `thread_id` 而无法关联 checkpoint。当前 TaskState 在每个请求中从零创建，无法跨请求追踪多步任务进度。

现有代码基础：
- `task_state.py`：TaskState/TaskStep TypedDict、is_task_message()、create_task_state()、format_task_state()
- `agent.py`：`AgentCustomState` 已通过 `state_schema` 传入 `create_agent`，`InMemorySaver()` 已配置
- `api/chat.py`：`session_id` 已传递到 `agent_manager.astream(message, history, session_id=session_id)`

## Goals / Non-Goals

**Goals:**

- 激活 LangGraph checkpointer 持久化，TaskState 跨请求自动恢复
- 将 TaskState 写入 `AgentCustomState.task_state`，而非仅作为局部变量
- 提供 `update_task` 工具，允许 Agent 主动更新步骤状态、添加产物和阻塞项
- 后续请求优先恢复已有 TaskState，仅在新任务时创建

**Non-Goals:**

- 不实现 TaskState 的前端可视化（P3，后续迭代）
- 不实现子任务/嵌套任务（P3，后续迭代）
- 不改变现有 SSE 事件流协议
- 不改变 workspace Markdown 文件

## Decisions

### 决策 1：使用 session_id 作为 thread_id

**选择**：将 `session_id` 直接作为 LangGraph checkpointer 的 `thread_id`。

**替代方案**：生成独立的 `task_id`，在 session 中维护 task_id → thread_id 映射。

**理由**：
- 当前系统一个 session 同时只有一个活跃任务（单线程对话模型）
- session_id 已经在 API 层可用，无需额外映射表
- `InMemorySaver` 按 thread_id 隔离状态，session_id 天然满足隔离需求
- 未来如需支持多任务，可在 session 内维护 task_id 前缀

### 决策 2：通过 Agent 工具实现状态更新

**选择**：定义 `update_task` 工具，Agent 通过工具调用来更新 TaskState。

**替代方案 A**：在中间件中自动检测工具调用结果并更新步骤。

**替代方案 B**：在系统提示中指示 Agent 直接修改 state 字段。

**理由**：
- 工具调用是 LangChain Agent 更新状态的标准方式，与现有工具（terminal、read_file 等）一致
- 自动检测（方案 A）需要推断哪个工具调用对应哪个步骤，不可靠
- 直接修改 state（方案 B）不是 LangGraph Agent 的标准交互模式
- 工具方式显式、可控，Agent 有明确意图时才更新

### 决策 3：TaskState 恢复策略——加载 → 合并 → 注入

**选择**：每次请求从 checkpoint 加载已有 TaskState，新任务动词消息追加步骤而非覆盖。

**替代方案**：每次任务动词消息重置 TaskState。

**理由**：
- 用户可能在多步任务中发出新的子指令（如"现在帮我写测试"），应追加步骤而非覆盖
- 覆盖会导致已完成步骤信息丢失
- 合并策略：保留已有步骤，新消息作为新步骤追加

### 数据流

```
用户消息到达 (api/chat.py, session_id)
  ↓
agent.astream(message, history, session_id)
  ↓
[TaskState 恢复]
  ├─ config={"configurable": {"thread_id": session_id}}  ← 激活 checkpoint
  ├─ 从 agent.astream 返回的 state 中读取 task_state
  ├─ 有活跃 task_state → 合并新任务/更新步骤
  └─ 无活跃 task_state + is_task_message → 创建新 TaskState
  ↓
[TaskState 写入]
  ├─ 写入 AgentCustomState.task_state
  └─ checkpointer 自动持久化
  ↓
[TaskState 格式化注入]
  ├─ format_task_state() → Markdown
  └─ build_dynamic_prefix(task_state=...) → SystemMessage
  ↓
[Agent 调用]
  ├─ Agent 使用 update_task 工具更新步骤
  ├─ 工具执行后 task_state 更新写入 state
  └─ checkpointer 持久化更新后的状态
  ↓
SSE 事件流返回
```

## Risks / Trade-offs

**[风险] InMemorySaver 重启丢失** → InMemorySaver 是内存级 checkpointer，进程重启后所有 TaskState 丢失。当前阶段可接受，未来升级为 SqliteSaver 持久化存储。缓解：TaskState 的核心信息（目标、步骤）同时以 SystemMessage 注入，即使 checkpoint 丢失，Agent 仍可从上下文推断。

**[风险] update_task 工具增加 token 开销** → 每次工具调用消耗 token。缓解：工具参数精简（仅 action + description），系统提示指引 Agent 在关键节点更新而非每步都更新。

**[权衡] session_id 作为 thread_id 的局限** → 一个 session 只有一个 TaskState 状态槽。如果用户在一个 session 中切换任务，旧任务状态被覆盖。可接受：对话上下文本身是线性的。

## Open Questions

- `update_task` 工具的调用频率控制：是否需要中间件限制调用次数（避免 Agent 反复调用浪费 token）？
- TaskState 步骤上限：是否需要限制最大步骤数防止 state 膨胀？
