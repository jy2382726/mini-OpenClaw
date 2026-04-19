## MODIFIED Requirements

### Requirement: 统一辅助模型配置结构

系统 SHALL 在 `config.json` 中提供 `auxiliary_model` 配置段，包含 `model`（模型名称）和 `temperature`（生成温度）两个字段。辅助模型 MUST 复用主模型的 `api_key` 和 `base_url`。

默认值 MUST 定义在 `_DEFAULT_CONFIG` 字典的 `auxiliary_model` 字段中，值为 `{"model": "qwen3.5-flash", "temperature": 0}`。`get_auxiliary_model_config()` 和 `get_settings_for_display()` MUST 引用 `_DEFAULT_CONFIG["auxiliary_model"]` 作为默认值，MUST NOT 重复硬编码默认值。

`get_auxiliary_model_config()` MUST 直接从 `auxiliary_model` 配置段读取，MUST NOT 回退到 `summary_model` 或 `mem0.extraction_model`。辅助模型统一用于摘要、标题生成、记忆提取等所有辅助任务。

#### Scenario: 使用默认辅助模型配置

- **WHEN** `config.json` 中未配置 `auxiliary_model` 段
- **THEN** 系统 SHALL 使用 `_DEFAULT_CONFIG["auxiliary_model"]` 中的默认值 `qwen3.5-flash` + `temperature: 0`

#### Scenario: 自定义辅助模型

- **WHEN** 用户在前端设置页面将辅助模型改为 `qwen-turbo`，temperature 改为 `0.1`
- **THEN** 系统 SHALL 使用 `qwen-turbo` + `temperature: 0.1` 创建所有辅助 LLM 实例

#### Scenario: API key 复用主模型

- **WHEN** 主模型配置了 `api_key: "sk-xxx"` 且 `base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"`
- **THEN** 辅助模型 MUST 使用相同的 `api_key` 和 `base_url`，无需独立配置

#### Scenario: 不回退到 summary_model

- **WHEN** `config.json` 中未配置 `auxiliary_model` 但配置了 `summary_model`
- **THEN** 系统 MUST 使用 `_DEFAULT_CONFIG["auxiliary_model"]` 默认值，MUST NOT 使用 `summary_model` 的值
