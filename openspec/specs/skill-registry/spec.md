## Purpose

定义技能注册表（SkillRegistry）的元数据解析、启发式属性推断、多维度索引构建和四级技能上下文注入机制，实现 Agent Skills 标准兼容的技能管理系统。

## Requirements

### Requirement: Agent Skills 标准兼容的元数据解析

系统 SHALL 解析 SKILL.md frontmatter 中遵循 Agent Skills 标准的字段：`name`（建议提供，缺失时用目录名兜底）、`description`（建议提供，缺失时为空字符串）、`metadata`（可选，string→string 映射，存放扩展属性）。

metadata 全部可选。当技能仅提供 name + description 时，系统 MUST 通过启发式规则从 description 内容自动推断 `invocation_auto`、`trigger_patterns` 等属性，确保最低配置的技能无缝接入。

#### Scenario: 解析包含 metadata 的复杂技能

- **WHEN** 一个 SKILL.md 的 frontmatter 包含 `name`、`description` 和 `metadata`（含 `invocation_auto: "true"`、`trigger_patterns: "天气,气温"` 等键值对）
- **THEN** 系统正确解析所有字段，metadata 中的值 MUST 为 string 类型

#### Scenario: 解析仅含标准字段的简单技能

- **WHEN** 一个 SKILL.md 的 frontmatter 仅包含 `name` 和 `description`
- **THEN** 系统正确解析，`metadata` 默认为空字典，通过启发式推断自动调用权限和触发词

#### Scenario: name 缺失时用目录名兜底

- **WHEN** 一个 SKILL.md 的 frontmatter 缺少 `name` 字段
- **THEN** 系统 SHALL 使用 SKILL.md 所在目录名作为技能名（如 `skills/get_weather/SKILL.md` → name="get_weather"），不跳过该技能

#### Scenario: 外部技能直接接入

- **WHEN** 一个遵循 Agent Skills 标准的外部 SKILL.md 被放入 `skills/` 目录
- **THEN** 系统 MUST 能识别并注册该技能，未知 metadata 键 MUST 被忽略而不报错

### Requirement: 启发式属性推断

当 metadata 缺失时，系统 SHALL 从 description 内容推断以下属性：

#### Scenario: 推断自动调用权限（invocation_auto）

推断优先级：
1. `metadata.invocation_auto` 显式设置 → 使用该值
2. description 包含中文触发句式（"当...时使用"、"立即使用"、"自动触发"） → 自动调用
3. description 包含英文被动句式（"Use when asked to"） → 手动触发
4. 默认 → 自动调用

#### Scenario: 推断触发词（trigger_patterns）

推断优先级：
1. `metadata.trigger_patterns` 显式设置 → 使用该值
2. 从 description 中的引号内容（ASCII `"`、Unicode `""` `「」`）提取关键词（长度限制 1-10 字符）
3. 无引号时触发词为空列表（技能仍可正常工作）

#### Scenario: 无 metadata 技能的完整工作流

- **WHEN** 一个 SKILL.md 仅含 `name: get_weather` 和 `description: 获取天气信息`
- **THEN** 技能注册成功，`is_auto_invocable` 为 `true`（默认），`trigger_patterns` 为空列表，可正常被系统发现和注入

### Requirement: 技能注册表多维度索引

系统 SHALL 维护 `SkillRegistry`，在启动时扫描 `skills/` 目录，构建按触发词和分类的多维度索引。

触发词索引为 `dict[str, str]`（1:1 映射）。两个技能共享同一触发词时，后注册者覆盖前者。

#### Scenario: 按触发词匹配技能

- **WHEN** 用户消息包含"天气"关键词，且技能的 `trigger_patterns`（显式或推断）包含"天气"
- **THEN** 系统通过触发词索引找到该技能

#### Scenario: 无触发词的技能不参与触发匹配

- **WHEN** 技能既无显式 `trigger_patterns` 也无 description 引号内容
- **THEN** 该技能不进入触发词索引，但仍在注册表中可被其他方式发现

#### Scenario: 按分类检索技能

- **WHEN** 系统查询 "utility" 分类的技能列表
- **THEN** 返回 `metadata.categories` 包含 "utility" 的所有技能

#### Scenario: 获取 Agent 可自动调用的技能

- **WHEN** 系统需要构建系统提示中的技能索引
- **THEN** 返回 `is_auto_invocable` 为 `true` 的技能列表（含显式设置和启发式推断为 auto 的技能）

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
