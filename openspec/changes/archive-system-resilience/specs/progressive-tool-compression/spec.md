## MODIFIED Requirements

### Requirement: 工具输出自动归档

当单条工具输出超过上下文窗口的 5%（`ARCHIVE_RATIO = 0.05`）时，系统 SHALL 将完整输出归档到文件，ToolMessage 中仅保留截断摘要和文件路径引用。

归档文件 MUST 保存到 `sessions/archive/` 目录，文件名格式为 `tool_{tool_name}_{session_id}_{timestamp}.txt`，其中 `session_id` 从 `abefore_model` 的 runtime config 中 `configurable.thread_id` 获取。

`_archive_output()` 方法 MUST 包裹 try/except：写入成功时返回路径引用 + 截断摘要；写入失败（磁盘满、权限不足等）时 MUST log.warning 并返回纯截断结果（不包含归档路径），MUST NOT 向上抛出异常。

归档操作在截断之前检查（先归档再截断）。

#### Scenario: 超大输出自动归档

- **WHEN** 一条 terminal 输出超过上下文窗口的 5%，且 session_id 为 "sess-abc123"
- **THEN** 系统将完整输出保存到 `sessions/archive/tool_terminal_sess-abc123_1713000000.txt`，ToolMessage content 替换为 `[完整输出已归档到 sessions/archive/tool_terminal_sess-abc123_1713000000.txt，可用 read_file 查看]\n{截断摘要}`

#### Scenario: 归档文件可被 Agent 重新读取

- **WHEN** Agent 需要查看已归档的完整输出
- **THEN** Agent MUST 能通过 `read_file("sessions/archive/tool_terminal_sess-abc123_1713000000.txt")` 读取完整内容

#### Scenario: 归档写入失败时安全降级

- **WHEN** `sessions/archive/` 目录不可写（权限不足、磁盘满）
- **THEN** 系统 MUST 仅执行截断压缩，不归档完整输出，MUST 在日志中记录 warning 级别警告，MUST NOT 向上抛出异常

#### Scenario: session_id 获取

- **WHEN** `abefore_model` 被调用
- **THEN** 系统 MUST 从 runtime config 的 `configurable.thread_id` 获取 session_id，传递给 `_archive_output()`

## ADDED Requirements

### Requirement: 归档文件级联清理

系统 MUST 在删除或清空会话时，级联清理 `sessions/archive/` 目录下该 session_id 关联的归档文件。

`DELETE /api/sessions/{session_id}` 端点和 `POST /api/sessions/{session_id}/clear` 端点 MUST 在清理 checkpoint 后，调用 `_cleanup_session_archives(session_id)` 删除所有匹配 `tool_*_{session_id}_*.txt` 的文件。

清理失败时 MUST log.warning，MUST NOT 阻塞删除/清空操作。

#### Scenario: 删除会话时清理归档文件

- **GIVEN** `sessions/archive/` 下存在 `tool_terminal_sess-abc123_*.txt` 归档文件
- **WHEN** 用户调用 `DELETE /api/sessions/sess-abc123`
- **THEN** 系统 MUST 删除所有匹配 `tool_*_sess-abc123_*.txt` 的归档文件

#### Scenario: 清空会话时清理归档文件

- **GIVEN** `sessions/archive/` 下存在 `tool_terminal_sess-abc123_*.txt` 归档文件
- **WHEN** 用户调用 `POST /api/sessions/sess-abc123/clear`
- **THEN** 系统 MUST 删除所有匹配 `tool_*_sess-abc123_*.txt` 的归档文件

#### Scenario: 清理失败不阻塞操作

- **WHEN** 归档文件清理过程发生 I/O 错误
- **THEN** 系统 MUST 在日志中记录 warning，MUST 仍然返回成功的删除/清空响应

### Requirement: 归档文件过期清理（GC）

系统 SHALL 在应用启动时执行归档文件过期清理，删除 `sessions/archive/` 下所有超过 7 天的文件。

GC MUST 同时清理新格式（`tool_{tool_name}_{session_id}_{timestamp}.txt`）和旧格式（`tool_{tool_name}_{timestamp}.txt`）的归档文件。

过期判断 MUST 基于文件的修改时间（`st_mtime`）。

GC 失败时 MUST log.warning，MUST NOT 阻塞应用启动。

#### Scenario: 启动时清理超期归档文件

- **GIVEN** `sessions/archive/` 下存在修改时间超过 7 天的归档文件
- **WHEN** 应用启动
- **THEN** 系统 MUST 删除这些超期文件

#### Scenario: 未超期文件保留

- **GIVEN** `sessions/archive/` 下存在修改时间在 7 天内的归档文件
- **WHEN** 应用启动执行 GC
- **THEN** 系统 MUST NOT 删除这些未超期文件

#### Scenario: GC 失败不阻塞启动

- **WHEN** GC 执行过程中发生 I/O 错误
- **THEN** 系统 MUST 在日志中记录 warning，应用正常启动
