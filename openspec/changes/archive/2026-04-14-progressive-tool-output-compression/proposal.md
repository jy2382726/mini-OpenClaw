## Why

当前 `ToolOutputBudgetMiddleware` 在每次模型调用前**无条件截断所有超预算的工具输出**，不区分「Agent 正在使用的当前轮次」和「已经过时的早期轮次」，也不考虑上下文是否真正紧张。这导致 Agent 在多轮工具调用链中丢失关键信息（如第 1 轮 terminal 输出被截断后，第 3 轮模型无法准确分析 bug），且所有阈值写死为绝对 token 数，无法适应不同模型的上下文窗口大小。

详细分析见 `docs/tool-output-compression-analysis.md`。

## What Changes

- **重构 `ToolOutputBudgetMiddleware` 为渐进式压缩**：引入「上下文窗口比例」作为触发基准（安全区 25%、紧张区 45%），替代写死的绝对 token 阈值
- **新增「当前轮次保护」机制**：根据上下文压力动态调整保护范围（宽裕时全部保护 → 紧张时仅保护最近 1 组工具输出）
- **引入 `context_window` 配置项**：在 `config.json` 中声明模型上下文窗口大小，前端设置页面根据模型选择自动填入
- **新增工具输出归档能力**：极端大输出（> 窗口 5%）自动落盘为文件，ToolMessage 中仅保留摘要 + 文件路径引用
- **联动 `SummarizationMiddleware` 的 trigger**：从写死 8000 token 改为基于上下文窗口比例触发

## Capabilities

### New Capabilities

- `progressive-tool-compression`: 渐进式工具输出压缩 — 定义基于上下文窗口比例的触发时机、当前轮次保护、渐进压缩策略、归档能力

### Modified Capabilities

- `middleware-chain`: `ToolOutputBudgetMiddleware` 的触发逻辑从无条件截断改为渐进式压缩，`SummarizationMiddleware` 的 trigger 从写死值改为窗口比例联动

## Impact

**后端代码变更：**
- `backend/graph/middleware.py` — 重构 `ToolOutputBudgetMiddleware`，新增 `_get_protected_tool_ids()`、`_estimate_tokens()`、`_compress()` 等方法
- `backend/config.py` — 新增 `context_window` 配置项和 `get_context_window()` getter
- `backend/config.json` — `llm` 段新增 `context_window` 字段，`middleware.tool_output_budget` 段新增比例配置
- `backend/graph/agent.py` — `_build_middleware()` 中传入 `context_window` 参数，SummarizationMiddleware trigger 联动窗口比例

**前端代码变更：**
- `frontend/src/app/settings/page.tsx` — 新增 `context_window` 配置控件（模型切换时自动填入）
- `frontend/src/lib/settingsApi.ts` — `SystemSettings` 类型新增 `context_window` 字段

**测试变更：**
- `backend/tests/test_middleware.py` — 重写 `ToolOutputBudgetMiddleware` 测试用例，覆盖渐进式逻辑
- 新增上下文窗口比例计算的测试用例
- 新增当前轮次保护机制的测试用例
