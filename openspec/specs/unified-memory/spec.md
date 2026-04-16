## Purpose

定义统一记忆检索接口（UnifiedMemoryRetriever），整合 MEMORY.md 文件注入、mem0 结构化记忆和 RAG 向量检索，实现记忆按相关性过滤和优化注入位置。

## Requirements

### Requirement: 统一记忆检索接口

系统 SHALL 提供 `UnifiedMemoryRetriever` 类，将 MEMORY.md 文件注入、mem0 结构化记忆、RAG 向量检索统一为单一 `retrieve(query, top_k)` 接口。检索结果 MUST 按来源和相关性综合排序。

#### Scenario: 从多记忆源检索

- **WHEN** 调用 `retrieve("用户偏好", top_k=5)`
- **THEN** 系统依次从 mem0（置信度 + 新鲜度）、RAG 向量索引（LlamaIndex）、短期对话缓冲区检索，合并后按相关性返回最多 5 条结果

#### Scenario: 记忆源优先级排序

- **WHEN** 同一信息在多个记忆源中存在
- **THEN** 优先返回 mem0 结构化记忆（有置信度和新鲜度指标），其次为 RAG 检索结果

#### Scenario: 某记忆源不可用时降级

- **WHEN** mem0 服务不可用
- **THEN** 系统 MUST 从 RAG 向量索引和 MEMORY.md 文件继续检索，不阻塞请求

### Requirement: 记忆注入位置优化

系统 MUST 将记忆检索结果作为 system 消息注入在当前用户消息之前，而非作为 assistant 消息追加到历史末尾。

#### Scenario: RAG 模式下记忆注入为 system 消息

- **WHEN** RAG 模式开启，检索到相关记忆条目
- **THEN** 记忆内容作为 SystemMessage 注入，格式为"[相关记忆] 内容（来源: mem0，置信度: 0.9）"，位于当前 HumanMessage 之前

#### Scenario: 非 RAG 模式下 MEMORY.md 注入优化

- **WHEN** 非 RAG 模式，MEMORY.md 内容通过统一检索接口按相关性过滤
- **THEN** 只注入与当前 query 相关的 MEMORY.md 片段，而非全文注入

### Requirement: 记忆条目相关性过滤

系统 MUST 根据当前用户消息的语义，对检索到的记忆条目进行相关性过滤，只注入相关度超过阈值的条目。

#### Scenario: 高相关度记忆被注入

- **WHEN** 用户消息为"帮我查天气"，检索到记忆"用户住在北京（置信度: 0.9）"
- **THEN** 该记忆条目被注入到请求上下文中

#### Scenario: 低相关度记忆被过滤

- **WHEN** 用户消息为"帮我查天气"，检索到记忆"用户上周买了新电脑（置信度: 0.3）"
- **THEN** 该记忆条目不被注入，减少无关 token 开销
