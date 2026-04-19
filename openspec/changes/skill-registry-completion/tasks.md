## 1. Level 1 触发词格式

- [ ] 1.1 修改 `build_compact_snapshot()` 方法，遍历技能时检查 `trigger_patterns`，非空时追加 `[触发: 关键词1/关键词2]`，最多 3 个，用 `/` 分隔；无触发词时不追加（`backend/graph/skill_registry.py:134-143`）

## 2. Level 3 预加载注入

- [ ] 2.1 在 `SkillRegistry` 中新增 `get_preload_skills()` 方法，返回所有 `inject_system_prompt=true` 的技能列表（`backend/graph/skill_registry.py`）
- [ ] 2.2 修改 `_TEMPLATE` 模板，在 Zone 2 技能摘要列表之后追加 `{preload_skills}` 占位符（`backend/graph/prompt_builder.py`）
- [ ] 2.3 修改 `build_stable_prefix()` 函数，接收 `SkillRegistry` 实例（或预加载技能列表），遍历 `get_preload_skills()` 读取完整 SKILL.md 并拼接到 `{preload_skills}` 占位符，总量超过 60000 字符时 log.warning 并截断（`backend/graph/prompt_builder.py`）
- [ ] 2.4 修改 `agent.py` 中调用 `build_stable_prefix()` 的位置，传入 `SkillRegistry` 实例或预加载技能列表（`backend/graph/agent.py`）

## 3. 测试更新

- [ ] 3.1 更新 `test_skill_registry.py` 中快照格式断言，验证有触发词的技能追加 `[触发: ...]`、无触发词的不追加（`backend/tests/test_skill_registry.py`）
- [ ] 3.2 新增测试：触发词超过 3 个时截断显示
- [ ] 3.3 新增测试：`get_preload_skills()` 仅返回 `inject_system_prompt=true` 的技能
- [ ] 3.4 新增测试：`build_stable_prefix()` 包含预加载技能内容，无预加载技能时不变

## 验证

- [ ] 3.5 运行 `pytest backend/tests/test_skill_registry.py -v` 确认所有测试通过
- [ ] 3.6 启动应用，发送消息检查系统提示中技能索引是否包含触发词格式
