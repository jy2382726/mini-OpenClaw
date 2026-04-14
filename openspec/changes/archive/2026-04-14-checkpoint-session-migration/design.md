## Context

当前系统存在两套独立的对话持久化机制，服务于不同的消费者：

```text
session_manager (JSON)
  ├── Agent 输入历史来源（chat.py L86: load_session_for_agent）
  ├── 前端展示历史（/history, /messages）
  ├── 会话元数据管理（列表、标题、创建/更新时间）
  ├── clear/delete 的用户可见语义
  └── compressed_context 压缩归档

checkpointer (SQLite)
  ├── TaskState 跨请求持久化（aget_state/aupdate_state）
  ├── SummarizationMiddleware 状态持久化
  └── LangGraph 框架自动状态快照
```

### 当前已验证的事实

1. `session_manager` 同时承担了三类职责（Agent 输入、UI 后端、展示格式落盘）
2. 同一轮对话存在消息双写（JSON + SQLite）
3. Agent 的消息输入来自 session_manager，不是 checkpoint
4. checkpoint 中的 messages 实际上没有被读取回传给 Agent
5. TaskState 通过 checkpoint 持久化已验证可用
6. clear/delete 只处理了 JSON 侧，checkpoint 侧存在残留

### 当前未验证的关键假设

1. 同一 `thread_id` 在不传历史消息时，checkpoint 能否完整恢复 messages
2. 恢复后的消息列表是否包含 SummarizationMiddleware 修改后的结果
3. 恢复后的消息是否会与应用层传入的历史产生重复注入
4. 当前 `AsyncSqliteSaver` 是否支持 thread 级历史读取与清理
5. checkpoint 投影能否忠实恢复当前前端展示契约（assistant 分段、tool_calls 挂接）

## Goals / Non-Goals

**Goals:**

1. 消除消息双写：同一轮对话不再同时写入 JSON 和 SQLite
2. 消除历史双来源：Agent 输入和前端展示使用同一个数据源
3. 让 checkpointer 成为 LangGraph 线程状态的主要权威来源
4. 让 SessionRepository 成为会话元数据的权威来源
5. 修正 clear/delete 语义，确保 checkpoint 侧数据也被正确处理
6. 每一步迁移都可通过 feature flag 回滚

**Non-Goals:**

1. 不删除 `session_manager.py`——它将转型为元数据管理器，消息读写方法逐步废弃
2. 不让 checkpointer 管理 workspace prompt、mem0、memory_buffer 等外部状态
3. 不修改 LangGraph 原生 checkpoint 表结构
4. 不在前端做功能性改造——期望零 UI 改动完成迁移
5. 不改变现有 SSE 事件协议（token/tool_start/tool_end/new_response/done）
6. 不在本次迁移中重构 `compress.py`——只有 projection 能替代 `compressed_context` 后才能清理

## Decisions

### Decision 1: Phase 0 验证优先，不做未经验证的假设

**选择**：先做 spike 验证 checkpoint 的消息恢复、历史投影和线程清理能力，再开始正式迁移。

**而非**：直接假设 checkpoint 能替代 JSON 并开始删除代码。

**原因**：设计文档 v2 的核心教训是"不把 LangGraph 的默认行为当作已证实事实"。当前仓库验证了 TaskState 持久化，但没有验证消息恢复语义。如果恢复后消息有重复注入、缺少中间件修改结果、或格式与 UI 契约不一致，直接迁移会导致生产事故。

**验证清单**：
- 仅传本轮用户消息，LangGraph 是否基于 thread_id 自动恢复 messages
- 恢复后的消息是否包含 SummarizationMiddleware 的修改结果
- 是否存在消息重复注入
- `AsyncSqliteSaver` 是否有 thread 级读取/删除接口

### Decision 2: 分层架构——SessionRepository + CheckpointProjection

**选择**：将系统分为三层：SessionRepository（元数据）、Checkpoint Thread State（线程状态）、Projection Services（投影）。

**而非**：把所有职责都交给 checkpointer 或简单删除 session_manager。

