## ADDED Requirements

### Requirement: Checkpoint 级手动摘要 API

系统 SHALL 提供 `POST /api/sessions/{session_id}/summarize` 端点，从 checkpoint 读取消息列表，对早期消息生成结构化摘要并写回 checkpoint。

该端点 MUST 使用辅助 LLM（`create_auxiliary_llm()`）和 `SummarizationMiddleware` 的 `DEFAULT_SUMMARY_PROMPT` 生成四段式摘要（SESSION INTENT / SUMMARY / ARTIFACTS / NEXT STEPS）。

该端点 MUST 保留最近 10 条消息不被摘要（与自动摘要的 `keep=("messages", 10)` 策略一致）。

摘要消息 MUST 以 `HumanMessage` 格式注入，内容前缀为 `"Here is a summary of the conversation to date:\n\n"`，`additional_kwargs` 包含 `{"lc_source": "summarization"}`。

该端点 MUST 通过 `agent.aupdate_state(config, {"messages": new_messages}, as_node="model")` 将摘要后的消息列表写回 checkpoint。

该端点 MUST 检查 checkpoint 是否存在以及消息数量是否超过 keep 阈值，不满足条件时返回适当的错误响应。

#### Scenario: 成功执行手动摘要

- **WHEN** 调用 `POST /api/sessions/{session_id}/summarize`，且该 session 的 checkpoint 中存在超过 10 条消息
- **THEN** 系统 MUST 将除最近 10 条外的所有消息替换为一条结构化摘要 HumanMessage，摘要后的消息列表 MUST 通过 `aupdate_state` 写回 checkpoint

#### Scenario: 消息数不足时不执行摘要

- **WHEN** 调用 `POST /api/sessions/{session_id}/summarize`，且该 session 的 checkpoint 中消息数 ≤ 10
- **THEN** 系统 MUST 返回 `{summarized: false, reason: "消息数不足，无需摘要"}`，不修改 checkpoint

#### Scenario: checkpoint 不存在

- **WHEN** 调用 `POST /api/sessions/{session_id}/summarize`，且该 session 无 checkpoint 数据
- **THEN** 系统 MUST 返回 HTTP 400，错误信息说明该会话无可用消息

#### Scenario: 摘要后 Agent 下次对话看到摘要结果

- **WHEN** 手动摘要成功执行后，用户在同一会话中发送新消息
- **THEN** Agent MUST 从 checkpoint 恢复包含摘要的消息列表，不再包含已被摘要替换的原始早期消息

### Requirement: AI/Tool 消息配对保护

手动摘要的切割点 MUST 保证 AIMessage（含 tool_calls）与其对应的 ToolMessage 不被分离。如果切割点落在 ToolMessage 上，MUST 向前移动到包含对应 tool_calls 的 AIMessage 之前，或将 ToolMessage 一起纳入摘要范围。

#### Scenario: 切割点落在 ToolMessage 上

- **WHEN** 最近 10 条消息的边界恰好落在一条 ToolMessage 上
- **THEN** 系统 MUST 调整切割点，确保该 ToolMessage 及其对应的 AIMessage（含 tool_calls）要么一起被保留，要么一起被摘要

### Requirement: 摘要 Prompt 与自动摘要一致

手动摘要 MUST 使用 `langchain.agents.middleware.summarization.DEFAULT_SUMMARY_PROMPT` 作为摘要 Prompt，确保手动和自动摘要的输出格式一致（SESSION INTENT / SUMMARY / ARTIFACTS / NEXT STEPS 四段结构）。

#### Scenario: 摘要输出格式

- **WHEN** 手动摘要成功执行
- **THEN** 摘要内容 MUST 包含 SESSION INTENT、SUMMARY、ARTIFACTS、NEXT STEPS 四个结构化段落

### Requirement: 并发安全锁

系统 MUST 防止同一会话的并发摘要请求。当已有摘要请求正在执行时，后续请求 MUST 被拒绝。

#### Scenario: 并发摘要请求被拒绝

- **WHEN** 用户快速连续两次点击压缩按钮，第二次请求到达时第一次仍在执行
- **THEN** 第二次请求 MUST 返回 HTTP 409（Conflict），提示摘要正在进行中

### Requirement: AgentManager 摘要方法封装

`AgentManager` SHALL 提供公开的 `async summarize_checkpoint(session_id, keep_count=10)` 方法，封装从 checkpoint 读取消息、生成摘要、写回 checkpoint 的完整流程。API 层 MUST 委托给此方法执行，不直接操作 checkpointer。

#### Scenario: API 层委托摘要逻辑

- **WHEN** `POST /api/sessions/{session_id}/summarize` 被调用
- **THEN** 端点处理函数 MUST 调用 `agent_manager.summarize_checkpoint(session_id)` 执行摘要，不直接访问 checkpointer 或辅助 LLM
