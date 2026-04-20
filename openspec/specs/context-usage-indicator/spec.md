## Purpose

定义上下文使用率实时指示器功能，在压缩按钮上显示使用率百分比，超过 80% 时禁用发送按钮强制用户先执行压缩。

## Requirements

### Requirement: Token API 返回上下文使用率

`GET /tokens/session/{session_id}` 端点 SHALL 在现有响应中新增两个字段：
- `context_window`（int）：当前模型上下文窗口大小（token 数），从 `get_context_window()` 读取
- `usage_ratio`（float）：上下文使用率，计算公式 `total_tokens / context_window`，保留两位小数

注意：`total_tokens` 仅计消息 `content` 字段（不含 tool_calls 元数据、role 标签等），为下界估算。前端阈值设计已考虑此偏差。

#### Scenario: 正常返回使用率

- **WHEN** 调用 `GET /tokens/session/abc123`
- **THEN** 响应 MUST 包含 `{ system_tokens, message_tokens, total_tokens, context_window, usage_ratio }`

#### Scenario: 空会话返回零使用率

- **WHEN** 调用 `GET /tokens/session/{id}` 且会话无消息
- **THEN** `usage_ratio` MUST 为 0.0，`context_window` 为配置的窗口大小

### Requirement: 前端上下文使用率状态管理

前端 store SHALL 维护 `contextUsage` 状态，包含 `ratio`（float）、`totalTokens`（int）、`contextWindow`（int）。

store SHALL 通过 `useEffect` 监听 `[sessionId, messages.length, isStreaming]` 触发 token API 调用。MUST 在 `isStreaming` 为 `true` 时跳过调用，避免 SSE 流式传输期间逐 token 触发 API 请求。流式传输结束后（`isStreaming` 变为 `false`）且 `messages.length > 0` 时触发更新。

空消息列表时（`messages.length === 0`），`contextUsage` MUST 设为 `null`。

API 调用失败时，`contextUsage` 状态 MUST 降级为 `null`，不影响其他功能。

#### Scenario: 流式传输结束后更新使用率

- **WHEN** 用户发送消息且 AI 回复完成（`isStreaming` 从 `true` 变为 `false`）
- **THEN** store MUST 自动调用 token API 更新 `contextUsage`

#### Scenario: 流式传输期间不调用 API

- **WHEN** SSE 流式传输正在进行（`isStreaming` 为 `true`）
- **THEN** store MUST NOT 调用 token API，避免逐 token 触发请求

#### Scenario: 会话切换后更新使用率

- **WHEN** 用户切换到已有消息的会话
- **THEN** store MUST 调用 token API 获取该会话的使用率

#### Scenario: 空会话不获取使用率

- **WHEN** 会话无消息（`messages.length === 0`）
- **THEN** `contextUsage` MUST 为 `null`，不调用 token API

#### Scenario: API 失败降级

- **WHEN** token API 调用失败
- **THEN** `contextUsage` MUST 为 `null`，压缩按钮和发送按钮行为与当前一致（不显示百分比，不强制禁用）

### Requirement: 压缩按钮显示上下文使用率

ChatInput 中的压缩按钮 SHALL 根据上下文使用率显示不同状态：
- `contextUsage` 为 `null` 或 `ratio < 0.6`：显示 "压缩"
- `0.6 ≤ ratio < 0.8`：显示 "压缩 (N%)"，文字变为橙色
- `ratio ≥ 0.8`：显示 "压缩 (N%)"，文字变为红色

#### Scenario: 低使用率不显示百分比

- **WHEN** `contextUsage.ratio < 0.6`
- **THEN** 压缩按钮 MUST 显示 "压缩"，使用默认灰色样式

#### Scenario: 中等使用率显示橙色百分比

- **WHEN** `0.6 ≤ contextUsage.ratio < 0.8`
- **THEN** 压缩按钮 MUST 显示 "压缩 (65%)" 样式，文字为橙色

#### Scenario: 高使用率显示红色百分比

- **WHEN** `contextUsage.ratio ≥ 0.8`
- **THEN** 压缩按钮 MUST 显示 "压缩 (85%)" 样式，文字为红色

### Requirement: 发送按钮超限禁用

ChatInput 中的发送按钮 SHALL 在上下文使用率超过 80% 时禁用，强制用户先执行手动压缩。

注意：当前 token 计数为下界估算（仅计 content，不含元数据），精确计算将作为后续独立提案优化。此阈值与自动摘要的 60% 触发阈值互补：自动摘要在 model call 时触发，发送禁用作为安全网防止摘要失败或单条消息极大跳升的场景。

禁用条件：`contextUsage` 非 `null` 且 `contextUsage.ratio > 0.8` 且非正在压缩。

禁用时 MUST 显示提示信息："上下文空间不足，请先压缩对话"。

用户执行压缩后，使用率下降到 80% 以下，发送按钮自动恢复可用。

#### Scenario: 超限禁用发送

- **WHEN** `contextUsage.ratio > 0.8` 且用户输入了文本
- **THEN** 发送按钮 MUST 为禁用状态，输入框下方 MUST 显示提示信息

#### Scenario: 压缩后恢复发送

- **WHEN** 用户执行手动压缩后 `contextUsage.ratio` 下降到 0.8 以下
- **THEN** 发送按钮 MUST 自动恢复可用

#### Scenario: API 失败不强制禁用

- **WHEN** `contextUsage` 为 `null`（API 调用失败）
- **THEN** 发送按钮 MUST 保持原有禁用逻辑（`!text.trim() || isCompressing`），不额外禁用
