# 工具输出压缩中间件优化方案

> 针对 `ToolOutputBudgetMiddleware` 的两个已确认缺陷，结合 Claude Code、Anthropic `clear_tool_uses`、OpenCode 等成熟系统设计，提出具体优化方案。

---

## 一、问题根因

当前 `abefore_model` 直接修改 `ToolMessage.content`，不记录任何处理状态，导致两个缺陷：

### 缺陷 1：无标记 + 无检测 → 反复压缩

```
Round N:   原始(15000字符) → 超budget → _compress → "头...[省略约7000字符]...尾"(8000字符)
Round N+1: 压缩后(8000字符) → 无标记，被视为新数据 → 策略变为aggressive时再次截断
           → 嵌套多层 [省略] 标记，信息持续衰减
```

代码路径（`middleware.py:199-201`）：
```python
elif _exceeds_budget(content, self._budgets[msg.name]):
    compressed = self._compress(content, self._budgets[msg.name], strategy)
    # 无任何判断：content 是否已经是被压缩过的内容？
```

### 缺陷 2：截断后归档 → 信息失真

跨轮次场景下，归档和摘要基于已截断数据：

```
Round N:   原始(15000字符) → 未达 archive_threshold 但超 budget → _compress 截断
           → state 中的消息被替换为截断内容，原始数据永久丢失
Round N+1: 截断后(8000字符) → 触发归档 → _archive_output 保存的是已截断内容
           → 归档文件不完整，"摘要"是对截断内容的再截断 → 双重失真
```

`_archive_output` 的"摘要"本身也只是头部截断（`content[:budget*2//3]`），非语义摘要：

```python
summary = content[: summary_budget * 2 // 3] + "\n...[已截断]..."
# 只取头部，丢失尾部信息，且省略量标注不精确
```

---

## 二、设计原则（借鉴成熟系统）

| 原则 | 来源 | 含义 |
|------|------|------|
| **一次性处理** | Claude Code `[Old tool result content cleared]` 占位符 | 每条消息只被压缩一次，处理过的永远跳过 |
| **先归档后截断** | Anthropic `clear_tool_uses`：保留 tool_use 记录，只清除载荷 | 截断前必须保全原始数据到归档文件 |
| **状态标记** | OpenCode `compacted = Date.now()` 非破坏性标记 | 通过标记区分消息的压缩状态，避免重复处理 |

### 三条原则如何解决两个缺陷

| 原则 | 解决的缺陷 |
|------|-----------|
| 一次性处理 | 缺陷 1：已处理的消息不再进入压缩流程 |
| 先归档后截断 | 缺陷 2：归档文件永远保存原始数据 |
| 状态标记 | 缺陷 1 + 2：标记既是幂等检测的依据，也记录了原始长度供归档引用 |

---

## 三、压缩状态机

每条 `ToolMessage` 的生命周期：

```
raw ──→ archived ──→ 终态
  │
  └──→ truncated ──→ 终态
```

**关键约束**：状态只能前进，不可回退。进入终态后中间件永远跳过该消息。

### 标记格式

放在 `content` 头部，兼顾可读性和可解析性：

```
<!-- compressed:{method}:{original_length}:{archive_path} -->
```

示例：
```
<!-- compressed:archived:30000:sessions/archive/tool_terminal_xxx_1713523200.txt -->
<!-- compressed:truncated:15000:sessions/archive/tool_terminal_xxx_1713523200.txt -->
```

字段含义：
- `method`：压缩方式（`archived` = 归档 + 摘要引用，`truncated` = 截断 + 归档引用）
- `original_length`：原始字符数（供日志和调试）
- `archive_path`：原始内容的归档文件路径

检测逻辑只需一行：
```python
isinstance(content, str) and content.startswith("<!-- compressed:")
```

### 向下兼容

没有标记的旧消息（包括 checkpoint 中已持久化的历史消息）会被正常处理（与当前行为一致）。处理后就带上了标记，后续轮次不再重复处理。

---

## 四、新决策树

