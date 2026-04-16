## Context

checkpoint-session-migration 完成后，消息数据的唯一来源是 LangGraph checkpoint（SQLite）。然而前端"压缩"按钮仍调用旧端点 `POST /api/sessions/{id}/compress`（`api/compress.py`），该端点操作的是已弃用的 session JSON 文件：

```
当前流程（脱节）：
前端按钮 → compressSession() → POST /sessions/{id}/compress
  → session_manager.load_session()     ← 读取 JSON 文件（可能过时）
  → 辅助 LLM 生成摘要
  → session_manager.compress_history()  ← 写入 JSON 文件（不影响 checkpoint）
  → Agent 下次对话仍看到 checkpoint 中的完整未压缩历史
```

同时，`SummarizationMiddleware` 已作为自动摘要机制在每次模型调用前运行（触发阈值 60% 上下文窗口），具备完整的摘要生成能力（四段式结构化输出）。

## Goals / Non-Goals

**Goals:**

- 前端手动压缩按钮直接操作 checkpoint，压缩结果对 Agent 下次对话立即可见
- 复用 SummarizationMiddleware 的摘要 Prompt 和辅助 LLM 配置，保持自动/手动摘要输出格式一致
- 前端压缩完成后自动刷新 history 和 token 统计

**Non-Goals:**

- 不修改 SummarizationMiddleware 的自动触发逻辑（保持现有 60% 阈值）
- 不修改 ToolOutputBudgetMiddleware 的压缩策略
- 不实现 Observation 遮蔽（属于独立的 future work）
- 不在此提案中移除 `session_manager.compress_history()` 方法（标记 deprecated 即可，后续清理）

## Decisions

### Decision 1：手动摘要由 API 层直接操作 checkpoint，不经过 SummarizationMiddleware 实例

**选择**：在 `api/compress.py` 中实现独立的 `summarize_checkpoint` 逻辑，直接读取 checkpoint → 调用辅助 LLM → 写回 checkpoint。

**不选**：实例化 SummarizationMiddleware 并调用其 `abefore_model`。

**理由**：
- `SummarizationMiddleware.abefore_model` 设计为中间件钩子，需要 `AgentState` 和 `Runtime` 参数，且它的返回格式是 `{"messages": [RemoveMessage(ALL), ...]}`，这是给 LangGraph reducer 消费的，不适合在 API 层直接使用
- 它内部使用 `REMOVE_ALL_MESSAGES` 全量替换策略，而手动触发时我们需要更精细的控制（比如只在消息数 > keep 阈值时才执行）
- 复用其 `DEFAULT_SUMMARY_PROMPT`（四段式模板）和辅助 LLM 配置即可，不需要复用其消息替换机制

### Decision 2：手动摘要的 keep 策略与自动摘要一致

**选择**：手动摘要保留最近 10 条消息（`keep=("messages", 10)`），与 `SummarizationMiddleware` 配置一致。

**不选**：保留前 50% 消息（旧逻辑）。

**理由**：
- 旧逻辑的"前 50%"是一个粗粒度比例，没有考虑上下文压力
- 手动触发意味着用户主动希望释放上下文空间，应采用与自动摘要一致的策略
- 用户可以通过多次手动压缩逐步释放更多空间

### Decision 3：摘要结果通过 `aupdate_state` 写入 checkpoint

**选择**：使用 LangGraph 的 `aupdate_state` API 将摘要后的消息列表写回 checkpoint。

**数据流**：

```
前端按钮 → POST /api/sessions/{id}/summarize
  │
  ├─ 1. 从 checkpoint 加载消息（aget_tuple）
  │     messages = checkpoint["channel_values"]["messages"]
  │
  ├─ 2. 判断是否需要摘要
  │     if len(messages) <= keep_count → 返回 {summarized: false}
  │
  ├─ 3. 切分消息
  │     to_summarize = messages[:-keep_count]
  │     preserved = messages[-keep_count:]
  │     确保 AI/Tool 消息配对不被切断
  │
  ├─ 4. 调用辅助 LLM 生成摘要
  │     使用 SummarizationMiddleware 的 DEFAULT_SUMMARY_PROMPT
  │     摘要包含：SESSION INTENT / SUMMARY / ARTIFACTS / NEXT STEPS
  │
  ├─ 5. 构造新消息列表
  │     new_messages = [
  │       HumanMessage("Here is a summary...\n" + summary,
  │                     additional_kwargs={"lc_source": "summarization"}),
  │       ...preserved
  │     ]
  │
  ├─ 6. 写回 checkpoint
  │     await agent.aupdate_state(config, {"messages": new_messages}, as_node="model")
  │
  └─ 7. 返回结果
        {summarized: true, summarized_count: N, preserved_count: M}
```

### Decision 4：保留旧端点作为 deprecated 兼容，新增 `/summarize` 端点

**选择**：在 `compress.py` 中新增 `POST /sessions/{id}/summarize` 端点，旧 `POST /sessions/{id}/compress` 标记为 deprecated 但不删除。

**不选**：直接替换旧端点逻辑。

**理由**：
- 旧端点可能在某些场景（如 feature flag 未开启时）仍有使用价值
- 新旧端点可以并存一个版本周期，给前端平滑迁移时间
- 旧端点的路由路径不同，前端可以逐步切换

### Decision 5：API 端点需要获取 checkpointer 实例

**选择**：通过 `agent_manager._checkpointer` 属性获取已初始化的 checkpointer，并在 `agent.py` 中暴露一个公开方法 `summarize_checkpoint(session_id)` 封装完整的摘要逻辑。

**不选**：在 `compress.py` 中直接实例化 checkpointer。

**理由**：
- checkpointer 的初始化涉及异步数据库连接，应保持单例
- `AgentManager` 已经持有 checkpointer 实例和辅助 LLM 配置
- 封装为公开方法便于测试和复用

## Risks / Trade-offs

- **[Risk] aupdate_state 与中间件冲突** → 手动摘要写入后，如果下一轮自动触发 SummarizationMiddleware，可能在已摘要的消息上再次摘要。缓解：摘要后的 HumanMessage 带 `lc_source: "summarization"` 标记，SummarizationMiddleware 会将其作为普通消息处理，不会对摘要本身产生问题（摘要文本很短，远低于触发阈值）。
- **[Risk] 并发安全** → 用户快速连续点击压缩按钮可能导致重复摘要。缓解：前端已有 `isCompressing` 状态锁，后端可增加简单的 in-memory 锁（按 session_id）。
- **[Trade-off] 不复用 SummarizationMiddleware 实例** → 意味着需要维护两份相似的摘要逻辑（但 prompt 一致）。这是可接受的，因为自动/手动的触发条件、消息替换策略本质不同。
- **[Risk] checkpoint 消息为空** → 会话可能没有 checkpoint（新会话或已 clear）。缓解：API 层检查 checkpoint 是否存在，不存在时返回 400。
