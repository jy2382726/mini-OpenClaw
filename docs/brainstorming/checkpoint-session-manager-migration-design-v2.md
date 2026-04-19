# session_manager 向 LangGraph checkpointer 迁移设计稿 v2

> 本文档以当前仓库可验证代码为准，目标是修正上一版中把推断写成事实的问题。
> 结论分为三类：
> 1. 已由代码直接验证的事实
> 2. 尚未验证、必须先做 spike 的假设
> 3. 当前阶段明确不纳入“checkpointer 单一事实来源”的外部状态

---

## 一、文档定位

这不是“立刻删除 `session_manager`”的执行稿，而是一份更保守的迁移设计。

本文约束：

- 不把 LangGraph 的默认行为、checkpointer 的恢复语义、线程删除能力当作已证实事实
- 不把当前 Agent 的所有运行状态都错误归入 checkpointer
- 不把前端当前展示契约简化为“只要能投影 messages 就够了”

一句话目标：

> **优先消除消息双写与历史双来源，但前提是先验证 checkpoint 消息恢复、历史投影和线程清理能力。**

---

## 二、当前代码中已验证的事实

以下结论均可从当前代码直接确认。

### 2.1 `session_manager` 目前同时承担了三类职责

#### A. Agent 输入历史来源

- `backend/api/chat.py` 在流式请求开始前调用 `session_manager.load_session_for_agent(session_id)`
- `backend/graph/agent.py` 的非流式 `ainvoke()` 也调用 `session_manager.load_session_for_agent(session_id)`
- 当前 Agent 不是“只传本轮用户消息”，而是显式把 JSON 历史重新拼接为 `messages`

#### B. 会话 UI / API 后端

- `backend/api/sessions.py`
  - `GET /api/sessions`
  - `POST /api/sessions`
  - `PUT /api/sessions/{session_id}`
  - `DELETE /api/sessions/{session_id}`
  - `GET /api/sessions/{session_id}/history`
  - `GET /api/sessions/{session_id}/messages`
  - `POST /api/sessions/{session_id}/generate-title`
  - `POST /api/sessions/{session_id}/clear`
- `backend/api/tokens.py` 的 `GET /api/tokens/session/{session_id}` 也依赖 JSON 消息
- `backend/api/compress.py` 仍可用，前端仍保留压缩入口

#### C. 展示友好的消息落盘格式

- `backend/api/chat.py` 在 SSE `done` 阶段把用户消息和 assistant segment 写入 JSON
- 当流式中断时，`finally` 里仍会把部分内容保存到 JSON
- 当前前端消费的 `tool_calls` 不是直接来自 checkpoint，而是来自 `chat.py` 手工组织后的 JSON 结构

### 2.2 当前系统确实存在消息双写

同一轮对话里，至少存在两套持久化路径：

1. `AgentManager` 构建带 checkpointer 的 agent，并传入 `thread_id=session_id`
2. `chat.py` 或 `agent.py` 在请求结束时再调用 `session_manager.save_message()`

这说明：

- JSON 仍是当前应用层真正使用的历史来源
- checkpointer 也在被写入，但当前项目主要把它用于 TaskState 与 LangGraph 内部状态

### 2.3 当前项目已验证的是 TaskState 持久化，不是消息恢复语义

仓库内已有测试能证明：

- `thread_id` 被传入 `agent.astream()` / `agent.ainvoke()`
- `task_state` 可通过 `aget_state()` / `aupdate_state()` 跨请求恢复
- `AsyncSqliteSaver` 已被接入当前 AgentManager

但当前仓库**没有**等价证明以下命题：

- 同一 `thread_id` 的后续请求在不传历史消息时，checkpoint 中的 `messages` 能完整恢复并替代 JSON 历史
- 恢复后的消息顺序、工具消息、摘要结果与当前 UI 契约一致
- 恢复后的上下文不会与应用层传入的历史产生重复注入

因此不能把“checkpointer 可以直接替代当前 Agent 输入历史”写成既成事实。

### 2.4 当前前端展示语义比文档初稿描述更复杂

当前前端展示不是简单的“user / assistant 消息列表”，而是包含以下行为：

- 一轮回答可能拆成多个 assistant 气泡
- `new_response` 会创建新的 assistant 消息段
- 每个 assistant 段可挂接自己的 `tool_calls`
- Raw Messages 面板展示的是 `system prompt + JSON messages`
- 会话切换时，前端直接从 `/history` 恢复既有消息显示