```
abefore_model 被调用
  │
  ├─ Phase 0: tokens < window × safe_ratio → 不处理，返回 None
  │
  ├─ 确定保护范围
  │   tokens < window × pressure_ratio → protect_recent = 3
  │   tokens ≥ window × pressure_ratio → protect_recent = 1
  │
  └─ 遍历每条 unprotected ToolMessage：
     │
     ├─ Step 1: 幂等检测
     │   content 以 "<!-- compressed:" 开头？
     │   → YES: SKIP（已处理过，不再触碰）
     │
     ├─ Step 2: 预算检测
     │   content ≤ budget × 4 ？
     │   → YES: SKIP（未超预算，无需处理）
     │
     ├─ Step 3: 保全原始数据（核心改动）
     │   将原始 content 写入归档文件（无论后续采用哪种压缩策略）
     │   归档失败 → 降级为仅加标记的轻截断（标记不含归档路径）
     │
     ├─ Step 4: 选择压缩策略
     │   │
     │   ├─ 原始 > window × ARCHIVE_RATIO（超大内容）
     │   │   → archived 策略
     │   │   替换内容 = 标记 + 归档引用说明 + 结构化摘要
     │   │   摘要 = 头部(~500 token) + 尾部(~200 token) + 精确省略量
     │   │
     │   └─ 原始 > budget 但 ≤ window × ARCHIVE_RATIO（中等内容）
     │       → truncated 策略
     │       替换内容 = 标记 + 归档引用说明 + 截断内容
     │       截断 = 头 2/3 + 尾 1/3（或 aggressive: 头 1/2 + 尾 1/4）
     │
     └─ Step 5: model_copy 更新消息
```

### 与当前流程的关键差异对照

| 环节 | 当前流程 | 优化后流程 |
|------|---------|-----------|
| 检测 | 无，直接判断 content 长度 | 先检查 `<!-- compressed:` 标记，已处理则跳过 |
| 归档时机 | 仅超大内容（> window × 5%）触发归档，且可能收到已截断数据 | **所有**超预算内容先归档原始数据，再截断 |
| 归档内容 | 可能是已截断的数据 | 永远是原始数据 |
| 摘要方式 | 取头部截断 `content[:budget*2//3]` | 头部 + 尾部 + 精确省略量 |
| 幂等性 | 不支持，同一消息可被多次压缩 | 支持，标记确保只处理一次 |

---

## 五、具体改动点

改动集中在 `backend/graph/middleware.py` 的 `ToolOutputBudgetMiddleware` 类，不涉及 state 结构、接口签名或其他模块。

### 5.1 新增常量和方法

```python
_COMPRESSED_MARKER = "<!-- compressed:"

def _is_compressed(self, content: str) -> bool:
    """检测 content 是否已被压缩处理。"""
    return isinstance(content, str) and content.startswith(_COMPRESSED_MARKER)

def _make_marker(self, method: str, original_length: int, archive_path: str | None) -> str:
    """生成压缩标记头。"""
    path = archive_path or "none"
    return f"{_COMPRESSED_MARKER}{method}:{original_length}:{path} -->"
```

### 5.2 重构 _archive_output

核心变化：
1. 始终接收并保存**原始** content（调用方保证传入的是原始数据）
2. 摘要改为头尾结构化（不再只取头部）
3. 不再自行生成标记（由调用方统一处理）

```python
def _archive_original(self, content: str, tool_name: str, session_id: str) -> str | None:
    """将原始内容保存到归档文件，返回文件路径。写入失败返回 None。"""
    archive_dir = self._base_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time())
    filename = f"tool_{tool_name}_{session_id}_{timestamp}.txt"
    filepath = archive_dir / filename
    try:
        filepath.write_text(content, encoding="utf-8")
        return f"sessions/archive/{filename}"
    except (OSError, PermissionError, IOError):
        logger.warning("归档写入失败 %s", filepath)
        return None
```

### 5.3 重构 _compress → _compress_with_marker

核心变化：
1. 接收归档路径参数，输出内容包含归档引用
2. 摘要改为头尾结构化
3. 输出始终带标记头