**原因**：设计文档 v2 明确指出"checkpointer 不是当前 Agent 的唯一状态来源"。会话元数据（标题、列表、bootstrap 规则）是应用层关注点，不应归入 LangGraph 框架的 checkpoint。投影层是必要的，因为 LangGraph 内部的 message 格式（HumanMessage、AIMessage、ToolMessage）与前端消费的 DTO（role/content/tool_calls）不一致，需要专门的转换层。

### Decision 3: 双投影服务——History + DebugView

**选择**：拆分为 `CheckpointHistoryService`（面向 UI 气泡）和 `CheckpointDebugViewService`（面向 Raw Messages 调试视图）。

**而非**：用一个统一的投影服务处理所有展示场景。

**原因**：前端展示语义比初看复杂得多。一轮回答可拆成多个 assistant 气泡（`new_response` 事件），每个段可挂接自己的 `tool_calls`。Raw Messages 面板展示的是 system prompt + 消息列表。两者需要的投影规则不同，拆开后各自的复杂度可控，且 DebugView 可以明确标注为"近似调试视图"而非"真实模型输入"。

### Decision 4: Feature Flag 灰度控制

**选择**：通过三个独立的 feature flag 控制迁移切换：
- `checkpoint_history_read`：控制 /history 和 /messages 的数据来源
- `checkpoint_agent_input`：控制 Agent 的历史输入来源
- `session_json_write_enabled`：控制是否继续 JSON 消息双写

**而非**：一次性全部切换或只用一个全局开关。

**原因**：三个 flag 对应三个独立的关注点（读取、输入、写入），可以独立开启/关闭。例如可以先关闭 JSON 写入但保持从 JSON 读取，或先切 Agent 输入但保持前端从 JSON 读取。这种粒度确保每一步可回滚。

### Decision 5: session 元数据存储在 SQLite 的业务表中

**选择**：在 `checkpoints.sqlite` 中新增 `sessions` 元数据表，与 LangGraph 原生表共存。

**而非**：继续用 JSON 文件存储元数据，或使用独立的数据库文件。

**原因**：同一 SQLite 文件减少文件管理复杂度；LangGraph 原生表不修改；元数据表只存 session_id、title、created_at、updated_at、deleted_at，结构简单。如果未来需要切换到 PostgreSQL 等其他后端，元数据和 checkpoint 数据一起迁移。

### Decision 6: clear/delete 的分层语义

**选择**：
- clear：用户视角下历史清空。候选实现为"新 thread_id + 旧 thread 屏蔽"，而非强制要求物理删除
- delete：产品语义为"从列表消失，历史不可访问"，元数据标记删除，checkpoint 物理删除可延后为后台 GC

**而非**：要求 clear/delete 必须物理删除 checkpoint 数据。

**原因**：当前 `AsyncSqliteSaver` 的 thread 级物理删除能力未验证。如果底层不支持，强求物理删除会阻塞整个迁移。分层语义确保产品行为正确，存储清理可以异步处理。`session_id` 与 `thread_id` 的绑定关系在 clear 后需要重新定义——不坚持 `thread_id` 必须永远等于 `session_id`。

### Decision 7: 保留 JSON 文件只读兼容期

**选择**：在 Phase 3-4 期间，保留 JSON 文件的只读能力，允许新旧系统并行运行和结果对比。

**而非**：切换后立即删除所有 JSON 读写代码。

**原因**：灰度切换期间需要对比 JSON 和 checkpoint 的结果。只读兼容也确保回滚路径存在——如果 checkpoint 投影发现问题，可以立即切回 JSON 读取。

## Risks / Trade-offs

### [Risk] checkpoint 消息恢复不完整或格式异常 → Mitigation
Phase 0 的 spike 如果发现恢复后消息有问题（重复注入、缺少中间件修改、顺序错误），则不能进入 Phase 3。此时需要评估问题是否可通过代码修正，还是需要继续依赖 session_manager。

### [Risk] 前端展示契约无法忠实恢复 → Mitigation
assistant 分段恢复和 tool_calls 挂接是最大的投影复杂点。如果无法完美恢复当前 UI 契约，需要明确告知用户这是产品变更而非内部实现替换。Phase 2 的对比验证会提前暴露这个问题。

