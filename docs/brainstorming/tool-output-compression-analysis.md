# 工具输出压缩：需求分析报告

> 基于 2026-04-12 上下文工程优化分析报告、当前 `ToolOutputBudgetMiddleware` 实现缺陷、以及 LangChain 中间件钩子时序的综合分析。

---

## 一、根本目的

工具输出压缩的根本目的**不是节省存储**，而是**在上下文窗口有限的前提下，为 Agent 的当前推理保留足够的信息质量**。

具体来说：

1. **延缓上下文溢出**：大型工具输出（如 terminal 执行日志、文件内容）是上下文膨胀的最大来源。一个 `ls -laR` 可能返回上万字符，单条消息就吃掉几千 token。
2. **延缓摘要触发**：`SummarizationMiddleware` 在 token 超阈值时触发摘要，摘要本身消耗 LLM 调用（成本+延迟）。前置压缩工具输出可以降低 token 总量，推迟摘要触发。
3. **提升摘要质量**：当摘要不得不触发时，如果消息列表中充斥着大量工具原始输出，摘要 LLM 的注意力被垃圾数据分散，关键决策信息反而容易被忽略。

**一句话总结**：工具输出压缩的目的是「让有限的上下文窗口装更多有价值的信息」，而不是「抹掉工具输出」。

---

## 二、核心约束：不影响当前轮次的推理质量

这是最重要的约束，也是当前实现的根本问题所在。

### 2.1 Agent 工具调用是多轮推理过程

Agent 完成一次任务通常经历多轮工具调用：

```
用户："帮我修复 auth 模块的 bug"

第 1 轮：模型 → terminal("find . -name '*auth*'") → 工具返回文件列表
第 2 轮：模型 → read_file("auth.py")             → 工具返回文件内容
第 3 轮：模型 → terminal("pytest auth_test.py")   → 工具返回测试结果
第 4 轮：模型 → write_file("auth.py", ...)        → 工具返回写入确认
第 5 轮：模型 → terminal("pytest auth_test.py")   → 工具返回测试通过
第 6 轮：模型 → 最终回复用户
```

在第 3 轮时，Agent 需要同时参考：
- 第 1 轮的文件列表（确认修复了正确的文件）
- 第 2 轮的文件内容（理解代码逻辑）
- 第 3 轮的测试结果（定位 bug）

**如果在第 3 轮模型调用前就把第 1、2 轮的工具输出截断了，Agent 就无法准确分析 bug 并制定修复策略。**

### 2.2 当前实现的问题

`ToolOutputBudgetMiddleware.abefore_model()` 的行为：

```
每次模型调用前 → 遍历消息列表中所有 ToolMessage → 无条件截断超预算的
```

这意味着：

| 场景 | 当前行为 | 后果 |
|------|---------|------|
| Agent 连续 3 次 terminal 调用 | 每次模型调用前都截断**所有** terminal 输出 | 第 2 次模型调用时，第 1 次的完整输出已丢失 |
| read_file 返回 200 行代码 | 被截断为头 133 行 + 尾 67 行 | Agent 可能错过中间的关键函数定义 |
| 上下文只有 2000 token | 仍然触发截断 | 即使上下文完全不紧张，也在破坏信息 |

**核心缺陷：没有区分「Agent 正在使用的工具输出」和「已经过时的工具输出」。**

---

## 三、正确的压缩时机和策略

### 3.1 时序分析：LangChain 中间件钩子

```
用户消息到达
  ↓
[before_model] ← ToolOutputBudgetMiddleware 当前挂载点
  ↓
[wrap_model_call] ← 模型调用，返回 tool_calls 或文本
  ↓
[after_model] ← 模型返回后
  ↓
Agent 调用工具执行
  ↓
工具返回 ToolMessage，追加到消息列表
  ↓
[before_model] ← 下一轮模型调用前，再次触发
  ↓
...
```