同时，Agent 输入历史与前端展示历史也不是同一种表示：

- 给前端看的 JSON 可以保留多个连续 assistant 段
- 给模型用的 `load_session_for_agent()` 会重新合并连续 assistant 消息
- `compressed_context` 会作为首条 assistant 消息注入给模型

这意味着迁移并不是“做一个 checkpoint -> history DTO 投影”这么简单，而是至少要定义两类投影：

1. UI 展示投影
2. Agent 输入投影（如果仍需要应用层参与组装）

### 2.5 `clear` / `delete` 当前确实只处理了 JSON 侧

当前实现中：

- `POST /api/sessions/{id}/clear` 只调用 `session_manager.clear_messages()`
- `DELETE /api/sessions/{id}` 只删除 JSON 文件

当前代码里没有线程级 checkpoint 清理逻辑。

因此用户看到“清空/删除”后：

- JSON 历史确实会消失
- 但 checkpoint 侧的 `task_state`、中间件状态、消息快照是否仍残留，没有被当前接口处理

这一点是已确认问题，不是推测。

### 2.6 checkpointer 不是当前 Agent 的唯一状态来源

即便未来迁移成功，checkpointer 也不能被表述为“Agent 全部运行状态唯一来源”。

当前系统还依赖以下外部状态：

- `workspace/SOUL.md`、`IDENTITY.md`、`USER.md`、`AGENTS.md`
- 技能快照与 SkillRegistry 结果
- `UnifiedMemoryRetriever` 的检索结果
- mem0 及其本地向量存储
- `storage/memory_buffer.json` 中的缓冲对话

因此更准确的说法应为：

> **checkpointer 可以成为 LangGraph 线程状态的主要持久化来源，但不是整个应用全部运行状态的唯一来源。**

---

## 三、上一版文档中需要纠正的结论

### 3.1 需要删除或降级的强结论

以下表述在当前仓库中证据不足，必须改写：

1. “只传本轮用户输入即可，由 checkpointer 恢复完整历史”
2. “checkpoint latest state 一定可以稳定投影为现有前端历史格式”
3. “clear 时直接重建 session thread”已经可实施
4. “delete 可以直接清理该 thread_id 的 checkpoint 数据”
5. “checkpointer = Agent 运行状态唯一来源”

这些结论最多只能写成：

- 待验证目标
- 设计假设
- 需要 spike 后确认的方案

### 3.2 被遗漏的接口与行为

上一版文档遗漏或低估了以下现状：

1. `POST /api/sessions/{id}/generate-title` 仍存在
2. `POST /api/sessions/{id}/compress` 仍存在，且前端仍可触发
3. 会话默认值存在 `"default"` 语义
4. 流式中断时当前会保存“部分回答”
5. 非流式 `ainvoke()` 与流式 `astream()` 的落盘语义并不完全一致

---

## 四、修正后的目标定义

### 4.1 总体目标

将当前系统从：

```text
JSON session_manager
  - Agent 输入历史
  - 会话元数据
  - UI 展示消息
  - clear/delete 的用户可见语义

SQLite checkpointer
  - TaskState
  - LangGraph 内部状态
  - 中间件状态
```

逐步演进到：

```text
SessionRepository
  - 会话元数据
  - 标题
  - 创建/更新时间
  - 删除标记或删除状态

Checkpoint Thread State
  - LangGraph 线程消息
  - task_state
  - LangGraph middleware state

Projection Layer
  - checkpoint -> UI history
  - checkpoint -> raw/debug view
```

### 4.2 修正后的关键措辞

推荐把目标写成：

> **让 checkpointer 成为 LangGraph 线程状态的主要权威来源；让 SessionRepository 成为会话元数据权威来源；让 Projection 层成为前端历史展示来源。**

不要写成：

> checkpointer 负责 Agent 全部运行状态

---

## 五、当前阶段必须先验证的前置假设

这些问题不先验证，后续迁移方案都只能停留在纸面。

### 5.1 假设 A：checkpoint 消息可安全替代 JSON 历史输入

需要验证：

1. 仅传本轮用户消息时，LangGraph 是否会基于 `thread_id` 自动恢复先前 `messages`
2. 恢复后的消息列表是否包含 `SummarizationMiddleware` 修改后的结果
3. 恢复后的消息是否会与当前应用层传入历史发生重复
4. 恢复后的消息是否满足当前工具调用、多轮对话、系统提示注入顺序需求

