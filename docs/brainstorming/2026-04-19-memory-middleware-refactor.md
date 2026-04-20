# 长期记忆中间件重构方案

> 将散落在 `agent.py` 中的记忆检索、注入、写入逻辑，封装为独立的 `MemoryMiddleware` 中间件，彻底解耦 Agent 核心流程与记忆管理。

---

## 一、现状问题

### 1.1 记忆逻辑散落在 astream/ainvoke 中

记忆操作分布在 `agent.py` 的 6 个位置，且 `astream()` 和 `ainvoke()` 中存在完全重复的实现：

| 位置 | 行号 | 职责 |
|------|------|------|
| `astream()` | 487-501 | 记忆检索 |
| `astream()` | 569-577 | Zone 3 记忆注入（SystemMessage） |
| `astream()` | 601-607 | 记忆写入调度 |
| `astream()` | 654-684 | `_schedule_mem0_write()` 方法定义 |
| `ainvoke()` | 695-701 | 记忆检索（与 astream 重复） |
| `ainvoke()` | 751-756 | Zone 3 注入（与 astream 重复） |

**问题**：
- 记忆的完整生命周期（检索→注入→写入）横跨多个方法，无统一管理入口
- `astream()` 和 `ainvoke()` 重复实现相同逻辑（~70 行），维护成本高
- 修改记忆逻辑需同时修改两处，容易遗漏

### 1.2 与中间件链风格不一致

当前 4 层中间件链（ToolOutputBudget → Summarization → ToolFilter → ToolCallLimit）职责清晰，但记忆管理完全游离在中间件体系之外。

---

## 二、设计目标

| 目标 | 说明 |
|------|------|
| **封装** | 记忆的检索、注入、写入全部收敛到单一中间件类 |
| **解耦** | agent.py 不再直接调用 UnifiedMemoryRetriever / MemoryBuffer / Mem0Manager |
| **去重** | astream/ainvoke 中的重复代码全部消除 |
| **兼容** | SSE 事件格式不变，前端无需改动，checkpoint 向下兼容 |
| **可回退** | 注入方式通过配置切换，支持从 system_prompt 模式回退到 SystemMessage 模式 |
| **状态持久** | 记忆上下文通过 graph state 传递，HITL resume 不丢失 |

---

## 三、方案选择

### 方案 A：单一 MemoryMiddleware 类（已选）

将检索、注入、写入全部封装在一个 `MemoryMiddleware` 类中，通过不同 hook 分管：

| Hook | 职责 | 时机 |
|------|------|------|
| `abefore_agent` | 记忆检索 + SSE 事件 | 每轮对话开始前 |
| `awrap_model_call` | 注入记忆到 system_message | 每次模型调用前 |
| `aafter_agent` | 记忆写入 | 每轮对话结束后 |

**选择理由**：
1. 记忆生命周期闭环在一个类中，职责清晰
2. 与项目现有 4 层中间件链风格一致
3. 改动文件少（1 个新文件 + 1 处注册 + 配置）
4. 每条 hook 方法独立，单类 ~180 行可控

### 备选方案（未选）

| 方案 | 描述 | 未选理由 |
|------|------|---------|
| 方案 B：读写分离双中间件 | 拆为 RetrievalMiddleware + WriteMiddleware | 当前读写逻辑简单，拆分过度 |
| 方案 C：函数式 hook | 不继承 AgentMiddleware，用装饰器 | 与现有中间件风格不一致 |

---

## 四、LangChain 框架 API 验证

### 4.1 `dynamic_prompt` 机制

源码路径：`langchain/agents/middleware/types.py:1590-1733`

`dynamic_prompt` 是**装饰器 API**，不是类方法。其内部实现为 `wrap_model_call`/`awrap_model_call` hook：

```python
# dynamic_prompt 装饰器的核心实现（types.py:1669-1691）
async def async_wrapped(_self, request, handler):
    prompt = await func(request)
    if isinstance(prompt, SystemMessage):
        request = request.override(system_message=prompt)
    else:
        request = request.override(system_message=SystemMessage(content=prompt))
    return await handler(request)
```

**关键发现**：`dynamic_prompt` 的底层就是 `awrap_model_call` + `request.override(system_message=...)`。我们直接在类中实现 `awrap_model_call`，效果完全一致。

### 4.2 `AgentMiddleware` 可用 Hook

