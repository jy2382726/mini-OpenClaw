## Why

Agent 执行高风险工具（终端命令、文件写入、代码执行）时缺乏人工干预机制。用户无法在工具实际执行前进行审批，只能事后观察结果。这在生产环境中存在安全风险——Agent 可能执行破坏性命令或写入意外文件。需要一种机制，让用户在工具调用执行前有机会审核并决定是否放行。

## What Changes

- **配置层**：`config.json` 新增 `hitl` 配置段，控制审批开关、审批工具列表、超时策略，支持热重载
- **Agent 构建**：当 HITL 启用时，传入 `interrupt_before=["tools"]` 使 Agent 在工具节点前暂停，checkpoint 自动保存状态
- **SSE 流中断**：`event_generator` 检测 interrupt 状态，发送 `tool_approval` 事件通知前端，SSE 流暂停（不发 `done`）
- **审批端点**：新增 `POST /api/chat/approve` 和 `POST /api/chat/reject`，从 checkpoint 恢复 Agent 执行
- **前端状态**：`ToolCall` 接口新增 `pending_approval` 状态和 `toolCallId` 字段，SSE 解析新增 `tool_approval` 事件处理
- **审批 UI**：工具调用气泡内嵌批准/拒绝按钮，超时倒计时提示，复用 Tailwind + dark mode

## Capabilities

### New Capabilities

- `hitl-tool-approval`：Human-in-the-Loop 工具审批机制——配置驱动的工具调用审批流程，基于 LangGraph interrupt + checkpoint 暂停/恢复，包含后端中断检测、审批端点、前端审批 UI

### Modified Capabilities

无。HITL 是纯增量功能，不修改现有 spec 的需求定义。现有 checkpoint、SSE 流、中间件链的行为不变，HITL 在其上层叠加审批逻辑。

## Impact

**后端改动：**

- `backend/config.py`（~10 行）：新增 `get_hitl_config()` 函数，读取 `hitl` 配置段
- `backend/graph/agent.py`（~10 行）：`_build_agent` 方法根据配置传入 `interrupt_before`
- `backend/api/chat.py`（~80 行）：`event_generator` 新增 interrupt 检测逻辑 + `_resume_event_generator` 复用函数 + approve/reject 端点

**前端改动：**

- `frontend/src/lib/api.ts`（~20 行）：新增 `approveTool()`、`rejectTool()` 函数
- `frontend/src/lib/store.tsx`（~15 行）：ToolCall 状态扩展 + `tool_approval` SSE 事件处理
- `frontend/src/components/chat/`（~30 行）：审批按钮渲染组件

**依赖：**

- LangGraph `interrupt_before` 原生支持（已集成）
- AsyncSqliteSaver checkpoint（已有）
- 无新增外部依赖

**回滚方案：**

`hitl.enabled` 默认 `false`，关闭后所有改动处于不激活状态，零行为影响。若需彻底移除，删除配置段和审批端点即可，不涉及数据迁移。

### Out of Scope（不做的事项）

- **细粒度权限控制**：不支持按用户角色、工具参数条件动态审批，仅支持工具名级别的固定列表
- **审批历史审计日志**：不记录审批操作的审计轨迹
- **多用户协作审批**：不支持多人会签或委托审批
- **非流式模式 HITL**：`ainvoke` 模式不支持 HITL，启用时强制使用 stream
- **自定义审批 UI 样式**：不提供审批界面的主题定制能力
- **工具参数级审批**：不区分同一工具不同参数的审批策略（如只审批 `rm -rf` 不审批 `ls`）
