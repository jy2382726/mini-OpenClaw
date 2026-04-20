## Context

`SummarizationMiddleware`（中间件链第 2 层）负责在对话接近上下文窗口上限时自动生成摘要。当前存在 4 个已确认缺陷：

1. `config.json` 中 `trigger_tokens: 8000` 被代码忽略，硬编码 `context_window * 0.6`（`agent.py:174-175`）
2. `trim_tokens_to_summarize` 使用 LangChain 默认值 4000，未传参（`agent.py:176-181`），128K 窗口下仅 6% 待摘要内容发给 LLM
3. Zone 3 动态内容作为 SystemMessage 注入 messages（`agent.py:576-577`），被摘要中间件无差别处理
4. 使用英文通用 `DEFAULT_SUMMARY_PROMPT`，与项目中文工具密集场景不匹配

手动摘要（`agent.py:_generate_checkpoint_summary`）同样使用 `DEFAULT_SUMMARY_PROMPT`，需同步更新以保持一致。

## Goals / Non-Goals

**Goals:**

- 修复 4 个已确认缺陷，提升长对话场景下的摘要质量
- 通过 `ContextAwareSummarizationMiddleware` 子类保护 SystemMessage
- 配置驱动所有阈值（trigger_ratio/trim_ratio），支持运行时调整
- 自定义中文摘要提示词，遵循 Markdown-as-config 模式
- 在前端压缩按钮上实时显示上下文使用率，超过 80% 禁用发送按钮强制压缩

**Non-Goals:**

- 不修改 LangChain 框架源码（通过子类扩展）
- 不修改 `state["messages"]` 数据结构
- 不修改 Checkpoint 持久化逻辑
- 不改变摘要触发的整体时机（仍为 60% 上下文窗口，但可配置）
- 不实现 SSE 实时推送 token 使用量（按需拉取，消息变更时刷新）

## Decisions

### 决策 1：子类化 SummarizationMiddleware 保护 SystemMessage

**选择**：创建 `ContextAwareSummarizationMiddleware` 继承 `SummarizationMiddleware`，重写 `abefore_model`

**备选**：在 `abefore_model` 中用 decorator 包装过滤 SystemMessage

**理由**：
- 子类方案职责清晰，保护逻辑集中在一个类中
- 无 SystemMessage 时直接 `super()` 回退，零开销
- 不修改框架代码，保持升级兼容性

### 决策 2：配置比例优先 + 绝对值覆盖

**选择**：`trigger_ratio`（默认 0.6）和 `trim_ratio`（默认 0.30）为主配置，`trigger_tokens`/`trim_tokens` 绝对值覆盖

**备选**：仅支持绝对值配置

**理由**：
- 比例模式自动适配不同上下文窗口大小（32K/128K/1M）
- 绝对值覆盖兼容旧配置（`trigger_tokens: 8000` 可直接生效）
- 向下兼容：无新配置字段时行为与当前一致

### 决策 3：Markdown 文件存放摘要提示词

**选择**：`workspace/summary_prompt.md`，通过 `config.json` 的 `summary_prompt_file` 可切换

**备选**：在 config.json 中内嵌提示词字符串

**理由**：
- 遵循项目 Markdown-as-config 设计模式
- 用户可直接编辑 Markdown 文件，无需改代码
- 支持多版本提示词切换

### 决策 4：手动摘要同步使用自定义提示词

**选择**：`_generate_checkpoint_summary` 从 `DEFAULT_SUMMARY_PROMPT` 切换为自定义提示词加载逻辑

**备选**：手动摘要保持使用 `DEFAULT_SUMMARY_PROMPT`

**理由**：
- 现有规范要求"手动摘要与自动摘要输出格式一致"
- 使用不同提示词会导致手动/自动摘要结构不统一
- 实现成本低：复用 `_load_summary_prompt` 方法

### 决策 5：增强现有 Token API 返回上下文使用率

**选择**：在 `/tokens/session/{id}` 响应中新增 `context_window` 和 `usage_ratio` 字段

**备选**：新增独立的 `/tokens/session/{id}/usage` 端点

**理由**：
- 现有 API 已返回 `total_tokens`，新增两个字段是自然扩展
- 避免新增端点的维护成本
- `context_window` 从 `get_context_window()` 读取（config.json `llm.context_window`）

### 决策 6：消息变更时按需拉取使用率