`abefore_model` 是在「模型调用前」触发，不是「工具执行后」触发。这意味着它能看到**所有历史消息**，包括上一轮的工具输出。这个挂载点本身没问题，**问题在于截断逻辑不对**。

### 3.2 正确的压缩策略：基于上下文窗口比例的渐进式管理

**关键设计决策：阈值不应写死为绝对 token 数，而应基于模型上下文窗口的比例。**

不同模型的上下文窗口差异巨大：

| 模型 | 上下文窗口 |
|------|-----------|
| qwen3.5-plus | 128K |
| qwen-turbo | 1M |
| qwen3.5-flash | 128K |
| deepseek-chat | 64K |

写死 4000/6000 token 的问题：
- 用 128K 模型时，4000 token 只是窗口的 3%，几乎不会触发
- 用 32K 模型时，4000 token 已是窗口的 12.5%，可能触发太晚
- 用户在前端切换模型后，压缩行为应该自动适应

**基于比例的阈值设计：**

```python
# 压缩阈值以模型上下文窗口比例为基准
SAFE_RATIO = 0.25       # 窗口的 25% — 低于此比例不压缩
PRESSURE_RATIO = 0.45   # 窗口的 45% — 高于此比例激进压缩

# 示例：128K 模型
# SAFE: 128K × 0.25 = 32K tokens  — 低于 32K 不处理
# PRESSURE: 128K × 0.45 = 57K tokens — 高于 57K 激进压缩

# 示例：32K 模型
# SAFE: 32K × 0.25 = 8K tokens
# PRESSURE: 32K × 0.45 = 14K tokens
```

```
压缩策略决策树（基于上下文窗口比例）：

1. 已用 token < 窗口 × 25%
   → 不做任何处理，所有工具输出保持完整

2. 窗口 × 25% ≤ 已用 token < 窗口 × 45%
   → 仅压缩「早期轮次」的工具输出（Agent 已经分析过的）
   → 「当前轮次」的工具输出保持完整

3. 已用 token ≥ 窗口 × 45%
   → 激进压缩：早期轮次用更短的摘要替代
   → 当前轮次保留头部+关键信息
   → 同时触发 SummarizationMiddleware 摘要
```

**配置方式（config.json）：**

```json
{
  "llm": {
    "model": "qwen3.5-plus",
    "context_window": 131072
  },
  "middleware": {
    "tool_output_budget": {
      "safe_ratio": 0.25,
      "pressure_ratio": 0.45
    }
  }
}
```

`context_window` 可由前端根据模型选择自动填入（维护一份模型→窗口大小的映射表），也可手动覆盖。

### 3.3 「当前轮次」的定义

关键问题：如何区分「当前正在使用的工具输出」和「已经过时的工具输出」？

**方案：基于工具调用链的轮次划分**

```
消息列表：
  [Human] "帮我修复 bug"          ← 用户消息
  [AI] tool_calls: [terminal...]   ← Agent 决定调用工具
  [Tool] terminal 输出              ← 工具结果
  [AI] tool_calls: [read_file...]  ← Agent 基于结果决定下一步
  [Tool] read_file 输出             ← 工具结果
  [AI] tool_calls: [terminal...]   ← Agent 基于结果决定下一步
  [Tool] terminal 输出              ← 工具结果（当前轮次）
  [AI] 最终回复                     ← 当前位置
```

一个「轮次」定义为：从用户消息或上一条最终回复开始，到下一条最终回复或当前消息为止的所有消息。

**但更实用的划分方式是：最近 N 条 ToolMessage 保持完整，更早的才压缩。** N 可以根据上下文压力动态调整：
- 上下文不紧张时：N = 全部（不压缩）
- 上下文开始紧张时：N = 最近 3 组
- 上下文很紧张时：N = 最近 1 组

### 3.4 压缩手段的递进关系

