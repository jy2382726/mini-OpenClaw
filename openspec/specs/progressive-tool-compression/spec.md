## Purpose

定义基于上下文窗口比例的工具输出压缩策略，包括安全水位和紧张水位的阈值设定、当前轮次工具输出保护、多级渐进式压缩策略、工具输出自动归档等功能。此中间件为四层中间件链的第一层（ToolOutputBudgetMiddleware）。

## Requirements

### Requirement: 基于上下文窗口比例的压缩触发

系统 SHALL 以模型上下文窗口的使用比例作为工具输出压缩的触发基准，而非固定的绝对 token 数。系统 MUST 从 `config.json` 的 `llm.context_window` 读取窗口大小，默认值为 131072。

两个关键比例阈值 MUST 可配置：
- `safe_ratio`：安全水位（默认 0.25），低于此比例不压缩任何工具输出
- `pressure_ratio`：紧张水位（默认 0.45），高于此比例启用激进压缩

#### Scenario: 上下文宽裕时不触发压缩

- **WHEN** 模型上下文窗口为 131072 tokens，消息列表总 token 估算为 20000（约 15%）
- **THEN** 系统 MUST 不对任何 ToolMessage 执行压缩，所有工具输出保持完整

#### Scenario: 上下文达到安全水位时开始压缩

- **WHEN** 模型上下文窗口为 131072 tokens，消息列表总 token 估算为 40000（约 30%）
- **THEN** 系统 SHALL 对「早期轮次」的超预算 ToolMessage 执行截断压缩

#### Scenario: 上下文达到紧张水位时激进压缩

- **WHEN** 模型上下文窗口为 131072 tokens，消息列表总 token 估算为 65000（约 50%）
- **THEN** 系统 SHALL 启用激进压缩策略，仅保护最近 1 组工具输出

#### Scenario: 不同模型自动适应阈值

- **WHEN** 用户将模型从 qwen3.5-plus（128K）切换为 deepseek-chat（64K）
- **THEN** 系统的所有压缩阈值 MUST 自动按新窗口大小重新计算

### Requirement: 当前轮次工具输出保护

系统 MUST 保护「当前轮次」的工具输出不被压缩。「当前轮次」定义为最近 N 组 AIMessage(tool_calls) → ToolMessage 序列。N 的值 MUST 根据上下文压力动态调整：
- 上下文 < safe_ratio：N = 全部（不压缩任何输出）
- safe_ratio ≤ 上下文 < pressure_ratio：N = 最近 3 组
- 上下文 ≥ pressure_ratio：N = 最近 1 组

保护逻辑通过 `_get_protected_tool_ids()` 方法实现，从消息末尾向前扫描 AIMessage(tool_calls) 分组。

#### Scenario: 多步工具调用链中早期输出被保护

- **WHEN** Agent 连续执行 3 次工具调用（terminal → read_file → terminal），上下文使用比例为 30%
- **THEN** 系统 MUST 保持最近 3 组工具输出的完整性，仅压缩更早的工具输出

#### Scenario: 激进模式下仅保护最新一步

- **WHEN** 上下文使用比例超过 pressure_ratio，Agent 刚执行完第 5 次工具调用
- **THEN** 系统 MUST 仅保持第 5 次工具输出的完整性，第 1-4 次的超预算输出均被压缩

### Requirement: 渐进式压缩策略

系统 MUST 支持多级压缩策略，根据上下文压力递进执行：

| 级别 | 触发条件 | 行为 |
|------|---------|------|
| 0 | < safe_ratio | 不处理 |
| 1 | safe_ratio ~ pressure_ratio | 头尾截断（头 2/3 + 尾 1/3） |
| 2 | ≥ pressure_ratio | 短截断（头 1/2 + 尾 1/4） |

压缩 MUST 保留工具名称和截断标注，格式为 `{head}\n...[省略约 N 字符]...\n{tail}`。

#### Scenario: 安全水位使用标准截断

- **WHEN** 上下文使用比例为 30%，一条早期 terminal 输出为 12000 字符（预算 8000）
- **THEN** 系统截断为前 5333 字符 + 尾 2666 字符，中间插入省略标注

#### Scenario: 紧张水位使用短截断

- **WHEN** 上下文使用比例为 50%，一条早期 terminal 输出为 12000 字符（预算 8000）
- **THEN** 系统截断为前 4000 字符 + 尾 2000 字符，中间插入省略标注

### Requirement: 工具输出自动归档

当单条工具输出超过上下文窗口的 5%（`ARCHIVE_RATIO = 0.05`）时，系统 SHALL 将完整输出归档到文件，ToolMessage 中仅保留截断摘要和文件路径引用。

归档文件 MUST 保存到 `sessions/archive/` 目录，文件名格式为 `tool_{tool_name}_{session_id}_{timestamp}.txt`，其中 `session_id` 通过 `get_config()` 从 LangGraph 上下文变量获取 `configurable.thread_id`。不在 graph 上下文中时 fallback 为 `"unknown"`。

`_archive_output()` 方法 MUST 包裹 try/except：写入成功时返回路径引用 + 截断摘要；写入失败（磁盘满、权限不足等）时 MUST log.warning 并返回纯截断结果（不包含归档路径），MUST NOT 向上抛出异常。

归档操作在截断之前检查（先归档再截断）。

#### Scenario: 超大输出自动归档

