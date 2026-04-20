## MODIFIED Requirements

### Requirement: 注入位置优化

系统 SHALL 将记忆上下文注入到模型可见的提示中。默认注入方式为 `system_prompt` 模式：通过 `MemoryMiddleware.awrap_model_call` 使用 `request.override(system_message=...)` 将记忆追加到系统提示末尾。

系统 MUST 支持通过 `config.json` 的 `memory_middleware.injection_mode` 切换为 `"system_message"` 回退模式：记忆作为 SystemMessage 插入 messages 列表（当前行为）。

`system_prompt` 模式下，记忆 MUST 追加到 Zone 2 稳定前缀之后，格式为 `\n\n<!-- Zone 3: Dynamic -->\n{memory_context}`。

`system_message` 模式下，SystemMessage MUST 插入在当前用户消息之前。

#### Scenario: system_prompt 模式注入位置

- **WHEN** `injection_mode` 为 `"system_prompt"` 且检索到记忆
- **THEN** 记忆 MUST 追加到 system_message 末尾，位于 Zone 2 稳定前缀之后

#### Scenario: system_message 模式注入位置

- **WHEN** `injection_mode` 为 `"system_message"` 且检索到记忆
- **THEN** 记忆 MUST 作为 SystemMessage 插入 messages 列表，位于当前用户消息之前

#### Scenario: 两种模式输出格式一致

- **WHEN** 使用任意注入模式
- **THEN** 模型接收到的完整提示内容 MUST 包含 Zone 1 + Zone 2 + 记忆上下文 + 用户消息