| Hook | 签名 | 执行时机 |
|------|------|---------|
| `abefore_agent` | `(state, runtime) -> dict \| None` | 每轮对话开始前（一次） |
| `abefore_model` | `(state, runtime) -> dict \| None` | 每次模型调用前 |
| `awrap_model_call` | `(request, handler) -> ModelResponse` | 拦截模型调用，可修改 request |
| `aafter_model` | `(state, runtime) -> dict \| None` | 每次模型调用后 |
| `aafter_agent` | `(state, runtime) -> dict \| None` | 每轮对话结束后（一次） |

**同类多 hook 可行性验证**：`ShellToolMiddleware` 同时实现了 `abefore_agent` + `aafter_agent`（shell_tool.py:643-673），factory.py 独立收集每个 hook 并注册为独立 graph node。

### 4.3 `runtime.stream_writer()` 与 SSE 事件

**已验证可行**。源码链路：

1. `Runtime` 类有 `stream_writer` 字段（`langgraph/runtime.py:109`），默认为 `_no_op_stream_writer`
2. `Pregel.astream()` 中，当 `stream_mode` 包含 `"custom"` 时，创建真实的 `stream_writer`（Pregel.astream:175-189），通过 `Runtime` 传递给中间件 hook
3. `RunnableCallable.ainvoke()` 从 config 中提取 `runtime` 并作为 kwargs 传给中间件函数

**验证方法**：Pregel.astream 源码（175-189 行）中 `"custom"` 在 `stream_modes` 时创建真实 `stream_writer`，写入 `(namespace, "custom", data)` 格式的 tuple 到 async stream。

### 4.4 中间件 state_schema 合并机制

每个中间件可定义独立的 `state_schema`（TypedDict），`create_agent()` 通过 `_resolve_schema()` 合并所有 schema（factory.py:1008-1013）。中间件自定义字段自动成为 graph state 的一部分，随 checkpoint 持久化。

---

## 五、详细设计

### 5.1 类结构与 state schema

```python
from typing import NotRequired
from langchain.agents.middleware.types import AgentMiddleware, AgentState


class MemoryMiddlewareState(AgentState):
    """MemoryMiddleware 专用 state 扩展。

    memory_context 通过 graph state 传递，确保 HITL resume 场景不丢失。
    abefore_agent 写入，awrap_model_call 读取。
    """
    memory_context: NotRequired[str]


class MemoryMiddleware(AgentMiddleware):
    """长期记忆中间件：检索 + 注入 + 写入全生命周期管理。"""

    state_schema = MemoryMiddlewareState

    def __init__(self, base_dir: Path, config: dict | None = None,
                 write_executor: ThreadPoolExecutor | None = None):
        self._base_dir = base_dir
        self._config = config or {}
        self._injection_mode = self._config.get("injection_mode", "system_prompt")

        # 延迟初始化的组件
        self._retriever: UnifiedMemoryRetriever | None = None

        # 外部注入的线程池（由 AgentManager 管理，避免 per-request 泄漏）
        self._write_executor = write_executor

    async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
        """Hook 1：每轮对话开始前 — 记忆检索 + SSE 事件发送。"""

    async def awrap_model_call(self, request, handler):
        """Hook 2：拦截模型调用 — 注入记忆到 system_message（主方案）。"""

    async def aafter_agent(self, state, runtime) -> dict[str, Any] | None:
        """Hook 3：每轮对话结束后 — 记忆写入缓冲区。"""
```

**关键设计决策**：

| 决策 | 原因 |
|------|------|
| `memory_context` 存储在 graph state 而非实例变量 | graph state 随 checkpoint 持久化，HITL resume 时不丢失；实例变量随 `_build_agent()` 每次 request 重建 |
| `write_executor` 由外部注入 | `_build_agent()` 每次 request 调用，中间件实例 per-request，自行创建 ThreadPoolExecutor 会泄漏 |
| `state_schema = MemoryMiddlewareState` | 通过 factory.py 的 schema 合并机制，`memory_context` 自动成为 graph state 字段 |

### 5.2 `abefore_agent` — 记忆检索

