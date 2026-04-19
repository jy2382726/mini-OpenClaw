## MODIFIED Requirements

### Requirement: 会话 touch 活跃时间更新

系统 MUST 在每次 `/api/chat` 请求时调用 `SessionRepository.touch(session_id)` 更新会话的 `updated_at`，确保会话列表的排序反映真实的最后活跃时间。

`chat.py` 中 `bootstrap_if_missing` 和 `touch` 调用 MUST 使用 `try...except Exception` 包裹，捕获时 MUST 使用 `logger.warning()` 记录异常详情（包括 session_id 和异常信息），MUST 仍然不阻塞对话请求。

#### Scenario: 发送消息后更新活跃时间

- **GIVEN** `sessions` 表中存在该会话记录
- **WHEN** 用户在某个会话中发送消息
- **THEN** 该会话的 `updated_at` MUST 更新为当前时间，使其在会话列表中排到最前

#### Scenario: bootstrap/touch 失败时记录日志

- **WHEN** `bootstrap_if_missing` 或 `touch` 抛出异常（如 SQLite 连接失败）
- **THEN** 系统 MUST 在日志中记录 warning 级别信息（包含 session_id 和异常详情），MUST 仍然返回正常对话响应，不阻塞用户
