## Why

`ToolOutputBudgetMiddleware` 的工具输出归档功能存在 4 个连锁问题：写入失败会导致中间件链中断（P0 Bug）、归档文件名不含 session_id 导致无法按会话清理、delete/clear 端点不清理归档文件造成资源永久泄漏、且无过期清理机制。长期运行后 `sessions/archive/` 目录会无限膨胀。

## What Changes

- 修复 `_archive_output()` 写入失败时的降级处理：包裹 try/except，失败时 log.warning 并降级为纯截断，不中断中间件链
- **BREAKING** 归档文件名格式从 `tool_{tool_name}_{timestamp}.txt` 改为 `tool_{tool_name}_{session_id}_{timestamp}.txt`，支持按会话关联清理
- `delete_session` 和 `clear_session_messages` 端点新增归档文件级联清理逻辑
- 应用启动时清理超过 7 天的归档文件（GC）

## Capabilities

### New Capabilities

（无新增能力）

### Modified Capabilities

- `progressive-tool-compression`: 修改归档文件名格式（含 session_id）、写入降级保护、级联清理、GC 过期清理四个 Requirement

## Impact

**后端文件**:
- `backend/graph/middleware.py` — `_archive_output()` 降级保护 + 文件名格式变更 + `abefore_model` 传入 session_id
- `backend/api/sessions.py` — `delete_session` 和 `clear_session_messages` 新增归档文件清理
- `backend/graph/agent.py` — `_build_middleware()` 传入 base_dir（已有）或新增 session_id 获取逻辑
- `backend/app.py` — 启动时调用归档 GC

**测试文件**:
- `backend/tests/test_middleware.py` — 更新归档相关测试的文件名断言、新增降级测试、GC 测试

**API 行为变更**:
- 归档文件名格式变更，已有的 `read_file("sessions/archive/tool_xxx_timestamp.txt")` 引用需兼容新格式

**回滚方案**: 低风险变更。降级保护是纯防御性代码；文件名变更是增量式的（新文件用新格式，旧文件不受影响）；级联清理和 GC 可通过配置项关闭。
