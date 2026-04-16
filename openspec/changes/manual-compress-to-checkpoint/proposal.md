## Why

前端"压缩"按钮仍调用已弃用的 `POST /api/sessions/{id}/compress`，该接口操作的是旧的 session JSON 文件数据源（`session_manager.compress_history`），而非 checkpoint-session-migration 完成后的唯一消息数据源——checkpoint。这意味着手动压缩与实际对话数据脱节，压缩结果不会反映到 Agent 的上下文中。需要将手动压缩改为直接操作 checkpoint，复用已有的 SummarizationMiddleware 摘要能力，实现前端手动触发与自动摘要的统一。

## What Changes

- **新增** checkpoint 级手动摘要 API 端点，替代已弃用的 JSON 文件压缩逻辑
- **新增** 前端 compress 按钮对接新端点，支持基于 checkpoint 的手动摘要触发
- **修改** 前端 compress 按钮的 UI 提示文案，反映新的压缩行为（摘要早期消息而非固定前 50%）
- **移除** `api/compress.py` 中对 `session_manager.compress_history()` 的依赖
- **移除** 前端对旧 `POST /sessions/{id}/compress` 端点的调用

## Capabilities

### New Capabilities
- `checkpoint-summarize`: 从 checkpoint 读取消息，调用辅助 LLM 生成结构化摘要，通过 `aupdate_state` 写回 checkpoint，替代旧的 JSON 文件压缩

### Modified Capabilities
- `middleware-chain`: 手动摘要触发后复用 SummarizationMiddleware 的摘要 Prompt 和四段式输出格式（SESSION INTENT / SUMMARY / ARTIFACTS / NEXT STEPS），与自动摘要保持一致
- `checkpoint-projection`: 前端 compress 完成后刷新 history 和 token 统计，确保 UI 反映摘要后的消息状态

## Impact

- `backend/api/compress.py` — 重写端点逻辑，从操作 session JSON 改为操作 checkpoint
- `backend/graph/agent.py` — 可能需要暴露摘要能力（如 checkpoint 读取 + 辅助 LLM 调用）供 API 层调用
- `frontend/src/lib/api.ts` — 更新 `compressSession()` API 调用
- `frontend/src/lib/store.tsx` — 更新 `compressCurrentSession` 回调，适配新的响应格式和刷新逻辑
- `frontend/src/components/chat/ChatInput.tsx` — 更新确认对话框文案
- `frontend/src/components/layout/Sidebar.tsx` — 更新压缩确认对话框文案