```python
async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
    """每轮对话开始前：检索相关记忆，写入 state。"""
    features = get_features_config()
    if not features.get("unified_memory", True):
        return None

    messages = state.get("messages", [])
    user_msg = self._extract_latest_user_message(messages)
    if not user_msg:
        return None

    # 延迟初始化 retriever
    if self._retriever is None:
        from graph.unified_memory import get_unified_retriever
        self._retriever = get_unified_retriever(self._base_dir)

    # 异步检索
    results = await self._retriever.retrieve_async(user_msg)
    memory_context = ""
    if results:
        # SSE 事件：通知前端检索结果
        runtime.stream_writer({
            "type": "retrieval",
            "query": user_msg,
            "results": results,
        })
        memory_context = self._retriever.format_for_injection(results)

    # 回退模式：通过 SystemMessage 注入（不经过 awrap_model_call）
    if self._injection_mode == "system_message" and memory_context:
        from langchain_core.messages import SystemMessage
        memory_msg = SystemMessage(
            content=f"<!-- Zone 3: Dynamic -->\n{memory_context}"
        )
        if len(messages) > 1:
            return {
                "memory_context": memory_context,
                "messages": messages[:-1] + [memory_msg] + [messages[-1]],
            }

    # 写入 state（供 awrap_model_call 读取）
    return {"memory_context": memory_context}
```

**设计要点**：
- 延迟初始化 `UnifiedMemoryRetriever`（避免构造时加载重依赖）
- `runtime.stream_writer()` 发送 custom stream 事件（已验证可行，见 4.3 节）
- 检索结果写入 `state["memory_context"]`（非实例变量），checkpoint 自动持久化
- 回退模式在 `abefore_agent` 中直接注入 SystemMessage

### 5.3 `awrap_model_call` — 记忆注入（主方案）

```python
async def awrap_model_call(self, request, handler):
    """拦截模型调用：从 state 读取记忆上下文，追加到 system_message。"""
    if self._injection_mode != "system_prompt":
        return await handler(request)

    # 从 state 读取 memory_context（而非实例变量）
    memory_context = request.state.get("memory_context", "")
    if not memory_context:
        return await handler(request)

    # 拼接记忆上下文到 system_message
    base_content = request.system_message.content if request.system_message else ""
    memory_section = f"\n\n<!-- Zone 3: Dynamic -->\n{memory_context}"
    enhanced = base_content + memory_section

    request = request.override(
        system_message=SystemMessage(content=enhanced)
    )
    return await handler(request)
```

**为什么用 `awrap_model_call` 而非 `abefore_model`**：
- `abefore_model` 只能修改 state（messages），无法修改 `system_message`
- `awrap_model_call` 可通过 `request.override(system_message=...)` 直接修改系统提示
- 这与 `dynamic_prompt` 装饰器的实现机制完全一致

**state-based 方案 vs instance-variable 方案**：

| 维度 | 实例变量 `_pending_context` | state `memory_context` |
|------|---------------------------|----------------------|
| HITL resume | 丢失（新中间件实例） | 不丢失（checkpoint 恢复） |
| 多次 model call | 首次消费后清空 | 每次都能读取（更好） |
| 并发安全 | per-request 实例安全 | graph state 天然隔离 |
| 复杂度 | 低 | 略高（需定义 state_schema） |

### 5.4 `aafter_agent` — 记忆写入

```python
async def aafter_agent(self, state, runtime) -> dict[str, Any] | None:
    """每轮对话结束后：将对话写入记忆缓冲区。"""
    mem0_cfg = get_mem0_config()
    if not mem0_cfg.get("enabled") or not mem0_cfg.get("auto_extract"):
        return None

    # 无 write_executor 说明初始化不完整，跳过
    if self._write_executor is None:
        return None

    messages = state.get("messages", [])
    user_msg, assistant_msg = self._extract_last_exchange(messages)
    if not user_msg or not assistant_msg:
        return None

    self._schedule_write(user_msg, assistant_msg, mem0_cfg)
    return None

def _schedule_write(self, user_msg: str, assistant_msg: str, mem0_cfg: dict) -> None:
    """后台线程执行缓冲区写入 + mem0 提取。"""
    base_dir = self._base_dir
    executor = self._write_executor

    def _background():
        try:
            from graph.memory_buffer import get_memory_buffer
            from graph.mem0_manager import get_mem0_manager

            buffer = get_memory_buffer(base_dir)
            buffer.add_turn(user_msg, assistant_msg, "default")

            should_flush = buffer.check_immediate_trigger(user_msg)
            if not should_flush:
                should_flush = buffer.should_flush()

            if should_flush:
                turns = buffer.flush()
                if turns:
                    mgr = get_mem0_manager(base_dir)
                    mgr.batch_add(turns, user_id=mem0_cfg.get("user_id", "default"))
                    print(f"🧠 mem0 后台写入完成（{len(turns)} 轮对话）")
        except Exception as e:
            print(f"⚠️ mem0 后台写入失败: {e}")

    executor.submit(_background)
```

