## Context

当前 Agent 巧行执行工具时无人工干预环节——终端命令、文件写入等高风险操作直接执行，用户只能事后观察结果。系统已具备 LangGraph checkpoint 持久化（AsyncSqliteSaver）和 SSE 流式传输，HITL 将复用这些现有能力。

当前 Agent 通过 `create_agent` 构建，支持 `checkpointer`、`state_schema`、`middleware` 等参数。LangGraph 原生支持 `interrupt_before` 参数，可在指定节点前暂停执行并通过 checkpoint 保存状态。

SSE 事件流已定义：`token`、`tool_start`、`tool_end`、`new_response`、`task_update`、`done`、`error`、`title`、`retrieval`。前端 `ToolCall` 接口状态为 `"running" | "done"`，通过 `ThoughtChain` 组件渲染。

## Goals / Non-Goals

**Goals:**

- 为高风险工具调用增加可选的人工审批环节
- 基于现有 checkpoint + SSE 架构实现，零新增基础设施
- 配置驱动，默认关闭，支持热重载
- 审批 UI 内嵌对话气泡，不中断对话流视觉连贯性
- 支持断线恢复——checkpoint 持久化审批状态

**Non-Goals:**

- 细粒度权限控制（按用户角色/工具参数条件动态审批）
- 审计日志（记录审批操作历史）
- 多用户协作审批
- 非流式模式 HITL（ainvoke 不支持）
- 工具参数级审批策略

## Decisions

### D1: 使用 LangGraph interrupt_before + checkpoint 恢复，而非 asyncio.Event 或双工具包装器

**选定**：LangGraph `interrupt_before=["tools"]`

**备选方案对比：**

| 方案 | 优势 | 劣势 |
|------|------|------|
| **A. interrupt_before + checkpoint** | 持久化零成本，断线可恢复，复用现有基础设施 | SSE 流需中断再恢复 |
| B. asyncio.Event 同步等待 | 实现最简单，SSE 不断开 | 长连接挂起，断线丢失审批状态 |
| C. 双工具包装器 | 对 Agent 透明 | 维护成本高，每个工具写 wrapper |

**理由**：方案 A 复用已有 checkpoint 机制，无需额外持久化。断线恢复是刚需——用户可能在审批前关闭浏览器。SSE 流中断是可接受的代价，恢复时新开 SSE 流即可。

### D2: 配置嵌入 config.json，复用 TTL 缓存机制

**理由**：系统已有成熟的配置热更新机制（30s TTL 缓存 + `load_config()` + `save_config()`），无需引入新的配置源。`hitl` 配置段与 `middleware`、`features` 平级，保持一致的模式。

### D3: approve/reject 端点返回 SSE 流，而非 JSON

**理由**：审批恢复后 Agent 可能继续产生多轮工具调用和文本输出。返回 SSE 流复用现有前端 SSE 解析逻辑（`streamChat` 的解析器），避免为审批恢复单独写一套响应处理。approve/reject 端点与 `/api/chat` 产出相同格式的事件流。

### D4: 前端超时自动 reject，而非后端超时

**理由**：后端 `interrupt_before` 暂停后不再消耗资源（checkpoint 静态保存），超时计时放在前端更合理——用户可以看到倒计时，后端无需维护超时定时器。

### D5: 批量工具调用时统一暂停，逐个审批

**理由**：LangGraph `interrupt_before=["tools"]` 会暂停整个工具节点（包含所有待执行工具）。审批时只放行 `approval_required` 列表内的工具，其余工具自动执行。这避免了复杂的 per-tool interrupt 逻辑。

## 架构

### 组件层级图

```
config.json
  └─ hitl 段（enabled, approval_required, timeout_seconds, timeout_action）
       │
backend/config.py
  └─ get_hitl_config() ── TTL 缓存读取
       │
backend/graph/agent.py
  ├─ _build_agent() ── 传入 interrupt_before=["tools"]
  └─ astream() ── interrupt 检测 + 非审批工具自动恢复 + yield tool_approval_needed
       │
backend/api/chat.py
  ├─ event_generator() ── 处理 tool_approval_needed → 转发 SSE tool_approval
  ├─ POST /api/chat/approve ── 恢复执行（SSE 流）
  └─ POST /api/chat/reject ── 注入拒绝消息 + 恢复（SSE 流）
       │
frontend/src/lib/api.ts
  ├─ approveTool() ── POST approve，解析 SSE 响应
  └─ rejectTool() ── POST reject，解析 SSE 响应
       │
frontend/src/lib/store.tsx
  ├─ ToolCall.status 新增 "pending_approval"
  ├─ ToolCall 新增 toolCallId 字段
  └─ tool_approval SSE 事件处理
       │
frontend/src/components/chat/ThoughtChain.tsx
  └─ pending_approval 状态渲染审批按钮
```

