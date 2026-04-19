## Why

代码中存在两处静默吞异常和默认值重复定义的韧性缺陷：`chat.py` 中 bootstrap/touch 异常被 `except Exception: pass` 完全吞掉，导致 SQLite 连接故障等严重问题无法排查；`config.py` 中辅助模型默认值在两处函数中重复硬编码，修改时容易遗漏导致行为不一致。

## What Changes

- `chat.py` 的 bootstrap/touch try/except 块中添加 `logger.warning()` 记录异常，保留不阻塞对话的行为但确保异常可追踪
- `config.py` 的 `_DEFAULT_CONFIG` 中新增 `auxiliary_model` 默认配置，`get_auxiliary_model_config()` 和 `get_settings_for_display()` 改为引用该默认值，消除重复定义

## Capabilities

### New Capabilities

（无新增能力）

### Modified Capabilities

- `session-repository`: 修改会话 touch 活跃时间更新 Requirement，异常时 MUST 记录 warning 日志
- `auxiliary-model`: 修改统一辅助模型配置结构 Requirement，默认值 MUST 定义在 `_DEFAULT_CONFIG` 中

## Impact

**后端文件**:
- `backend/api/chat.py` — 第 93-95 行 try/except 块添加 `logger.warning()`
- `backend/config.py` — `_DEFAULT_CONFIG` 新增 `auxiliary_model` 字段，`get_auxiliary_model_config()` 和 `get_settings_for_display()` 引用默认值

**测试文件**:
- `backend/tests/test_config.py`（如存在）— 验证默认值来源统一

**回滚方案**: 极低风险。日志记录是纯增量行为；默认值统一是重构，功能不变。
