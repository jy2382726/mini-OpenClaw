## 1. 配置更新

- [x] 1.1 在 `backend/config.json` 的 `summarization` 配置段新增 `trigger_ratio`（0.6）、`trim_ratio`（0.30）、`trigger_tokens`（null）、`trim_tokens`（null）、`summary_prompt_file`（null）字段
- [x] 1.2 确认 `trigger_tokens: 8000` 旧配置向下兼容（作为绝对值覆盖）

## 2. ContextAwareSummarizationMiddleware 子类实现

- [x] 2.1 在 `backend/graph/middleware.py` 中新增 `ContextAwareSummarizationMiddleware` 类，继承 `SummarizationMiddleware`
- [x] 2.2 重写 `abefore_model` 方法：提取 SystemMessage → 调用父类 `abefore_model` 处理非 System 消息 → 将 SystemMessage 重新注入结果（父类返回格式：`[RemoveMessage(REMOVE_ALL_MESSAGES), HumanMessage(summary), ...preserved]`，insert_pos=2）
- [x] 2.3 在 `backend/graph/middleware.py` 中新增 `DEFAULT_SUMMARY_PROMPT_ZH` 内置中文摘要提示词常量（8 节结构）

## 3. Agent 配置读取逻辑

- [x] 3.1 在 `backend/graph/agent.py` 的 `_build_middleware` 中替换 `SummarizationMiddleware` 为 `ContextAwareSummarizationMiddleware`
- [x] 3.2 实现 `trigger_ratio`/`trigger_tokens` 配置读取逻辑（比例优先，绝对值覆盖）
- [x] 3.3 实现 `trim_ratio`/`trim_tokens` 配置读取逻辑，将计算值传入 `trim_tokens_to_summarize` 参数
- [x] 3.4 新增 `_load_summary_prompt(sum_cfg)` 方法，实现三级加载优先级（配置文件 > workspace/summary_prompt.md > 内置常量）
- [x] 3.5 将 `summary_prompt` 传入 `ContextAwareSummarizationMiddleware` 构造函数

## 4. 自定义摘要提示词

- [x] 4.1 创建 `backend/workspace/summary_prompt.md`，包含 8 节中文摘要提示词（会话意图、关键决策、工具调用、文件产物、错误修复、用户消息、当前进展、后续步骤），使用 `{messages}` 占位符

## 5. 手动摘要同步更新

- [x] 5.1 修改 `backend/graph/agent.py` 的 `_generate_checkpoint_summary` 方法，将 `DEFAULT_SUMMARY_PROMPT` 替换为 `_load_summary_prompt()` 加载的自定义提示词，保持手动/自动摘要格式一致

## 6. 上下文使用率后端

- [x] 6.1 修改 `backend/api/tokens.py` 的 `get_session_token_count`，新增 `context_window`（从 `get_context_window()` 读取）和 `usage_ratio`（`total_tokens / context_window`）字段

## 7. 上下文使用率前端

- [x] 7.1 修改 `frontend/src/lib/api.ts` 的 `getSessionTokenCount` 返回类型，新增 `context_window` 和 `usage_ratio` 字段
- [x] 7.2 在 `frontend/src/lib/store.tsx` 中新增 `contextUsage` 状态（`{ ratio, totalTokens, contextWindow } | null`），通过 `useEffect([sessionId, messages.length, isStreaming])` 触发 token API 调用，`isStreaming=true` 时跳过
- [x] 7.3 修改 `frontend/src/components/chat/ChatInput.tsx` 压缩按钮：`ratio < 0.6` 显示"压缩"；`0.6-0.8` 显示"压缩 (N%)"橙色；`≥ 0.8` 显示"压缩 (N%)"红色
- [x] 7.4 修改 `frontend/src/components/chat/ChatInput.tsx` 发送按钮：`contextUsage.ratio > 0.8` 时禁用，显示提示"上下文空间不足，请先压缩对话"

## 8. 验证

- [x] 8.1 启动后端，确认 `trigger_ratio`/`trim_ratio` 配置生效（通过日志确认计算值）
- [x] 8.2 构造长对话触发自动摘要，验证摘要内容使用中文提示词且包含 8 节结构
- [x] 8.3 验证 Zone 3 SystemMessage 在摘要后仍保留在消息列表中
- [x] 8.4 验证手动摘要与自动摘要使用相同提示词
- [x] 8.5 验证 `trigger_tokens` 绝对值配置可正确覆盖比例计算
- [x] 8.6 验证前端压缩按钮正确显示上下文使用率百分比
- [x] 8.7 验证使用率超过 80% 时发送按钮被禁用，压缩后自动恢复
