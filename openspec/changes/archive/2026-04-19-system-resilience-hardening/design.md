## Context

两个独立的韧性缺陷分布在不同模块：

1. `chat.py:93-95` 的 `try...except Exception: pass` 吞掉了 `bootstrap_if_missing` 和 `touch` 的所有异常，包括 SQLite 连接失败、磁盘满等严重错误
2. `config.py` 的 `_DEFAULT_CONFIG` 字典缺少 `auxiliary_model` 字段，默认值 `{"model": "qwen3.5-flash", "temperature": 0}` 分别硬编码在 `get_auxiliary_model_config()`（第 192 行）和 `get_settings_for_display()`（第 286 行）

## Goals / Non-Goals

**Goals:**
- bootstrap/touch 异常可追踪（有日志记录）
- 辅助模型默认值单一来源（`_DEFAULT_CONFIG`）

**Non-Goals:**
- 不改变 bootstrap/touch 的错误处理策略（仍不阻塞对话）
- 不改变辅助模型的功能行为
- 不涉及 C3（流结束自动完成步骤），该行为已确认保持现状

## Decisions

### D1. 异常日志级别：warning

**选择**: 使用 `logger.warning()` 而非 `logger.error()`

**不选**: `logger.error()` 或 `logger.exception()`

**理由**: bootstrap/touch 失败后对话仍可正常进行（checkpoint 机制兜底），不构成需要告警的错误级别。warning 级别足以让运维人员在排查会话列表问题时追踪到根因。

### D2. 默认值统一方式：直接引用 `_DEFAULT_CONFIG` + 简化回退链

**选择**: 在 `_DEFAULT_CONFIG` 中新增 `auxiliary_model` 字段作为唯一辅助模型配置来源；`get_auxiliary_model_config()` 去掉三级回退链（auxiliary_model → summary_model → mem0.extraction_model），改为直接从 `config.get("auxiliary_model", _DEFAULT_CONFIG["auxiliary_model"])` 获取。

**不选**: 保留三级回退链 + 仅统一默认值位置

**理由**: `_DEFAULT_CONFIG` 新增 `auxiliary_model` 后，`_deep_merge` 会将默认值合并进配置，导致 `config.get("auxiliary_model")` 永远非空，三级回退链的第二、三级被短路。辅助模型用途统一（摘要、标题生成、记忆提取），不需要散乱多模型管理，简化为单层获取更清晰。

**注意**: 旧的 `summary_model` 和 `mem0.extraction_model` 配置段保留在 `_DEFAULT_CONFIG` 中以兼容旧配置文件，但不再参与辅助模型选择。

## Risks / Trade-offs

**[日志量增加]** → 仅在异常时产生 warning，正常流程无额外日志。

**[回退链移除]** → 原来用户只配了 `summary_model`（如 `qwen-turbo`）而没有 `auxiliary_model` 时，系统会使用 `qwen-turbo`。简化后统一使用 `_DEFAULT_CONFIG["auxiliary_model"]` 的 `qwen3.5-flash`。如果用户需要自定义辅助模型，必须在 `auxiliary_model` 配置段中设置。

**[默认值引用路径变更]** → `get_auxiliary_model_config()` 和 `get_settings_for_display()` 的默认值来源从硬编码变为 `_DEFAULT_CONFIG`。

## 回滚策略

极低风险：
- 日志记录：删除 `logger.warning()` 调用即可回退
- 默认值统一：功能等价重构
- 回退链简化：如需恢复三级回退，还原 `get_auxiliary_model_config()` 的优先级链逻辑即可
