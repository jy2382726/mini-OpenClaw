## Context

`SkillRegistry`（`skill_registry.py`）实现了四级技能上下文注入机制。Level 1（索引）在系统提示 Zone 2 生成精简快照，但缺少触发词信息。Level 3（预加载）的属性推断已实现，但注入逻辑缺失——没有任何代码读取 `inject_system_prompt` 属性并将完整 SKILL.md 注入系统提示。

## Goals / Non-Goals

**Goals:**
- Level 1 快照为有触发词的技能追加 `[触发: ...]` 格式，无触发词技能保持不变
- Level 3 将 `inject_system_prompt=true` 的技能完整 SKILL.md 注入系统提示 Zone 2
- 触发词数量限制为最多 3 个，避免单行过长

**Non-Goals:**
- 不改变触发词推断逻辑（已有实现足够）
- 不改变 `inject_system_prompt` 属性推断逻辑
- 不改变 Level 0/2 的行为
- 不对注入的 SKILL.md 做语义压缩或截断（使用 `_read_component` 的 MAX_COMPONENT_LENGTH 保护）

## Decisions

### D1. Level 3 注入位置：Zone 2（稳定前缀层）

**选择**: 在 `build_stable_prefix()` 中注入 Level 3 技能内容

**不选**: Zone 3（动态层）

**理由**: 预加载技能的内容在技能加载/卸载前不变，属于低频变化内容，符合 Zone 2 的语义。注入 Zone 2 可以利用 KV-cache，避免每次请求重新传输完整的 SKILL.md 内容。

**实现方式**: 在 `_TEMPLATE` 的 Zone 2 区块末尾追加 `{preload_skills}` 占位符，`build_stable_prefix()` 中遍历所有 `inject_system_prompt=true` 的技能，读取完整 SKILL.md 内容拼接到该占位符。

### D2. 触发词显示数量限制

**选择**: 最多显示 3 个触发词

**不选**: 显示全部触发词

**理由**: 部分技能可能通过 metadata 设置较多触发词（如 10 个），全部显示会导致单行过长影响系统提示可读性。3 个足以覆盖最常见的触发场景。

### D3. 预加载内容长度控制

**选择**: 复用 `_read_component()` 的 `MAX_COMPONENT_LENGTH`（20000 字符）截断保护

**不选**: 单独为 SKILL.md 设置长度限制

**理由**: `_read_component()` 已有成熟的截断逻辑（含多编码回退），直接复用避免重复代码。单个 SKILL.md 极少超过 20000 字符。

## Risks / Trade-offs

**[Zone 2 膨胀影响缓存]** → 当前项目中没有技能设置 `inject_system_prompt=true`，因此本次变更不增加实际 Zone 2 大小。未来如果添加预加载技能，每个技能最多增加 20K 字符，需评估对缓存命中率的影响。

**[多技能同时预加载]** → 理论上可以多个技能同时设置 `inject_system_prompt=true`，此时 Zone 2 会显著膨胀。建议在 `build_stable_prefix()` 中添加总量检查，超过阈值时 log.warning 并截断。

## 调用关系

```
prompt_builder.build_stable_prefix(base_dir, skills_snapshot)
  ↓ skills_snapshot 由 SkillRegistry.build_compact_snapshot() 生成
  ↓
  1. 读取 Zone 1 文件（SOUL.md, IDENTITY.md, USER.md）
  2. 拼接 skills_snapshot（Zone 2，含触发词格式）
  3. 遍历 SkillRegistry.get_preload_skills()  ← 新增
     → 通过 base_dir / "skills" / skill.name / "SKILL.md" 定位文件（不使用 skill.location，该字段是面向 Agent 显示的相对路径）
     → 使用 _read_component() 读取完整 SKILL.md（受 MAX_COMPONENT_LENGTH 截断）
     → 拼接到 Zone 2 末尾
  4. 返回完整稳定前缀
```

## 回滚策略

极低风险，无需特殊回滚：
- 触发词格式是纯增量追加，无触发词的技能输出不变
- Level 3 注入仅在 `inject_system_prompt=true` 时触发，当前项目无此类技能
- 通过注释掉 preload 逻辑即可恢复原始行为
