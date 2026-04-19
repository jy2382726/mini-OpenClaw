## MODIFIED Requirements

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
