## ADDED Requirements

### Requirement: 统一辅助模型配置结构

系统 SHALL 在 `config.json` 中提供 `auxiliary_model` 配置段，包含 `model`（模型名称）和 `temperature`（生成温度）两个字段。辅助模型 MUST 复用主模型的 `api_key` 和 `base_url`，不提供独立认证配置。

默认值 MUST 为 `{"model": "qwen3.5-flash", "temperature": 0}`。

#### Scenario: 使用默认辅助模型配置

- **WHEN** `config.json` 中未配置 `auxiliary_model` 段
- **THEN** 系统 SHALL 使用默认值 `qwen3.5-flash` + `temperature: 0`

#### Scenario: 自定义辅助模型

- **WHEN** 用户在前端设置页面将辅助模型改为 `qwen-turbo`，temperature 改为 `0.1`
- **THEN** 系统 SHALL 使用 `qwen-turbo` + `temperature: 0.1` 创建所有辅助 LLM 实例

#### Scenario: API key 复用主模型

- **WHEN** 主模型配置了 `api_key: "sk-xxx"` 且 `base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"`
- **THEN** 辅助模型 MUST 使用相同的 `api_key` 和 `base_url`，无需独立配置

### Requirement: 辅助模型向后兼容迁移

系统 SHALL 提供 `get_auxiliary_model_config()` 函数，按优先级链解析配置：`auxiliary_model` > `summary_model` > `mem0.extraction_model` > 默认值。

#### Scenario: 旧配置 summary_model 自动迁移

- **WHEN** `config.json` 中有 `summary_model: {"model": "qwen-turbo", "temperature": 0}` 但无 `auxiliary_model`
- **THEN** 系统 SHALL 将 `summary_model` 的配置作为辅助模型配置使用

#### Scenario: 辅助模型配置优先于旧配置

- **WHEN** `config.json` 中同时存在 `auxiliary_model` 和 `summary_model`
- **THEN** 系统 MUST 使用 `auxiliary_model` 的配置，忽略 `summary_model`

### Requirement: 集中式辅助 LLM 工厂函数

系统 SHALL 在 `config.py` 中提供 `create_auxiliary_llm()` 函数，返回 `ChatOpenAI` 实例或 `None`。所有辅助任务 MUST 通过此函数创建 LLM 实例。

#### Scenario: 正常创建辅助 LLM

- **WHEN** 主模型配置了有效的 `api_key`
- **THEN** 函数 SHALL 返回使用 `auxiliary_model.model` + 主模型 `api_key` + `base_url` 的 `ChatOpenAI` 实例

#### Scenario: 无 API key 时安全降级

- **WHEN** 主模型未配置 `api_key` 且环境变量 `DASHSCOPE_API_KEY` 也未设置
- **THEN** 函数 SHALL 返回 `None`，调用方 MUST 跳过辅助操作

### Requirement: 所有辅助任务统一使用辅助模型

以下辅助任务 MUST 通过 `create_auxiliary_llm()` 获取 LLM 实例：

1. `SummarizationMiddleware`（`agent.py`）
2. 任务目标摘要 `_summarize_goal()`（`agent.py`）
3. mem0 事实提取（`mem0_manager.py`）
4. 会话标题生成 `_generate_title()`（`chat.py`）
5. 会话摘要生成（`sessions.py`）
6. 历史压缩摘要（`compress.py`）

#### Scenario: 会话标题生成使用辅助模型

- **WHEN** 新会话的第一条消息触发标题生成
- **THEN** 系统 SHALL 使用辅助模型（而非主模型）生成标题

#### Scenario: mem0 事实提取使用辅助模型

- **WHEN** mem0 执行对话事实提取
- **THEN** 系统 SHALL 使用辅助模型的 model 配置创建 mem0 LLM 实例，temperature 固定为 0.1

#### Scenario: 摘要中间件使用辅助模型

- **WHEN** `SummarizationMiddleware` 需要创建摘要 LLM
- **THEN** 系统 SHALL 通过 `create_auxiliary_llm()` 获取实例，替代当前的 `_create_summary_llm()`

### Requirement: 前端设置页面辅助模型配置

前端设置页面 SHALL 在「LLM 模型」和「Embedding」分类之间新增「辅助模型」配置分类，包含模型选择和 temperature 滑块两个控件。

模型选择 MUST 提供预设列表（qwen3.5-flash、qwen-turbo、qwen-plus）并支持自定义输入。

#### Scenario: 用户在设置页面修改辅助模型

- **WHEN** 用户在「辅助模型」分类中选择 `qwen-turbo` 并点击保存
- **THEN** 前端 SHALL 将 `{auxiliary_model: {model: "qwen-turbo", temperature: 0}}` 写入 config.json

#### Scenario: 辅助模型设置独立于主模型

- **WHEN** 用户修改辅助模型配置
- **THEN** 主对话模型的配置 MUST 不受影响，反之亦然
