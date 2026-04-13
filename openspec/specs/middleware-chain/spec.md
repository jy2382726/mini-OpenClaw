## ADDED Requirements

### Requirement: 工具输出预算截断中间件

系统 SHALL 提供 `ToolOutputBudgetMiddleware`，在每次模型调用前检查所有 `ToolMessage`，对超过预设 token 预算的工具输出进行截断。截断 MUST 保留头尾内容并标注省略字数，不改变消息数量和顺序。

每个工具类型 MUST 有独立的 token 预算配置：terminal(2000)、python_repl(1500)、fetch_url(3000)、read_file(2000)、search_knowledge(1000)。

#### Scenario: 工具输出超过预算时自动截断

- **WHEN** 一条 ToolMessage（工具名为 `terminal`）的 content 超过 8000 字符（约 2000 tokens）
- **THEN** 系统截断该消息 content，保留前 2/3 和后 1/3 内容，中间插入 `[省略约 N 字符]` 标注

#### Scenario: 工具输出未超预算时不处理

- **WHEN** 一条 ToolMessage 的 content 未超过对应工具的 token 预算
- **THEN** 该消息 content 保持不变

#### Scenario: 非工具消息不受影响

- **WHEN** 消息列表中包含 HumanMessage、AIMessage、SystemMessage 等非 ToolMessage 类型
- **THEN** 这些消息的内容和顺序均不受影响

### Requirement: 自动对话摘要中间件

系统 SHALL 使用 LangChain 内置的 `SummarizationMiddleware`，配置为 `trigger=("tokens", 8000)` + `keep=("messages", 10)`，在 token 数超过阈值时自动生成结构化摘要替换旧消息。

摘要 MUST 包含四段结构：SESSION INTENT（会话意图）、SUMMARY（关键上下文和决策）、ARTIFACTS（文件变更记录）、NEXT STEPS（待办任务）。

摘要 MUST 使用轻量 LLM（如 qwen-turbo）生成，temperature 为 0。

#### Scenario: Token 超阈值自动触发摘要

- **WHEN** 当前消息列表的 token 总数超过 8000
- **THEN** 系统自动生成四段结构化摘要，替换超出 `keep` 范围的旧消息

#### Scenario: 最近 10 条消息始终保留

- **WHEN** 摘要触发后
- **THEN** 最新的 10 条消息 MUST 完整保留，不被摘要替换

#### Scenario: AI/Tool 消息配对保护

- **WHEN** 摘要的截断点落在一条 AIMessage（含 tool_calls）和对应的 ToolMessage 之间
- **THEN** 系统 MUST 将截断点移动到安全位置，确保 tool_calls 和 tool_result 的配对完整性

#### Scenario: Token 未超阈值不触发

- **WHEN** 当前消息列表的 token 总数未超过 8000
- **THEN** 消息列表保持不变，不执行摘要操作

### Requirement: 运行时工具过滤中间件

系统 SHALL 提供 `ContextAwareToolFilter`，在每次模型调用时根据最近消息的上下文关键词动态裁剪可用工具集。工具定义 MUST 始终全量加载（保护 KV-cache），过滤仅控制运行时可见范围。

工具分类 MUST 遵循以下 tier 定义：
- always: `read_file`, `search_knowledge`（始终可用）
- coding: `terminal`, `python_repl`, `write_file`（编码上下文时可用）
- web: `fetch_url`（网络请求上下文时可用）
- memory: `save_memory`, `search_memories`（记忆操作时可用）
- admin: `create_skill_version`（管理上下文时可用）

#### Scenario: 纯闲聊上下文只暴露基础工具

- **WHEN** 最近 6 条消息不包含任何编码、网络、记忆相关关键词
- **THEN** 模型调用时只暴露 `always` tier 的工具（`read_file`, `search_knowledge`）

#### Scenario: 编码上下文暴露编码工具

- **WHEN** 最近 6 条消息包含"代码"、"函数"、"文件"、"终端"、"运行"、"python"、"terminal" 等关键词
- **THEN** 模型调用时暴露 `always` + `coding` tier 的工具

#### Scenario: 工具定义不变

- **WHEN** 工具过滤中间件运行时
- **THEN** 所有工具的 JSON Schema 定义 MUST 完整保留，过滤不改变工具定义的序列化形式

### Requirement: 工具调用限流中间件

系统 SHALL 使用 LangChain 内置的 `ToolCallLimitMiddleware`，为每个工具设定单会话调用次数上限。`terminal` 上限为 10 次，`python_repl` 上限为 5 次。

#### Scenario: 工具调用达到上限

- **WHEN** `terminal` 工具在同一会话中已被调用 10 次，Agent 尝试第 11 次调用
- **THEN** 系统 MUST 拒绝该工具调用，并返回提示信息告知 Agent 该工具已达调用上限

#### Scenario: 未达上限正常调用

- **WHEN** `terminal` 工具在同一会话中被调用 5 次
- **THEN** 工具正常执行，不受限制

### Requirement: 中间件链执行顺序

系统 MUST 按以下顺序执行中间件：`ToolOutputBudgetMiddleware` → `SummarizationMiddleware` → `ContextAwareToolFilter` → `ToolCallLimitMiddleware`。

#### Scenario: 中间件按序执行

- **WHEN** 用户消息到达，触发模型调用
- **THEN** 系统依次执行：工具输出截断 → 自动摘要 → 运行时工具过滤 → 工具限流 → 模型调用

#### Scenario: 前置截断先于摘要执行

- **WHEN** 消息列表中包含超过预算的工具输出，且总 token 数超过摘要阈值
- **THEN** 系统先截断工具输出降低 token 总量，再判断是否需要触发摘要
