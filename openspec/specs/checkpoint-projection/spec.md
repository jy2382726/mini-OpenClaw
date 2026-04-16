## Purpose

定义将 LangGraph checkpoint 转换为前端所需格式的投影服务，包括 UI 气泡投影、调试视图投影、feature flag 控制、clear/delete 语义修正等，实现从 JSON 文件存储向 checkpoint 存储的平滑迁移。

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

该服务 MUST 明确标注为"近似调试视图"，而非 Agent 本轮真实输入——因为真实输入还包含动态注入的 Zone 3 内容、TaskState 注入、unified memory 检索结果等，这些不存储在 checkpoint 的 messages 中。

输出 MUST 包含：`system_prompt`（从 `build_system_prompt()` 生成）+ 投影后的消息列表。

#### Scenario: 调试视图包含系统提示

- **WHEN** 前端调用 `GET /api/sessions/{session_id}/messages`
- **THEN** 返回结果 MUST 在消息列表开头包含一条 `{role: "system", content: <system_prompt>}` 消息

#### Scenario: 调试视图标注为近似

- **WHEN** 前端展示 Raw Messages 面板
- **THEN** API 响应 MUST 包含 `is_approximation: true` 字段，明确告知前端这不是 Agent 真实输入

### Requirement: Feature Flag 控制投影切换

系统 MUST 通过 `features.checkpoint_history_read` 配置项控制 `/history` 和 `/messages` 的数据来源。

- 当 `checkpoint_history_read` 为 `false`（默认）时，继续从 `session_manager` 的 JSON 文件读取
- 当 `checkpoint_history_read` 为 `true` 时，从 checkpoint projection 读取

手动摘要完成后，前端 MUST 自动刷新 `/history` 和 `/api/tokens/session/{id}` 以反映摘要后的消息状态。

#### Scenario: Flag 关闭时使用 JSON 数据源

- **WHEN** `checkpoint_history_read` 为 false，前端请求 `/api/sessions/{id}/history`
- **THEN** 系统 MUST 返回 JSON 文件中的消息数据，行为与迁移前完全一致

#### Scenario: Flag 开启时使用 checkpoint 投影

- **WHEN** `checkpoint_history_read` 为 true，前端请求 `/api/sessions/{id}/history`
- **THEN** 系统 MUST 调用 `CheckpointHistoryService.project(thread_id)` 返回投影结果

#### Scenario: 手动摘要后 history 自动刷新

- **WHEN** 前端手动摘要成功完成后
- **THEN** 前端 MUST 自动调用 `/api/sessions/{id}/history` 和 `/api/tokens/session/{id}` 刷新聊天记录和 token 统计，显示摘要后的消息

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

- **WHEN** 前端请求 `GET /api/sessions/{id}/task-state`，且该会话有活跃 TaskState
- **THEN** 系统 MUST 返回完整的 TaskState 对象

#### Scenario: 无 TaskState 时返回 null

- **WHEN** 前端请求 `GET /api/sessions/{id}/task-state`，且该会话无活跃 TaskState
- **THEN** 系统 MUST 返回 `{"task_state": null}`，HTTP 状态码 200

#### Scenario: checkpoint 不存在时返回 null

- **WHEN** 前端请求 `GET /api/sessions/{id}/task-state`，但该会话无 checkpoint 数据
- **THEN** 系统 MUST 返回 `{"task_state": null}`，HTTP 状态码 200

### Requirement: Feature Flag 控制 Agent 输入来源

系统 MUST 通过 `features.checkpoint_agent_input` 配置项控制 Agent 的历史消息输入来源。

- 当 `checkpoint_agent_input` 为 `false`（默认）时，Agent 历史从 `session_manager.load_session_for_agent()` 读取
- 当 `checkpoint_agent_input` 为 `true` 时，Agent 不接收应用层传入的历史，依赖 checkpoint 的线程恢复能力

此 feature flag 的前提是 Phase 0 已验证 checkpoint 消息恢复语义可靠。

#### Scenario: Flag 关闭时传入 JSON 历史

- **WHEN** `checkpoint_agent_input` 为 false，用户发送消息
- **THEN** 系统 MUST 调用 `session_manager.load_session_for_agent()` 加载历史，传入 `agent_manager.astream()`

#### Scenario: Flag 开启时依赖 checkpoint 恢复

- **WHEN** `checkpoint_agent_input` 为 true，用户发送消息
- **THEN** 系统 MUST 仅传入当前用户消息（不传历史），依赖 LangGraph 通过 `thread_id` 从 checkpoint 恢复先前 messages

#### Scenario: 恢复后消息不重复注入

- **WHEN** `checkpoint_agent_input` 为 true，Agent 从 checkpoint 恢复了先前 messages
- **THEN** 系统 MUST 确保恢复的消息与应用层传入的内容不产生重复

### Requirement: Feature Flag 控制 JSON 消息写入

系统 MUST 通过 `features.session_json_write_enabled` 配置项控制是否在 SSE 完成后继续调用 `session_manager.save_message()`。

- 当 `session_json_write_enabled` 为 `true`（默认）时，继续写入 JSON
- 当 `session_json_write_enabled` 为 `false` 时，停止 JSON 写入

