## Context

`ToolOutputBudgetMiddleware`（`middleware.py`）在工具输出超过上下文窗口 5% 时将完整内容归档到 `sessions/archive/`。当前实现存在四个连锁问题：

1. `_archive_output()` 无 try/except，写入失败时异常冒泡导致中间件链中断
2. 归档文件名 `tool_{tool_name}_{timestamp}.txt` 不含 session_id，无法按会话关联
3. `delete_session` 和 `clear_session_messages` 不清理归档文件
4. 无过期清理机制

## Goals / Non-Goals

**Goals:**
- 归档写入失败时安全降级为纯截断，不中断 Agent 请求
- 归档文件可按 session_id 关联和清理
- delete/clear 端点级联清理关联的归档文件
- 启动时自动清理超期归档文件

**Non-Goals:**
- 不改变归档触发阈值（`ARCHIVE_RATIO = 0.05`）
- 不改变归档文件内容格式
- 不实现归档文件的压缩或去重
- 不处理 `session_manager.py` 中旧版 compress 产生的 `{session_id}_{timestamp}.json` 归档文件（该端点已废弃）

## Decisions

### D1. session_id 获取方式：从 LangGraph config 获取而非构造函数传入

**选择**: 在 `abefore_model(state, runtime)` 中通过 `get_config()` 从上下文变量获取 `thread_id`

**不选**: 在 `__init__` 中新增 `session_id` 参数

**理由**: `ToolOutputBudgetMiddleware` 在 `AgentManager._build_middleware()` 中构建一次，而 session_id 每次请求不同。从 config 获取无需修改构造函数签名和所有调用点，且 `thread_id` 就是 `session_id`，语义一致。

**获取路径**: `abefore_model` 中调用 `from langgraph.config import get_config`，通过 `get_config().get("configurable", {}).get("thread_id", "unknown")` 获取。`Runtime` 对象不包含 config 字段（LangGraph 1.1 设计），需通过 `get_config()` 上下文变量获取。不在 graph 上下文中时 fallback 为 `"unknown"`。

### D2. 归档文件名格式

**选择**: `tool_{tool_name}_{session_id}_{timestamp}.txt`

**不选**: `{session_id}/tool_{tool_name}_{timestamp}.txt`（按会话建子目录）

**理由**: 子目录方案虽然隔离性更好，但需要更多的目录操作和权限管理。前缀方案更简单，且用 `glob(f"tool_*_{session_id}_*.txt")` 即可批量匹配清理。

### D3. GC 时机：应用启动时同步执行

**选择**: 在 `app.py` 的 `lifespan()` 启动阶段同步执行一次清理

**不选**: 后台定期任务 / 定时器

**理由**: 归档文件不是高频写入资源，启动时清理一次足以防止长期膨胀。后台定期任务增加复杂性，且归档文件总量通常不大（每个会话最多几 MB）。默认保留 7 天。

### D4. 旧格式归档文件兼容

**选择**: GC 清理时同时清理旧格式（`tool_{tool_name}_{timestamp}.txt`）和新格式文件

**理由**: 旧格式文件无法按 session_id 关联，只能按时间清理。GC 按文件修改时间判断过期，不受文件名格式影响。

## Risks / Trade-offs

**[文件名格式变更]** → 旧归档文件的路径引用（如 Agent 用 `read_file` 读取的路径）不受影响，旧文件保持原样直到 GC 过期清理。新文件使用新格式。

**[GC 误删活跃文件]** → GC 仅清理超过 7 天的文件。正常使用场景下活跃会话不会 7 天不访问。极端情况下 Agent 可重新生成归档输出。

**[runtime config 获取 thread_id 的可用性]** → 已验证：LangGraph 1.1 的 `Runtime` 对象不包含 `config` 字段（官方文档明确说明）。实际使用 `get_config()` 从上下文变量获取 `configurable.thread_id`，在 graph 上下文中可用。不在 graph 上下文中（如单元测试）fallback 为 `"unknown"`。

## 调用关系

```
Agent 请求 → agent.astream(message, config={thread_id})
  → ToolOutputBudgetMiddleware.abefore_model(state, runtime)
    → 通过 get_config() 从上下文变量获取 session_id
    → 检测超预算 → _archive_output(content, tool_name, session_id)
      → try: write_text → 成功返回路径引用
      → except: log.warning → 降级为纯截断

DELETE /api/sessions/{id} → delete_session()
  → repo.soft_delete()
  → checkpointer.adelete_thread()
  → _cleanup_session_archives(session_id)  ← 新增

POST /api/sessions/{id}/clear → clear_session_messages()
  → checkpointer.adelete_thread()
  → _cleanup_session_archives(session_id)  ← 新增

app.py lifespan() 启动
  → _gc_expired_archives(max_age_days=7)  ← 新增
```

## 组件层级图

```
sessions/archive/
├── tool_terminal_sess-abc123_1713000000.txt   ← 新格式
├── tool_read_file_sess-abc123_1713000001.txt  ← 新格式
└── tool_terminal_1712000000.txt               ← 旧格式（仅 GC 清理）
```

## API 端点变更

| 端点 | 变更 |
|------|------|
| `DELETE /api/sessions/{id}` | 新增归档文件级联清理 |
| `POST /api/sessions/{id}/clear` | 新增归档文件级联清理 |
| 启动 `lifespan()` | 新增 `_gc_expired_archives()` 调用 |

## 回滚策略

低风险变更，无需特殊回滚：
- 降级保护是纯防御性代码，不影响正常路径
- 文件名变更仅影响新创建的文件
- 级联清理和 GC 可通过注释掉对应调用行临时关闭
- 旧格式文件在 GC 未执行前不受影响
