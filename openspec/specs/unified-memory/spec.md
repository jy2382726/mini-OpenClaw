## Purpose

定义统一记忆检索接口（UnifiedMemoryRetriever），整合 MEMORY.md 文件注入、mem0 结构化记忆和 RAG 向量检索，实现记忆按相关性过滤和优化注入位置。同时定义记忆写入侧组件（MemoryBuffer、Mem0Manager、MemoryConsolidator）的行为规范。

## Requirements

### Requirement: 统一记忆检索接口

系统 SHALL 提供 `UnifiedMemoryRetriever` 类，将 mem0 结构化记忆、RAG 向量索引、MEMORY.md 文件段落匹配统一为单一 `retrieve(query, top_k)` 接口。

检索结果 MUST 按分数降序统一排序（`_result_score` 函数），不区分来源优先级。mem0 检索结果自带置信度分数，RAG 检索结果自带相似度分数，MEMORY.md 段落匹配使用固定分数 0.5。

默认 `top_k=5`，最小相关性阈值 `MIN_RELEVANCE_SCORE = 0.3`。

检索 MUST 支持异步版本 `retrieve_async(query, top_k)`，通过 `run_in_executor` 包装同步调用。

#### Scenario: 从多记忆源检索

- **WHEN** 调用 `retrieve("用户偏好", top_k=5)`
- **THEN** 系统依次从 mem0（置信度分数）、RAG 向量索引（LlamaIndex 相似度分数）、MEMORY.md（关键词匹配，固定分数 0.5）检索，合并去重后按分数降序返回最多 5 条结果

#### Scenario: 按分数统一排序

- **WHEN** 同一信息在多个记忆源中存在
- **THEN** 系统 MUST 按分数降序统一排序，高分 RAG 结果可以排在低分 mem0 结果前面。分数相同时 mem0 结果因先被加入列表而排在前面（Python sort 稳定排序）

#### Scenario: 某记忆源不可用时降级

- **WHEN** mem0 服务不可用或 `mem0_client` 为 None
- **THEN** 系统 MUST 从 RAG 向量索引和 MEMORY.md 文件继续检索，不阻塞请求。每个检索源独立 try/except，失败时打印警告并返回空列表

### Requirement: 记忆注入位置优化

系统 MUST 将记忆检索结果作为 SystemMessage 注入在当前用户消息之前，而非作为 AssistantMessage 追加到历史末尾。

注入格式通过 `format_for_injection()` 方法生成，格式为 `[相关记忆]\n内容（来源: {source}，置信度: {score}）`。

非 RAG 模式下也通过统一检索接口按需检索（受 `features.unified_memory` 控制，默认为 true）。

#### Scenario: 检索结果注入为 SystemMessage

- **WHEN** 检索到相关记忆条目
- **THEN** 记忆内容作为 SystemMessage 注入，位于当前 HumanMessage 之前，通过 `messages.insert(len(messages) - 1, SystemMessage(content=dynamic_prefix))` 实现

#### Scenario: 非 RAG 模式也使用统一检索

- **WHEN** 非 RAG 模式且 `features.unified_memory` 为 true
- **THEN** 系统 MUST 通过 `UnifiedMemoryRetriever.retrieve()` 按相关性过滤 MEMORY.md 片段，只注入与当前 query 匹配的段落（关键词子串匹配），而非全文注入

### Requirement: 记忆条目相关性过滤

系统 MUST 对检索到的记忆条目进行相关性过滤，只保留分数超过 `MIN_RELEVANCE_SCORE`（0.3）的条目。

过滤通过 `_result_score()` 函数实现，优先取 `confidence` 字段，其次取 `score` 字段作为排序/过滤依据。

#### Scenario: 高分记忆被注入

- **WHEN** 检索到置信度 0.9 的 mem0 记忆
- **THEN** 该记忆条目（分数 0.9 > 0.3 阈值）MUST 被注入到请求上下文中

#### Scenario: 低分记忆被过滤

- **WHEN** 检索到置信度 0.1 的 mem0 记忆
- **THEN** 该记忆条目（分数 0.1 < 0.3 阈值）MUST NOT 被注入，减少无关 token 开销

### Requirement: 对话缓冲区（MemoryBuffer）

系统 SHALL 提供 `MemoryBuffer`，作为对话缓冲区 + 智能截流器，累积对话轮次并智能判断 mem0 写入时机。

MemoryBuffer MUST 支持五种触发写入模式：
1. **立即触发**：显式保存指令或强烈纠正
2. **轮次触发**：累积到配置的轮次阈值
3. **时间触发**：超过配置的时间间隔
4. **会话结束触发**：会话结束时 flush
5. **启动恢复触发**：应用启动时恢复缓冲区状态

MemoryBuffer 仅用于写入侧（向 mem0 批次化写入），不参与 `UnifiedMemoryRetriever.retrieve()` 的检索。

#### Scenario: 显式保存指令触发立即写入

- **WHEN** 用户消息包含"记住这个"等保存指令
- **THEN** MemoryBuffer MUST 立即将累积的对话轮次写入 mem0

#### Scenario: 累积到轮次阈值批量写入

- **WHEN** 对话轮次累积到配置的阈值（默认 5 轮）
- **THEN** MemoryBuffer MUST 批量将累积轮次写入 mem0

### Requirement: Mem0 生命周期管理（Mem0Manager）

系统 SHALL 提供 `Mem0Manager`，封装 mem0 客户端的完整生命周期管理，包括 add/search/delete/verify/batch_add 操作和结构化事实提取。

Mem0Manager 的 LLM 实例使用 `get_auxiliary_model_config()` 获取模型名称后自行构建 mem0 专用 LLM 配置（temperature 固定为 0.1），而非直接使用 `create_auxiliary_llm()` 返回的 ChatOpenAI 实例。

#### Scenario: 事实提取使用辅助模型

- **WHEN** mem0 执行对话事实提取
- **THEN** 系统 SHALL 使用辅助模型的 model 配置创建 mem0 LLM 实例，temperature 固定为 0.1

### Requirement: 离线记忆整合（MemoryConsolidator）

系统 SHALL 提供 `MemoryConsolidator`，作为离线记忆整合器，执行四阶段管道：去重、合并、冲突检测、过期清理。

#### Scenario: 重复记忆去重

- **WHEN** mem0 中存在语义重复的记忆条目
- **THEN** MemoryConsolidator SHALL 合并为单条记忆，保留最新的元数据

### Requirement: 后台 mem0 写入

系统 MUST 在 Agent 流式响应完成后，通过后台线程（`_schedule_mem0_write`）异步执行 mem0 写入操作，不阻塞 SSE 流。

#### Scenario: mem0 写入不阻塞响应

- **WHEN** Agent 完成响应，需要将对话内容写入 mem0
- **THEN** 系统 MUST 在后台线程中执行写入，SSE 流无需等待写入完成