### [Risk] clear/delete 的线程清理不支持 → Mitigation
使用"新 thread_id + 旧 thread 屏蔽"的候选方案，不依赖物理删除。元数据层维护 session_id 到 thread_id 的映射，clear 后映射指向新 thread。

### [Risk] 迁移过程中流式中断的部分内容保存语义丢失 → Mitigation
当前 JSON 会在流式中断时保存部分回答。checkpoint 的自动快照在 `agent.astream` 正常执行时才写入，中断时可能不产生 checkpoint。需要明确定义迁移后的中断保存行为，可能需要保留独立的"部分内容"暂存机制。

### [Risk] "default" 会话和懒创建 bootstrap 规则 → Mitigation
当前 JSON 文件的懒创建行为（不显式建会话也能直接聊天）在迁移到数据库后需要重新实现。必须先明确 default 会话的保留/移除策略，否则前端在"默认会话/删除当前会话/新建后立即发送消息"场景会出现行为回归。

### [Trade-off] 增加 SQLite 中的业务表 vs 保持 JSON 文件
选择在 SQLite 中新增业务表，好处是统一存储、减少文件 I/O、便于事务管理。代价是与 LangGraph 原生表耦合在同一文件中，如果未来 LangGraph 升级改变了 checkpoint 存储方式，业务表需要独立迁移。

## 数据流

### 当前数据流

```text
前端 POST /api/chat
  │
  ├── 读取 JSON 历史 ← session_manager.load_session_for_agent()
  │
  ├── 传入 Agent ← agent_manager.astream(message, history, session_id)
  │       │
  │       ├── 构建 Agent（带 checkpointer）
  │       ├── 读取 TaskState ← agent.aget_state() (SQLite)
  │       ├── 写入 TaskState ← agent.aupdate_state() (SQLite)
  │       └── 执行 Agent ← 自动写入 checkpoint (SQLite)
  │
  └── SSE done 后写入 JSON ← session_manager.save_message() (JSON)
```

### 目标数据流

```text
前端 POST /api/chat
  │
  ├── touch 会话元数据 ← SessionRepository.touch()
  │
  ├── Agent 输入由 checkpoint 恢复（不传历史）
  │
  ├── Agent 执行 ← 自动写入 checkpoint (SQLite)
  │       │
  │       ├── TaskState 自动持久化
  │       └── 中间件状态自动持久化
  │
  └── 不再写 JSON（save_message 不再调用）

前端 GET /api/sessions/{id}/history
  │
  └── CheckpointHistoryService.project(thread_id)
          │
          ├── 从 checkpoint 读取最新 state
          └── 转换为 UI DTO（role/content/tool_calls）

前端 GET /api/sessions
  │
  └── SessionRepository.list()
          │
          └── 从 sessions 表查询（不再扫描 JSON 文件）
```

## Migration Plan

### Phase 0：验证期（spike）

不改线上语义，只验证假设。

1. 验证 `thread_id` 下的 checkpoint 消息恢复行为
2. 验证是否存在 thread 级历史读取与清理能力
3. 做一个最小 `checkpoint → history DTO` 原型
4. 明确 `"default"` 会话与懒创建语义
5. 验证流式中断时的 checkpoint 状态

**验收**：形成可执行结论，决定是否继续 Phase 1-5。

### Phase 1：元数据抽离

1. 新增 `sessions` 表
2. 新增 `SessionRepository` 类
3. `create/list/rename/touch/delete` 切到 repository
4. 保留 JSON 消息读写不动

**验收**：会话列表不再依赖扫描 JSON 文件。

### Phase 2：历史读取实验迁移

1. 新增 `CheckpointHistoryService`
2. 新增 `CheckpointDebugViewService`
3. 增加 feature flag
4. 灰度模式下对比 JSON 与 checkpoint 投影结果

**验收**：找到 assistant 分段、tool_calls、摘要消息的差异，评估是否能忠实恢复 UI 契约。

### Phase 3：Agent 输入迁移

前提：Phase 0 证明 checkpoint 消息恢复语义可靠。

1. 引入 `checkpoint_agent_input` flag
2. 在 flag 打开时不再从 JSON 读取 Agent 历史
3. 保留 JSON 写入，便于回滚和结果比对