### 5.5 辅助方法

```python
@staticmethod
def _extract_latest_user_message(messages: list) -> str | None:
    """从 messages 中提取最新用户消息文本。"""
    from langchain_core.messages import HumanMessage
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage) and msg.content:
            return msg.content
    return None

@staticmethod
def _extract_last_exchange(messages: list) -> tuple[str | None, str | None]:
    """从 messages 中提取最后一轮用户-助手对话。"""
    from langchain_core.messages import HumanMessage, AIMessage
    user_msg = None
    assistant_msg = None
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content and assistant_msg is None:
            assistant_msg = msg.content
        elif isinstance(msg, HumanMessage) and msg.content:
            user_msg = msg.content
            break
    return user_msg, assistant_msg
```

---

## 六、中间件链注册

### 6.1 在中间件链中的位置

```
第 1 层：ToolOutputBudgetMiddleware      — 工具输出压缩
第 2 层：SummarizationMiddleware         — 自动摘要
第 3 层：ContextAwareToolFilter          — 运行时工具过滤
第 4 层：ToolCallLimitMiddleware         — 工具调用限流
第 5 层：MemoryMiddleware                — 长期记忆（新增）
第 6 层：FilesystemFileSearchMiddleware  — 文件搜索工具
```

放在第 5 层，理由：
- 在摘要之后（SummarizationMiddleware 不需要处理记忆检索逻辑）
- 在工具过滤之前完成记忆注入（检索结果可能影响工具选择）

### 6.2 注册代码

```python
# agent.py _build_middleware() 新增
mem_cfg = mw_cfg.get("memory_middleware", {})
if mem_cfg.get("enabled", True) and self._base_dir:
    from graph.memory_middleware import MemoryMiddleware
    middleware.append(
        MemoryMiddleware(
            base_dir=self._base_dir,
            config=mem_cfg,
            write_executor=self._write_executor,  # 共享 AgentManager 的线程池
        )
    )
```

**注意**：`self._write_executor` 由 `AgentManager.__init__` 创建（agent.py:24），整个进程生命周期只创建一次。中间件通过参数引用，不自行创建。

---

## 七、SSE 事件流适配

### 7.1 问题

当前 `astream()` 的 retrieval 事件通过 `yield {"type": "retrieval", ...}` 在 `agent.astream()` 之前发出。中间件运行在 LangGraph agent 执行循环内部，`runtime.stream_writer()` 需要 `stream_mode=["custom"]`。

当前 `_stream_events` 使用 `stream_mode=["messages", "updates"]`，不支持 custom 事件。

### 7.2 解决方案（已验证可行）

扩展 `_stream_events` 支持 custom stream mode，改动 2 处：

**改动 1**：添加 stream mode

```python
# agent.py _stream_events 第 390-394 行
async for event in agent.astream(
    stream_input,
    config=thread_config,
    stream_mode=["messages", "updates", "custom"],  # 新增 "custom"
):
```

**改动 2**：添加 custom 事件透传

```python
# 在 elif mode == "updates": 分支之后新增
elif mode == "custom":
    yield data  # 透传自定义事件（如记忆检索结果）
```

**可行性验证**：
- Pregel.astream 源码（175-189 行）确认 `"custom"` 在 stream_modes 时创建真实 `stream_writer`
- `stream_writer` 通过 `Runtime` 对象传递给中间件 hook（RunnableCallable.ainvoke 中从 config 提取 runtime）
- 现有 `_stream_events` 已处理 tuple 格式的 mode 分支，新增 `"custom"` 不影响现有逻辑

### 7.3 修正后的完整事件流

```
astream() → agent.astream(stream_mode=["messages","updates","custom"])
  │
  ├─ MemoryMiddleware.abefore_agent:
  │   ├─ retrieve_async(message)
  │   ├─ runtime.stream_writer({"type":"retrieval", ...})  ← 通过 custom mode 传递
  │   └─ 返回 {"memory_context": "..."} → 写入 graph state
  │
  ├─ MemoryMiddleware.awrap_model_call:
  │   ├─ 读取 request.state["memory_context"]
  │   └─ request.override(system_message=...)  ← 注入记忆到系统提示
  │
  ├─ _stream_events 捕获 mode=="custom" → yield 给外层
  │
  └─ MemoryMiddleware.aafter_agent:
      └─ _schedule_write(...)  ← 后台写入记忆
```