```
                信息保留度                成本
                ←────── 高 ──────→       ←── 低 ──→

  不处理    │    头尾截断    │   LLM摘要   │  完全归档
           │               │            │  + 文件链接
  ─────────┼───────────────┼────────────┼──────────
  上下文    │   上下文开始   │  上下文     │  上下文
  宽裕时    │   紧张时       │  很紧张时   │  极端时
```

| 压缩手段 | 适用场景 | 信息保留 | 实现复杂度 |
|---------|---------|---------|-----------|
| **不处理** | 已用 token < 窗口 × 25% | 100% | 无 |
| **头尾截断** | 窗口 × 25% ≤ 已用 token < 窗口 × 45% | ~75%（头 2/3 + 尾 1/3） | 低（当前实现） |
| **LLM 摘要** | 已用 token ≥ 窗口 × 45% | ~50%（关键信息提取） | 中（需调用 LLM） |
| **归档 + 文件链接** | 单条输出 > 窗口 × 5% | 引用 + 摘要 | 中（需写文件） |

---

## 四、与原始设计文档的差距分析

### 4.1 原始设计（2026-04-12 分析报告 Section 3.2）

原始设计对 `ToolOutputBudgetMiddleware` 的描述：

> "每次模型调用前检查所有 ToolMessage，对超过预设 token 预算的工具输出进行截断"

以及 Section 3.3 的 Observation 遮蔽：

> "once a tool has been called deep in the message history, why would the agent need to see the raw result again?"

**原始设计的合理部分：**
- 前置截断延缓摘要触发 ✓
- 头尾保留策略 ✓
- 极端场景的归档方案 ✓
- Observation 遮蔽的理念（早期工具输出不需要完整保留）✓

**原始设计的缺失部分：**
- 没有区分「当前轮次」和「早期轮次」→ 导致实现时无差别截断
- 没有考虑上下文压力阈值 → 导致不管上下文是否紧张都截断
- Observation 遮蔽只在 Section 3.3 提了概念但未纳入实现方案

### 4.2 需要修正的设计决策

| 决策点 | 原始设计 | 修正后 |
|-------|---------|--------|
| 触发时机 | 每次 `before_model` 无条件执行 | 仅在已用 token 超过上下文窗口安全比例时执行 |
| 压缩范围 | 所有超预算的 ToolMessage | 仅压缩「早期轮次」的超预算 ToolMessage |
| 当前轮次保护 | 无 | 最近 N 组 ToolMessage 保持完整（N 随上下文压力调整） |
| 压缩手段 | 仅头尾截断 | 渐进式：不处理 → 截断 → LLM 摘要 → 归档 |
| 归档能力 | 仅在 Section 3.3 提到 | 纳入核心实现，极端大输出自动归档 + 文件链接 |

---

## 五、推荐的实现方案

### 5.1 重构后的 ToolOutputBudgetMiddleware

```python
class ToolOutputBudgetMiddleware(AgentMiddleware):
    """渐进式工具输出压缩：基于上下文窗口比例，仅在压力增大时压缩早期输出。"""

    # 压缩触发比例（相对于模型上下文窗口）
    SAFE_RATIO = 0.25       # 窗口的 25% — 低于此比例不压缩
    PRESSURE_RATIO = 0.45   # 窗口的 45% — 高于此比例激进压缩

    def __init__(self, budgets=None, context_window=131072):
        self._budgets = budgets if budgets is not None else TOOL_OUTPUT_BUDGETS
        self._context_window = context_window

    async def abefore_model(self, state, runtime):
        messages = state.get("messages", [])
        if not messages:
            return None

        total_tokens = self._estimate_tokens(messages)
        safe = self._context_window * self.SAFE_RATIO
        pressure = self._context_window * self.PRESSURE_RATIO

        # 阶段 1：上下文宽裕，不处理
        if total_tokens < safe:
            return None

        # 阶段 2/3：根据压力确定保护范围
        if total_tokens < pressure:
            protect_recent = 3  # 保护最近 3 组工具输出
            strategy = "truncate"
        else:
            protect_recent = 1  # 仅保护最近 1 组
            strategy = "aggressive"

        # 找到需要压缩的 ToolMessage（排除保护范围内的）
        protected_ids = self._get_protected_tool_ids(messages, protect_recent)

        processed = []
        changed = False
        for msg in messages:
            if (isinstance(msg, ToolMessage)
                and msg.name in self._budgets
                and msg.id not in protected_ids
                and _exceeds_budget(msg.content, self._budgets[msg.name])):
                # 根据策略选择压缩手段
                compressed = self._compress(msg, strategy)
                msg = msg.model_copy(update={"content": compressed})
                changed = True
            processed.append(msg)

        if changed:
            return {"messages": processed}
        return None
```

