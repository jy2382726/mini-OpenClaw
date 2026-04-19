## Purpose

定义将 LangGraph checkpoint 转换为前端所需格式的投影服务，包括 UI 气泡投影、调试视图投影、clear/delete 语义等。系统已完全切换到 checkpoint 作为数据源，不提供 JSON 文件回退。

## Requirements

### Requirement: CheckpointHistoryService UI 气泡投影

系统 SHALL 提供 `CheckpointHistoryService`，将 LangGraph checkpoint 中的 message state 转换为前端聊天气泡所需的 DTO 格式。

投影输出 MUST 为 JSON 数组，每个元素包含：`role`（user/assistant）、`content`（文本内容）、`tool_calls`（可选，工具调用列表）。

投影 MUST 正确处理以下 LangGraph 消息类型的转换：
- `HumanMessage` → `{role: "user", content: ...}`
- `AIMessage` → `{role: "assistant", content: ..., tool_calls: ...}`
- `ToolMessage` → 挂接到前一个含 tool_calls 的 AIMessage 上，作为其输出

#### Scenario: 基本用户-助手对话投影

- **WHEN** checkpoint 中存在一条 HumanMessage 和一条 AIMessage
- **THEN** 投影输出 MUST 包含两个 DTO 对象，role 分别为 "user" 和 "assistant"，content 对应原文

#### Scenario: 工具调用消息的 tool_calls 挂接

- **WHEN** checkpoint 中存在一条 AIMessage（含 tool_calls）和对应的 ToolMessage
- **THEN** 投影 MUST 将 ToolMessage 的内容作为对应 tool_call 的 output 字段挂接到 AIMessage 的 tool_calls 数组中

#### Scenario: 连续 assistant 消息的合并

- **WHEN** checkpoint 中存在两条连续的 AIMessage（如 Agent 先执行工具再生成文本）
- **THEN** 投影 MUST 将它们作为两个独立的 DTO 对象输出，与当前前端 new_response 事件的分段语义一致

#### Scenario: 仅工具调用无文本的 assistant 消息

- **WHEN** checkpoint 中存在一条 AIMessage 仅有 tool_calls 但 content 为空
- **THEN** 投影 MUST 保留该 DTO 对象，content 为空字符串，tool_calls 包含完整的工具调用信息

### Requirement: CheckpointDebugViewService 调试视图投影

系统 SHALL 提供 `CheckpointDebugViewService`，将 checkpoint 状态转换为近似调试视图，用于 Raw Messages 面板展示。

该服务 MUST 明确标注为"近似调试视图"，而非 Agent 本轮真实输入。

输出 MUST 包含：`system_prompt`（从 `build_system_prompt()` 生成）+ 投影后的消息列表。

#### Scenario: 调试视图包含系统提示

- **GIVEN** checkpoint 中存在消息数据
- **WHEN** 前端调用 `GET /api/sessions/{session_id}/messages`
- **THEN** 返回结果 MUST 在消息列表开头包含一条 `{role: "system", content: <system_prompt>}` 消息

#### Scenario: 调试视图标注为近似

- **GIVEN** checkpoint 中存在消息数据
- **WHEN** 前端展示 Raw Messages 面板
- **THEN** API 响应 MUST 包含 `is_approximation: true` 字段，明确告知前端这不是 Agent 真实输入

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

- **GIVEN** 该会话有活跃 TaskState
- **WHEN** 前端请求 `GET /api/sessions/{id}/task-state`
- **THEN** 系统 MUST 返回完整的 TaskState 对象

#### Scenario: 无 TaskState 时返回 null

- **GIVEN** 该会话无活跃 TaskState
- **WHEN** 前端请求 `GET /api/sessions/{id}/task-state`
- **THEN** 系统 MUST 返回 `{"task_state": null}`，HTTP 状态码 200

#### Scenario: checkpoint 不存在时返回 null

- **GIVEN** 该会话无 checkpoint 数据
- **WHEN** 前端请求 `GET /api/sessions/{id}/task-state`
- **THEN** 系统 MUST 返回 `{"task_state": null}`，HTTP 状态码 200

### Requirement: Agent 输入来源（Checkpoint 模式）

系统始终使用 checkpoint 模式恢复 Agent 历史。Agent 不接收应用层传入的历史消息，依赖 LangGraph 通过 `thread_id` 从 checkpoint 恢复先前 messages。