- **WHEN** 一条 terminal 输出超过上下文窗口的 5%，且 session_id 为 "sess-abc123"
- **THEN** 系统将完整输出保存到 `sessions/archive/tool_terminal_sess-abc123_1713000000.txt`，ToolMessage content 替换为 `[完整输出已归档到 sessions/archive/tool_terminal_sess-abc123_1713000000.txt，可用 read_file 查看]\n{截断摘要}`

#### Scenario: 归档文件可被 Agent 重新读取

- **WHEN** Agent 需要查看已归档的完整输出
- **THEN** Agent MUST 能通过 `read_file("sessions/archive/tool_terminal_sess-abc123_1713000000.txt")` 读取完整内容

#### Scenario: 归档写入失败时安全降级

- **WHEN** `sessions/archive/` 目录不可写（权限不足、磁盘满）
- **THEN** 系统 MUST 仅执行截断压缩，不归档完整输出，MUST 在日志中记录 warning 级别警告，MUST NOT 向上抛出异常

#### Scenario: session_id 获取

- **WHEN** `abefore_model` 被调用
- **THEN** 系统 MUST 通过 `get_config()` 从 LangGraph 上下文变量获取 `configurable.thread_id` 作为 session_id，传递给 `_archive_output()`。获取失败时 fallback 为 `"unknown"`

### Requirement: 上下文窗口大小配置

系统 SHALL 在 `config.json` 的 `llm` 段中支持 `context_window` 字段（整数，单位 token）。前端 MUST 提供模型到上下文窗口的映射表（`CONTEXT_WINDOWS`），用户选择模型时自动填入，自定义模型可手动输入。

#### Scenario: 用户选择 qwen3.5-plus 模型

- **WHEN** 用户在前端设置页面选择 `qwen3.5-plus`
- **THEN** 前端 MUST 自动将 `context_window` 设为 131072

#### Scenario: 用户使用自定义模型

- **WHEN** 用户在前端设置页面选择「自定义」并输入模型名称
- **THEN** 前端 MUST 允许用户手动输入 `context_window` 值

### Requirement: SummarizationMiddleware 触发阈值联动

`SummarizationMiddleware` 的 trigger_tokens MUST 基于上下文窗口比例计算，比例固定为 0.6（`int(context_window * 0.6)`）。

#### Scenario: 128K 模型的摘要触发点

- **WHEN** 模型上下文窗口为 131072 tokens
- **THEN** SummarizationMiddleware 的 trigger_tokens MUST 为 `131072 × 0.6 = 78643`（取整）

#### Scenario: 64K 模型的摘要触发点

- **WHEN** 模型上下文窗口为 65536 tokens
- **THEN** SummarizationMiddleware 的 trigger_tokens MUST 为 `65536 × 0.6 = 39321`（取整）

### Requirement: 归档文件级联清理

系统 MUST 在删除或清空会话时，级联清理 `sessions/archive/` 目录下该 session_id 关联的归档文件。

`DELETE /api/sessions/{session_id}` 端点和 `POST /api/sessions/{session_id}/clear` 端点 MUST 在清理 checkpoint 后，调用 `_cleanup_session_archives(session_id)` 删除所有匹配 `tool_*_{session_id}_*.txt` 的文件。

清理失败时 MUST log.warning，MUST NOT 阻塞删除/清空操作。

#### Scenario: 删除会话时清理归档文件

- **GIVEN** `sessions/archive/` 下存在 `tool_terminal_sess-abc123_*.txt` 归档文件
- **WHEN** 用户调用 `DELETE /api/sessions/sess-abc123`
- **THEN** 系统 MUST 删除所有匹配 `tool_*_sess-abc123_*.txt` 的归档文件

#### Scenario: 清空会话时清理归档文件

- **GIVEN** `sessions/archive/` 下存在 `tool_terminal_sess-abc123_*.txt` 归档文件
- **WHEN** 用户调用 `POST /api/sessions/sess-abc123/clear`
- **THEN** 系统 MUST 删除所有匹配 `tool_*_sess-abc123_*.txt` 的归档文件

#### Scenario: 清理失败不阻塞操作

- **WHEN** 归档文件清理过程发生 I/O 错误
- **THEN** 系统 MUST 在日志中记录 warning，MUST 仍然返回成功的删除/清空响应

### Requirement: 归档文件过期清理（GC）

系统 SHALL 在应用启动时执行归档文件过期清理，删除 `sessions/archive/` 下所有超过 7 天的文件。

GC MUST 同时清理新格式（`tool_{tool_name}_{session_id}_{timestamp}.txt`）和旧格式（`tool_{tool_name}_{timestamp}.txt`）的归档文件。

过期判断 MUST 基于文件的修改时间（`st_mtime`）。

GC 失败时 MUST log.warning，MUST NOT 阻塞应用启动。

#### Scenario: 启动时清理超期归档文件

- **GIVEN** `sessions/archive/` 下存在修改时间超过 7 天的归档文件
- **WHEN** 应用启动
- **THEN** 系统 MUST 删除这些超期文件

#### Scenario: 未超期文件保留

- **GIVEN** `sessions/archive/` 下存在修改时间在 7 天内的归档文件
- **WHEN** 应用启动执行 GC
- **THEN** 系统 MUST NOT 删除这些未超期文件

#### Scenario: GC 失败不阻塞启动

- **WHEN** GC 执行过程中发生 I/O 错误
- **THEN** 系统 MUST 在日志中记录 warning，应用正常启动
