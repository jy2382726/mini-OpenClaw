# HITL（Human-in-the-Loop）工具审批机制设计

> 日期：2026-04-17
> 状态：已确认，待实施

## 目标

为 Agent 工具调用增加人工审批环节。当 Agent 请求执行高风险工具（如 terminal、write_file）时，暂停执行并在前端展示审批 UI，用户批准后继续，拒绝后 Agent 自行调整路径。

## 设计原则

- **复用现有架构**：基于 LangGraph interrupt + AsyncSqliteSaver checkpoint，零新增基础设施
- **最小改动**：~165 行改动，0 个新文件
- **配置驱动**：通过 config.json 的 `hitl` 段控制，默认关闭，热重载生效
- **渐进体验**：审批 UI 内嵌对话气泡，不中断对话流视觉连贯性

## 方案选型

| 方案 | 思路 | 优势 | 劣势 |
|------|------|------|------|
| **A. SSE 中断 + 恢复 API** | LangGraph interrupt 暂停，checkpoint 保存，approve/reject 恢复 | 持久化零成本，断线可恢复 | SSE 流需中断再恢复 |
| B. asyncio.Event 同步等待 | SSE 流不断开，await 等待审批信号 | 实现最简单 | 长连接挂起，断线丢失 |
| C. 双工具包装器 | 包装每个需审批的工具 | 对 Agent 透明 | 维护成本高，每个工具写 wrapper |

**选定方案 A**。

## 详细设计

### 1. 配置层（config.py）

config.json 新增 `hitl` 配置段：

```json
{
  "hitl": {
    "enabled": false,
    "approval_required": ["terminal", "write_file", "python_repl"],
    "timeout_seconds": 30,
    "timeout_action": "reject"
  }
}
```

新增 `get_hitl_config()` 函数，复用现有 TTL 缓存机制读取配置。

**默认值**：`enabled=false`，开启后默认审批 `terminal`、`write_file`、`python_repl`。只读工具（`read_file`、`search_knowledge`、`fetch_url`）不在默认审批列表中。

### 2. Agent 构建（agent.py）

`_build_agent` 方法改动（~10 行）：

当 `hitl.enabled=true` 且 `approval_required` 非空时，给 `create_agent` 传入 `interrupt_before=["tools"]`。Agent 执行到工具节点时暂停，checkpoint 保存状态。

```python
interrupt_before = None
hitl_cfg = load_config().get("hitl", {})
if hitl_cfg.get("enabled") and hitl_cfg.get("approval_required"):
    interrupt_before = ["tools"]

agent = create_agent(
    ...,
    interrupt_before=interrupt_before,
)
```

### 3. SSE 流中断处理（chat.py）

#### event_generator 改动

在 `astream` 循环结束后，检测 interrupt 状态：

```python
# 循环结束后检测
snapshot = await agent.aget_state(thread_config)
if snapshot.next:  # 有待执行节点 = 被 interrupt 暂停
    hitl_cfg = load_config().get("hitl", {})
    approval_required = hitl_cfg.get("approval_required", [])
    
    pending_tools = []
    for msg in snapshot.values.get("messages", []):
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc["name"] in approval_required:
                    pending_tools.append({
                        "tool_call_id": tc["id"],
                        "tool": tc["name"],
                        "input": tc.get("args", {}),
                    })
    
    if pending_tools:
        yield {
            "event": "tool_approval",
            "data": json.dumps({
                "pending_tools": pending_tools,
                "session_id": session_id,
            }, ensure_ascii=False),
        }
        return  # 不发 done，流暂停
```

关键：只拦截 `approval_required` 列表中的工具。不在列表中的工具不应触发 interrupt（需在 approve 恢复时处理）。

#### 新增 approve/reject 端点

```python
class ApprovalRequest(BaseModel):
    session_id: str
    tool_call_id: str
    approved: bool

@router.post("/chat/approve")
async def approve_tool(request: ApprovalRequest):
    """批准工具调用 → 从 checkpoint 恢复执行"""
    # 复用 event_generator 逻辑，初始消息为 None（从 checkpoint 恢复）
    return EventSourceResponse(
        _resume_event_generator(request.session_id)
    )

@router.post("/chat/reject")
async def reject_tool(request: ApprovalRequest):
    """拒绝工具调用 → 注入拒绝消息，Agent 自行调整"""
    # 1. 注入 ToolMessage(content="用户拒绝了此工具调用") 到 checkpoint
    # 2. 复用 event_generator 逻辑恢复执行
    return EventSourceResponse(
        _resume_event_generator(request.session_id, rejected_tool_id=request.tool_call_id)
    )
```

