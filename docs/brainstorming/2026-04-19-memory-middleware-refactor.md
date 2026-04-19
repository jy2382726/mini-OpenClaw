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

### 4.3 `runtime.stream_writer()` 与 SSE 事件

`runtime.stream_writer()` 发送自定义事件需要 `stream_mode=["custom"]`。当前 `_stream_events` 使用 `stream_mode=["messages", "updates"]`，需扩展。

---

## 五、详细设计

### 5.1 类结构

```python
class MemoryMiddleware(AgentMiddleware):
    """长期记忆中间件：检索 + 注入 + 写入全生命周期管理。"""

    def __init__(self, base_dir: Path, config: dict | None = None):
        self._base_dir = base_dir
        self._config = config or {}
        self._injection_mode = self._config.get("injection_mode", "system_prompt")
        self._pending_context: str = ""

        self._retriever: UnifiedMemoryRetriever | None = None
        self._write_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mem0_write")

    async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
        """Hook 1：每轮对话开始前 — 记忆检索 + SSE 事件发送。"""

    async def awrap_model_call(self, request, handler):
        """Hook 2：拦截模型调用 — 注入记忆到 system_message（主方案）。"""

    async def aafter_agent(self, state, runtime) -> dict[str, Any] | None:
        """Hook 3：每轮对话结束后 — 记忆写入缓冲区。"""
```

### 5.2 `abefore_agent` — 记忆检索

```python
async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
    """每轮对话开始前：检索相关记忆，暂存结果。"""
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
    if results:
        # SSE 事件：通知前端检索结果
        runtime.stream_writer({
            "type": "retrieval",
            "query": user_msg,
            "results": results,
        })
        self._pending_context = self._retriever.format_for_injection(results)
    else:
        self._pending_context = ""

    # 回退模式：通过 SystemMessage 注入
    if self._injection_mode == "system_message" and self._pending_context:
        from langchain_core.messages import SystemMessage
        memory_msg = SystemMessage(
            content=f"<!-- Zone 3: Dynamic -->\n{self._pending_context}"
        )
        if len(messages) > 1:
            return {"messages": messages[:-1] + [memory_msg] + [messages[-1]]}

    return None
```

**设计要点**：
- 延迟初始化 `UnifiedMemoryRetriever`（避免构造时加载重依赖）
- `runtime.stream_writer()` 替代当前的 `yield {"type": "retrieval", ...}`
- 回退模式在 `abefore_agent` 中直接注入 SystemMessage
- 检索结果暂存到 `self._pending_context`，供 `awrap_model_call` 消费

### 5.3 `awrap_model_call` — 记忆注入（主方案）

```python
async def awrap_model_call(self, request, handler):
    """拦截模型调用：将记忆上下文追加到 system_message。"""
    if self._injection_mode != "system_prompt" or not self._pending_context:
        return await handler(request)

    # 拼接记忆上下文到 system_message
    base_content = request.system_message.content if request.system_message else ""
    memory_section = f"\n\n<!-- Zone 3: Dynamic -->\n{self._pending_context}"
    enhanced = base_content + memory_section

    request = request.override(
        system_message=SystemMessage(content=enhanced)
    )

    # 消费后清空
    self._pending_context = ""
    return await handler(request)
```

**为什么用 `awrap_model_call` 而非 `abefore_model`**：
- `abefore_model` 只能修改 state（messages），无法修改 `system_message`
- `awrap_model_call` 可通过 `request.override(system_message=...)` 直接修改系统提示
- 这与 `dynamic_prompt` 装饰器的实现机制完全一致

### 5.4 `aafter_agent` — 记忆写入

```python
async def aafter_agent(self, state, runtime) -> dict[str, Any] | None:
    """每轮对话结束后：将对话写入记忆缓冲区。"""
    mem0_cfg = get_mem0_config()
    if not mem0_cfg.get("enabled") or not mem0_cfg.get("auto_extract"):
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

    self._write_executor.submit(_background)
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
        )
    )
```

---

## 七、SSE 事件流适配

### 7.1 问题

当前 `astream()` 的 retrieval 事件通过 `yield {"type": "retrieval", ...}` 在 `agent.astream()` 之前发出。中间件运行在 LangGraph agent 执行循环内部，`runtime.stream_writer()` 需要 `stream_mode=["custom"]`。

当前 `_stream_events` 使用 `stream_mode=["messages", "updates"]`，不支持 custom 事件。

### 7.2 解决方案

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

**安全性**：`"custom"` 是 LangGraph 内置 stream_mode，添加后不影响现有的 `"messages"` 和 `"updates"` 事件处理。