### 5.2 关键方法说明

**`_get_protected_tool_ids(messages, n)`**：识别最近 N 组工具调用的 ToolMessage ID，这些消息不受压缩影响。

**`_estimate_tokens(messages)`**：粗略估算消息列表的总 token 数（与 SummarizationMiddleware 使用相同的估算逻辑）。

**`_compress(msg, strategy)`**：根据策略选择压缩手段。
- `"truncate"`：头尾截断（当前实现）
- `"aggressive"`：更短的头尾截断 + 省略摘要（未来可扩展为 LLM 摘要）

### 5.3 与 SummarizationMiddleware 的协作

```
上下文窗口使用比例：

0% ──── 25% ──── 45% ──── 60% ────→

│  不处理  │ 截断早期  │ 激进截断  │
│          │ 工具输出  │ + LLM摘要  │
│          │          │           │
│  ToolOutputBudget 职责范围 ──────→│
│                              │ Summarization │
│                              │ 职责范围 ────→│
```

两层中间件的协作关系：
1. **ToolOutputBudgetMiddleware**（第 1 层）：渐进压缩工具输出，延缓 token 增长
2. **SummarizationMiddleware**（第 2 层）：当 token 真的超阈值时，生成结构化摘要替换旧消息

SummarizationMiddleware 的 `trigger_tokens` 也应联动上下文窗口比例（如窗口的 60%），而非写死 8000。

第 1 层做得越好，第 2 层触发得越晚，整体上下文质量越高。

---

## 六、归档能力（未来扩展）

对极端大输出（> 10000 字符），可增加自动归档：

```python
# 工具输出自动保存为文件，ToolMessage 中只保留摘要 + 文件路径
archive_path = f"sessions/archive/tool_{tool_name}_{timestamp}.txt"
write_file(archive_path, full_output)
compressed = f"[完整输出已归档到 {archive_path}，可用 read_file 查看]\n{truncated_summary}"
```

这样 Agent 在需要时可以通过 `read_file` 重新加载完整输出，兼顾了压缩和可恢复性。

**建议将归档能力作为 Phase 2 实现**，Phase 1 先解决核心的渐进式压缩逻辑。

---

## 七、总结

| 维度 | 当前实现 | 应有的实现 |
|------|---------|-----------|
| 触发条件 | 无条件（每次 before_model） | 基于上下文窗口使用比例（25%/45%） |
| 阈值基准 | 无（写死工具预算 token 数） | 模型上下文窗口大小（config.json 配置或模型映射表） |
| 压缩范围 | 所有超预算的 ToolMessage | 仅早期轮次，保护当前轮次 |
| 压缩手段 | 单一（头尾截断） | 渐进式（不处理→截断→摘要→归档） |
| 对推理的影响 | **可能破坏当前轮次的数据分析** | 不影响当前轮次，仅压缩已消费的数据 |
| 与 Summarization 的协作 | 独立运作，各自写死阈值 | 均基于上下文窗口比例，分层协作 |
| 模型切换适应 | 不适应 | 切换模型后自动调整所有阈值 |
