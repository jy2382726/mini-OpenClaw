## Purpose

定义会话元数据的数据库表结构和 SessionRepository 管理类，将元数据与会话消息存储解耦，提供 CRUD 操作和 bootstrap_if_missing 等会话管理功能。

## Requirements

### Requirement: 会话元数据数据库表

系统 SHALL 在 `checkpoints.sqlite` 中新增 `sessions` 表，存储会话元数据，包含以下字段：`session_id`（TEXT PRIMARY KEY）、`title`（TEXT NOT NULL DEFAULT 'New Chat'）、`created_at`（REAL NOT NULL）、`updated_at`（REAL NOT NULL）、`deleted_at`（REAL，软删除标记）。

系统 MUST 在 `updated_at` 字段上创建降序索引 `idx_sessions_updated_at`，优化会话列表查询性能。

系统 MUST NOT 修改 LangGraph 原生 checkpoint 表结构。

#### Scenario: 新建会话时插入元数据

- **GIVEN** `sessions` 表已初始化
- **WHEN** 前端调用 `POST /api/sessions` 创建新会话
- **THEN** 系统 MUST 在 `sessions` 表中插入一条新记录，`session_id` 为生成的唯一标识，`title` 为 "New Chat"，`created_at` 和 `updated_at` 为当前时间戳，`deleted_at` 为 NULL。使用 `INSERT OR IGNORE` 防止重复插入报错

#### Scenario: 会话列表按更新时间降序排列

- **GIVEN** `sessions` 表中存在多条未删除的会话记录
- **WHEN** 前端调用 `GET /api/sessions` 获取会话列表
- **THEN** 系统 MUST 从 `sessions` 表查询所有 `deleted_at` 为 NULL 的记录，按 `updated_at` 降序排列返回

#### Scenario: 软删除会话

- **GIVEN** `sessions` 表中存在目标会话记录
- **WHEN** 前端调用 `DELETE /api/sessions/{session_id}` 删除会话
- **THEN** 系统 MUST 将对应记录的 `deleted_at` 设为当前时间戳，而非物理删除记录。同时调用 `checkpointer.adelete_thread(session_id)` 物理删除 checkpoint 数据

### Requirement: SessionRepository 元数据管理类

系统 SHALL 提供 `SessionRepository` 类，封装会话元数据的 CRUD 操作，替代当前 `session_manager` 的元数据管理职责。

`SessionRepository` MUST 提供以下方法：
- `create(session_id)` — 创建新会话元数据记录（使用 `INSERT OR IGNORE`）
- `list()` — 查询所有未删除会话，按更新时间降序
- `get(session_id)` — 获取单个会话元数据（已删除的返回 None）
- `rename(session_id, title)` — 更新会话标题
- `touch(session_id)` — 更新会话的 `updated_at` 为当前时间
- `soft_delete(session_id)` — 软删除（设置 deleted_at）
- `bootstrap_if_missing(session_id)` — 如果会话不存在则自动创建

`SessionRepository` MUST NOT 负责消息的读写或 LangGraph 线程状态管理。

`SessionRepository` 与 `AgentManager` 共享 `aiosqlite` 连接，在 `_ensure_checkpointer()` 中一起初始化。

#### Scenario: 不存在的会话自动 bootstrap

- **GIVEN** `sessions` 表中无该 `session_id` 的记录
- **WHEN** 前端直接对不存在的 `session_id` 发送 `/api/chat` 请求
- **THEN** 系统 MUST 自动创建该 session_id 的元数据记录，确保请求正常处理

#### Scenario: 会话标题更新

- **GIVEN** `sessions` 表中存在目标会话记录
- **WHEN** 前端调用 `PUT /api/sessions/{session_id}` 更新标题为"修复登录 Bug"
- **THEN** 系统 MUST 更新 `sessions` 表中对应记录的 `title` 字段和 `updated_at` 字段

#### Scenario: 查询已删除会话返回空

- **GIVEN** `sessions` 表中存在 `deleted_at` 不为 NULL 的会话
- **WHEN** 前端调用 `GET /api/sessions` 查询会话列表
- **THEN** `deleted_at` 不为 NULL 的会话 MUST NOT 出现在结果中

### Requirement: 会话列表从数据库查询

系统 MUST 从 `sessions` 数据库表查询会话列表，不再扫描 `sessions/*.json` 文件。

注意：`session_manager.py` 仍保留 `list_sessions()` 方法（通过 `glob("*.json")` 扫描文件），但主流程已不使用该方法，仅在已废弃的 `POST /sessions/{session_id}/compress` 端点中引用。

#### Scenario: 会话列表查询性能改善

- **WHEN** 存在 100 个会话文件
- **THEN** `GET /api/sessions` MUST 通过数据库索引查询，而非逐个读取 JSON 文件解析元数据

#### Scenario: JSON 文件不存在时会话列表仍正常

- **WHEN** 某个 session_id 在 `sessions` 表中有记录但对应的 JSON 文件不存在
- **THEN** 该会话 MUST 仍出现在列表中（元数据与消息存储解耦）

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

### Requirement: "default" 会话 bootstrap 规则

系统 MUST 支持不显式创建会话直接开始聊天的 bootstrap 语义。

`ChatRequest.session_id` 默认值为 `"default"`，前端未指定 session_id 时自动使用 "default"。系统 MUST 通过 `SessionRepository.bootstrap_if_missing()` 自动创建元数据。

#### Scenario: 首次使用直接聊天

- **GIVEN** `sessions` 表中无 "default" 记录
- **WHEN** 前端未调用 `POST /api/sessions` 创建会话，直接向 `/api/chat` 发送消息，`session_id` 为 "default"
- **THEN** 系统 MUST 自动创建 "default" 会话的元数据，确保消息正常处理
