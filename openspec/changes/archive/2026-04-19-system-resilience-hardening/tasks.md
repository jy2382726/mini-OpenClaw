## 1. bootstrap/touch 异常日志记录

- [ ] 1.1 在 `chat.py` 顶部确认 `logger` 已初始化（如未初始化则新增 `import logging; logger = logging.getLogger(__name__)`）（`backend/api/chat.py`）
- [ ] 1.2 修改第 93-95 行的 `try...except Exception: pass` 为 `try...except Exception: logger.warning("会话元数据操作失败 session_id=%s: %s", session_id, e)`（`backend/api/chat.py`）

## 2. 辅助模型默认值统一 + 回退链简化

- [ ] 2.1 在 `config.py` 的 `_DEFAULT_CONFIG` 字典中新增 `auxiliary_model` 字段：`"auxiliary_model": {"model": "qwen3.5-flash", "temperature": 0}`（`backend/config.py`）
- [ ] 2.2 简化 `get_auxiliary_model_config()`：去掉三级回退链（auxiliary_model → summary_model → mem0.extraction_model），改为直接从 `config.get("auxiliary_model", _DEFAULT_CONFIG["auxiliary_model"])` 获取（`backend/config.py`）
- [ ] 2.3 修改 `get_settings_for_display()` 中 `auxiliary_model` 缺失时的默认值，改为引用 `_DEFAULT_CONFIG["auxiliary_model"]`（`backend/config.py`）

## 验证

- [ ] 2.4 运行 `pytest backend/tests/ -v` 确认所有测试通过
- [ ] 2.5 启动应用，验证辅助模型设置页面正常显示默认值