`_resume_event_generator` 是从现有 `event_generator` 提取的复用函数，核心区别：
- 不传新的 `message`（传 `None`）
- reject 时先注入拒绝 ToolMessage 再恢复
- 产出相同的 SSE 事件格式（token/tool_start/tool_end/done）

### 4. 前端改动

#### api.ts

新增两个 API 函数：

```typescript
export async function approveTool(sessionId: string, toolCallId: string): Promise<Response> {
  return fetch(`${API_BASE}/chat/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, tool_call_id: toolCallId, approved: true }),
  });
}

export async function rejectTool(sessionId: string, toolCallId: string): Promise<Response> {
  return fetch(`${API_BASE}/chat/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, tool_call_id: toolCallId, approved: false }),
  });
}
```

#### store.tsx

ToolCall 接口扩展：

```typescript
interface ToolCall {
  tool: string;
  input: string;
  output?: string;
  status: "running" | "done" | "pending_approval";  // 新增 pending_approval
  toolCallId?: string;  // 新增
}
```

SSE 事件处理新增 `tool_approval` case（~10 行）：将 `pending_tools` 映射为 `status: "pending_approval"` 的 ToolCall 数组。

审批按钮点击处理（~10 行）：
- 调用 `approveTool` / `rejectTool`
- 解析返回的 SSE 流，复用现有 SSE 解析逻辑更新消息

#### 审批按钮渲染

在现有工具调用气泡组件中，当 `status === "pending_approval"` 时渲染内嵌按钮（~30 行）：

- 批准按钮（绿色）+ 拒绝按钮（红色）
- 超时倒计时文字提示
- 复用 Tailwind 样式，支持 dark mode

### 5. 数据流

```
用户消息 → POST /api/chat (SSE)
  → Agent 执行 → 请求工具
  → interrupt 暂停 → checkpoint 保存
  → SSE 发送 tool_start（正常）+ tool_approval → 流暂停（不发 done）

用户点击「批准」→ POST /api/chat/approve
  → 从 checkpoint 恢复 → 执行工具
  → 新 SSE 流: tool_end → token → done

用户点击「拒绝」→ POST /api/chat/reject
  → 注入拒绝 ToolMessage → checkpoint 更新
  → 从 checkpoint 恢复 → Agent 收到拒绝信息
  → 新 SSE 流: token（调整后的响应）→ done

用户无操作 → 前端倒计时结束 → 自动调用 reject
```

### 6. 边界情况

| 场景 | 处理方式 |
|------|----------|
| 超时 | 前端倒计时，到期自动调用 reject |
| 批量工具调用 | interrupt 暂停全部，逐个审批，不在审批列表的自动放行 |
| 断线恢复 | checkpoint 持久化，重连后加载历史消息，pending_approval 状态的工具调用重新渲染审批按钮 |
| 非流式模式（ainvoke） | 首轮不支持 HITL，hitl.enabled 时强制使用 stream 模式 |
| 配置变更 | config.json TTL 缓存（30s），下次请求自动生效 |

### 7. 改动文件清单

| 文件 | 改动量 | 说明 |
|------|--------|------|
| `backend/config.py` | ~10 行 | `get_hitl_config()` + 默认配置 |
| `backend/graph/agent.py` | ~10 行 | `_build_agent` 传入 `interrupt_before` |
| `backend/api/chat.py` | ~80 行 | interrupt 检测 + approve/reject 端点 + resume 生成器 |
| `frontend/src/lib/api.ts` | ~20 行 | approve/reject API + tool_approval SSE 解析 |
| `frontend/src/lib/store.tsx` | ~15 行 | ToolCall 状态扩展 + tool_approval 事件 |
| `frontend/src/components/chat/` | ~30 行 | 审批按钮渲染 |
| **总计** | **~165 行** | **0 个新文件** |
