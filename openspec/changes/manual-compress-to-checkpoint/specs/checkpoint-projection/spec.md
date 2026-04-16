## MODIFIED Requirements

### Requirement: Feature Flag 控制投影切换

系统 MUST 通过 `features.checkpoint_history_read` 配置项控制 `/history` 和 `/messages` 的数据来源。

- 当 `checkpoint_history_read` 为 `false`（默认）时，继续从 `session_manager` 的 JSON 文件读取
- 当 `checkpoint_history_read` 为 `true` 时，从 checkpoint projection 读取

手动摘要完成后，前端 MUST 自动刷新 `/history` 和 `/api/tokens/session/{id}` 以反映摘要后的消息状态。

#### Scenario: Flag 关闭时使用 JSON 数据源

- **WHEN** `checkpoint_history_read` 为 false，前端请求 `/api/sessions/{id}/history`
- **THEN** 系统 MUST 返回 JSON 文件中的消息数据，行为与迁移前完全一致

#### Scenario: Flag 开启时使用 checkpoint 投影

- **WHEN** `checkpoint_history_read` 为 true，前端请求 `/api/sessions/{id}/history`
- **THEN** 系统 MUST 调用 `CheckpointHistoryService.project(thread_id)` 返回投影结果

#### Scenario: 手动摘要后 history 自动刷新

- **WHEN** 前端手动摘要成功完成后
- **THEN** 前端 MUST 自动调用 `/api/sessions/{id}/history` 和 `/api/tokens/session/{id}` 刷新聊天记录和 token 统计，显示摘要后的消息
