## Why

项目中存在多个 LLM 实例分散在不同文件中，用途重叠但配置方式不统一：

- **中间件摘要**（`SummarizationMiddleware`）使用 `config.json → summary_model`（qwen-turbo）
- **mem0 事实提取** 使用 `config.json → mem0.extraction_model`（qwen3.5-flash）
- **任务目标摘要**（`_summarize_goal`）复用 `summary_model`
- **会话标题生成**（`chat.py`）和**会话摘要**（`sessions.py`）直接硬编码 `os.getenv()` 读取主模型
- **压缩摘要**（`compress.py`，已废弃）同样硬编码

这导致：1）轻量任务使用了不必要的大模型（标题生成用 qwen3.5-plus），增加成本和延迟；2）同类辅助任务使用不同模型（qwen-turbo vs qwen3.5-flash），管理碎片化；3）前端设置页面无法管理辅助模型配置。

## What Changes

- 新增统一的 `auxiliary_model` 配置段，替代 `summary_model` 和 `mem0.extraction_model`
- 默认使用 `qwen3.5-flash` 作为辅助模型（比 qwen-turbo 更新更快、质量更好）
- 将 `chat.py`、`sessions.py`、`compress.py` 中硬编码的 LLM 实例统一迁移到 `auxiliary_model`
- 前端设置页面新增「辅助模型」配置分类，支持模型选择和参数调整
- 废弃 `summary_model` 配置段（向后兼容：自动迁移到 `auxiliary_model`）
- 废弃 `mem0.extraction_model` 独立配置（合并到 `auxiliary_model`）

## Capabilities

### New Capabilities

- `auxiliary-model`: 统一辅助模型管理 — 定义辅助模型的配置结构、创建逻辑、消费方接入规范

### Modified Capabilities

- `middleware-chain`: SummarizationMiddleware 的 LLM 实例创建从 `summary_model` 迁移到 `auxiliary_model`

## Impact

**后端代码变更：**
- `backend/config.py` — 新增 `auxiliary_model` 默认配置和 getter，废弃 `summary_model` getter
- `backend/config.json` — 新增 `auxiliary_model` 段，保留 `summary_model` 和 `mem0.extraction_model` 用于向后兼容迁移
- `backend/graph/agent.py` — `_create_summary_llm()` 和 `_summarize_goal()` 改用 `auxiliary_model`
- `backend/graph/mem0_manager.py` — `initialize()` 改用 `auxiliary_model` 替代 `mem0.extraction_model`
- `backend/api/chat.py` — `_generate_title()` 改用 `auxiliary_model`，消除硬编码
- `backend/api/sessions.py` — 会话摘要改用 `auxiliary_model`，消除硬编码
- `backend/api/compress.py` — 压缩摘要改用 `auxiliary_model`（废弃模块，保持一致性）

**前端代码变更：**
- `frontend/src/app/settings/page.tsx` — 新增「辅助模型」配置分类
- `frontend/src/lib/settingsApi.ts` — `SystemSettings` 类型新增 `auxiliary_model` 字段

**测试变更：**
- `backend/tests/test_mem0_optimizations.py` — 更新模型名称断言
- 新增辅助模型配置迁移的测试用例
