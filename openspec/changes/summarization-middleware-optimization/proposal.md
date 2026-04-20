## Why

`SummarizationMiddleware` 存在 4 个已确认缺陷，导致长对话场景下摘要质量严重下降：

1. **`trigger_tokens` 配置项失效**：`config.json` 中配置的 `trigger_tokens: 8000` 被忽略，代码硬编码 `context_window * 0.6`
2. **`trim_tokens_to_summarize` 写死 4000**：128K 窗口下仅 6% 的待摘要内容被发送给摘要 LLM，94% 信息丢失
3. **Zone 3 SystemMessage 无保护**：动态注入的记忆上下文、任务状态等 SystemMessage 被当作普通消息参与压缩
4. **摘要提示词不可配置**：使用英文通用提示词，与项目中文人设不一致，且未覆盖工具调用密集场景

此外，用户无法感知上下文空间使用情况，容易在接近窗口上限时仍继续发送消息导致截断或摘要触发不可预期。需要在压缩按钮上显示上下文使用率，并在超过 80% 时禁用发送按钮强制用户先压缩。

## What Changes

- 修复 `trigger_tokens`/`trigger_ratio` 配置读取逻辑，支持比例模式和绝对值覆盖
- 新增 `trim_ratio`/`trim_tokens` 配置项，联动上下文窗口计算 `trim_tokens_to_summarize`
- 新增 `ContextAwareSummarizationMiddleware` 子类，在摘要过程中保护 SystemMessage 不被压缩
- 新增自定义中文摘要提示词（8 节结构，覆盖工具调用、错误修复、文件变更等场景）
- 提示词通过 Markdown 文件配置（`workspace/summary_prompt.md`），遵循 Markdown-as-config 模式
- 后端 `/tokens/session/{id}` API 新增返回 `context_window` 和 `usage_ratio` 字段
- 前端压缩按钮显示上下文使用率百分比
- 前端发送按钮在上下文使用率超过 80% 时禁用，强制用户先执行压缩

## Capabilities

### New Capabilities

- `context-usage-indicator`：上下文使用率实时指示器 — 在压缩按钮上显示使用率百分比，超过 80% 禁用发送按钮强制压缩

### Modified Capabilities

- `middleware-chain`：SummarizationMiddleware 注册参数变更（trigger/trim 配置化）、新增 SystemMessage 保护子类、自定义摘要提示词
- `checkpoint-summarize`：手动摘要提示词需与自动摘要保持一致，同步更新为自定义中文提示词

## Impact

### 受影响文件

| 文件 | 改动类型 |
|------|---------|
| `backend/graph/agent.py` | 修改 — `_build_middleware` 读取 trigger/trim 配置；`SummarizationMiddleware` 替换为 `ContextAwareSummarizationMiddleware`；新增 `_load_summary_prompt` 方法 |
| `backend/graph/middleware.py` | 新增类 — `ContextAwareSummarizationMiddleware`；新增常量 `DEFAULT_SUMMARY_PROMPT_ZH` |
| `backend/workspace/summary_prompt.md` | 新增文件 — 默认中文摘要提示词 |
| `backend/config.json` | 修改 — `summarization` 配置段新增 `trigger_ratio`、`trim_ratio`、`trim_tokens`、`summary_prompt_file` |
| `backend/api/tokens.py` | 修改 — `get_session_token_count` 新增返回 `context_window` 和 `usage_ratio` |
| `frontend/src/lib/api.ts` | 修改 — `getSessionTokenCount` 返回类型新增 `context_window`、`usage_ratio` |
| `frontend/src/lib/store.tsx` | 修改 — 新增 `contextUsage` 状态，消息变更时自动获取使用率 |
| `frontend/src/components/chat/ChatInput.tsx` | 修改 — 压缩按钮显示使用率；发送按钮条件禁用 |

### 不受影响

- LangChain 框架源码 — 通过子类扩展，不修改框架代码
- `state["messages"]` 数据结构
- Checkpoint 持久化逻辑

### 回滚方案

删除 `ContextAwareSummarizationMiddleware` 子类，恢复直接使用 `SummarizationMiddleware`，移除新增配置字段。所有新配置字段有默认值，缺失时行为与当前一致。前端上下文使用率功能为渐进增强，API 字段缺失时降级为不显示。
