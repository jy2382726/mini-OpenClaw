## 1. 后端：配置层与工厂函数

- [x] 1.1 在 `backend/config.py` 中新增 `auxiliary_model` 默认配置（`{"model": "qwen3.5-flash", "temperature": 0}`）
- [x] 1.2 在 `backend/config.py` 中实现 `get_auxiliary_model_config()` getter，包含向后兼容优先级链
- [x] 1.3 在 `backend/config.py` 中实现 `create_auxiliary_llm()` 工厂函数，复用主模型 api_key/base_url
- [x] 1.4 在 `backend/config.json` 中新增 `auxiliary_model` 配置段

## 2. 后端：消费方迁移

- [x] 2.1 重构 `backend/graph/agent.py` 的 `_create_summary_llm()` 改用 `create_auxiliary_llm()`
- [x] 2.2 重构 `backend/graph/agent.py` 的 `_summarize_goal()` 改用 `create_auxiliary_llm()`
- [x] 2.3 重构 `backend/graph/mem0_manager.py` 的 `initialize()` 改用 `get_auxiliary_model_config()` 获取模型配置
- [x] 2.4 重构 `backend/api/chat.py` 的 `_generate_title()` 改用 `create_auxiliary_llm()`，消除硬编码
- [x] 2.5 重构 `backend/api/sessions.py` 的会话摘要逻辑改用 `create_auxiliary_llm()`，消除硬编码
- [x] 2.6 重构 `backend/api/compress.py` 改用 `create_auxiliary_llm()`，消除硬编码

## 3. 前端：设置页面

- [x] 3.1 在 `frontend/src/lib/settingsApi.ts` 的 `SystemSettings` 类型中新增 `auxiliary_model` 字段
- [x] 3.2 在 `frontend/src/app/settings/page.tsx` 的 CATEGORIES 数组中新增「辅助模型」分类
- [x] 3.3 实现辅助模型配置表单：模型选择（预设 + 自定义）+ temperature 滑块
- [x] 3.4 验证设置保存后 config.json 正确写入 `auxiliary_model` 段

## 4. 测试

- [x] 4.1 新增 `get_auxiliary_model_config()` 向后兼容测试（summary_model 迁移、优先级链）
- [x] 4.2 新增 `create_auxiliary_llm()` 工厂函数测试（正常创建、无 API key 降级）
- [x] 4.3 更新 `test_mem0_optimizations.py` 中的模型名称断言
- [x] 4.4 验证所有现有测试通过
