## MODIFIED Requirements

### Requirement: 任务状态通过 state_schema 嵌入

系统 MUST 通过 `create_agent` 的 `state_schema` 参数将 `TaskState` 嵌入 Agent 状态，与 `middleware` 同时使用（通过 `_resolve_schema` 自动合并）。

系统 MUST 在每次 agent.astream/ainvoke 调用时传入 `config={"configurable": {"thread_id": session_id}}`，激活 InMemorySaver checkpointer 的状态持久化。

系统 MUST 在创建 TaskState 后将其写入 `AgentCustomState.task_state` 字段，而非仅作为局部变量使用。

#### Scenario: TaskState 自动持久化

- **WHEN** Agent 在一次请求中通过 `update_task` 工具更新了 TaskState 的某个步骤状态
- **THEN** 更新后的 TaskState MUST 通过 checkpointer 自动持久化，下次同一 session 请求时自动恢复

#### Scenario: TaskState 跨请求恢复

- **WHEN** 用户在同一 session 中发送后续消息，且前一次请求中有活跃的 TaskState
- **THEN** 系统 MUST 从 checkpoint 恢复已有 TaskState，优先使用恢复的状态而非重新创建

#### Scenario: 新任务追加步骤

- **WHEN** 用户在有活跃 TaskState 的 session 中发送包含任务性动词的新消息
- **THEN** 系统 MUST 将新目标作为新步骤追加到已有 TaskState 的 steps 列表，而非覆盖整个 TaskState

#### Scenario: state_schema 与 middleware 不冲突

- **WHEN** 同时配置了 `state_schema=AgentCustomState` 和 `middleware=[...]` 参数
- **THEN** 两者 MUST 正常工作，`_resolve_schema` 自动合并两者的 schema 定义

## ADDED Requirements

### Requirement: Agent 主动更新任务状态

系统 MUST 提供 `update_task` 工具，允许 Agent 在执行过程中主动更新 TaskState。工具 MUST 支持以下操作：

- `add_step`：添加新步骤到 steps 列表
- `update_step`：更新已有步骤的 status 和 result_summary
- `add_artifact`：添加已创建/修改的文件路径
- `add_blocker`：添加阻塞项
- `add_decision`：添加关键决策记录

工具 MUST 在执行后将更新写入 `AgentCustomState.task_state`，触发 checkpointer 持久化。

#### Scenario: Agent 添加步骤

- **WHEN** Agent 调用 `update_task` 工具，action 为 `add_step`，description 为"创建数据库模型"
- **THEN** 系统 MUST 在 TaskState.steps 列表末尾追加一个新步骤，status 为 `in_progress`

#### Scenario: Agent 完成步骤

- **WHEN** Agent 调用 `update_task` 工具，action 为 `update_step`，step_index 为 0，status 为 `completed`，result_summary 为"模型已创建"
- **THEN** 系统 MUST 更新对应步骤的 status 和 result_summary

#### Scenario: Agent 记录产物

- **WHEN** Agent 通过 write_file 工具创建了文件 `backend/models.py`，随后调用 `update_tool` 工具，action 为 `add_artifact`，path 为 `backend/models.py`
- **THEN** 系统 MUST 将该路径添加到 TaskState.artifacts 列表

### Requirement: 任务状态更新指引注入

当存在活跃 TaskState 且包含未完成步骤时，系统 MUST 在 Zone 3 动态内容中注入任务状态更新指引，提示 Agent 在完成关键操作后使用 `update_task` 工具更新状态。

#### Scenario: 指引注入

- **WHEN** 当前会话有活跃 TaskState，包含至少一个 `in_progress` 步骤
- **THEN** Zone 3 注入内容中 MUST 包含任务状态更新指引，说明可用的 update_task 操作

#### Scenario: 无活跃任务时不注入指引

- **WHEN** 当前会话没有活跃的 TaskState 或所有步骤已 completed
- **THEN** 不注入任务状态更新指引
