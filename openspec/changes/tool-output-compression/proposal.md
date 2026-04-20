## Why

`ToolOutputBudgetMiddleware` 的 `abefore_model` 直接修改 `ToolMessage.content`，不记录任何处理状态，导致两个已确认缺陷：

1. **无标记+无检测→反复压缩**：已被截断的消息在下一轮被视为新数据再次截断，产生嵌套 `[省略]` 标记，信息逐轮衰减
2. **截断后归档→信息失真**：归档操作可能收到已截断数据，且"摘要"仅取头部截断（`content[:budget*2//3]`），非语义摘要，导致归档文件不完整

## What Changes

- 在 `ToolMessage.content` 头部嵌入压缩状态标记（`<!-- compressed:method:length:path -->`），实现幂等检测——已处理的消息永远跳过
- 重构压缩流程为「先归档后截断」：所有超预算内容先将**原始数据**写入归档文件，再执行截断/摘要替换
- 截断策略改为头尾结构化摘要（头部+尾部+精确省略量），替代当前的纯头部截断
- 新增 `_archive_original()` 方法（保存原始内容）、`_make_archived_content()` 方法（超大内容归档摘要）、`_make_truncated_content()` 方法（中等内容截断）

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `progressive-tool-compression`：压缩幂等检测（标记机制）、先归档后截断流程、结构化头尾摘要替代纯头部截断

## Impact

### 受影响文件

| 文件 | 改动类型 |
|------|---------|
| `backend/graph/middleware.py` | 修改 — `ToolOutputBudgetMiddleware` 类新增标记检测方法、重构 `_compress` → 拆分为 `_archive_original` + `_make_archived_content` + `_make_truncated_content`、重构 `abefore_model` 加入幂等检测和先归档后截断 |

### 不受影响

- 中间件链顺序和其他中间件层
- `config.json` 配置结构（`safe_ratio`、`pressure_ratio`、`budgets` 含义不变）
- 前端（不解析 `<!-- compressed: -->` 标记）
- State 结构和 Checkpoint 持久化逻辑
- 归档文件清理逻辑（GC 机制不变）

### 回滚方案

移除标记检测逻辑，恢复原始 `_compress` 方法。旧消息无标记时行为与当前一致（向下兼容）。标记嵌入在 `content` 中会随 checkpoint 持久化，回滚后已带标记的消息会被原始 `_compress` 再次处理（标记字符串被当作内容截断）——这是可接受的降级行为。