**选择**：前端在消息列表变更后调用 `getSessionTokenCount` 获取使用率，存入 store

**备选**：后端通过 SSE 事件主动推送 token 使用量

**理由**：
- 按需拉取实现简单，无需修改后端 SSE 事件格式
- 消息变更频率低（用户发送/接收完成后），不需要实时推送
- 与现有 Sidebar 中 token 显示的获取方式一致

### 组件层级图

```
AgentManager._build_agent()
  │
  └─ middleware: _build_middleware()
      ├─ 第 1 层：ToolOutputBudgetMiddleware
      ├─ 第 2 层：ContextAwareSummarizationMiddleware  ← 替换 SummarizationMiddleware
      │   ├─ 配置：trigger_ratio / trim_ratio / summary_prompt
      │   ├─ abefore_model：提取 SystemMessage → 父类摘要 → 重新注入
      │   └─ 父类 SummarizationMiddleware 核心逻辑不变
      ├─ 第 3 层：ContextAwareToolFilter
      ├─ 第 4 层：ToolCallLimitMiddleware
      ├─ 第 5 层：MemoryMiddleware（规划中）
      └─ 第 6 层：FilesystemFileSearchMiddleware

AgentManager._generate_checkpoint_summary()
  └─ 使用 _load_summary_prompt() 加载提示词 ← 新增，与第 2 层一致
```

### 前端上下文使用率数据流

```
用户发送/接收消息完成（isStreaming 变为 false）
  │
  ├─ store: useEffect([sessionId, messages.length, isStreaming])
  │   ├─ isStreaming=true → 跳过（避免逐 token 触发）
  │   └─ isStreaming=false && messages.length > 0
  │       └─ GET /tokens/session/{id}
  │           └─ 返回 { total_tokens, context_window, usage_ratio }
  │               └─ store.contextUsage = { ratio, totalTokens, contextWindow }
  │
  ├─ ChatInput: 压缩按钮
  │   └─ 显示 "压缩 (45%)" / 超过 60% 变橙色 / 超过 80% 变红色
  │
  └─ ChatInput: 发送按钮
      └─ disabled = usage_ratio > 0.8 && !isCompressing
          超过 80% 时显示提示："上下文空间不足，请先压缩对话"
          注：当前 token 计数为下界估算，精确计算将作为后续独立提案
```

### 配置数据流

```
config.json → _build_middleware()
  │
  ├─ summarization.trigger_ratio → trigger_tokens = int(context_window * ratio)
  ├─ summarization.trigger_tokens → 覆盖 trigger_tokens（如非 null）
  ├─ summarization.trim_ratio → trim_tokens = int(context_window * ratio)
  ├─ summarization.trim_tokens → 覆盖 trim_tokens（如非 null）
  └─ summarization.summary_prompt_file → _load_summary_prompt()
       ├─ 优先：配置指定路径
       ├─ 其次：workspace/summary_prompt.md
       └─ 兜底：DEFAULT_SUMMARY_PROMPT_ZH 内置常量
```

## Risks / Trade-offs

- **[父类返回格式已验证]** → `SummarizationMiddleware.abefore_model` 返回 `[RemoveMessage(REMOVE_ALL_MESSAGES), HumanMessage(summary), ...preserved_messages]`（summarization.py:360-366）。`ContextAwareSummarizationMiddleware` 的 SystemMessage 重新注入逻辑基于此格式实现，`insert_pos = 2`（RemoveMessage[0] + summary[1] 之后）正确。
- **[trim_ratio=0.30 可能超出辅助模型窗口]** → 辅助模型 `qwen3.5-flash` 上下文 128K，30% 即 38400 token + 摘要 prompt ~1K + 输出 ~2K ≈ 41K，远低于 128K 上限。较小窗口模型需通过 `trim_tokens` 绝对值覆盖。
- **[SystemMessage 保留策略]** → 当前方案保留所有历史 SystemMessage。长对话中可能累积大量 Zone 3 消息（每轮一条）。后续可优化为仅保留最近 N 条 SystemMessage，但当前作为安全保守策略先全量保留。
- **[摘要提示词变更]** → 从 4 节英文结构切换为 8 节中文结构，已有 checkpoint 中的摘要格式会不一致。但摘要本质是对话历史的压缩，新旧格式混存不影响 Agent 理解。