### 数据流图

```
正常流程（HITL 启用，需审批工具）：
  用户消息 → POST /api/chat (SSE)
    → Agent 执行 → LLM 返回 tool_calls
    → interrupt_before=["tools"] → checkpoint 保存
    → astream() 检测 interrupt → 提取审批列表内的工具
    → SSE: tool_start + tool_approval → 流暂停（不发 done）

免审批工具自动恢复：
  用户消息 → POST /api/chat (SSE)
    → Agent 执行 → LLM 返回 tool_calls（全部不在审批列表中）
    → interrupt_before=["tools"] → checkpoint 保存
    → astream() 检测 interrupt → 无 pending_tools
    → 自动恢复 agent.astream(None) → 工具正常执行
    → SSE: tool_start → tool_end → done（正常结束）

  用户点击「批准」→ POST /api/chat/approve
    → 从 checkpoint 恢复 → 执行工具
    → 新 SSE 流: tool_end → token → done

  用户点击「拒绝」→ POST /api/chat/reject
    → 注入 ToolMessage("用户拒绝了此工具调用") → checkpoint 更新
    → 从 checkpoint 恢复 → Agent 收到拒绝 → 调整响应
    → 新 SSE 流: token → done

  超时无操作 → 前端倒计时结束 → 自动调用 reject

断线恢复：
  重连页面 → 加载历史消息（checkpoint 投影）
  → pending_approval 状态的工具调用渲染审批按钮
  → 用户可继续审批或拒绝
```

### API 端点规范

#### POST /api/chat/approve

**请求体：**
```json
{
  "session_id": "string",
  "tool_call_id": "string"
}
```
> 注：动作由 URL 路径决定，无需额外的 `approved` 字段。

**响应：** SSE 流（与 `/api/chat` 相同格式），包含 `tool_end`、`token`、`done` 等事件。

**行为：** 从 checkpoint 恢复 Agent 执行，工具正常调用。

#### POST /api/chat/reject

**请求体：**
```json
{
  "session_id": "string",
  "tool_call_id": "string"
}
```

**响应：** SSE 流，包含 Agent 调整后的 `token`、`done` 等事件。

**行为：** 向 checkpoint 注入拒绝 ToolMessage，Agent 收到拒绝后自行调整。

#### SSE 事件: tool_approval

**触发条件：** Agent 被 `interrupt_before` 暂停，且有待审批的工具调用。

**数据格式：**
```json
{
  "pending_tools": [
    {
      "tool_call_id": "string",
      "tool": "string",
      "input": {}
    }
  ],
  "session_id": "string",
  "timeout_seconds": 30
}
```
> `timeout_seconds` 来自后端 HITL 配置，前端据此启动倒计时。前端批量审批防竞态：点击任一按钮后同消息内所有 `pending_approval` 工具统一标记为 `running`。

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|----------|
| SSE 流中断后恢复需要新连接 | 恢复端点返回新 SSE 流，前端复用现有 SSE 解析逻辑，用户体验为短暂等待后继续输出 |
| `create_agent` 可能不支持 `interrupt_before` | 实施前验证 LangGraph `create_agent` API 是否接受此参数；若不支持，改用底层 `StateGraph` 构建 |
| 批量工具调用中混合需审批和免审批工具 | 只拦截 `approval_required` 列表中的工具，免审批工具在恢复时自动执行 |
| checkpoint 中断状态残留 | 前端断线恢复时检测 pending_approval 状态，允许用户继续审批或超时自动 reject |
| 配置变更期间正在进行审批 | TTL 缓存 30s 生效，进行中的审批使用中断时刻的配置快照 |

## Migration Plan

1. 部署时 `hitl.enabled` 默认 `false`，所有现有行为不变
2. 用户通过 config.json 开启后，后续请求自动生效（TTL 缓存 30s 内）
3. 回滚：将 `hitl.enabled` 设为 `false` 即可，无数据迁移影响
4. 彻底移除：删除配置段 + 审批端点 + 前端审批逻辑，不影响现有功能

## Open Questions

1. `create_agent` 是否原生支持 `interrupt_before` 参数？需在实施前验证。若为项目自定义的 `create_agent` 包装器，可能需要调整参数透传。
2. 批量工具调用中多个需审批工具的处理策略——当前设计是统一暂停后逐个审批，但 LangGraph `interrupt_before` 暂停粒度是整个 `tools` 节点。如果需要 per-tool 审批，可能需要更细粒度的 interrupt 机制。
