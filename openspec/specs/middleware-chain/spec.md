## Purpose

定义三段式系统提示缓存前缀架构，将系统提示分为极稳定层、低频变化层和高频变化层，实现动态内容与静态前缀的分离，最大化 KV-cache 命中率。

## Requirements

### Requirement: 三段式系统提示缓存前缀

系统 MUST 将系统提示分为三个 Cache Zone：

- Cache Zone 1（极稳定层）：SOUL.md + IDENTITY.md + USER.md，几乎不变的内容
- Cache Zone 2（低频变化层）：AGENTS.md + 精简技能摘要（由 SkillRegistry.build_compact_snapshot 生成）
- Cache Zone 3（高频变化层）：动态记忆注入 + 任务状态

#### Scenario: Zone 1 和 Zone 2 内容稳定

- **WHEN** 连续两次请求之间 workspace 文件和技能列表未变化
- **THEN** Zone 1 + Zone 2 的系统提示前缀 MUST 完全一致，便于 KV-cache 命中

#### Scenario: Zone 3 每次请求可能变化

- **WHEN** 用户发送新消息触发请求
- **THEN** Zone 3 的动态记忆注入和任务状态 MUST 根据当前查询实时更新

### Requirement: 动态内容与静态前缀分离

系统 MUST 将 MEMORY.md 全文注入和 RAG 检索结果从系统提示中移除，改为通过 `UnifiedMemoryRetriever` 检索后作为 system 消息注入在用户消息之前。

#### Scenario: MEMORY.md 不再注入到系统提示

- **WHEN** 非 RAG 模式下构建系统提示
- **THEN** 系统 MUST NOT 将 MEMORY.md 全文拼入系统提示第 6 层，而是通过统一记忆检索按需注入

#### Scenario: RAG 结果不再注入为 assistant 消息

- **WHEN** RAG 模式下检索到结果
- **THEN** 检索结果 MUST 作为 SystemMessage 注入在用户消息之前，MUST NOT 作为 AssistantMessage 注入到历史末尾

### Requirement: 系统提示构建确定性

系统 MUST 确保相同输入下系统提示的序列化结果完全一致（字符级别），以最大化缓存命中率。

#### Scenario: 相同输入产生相同输出

- **WHEN** 两次调用 `build_system_prompt()` 时 workspace 文件和技能列表完全相同
- **THEN** 两次调用返回的字符串 MUST 逐字符一致

#### Scenario: 使用模板化拼接

- **WHEN** 构建系统提示
- **THEN** 系统 MUST 使用带变量占位符的模板，而非字符串拼接，确保拼接顺序和格式的确定性
