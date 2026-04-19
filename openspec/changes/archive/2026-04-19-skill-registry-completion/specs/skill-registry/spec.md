## MODIFIED Requirements

### Requirement: 精简技能快照格式

系统 MUST 将技能快照生成为精简 Markdown 列表格式，仅列出可自动调用技能的名称和描述。

快照 MUST 以标题行开头："## 可用技能（按需读取 SKILL.md 获取详情）"。

每行格式为：`- {skill_name}: {description}`。当技能有触发词时（`trigger_patterns` 非空），MUST 追加 `[触发: 关键词1/关键词2]`，最多显示 3 个触发词，用 `/` 分隔。无触发词的技能 MUST NOT 追加空括号。

#### Scenario: 构建带触发词的精简快照

- **WHEN** 技能 `get_weather` 有触发词 `["天气", "气温"]`，系统需要生成技能快照用于系统提示 Zone 2
- **THEN** 快照中该行 MUST 为 `- get_weather: 获取天气信息 [触发: 天气/气温]`

#### Scenario: 无触发词技能保持原格式

- **WHEN** 技能 `dialogue-summarizer` 无触发词（`trigger_patterns` 为空列表）
- **THEN** 快照中该行 MUST 为 `- dialogue-summarizer: 对话摘要工具`，不追加任何括号

#### Scenario: 触发词超过 3 个时截断

- **WHEN** 技能有 5 个触发词 `["天气", "气温", "温度", "下雨", "晴"]`
- **THEN** 快照中 MUST 仅显示前 3 个：`[触发: 天气/气温/温度]`

#### Scenario: SKILL.md 格式错误时跳过并记录警告

- **WHEN** 一个 SKILL.md 的 frontmatter YAML 解析失败
- **THEN** 系统 SHALL 跳过该技能，MUST 在日志中记录解析警告，不影响其他技能注册

### Requirement: 四级技能上下文注入

系统 MUST 根据技能的属性（显式 metadata 或启发式推断），采用四种注入级别：

- Level 0（隐藏）：`is_auto_invocable` 为 `false`，不注入任何内容
- Level 1（索引）：`is_auto_invocable` 为 `true`，注入"名称 + 描述 + 触发词（如有）"
- Level 2（按需加载）：Agent 决定使用时，通过 `read_file` 加载完整 SKILL.md
- Level 3（预加载）：`inject_system_prompt` 为 `"true"` 时，完整 SKILL.md 注入系统提示 Zone 2

Level 3 注入 MUST 在 `build_stable_prefix()` 中实现，遍历所有 `inject_system_prompt=true` 的技能，通过 `_read_component()` 读取完整 SKILL.md 内容拼接到 Zone 2 末尾。每个预加载技能的内容 MUST 受 `MAX_COMPONENT_LENGTH`（20000 字符）截断保护。

Level 3 注入的总量 SHOULD 不超过 60000 字符（约 3 个技能），超过时 MUST log.warning 并截断。

#### Scenario: 简单技能使用 Level 1 注入

- **WHEN** 技能 `is_auto_invocable` 为 `true` 且无触发词
- **THEN** 系统提示中注入精简格式：`- skill_name: 描述`

#### Scenario: 带触发词的技能使用 Level 1 注入

- **WHEN** 技能 `is_auto_invocable` 为 `true` 且有触发词
- **THEN** 系统提示中注入：`- skill_name: 描述 [触发: 关键词1/关键词2]`

#### Scenario: 手动触发技能使用 Level 0

- **WHEN** 技能 `is_auto_invocable` 为 `false`
- **THEN** 该技能不注入系统提示，仅注册到技能索引供 `search_knowledge` 工具发现

#### Scenario: 关键技能使用 Level 3 预加载

- **WHEN** 某技能的 `inject_system_prompt` 为 `"true"`
- **THEN** 完整 SKILL.md 内容 MUST 注入到系统提示 Zone 2 中，受 MAX_COMPONENT_LENGTH 截断保护

#### Scenario: 无预加载技能时 Zone 2 不变

- **WHEN** 所有技能的 `inject_system_prompt` 为 `"false"` 或未设置
- **THEN** Zone 2 内容 MUST 与当前行为完全一致，不追加任何额外内容
