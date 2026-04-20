## 1. 后端配置层

- [x] 1.1 在 `backend/config.py` 的 `_DEFAULT_CONFIG` 中新增 `hitl` 默认配置段，添加 `get_hitl_config()` 函数，复用 TTL 缓存机制
- [x] 1.2 在 `get_settings_for_display()` 中加入 `hitl` 配置输出，在 `update_settings()` 中支持 `hitl` 配置更新
- [x] 1.3 验证 `get_hitl_config()` 返回正确默认值和自定义值

## 2. Agent interrupt 配置

- [x] 2.1 验证 `create_agent` 是否支持 `interrupt_before` 参数（检查 `langchain.agents` 的 `create_agent` 实现或 LangGraph 文档）
- [x] 2.2 在 `backend/graph/agent.py` 的 `_build_agent()` 中，根据 `hitl.enabled` 和 `approval_required` 动态传入 `interrupt_before=["tools"]`

## 3. SSE 流中断检测

- [x] 3.1 在 `backend/api/chat.py` 的 `event_generator` 循环结束后新增 interrupt 状态检测：调用 `aget_state` 检查 `snapshot.next`
- [x] 3.2 检测到中断时，从 checkpoint messages 中提取需审批的 tool_calls，发送 `tool_approval` SSE 事件
- [x] 3.3 发送 `tool_approval` 后停止 SSE 流（不发 `done`），确保流正常结束

## 4. 审批端点

- [x] 4.1 在 `backend/api/chat.py` 中定义 `ApprovalRequest` Pydantic model（`session_id`、`tool_call_id`、`approved`）
- [x] 4.2 提取 `_resume_event_generator` 复用函数：从现有 `event_generator` 抽取恢复逻辑，支持从 checkpoint 恢复执行（不传新消息）
- [x] 4.3 实现 `POST /api/chat/approve` 端点：从 checkpoint 恢复执行，返回 SSE 流
- [x] 4.4 实现 `POST /api/chat/reject` 端点：注入拒绝 ToolMessage 后恢复执行，返回 SSE 流
- [x] 4.5 处理边界情况：不存在的 `tool_call_id` 返回 404，无 interrupt 状态返回错误

## 5. 前端 API 层

- [x] 5.1 在 `frontend/src/lib/api.ts` 中添加 `approveTool(sessionId, toolCallId)` 函数，POST approve 端点，返回 SSE AsyncGenerator
- [x] 5.2 在 `frontend/src/lib/api.ts` 中添加 `rejectTool(sessionId, toolCallId)` 函数，POST reject 端点，返回 SSE AsyncGenerator

## 6. 前端状态管理

- [x] 6.1 在 `frontend/src/lib/store.tsx` 中扩展 `ToolCall` 接口：`status` 新增 `"pending_approval"`，新增 `toolCallId` 可选字段
- [x] 6.2 在 `sendMessage` 的 SSE 事件循环中新增 `tool_approval` 事件处理：将 `pending_tools` 映射为 `pending_approval` 状态的 ToolCall
- [x] 6.3 新增 `approveToolCall` 和 `rejectToolCall` action：调用 API 函数，解析返回的 SSE 流，更新消息和 ToolCall 状态

## 7. 前端审批 UI

- [x] 7.1 在 `frontend/src/components/chat/ThoughtChain.tsx` 中为 `pending_approval` 状态的工具调用渲染审批按钮区域（批准/拒绝/倒计时）
- [x] 7.2 实现倒计时逻辑：基于配置的 `timeout_seconds`，到期自动调用 reject
- [x] 7.3 添加 dark mode 支持（`dark:` 前缀样式）

## 8. 集成验证

- [x] 8.1 端到端测试：HITL 关闭时行为不变，所有现有功能正常
- [x] 8.2 端到端测试：HITL 开启后，高风险工具触发审批，批准后正常执行，拒绝后 Agent 调整响应
- [x] 8.3 测试超时自动拒绝场景
- [x] 8.4 测试断线恢复：页面刷新后 pending_approval 状态正确恢复
