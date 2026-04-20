## Purpose

增量更新手动摘要规范，同步使用自定义中文提示词，保持手动/自动摘要格式一致。

## MODIFIED Requirements

### Requirement: Checkpoint 级手动摘要 API

系统 SHALL 提供 `POST /api/sessions/{session_id}/summarize` 端点，从 checkpoint 读取消息列表，对早期消息生成结构化摘要并写回 checkpoint。

该端点 MUST 使用辅助 LLM（`create_auxiliary_llm()`）和自定义中文摘要提示词生成 8 节结构化摘要。

该端点 MUST 保留最近 10 条消息不被摘要（与自动摘要的 `keep=("messages", 10)` 策略一致）。

摘要消息 MUST 以 `HumanMessage` 格式注入，内容前缀为 `"Here is a summary of the conversation to date:\n\n"`，`additional_kwargs` 包含 `{"lc_source": "summarization"}`。

该端点 MUST 通过 `agent.aupdate_state(config, {"messages": new_messages}, as_node="model")` 将摘要后的消息列表写回 checkpoint。

该端点 MUST 检查 checkpoint 是否存在以及消息数量是否超过 keep 阈值，不满足条件时返回适当的错误响应。

辅助 LLM 不可用时（`create_auxiliary_llm()` 返回 None），端点 MUST 返回 HTTP 503 错误。

#### Scenario: 成功执行手动摘要

- **GIVEN** 该 session 的 checkpoint 中存在超过 10 条消息
- **WHEN** 调用 `POST /api/sessions/{session_id}/summarize`
- **THEN** 系统 MUST 将除最近 10 条外的所有消息替换为一条结构化摘要 HumanMessage，摘要后的消息列表 MUST 通过 `aupdate_state` 写回 checkpoint

#### Scenario: 消息数不足时不执行摘要

- **GIVEN** 该 session 的 checkpoint 中消息数 ≤ 10
- **WHEN** 调用 `POST /api/sessions/{session_id}/summarize`
- **THEN** 系统 MUST 返回 `{summarized: false, reason: "消息数不足，无需摘要"}`，不修改 checkpoint

#### Scenario: checkpoint 不存在

- **GIVEN** 该 session 无 checkpoint 数据
- **WHEN** 调用 `POST /api/sessions/{session_id}/summarize`
- **THEN** 系统 MUST 返回 HTTP 400，错误信息说明该会话无可用消息

#### Scenario: 摘要后 Agent 下次对话看到摘要结果

- **WHEN** 手动摘要成功执行后，用户在同一会话中发送新消息
- **THEN** Agent MUST 从 checkpoint 恢复包含摘要的消息列表，不再包含已被摘要替换的原始早期消息

#### Scenario: 辅助 LLM 不可用

- **WHEN** `create_auxiliary_llm()` 返回 None（无 API key 或配置无效）
- **THEN** 系统 MUST 返回 HTTP 503，提示辅助模型不可用

### Requirement: 摘要 Prompt 与自动摘要一致

手动摘要 MUST 使用 `_load_summary_prompt()` 加载的提示词（与自动摘要 `ContextAwareSummarizationMiddleware` 共享同一加载逻辑），确保手动和自动摘要的输出格式一致。

#### Scenario: 摘要输出格式

- **WHEN** 手动摘要成功执行
- **THEN** 摘要内容 MUST 使用自定义中文提示词定义的结构（会话意图、关键决策、工具调用、文件产物、错误修复、用户消息、当前进展、后续步骤）
