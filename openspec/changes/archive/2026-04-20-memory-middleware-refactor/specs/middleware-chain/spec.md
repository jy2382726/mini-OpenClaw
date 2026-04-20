## MODIFIED Requirements

### Requirement: 四层中间件链架构

系统 SHALL 在 `AgentManager._build_middleware()` 中构建中间件链，按以下顺序执行：

1. **ToolOutputBudgetMiddleware** — 工具输出渐进式截断/归档
2. **SummarizationMiddleware** — 自动对话摘要（token 超阈值时触发）
3. **ContextAwareToolFilter** — 运行时工具过滤（基于技能上下文动态调整可用工具）
4. **ToolCallLimitMiddleware** — 工具调用限流（防止 Agent 无限循环调用工具）
5. **MemoryMiddleware** — 长期记忆检索/注入/写入
6. **FilesystemFileSearchMiddleware** — 文件搜索工具注册

每层通过 `config.json` 的 `middleware` 配置段独立开关。

#### Scenario: 中间件按序执行

- **WHEN** Agent 执行一次模型调用
- **THEN** 中间件按截断 → 摘要 → 工具过滤 → 限流 → 记忆 → 文件搜索 的顺序依次执行

#### Scenario: MemoryMiddleware 可独立关闭

- **WHEN** `config.json` 中 `middleware.memory_middleware.enabled` 设为 false
- **THEN** MemoryMiddleware 不被加载，其他层正常工作

#### Scenario: 单层中间件可独立关闭

- **WHEN** `config.json` 中 `middleware.summarization.enabled` 设为 false
- **THEN** SummarizationMiddleware 不被加载，其他层正常工作
