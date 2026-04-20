## Purpose

增量更新中间件链规范，修复 SummarizationMiddleware 的 4 个缺陷：配置项生效、trim 联动窗口、SystemMessage 保护、自定义提示词。

## MODIFIED Requirements

### Requirement: 自动对话摘要中间件（SummarizationMiddleware）

系统 SHALL 使用 `ContextAwareSummarizationMiddleware`（继承自 `SummarizationMiddleware`）作为中间件链第 2 层。

触发阈值 MUST 支持两种配置模式，比例优先：
- `trigger_ratio`（默认 0.6）：上下文窗口的百分比，计算 `trigger_tokens = int(context_window * trigger_ratio)`
- `trigger_tokens`（可选）：绝对值，非 null 时覆盖比例计算结果

Trim 阈值 MUST 支持两种配置模式，比例优先：
- `trim_ratio`（默认 0.30）：上下文窗口的百分比，计算 `trim_tokens = int(context_window * trim_ratio)`
- `trim_tokens`（可选）：绝对值，非 null 时覆盖比例计算结果

`trim_tokens_to_summarize` 参数 MUST 传入计算的 trim 值，不使用 LangChain 默认值 4000。

摘要 MUST 使用自定义中文提示词，通过 `_load_summary_prompt()` 加载，加载优先级：
1. `config.json` 中 `summary_prompt_file` 指定的文件路径
2. `workspace/summary_prompt.md` 默认文件
3. 内置 `DEFAULT_SUMMARY_PROMPT_ZH` 常量兜底

保留最近 N 条消息（默认 10 条），N 值通过 `keep_messages` 配置。

`ContextAwareSummarizationMiddleware` MUST 在摘要前提取所有 SystemMessage，调用父类摘要逻辑处理非 System 消息，摘要完成后将 SystemMessage 重新注入结果。

#### Scenario: 比例模式触发摘要

- **WHEN** `trigger_ratio` 为 0.6 且上下文窗口为 131072 token
- **THEN** 触发阈值 MUST 为 `int(131072 * 0.6) = 78643` token

#### Scenario: 绝对值覆盖触发阈值

- **WHEN** `trigger_tokens` 配置为 8000 且 `trigger_ratio` 为 0.6
- **THEN** 触发阈值 MUST 为 8000 token（绝对值覆盖比例计算）

#### Scenario: Trim 联动上下文窗口

- **WHEN** `trim_ratio` 为 0.30 且上下文窗口为 131072 token
- **THEN** `trim_tokens_to_summarize` MUST 为 `int(131072 * 0.30) = 39321` token

#### Scenario: SystemMessage 保护

- **WHEN** 消息列表中包含 SystemMessage（Zone 3 动态内容）且摘要触发
- **THEN** SystemMessage MUST 不参与 LLM 摘要，摘要完成后 MUST 重新注入到结果消息列表中

#### Scenario: 自定义提示词加载

- **WHEN** `config.json` 指定 `summary_prompt_file` 为 `workspace/custom_prompt.md`
- **THEN** 摘要 MUST 使用该文件的提示词内容

#### Scenario: 无自定义提示词时使用内置中文提示词

- **WHEN** `summary_prompt_file` 为 null 且 `workspace/summary_prompt.md` 不存在
- **THEN** 摘要 MUST 使用 `DEFAULT_SUMMARY_PROMPT_ZH` 内置常量

#### Scenario: Token 未超阈值不触发

- **WHEN** 当前消息列表的 token 总数未超过触发阈值
- **THEN** 消息列表保持不变，不执行摘要操作

### Requirement: 手动摘要与自动摘要衔接

手动摘要（`POST /api/sessions/{id}/summarize`）MUST 复用 `ContextAwareSummarizationMiddleware` 的提示词加载逻辑（`_load_summary_prompt`），确保手动和自动摘要的输出格式一致。

手动摘要的消息替换逻辑（保留最近 10 条、AI/Tool 配对保护）MUST 与 `SummarizationMiddleware` 的策略对齐。

#### Scenario: 手动摘要与自动摘要 Prompt 一致

- **WHEN** 用户通过前端按钮触发手动摘要
- **THEN** 摘要 MUST 使用 `_load_summary_prompt()` 加载的提示词生成，输出格式与自动摘要完全一致

#### Scenario: 摘要生成失败时不破坏消息列表

- **WHEN** 辅助 LLM 调用超时或返回无效摘要
- **THEN** 系统 MUST 保留原始消息列表不变，MUST NOT 用空内容或错误信息替换消息
