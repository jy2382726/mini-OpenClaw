## ADDED Requirements

### Requirement: 压缩幂等检测

系统 SHALL 在 `ToolOutputBudgetMiddleware.abefore_model` 中对每条待处理的 `ToolMessage` 执行幂等检测。已被压缩处理的消息 MUST 永远跳过，不再进入压缩流程。

检测方式：检查 `content` 是否以 `<!-- compressed:` 开头。是则跳过该消息。

标记格式 MUST 为 `<!-- compressed:{method}:{original_length}:{archive_path} -->`，其中：
- `method`：压缩方式（`archived` 或 `truncated`）
- `original_length`：原始字符数（整数）
- `archive_path`：归档文件相对路径，归档失败时为 `none`

#### Scenario: 已处理消息被跳过

- **WHEN** 一条 ToolMessage 的 content 以 `<!-- compressed:truncated:15000:sessions/archive/tool_terminal_xxx.txt -->` 开头
- **THEN** 系统 MUST 跳过该消息，不执行任何压缩操作

#### Scenario: 旧消息无标记时正常处理

- **WHEN** 一条 ToolMessage 的 content 不以 `<!-- compressed:` 开头，且超过预算
- **THEN** 系统 MUST 正常执行压缩流程，并在替换内容中嵌入标记

#### Scenario: 标记检测不影响未超预算消息

- **WHEN** 一条 ToolMessage 的 content 以 `<!-- compressed:` 开头，但未超过预算
- **THEN** 系统 MUST 仍然跳过该消息（标记已表明被处理过）

### Requirement: 先归档后截断

系统 SHALL 在截断任何工具输出之前，先将原始内容保存到归档文件。此流程 MUST 适用于所有超预算内容（不限于超大输出）。

新增 `_archive_original()` 方法，负责将原始 content 写入归档文件。写入成功时返回归档文件相对路径，写入失败时返回 `None`。

截断流程中，归档 MUST 在内容替换之前执行。归档失败时系统 MUST 降级为仅加标记的轻截断（标记中 `archive_path` 为 `none`），MUST NOT 向上抛出异常。

#### Scenario: 中等输出先归档再截断

- **WHEN** 一条 terminal 输出为 12000 字符（预算 8000），未达 `ARCHIVE_RATIO`
- **THEN** 系统 MUST 先将原始 12000 字符保存到归档文件，再执行截断替换

#### Scenario: 归档失败时安全降级

- **WHEN** 归档文件写入失败（磁盘满、权限不足）
- **THEN** 系统 MUST 仍执行截断替换，标记中 `archive_path` 字段为 `none`，MUST NOT 向上抛出异常

#### Scenario: 归档文件可被 Agent 重新读取

- **WHEN** Agent 需要查看已归档的原始输出
- **THEN** Agent MUST 能通过 `read_file` 工具读取归档文件中的完整原始内容

### Requirement: 压缩处理统计日志

系统 SHALL 在 `abefore_model` 执行压缩后记录统计日志，包含已处理消息数、压缩策略和保护组数。

日志级别 MUST 为 `info`，格式为 `"工具输出压缩: {count} 条消息已处理, 策略={strategy}, 保护={protect_recent}组"`。

#### Scenario: 执行压缩后记录日志

- **WHEN** `abefore_model` 完成至少一条消息的压缩处理
- **THEN** 系统 MUST 记录 info 级别日志，包含处理数量、策略和保护范围

#### Scenario: 未执行压缩时不记录

- **WHEN** `abefore_model` 未压缩任何消息（`changed = False`）
- **THEN** 系统 MUST NOT 记录压缩统计日志

## MODIFIED Requirements

### Requirement: 渐进式压缩策略

系统 MUST 支持多级压缩策略，根据上下文压力递进执行：

| 级别 | 触发条件 | 行为 |
|------|---------|------|
| 0 | < safe_ratio | 不处理 |
| 1 | safe_ratio ~ pressure_ratio | 头尾截断（头 2/3 + 尾 1/3） |
| 2 | ≥ pressure_ratio | 短截断（头 1/2 + 尾 1/4） |

压缩输出 MUST 包含三个部分：
1. 压缩标记头（`<!-- compressed: -->`）
2. 归档引用说明（如有归档路径）
3. 结构化头尾摘要（头部 + 精确省略量 + 尾部）

超大输出（> `ARCHIVE_RATIO`）MUST 使用 `_make_archived_content()` 方法，生成约 500 token 头部 + 200 token 尾部的结构化摘要。

中等输出（> budget 但 ≤ `ARCHIVE_RATIO`）MUST 使用 `_make_truncated_content()` 方法，按策略分配头尾预算。

#### Scenario: 安全水位使用标准截断

- **WHEN** 上下文使用比例为 30%，一条早期 terminal 输出为 12000 字符（预算 8000）
- **THEN** 系统截断为标记头 + 归档引用 + 前 5333 字符 + 精确省略量 + 尾 2666 字符

#### Scenario: 紧张水位使用短截断

- **WHEN** 上下文使用比例为 50%，一条早期 terminal 输出为 12000 字符（预算 8000）
- **THEN** 系统截断为标记头 + 归档引用 + 前 4000 字符 + 精确省略量 + 尾 2000 字符

#### Scenario: 超大输出使用归档摘要

- **WHEN** 一条 terminal 输出超过上下文窗口的 5%
- **THEN** 系统生成标记头（method=archived）+ 归档引用说明 + 头部（~500 token）+ 精确省略量 + 尾部（~200 token）

## REMOVED Requirements

### Requirement: 工具输出自动归档

**Reason**: 归档逻辑被「先归档后截断」流程替代。新流程中归档不再限于超大输出，而是所有超预算内容都先归档原始数据。

**Migration**: `_archive_output()` 方法替换为 `_archive_original()` + `_make_archived_content()` + `_make_truncated_content()`，归档文件格式不变（`tool_{tool_name}_{session_id}_{timestamp}.txt`），归档目录不变（`sessions/archive/`），GC 清理逻辑不变。