此 feature flag 的前提是 `/history` 和 `/messages` 已稳定由 checkpoint projection 提供。

#### Scenario: Flag 开启时继续双写

- **WHEN** `session_json_write_enabled` 为 true，SSE 流正常完成
- **THEN** 系统 MUST 调用 `session_manager.save_message()` 将用户消息和助手回复写入 JSON

#### Scenario: Flag 关闭时停止写入

- **WHEN** `session_json_write_enabled` 为 false，SSE 流正常完成
- **THEN** 系统 MUST NOT 调用 `session_manager.save_message()`，不产生新的 JSON 文件写入

### Requirement: clear 语义修正

系统 MUST 确保 `POST /api/sessions/{session_id}/clear` 在迁移后正确清理 checkpoint 侧数据。

当前实现只清理 JSON 侧消息。迁移后，clear 的产品语义 MUST 满足：
1. 用户视角下，对话历史被清空
2. 后续对话不受旧 TaskState、旧摘要、旧 thread message 影响
3. 会话标题和元数据可保留

实现策略根据 Phase 0 验证结果决定：
- 如果 `AsyncSqliteSaver` 支持 thread 级清理：直接删除该 thread 的 checkpoint 数据
- 如果不支持：使用"新 thread_id + 旧 thread 屏蔽"策略，元数据层维护 session_id 到当前 thread_id 的映射

#### Scenario: clear 后前端历史为空

- **WHEN** 用户调用 `POST /api/sessions/{id}/clear`
- **THEN** 后续 `GET /api/sessions/{id}/history` MUST 返回空消息列表

#### Scenario: clear 后后续对话不受旧状态影响

- **WHEN** 用户 clear 后在同一会话中发送新消息
- **THEN** 新对话 MUST NOT 受到旧 TaskState、旧摘要、旧 thread message 的影响

#### Scenario: clear 保留元数据

- **WHEN** 用户调用 `POST /api/sessions/{id}/clear`
- **THEN** 会话标题、session_id、创建时间 MUST 保持不变

### Requirement: delete 语义修正

系统 MUST 确保 `DELETE /api/sessions/{session_id}` 在迁移后正确处理 checkpoint 侧数据。

delete 的分层语义：
1. 产品语义：会话从列表消失，历史不可访问
2. 元数据语义：`SessionRepository.soft_delete()` 标记 `deleted_at`
3. 存储语义：checkpoint 物理删除可延后为后台 GC

#### Scenario: delete 后会话从列表消失

- **WHEN** 用户调用 `DELETE /api/sessions/{id}`
- **THEN** 后续 `GET /api/sessions` MUST NOT 包含该会话

#### Scenario: delete 后历史不可访问

- **WHEN** 用户删除会话后尝试访问 `/api/sessions/{id}/history`
- **THEN** 系统 MUST 返回 404 或空数据

#### Scenario: checkpoint 物理删除可延后

- **WHEN** 会话被软删除
- **THEN** checkpoint 数据的物理删除 MAY 延后执行（如后台 GC），不阻塞 delete 操作的响应

### Requirement: 流式中断的部分内容保存策略

系统 MUST 定义在停止 JSON 消息双写后，SSE 流式中断（GeneratorExit、Exception、CancelledError）时的部分内容保存策略。

当前行为：JSON 模式下，`chat.py` 的 `finally` 块会将部分回答写入 JSON 文件。

迁移后必须从以下策略中选择一种，并在实施时明确：

- **策略 A：依赖 checkpoint 快照**：如果 `agent.astream` 在中断前已产生 checkpoint，部分内容通过 checkpoint 恢复。风险是中断时可能不产生有效 checkpoint
- **策略 B：独立暂存机制**：在 SSE 过程中实时将部分内容暂存到独立存储（如 `SessionRepository` 的临时字段），中断时从暂存恢复
- **策略 C：放弃部分内容保存**：中断后用户看到空消息，需要重新发送。这是最简单的方案，但会丢失当前的部分回答能力

Phase 0 需要验证中断时的 checkpoint 状态，以决定采用哪种策略。

#### Scenario: 中断时 checkpoint 无有效快照

- **WHEN** SSE 流在 Agent 执行过程中被客户端断开，且 `agent.astream` 未完成当前轮次的 checkpoint 写入
- **THEN** 系统 MUST NOT 产生不完整的 checkpoint，避免后续恢复时出现截断状态

#### Scenario: 中断后用户重新进入会话

- **WHEN** 用户在流式中断后重新打开会话
- **THEN** 系统 MUST 仅展示上一次完整 checkpoint 中的消息，不包含中断轮次的部分内容（除非选择了策略 B）

### Requirement: Token 统计迁移到 checkpoint 投影

系统 MUST 将 `GET /api/tokens/session/{session_id}` 的消息来源从 `session_manager.load_session()` 切换到 checkpoint 投影，确保 token 统计基于用户实际看到的历史。

#### Scenario: token 统计基于 checkpoint 数据

- **WHEN** `checkpoint_history_read` 为 true，前端请求 `GET /api/tokens/session/{id}`
- **THEN** 系统 MUST 从 checkpoint projection 获取消息列表进行 token 计算