在该假设验证完成前，不能把 Phase 3 写成既定事实。

### 5.2 假设 B：checkpoint 可投影成现有 UI 契约

需要验证：

1. 如何从 checkpoint 中拿到最新可用状态
2. 如何从 LangGraph message state 重建当前前端需要的 assistant 分段
3. 如何正确挂接 `tool_calls`
4. 如何处理仅工具调用无文本的 assistant 段
5. 如何处理历史摘要、被中间件压缩后的消息、以及 ToolMessage 的展示策略

如果无法忠实恢复“当前 UI 契约”，就要明确这是产品变更，而不是“内部实现替换”。

### 5.3 假设 C：当前 saver 支持或可替代实现 thread 级清理

需要验证：

1. 当前依赖版本下，是否存在 thread 级删除接口
2. 若不存在，是否能通过“新 thread_id + 旧 thread 屏蔽”实现 clear
3. 若 session_id 与 thread_id 强绑定，clear 后是否仍保留现有 URL 与前端引用
4. delete 的最小正确语义是软删除 metadata 还是必须物理删除 checkpoint

在这件事确认前，`clear` / `delete` 章节不能写死实现方式。

---

## 六、修正后的架构分层

### 6.1 推荐分层

```text
前端
  │
  ├─ /api/chat
  │    ├─ SessionService.touch_session()
  │    ├─ AgentManager.astream()/ainvoke()
  │    └─ 根据 feature flag 决定历史输入来源
  │
  ├─ /api/sessions*
  │    └─ SessionRepository
  │
  ├─ /api/sessions/{id}/history
  │    └─ CheckpointHistoryService
  │
  ├─ /api/sessions/{id}/messages
  │    └─ CheckpointDebugViewService
  │
  └─ /api/tokens/session/{id}
       └─ TokenCountService
```

### 6.2 各层职责

#### A. SessionRepository

职责：

- 创建会话元数据
- 更新标题
- 查询列表
- 标记删除 / 软删除
- touch 最近活跃时间
- 维护 `"default"` 及“未显式创建就开始聊天”的 bootstrap 语义

不负责：

- 持久化 LangGraph messages
- 持久化 task_state
- 直接生成前端历史 DTO

#### B. Checkpoint Thread State

职责：

- 由 LangGraph 持久化线程消息
- 持久化 `task_state`
- 持久化中间件相关状态

不负责：

- 会话标题
- 会话列表
- 默认会话 bootstrap
- mem0 / memory buffer / workspace prompt 文件

#### C. Projection Services

建议拆为两个读取层，而非一个：

1. `CheckpointHistoryService`
   - 面向聊天气泡
   - 输出 `{role, content, tool_calls}` 等 UI DTO

2. `CheckpointDebugViewService`
   - 面向 Raw Messages / 调试视图
   - 明确这是“近似调试视图”还是“真实模型输入视图”

#### D. SessionService

编排层职责：

- create
- touch
- rename
- delete
- clear
- generate-title
- 兼容 feature flag 切换

---

## 七、数据模型修正版

### 7.1 checkpoint 表

继续使用 LangGraph 原生表，不直接改表结构。

### 7.2 新增业务元数据表

建议仍在 `checkpoints.sqlite` 中新增业务表，但只保存元数据：

```sql
CREATE TABLE sessions (
  session_id   TEXT PRIMARY KEY,
  title        TEXT NOT NULL,
  created_at   REAL NOT NULL,
  updated_at   REAL NOT NULL,
  deleted_at   REAL
);

CREATE INDEX idx_sessions_updated_at
ON sessions(updated_at DESC);
```

第一阶段不要把消息缓存、计数缓存、首条消息摘要等字段提前做重。

### 7.3 会话 bootstrap 规则

新增文档必须明确以下规则，否则无法替代当前 JSON 的懒创建行为：

1. `POST /api/sessions` 仍创建显式新会话
2. 若前端直接对不存在的 `session_id` 发送 `/api/chat`
   - 系统要么自动创建 metadata
   - 要么拒绝请求并强制前端先建会话
3. `"default"` 会话是保留概念还是彻底移除，必须先定

如果不先定这条，迁移后前端会在“默认会话 / 删除当前会话 / 新建后立即发送消息”场景出现行为回归。

---

## 八、接口修正版

### 8.1 可以保留路径，但不能承诺“只改内部实现”的接口

以下路径可保留 URL，但部分返回语义很可能变化，需要单独说明：

