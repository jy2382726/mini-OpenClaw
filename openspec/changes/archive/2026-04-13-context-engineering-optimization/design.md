## Context

mini-OpenClaw 是轻量级 AI Agent 对话系统，当前使用 `create_agent()` + `astream` 的最基础用法。系统提示通过 `prompt_builder.py` 6 层拼接，对话压缩通过前端手动触发 `api/compress.py`，工具全量加载，任务状态完全缺失。

项目已安装 `langchain==1.2.12` + `langgraph==1.1.2`，但 `SummarizationMiddleware`、`trim_messages`、`wrap_model_call`、`ToolCallLimitMiddleware` 等内置能力完全未使用。源码 `langchain/agents/factory.py:335-340` 的 `_resolve_schema` 函数会合并 `middleware` 和 `state_schema`，两者可以同时使用。

当前核心瓶颈：
- 系统提示每次全量重建，无缓存前缀设计
- 手动压缩质量低（500 字摘要 + 50% 切割），工具输出无截断
- 工具定义全量加载（7-10 个），无法按需裁剪
- 技能元数据仅 `name` + `description`，无法支持触发条件、调用权限等
- 记忆注入位置错误（RAG 结果作为 assistant 消息）
- 任务状态完全缺失，长任务容易偏离目标

## Goals / Non-Goals

**Goals:**

- 将对话压缩从手动触发升级为 LangChain 中间件自动管理
- 建立三段式系统提示缓存前缀，提升 KV-cache 命中率
- 实现技能注册表，遵循 Agent Skills 标准，支持分级注入
- 统一三大记忆源为单一检索接口，优化注入位置
- 通过 state_schema 嵌入任务状态，支持多步任务追踪
- 运行时动态过滤工具，减少无关工具的 token 开销

**Non-Goals:**

- 不重构为自定义 StateGraph（方案 B），保持 `create_agent()` 架构
- 不实现子 Agent 上下文隔离（P3 优先级，后续迭代）
- 不实现上下文健康度监控和防模式化检测（P3，后续迭代）
- 不改变 SSE 事件流协议和前端消息渲染逻辑
- 不改变 workspace Markdown 文件（SOUL.md 等）的内容

## Decisions

### 决策 1：选择方案 A（create_agent + middleware + state_schema）而非方案 B（自定义 StateGraph）

**选择**：在当前 `create_agent()` 架构上添加 `middleware` 参数和 `state_schema` 参数。

**替代方案 B**：自定义 LangGraph StateGraph，完全重写 Agent 构建逻辑。

**理由**：
- 当前需求（压缩、限流、工具过滤、任务状态）均可通过方案 A 实现
- 方案 A 改造成本低，`middleware` 和 `state_schema` 通过 `_resolve_schema` 自动合并
- 方案 B 需要重写 `agent.py`、自行实现压缩和限流，成本高且收益仅在需要条件分支/人工介入时体现
- 渐进式升级：先通过方案 A 落地所有 P0/P1 优化，未来如需复杂流程控制再考虑方案 B

### 决策 2：使用 SummarizationMiddleware 替代手动压缩

**选择**：LangChain 内置 `SummarizationMiddleware`，配置 `trigger=("tokens", 8000)` + `keep=("messages", 10)`。

**替代方案**：优化当前 `api/compress.py` 的摘要 prompt 和切割策略。

**理由**：
- SummarizationMiddleware 内置四段结构化摘要（SESSION INTENT / SUMMARY / ARTIFACTS / NEXT STEPS），覆盖了自定义摘要的所有需求
- 内置 AI/Tool 消息配对保护（`_find_safe_cutoff_point`），避免截断点落在 ToolMessage 中间
- 自动基于 token 计数触发，替代前端手动点击
- 配置即用，无需编写和维护自定义摘要逻辑

### 决策 3：工具输出预算制 + 前置截断

**选择**：自定义 `ToolOutputBudgetMiddleware`，在 SummarizationMiddleware 之前运行，按工具类型设定 token 预算并截断输出。

**替代方案**：在 SummarizationMiddleware 的摘要 prompt 中指示压缩工具输出。