```python
def _make_archived_content(
    self, original: str, archive_path: str | None, strategy: str
) -> str:
    """生成 archived 策略的替换内容（用于超大输出）。"""
    marker = self._make_marker("archived", len(original), archive_path)
    head_budget = 500 * 4          # ~500 token 头部
    tail_budget = 200 * 4          # ~200 token 尾部
    ref = f"[完整输出({len(original)}字符)已归档至 {archive_path}，可用 read_file 查看]" if archive_path else ""

    head = original[:head_budget]
    tail = original[-tail_budget:] if tail_budget > 0 else ""
    omitted = len(original) - head_budget - tail_budget

    return f"{marker}\n{ref}\n{head}\n...[省略 {omitted} 字符]...\n{tail}"

def _make_truncated_content(
    self, original: str, budget: int, strategy: str, archive_path: str | None
) -> str:
    """生成 truncated 策略的替换内容（用于中等输出）。"""
    marker = self._make_marker("truncated", len(original), archive_path)
    char_budget = budget * 4
    ref = f"[原始输出({len(original)}字符)已归档至 {archive_path}]" if archive_path else ""

    if strategy == "truncate":
        head_len = char_budget * 2 // 3
        tail_len = char_budget // 3
    else:  # aggressive
        head_len = char_budget // 2
        tail_len = char_budget // 4

    head = original[:head_len]
    tail = original[-tail_len:] if tail_len > 0 else ""
    omitted = len(original) - head_len - tail_len

    return f"{marker}\n{ref}\n{head}\n...[省略 {omitted} 字符]...\n{tail}"
```

### 5.4 重构 abefore_model

核心变化：加入 Step 1 幂等检测 + Step 3 先归档后截断。

```python
async def abefore_model(self, state, runtime):
    messages = state.get("messages", [])
    if not messages:
        return None

    try:
        config = get_config()
        session_id = config.get("configurable", {}).get("thread_id", "unknown")
    except RuntimeError:
        session_id = "unknown"

    total_tokens = self._estimate_tokens(messages)
    safe = int(self._context_window * self._safe_ratio)
    pressure = int(self._context_window * self._pressure_ratio)
    archive_threshold = int(self._context_window * self.ARCHIVE_RATIO)

    # Phase 0: 上下文宽裕，不处理
    if total_tokens < safe:
        return None

    # 确定保护范围和压缩策略
    if total_tokens < pressure:
        protect_recent = 3
        strategy = "truncate"
    else:
        protect_recent = 1
        strategy = "aggressive"

    protected_ids = self._get_protected_tool_ids(messages, protect_recent)

    processed = []
    changed = False

    for msg in messages:
        if (
            isinstance(msg, ToolMessage)
            and msg.name in self._budgets
            and msg.id not in protected_ids
        ):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)

            # Step 1: 幂等检测 — 已处理过的消息直接跳过
            if self._is_compressed(content):
                processed.append(msg)
                continue

            # Step 2: 预算检测 — 未超预算的不处理
            if not _exceeds_budget(content, self._budgets[msg.name]):
                processed.append(msg)
                continue

            # Step 3: 保全原始数据 — 先归档，再压缩
            archive_path = self._archive_original(content, msg.name, session_id)

            # Step 4: 选择压缩策略
            if len(content) > archive_threshold * 4:
                # 超大内容：归档 + 结构化摘要
                new_content = self._make_archived_content(content, archive_path, strategy)
            else:
                # 中等内容：截断 + 归档引用
                new_content = self._make_truncated_content(
                    content, self._budgets[msg.name], strategy, archive_path
                )

            msg = msg.model_copy(update={"content": new_content})
            changed = True

        processed.append(msg)

    if changed:
        return {"messages": processed}
    return None
```

---

## 六、变更影响分析

| 维度 | 影响 |
|------|------|
| 修改文件 | 仅 `backend/graph/middleware.py` |
| 接口变更 | 无（`ToolOutputBudgetMiddleware.__init__` 签名不变） |
| 配置变更 | 无（`safe_ratio`、`pressure_ratio`、`budgets` 含义不变） |
| State 结构 | 无变更（不新增字段，标记内嵌在 content 中） |
| Checkpoint 兼容 | 向下兼容（旧消息无标记，首次被处理后加上标记） |
| 归档文件 | 量会增加（所有超预算内容都归档，而非仅超大内容），但磁盘开销可忽略 |
| 前端影响 | 无（前端不解析 `<!-- compressed: -->` 标记） |

### 可观测性

标记中包含原始长度和归档路径，可通过日志统计压缩效果：

```python
# 可选：在 abefore_model 末尾添加统计日志
if changed:
    compressed_count = sum(1 for m in processed if isinstance(m, ToolMessage) and self._is_compressed(m.content))
    logger.info("工具输出压缩: %d 条消息已处理, 策略=%s, 保护=%d组", compressed_count, strategy, protect_recent)
```