- `POST /api/chat`
- `GET /api/sessions`
- `POST /api/sessions`
- `PUT /api/sessions/{session_id}`
- `DELETE /api/sessions/{session_id}`
- `GET /api/sessions/{session_id}/history`
- `GET /api/sessions/{session_id}/messages`
- `POST /api/sessions/{session_id}/clear`
- `POST /api/sessions/{session_id}/generate-title`
- `GET /api/tokens/session/{session_id}`

### 8.2 `/api/chat` 修正版

当前：

- 读 JSON 历史
- 调 Agent
- 结束后写 JSON

目标：

- 由 feature flag 控制历史输入来源
- 无论是否切换消息源，都应先 touch 会话 metadata
- 首轮标题生成逻辑不能依赖“JSON 一定已经存在”
- 流式中断时是否保留部分内容，必须单独定义

### 8.3 `/history` 修正版

目标不是一句“从 checkpoint 提取消息”，而是：

1. 定义 UI 需要的精确 DTO
2. 定义 assistant 分段恢复规则
3. 定义 `tool_calls` 归并规则
4. 明确摘要消息是否直接暴露
5. 验证与前端当前气泡恢复逻辑一致

### 8.4 `/messages` 修正版

当前 `/messages` 返回的是：

```text
build_system_prompt() + JSON messages
```

这其实只是一个“近似调试视图”，不是 Agent 本轮真实输入，因为真实输入还可能额外插入：

- Zone 3 dynamic system message
- task_state 注入
- unified memory 检索结果
- tool reminder

因此迁移后必须先选一个明确语义：

#### 方案 A：继续提供“近似调试视图”

优点：

- 成本低
- 前端改动小

缺点：

- 不是 agent 的真实输入

#### 方案 B：提供“真实执行载荷视图”

优点：

- 更真实

缺点：

- 需要把动态注入内容一起建模
- 与当前前端展示差异更大

推荐先落地方案 A，并在接口注释中明确它是 debug approximation。

### 8.5 `/clear` 修正版

在未验证 thread 删除能力前，推荐只定义目标语义，不写死实现。

目标语义：

1. 用户视角下，对话历史被清空
2. 后续对话不受旧 `task_state` / 旧摘要 / 旧 thread message 影响
3. 会话标题和 metadata 可保留

候选实现：

- 真正删除 thread checkpoint
- 标记旧 thread 废弃并切换到新 thread
- 保留 session_id，但额外引入内部 thread_revision

在未验证前，不建议继续坚持“thread_id 必须永远等于 session_id”这一条。

### 8.6 `/delete` 修正版

推荐分层定义：

1. 产品语义：会话从列表消失，历史不可再访问
2. 元数据语义：metadata 被删除或软删除
3. 存储语义：checkpoint 物理删除可延后为后台 GC

这样即使当前 saver 不支持线程级物理删除，也能先保证产品语义正确。

---

## 九、推荐实施顺序 v2

### Phase 0：验证期

先做 spike，不改线上语义。

工作项：

1. 验证 `thread_id` 下的 checkpoint 消息恢复行为
2. 验证是否存在 thread 级历史读取与清理能力
3. 做一个最小 `checkpoint -> history DTO` 原型
4. 明确 `"default"` 会话与懒创建语义

验收：

- 形成可执行结论，而不是继续靠推测写设计

### Phase 1：元数据抽离

目标：

- 先把会话列表、标题、更新时间从 JSON 中抽离

工作项：

1. 新增 `sessions` 表
2. 新增 `SessionRepository`
3. `create/list/rename/touch/delete` 切到 repository
4. 保留 JSON 消息读写不动

验收：

- 会话 Recent 列表不再依赖扫描 `sessions/*.json`
- 标题与更新时间不再依赖 JSON 文件时间戳

### Phase 2：历史读取实验迁移

目标：

- 并行提供 checkpoint projection 读取链路，但默认不切主

工作项：

1. 新增 `CheckpointHistoryService`
2. 增加 feature flag，例如：

```text
features:
  checkpoint_history_read: false
  checkpoint_agent_input: false
  session_json_write_enabled: true
```

3. 在灰度模式下让 `/history`、`/messages` 可切到 checkpoint projection

验收：

- 对比 JSON 与 checkpoint 投影结果
- 找出 assistant 分段、tool_calls、摘要消息的差异

### Phase 3：Agent 输入迁移

前提：

- Phase 0 证明 checkpoint 消息恢复语义可靠

