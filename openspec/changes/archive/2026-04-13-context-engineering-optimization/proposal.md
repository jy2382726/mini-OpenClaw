## Why

mini-OpenClaw 当前上下文工程存在四大瓶颈：系统提示无缓存设计导致每次请求全量计算、对话压缩质量低下（500 字粗暴摘要 + 50% 切割）、工具全量加载无法按需裁剪、任务状态完全缺失导致长任务偏离目标。项目已安装 `langchain==1.2.12` + `langgraph==1.1.2`，但 `SummarizationMiddleware`、`trim_messages`、`wrap_model_call`、`ToolCallLimitMiddleware` 等内置中间件能力完全未使用，ROI 最高的优化被严重低估。

## What Changes

- 引入 **三段式系统提示缓存前缀**（Cache Zone 1-3），将动态内容（记忆/RAG）从系统提示移至用户消息前缀，提升 KV-cache 命中率
- 引入 **LangChain 中间件链**：`ToolOutputBudgetMiddleware`（前置截断）→ `SummarizationMiddleware`（自动结构化摘要）→ `ContextAwareToolFilter`（运行时工具过滤）→ `ToolCallLimitMiddleware`（防死循环），替代当前手动压缩和硬编码截断
- 升级 **技能元数据管理**：遵循 Agent Skills 标准（agentskills.io），通过 `metadata` 字段扩展技能属性，实现分级注入（隐藏/索引/按需/预加载）
- 新增 **统一记忆检索层**：合并 MEMORY.md、mem0、RAG 三大记忆源为单一接口，记忆注入位置从 assistant 消息改为 system 消息
- 新增 **任务状态管理**：通过 `state_schema` 嵌入 `TaskState`，实现多步任务追踪和断点续做
- **BREAKING**：移除 `api/compress.py` 前端手动压缩接口，改为 SummarizationMiddleware 自动触发

## Capabilities

### New Capabilities

- `middleware-chain`: LangChain 中间件链架构，包含工具输出截断、自动摘要、运行时工具过滤、工具限流四个中间件
- `skill-registry`: 遵循 Agent Skills 标准的技能注册表，支持元数据扩展、分级注入、触发词索引
- `unified-memory`: 统一记忆检索层，合并三大记忆源为单一接口，优化注入位置和方式
- `task-state`: 通过 state_schema 嵌入的任务状态管理，支持多步任务追踪和断点续做
- `cache-prefix`: 三段式系统提示缓存前缀设计，分离静态/低频/高频变化内容

### Modified Capabilities

（无已有 spec 需要修改）

## Impact

- `backend/graph/agent.py` — Agent 构建逻辑重构，添加 middleware 链和 state_schema
- `backend/graph/prompt_builder.py` — 三段式缓存前缀设计，技能快照改为精简格式
- `backend/tools/skills_scanner.py` — 升级为 SkillRegistry，解析 Agent Skills 标准 metadata
- `backend/api/compress.py` — **BREAKING** 移除或标记废弃（由 SummarizationMiddleware 替代）
- `backend/graph/memory_indexer.py` — 集成到统一记忆检索层
- `backend/api/chat.py` — RAG 结果注入方式从 assistant 消息改为 system 消息
- `backend/tools/` — 各工具的 SKILL.md 需补充 metadata 扩展字段
- `backend/workspace/` — 无变更（SOUL.md 等保持不变）
- `frontend/` — 移除手动压缩按钮 UI（或标记为可选）
- 依赖：`langchain>=1.2.12`（已满足）、`langgraph>=1.1.2`（已满足）