---

## 八、agent.py 清理

### 8.1 astream() 清理点

| 原始位置 | 原始逻辑 | 处理方式 |
|---------|---------|---------|
| 487-501 行 | 记忆检索（13 行） | **删除** |
| 488 行 | `rag_context = ""` 变量声明 | **删除** |
| 569-572 行 | `build_dynamic_prefix` 中 `memory_context=rag_context` | 改为 `memory_context=""` |
| 601-607 行 | `_schedule_mem0_write` 调用（7 行） | **删除** |
| 654-684 行 | `_schedule_mem0_write` 方法定义（31 行） | **删除** |

### 8.2 ainvoke() 清理点

与 astream() 完全对称：
- 695-701 行（记忆检索，7 行）→ **删除**
- 694 行 `rag_context` 变量 → **删除**
- 751-756 行（Zone 3 注入中 `memory_context=rag_context`）→ 改为 `memory_context=""`
- 记忆写入调用 → **删除**（ainvoke 当前也没有 memory write，与 astream 不同）

### 8.3 其他清理

| 位置 | 改动 |
|------|------|
| `AgentManager.__init__` | 保留 `self._write_executor`（供中间件引用），不删除 |
| `_stream_events` | stream_mode 添加 `"custom"`，新增 custom 透传 |

### 8.4 保留部分

`astream()` 中 TaskState 相关逻辑（503-561 行）**完全不动**。Zone 3 注入仅保留 TaskState：

```python
# 清理后的 Zone 3 注入
has_active_steps = _has_in_progress_steps(task_state_dict)
dynamic_prefix = build_dynamic_prefix(
    memory_context="",  # 记忆已由中间件通过 system_message 注入，此处不再传递
    task_state=task_state_md,
    has_active_steps=has_active_steps,
)
if dynamic_prefix:
    messages.insert(len(messages) - 1, SystemMessage(content=dynamic_prefix))
```

**Zone 3 分裂说明**：重构后 Zone 3 的两部分通过不同路径注入：
- **记忆上下文**：`awrap_model_call` → 追加到 `system_message`（Zone 1+2 的一部分）
- **TaskState**：`astream()` 中的 SystemMessage → 插入 messages 列表

两部分功能独立、路径清晰，`build_dynamic_prefix()` 仍可复用（`memory_context=""` 时只输出 TaskState 部分）。

---

## 九、配置变更

### 9.1 config.json

```json
"middleware": {
    "tool_output_budget": { ... },
    "summarization": { ... },
    "tool_filter": { ... },
    "tool_call_limit": { ... },
    "memory_middleware": {
        "enabled": true,
        "injection_mode": "system_prompt"
    }
}
```

### 9.2 字段说明

| 字段 | 类型 | 默认值 | 说明 |
|------|------|-------|------|
| `enabled` | bool | true | 是否启用记忆中间件 |
| `injection_mode` | string | `"system_prompt"` | `"system_prompt"`（主方案，修改 system_message）或 `"system_message"`（回退，插入 SystemMessage） |

---

## 十、边界场景分析

### 10.1 HITL（Human-in-the-loop）resume

**场景**：Agent 调用工具 → HITL interrupt → 用户审批 → `resume_stream()` 恢复

**中间件行为**：
- `resume_stream()` 调用 `_build_agent()` 创建新中间件实例
- LangGraph 从 checkpoint 恢复 state（包含 `memory_context`）
- `abefore_agent` **不执行**（resume 从 tools 节点继续，不经过 entry node）
- `awrap_model_call` 从 `request.state["memory_context"]` 读取 → **记忆上下文不丢失**
- `aafter_agent` 在恢复后的完整对话结束后执行 → 写入正常

**验证依据**：`abefore_agent` 是 graph entry node，resume 时 LangGraph 跳过已完成的节点，直接从 interrupt 点继续。

### 10.2 resume_stream 中的重复检索

`resume_stream()` (agent.py:609) 创建新 agent 后调用 `_stream_events()`。由于 `abefore_agent` 是 entry node，resume 时**不执行**，因此**不会触发重复检索**。`memory_context` 从 checkpoint 恢复即可。

### 10.3 ainvoke() 路径

`ainvoke()` 使用 `agent.ainvoke()`（agent.py:760），非流式调用。

- `abefore_agent` 正常执行 → 检索 → 写入 state
- `awrap_model_call` 正常执行 → 从 state 读取 → 注入
- `runtime.stream_writer()` 为 no-op（非流式模式），retrieval 事件不发出 — **行为与当前一致**（ainvoke 本身不返回检索事件）
- `aafter_agent` 正常执行 → 写入

