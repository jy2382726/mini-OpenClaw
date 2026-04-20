# hitl-tool-approval Specification

## Purpose
TBD - created by archiving change hitl-tool-approval. Update Purpose after archive.
## Requirements
### Requirement: HITL 配置管理

系统 SHALL 在 `config.json` 中提供 `hitl` 配置段，包含以下字段：
- `enabled`：布尔值，控制 HITL 审批开关，默认 `false`
- `approval_required`：字符串数组，列出需要审批的工具名称，默认 `["terminal", "write_file", "python_repl"]`
- `timeout_seconds`：整数，前端超时倒计时秒数，默认 `30`
- `timeout_action`：字符串，超时后的动作，默认 `"reject"`

系统 SHALL 通过 `get_hitl_config()` 函数读取配置，复用现有 TTL 缓存机制（30秒），支持热重载。

系统 SHALL 在 `get_settings_for_display()` 输出中包含 `hitl` 配置段，供前端展示。

#### Scenario: 默认配置加载

- **WHEN** config.json 不包含 `hitl` 段
- **THEN** `get_hitl_config()` 返回默认值 `{enabled: false, approval_required: ["terminal", "write_file", "python_repl"], timeout_seconds: 30, timeout_action: "reject"}`

#### Scenario: 热重载配置变更

- **WHEN** 用户在 config.json 中将 `hitl.enabled` 从 `false` 改为 `true`
- **THEN** 30 秒 TTL 缓存过期后，下一次请求的 `get_hitl_config()` 返回 `enabled: true`

#### Scenario: 自定义审批工具列表

- **WHEN** 用户配置 `approval_required: ["terminal", "write_file"]`
- **THEN** 只有 `terminal` 和 `write_file` 触发审批，其他工具直接执行

### Requirement: Agent interrupt_before 配置

当 `hitl.enabled` 为 `true` 且 `approval_required` 列表非空时，`_build_agent()` SHALL 向 `create_agent` 传入 `interrupt_before=["tools"]`，使 Agent 在执行工具节点前暂停，checkpoint 自动保存当前状态。

当 `hitl.enabled` 为 `false` 或 `approval_required` 为空时，SHALL NOT 传入 `interrupt_before` 参数，Agent 行为与当前一致。

#### Scenario: HITL 启用时 Agent 暂停

- **WHEN** `hitl.enabled=true` 且 Agent 请求执行 `terminal` 工具
- **THEN** Agent 在工具节点前暂停，checkpoint 保存包含 `tool_calls` 的 AIMessage

#### Scenario: HITL 关闭时 Agent 不中断

- **WHEN** `hitl.enabled=false` 且 Agent 请求执行 `terminal` 工具
- **THEN** 工具直接执行，行为与当前完全一致

### Requirement: SSE 流中断检测与 tool_approval 事件

`event_generator` 在 SSE 流循环结束后 SHALL 检测 Agent 是否被 interrupt 暂停。检测方式：调用 `agent.aget_state(thread_config)`，若 `snapshot.next` 非空（有待执行节点），说明被中断。

当检测到中断时，SHALL 从 checkpoint 的 `messages` 中提取最后一个包含 `tool_calls` 的 AIMessage，筛选出 `tool_call.name` 在 `approval_required` 列表中的工具调用。

SHALL 发送 `tool_approval` SSE 事件，包含 `pending_tools` 数组（每个元素含 `tool_call_id`、`tool`、`input`）和 `session_id`。

发送 `tool_approval` 后 SHALL 停止 SSE 流（不发送 `done` 事件），流自然结束。

#### Scenario: 需审批工具触发 tool_approval 事件

- **WHEN** HITL 启用且 Agent 请求执行 `terminal`（在审批列表中）
- **THEN** SSE 流按序发送 `tool_start` → `tool_approval`，然后流结束（不发 `done`）

#### Scenario: 免审批工具不触发中断

- **WHEN** HITL 启用但 Agent 请求执行 `read_file`（不在审批列表中）
- **THEN** 工具直接执行，SSE 流发送 `tool_start` → `tool_end`，流程正常继续

#### Scenario: 断线后中断状态持久化

- **WHEN** SSE 流在 tool_approval 事件后断开，用户重新连接并加载历史消息
- **THEN** checkpoint 中保留 interrupt 状态，pending_approval 工具调用可被前端重新渲染审批按钮

### Requirement: 审批端点 approve

SHALL 提供 `POST /api/chat/approve` 端点，接受 `ApprovalRequest`（包含 `session_id` 和 `tool_call_id`）。

端点 SHALL 从 checkpoint 恢复 Agent 执行（不传新消息，使用 `None` 作为输入），返回 SSE 流。

恢复后 Agent SHALL 正常执行被批准的工具，SSE 流产出 `tool_end`、`token`、`done` 等标准事件。

#### Scenario: 批准后工具正常执行

- **WHEN** 用户对 `terminal` 工具调用点击「批准」
- **THEN** `POST /api/chat/approve` 返回 SSE 流，包含 `tool_end`（工具输出）、`token`（后续 LLM 响应）、`done`

#### Scenario: 批准不存在的 tool_call_id

