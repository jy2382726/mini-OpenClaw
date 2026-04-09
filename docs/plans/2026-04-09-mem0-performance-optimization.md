# mem0 性能优化方案

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 降低 mem0 记忆写入延迟（当前 20-90 秒），提升并发吞吐量和系统稳定性。

**Architecture:** 分四个维度优化 —— LLM 调用加速（独立轻量模型）、并发模型改进（线程池 + async executor）、冗余 IO 消除（缓存 + 单次扫描）、数据安全加固（verify 先加后删）。所有改动限定在已有的 6 个文件内，不新增文件。

**Tech Stack:** Python 3.11+, asyncio, concurrent.futures.ThreadPoolExecutor, mem0ai, Qdrant

---

## 瓶颈分析

### 当前写入流程时间构成

```
用户发送消息 → Agent 回复完成 → 后台线程启动
                                    │
                                    ├─ 1. buffer.add_turn()        < 1ms
                                    ├─ 2. buffer.should_flush()    < 1ms
                                    ├─ 3. buffer.flush()           ~ 2ms（磁盘写 JSON）
                                    └─ 4. mgr.batch_add()          20-90s ← 瓶颈
                                         ├─ LLM 事实提取           15-60s (70-90%)
                                         ├─ Embedding 计算          2-5s (10-15%)
                                         └─ Qdrant 写入            < 100ms (<1%)
```

**核心结论：** 写入延迟的 70-90% 来自 LLM 事实抽取调用。当前直接复用主对话模型 `qwen3.5-plus`，属于"杀鸡用牛刀"。

### 其他性能问题

| 问题 | 位置 | 影响 |
|------|------|------|
| 后台写入无线程池，每轮新建 Thread | `agent.py:277` | 高并发时线程数不可控 |
| 检索同步阻塞 async 事件循环 | `memory_retriever.py:94` | 所有 SSE 流在搜索期间卡住 |
| HybridRetriever 串行查询两源 | `memory_retriever.py:271-272` | hybrid 模式延迟翻倍 |
| 整合管道 3 次全量 get_all() | `memory_consolidator.py:62,86,97` | 浪费 IO |
| verify_memory 先删后加，中途失败丢数据 | `mem0_manager.py:358-359` | 数据安全风险 |
| config.json 每次请求读磁盘 | `config.py:67` | 不必要的 IO 开销 |
| max_tokens=1500 过大 | `mem0_manager.py:70` | 浪费生成时间 |

---

## 优化方案

### 方案一：mem0 独立轻量 LLM 模型（收益最大）

**目标：** 将 mem0 事实抽取的 LLM 调用从 15-60 秒降至 3-10 秒。

**原理：** 事实抽取是结构化 NLP 任务（从对话中提取 JSON 格式的事实），不需要创意写作能力。轻量模型完全胜任，响应速度提升 3-5 倍。

**改动：**
- `config.json` — `mem0` 块新增 `extraction_model` 配置
- `config.py` — `_DEFAULT_CONFIG["mem0"]` 增加默认值，`get_mem0_config()` 已自动合并无需改动
- `mem0_manager.py:48-70` — 初始化时读取 `extraction_model` 配置，有值则覆盖主模型；`max_tokens` 从 1500 降至 512；**关键：设置 `enable_thinking: false` 关闭思考模式**

**配置示例：**
```json
{
  "mem0": {
    "extraction_model": {
      "model": "qwen3.5-flash",
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key": "",
      "max_tokens": 512,
      "enable_thinking": false
    }
  }
}
```

**模型选型确认（已验证）：**

经过 DashScope 官方文档确认，各模型对比如下：

| 模型 | 定价（输入/输出） | 速度定位 | 结构化抽取能力 | 推荐度 |
|------|------------------|---------|--------------|-------|
| **qwen3.5-flash** | 0.2/2 元/M token | 极快（秒级） | 充分胜任，支持 JSON 输出 | **首选推荐** |
| qwen-flash | 更低 | 极快 | 基础抽取可用 | 预算敏感场景备选 |
| qwen-plus | 0.8/4 元/M token | 中等 | 优秀 | 性能过剩，不推荐 |
| qwen3.5-plus | 4/16 元/M token | 慢（10s+） | 优秀 | 当前使用，严重过度 |
| qwen-turbo | 已弃用 | - | - | **官方已宣布停止更新，勿用** |

**最终推荐：`qwen3.5-flash`（非思考模式）**

关键配置要点：
1. **必须关闭思考模式**：qwen3.5-flash 默认启用 thinking（深度推理），会显著增加延迟。mem0 事实抽取是结构化 NLP 任务，不需要推理链。通过 API 参数 `"extra_body": {"enable_thinking": false}` 或 `"chat_template_kwargs": {"enable_thinking": false}` 关闭。
2. **支持上下文缓存**：qwen3.5-flash 支持上下文缓存功能，可进一步减少重复 Prompt 的 token 消耗。
3. **性价比极高**：输入 0.2 元/M token、输出 2 元/M token，仅为 qwen3.5-plus 的 1/20 ~ 1/8。

