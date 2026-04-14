## 1. 配置层：上下文窗口支持

- [x] 1.1 在 `backend/config.py` 中新增 `context_window` 默认配置（131072），实现 `get_context_window()` getter
- [x] 1.2 在 `backend/config.json` 的 `llm` 段中新增 `context_window` 字段
- [x] 1.3 在 `backend/config.json` 的 `middleware.tool_output_budget` 段中新增 `safe_ratio` 和 `pressure_ratio` 配置

## 2. 核心重构：ToolOutputBudgetMiddleware

- [x] 2.1 重构 `ToolOutputBudgetMiddleware.__init__()` 接受 `context_window`、`safe_ratio`、`pressure_ratio` 参数
- [x] 2.2 实现 `_estimate_tokens(messages)` 方法：粗略估算消息列表总 token 数
- [x] 2.3 实现 `_get_protected_tool_ids(messages, n)` 方法：识别最近 N 组工具输出 ID
- [x] 2.4 重构 `abefore_model()` 主逻辑：基于窗口比例判断水位 → 确定保护范围 → 执行渐进压缩
- [x] 2.5 实现 `_compress(content, budget, strategy)` 多级压缩策略：标准截断 vs 短截断
- [x] 2.6 实现归档能力：超大输出自动保存到 `sessions/archive/`，ToolMessage 替换为文件引用 + 截断摘要

## 3. 联动：SummarizationMiddleware 配置

- [x] 3.1 修改 `backend/graph/agent.py` 的 `_build_middleware()` 中 SummarizationMiddleware 的 trigger_tokens 为 `context_window * 0.6`
- [x] 3.2 修改 ToolOutputBudgetMiddleware 实例化，传入 `context_window` 和比例参数

## 4. 前端：上下文窗口配置

- [x] 4.1 在 `frontend/src/lib/settingsApi.ts` 的 `SystemSettings` 类型中新增 `context_window` 字段
- [x] 4.2 在 `frontend/src/app/settings/page.tsx` 中新增模型→上下文窗口映射表（qwen3.5-plus: 131072, qwen-turbo: 1000000, deepseek-chat: 65536 等）
- [x] 4.3 实现模型选择联动：切换模型时自动填入 `context_window`，自定义模型允许手动输入

## 5. 测试

- [x] 5.1 重写 `backend/tests/test_middleware.py` 中 ToolOutputBudgetMiddleware 测试：覆盖渐进式逻辑（不处理 → 标准截断 → 短截断）
- [x] 5.2 新增当前轮次保护机制测试：验证最近 N 组工具输出不被压缩
- [x] 5.3 新增上下文窗口比例计算测试：不同窗口大小下的阈值自动适应
- [x] 5.4 新增归档能力测试：超大输出归档 + 文件引用 + read_file 可恢复
- [x] 5.5 验证所有现有测试通过
