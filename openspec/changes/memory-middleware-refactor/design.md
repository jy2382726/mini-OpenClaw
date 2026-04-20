## Context

`agent.py` 中记忆操作分布在 6 个位置（astream 4 处 + ainvoke 2 处），完整生命周期横跨多个方法：
- 记忆检索：`astream:486-501`、`ainvoke:693-701`（重复）
- Zone 3 注入：`astream:569-577`、`ainvoke:750-756`（重复）
- 记忆写入：`astream:601-607`（调用）+ `astream:654-684`（方法定义）
- ainvoke 无记忆写入

~70 行重复代码，修改需同时改两处。当前 4 层中间件链风格清晰，但记忆管理完全游离在中间件体系之外。

## Goals / Non-Goals

**Goals:**

- 封装记忆检索、注入、写入到单一 `MemoryMiddleware` 类，通过 3 个 hook 管理完整生命周期
- 消除 `astream()`/`ainvoke()` 中的 ~70 行重复代码
- `memory_context` 通过 graph state 传递，checkpoint 自动持久化，HITL resume 不丢失
- SSE 事件格式不变，前端无需改动
- 支持双注入模式（system_prompt 主方案 / system_message 回退）

**Non-Goals:**

- 不修改 `UnifiedMemoryRetriever`、`MemoryBuffer`、`Mem0Manager` 接口
- 不修改 `prompt_builder.py`（`build_dynamic_prefix` 仍可复用）
- 不修改 `AgentCustomState`（中间件定义独立 `MemoryMiddlewareState`）
- 不修改前端代码
- 不改变记忆写入的后台线程模型（仍使用共享 `ThreadPoolExecutor`）

## Decisions

### 决策 1：单一中间件类 + 三 hook

**选择**：一个 `MemoryMiddleware` 类实现 `abefore_agent` + `awrap_model_call` + `aafter_agent`

**备选**：读写分离双中间件（RetrievalMiddleware + WriteMiddleware）

**理由**：
- 记忆生命周期闭环在一个类中，职责清晰
- 当前读写逻辑简单，拆分过度
- 与 ShellToolMiddleware（同时实现 abefore_agent + aafter_agent）风格一致

### 决策 2：state 传递 memory_context（非实例变量）

**选择**：`memory_context` 通过 `MemoryMiddlewareState` 定义为 graph state 字段

**备选**：实例变量 `_pending_context`

**理由**：
- HITL resume 创建新中间件实例，实例变量丢失；graph state 通过 checkpoint 恢复不丢失
- `_build_agent()` 每次 request 调用，实例变量 per-request 生命周期与记忆检索-注入跨步不一致
- factory.py 的 `_resolve_schema` 自动合并中间件 state_schema

### 决策 3：awrap_model_call 注入（非 abefore_model）

**选择**：通过 `awrap_model_call` + `request.override(system_message=...)` 注入记忆

**备选**：`abefore_model` 修改 messages

**理由**：
- `abefore_model` 只能修改 state（messages），无法修改 system_message
- `awrap_model_call` 可通过 `request.override` 直接修改系统提示
- 与 `dynamic_prompt` 装饰器的实现机制完全一致（已验证 types.py:1669-1691）
- `ModelRequest.state` 属性已确认存在（types.py:102），可从 state 读取 memory_context

### 决策 4：write_executor 外部注入

**选择**：`ThreadPoolExecutor` 由 `AgentManager.__init__` 创建，通过参数注入中间件

**备选**：中间件自行创建线程池

**理由**：
- `_build_agent()` 每次 request 调用，中间件实例 per-request
- 自行创建 ThreadPoolExecutor 会泄漏（无法在 per-request 实例中正确清理）
- 外部注入共享线程池，生命周期由 AgentManager 管理

### 决策 5：custom stream mode 扩展

**选择**：在 `_stream_events` 的 `stream_mode` 中新增 `"custom"`，透传 `runtime.stream_writer()` 事件

**备选**：维持当前 stream_mode，检索事件在中间件外发送

**理由**：
- `runtime.stream_writer()` 仅在 `stream_mode` 包含 `"custom"` 时创建真实 writer（已验证 Pregel.astream:175-189）
- 中间件内通过 writer 发送检索事件，封装更彻底
- 改动量小：stream_mode 加一项 + 新增 elif 分支

### 组件层级图

```
AgentManager._build_agent()
  │
  ├─ middleware: _build_middleware()
  │   ├─ 第 1 层：ToolOutputBudgetMiddleware
  │   ├─ 第 2 层：ContextAwareSummarizationMiddleware
  │   ├─ 第 3 层：ContextAwareToolFilter
  │   ├─ 第 4 层：ToolCallLimitMiddleware
  │   ├─ 第 5 层：MemoryMiddleware  ← 新增
  │   │   ├─ abefore_agent: 检索 → state["memory_context"]
  │   │   ├─ awrap_model_call: state → request.override(system_message)
  │   │   └─ aafter_agent: 后台写入 memory_buffer + mem0
  │   └─ 第 6 层：FilesystemFileSearchMiddleware
  │
  ├─ _stream_events: stream_mode=["messages","updates","custom"]  ← 新增 "custom"
  │
  ├─ astream(): 记忆检索/注入/写入逻辑 → 删除
  └─ ainvoke(): 记忆检索/注入逻辑 → 删除
```

### 事件流

```
astream() → agent.astream(stream_mode=["messages","updates","custom"])
  │
  ├─ MemoryMiddleware.abefore_agent:
  │   ├─ retrieve_async(message)
  │   ├─ runtime.stream_writer({"type":"retrieval",...})  ← custom mode
  │   └─ 返回 {"memory_context": "..."} → 写入 graph state
  │
  ├─ MemoryMiddleware.awrap_model_call:
  │   ├─ request.state["memory_context"]
  │   └─ request.override(system_message=...)  ← 注入记忆到系统提示
  │
  ├─ _stream_events 捕获 mode=="custom" → yield 给外层
  │
  └─ MemoryMiddleware.aafter_agent:
      └─ _schedule_write(...)  ← 后台线程写入
```

## Risks / Trade-offs

- **[HITL resume 时 abefore_agent 不执行]** → resume 从 tools 节点继续，跳过 entry node。`memory_context` 从 checkpoint 恢复，`awrap_model_call` 仍可读取。已通过 ShellToolMiddleware 同类模式验证。
- **[Zone 3 分裂]** → 重构后记忆通过 `system_message` 注入（system_prompt 模式），TaskState 仍通过 SystemMessage 注入 messages。两部分功能独立、路径清晰，`build_dynamic_prefix` 在 `memory_context=""` 时只输出 TaskState 部分。
- **[ainvoke 无 custom stream]** → `agent.ainvoke()` 非流式调用，`runtime.stream_writer()` 为 no-op，retrieval 事件不发出。与当前行为一致（ainvoke 本身不返回检索事件）。
- **[state_schema 合并冲突]** → `MemoryMiddlewareState` 新增 `memory_context` 字段，需确认不与 `AgentCustomState` 现有字段冲突。字段名唯一，风险极低。
- **[injection_mode 双模式维护]** → 支持 `system_prompt` 和 `system_message` 两种注入模式增加了代码复杂度。但回退方案是生产安全的必要保障，模式切换仅一个 if 分支。
