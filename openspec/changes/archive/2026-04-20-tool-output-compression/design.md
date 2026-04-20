## Context

`ToolOutputBudgetMiddleware`（中间件链第 1 层）负责在上下文窗口压力增大时压缩早期工具输出。当前 `abefore_model` 直接修改 `ToolMessage.content`，不记录任何处理状态，导致两个缺陷：

1. **反复压缩**：已被截断的消息在下一轮被当作新数据处理，产生嵌套 `[省略]` 标记，信息逐轮衰减（`middleware.py:199-201`）
2. **截断后归档**：归档操作可能收到已截断的数据，且"摘要"仅取头部（`middleware.py:144-145`，`content[:budget*2//3]`），归档文件不完整

## Goals / Non-Goals

**Goals:**

- 修复 2 个已确认缺陷：反复压缩和截断后归档信息失真
- 引入压缩状态标记（`<!-- compressed: -->`），实现幂等检测
- 重构为「先归档后截断」流程，确保归档文件永远保存原始数据
- 结构化头尾摘要替代纯头部截断

**Non-Goals:**

- 不改变中间件链顺序或与其他中间件的交互
- 不改变 `safe_ratio`、`pressure_ratio`、`budgets` 等配置含义
- 不修改归档文件清理逻辑（GC 机制）
- 不修改前端（前端不解析标记）
- 不引入 LLM 语义摘要（仍使用头尾结构化截断）

## Decisions

### 决策 1：标记内嵌在 content 头部

**选择**：在 `ToolMessage.content` 头部嵌入 `<!-- compressed:method:original_length:archive_path -->` 标记

**备选**：在 `ToolMessage.additional_kwargs` 中存储压缩状态

**理由**：
- 标记内嵌在 content 中，无需修改消息数据结构，对 checkpoint 持久化完全透明
- 检测逻辑仅需一行：`content.startswith("<!-- compressed:")`
- HTML 注释格式不会干扰 LLM 理解（LLM 通常忽略 HTML 注释）
- `additional_kwargs` 方案需要修改 LangChain 消息模型，且在 checkpoint 序列化时可能丢失

### 决策 2：先归档后截断

**选择**：所有超预算内容先将原始数据写入归档文件，再执行截断/摘要替换

**备选**：仅超大内容归档（当前行为）

**理由**：
- 当前行为下，中等内容（超预算但未达 `ARCHIVE_RATIO`）直接截断，原始数据永久丢失
- 先归档确保所有原始数据可恢复，归档文件可用 `read_file` 重新读取
- 磁盘开销可忽略（归档文件已有 GC 清理机制）

### 决策 3：两个压缩方法替代单一 _compress

**选择**：拆分为 `_make_archived_content()`（超大内容）和 `_make_truncated_content()`（中等内容），统一包含标记头和归档引用

**备选**：保留单一 `_compress` 方法，增加参数控制行为

**理由**：
- 两种策略的输出结构有本质差异：archived 策略包含完整归档引用说明，truncated 策略侧重截断内容
- 拆分后每个方法职责单一，更易理解和测试
- 当前 `_archive_output` 和 `_compress` 已是两个独立方法，重构只是明确分工

### 组件层级图

```
AgentManager._build_agent()
  │
  └─ middleware: _build_middleware()
      ├─ 第 1 层：ToolOutputBudgetMiddleware  ← 本次修改
      │   ├─ abefore_model 重构：幂等检测 → 预算检测 → 先归档 → 选策略 → 更新消息
      │   ├─ _is_compressed(): 标记检测
      │   ├─ _archive_original(): 原始数据归档（新方法）
      │   ├─ _make_archived_content(): 超大内容摘要（新方法）
      │   ├─ _make_truncated_content(): 中等内容截断（新方法）
      │   └─ _make_marker(): 标记生成
      │
      ├─ 第 2 层：SummarizationMiddleware（不修改）
      ├─ 第 3 层：ContextAwareToolFilter（不修改）
      └─ 第 4 层：ToolCallLimitMiddleware（不修改）
```

### 压缩状态机

```
raw ──→ archived ──→ 终态（跳过）
  │
  └──→ truncated ──→ 终态（跳过）
```

标记格式：`<!-- compressed:{method}:{original_length}:{archive_path} -->`
- `method`：`archived`（归档+摘要引用）或 `truncated`（截断+归档引用）
- `original_length`：原始字符数
- `archive_path`：归档文件路径，归档失败时为 `none`

### 决策树变更对照

```
当前流程:
  超大(>archive_ratio) → _archive_output → 保存 + 头部截断摘要
  中等(>budget) → _compress → 头尾截断（无标记、无归档）

优化后流程:
  Step 1: 检测 "<!-- compressed:" → 已处理，跳过
  Step 2: 检测预算 → 未超，跳过
  Step 3: _archive_original() → 保存原始数据（所有超预算内容）
  Step 4a: 超大 → _make_archived_content() → 标记 + 归档引用 + 头尾摘要
  Step 4b: 中等 → _make_truncated_content() → 标记 + 归档引用 + 头尾截断
```

## Risks / Trade-offs

- **[标记可见性]** → 标记以 HTML 注释形式嵌入 content，LLM 可能偶尔引用标记内容。但标记位于内容头部，且为注释格式，实际影响极低。
- **[归档量增加]** → 所有超预算内容都归档（而非仅超大内容），归档文件量增加。但已有 7 天 GC 清理机制，磁盘开销可忽略。
- **[向下兼容]** → 无标记的旧消息（包括 checkpoint 中已持久化的历史消息）会被正常处理，与当前行为一致。处理后就带上标记，后续不再重复处理。
- **[归档失败降级]** → 归档写入失败时，标记中 `archive_path` 为 `none`，输出为标记+轻截断。原始数据丢失但不会阻塞流程。
- **[标记被截断]** → 如果 content 极短（理论上不可能，因为标记仅在超预算时添加），标记本身可能不完整。但标记长度约 100 字符，远小于任何预算阈值，不存在此风险。
- **[可观测性]** → 标记中包含原始长度和归档路径，`abefore_model` 末尾在 `changed=True` 时记录统计日志（处理数量、策略、保护组数），便于排查压缩效果。
