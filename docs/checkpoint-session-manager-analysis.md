# checkpoint vs session_manager 双数据管理分析

> 基于 `backend/graph/session_manager.py`、`backend/graph/agent.py`、`backend/api/chat.py`、`backend/api/sessions.py` 的完整代码分析。

---

## 一、两套系统的完整流程

### 流程 A：session_manager（JSON 文件）

```
前端发消息 → chat.py
  │
  ├─ 读取：session_manager.load_session_for_agent(session_id)
  │       ↓ 从 sessions/{id}.json 加载消息历史
  │       ↓ 合并连续 assistant 消息、注入 compressed_context
  │
  ├─ 传入：history 传给 agent_manager.astream(message, history, session_id)
  │
  ├─ Agent 运行（SSE 流式）
  │
  └─ 写入：SSE done 事件后
          session_manager.save_message(session_id, "user", message)
          session_manager.save_message(session_id, "assistant", content, tool_calls)
                  ↓ 写入 sessions/{id}.json
```

### 流程 B：checkpoint（SQLite）

```
astream() 开始
  │
  ├─ 初始化：await self._ensure_checkpointer()  ← 懒加载 aiosqlite 连接
  │
  ├─ 构建 agent：agent = self._build_agent()
  │       checkpointer 传入 create_agent(checkpointer=self._checkpointer)
  │
  ├─ 读 TaskState：existing_task = await self._read_task_state(agent, thread_config)
  │       ↓ agent.aget_state(config) → 从 SQLite 读取上一次的 state
  │       ↓ snapshot.values.get("task_state")
  │
  ├─ 写 TaskState：await self._write_task_state(agent, thread_config, task_state_dict)
  │       ↓ agent.aupdate_state(config, {"task_state": ...}, as_node="model")
  │       ↓ 写入 SQLite checkpoints 表
  │
  └─ Agent 运行：agent.astream({"messages": messages}, config=thread_config)
          LangGraph 自动在每轮 tool 执行后往 SQLite 写入 checkpoint
          （包含完整 messages + AgentCustomState）
```

---

## 二、各自的数据结构

### session_manager — `sessions/{id}.json`

```json
{
  "title": "帮我修复 bug",
  "created_at": 1713000000,
  "updated_at": 1713000100,
  "messages": [
    {"role": "user", "content": "帮我修复 auth 模块"},
    {"role": "assistant", "content": "我来查看...", "tool_calls": [
      {"tool": "terminal", "input": "find . -name '*auth*'", "output": "auth.py\n..."}
    ]},
    {"role": "assistant", "content": "找到问题了，修复如下..."}
  ],
  "compressed_context": "[以下是之前对话的摘要]\n..."
}
```

- **键**：session_id（文件名）
- **内容**：标题 + 时间戳 + 消息数组 + 压缩上下文
- **格式**：纯文本 JSON，人可读
- **写入时机**：SSE done 事件后手动调用 `save_message()`
- **读取时机**：构建 messages 传给 agent，前端获取历史展示

### checkpoint — `checkpoints.sqlite`

```
checkpoints 表:
  thread_id | checkpoint_ns | checkpoint_id | parent_id | data(msgpack)
                                                            ↓
                                                        AgentState {
                                                          messages: [
                                                            HumanMessage, AIMessage,
                                                            ToolMessage, ...
                                                          ],
                                                          task_state: {
                                                            session_id, goal,
                                                            steps[], artifacts[],
                                                            decisions[], blockers[]
                                                          }
                                                        }

writes 表:
  thread_id | checkpoint_ns | checkpoint_id | task_id | idx | data(msgpack)
                                                              ↓
                                                          增量更新数据
```

- **键**：thread_id（= session_id）+ checkpoint_id（自增版本号）
- **内容**：LangGraph Agent 的完整 state（messages + task_state）
- **格式**：msgpack 二进制编码，不可直接阅读
- **写入时机**：agent.astream 每轮自动写入
- **读取时机**：`_read_task_state()`、LangGraph 内部中间件

---

## 三、功能重叠分析

| 能力 | session_manager | checkpoint | 重叠？ |
|------|----------------|------------|--------|
| **存储对话消息** | `save_message()` 追加写入 JSON | LangGraph 每轮自动写入 SQLite | **重叠** |
| **读取对话历史** | `load_session_for_agent()` → 传给 agent | `agent.aget_state()` → 可读 messages | **重叠** |
| **标识会话** | session_id = JSON 文件名 | thread_id = session_id | **重叠** |
| **会话列表** | `list_sessions()` 扫描 JSON 文件 | 无（SQLite 无此能力） | 不重叠 |
| **标题管理** | `rename_session()` / `update_title()` | 无 | 不重叠 |
| **压缩归档** | `compress_history()` + `compressed_context` | 无（由 SummarizationMiddleware 接管） | 互补 |
| **TaskState 持久化** | 无 | `_read/write_task_state()` | 不重叠 |
| **前端历史展示** | `GET /sessions/{id}/history` | 无 | 不重叠 |
| **Agent 中间件状态** | 无 | SummarizationMiddleware 等中间件自动持久化 | 不重叠 |

### 关键重叠：消息双写