- **WHEN** 请求中 `tool_call_id` 不匹配 checkpoint 中任何待审批工具
- **THEN** 返回 HTTP 404 错误，响应体包含错误描述

### Requirement: 拒绝端点 reject

SHALL 提供 `POST /api/chat/reject` 端点，接受 `ApprovalRequest`（包含 `session_id` 和 `tool_call_id`）。

端点 SHALL 先向 checkpoint 注入一条 `ToolMessage`，内容为 `"用户拒绝了此工具调用"`，`tool_call_id` 匹配被拒绝的工具调用。

注入拒绝消息后 SHALL 从 checkpoint 恢复 Agent 执行，返回 SSE 流。Agent 收到拒绝信息后 SHALL 自行调整响应路径。

#### Scenario: 拒绝后 Agent 调整响应

- **WHEN** 用户对 `write_file` 工具调用点击「拒绝」
- **THEN** `POST /api/chat/reject` 注入拒绝 ToolMessage → 恢复执行 → SSE 流包含 Agent 调整后的文本响应和 `done`

#### Scenario: 拒绝后 Agent 仍可继续对话

- **WHEN** 用户拒绝一个工具调用后
- **THEN** Agent 可以选择使用其他工具或直接文本回复，SSE 流正常完成（发 `done`）

### Requirement: 前端 ToolCall 状态扩展

`ToolCall` 接口 SHALL 扩展：`status` 字段新增 `"pending_approval"` 值，新增 `toolCallId` 可选字段（字符串类型），新增 `timeoutSeconds` 可选字段（整数类型，来自后端配置）。

前端 SSE 事件处理 SHALL 新增 `tool_approval` 事件处理：将 `pending_tools` 映射为 `status: "pending_approval"` 的 ToolCall 对象，附加到当前 assistant 消息的 `toolCalls` 数组。`timeout_seconds` 字段 SHALL 存入每个 ToolCall 的 `timeoutSeconds`。

#### Scenario: tool_approval 事件更新 ToolCall 状态

- **WHEN** 前端收到 `tool_approval` SSE 事件
- **THEN** 事件中每个 `pending_tools` 条目被映射为 `{tool, input, status: "pending_approval", toolCallId, timeoutSeconds}`，附加到当前 assistant 消息的 `toolCalls`

#### Scenario: 批准后 ToolCall 状态恢复

- **WHEN** 用户批准工具调用后，approve SSE 流返回 `tool_end` 事件
- **THEN** 对应 ToolCall 的 `status` 从 `"pending_approval"` 变为 `"done"`，`output` 字段被填充

#### Scenario: 批量审批防竞态

- **WHEN** 同一消息中有多个 `pending_approval` 工具调用，用户点击其中任一按钮
- **THEN** 同消息内所有 `pending_approval` 工具统一标记为 `running`，禁用全部审批按钮

### Requirement: 审批按钮渲染

ThoughtChain 组件 SHALL 在 `ToolCall.status === "pending_approval"` 时渲染审批按钮区域，包含：
- 「批准」按钮（绿色）
- 「拒绝」按钮（红色）
- 超时倒计时文字提示（基于后端传递的 `timeout_seconds`，存于 `ToolCall.timeoutSeconds`）

按钮点击 SHALL 调用对应的 `approveTool` 或 `rejectTool` API 函数，解析返回的 SSE 流，复用现有 SSE 事件处理逻辑更新消息。

倒计时到期 SHALL 自动调用 `rejectTool`。

审批按钮区域 SHALL 支持 dark mode（`dark:` 前缀）。

#### Scenario: 审批按钮显示

- **WHEN** ThoughtChain 渲染一个 `status: "pending_approval"` 的 ToolCall
- **THEN** 显示批准按钮（绿色）、拒绝按钮（红色）和倒计时提示

#### Scenario: 点击批准按钮

- **WHEN** 用户点击「批准」按钮
- **THEN** 调用 `approveTool(sessionId, toolCallId)`，解析返回的 SSE 流，更新消息内容

#### Scenario: 超时自动拒绝

- **WHEN** 倒计时到期（默认 30 秒）用户无操作
- **THEN** 自动调用 `rejectTool(sessionId, toolCallId)`

#### Scenario: dark mode 兼容

- **WHEN** 系统处于 dark mode
- **THEN** 审批按钮区域使用 `dark:` 前缀样式正确显示

### Requirement: 审批 API 函数

前端 SHALL 提供 `approveTool(sessionId: string, toolCallId: string)` 和 `rejectTool(sessionId: string, toolCallId: string)` API 函数。

两个函数 SHALL 向对应端点发送 POST 请求，返回值 SHALL 支持与 `streamChat` 相同的 SSE 流解析（AsyncGenerator）。

#### Scenario: approveTool 调用

- **WHEN** 调用 `approveTool("session-123", "call-abc")`
- **THEN** 向 `POST /api/chat/approve` 发送 `{session_id: "session-123", tool_call_id: "call-abc", approved: true}`，返回可迭代的 SSE 事件流

#### Scenario: 网络错误处理

- **WHEN** approve 或 reject 请求因网络错误失败
- **THEN** 在对应 ToolCall 旁显示错误提示，不中断整个对话

