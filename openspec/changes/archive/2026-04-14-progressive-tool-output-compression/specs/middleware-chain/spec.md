## MODIFIED Requirements

### Requirement: 工具输出预算截断中间件

系统 SHALL 提供 `ToolOutputBudgetMiddleware`，基于模型上下文窗口的使用比例，在每次模型调用前渐进式压缩早期工具输出。压缩 MUST 仅针对「早期轮次」的超预算 ToolMessage，「当前轮次」的工具输出 MUST 保持完整。

触发条件 MUST 基于上下文窗口比例而非固定 token 数：
- 低于 `safe_ratio`（默认 0.25）时不压缩
- 在 `safe_ratio` 和 `pressure_ratio`（默认 0.45）之间时使用标准截断（头 2/3 + 尾 1/3）
- 超过 `pressure_ratio` 时使用短截断（头 1/2 + 尾 1/4）

每个工具类型 MUST 有独立的 token 预算配置：terminal(2000)、python_repl(1500)、fetch_url(3000)、read_file(2000)、search_knowledge(1000)。

单条输出超过上下文窗口 5% 时 MUST 自动归档到文件，ToolMessage 中保留截断摘要和文件路径引用。

#### Scenario: 工具输出超过预算且上下文紧张时截断早期输出

- **WHEN** 一条早期 ToolMessage（工具名为 `terminal`）的 content 超过 8000 字符（约 2000 tokens），上下文使用比例为 30%
- **THEN** 系统截断该消息 content，保留前 2/3 和后 1/3 内容，中间插入 `[省略约 N 字符]` 标注

#### Scenario: 当前轮次工具输出不受截断影响

- **WHEN** 最近 3 组工具输出中有一条 terminal 输出超过 8000 字符，上下文使用比例为 30%
- **THEN** 该消息 MUST 保持完整，不被截断

#### Scenario: 上下文宽裕时不处理任何工具输出

- **WHEN** 上下文使用比例为 15%（低于 safe_ratio）
- **THEN** 所有 ToolMessage 的 content MUST 保持不变，无论是否超预算

#### Scenario: 非工具消息不受影响

- **WHEN** 消息列表中包含 HumanMessage、AIMessage、SystemMessage 等非 ToolMessage 类型
- **THEN** 这些消息的内容和顺序均不受影响

#### Scenario: 超大输出自动归档

- **WHEN** 一条工具输出超过上下文窗口的 5%
- **THEN** 完整输出 MUST 归档到 `sessions/archive/` 目录，ToolMessage 中仅保留截断摘要和文件路径

### Requirement: 自动对话摘要中间件

系统 SHALL 使用 LangChain 内置的 `SummarizationMiddleware`，trigger_tokens MUST 基于模型上下文窗口比例计算（默认 60%），keep 配置为 `("messages", 10)`。摘要 MUST 使用辅助模型（通过 `create_auxiliary_llm()` 获取）生成。

#### Scenario: 128K 模型的摘要触发

- **WHEN** 当前消息列表的 token 总数超过 131072 × 0.6 = 78643
- **THEN** 系统自动使用辅助模型生成四段结构化摘要，替换超出 `keep` 范围的旧消息

#### Scenario: 最近 10 条消息始终保留

- **WHEN** 摘要触发后
- **THEN** 最新的 10 条消息 MUST 完整保留，不被摘要替换

#### Scenario: 辅助模型不可用时跳过摘要

- **WHEN** `create_auxiliary_llm()` 返回 `None`（无 API key）
- **THEN** 系统跳过 SummarizationMiddleware 的创建，中间件链中不包含摘要层

### Requirement: 中间件链执行顺序

系统 MUST 按以下顺序执行中间件：`ToolOutputBudgetMiddleware` → `SummarizationMiddleware` → `ContextAwareToolFilter` → `ToolCallLimitMiddleware`。

#### Scenario: 中间件按序执行

- **WHEN** 用户消息到达，触发模型调用
- **THEN** 系统依次执行：渐进式工具输出压缩 → 自动摘要 → 运行时工具过滤 → 工具限流 → 模型调用

#### Scenario: 第 1 层压缩延缓第 2 层摘要触发

- **WHEN** 消息列表中包含超过预算的早期工具输出，且总 token 数接近摘要阈值
- **THEN** 系统先压缩早期工具输出降低 token 总量，可能避免触发摘要