**理由**：
- 摘要压缩是"事后"处理，单个工具输出可能达到数千 token，在摘要触发前已占用大量上下文窗口
- 前置截断是"事前"预防，降低 token 总量，延缓摘要触发时机
- 两者配合使用：前置截断处理日常场景，摘要处理长期积累

### 决策 4：技能元数据遵循 Agent Skills 标准

**选择**：使用 Agent Skills 标准（agentskills.io）的 `metadata` 字段存放扩展属性（触发条件、调用权限、资源约束等）。

**替代方案**：在 SKILL.md frontmatter 顶层添加自定义字段。

**理由**：
- Agent Skills 标准规定 `metadata` 是自由格式的 string→string 映射，足以存放所有扩展属性
- 保持与外部技能生态兼容——外部技能只需提供 `name` + `description` 即可被系统识别
- 不在顶层添加自定义字段，避免与标准未来版本冲突
- **启发式推断**：metadata 全部可选。当技能仅提供 name + description 时，从 description 内容自动推断 `invocation_auto`（中文触发句式 → auto，"Use when asked to" → manual，默认 auto）和 `trigger_patterns`（从引号内容提取关键词）。不要求任何现有技能补充 metadata，也不修改已有 SKILL.md 文件

### 决策 5：记忆注入位置从 assistant 消息改为 system 消息

**选择**：将 RAG 检索结果作为 system 消息注入在当前用户消息之前。

**替代方案**：保持当前 assistant 消息注入方式。

**理由**：
- 当前 RAG 结果作为 assistant 消息追加到历史末尾，LLM 可能将其视为自己说过的话而非外部知识（语义角色混淆）
- system 消息明确标记为外部知识，不会与 Agent 自己的回复混淆
- 放在最新消息旁边，attention 权重最高（避免 lost-in-the-middle 问题）

### 决策 6：任务状态通过 state_schema 嵌入

**选择**：通过 `create_agent` 的 `state_schema` 参数将 `TaskState` 嵌入 Agent 状态。

**替代方案**：将任务状态文本注入到用户消息之前（Recitation 方式）。

**理由**：
- `state_schema` 与 `middleware` 通过 `_resolve_schema` 自动合并，不冲突
- 类型安全、自动通过 checkpointer 持久化
- 消息注入方式会占用 token，且与 SummarizationMiddleware 的消息管理逻辑冲突

## 架构：中间件执行链路与数据流

```
用户消息到达 (api/chat.py)
  ↓
[系统提示构建] (prompt_builder.py)
  │  Cache Zone 1: SOUL + IDENTITY + USER (静态)
  │  Cache Zone 2: AGENTS + 精简技能摘要 (低频变化)
  │  Cache Zone 3: 动态记忆 + 任务状态 (高频变化，由 build_dynamic_prefix 生成)
  ↓
[Agent 调用] (agent.py → create_agent with middleware)
  │
  ├─ [ToolOutputBudgetMiddleware.abefore_model]    ← 第 1 层
  │    截断超过预算的工具输出（不改变消息数量和顺序）
  │    ↓
  ├─ [SummarizationMiddleware.abefore_model]       ← 第 2 层
  │    token > 8000 时自动触发，四段结构化摘要替换旧消息
  │    内置 AI/Tool 消息配对保护
  │    ↓
  ├─ [ContextAwareToolFilter.wrap_model_call]      ← 第 3 层
  │    根据对话上下文关键词匹配动态裁剪可用工具
  │    工具定义不变（保护 KV-cache），只控制运行时可见范围
  │    ↓
  ├─ [ToolCallLimitMiddleware]                     ← 第 4 层
  │    防止同一工具被反复调用（terminal: 10次, python_repl: 5次）
  │    ↓
  └─ [模型调用] → SSE 事件流返回
```

## 模块调用关系

