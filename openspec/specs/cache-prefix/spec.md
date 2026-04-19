## Purpose

定义三段式系统提示缓存前缀架构，将系统提示分为极稳定层、低频变化层和高频变化层，实现动态内容与静态前缀的分离，最大化 KV-cache 命中率。

## Requirements

### Requirement: 三段式系统提示缓存前缀

系统 MUST 将系统提示分为三个 Cache Zone：

- Cache Zone 1（极稳定层）：SOUL.md + IDENTITY.md + USER.md，几乎不变的内容
- Cache Zone 2（低频变化层）：AGENTS.md + 精简技能摘要（含触发词格式）+ Level 3 预加载技能完整内容
- Cache Zone 3（高频变化层）：动态记忆注入 + 任务状态

Zone 之间通过 `<!-- Zone N: xxx -->` HTML 注释标记分隔，便于调试和缓存边界识别。

Zone 2 中 Level 3 预加载技能内容 MUST 追加在技能摘要列表之后，使用 `### 预加载技能: {skill_name}` 作为分隔标题。预加载总量超过 60000 字符时 MUST log.warning 并截断。

`build_stable_prefix()` 函数生成 Zone 1 + Zone 2 的稳定前缀。
`build_dynamic_prefix()` 函数生成 Zone 3 的动态内容。

#### Scenario: Zone 1 和 Zone 2 内容稳定

- **WHEN** 连续两次请求之间 workspace 文件和技能列表未变化
- **THEN** Zone 1 + Zone 2 的系统提示前缀 MUST 完全一致，便于 KV-cache 命中

#### Scenario: 有预加载技能时 Zone 2 包含完整内容

- **WHEN** 某技能设置了 `inject_system_prompt: "true"`，技能列表包含该技能
- **THEN** Zone 2 MUST 在技能摘要列表之后包含该技能的完整 SKILL.md 内容

#### Scenario: 无预加载技能时 Zone 2 不变

- **WHEN** 所有技能均未设置 `inject_system_prompt: "true"`
- **THEN** Zone 2 内容 MUST 与变更前完全一致，仅包含 AGENTS.md + 技能摘要列表

#### Scenario: Zone 3 每次请求可能变化

- **WHEN** 用户发送新消息触发请求
- **THEN** Zone 3 的动态记忆注入和任务状态 MUST 根据当前查询实时更新

### Requirement: 动态内容与静态前缀分离

系统 MUST 将 MEMORY.md 全文注入和 RAG 检索结果从系统提示中移除，改为通过 `UnifiedMemoryRetriever` 检索后作为 SystemMessage 注入在用户消息之前。

`build_system_prompt()` 中的 `rag_mode` 参数保留但无实际作用，所有模式均通过统一记忆检索接口处理。

#### Scenario: MEMORY.md 不再注入到系统提示

- **WHEN** 非 RAG 模式下构建系统提示
- **THEN** 系统 MUST NOT 将 MEMORY.md 全文拼入系统提示，而是通过统一记忆检索按需注入

#### Scenario: 检索结果注入为 SystemMessage

- **WHEN** 检索到相关记忆条目
- **THEN** 检索结果 MUST 作为 SystemMessage 注入在用户消息之前（通过 `messages.insert(len(messages) - 1, SystemMessage(...))`），MUST NOT 作为 AssistantMessage 注入到历史末尾

### Requirement: 系统提示构建确定性

系统 MUST 确保相同输入下系统提示的序列化结果完全一致（字符级别），以最大化缓存命中率。

构建使用模板化拼接（`_TEMPLATE.format(...)` 带 `{soul}`/`{identity}` 等占位符），非字符串拼接。

每个组件读取通过 `_read_component(path, label)` 函数实现，包含以下安全措施：
- 文件不存在时返回空字符串，不阻塞构建
- 组件长度超过 `MAX_COMPONENT_LENGTH`（20000 字符）时截断
- 多编码回退策略（UTF-8 → GBK → latin-1）

#### Scenario: 相同输入产生相同输出

- **WHEN** 两次调用 `build_system_prompt()` 时 workspace 文件和技能列表完全相同
- **THEN** 两次调用返回的字符串 MUST 逐字符一致

#### Scenario: workspace 文件缺失时跳过对应 Zone

- **WHEN** Zone 1 或 Zone 2 对应的 workspace 文件不存在或读取失败
- **THEN** 系统 SHALL 跳过该 Zone 的内容，使用空字符串替代，不阻塞系统提示构建

#### Scenario: 组件长度超限截断

- **WHEN** 某个 workspace 文件内容超过 20000 字符
- **THEN** 系统 SHALL 截断该组件内容至 20000 字符，MUST 在截断处添加省略标注

### Requirement: 任务状态更新指引

当系统存在活跃的 TaskState（`has_active_steps=True`）时，Zone 3 动态前缀 MUST 包含 `_TASK_UPDATE_GUIDANCE` 常量内容，引导 Agent 正确使用 `update_task` 工具更新任务状态。

#### Scenario: 有活跃任务时注入指引

- **WHEN** 当前会话有活跃 TaskState，`has_active_steps` 为 true
- **THEN** Zone 3 MUST 包含任务更新工具使用指引

#### Scenario: 无活跃任务时不注入指引

- **WHEN** 当前会话无活跃 TaskState
- **THEN** Zone 3 MUST NOT 包含任务更新工具使用指引
