## Why

`SkillRegistry` 的四级技能上下文注入机制中，Level 1（索引）缺少触发词格式显示，Level 3（预加载）的注入逻辑完全缺失。这导致 Agent 无法在系统提示中看到技能的触发词信息（降低技能发现效率），且 `inject_system_prompt=true` 的技能无法自动注入完整内容到系统提示 Zone 2。

## What Changes

- Level 1 快照格式增强：有触发词的技能在索引行追加 `[触发: 关键词1/关键词2]`，无触发词的技能保持现有格式不变（向后兼容）
- Level 3 预加载注入实现：在 `build_stable_prefix()` 中检查 `inject_system_prompt=true` 的技能，将完整 SKILL.md 内容注入 Zone 2，支持内容长度限制

## Capabilities

### New Capabilities

（无新增能力）

### Modified Capabilities

- `skill-registry`: 修改精简技能快照格式 Requirement（追加触发词显示）和四级技能上下文注入 Requirement（Level 3 注入逻辑从待实现变为已实现）
- `cache-prefix`: 修改三段式系统提示缓存前缀 Requirement（Zone 2 新增预加载技能内容）

## Impact

**后端文件**:
- `backend/graph/skill_registry.py` — `build_compact_snapshot()` 追加触发词格式
- `backend/graph/prompt_builder.py` — `build_stable_prefix()` 新增 Level 3 预加载注入逻辑

**测试文件**:
- `backend/tests/test_skill_registry.py` — 更新快照格式断言、新增 Level 3 注入测试

**影响分析**:
- 快照格式变更仅影响系统提示文本，不影响 API 接口
- Zone 2 内容增加可能影响缓存命中率（仅当有 `inject_system_prompt=true` 的技能时）
- 触发词数量建议限制为最多 3 个，避免单行过长

**回滚方案**: 极低风险。快照格式变更是增量追加；Level 3 注入可通过对 `inject_system_prompt` 属性的检查自然关闭（无技能设置该属性时行为不变）。