```
app.py (启动时)
  ├─ SkillRegistry.discover(skills_dir)     ← 扫描 SKILL.md，构建元数据索引
  ├─ UnifiedMemoryRetriever.__init__()      ← 初始化三大记忆源
  └─ AgentManager._build_agent()            ← 构建 Agent
       ├─ prompt_builder.build_stable_prefix()   ← Zone 1 + Zone 2
       └─ middleware=[...]                        ← 四层中间件链

agent.py (每次请求)
  ├─ prompt_builder.build_dynamic_prefix()  ← Zone 3（动态记忆 + 任务状态）
  ├─ 注入 Zone 3 为 SystemMessage             ← 位于当前 HumanMessage 之前
  └─ AgentManager.stream(messages)              ← 中间件链 → 模型调用

api/chat.py (每次请求)
  ├─ UnifiedMemoryRetriever.retrieve(query)     ← 统一记忆检索
  ├─ 注入记忆为 system 消息                       ← 位置优化
  └─ AgentManager.stream(messages)              ← 中间件链 → 模型调用
```

## Risks / Trade-offs

**[风险] SummarizationMiddleware 与现有 SSE 流的兼容性** → 摘要发生在 `abefore_model` 钩子中，在模型调用之前执行，不影响 SSE 流式输出的 token 事件。但摘要生成本身会调用轻量 LLM，增加首次触发时的延迟。缓解：使用 `qwen-turbo` 等轻量模型，摘要延迟控制在 1-2 秒内。

**[风险] 工具输出截断丢失关键信息** → 前置截断保留头尾内容并标注省略字数，Agent 仍可通过 `read_file` 重新加载归档文件。对于极端大输出（> 10000 tokens），可选归档到 `sessions/archive/` 目录。

**[风险] 技能 metadata 扩展增加 SKILL.md 维护成本** → metadata 字段全部可选，简单技能（如 `get_weather`）只需 `name` + `description`，无需添加任何 metadata。系统通过启发式推断（从 description 内容提取触发词、判断自动调用权限）确保最低配置技能无缝接入。复杂技能按需扩展 metadata。不修改已有 SKILL.md 文件。

**[风险] 缓存前缀设计的 API 兼容性** → DashScope API 对 prompt caching 的支持可能需要特定的 `extra_body` 参数。缓解：Cache Zone 1+2 的分离设计是逻辑层面的优化，即使 API 不支持 cache control，减少动态内容混入静态区域仍有 token 效率收益。

**[权衡] 方案 A 不支持条件分支和人工介入** → 当前阶段不需要这些能力。如果未来需要（如用户确认后执行危险操作），需要迁移到方案 B（StateGraph），但中间件和 prompt_builder 模块可复用。

## Migration Plan

### 部署步骤

1. **Phase 1（低风险）**：添加 `ToolOutputBudgetMiddleware` 和 `ToolCallLimitMiddleware`，这是纯增量变更，不修改任何现有逻辑
2. **Phase 2（中风险）**：替换手动压缩为 `SummarizationMiddleware`，保留 `api/compress.py` 作为备用，前端压缩按钮降级为可选
3. **Phase 3（中风险）**：升级 `skills_scanner.py` 为 `SkillRegistry`，为现有技能补充 metadata
4. **Phase 4（低风险）**：修改记忆注入位置（assistant → system 消息）
5. **Phase 5（中风险）**：添加 `state_schema` 嵌入 `TaskState`
6. **Phase 6（中风险）**：系统提示三段式重构，可能需要调整 DashScope API 参数

### 回滚策略

- 每个 Phase 独立可回滚，通过 feature flag（`config.json`）控制开关
- Phase 2 回滚：重新启用 `api/compress.py`，移除 SummarizationMiddleware
- Phase 3 回滚：SkillRegistry 兼容旧格式（无 metadata 时退化为 `name` + `description`）
- Phase 6 回滚：缓存前缀是逻辑分离，可恢复为单段系统提示

## Open Questions

- DashScope API 对 prompt caching 的支持程度和参数格式需验证（可能需要查看最新文档或测试）
- `SummarizationMiddleware` 的结构化摘要是中文还是英文？需确认内置 prompt 是否支持中文，或需要自定义摘要 prompt
- 任务状态的触发检测（"帮我做"等动词）是否需要 LLM 辅助判断，还是关键词匹配足够
