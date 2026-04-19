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

### D2. 默认值统一方式：直接引用 `_DEFAULT_CONFIG`

**选择**: 在 `_DEFAULT_CONFIG` 中新增 `auxiliary_model` 字段，两处函数改为 `config.get("auxiliary_model", _DEFAULT_CONFIG["auxiliary_model"])`

**不选**: 提取为独立常量

**理由**: `_DEFAULT_CONFIG` 已是项目默认值的统一来源（包含 llm、embedding 等配置），新增 `auxiliary_model` 字段保持一致性。独立常量会引入新的模式。

## Risks / Trade-offs

**[日志量增加]** → 仅在异常时产生 warning，正常流程无额外日志。

**[默认值引用路径变更]** → `get_auxiliary_model_config()` 和 `get_settings_for_display()` 的默认值来源从硬编码变为 `_DEFAULT_CONFIG`。功能行为完全不变（值相同），但需确认 `_DEFAULT_CONFIG` 在两处函数调用时已初始化（它作为模块级常量，初始化时机无问题）。

## 回滚策略

极低风险：
- 日志记录：删除 `logger.warning()` 调用即可回退
- 默认值统一：功能等价重构，不影响任何运行时行为