每次 `agent.astream/ainvoke` 调用 MUST 仅传入当前用户消息（`messages = [HumanMessage(content=message)]`），通过 `config={"configurable": {"thread_id": session_id}}` 激活 checkpoint 恢复。

#### Scenario: Agent 从 checkpoint 恢复历史

- **WHEN** 用户发送消息
- **THEN** 系统 MUST 仅传入当前用户消息，依赖 LangGraph 通过 `thread_id` 从 checkpoint 恢复先前 messages

#### Scenario: 恢复后消息不重复注入

- **WHEN** Agent 从 checkpoint 恢复了先前 messages
- **THEN** 系统 MUST 确保恢复的消息与当前消息不产生重复

### Requirement: JSON 消息写入已停止

系统不再在 SSE 完成后调用 `session_manager.save_message()` 写入 JSON 文件。流式中断时依赖 checkpoint 的最后完成 node 快照恢复。

`chat.py` 的 `finally` 块为空，注释说明"checkpoint 已有最后完成 node 的快照"。

#### Scenario: SSE 完成后不写入 JSON

- **WHEN** SSE 流正常完成
- **THEN** 系统 MUST NOT 调用 `session_manager.save_message()`，不产生新的 JSON 文件写入

#### Scenario: 流式中断时依赖 checkpoint 快照

- **WHEN** SSE 流在 Agent 执行过程中被客户端断开
- **THEN** 系统 MUST NOT 产生不完整的 checkpoint，后续恢复时仅展示上一次完整 checkpoint 中的消息

#### Scenario: 中断后用户重新进入会话

- **WHEN** 用户在流式中断后重新打开会话
- **THEN** 系统 MUST 仅展示上一次完整 checkpoint 中的消息，不包含中断轮次的部分内容

### Requirement: Token 统计基于 checkpoint 投影

系统 MUST 将 `GET /api/tokens/session/{session_id}` 的消息来源从 checkpoint 投影获取，确保 token 统计基于用户实际看到的历史。

#### Scenario: token 统计基于 checkpoint 数据

- **WHEN** 前端请求 `GET /api/tokens/session/{id}`
- **THEN** 系统 MUST 从 CheckpointHistoryService 投影获取消息列表进行 token 计算

### Requirement: clear 语义（物理删除 checkpoint 线程）

系统 MUST 确保 `POST /api/sessions/{session_id}/clear` 物理删除该 session 的 checkpoint 线程数据。

clear 的产品语义 MUST 满足：
1. 用户视角下，对话历史被清空
2. 后续对话不受旧 TaskState、旧摘要、旧 thread message 影响
3. 会话标题和元数据可保留

实现：调用 `checkpointer.adelete_thread(session_id)` 删除整个线程的 checkpoint 数据。

#### Scenario: clear 后前端历史为空

- **GIVEN** 该会话存在消息数据
- **WHEN** 用户调用 `POST /api/sessions/{id}/clear`
- **THEN** 后续 `GET /api/sessions/{id}/history` MUST 返回空消息列表

#### Scenario: clear 后后续对话不受旧状态影响

- **WHEN** 用户 clear 后在同一会话中发送新消息
- **THEN** 新对话 MUST NOT 受到旧 TaskState、旧摘要、旧 thread message 的影响

#### Scenario: clear 保留元数据

- **WHEN** 用户调用 `POST /api/sessions/{id}/clear`
- **THEN** 会话标题、session_id、创建时间 MUST 保持不变

### Requirement: delete 语义（软删除 + 立即物理删除 checkpoint）

系统 MUST 确保 `DELETE /api/sessions/{session_id}` 同时执行：
1. 元数据语义：`SessionRepository.soft_delete()` 标记 `deleted_at`
2. 存储语义：`checkpointer.adelete_thread(session_id)` 立即物理删除 checkpoint 数据

delete 后会话从列表消失，历史不可访问，checkpoint 数据立即清除。

#### Scenario: delete 后会话从列表消失

- **GIVEN** `sessions` 表中存在目标会话
- **WHEN** 用户调用 `DELETE /api/sessions/{id}`
- **THEN** 后续 `GET /api/sessions` MUST NOT 包含该会话

#### Scenario: delete 后历史不可访问

- **GIVEN** 会话已被删除
- **WHEN** 用户尝试访问 `/api/sessions/{id}/history`
- **THEN** 系统 MUST 返回 404 或空数据