**预期效果：** 写入延迟降低 60-80%（从 20-90s → 5-15s），token 成本降低 80%+。

---

### 方案二：后台线程池替代裸 Thread

**目标：** 控制并发写入线程数量，避免高并发时线程爆炸。

**改动：**
- `agent.py` — 类级别创建 `ThreadPoolExecutor(max_workers=4)`，`_schedule_mem0_write()` 改用 `self._executor.submit()`

**改动前后对比：**
```python
# 改动前：每轮新建线程，无上限
thread = threading.Thread(target=_background_write, daemon=True)
thread.start()

# 改动后：共享线程池，上限 4 线程
self._write_executor.submit(_background_write)
```

**预期效果：** 4 个并发用户时从 4 个线程 → 共享池，内存和调度开销降低。超出容量的任务自动排队，不丢弃。

---

### 方案三：检索异步化（run_in_executor）

**目标：** 消除 mem0 搜索对 async 事件循环的阻塞。

**改动：**
- `memory_retriever.py` — `Mem0Retriever` 和 `HybridRetriever` 新增 `async retrieve_async()` 方法，用 `loop.run_in_executor()` 包装同步 `mgr.search()`
- `agent.py` — 对应调用处改用 `await retriever.retrieve_async()`

**注意：** 需要同步 `retrieve()` 方法保持向后兼容（给 consolidator 等同步场景使用）。

**安全性确认（已验证）：异步检索不会导致记忆滞后。**

技术分析：`run_in_executor` 不是"延迟执行"，而是"在线程池中执行，通过 await 等待结果"。代码流程如下：

```
agent.py astream() 调用链：
  ├─ await retriever.retrieve_async(message)  ← 等待检索完成
  │   └─ run_in_executor 包装 mgr.search()    ← 在线程池中执行，不阻塞事件循环
  ├─ rag_context 构建完成                      ← 此时已有完整检索结果
  ├─ self._build_messages(message, augmented_history)  ← 记忆已注入上下文
  └─ agent.astream()                           ← LLM 调用时记忆已就绪
```

关键点：
1. `await` 确保检索完成后才继续执行后续代码，不存在"先响应后检索"的问题
2. 唯一区别是检索期间（约 2-5 秒），async 事件循环可以同时处理其他请求的 SSE token 推送，而不是全部卡住
3. 当前检索发生在 LLM 调用之前（agent.py:159-176），检索结果注入到 messages 中再传给 LLM，流程完全同步

---

### 方案四：整合管道单次扫描

**目标：** 将 `run_consolidation()` 的 3 次 `get_all()` 减少为 1 次。

**改动：**
- `memory_consolidator.py` — 在 `run_consolidation()` 入口取一次全量数据，作为参数传递给各阶段函数；中间阶段（merge、conflict resolution）产生的删除操作先收集 ID 列表，最后阶段统一执行

---

### 方案五：verify_memory 安全加固

**目标：** 消除 delete+re-add 之间的数据丢失窗口。

**改动：**
- `mem0_manager.py:verify_memory()` — 改为**先加后删**（先 add 新版本，成功后再 delete 旧版本）。如果 add 失败，旧记忆完好无损。

---

### 方案六：配置缓存

**目标：** 避免每次请求都读磁盘解析 JSON。

**改动：**
- `config.py` — `load_config()` 加 TTL 缓存（30 秒），`save_config()` 时主动失效缓存

---

## 优先级与实施顺序

```
方案一（独立轻量模型）    ← 收益最大，改动最小
  ↓
方案二（线程池）          ← 安全性改进，改动小
  ↓
方案五（verify 安全加固）  ← 数据安全，改动小
  ↓
方案四（整合单次扫描）    ← 中等改动
  ↓
方案六（配置缓存）        ← 小改动
  ↓
方案三（检索异步化）      ← 改动较大，需同步/异步双接口
```

方案一和方案二可以并行实施，互不依赖。

---

## 影响范围

| 文件 | 涉及方案 | 改动规模 |
|------|---------|---------|
| `backend/config.json` | 一 | 新增 `extraction_model` 配置块 |
| `backend/config.py` | 一、六 | `_DEFAULT_CONFIG` 增加默认值 + `load_config()` 加缓存 |
| `backend/graph/mem0_manager.py` | 一、五 | 初始化读独立模型 + verify 先加后删 |
| `backend/graph/agent.py` | 二、三 | 线程池 + 检索调用异步化 |
| `backend/graph/memory_retriever.py` | 三 | 新增 `retrieve_async()` |
| `backend/graph/memory_consolidator.py` | 四 | 单次扫描重构 |

**不涉及前端改动，不涉及 API 接口变更。**

---

## 验证标准

1. **写入延迟：** 单次 `batch_add(5 turns)` 从 >20s 降至 <15s（目标 <10s）
2. **并发安全：** 10 个并发用户同时对话，后台写入无异常、无丢失
3. **兼容性：** 不配置 `extraction_model` 时行为与优化前完全一致（向后兼容）
4. **数据安全：** `verify_memory()` 中途失败时原始记忆不丢失