工作项：

1. 引入 `checkpoint_agent_input` flag
2. 在 flag 打开时不再从 JSON 读取 Agent 历史
3. 保留 JSON 写入，便于回滚和结果比对

验收：

- 多轮上下文正常
- task_state 恢复正常
- SummarizationMiddleware 正常
- 无明显历史重复注入

### Phase 4：停止 JSON 消息双写

前提：

- `/history` 与 `/messages` 已稳定由 projection 提供
- Agent 输入也已稳定切到 checkpoint

工作项：

1. 关闭 `session_json_write_enabled`
2. 停止 `save_message()` 调用
3. 保留只读兼容或迁移脚本

验收：

- 新会话不再生成新的 JSON 消息文件
- 前端行为无明显回归

### Phase 5：清理兼容逻辑

工作项：

1. 删除 `load_session_for_agent()` 运行时依赖
2. 删除消息相关 JSON 读写逻辑
3. 重新评估 `compress.py` 与 `compressed_context`
4. 明确 Raw Messages 的长期定义

注意：

- `compress.py` 不能被默认视为“无影响尾部清理项”
- 只有在 checkpoint projection 能替代 `compressed_context` 作用后，才能清理该逻辑

---

## 十、测试修正版

### 10.1 必须新增的验证项

相比上一版，以下用例不是“建议”，而是迁移前必须补上的证据：

1. **checkpoint message recovery**
   - 不传历史，只传当前 user message
   - 验证多轮上下文是否延续

2. **history projection fidelity**
   - checkpoint 投影结果与当前 `/history` JSON 结果逐项对比
   - 特别关注 assistant 分段与 `tool_calls`

3. **clear semantics**
   - clear 后前端为空
   - 后续对话不再受旧 task_state / 旧摘要 / 旧消息影响

4. **delete semantics**
   - delete 后列表消失
   - 历史不可访问
   - checkpoint 是否物理删除可分开断言

5. **stream interruption persistence**
   - 当前 JSON 会保存部分回答
   - 迁移后是否仍保留该能力必须明确定义并测试

6. **default/bootstrap behavior**
   - 不显式建会话直接开聊
   - 删除当前会话后继续聊天
   - 新建会话后立即生成标题

### 10.2 回归关注点

1. `generate-title` 在无 JSON 消息源后如何取首轮消息
2. 非流式 `ainvoke()` 与流式 `astream()` 的一致性
3. `tokens/session/{id}` 是否仍统计到用户真正看到的历史
4. Raw Messages 是否继续作为“近似调试视图”

---

## 十一、风险边界

### 11.1 当前最大风险不是“删不掉 session_manager”

最大风险实际上是：

> **在没有验证 checkpoint 消息恢复与历史投影能力之前，过早把 JSON 从运行主链路里移除。**

### 11.2 当前最容易低估的三个复杂点

1. assistant 分段恢复
2. `tool_calls` 挂接正确性
3. `clear/delete/default` 的产品语义与线程存储语义不一致

### 11.3 当前不建议承诺的能力

在做完 spike 前，不建议在设计稿里承诺：

- thread 级物理删除一定可做
- 现有前端历史展示可以零改动迁移
- 中断时半截回复一定可继续保留
- `thread_id = session_id` 在 clear 之后仍能无痛成立

---

## 十二、最终修正版结论

当前项目中，**LangGraph checkpointer 可以明确替代 `session_manager` 的一部分职责**，但必须把边界说准：

### 可以逐步迁移给 checkpointer 的

- LangGraph 线程消息状态
- `task_state`
- 中间件相关状态
- Agent 续聊所需的线程级上下文（前提是先验证）

### 应由应用层单独保留的

- 会话元数据
- 会话列表与标题
- clear/delete 的产品语义
- default/bootstrap 规则
- 前端历史 / raw messages / token 统计所需的投影与调试视图

### 不应被错误归入 checkpointer 的

- workspace prompt 文件
- unified memory 检索结果
- mem0 存储
- memory buffer 持久化文件

因此，正确迁移路线不是：

```text
删除 session_manager
→ 全部交给 checkpointer
```

而是：

```text
先验证 checkpoint 能否接管消息恢复
→ 抽离 session metadata
→ 建立 checkpoint projection
→ 灰度切换历史读取与 Agent 输入
→ 最后停止 JSON 消息双写
```

这才是一条与当前代码状态一致、可分阶段实施、可回滚、且风险表述准确的迁移方案。