同一轮对话中：
1. `agent.astream()` 执行时，LangGraph **自动**将 messages 写入 SQLite（每轮 tool 执行后都写一次 checkpoint）
2. SSE done 事件后，`chat.py` 又调用 `session_manager.save_message()` 将**同一份对话**写入 JSON

但两者的消费方完全不同：

```
session_manager 的消费者：
  ├── chat.py          → 加载历史传给 agent（agent 的输入）
  ├── sessions.py      → 前端会话列表、标题管理、历史展示
  └── compress.py      → 压缩归档（已废弃，由中间件接管）

checkpoint 的消费者：
  ├── _read_task_state()  → 恢复 TaskState
  ├── _write_task_state() → 写入 TaskState
  └── LangGraph 内部      → 中间件状态（SummarizationMiddleware 等）
```

**重要发现**：agent 的消息输入（`_build_messages()`）用的是 **session_manager 的数据**，不是 checkpoint 的。checkpoint 中的 messages 实际上**没有被读取回传给 agent**——它在写入后只用于 TaskState 的读写，以及 LangGraph 内部的状态管理。

---

## 四、为什么必须引入 checkpoint？

### 核心结论：checkpoint 不是为了替代 session_manager，而是 LangGraph 框架的要求。

#### 原因 1：`state_schema` 的持久化依赖 checkpointer

```python
agent = create_agent(
    model=self._llm,
    tools=self._tools,
    state_schema=AgentCustomState,   # ← 定义了 task_state 字段
    checkpointer=self._checkpointer,  # ← task_state 的读写依赖这个
)
```

`AgentCustomState` 中定义的 `task_state` 字段通过 `agent.aget_state()` / `agent.aupdate_state()` 读写，这两个方法**需要 checkpointer 才能工作**。没有 checkpointer，`aget_state()` 返回空值，TaskState 无法跨请求持久化。

**session_manager 能替代吗？** 理论上可以——把 TaskState 存到 JSON 文件里。但需要：
- 手动实现 `aget_state()` / `aupdate_state()` 的等价逻辑
- 手动管理 TaskState 的序列化/反序列化
- 手动处理并发写入保护

用 checkpoint 的成本：加一个 SQLite 文件。
不用的成本：手写一套持久化框架。明显不值得。

#### 原因 2：SummarizationMiddleware 的状态持久化

`SummarizationMiddleware` 在摘要生成后会修改消息列表（`RemoveMessage` + 新摘要消息）。这些修改通过 LangGraph 的 checkpoint 自动持久化。下一轮对话时，中间件能看到上一轮的摘要结果。

**session_manager 能替代吗？** 不能。中间件是 LangGraph 框架内部的组件，状态管理完全依赖 checkpointer，不经过 session_manager。

#### 原因 3：LangGraph 的 `agent.astream()` 自动写入

调用 `agent.astream({"messages": messages}, config=thread_config)` 时，LangGraph 在每轮 tool 执行后自动创建 checkpoint。这是框架行为，无法关闭。

**session_manager 能替代吗？** 不需要替代——这是 LangGraph 内部的状态快照，用于框架自身的恢复和重放能力。

### session_manager 能独立实现吗？

| 需求 | session_manager 独立实现 | 难度 |
|------|------------------------|------|
| 存储对话消息 | ✓ 已有 | 无 |
| TaskState 跨请求持久化 | 需要新增 JSON 字段 + 手动序列化 | 低 |
| 中间件状态持久化 | 无法实现（框架内部机制） | **不可能** |
| Agent 断点恢复 | 需要手动重建 LangGraph state | 高 |
| 消息格式转换 | 需要手动处理 LangChain Message 类型与 JSON 的互转 | 中 |

**结论**：如果只有 TaskState 需求，session_manager 可以扩展实现。但 SummarizationMiddleware 等中间件的状态持久化是框架级需求，无法绕过 checkpoint。

---

## 五、融合方向

```
当前状态：
  session_manager:  元数据 + 消息读写（前端 + Agent 输入）
  checkpoint:       TaskState + 中间件状态 + 消息自动快照（框架内部）

目标状态：
  session_manager:  元数据管理（标题、列表、删除） + 前端展示
  checkpoint:       Agent 全部状态（消息 + TaskState + 中间件）+ Agent 输入来源
```

具体融合方向：

1. **消除消息双写**：agent 的消息输入改为从 checkpoint 读取（而非 session_manager），`chat.py` 的 `save_message()` 逐步废弃
2. **session_manager 保留 UI 能力**：会话列表、标题管理、删除操作
3. **前端历史展示迁移**：从 checkpoint 读取消息（需新增 API 从 SQLite 提取）
4. **压缩归档统一**：`compress_history()` 废弃，由 SummarizationMiddleware 完全接管

```
                    session_manager              checkpoint
                    ─────────────────            ────────────────────
当前                元数据 + 消息读写             TaskState + 框架快照
                    （前端 + Agent 输入）          （Agent 内部）

目标                仅元数据管理                  Agent 全部状态
                    （标题、列表、删除）            （消息 + TaskState + 中间件）
                    前端展示辅助                   Agent 输入来源
```