**验收**：多轮上下文正常、TaskState 恢复正常、SummarizationMiddleware 正常、无历史重复注入。

### Phase 4：停止 JSON 消息双写

前提：`/history` 与 `/messages` 已稳定由 projection 提供，Agent 输入已稳定切到 checkpoint。

1. 关闭 `session_json_write_enabled`
2. 停止 `save_message()` 调用
3. 保留只读兼容或迁移脚本

**验收**：新会话不再生成新的 JSON 消息文件，前端行为无明显回归。

### Phase 5：清理兼容逻辑

1. 删除 `load_session_for_agent()` 运行时依赖
2. 删除消息相关 JSON 读写逻辑
3. 重新评估 `compress.py` 与 `compressed_context`
4. 明确 Raw Messages 的长期定义

**注意**：`compress.py` 不能默认视为尾部清理项，只有在 projection 能替代 `compressed_context` 作用后才能清理。

### 回滚策略

- 每个 Phase 都通过 feature flag 控制，可随时切回旧行为
- Phase 1-2 不改变任何现有行为，只新增并行链路
- Phase 3 关闭 flag 即可回滚到 session_manager 输入
- Phase 4 重新打开 `session_json_write_enabled` 即可恢复 JSON 写入
- Phase 5 才是真正删除代码，只有在 Phase 3-4 稳定运行后才执行

## 与其他提案的依赖关系

本提案（checkpoint-session-migration）与以下两个已存在提案存在文件冲突和功能依赖：

### 依赖 `unify-auxiliary-model`（统一辅助模型）

- **文件冲突**：两者都修改 `backend/api/chat.py`（generate_title LLM 来源 vs 历史加载逻辑）、`backend/api/sessions.py`（LLM 来源 vs SessionRepository 切换）、`backend/graph/agent.py`（辅助模型工厂 vs Agent 输入来源）
- **功能依赖**：`unify-auxiliary-model` 提供的 `create_auxiliary_llm()` 工厂函数会被 `compress.py`、标题生成等辅助任务使用。本提案 Phase 5 评估 `compress.py` 去留时，应基于已迁移到辅助模型的代码
- **建议**：在 `unify-auxiliary-model` 完成后再开始本提案的 Phase 1-5，以减少合并冲突和重复修改

### 依赖 `progressive-tool-output-compression`（渐进式工具输出压缩）

- **文件冲突**：两者都修改 `backend/graph/agent.py` 的 `_build_middleware()` 和 `backend/config.json`
- **功能依赖**：本提案 Phase 3 的验收条件之一是"SummarizationMiddleware 正常"。如果 `progressive-tool-output-compression` 已重构了中间件的触发逻辑（从固定 token 改为上下文窗口比例），则本提案的验证应基于重构后的中间件行为
- **Spec 冲突**：两个提案都修改了 `middleware-chain` spec 的 SummarizationMiddleware Requirement。归档时需要按正确顺序合并（先 progressive-compression，再本提案）
- **建议**：在 `progressive-tool-output-compression` 完成后再进入本提案 Phase 3（Agent 输入迁移）

### 推荐实施时间线

```text
unify-auxiliary-model ─────────────────────→ 完成
                         │
                         └─ 本提案 Phase 0 spike（可并行，不修改线上代码）
                                              ↓
                    progressive-compression ──→ 完成
                                              │
                                              ↓
                              本提案 Phase 1-5 ──→ 完成
```

## Open Questions

1. **`AsyncSqliteSaver` 是否有 thread 级读取和删除接口？** 需要在 Phase 0 中通过阅读源码和实际测试验证
2. **`thread_id` 是否必须永远等于 `session_id`？** 如果 clear 后需要切换 thread，是否引入 `thread_revision` 概念
3. **流式中断的部分内容保存是否仍需要？** checkpoint 在中断时可能不产生快照，是否需要独立的暂存机制
4. **`"default"` 会话的保留策略？** 是彻底移除还是保留为特殊概念
5. **非流式 `ainvoke()` 与流式 `astream()` 的 checkpoint 行为是否一致？** 当前两者的落盘语义并不完全相同
6. **前端 Raw Messages 面板是继续作为"近似调试视图"还是升级为"真实执行载荷视图"？** 影响投影层的实现复杂度
