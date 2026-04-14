## Context

当前 `ToolOutputBudgetMiddleware`（`backend/graph/middleware.py`）的实现存在三个核心缺陷：

1. **无条件截断**：每次 `abefore_model` 触发时，遍历所有 ToolMessage 无差别截断超预算的输出，不考虑上下文是否真正紧张
2. **不保护当前轮次**：Agent 在多轮工具调用链中（如 terminal → read_file → write_file → terminal），早期步骤的输出被截断后，后续步骤的推理质量受损
3. **写死绝对阈值**：所有阈值以固定 token 数定义，无法适应不同模型的上下文窗口（128K vs 32K）

详细分析见 `docs/tool-output-compression-analysis.md`。

## Goals / Non-Goals

**Goals:**

- 基于模型上下文窗口比例控制压缩触发时机，替代写死的绝对 token 阈值
- 区分「当前轮次」和「早期轮次」的工具输出，仅压缩后者
- 实现渐进式压缩策略：不处理 → 截断 → 激进截断 → 归档
- `context_window` 可通过 config.json 配置，前端支持模型映射自动填入
- SummarizationMiddleware 的 trigger 联动上下文窗口比例

**Non-Goals:**

- 不实现 LLM 摘要压缩（Phase 2，需调用辅助模型，成本高）
- 不修改 LangChain 内置的 SummarizationMiddleware 源码（仅调整其配置参数）
- 不修改 `ContextAwareToolFilter` 和 `ToolCallLimitMiddleware` 的逻辑
- 不实现上下文健康度监控（独立优化方向）

## Decisions

### 决策 1：阈值基准 — 上下文窗口比例

**选择**：所有压缩阈值以模型上下文窗口大小的比例定义。

```
safe_ratio = 0.25      # 窗口的 25% — 低于此比例不压缩
pressure_ratio = 0.45  # 窗口的 45% — 高于此比例激进压缩
archive_ratio = 0.05   # 窗口的 5% — 单条输出超过此值触发归档
```

128K 模型示例：safe=32K, pressure=57K, archive=6.5K
32K 模型示例：safe=8K, pressure=14K, archive=1.6K

**理由**：用户切换模型后压缩行为自动适应，无需手动调整阈值。

**备选方案**：固定 token 阈值 — 无法适应不同模型窗口，已否决。

### 决策 2：当前轮次保护 — 动态 N 组保护

**选择**：根据上下文压力动态调整保护最近 N 组工具输出。

- 上下文 < safe_ratio：N = 全部（不压缩任何输出）
- safe_ratio ≤ 上下文 < pressure_ratio：N = 最近 3 组（压缩更早的输出）
- 上下文 ≥ pressure_ratio：N = 最近 1 组（激进压缩）

「一组工具输出」= 从 AIMessage(tool_calls) 到对应的 ToolMessage 的完整序列。

**理由**：Agent 的多步推理通常依赖最近 2-3 步的工具结果。保护 3 组覆盖了绝大多数场景（如 terminal → read_file → write_file 链），而激进模式下仅保护 1 组确保最新决策有完整数据。

**备选方案**：固定保护最近 1 组 — 过于激进，在多步任务中可能截断关键上下文。

### 决策 3：压缩手段递进

**选择**：Phase 1 实现两级压缩，Phase 2 预留扩展点。

| 级别 | 触发条件 | 行为 | Phase |
|------|---------|------|-------|
| 0 | < safe_ratio | 不处理 | 1 |
| 1 | safe_ratio ~ pressure_ratio | 头尾截断（头 2/3 + 尾 1/3） | 1 |
| 2 | ≥ pressure_ratio | 更短截断（头 1/2 + 尾 1/4） | 1 |
| 3 | 单条 > archive_ratio | 归档 + 文件链接 | 1 |
| 4 | Phase 2 | LLM 摘要 | 2（未来） |

**理由**：Phase 1 以本地操作为主（截断、归档），无额外 LLM 调用成本。LLM 摘要留作 Phase 2，需要与 `auxiliary_model` 统一配置配合。

### 决策 4：context_window 配置来源

**选择**：`config.json` 中显式配置 `llm.context_window`，前端维护模型→窗口映射表自动填入。

```json
{
  "llm": {
    "model": "qwen3.5-plus",
    "context_window": 131072
  }
}
```

前端映射表：
```typescript
const CONTEXT_WINDOWS: Record<string, number> = {
  "qwen3.5-plus": 131072,
  "qwen3.5-flash": 131072,
  "qwen-turbo": 1000000,
  "deepseek-chat": 65536,
};
```

**理由**：模型 API 通常不返回上下文窗口信息，需要前端维护映射。用户选择模型时自动填入，也支持手动覆盖自定义模型。

**备选方案**：后端查询模型 API — DashScope/OpenAI 不提供标准接口获取窗口大小。

### 决策 5：SummarizationMiddleware trigger 联动

**选择**：`_build_middleware()` 中动态计算 SummarizationMiddleware 的 trigger_tokens。

```python
context_window = get_context_window()  # 从 config.json 读取
summary_trigger = int(context_window * 0.6)  # 窗口的 60%
```

**理由**：SummarizationMiddleware 的 trigger 应与 ToolOutputBudgetMiddleware 的阈值体系保持一致。当前写死的 8000 token 对 128K 模型来说触发太早（仅占 6%），浪费了上下文窗口。

### 调用关系

```
config.json
  ├── llm.context_window: 131072
  └── middleware.tool_output_budget.safe_ratio: 0.25
        middleware.tool_output_budget.pressure_ratio: 0.45
              ↓
config.py → get_context_window() → 131072
              ↓
agent.py → _build_middleware()
        ├── ToolOutputBudgetMiddleware(
        │       context_window=131072,
        │       safe_ratio=0.25,
        │       pressure_ratio=0.45)
        └── SummarizationMiddleware(
                trigger=("tokens", 131072 * 0.6))
              ↓
每次 abefore_model 触发：
  1. 估算总 token 数
  2. 计算使用比例 = total_tokens / context_window
  3. 判断水位 → 决定保护范围和压缩策略
  4. 仅对「早期轮次」的超预算 ToolMessage 执行压缩
```

## Risks / Trade-offs

- [token 估算不精确] → 使用 `len(content) * 0.25` 粗略估算（与当前实现一致），对中文（1 token ≈ 1-2 字）和英文（1 token ≈ 4 字符）有偏差 → 可接受，阈值本身就是近似值
- [归档文件堆积] → 工具输出归档到 `sessions/archive/` 可能长期堆积 → 归档文件随会话删除一并清理
- [context_window 配置缺失] → 用户可能不填 context_window → getter 提供 131072（128K）默认值

## Migration Plan

1. **Phase 1a**：`config.py` 新增 `context_window` 配置和 getter
2. **Phase 1b**：重构 `ToolOutputBudgetMiddleware` 核心逻辑（比例阈值 + 轮次保护）
3. **Phase 1c**：`agent.py` 联动 SummarizationMiddleware trigger
4. **Phase 1d**：实现归档能力
5. **Phase 1e**：前端新增 context_window 配置
6. **Phase 1f**：重写测试用例

**回滚策略**：`context_window` 有默认值 131072，旧 config.json 无需修改即可正常运行（退化为与当前类似的固定阈值行为）。