### 10.4 多次 model call 场景

Agent 在多工具调用场景中会多次调用 model。每次 `awrap_model_call` 都从 `request.state["memory_context"]` 读取，记忆上下文在整轮对话中持续可用。这比当前方案（仅首次注入）更好。

---

## 十一、向下兼容与回退策略

### 11.1 injection_mode 回退

```
system_prompt（主方案）
  │
  ├─ 正常工作：记忆追加到 Zone 2 稳定前缀之后
  │
  └─ 框架升级不兼容时
      │
      └─ 改配置 injection_mode="system_message"
          → 回退到 SystemMessage 插入，与当前行为完全一致
```

### 11.2 Checkpoint 兼容

| 模式 | Checkpoint 影响 |
|------|----------------|
| `system_prompt` | 记忆存在 state["memory_context"] 中，不在 messages 中 |
| `system_message` | 记忆作为 SystemMessage 存入 messages，与当前行为一致 |

旧 checkpoint 无 `memory_context` 字段，`state.get("memory_context", "")` 返回空字符串，自然兼容。

### 11.3 前端兼容

SSE 事件格式完全不变：

```json
{"event": "retrieval", "data": {"query": "...", "results": [...]}}
```

前端 `store.tsx:466-491` 的消费逻辑无需任何修改。

### 11.4 渐进式迁移路径

| 阶段 | 操作 | 验证点 |
|------|------|-------|
| 1 | 新增 `memory_middleware.py`，agent.py 注册但保留旧代码（注释掉） | 编译通过 |
| 2 | 切换 injection_mode 在两种模式间切换，对比行为一致性 | 检索结果、注入位置、写入触发 |
| 3 | 确认无误后删除 agent.py 中被注释的旧代码 | 代码整洁 |
| 4 | 清理 `_schedule_mem0_write` 等残留 | 无遗漏引用 |

---

## 十二、改动范围总览

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `backend/graph/memory_middleware.py` | **新增** | `MemoryMiddleware` 类 + `MemoryMiddlewareState`（~190 行） |
| `backend/graph/agent.py` | 修改 | 删除 ~80 行记忆逻辑，新增 ~12 行注册 + stream_mode 扩展 |
| `backend/config.json` | 修改 | 新增 `memory_middleware` 配置段 |

### 净效果

+190 行（新文件）- 80 行（清理）= **+110 行**，但 agent.py 减少 80 行重复代码，记忆生命周期闭环在独立模块中。

### 不改动的部分

| 组件 | 原因 |
|------|------|
| `unified_memory.py` | 被中间件调用，接口不变 |
| `memory_buffer.py` | 被中间件调用，接口不变 |
| `mem0_manager.py` | 被中间件调用，接口不变 |
| `mem0_tool.py` | Agent 工具，独立于中间件 |
| `prompt_builder.py` | `build_dynamic_prefix` 签名不变（`memory_context=""` 时只输出 TaskState） |
| `tools/__init__.py` | 无变更 |
| `task_state.py` | `AgentCustomState` 不变，MemoryMiddlewareState 由中间件独立定义 |
| 前端 | SSE 事件格式不变 |

---

## 附录 A：审核修正记录

本文档经过代码审核，修正了以下问题：

| 编号 | 严重度 | 问题 | 修正方式 |
|------|-------|------|---------|
| F1 | 致命 | `ThreadPoolExecutor` per-request 泄漏（`_build_agent()` 每次请求调用） | 改为外部注入，由 `AgentManager._write_executor` 管理 |
| S1 | 严重 | `_pending_context` 实例变量在 HITL resume 时丢失 | 改为 graph state 字段 `memory_context`，通过 `MemoryMiddlewareState` 定义 |
| S2 | 严重 | `runtime.stream_writer()` 可用性未实证 | 通过 Pregel.astream 源码验证（175-189 行），确认可行 |
| M1 | 中等 | `resume_stream()` 触发不必要的记忆检索 | 分析确认 resume 时 `abefore_agent` 不执行（entry node 跳过），无此问题 |
| M2 | 中等 | Zone 3 格式分裂未说明 | 新增第八章 8.4 节 Zone 3 分裂说明 |
| M3 | 中等 | `ainvoke()` 路径检索事件行为未说明 | 新增第十章 10.3 节 ainvoke 路径分析 |
