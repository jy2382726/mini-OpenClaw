## Context

项目中存在 5 个 LLM 实例创建点，分布在 4 个文件中，用途可归纳为两类：

| 类别 | 消费方 | 当前配置 | 文件 |
|------|-------|---------|------|
| 主对话 | `agent.py` 的 Agent LLM | `config.json → llm` | `agent.py` |
| 辅助任务 | SummarizationMiddleware | `config.json → summary_model` (qwen-turbo) | `agent.py:195` |
| 辅助任务 | 任务目标摘要 `_summarize_goal()` | 复用 `summary_model` | `agent.py:210` |
| 辅助任务 | mem0 事实提取 | `config.json → mem0.extraction_model` (qwen3.5-flash) | `mem0_manager.py:51` |
| 辅助任务 | 会话标题生成 | `os.getenv()` 硬编码 (qwen3.5-plus) | `chat.py:44` |
| 辅助任务 | 会话摘要生成 | `os.getenv()` 硬编码 (qwen3.5-plus) | `sessions.py:100` |
| 辅助任务 | 历史压缩摘要(废弃) | `os.getenv()` 硬编码 | `compress.py:24` |

前 3 个走 `config.json`，后 3 个硬编码。这些辅助任务对模型能力要求低（无需复杂推理），但当前分散配置导致管理困难和资源浪费。

## Goals / Non-Goals

**Goals:**

- 统一所有辅助任务的 LLM 模型为一个 `auxiliary_model` 配置项
- 默认使用 `qwen3.5-flash`（最新轻量模型，质量优于 qwen-turbo）
- 前端设置页面支持辅助模型的可视化配置
- 向后兼容：`summary_model` 和 `mem0.extraction_model` 旧配置自动迁移

**Non-Goals:**

- 不重构主对话模型 (`llm`) 的配置逻辑
- 不改变 mem0 的 embedding 模型配置
- 不实现辅助模型的热重载（辅助模型不参与 SSE 流式，重启生效即可）
- 不删除 `compress.py`（已标记废弃但保持向后兼容）

## Decisions

### 决策 1：配置结构设计

**选择**：在 `config.json` 顶层新增 `auxiliary_model` 段，复用主模型的 `api_key` 和 `base_url`。

```json
{
  "auxiliary_model": {
    "model": "qwen3.5-flash",
    "temperature": 0
  }
}
```

**理由**：辅助模型与主模型属于同一 API 提供商（DashScope），共享认证和端点。只需独立 `model` 和 `temperature` 两个字段。

**备选方案**：完全独立的 `auxiliary_model`（含独立 api_key/base_url）—— 过度设计，增加前端表单复杂度，无实际需求。

### 决策 2：向后兼容迁移策略

**选择**：`get_auxiliary_model_config()` getter 中的优先级链：

```
auxiliary_model > summary_model > mem0.extraction_model > 默认值
```

**理由**：如果用户只配了 `summary_model`，系统能自动识别并迁移，无需手动修改配置。

### 决策 3：集中式 LLM 工厂函数

**选择**：在 `config.py` 中新增 `create_auxiliary_llm()` 函数，统一创建辅助 LLM 实例。

```python
def create_auxiliary_llm() -> ChatOpenAI | None:
    """创建辅助模型 LLM 实例，所有辅助任务统一调用。"""
```

**理由**：替代各文件中分散的 LLM 创建逻辑，确保统一的降级策略（无 API key 时返回 None）和错误处理。

**备选方案**：在每个消费方内联创建 —— 导致逻辑重复，难以统一修改降级行为。

### 决策 4：前端 UI 方案

**选择**：在设置页面的 CATEGORIES 数组中新增「辅助模型」分类（位于「LLM 模型」和「Embedding」之间）。

配置项：
- 模型选择（预设列表 + 自定义输入）
- Temperature 滑块（0-1）

**理由**：与现有「LLM 模型」分类 UI 风格一致，用户无需理解 API 配置细节。辅助模型共享主模型的 API key/base_url，无需重复输入。

**备选方案**：在「高级设置」中添加 —— 辅助模型是常用配置，不应隐藏在高级选项中。

### 调用关系

```
config.json
  ├── llm (主模型) ──────────────→ agent.py → Agent LLM
  └── auxiliary_model (辅助模型)
        │
        ├── config.py:create_auxiliary_llm()
        │     └── ChatOpenAI(model, api_key, base_url, temperature)
        │
        ├── agent.py:_create_summary_llm()     → SummarizationMiddleware
        ├── agent.py:_summarize_goal()          → 任务目标摘要
        ├── mem0_manager.py:initialize()        → mem0 事实提取
        ├── chat.py:_generate_title()           → 会话标题
        ├── sessions.py:summarize_session()     → 会话摘要
        └── compress.py:compress_history()      → 历史压缩(废弃)
```

## Risks / Trade-offs

- [辅助模型能力不足] → qwen3.5-flash 对结构化摘要（SummarizationMiddleware 的四段输出）质量可能略低于 qwen-turbo → 提供配置覆盖能力，用户可回退到 qwen-turbo 或其他模型
- [向后兼容遗漏] → 旧配置文件中无 `auxiliary_model` 段 → getter 的优先级链确保旧配置自动生效
- [前端设置页 API key 暴露] → 辅助模型共享主模型 API key，前端不需单独展示 → 无风险

## Migration Plan

1. **Phase 1**：后端 `config.py` 新增 `auxiliary_model` 默认配置和 `create_auxiliary_llm()` 工厂函数
2. **Phase 2**：逐个迁移消费方（agent.py → mem0_manager.py → chat.py → sessions.py → compress.py）
3. **Phase 3**：前端设置页面新增辅助模型配置分类
4. **Phase 4**：更新测试用例

**回滚策略**：`get_auxiliary_model_config()` 的优先级链天然支持回滚——删除 `auxiliary_model` 段后自动降级到 `summary_model`。
