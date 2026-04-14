## Why

当前系统存在两套独立的对话持久化机制——`session_manager`（JSON 文件）和 LangGraph `checkpointer`（SQLite），导致同一轮对话的消息被双写到两个存储中，且 Agent 的输入历史与前端展示历史分别依赖不同的数据源。这种双写双来源架构不仅带来数据一致性风险和存储浪费，还阻碍了基于 checkpoint 的中间件状态管理（如 SummarizationMiddleware）在恢复语义上的完整性。

迁移的核心目标不是删除 `session_manager`，而是**消除消息双写与历史双来源**，让 checkpointer 成为 LangGraph 线程状态的主要权威来源，同时将 `session_manager` 的职责收窄为会话元数据管理。

本提案严格遵循保守迁移原则：**先验证 checkpoint 消息恢复能力，再通过 feature flag 灰度切换，逐步停止 JSON 消息双写。**

## What Changes

- **新增 SessionRepository**：从 `session_manager` 中抽离会话元数据管理（标题、列表、创建/更新时间、删除标记），独立于消息存储。当前会话列表依赖扫描 `sessions/*.json` 文件，迁移后改为基于数据库表查询
- **新增 CheckpointProjection 投影层**：提供两个读取服务——`CheckpointHistoryService`（面向聊天气泡 UI）和 `CheckpointDebugViewService`（面向 Raw Messages 调试视图），将 checkpoint 中的 LangGraph message state 转换为前端当前消费的 DTO 格式
- **新增 Feature Flag 灰度控制**：通过 `checkpoint_history_read`、`checkpoint_agent_input`、`session_json_write_enabled` 三个开关控制迁移切换，确保每一步可回滚
- **修改 Agent 输入来源**：`chat.py` 和 `agent.py` 中 Agent 的历史消息输入从 `session_manager.load_session_for_agent()` 切换到依赖 checkpoint 的线程恢复能力（需先验证）
- **停止 JSON 消息双写**：在历史读取和 Agent 输入均稳定切到 checkpoint 后，停止 `session_manager.save_message()` 调用
- **修正 clear/delete 语义**：当前 `POST /api/sessions/{id}/clear` 和 `DELETE /api/sessions/{id}` 只处理了 JSON 侧，checkpoint 侧的 TaskState、中间件状态、消息快照残留未被清理，需要在迁移中一并解决
- **保留 session_manager 的元数据职责**：会话列表、标题管理、默认会话 bootstrap 规则仍由应用层管理，不归入 checkpointer
- **明确不纳入本次迁移的外部状态**：workspace prompt 文件（SOUL.md 等）、unified memory 检索结果、mem0 存储、memory_buffer.json 不归入 checkpointer 范围

## Capabilities

### New Capabilities

- `session-repository`：会话元数据仓库，管理标题、列表、创建/更新时间、删除标记，替代当前基于 JSON 文件扫描的元数据管理方式。新增 `sessions` 数据库表和 `SessionRepository` 类
- `checkpoint-projection`：checkpoint 状态投影层，将 LangGraph 线程状态转换为前端可消费的历史 DTO。包含面向 UI 气泡的 `CheckpointHistoryService` 和面向调试视图的 `CheckpointDebugViewService`，需处理 assistant 分段恢复、tool_calls 挂接、摘要消息展示等前端展示契约

### Modified Capabilities

- `task-state`：TaskState 的读写仍通过 checkpoint 的 `aget_state()`/`aupdate_state()` 进行，但当 Agent 输入来源切换到 checkpoint 后，需要验证 TaskState 在不传历史消息场景下的恢复行为是否与当前一致
- `middleware-chain`：SummarizationMiddleware 的状态持久化已依赖 checkpoint，但当 Agent 输入来源切换后，需要验证中间件在仅依赖 checkpoint 恢复的消息列表上能正确工作。同时 `compressed_context` 注入逻辑的保留或废弃需要根据投影能力决定

## Impact

### 后端核心代码

- **`backend/graph/session_manager.py`** — 重大重构：剥离元数据管理职责，消息读写方法逐步废弃，保留 `compress_history()` 直到 projection 层能替代 `compressed_context` 作用
- **`backend/graph/agent.py`** — 修改 `_build_messages()` 的历史来源、`astream()`/`ainvoke()` 中的 session_manager 调用，新增 checkpoint projection 调用路径
- **`backend/api/chat.py`** — 修改 `event_generator()` 中历史加载逻辑（L86），移除或灰度关闭 `save_message()` 调用（L145-155），保留流式中断的部分内容保存语义
- **`backend/api/sessions.py`** — 切换到 SessionRepository，保留 URL 路径不变
- **`backend/api/tokens.py`** — 切换到 checkpoint projection 读取消息进行 token 统计
- **`backend/api/compress.py`** — 不能默认视为无影响尾部清理项，需在 projection 能替代 `compressed_context` 后再决定去留

### 配置与存储

- **`backend/config.json`** — 新增 `features.checkpoint_history_read`、`features.checkpoint_agent_input`、`features.session_json_write_enabled` 三个 feature flag
- **`checkpoints.sqlite`** — 新增 `sessions` 元数据表（与 LangGraph 原生表共存，不修改 LangGraph 表结构）

### 前端

- **`frontend/src/lib/api.ts`** — 可能需要适配 `/history` 返回格式的微小变化
- 前端核心展示逻辑（assistant 分段、tool_calls 气泡、Raw Messages 面板）期望零改动，但需要通过 Phase 2 的对比验证确认

### 依赖

- 需要验证当前 `langgraph-checkpoint-sqlite` 版本是否支持 thread 级历史读取与清理能力
- 不引入新的外部依赖

### 与其他提案的关系

本提案应在以下两个提案之后实施：

1. **`unify-auxiliary-model`（优先执行）**：提供 `create_auxiliary_llm()` 工厂函数，统一辅助 LLM 配置。本提案 Phase 5 评估 `compress.py` 去留时，应基于已使用辅助模型的代码。两个提案共同修改 `backend/api/chat.py`、`backend/api/sessions.py`、`backend/graph/agent.py`，串行执行可减少合并冲突
2. **`progressive-tool-output-compression`（次优先执行）**：重构中间件链的触发逻辑。本提案 Phase 3 验证"SummarizationMiddleware 正常"时，应基于重构后的中间件行为。两个提案都修改了 `middleware-chain` spec，归档时需按正确顺序合并

本提案的 Phase 0（验证期）可与其他提案并行执行，因为它不修改线上代码
