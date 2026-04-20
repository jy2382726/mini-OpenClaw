## Why

记忆操作（检索→注入→写入）的逻辑散落在 `agent.py` 的 `astream()` 和 `ainvoke()` 中（6 处，约 70 行重复代码），无统一管理入口。修改记忆逻辑需同时改两处，维护成本高，且与项目中间件链风格不一致。

## What Changes

- 新增 `MemoryMiddleware` 类（`backend/graph/memory_middleware.py`），通过 3 个 hook 闭环管理记忆生命周期：`abefore_agent`（检索）、`awrap_model_call`（注入）、`aafter_agent`（写入）
- 从 `agent.py` 的 `astream()` 和 `ainvoke()` 中删除重复的记忆检索、注入、写入逻辑（~80 行）
- 记忆注入方式从 SystemMessage 插入（Zone 3）切换为 `request.override(system_message=...)`（主方案），保留配置切换回 SystemMessage 模式
- `memory_context` 通过 graph state（`MemoryMiddlewareState`）传递，checkpoint 自动持久化，HITL resume 不丢失
- `_stream_events` 扩展 `stream_mode` 支持 `"custom"` 事件，适配 `runtime.stream_writer()` 的 retrieval 事件

## Capabilities

### New Capabilities

- `memory-middleware`：独立记忆中间件 — 封装检索、注入、写入全生命周期，支持双注入模式（system_prompt / system_message），graph state 持久化记忆上下文

### Modified Capabilities

- `middleware-chain`：中间件链从 4 层扩展为 5 层（新增 MemoryMiddleware），注册和配置读取逻辑变更
- `unified-memory`：记忆注入位置从 SystemMessage（Zone 3 messages 插入）变更为 system_message（request.override），支持配置回退

## Impact

### 受影响文件

| 文件 | 改动类型 |
|------|---------|
| `backend/graph/memory_middleware.py` | 新增 — `MemoryMiddleware` 类 + `MemoryMiddlewareState`（~190 行） |
| `backend/graph/agent.py` | 修改 — 删除 ~80 行记忆逻辑，新增 ~12 行中间件注册 + stream_mode 扩展 |
| `backend/config.json` | 修改 — 新增 `memory_middleware` 配置段 |

### 不受影响

- `unified_memory.py`、`memory_buffer.py`、`mem0_manager.py` — 被中间件调用，接口不变
- `prompt_builder.py` — `build_dynamic_prefix` 签名不变（`memory_context=""` 时只输出 TaskState）
- 前端 — SSE 事件格式不变（retrieval 事件格式完全一致）
- Checkpoint 向下兼容 — 旧 checkpoint 无 `memory_context` 字段时返回空字符串

### 回滚方案

将 `injection_mode` 设为 `system_message`（与当前行为一致），禁用 `MemoryMiddleware`，恢复 `agent.py` 中的旧记忆逻辑。所有新配置字段有默认值，缺失时行为与当前一致。渐进式迁移路径（设计文档第 11.4 节）：先注册保留旧代码（注释）→ 双模式对比验证 → 确认后删除旧代码。
