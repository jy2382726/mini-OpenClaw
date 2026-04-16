## 1. 后端：AgentManager 摘要方法

- [ ] 1.1 在 `backend/graph/agent.py` 中新增 `async summarize_checkpoint(session_id, keep_count=10)` 公开方法，封装从 checkpoint 读取消息 → 切分 → 调用辅助 LLM → 构造新消息 → aupdate_state 写回的完整流程
- [ ] 1.2 在 `summarize_checkpoint` 方法中实现 AI/Tool 消息配对保护逻辑（切割点落在 ToolMessage 时向前查找对应的 AIMessage）
- [ ] 1.3 使用 `SummarizationMiddleware.DEFAULT_SUMMARY_PROMPT` 作为摘要 Prompt，通过 `create_auxiliary_llm()` 创建辅助 LLM 实例
- [ ] 1.4 添加 in-memory 并发安全锁（按 session_id 粒度），防止同一会话的并发摘要请求

## 2. 后端：API 端点

- [ ] 2.1 在 `backend/api/compress.py` 中新增 `POST /sessions/{session_id}/summarize` 端点，委托给 `agent_manager.summarize_checkpoint()` 执行
- [ ] 2.2 端点返回 `{summarized: true/false, summarized_count: N, preserved_count: M}` 格式的响应
- [ ] 2.3 处理异常情况：checkpoint 不存在返回 400，并发冲突返回 409，辅助 LLM 不可用返回 503
- [ ] 2.4 旧 `POST /sessions/{session_id}/compress` 端点添加 deprecated 注释，保留不动

## 3. 前端：API 对接

- [ ] 3.1 在 `frontend/src/lib/api.ts` 中新增 `summarizeSession(sessionId)` 函数，调用 `POST /sessions/{id}/summarize`
- [ ] 3.2 更新 `frontend/src/lib/store.tsx` 中的 `compressCurrentSession` 回调，从调用旧 `compressSession()` 改为调用新的 `summarizeSession()`
- [ ] 3.3 更新压缩完成后的刷新逻辑：调用 `/history` 和 `/api/tokens/session/{id}` 刷新聊天记录和 token 统计

## 4. 前端：UI 文案更新

- [ ] 4.1 更新 `frontend/src/components/chat/ChatInput.tsx` 中压缩按钮的确认对话框文案，从"压缩前 N 条消息"改为"摘要早期消息，保留最近 10 条"
- [ ] 4.2 更新 `frontend/src/components/layout/Sidebar.tsx` 中压缩确认对话框的英文文案
- [ ] 4.3 更新 `frontend/src/components/chat/ChatInput.tsx` 中压缩按钮的 title 属性和 disabled 提示

## 5. 测试

- [ ] 5.1 为 `AgentManager.summarize_checkpoint()` 编写单元测试：成功摘要、消息数不足、AI/Tool 配对保护、并发锁
- [ ] 5.2 为 `POST /sessions/{id}/summarize` 端点编写 API 测试：成功响应、400/409/503 错误场景
- [ ] 5.3 前端启动后真实浏览器测试：点击压缩按钮 → 确认摘要生效 → history 刷新正确