### 7.3 修正后的完整事件流

```
astream() → agent.astream(stream_mode=["messages","updates","custom"])
  │
  ├─ MemoryMiddleware.abefore_agent:
  │   ├─ retrieve_async(message)
  │   ├─ runtime.stream_writer({"type":"retrieval", ...})
  │   └─ 暂存 _pending_context
  │
  ├─ MemoryMiddleware.awrap_model_call:
  │   └─ request.override(system_message=...)
  │
  ├─ _stream_events 捕获 mode=="custom" → yield 给外层
  │
  └─ MemoryMiddleware.aafter_agent:
      └─ _schedule_write(...)
```

---

## 八、agent.py 清理

### 8.1 astream() 清理点

| 原始位置 | 原始逻辑 | 处理方式 |
|---------|---------|---------|
| 487-501 行 | 记忆检索（13 行） | **删除** |
| 569-572 行 | `rag_context` 变量和传递 | **删除**（`build_dynamic_prefix` 中 `memory_context=""`） |
| 601-607 行 | `_schedule_mem0_write` 调用（7 行） | **删除** |
| 654-684 行 | `_schedule_mem0_write` 方法定义（31 行） | **删除** |

### 8.2 ainvoke() 清理点

与 astream() 完全对称：
- 695-701 行（记忆检索，7 行）→ **删除**
- 751-756 行（Zone 3 注入中 memory_context 部分）→ **删除**
- 记忆写入调用 → **删除**

### 8.3 其他清理

| 位置 | 改动 |
|------|------|
| `AgentManager.__init__` | 删除 `self._write_executor` |
| `_stream_events` | stream_mode 添加 `"custom"`，新增 custom 透传 |

### 8.4 保留部分

`astream()` 中 TaskState 相关逻辑（503-561 行）**完全不动**。Zone 3 注入仅保留 TaskState：

```python
# 清理后的 Zone 3 注入
has_active_steps = _has_in_progress_steps(task_state_dict)
dynamic_prefix = build_dynamic_prefix(
    memory_context="",  # 不再由 agent.py 管理
    task_state=task_state_md,
    has_active_steps=has_active_steps,
)
if dynamic_prefix:
    messages.insert(len(messages) - 1, SystemMessage(content=dynamic_prefix))
```

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

## 十、向下兼容与回退策略

### 10.1 injection_mode 回退

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

### 10.2 Checkpoint 兼容

| 模式 | Checkpoint 影响 |
|------|----------------|
| `system_prompt` | 记忆不在 messages 中，不影响 checkpoint |
| `system_message` | 记忆作为 SystemMessage 存入 checkpoint，与当前行为一致 |

旧 checkpoint 无标记，自然兼容。

### 10.3 前端兼容

SSE 事件格式完全不变：

```json
{"event": "retrieval", "data": {"query": "...", "results": [...]}}
```

前端 `store.tsx:466-491` 的消费逻辑无需任何修改。

### 10.4 渐进式迁移路径

| 阶段 | 操作 | 验证点 |
|------|------|-------|
| 1 | 新增 `memory_middleware.py`，agent.py 注册但保留旧代码（注释掉） | 编译通过 |
| 2 | 切换 injection_mode 在两种模式间切换，对比行为一致性 | 检索结果、注入位置、写入触发 |
| 3 | 确认无误后删除 agent.py 中被注释的旧代码 | 代码整洁 |
| 4 | 清理 `AgentManager._write_executor` 等残留 | 无遗漏引用 |

---

## 十一、改动范围总览

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `backend/graph/memory_middleware.py` | **新增** | `MemoryMiddleware` 类：检索 + 注入 + 写入（~180 行） |
| `backend/graph/agent.py` | 修改 | 删除 ~80 行记忆逻辑，新增 ~12 行注册 + stream_mode 扩展 |
| `backend/config.json` | 修改 | 新增 `memory_middleware` 配置段 |

### 净效果

+180 行（新文件）- 80 行（清理）= **+100 行**，但 agent.py 减少 80 行重复代码，记忆生命周期闭环在独立模块中。

### 不改动的部分

| 组件 | 原因 |
|------|------|
| `unified_memory.py` | 被中间件调用，接口不变 |
| `memory_buffer.py` | 被中间件调用，接口不变 |
| `mem0_manager.py` | 被中间件调用，接口不变 |
| `mem0_tool.py` | Agent 工具，独立于中间件 |
| `prompt_builder.py` | `build_dynamic_prefix` 签名不变 |
| `tools/__init__.py` | 无变更 |
| 前端 | SSE 事件格式不变 |
