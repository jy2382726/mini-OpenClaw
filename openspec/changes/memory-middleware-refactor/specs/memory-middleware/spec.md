## Purpose

定义 `MemoryMiddleware` 中间件，封装记忆检索、注入、写入全生命周期管理。

## ADDED Requirements

### Requirement: 记忆检索（abefore_agent）

系统 SHALL 在 `MemoryMiddleware.abefore_agent` 中执行记忆检索。该 hook 在每轮对话开始前调用一次。

检索条件：
- `config.json` 的 `middleware.memory_middleware.enabled` 为 `true`（默认）
- `features.unified_memory` 为 `true`（默认）
- messages 中存在最新用户消息

检索流程 MUST 使用 `UnifiedMemoryRetriever.retrieve_async()` 异步检索。

检索结果 MUST 写入 `state["memory_context"]`（通过 `MemoryMiddlewareState` 定义），供后续 `awrap_model_call` 读取。

检索结果非空时，MUST 通过 `runtime.stream_writer()` 发送 `{"type": "retrieval", "query": ..., "results": ...}` 自定义事件。

`UnifiedMemoryRetriever` MUST 延迟初始化（首次调用时创建），避免构造时加载重依赖。

#### Scenario: 正常检索

- **WHEN** 用户发送消息且 `unified_memory` 功能开启
- **THEN** 系统 MUST 调用 `retrieve_async` 检索相关记忆，将结果写入 `state["memory_context"]`，并通过 stream_writer 发送 retrieval 事件

#### Scenario: 功能关闭时跳过

- **WHEN** `memory_middleware.enabled` 为 `false` 或 `features.unified_memory` 为 `false`
- **THEN** 系统 MUST 返回 None，不执行任何检索

#### Scenario: 无用户消息时跳过

- **WHEN** messages 中无 `HumanMessage`
- **THEN** 系统 MUST 返回 None，不执行检索

#### Scenario: HITL resume 不重复检索

- **WHEN** HITL interrupt 后通过 `resume_stream()` 恢复执行
- **THEN** `abefore_agent` MUST NOT 执行（entry node 已完成），`memory_context` 从 checkpoint 恢复

### Requirement: 记忆注入（awrap_model_call）

系统 SHALL 在 `MemoryMiddleware.awrap_model_call` 中将记忆上下文注入系统提示。

系统 MUST 支持两种注入模式，通过 `config.json` 的 `memory_middleware.injection_mode` 配置：
- `"system_prompt"`（默认）：从 `request.state["memory_context"]` 读取，通过 `request.override(system_message=...)` 追加到系统提示
- `"system_message"`：在 `abefore_agent` 中直接插入 `SystemMessage` 到 messages 列表（与当前行为一致）

`system_prompt` 模式下，记忆上下文 MUST 追加到系统提示末尾，格式为 `\n\n<!-- Zone 3: Dynamic -->\n{memory_context}`。

`system_message` 模式下，`SystemMessage` MUST 插入在最后一条用户消息之前。

`memory_context` 为空时，`awrap_model_call` MUST 直接调用 `handler(request)` 不做修改。

#### Scenario: system_prompt 模式注入

- **WHEN** `injection_mode` 为 `"system_prompt"` 且 `memory_context` 非空
- **THEN** 系统 MUST 将记忆上下文追加到 `system_message.content` 末尾

#### Scenario: system_message 模式注入

- **WHEN** `injection_mode` 为 `"system_message"` 且检索结果非空
- **THEN** 系统 MUST 在 `abefore_agent` 中插入 SystemMessage 到 messages 列表（最后一条用户消息之前）

#### Scenario: 无记忆上下文时不注入

- **WHEN** `memory_context` 为空字符串
- **THEN** 系统 MUST NOT 修改系统提示或消息列表

### Requirement: 记忆写入（aafter_agent）

系统 SHALL 在 `MemoryMiddleware.aafter_agent` 中将对话写入记忆缓冲区。

写入条件：
- `mem0` 配置中 `enabled` 和 `auto_extract` 均为 `true`
- `write_executor` 不为 None（初始化完整）
- messages 中存在完整的最后一轮用户-助手对话

写入 MUST 通过后台线程执行，MUST NOT 阻塞 Agent 响应。后台线程 MUST 执行：
1. `MemoryBuffer.add_turn()` 追加对话
2. 检查立即触发条件（`check_immediate_trigger`）或轮次触发条件（`should_flush`）
3. 触发时执行 `buffer.flush()` + `Mem0Manager.batch_add()`

后台线程异常 MUST 仅记录日志，MUST NOT 向上抛出。

#### Scenario: 正常写入

- **WHEN** 对话结束且 mem0 启用
- **THEN** 系统 MUST 在后台线程中将对话写入 MemoryBuffer，满足条件时触发 mem0 提取

#### Scenario: mem0 关闭时跳过

- **WHEN** `mem0.enabled` 或 `mem0.auto_extract` 为 `false`
- **THEN** 系统 MUST NOT 执行任何写入操作

#### Scenario: 后台写入失败不影响响应

- **WHEN** 后台线程中发生异常
- **THEN** 系统 MUST 仅记录日志警告，MUST NOT 影响已返回给用户的响应

### Requirement: Graph State 持久化

系统 SHALL 通过 `MemoryMiddlewareState` TypedDict 在 graph state 中定义 `memory_context: NotRequired[str]` 字段。

`memory_context` MUST 通过 `abefore_agent` 写入 state，通过 `awrap_model_call` 从 `request.state` 读取。

`memory_context` MUST 随 checkpoint 自动持久化，HITL resume 时从 checkpoint 恢复。

#### Scenario: 记忆上下文持久化

- **WHEN** `abefore_agent` 检索到记忆并写入 `state["memory_context"]`
- **THEN** `memory_context` MUST 随 checkpoint 持久化

#### Scenario: 旧 checkpoint 兼容

- **WHEN** 从无 `memory_context` 字段的旧 checkpoint 恢复
- **THEN** `state.get("memory_context", "")` MUST 返回空字符串，不影响正常运行

### Requirement: SSE 自定义事件透传

系统 SHALL 在 `_stream_events` 方法中支持 `stream_mode=["messages", "updates", "custom"]`。

`mode == "custom"` 时 MUST 直接 `yield data` 透传自定义事件，不修改事件格式。

#### Scenario: custom 事件透传

- **WHEN** `_stream_events` 收到 `mode == "custom"` 的事件
- **THEN** 系统 MUST 直接 yield 该事件数据给调用方

#### Scenario: retrieval 事件格式不变

- **WHEN** MemoryMiddleware 通过 stream_writer 发送 retrieval 事件
- **THEN** 前端收到的 SSE 事件格式 MUST 与当前一致（`{"type": "retrieval", "query": ..., "results": ...}`）
