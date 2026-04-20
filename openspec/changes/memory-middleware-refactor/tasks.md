## 1. MemoryMiddleware 类实现

- [ ] 1.1 创建 `backend/graph/memory_middleware.py`，定义 `MemoryMiddlewareState(TypedDict)` 含 `memory_context: NotRequired[str]` 字段
- [ ] 1.2 实现 `MemoryMiddleware.__init__`：接收 `base_dir`、`config`、`write_executor` 参数，解析 `injection_mode`
- [ ] 1.3 实现 `_extract_latest_user_message(messages)` 静态方法：从 messages 末尾向前找 HumanMessage
- [ ] 1.4 实现 `_extract_last_exchange(messages)` 静态方法：提取最后一轮用户-助手对话

## 2. abefore_agent — 记忆检索

- [ ] 2.1 实现 `abefore_agent`：检查 enabled 和 unified_memory 配置开关
- [ ] 2.2 延迟初始化 `UnifiedMemoryRetriever`（首次调用时 `get_unified_retriever`）
- [ ] 2.3 调用 `retrieve_async` 异步检索，将结果格式化为 `memory_context`
- [ ] 2.4 通过 `runtime.stream_writer()` 发送 `{"type": "retrieval", "query": ..., "results": ...}` 自定义事件
- [ ] 2.5 `system_message` 模式下：构造 SystemMessage 插入 messages 列表（最后一条用户消息之前），与 memory_context 一并返回
- [ ] 2.6 `system_prompt` 模式下：仅返回 `{"memory_context": memory_context}`

## 3. awrap_model_call — 记忆注入

- [ ] 3.1 实现 `awrap_model_call`：`system_message` 模式下直接调用 `handler(request)` 跳过
- [ ] 3.2 `system_prompt` 模式下：从 `request.state["memory_context"]` 读取，为空时直接调用 handler
- [ ] 3.3 拼接记忆上下文到 `request.system_message.content` 末尾，格式 `\n\n<!-- Zone 3: Dynamic -->\n{memory_context}`
- [ ] 3.4 通过 `request.override(system_message=...)` 创建新请求并调用 handler

## 4. aafter_agent — 记忆写入

- [ ] 4.1 实现 `aafter_agent`：检查 mem0 配置（enabled + auto_extract）和 write_executor
- [ ] 4.2 调用 `_extract_last_exchange` 提取最后一轮对话
- [ ] 4.3 实现 `_schedule_write` 方法：通过 `write_executor.submit()` 提交后台任务
- [ ] 4.4 后台任务：`MemoryBuffer.add_turn` → `check_immediate_trigger` / `should_flush` → `flush` + `batch_add`
- [ ] 4.5 后台任务异常处理：try/except 包裹，仅打印警告不向上抛出

## 5. 中间件注册

- [ ] 5.1 在 `agent.py` 的 `_build_middleware()` 中新增 MemoryMiddleware 注册：读取 `memory_middleware` 配置，传入 `base_dir`、`config`、`self._write_executor`
- [ ] 5.2 在 `config.json` 的 `middleware` 段新增 `memory_middleware` 配置：`{"enabled": true, "injection_mode": "system_prompt"}`

## 6. SSE custom stream 扩展

- [ ] 6.1 修改 `agent.py` 的 `_stream_events`：`stream_mode` 从 `["messages", "updates"]` 扩展为 `["messages", "updates", "custom"]`
- [ ] 6.2 在 `_stream_events` 的事件循环中新增 `elif mode == "custom": yield data` 分支

## 7. agent.py 清理

- [ ] 7.1 删除 `astream()` 中的记忆检索逻辑（486-501 行）：`rag_context` 声明 + retriever 调用 + retrieval 事件 yield
- [ ] 7.2 修改 `astream()` 中 Zone 3 注入：`build_dynamic_prefix(memory_context=rag_context, ...)` 改为 `build_dynamic_prefix(memory_context="", ...)`
- [ ] 7.3 删除 `astream()` 中的记忆写入调度（601-607 行）
- [ ] 7.4 删除 `_schedule_mem0_write` 方法定义（654-684 行）
- [ ] 7.5 删除 `ainvoke()` 中的记忆检索逻辑（693-701 行）
- [ ] 7.6 修改 `ainvoke()` 中 Zone 3 注入：`memory_context=rag_context` 改为 `memory_context=""`

## 8. 验证

- [ ] 8.1 启动后端，发送消息确认记忆检索正常触发（通过日志确认 retrieve_async 调用）
- [ ] 8.2 确认 retrieval SSE 事件格式与当前一致（前端正常显示）
- [ ] 8.3 切换 `injection_mode` 为 `"system_prompt"`，确认记忆注入到 system_message
- [ ] 8.4 切换 `injection_mode` 为 `"system_message"`，确认记忆作为 SystemMessage 注入
- [ ] 8.5 触发 HITL interrupt + resume，确认记忆上下文不丢失
- [ ] 8.6 确认 mem0 后台写入正常（通过日志确认 buffer.add_turn 和 batch_add 调用）
- [ ] 8.7 设置 `memory_middleware.enabled=false`，确认中间件不加载，行为与当前一致
