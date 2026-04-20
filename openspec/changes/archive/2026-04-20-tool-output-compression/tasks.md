## 1. 新增标记常量和检测方法

- [x] 1.1 在 `backend/graph/middleware.py` 中新增 `_COMPRESSED_MARKER = "<!-- compressed:"` 常量
- [x] 1.2 在 `ToolOutputBudgetMiddleware` 中新增 `_is_compressed(content)` 方法：检测 content 是否以 `_COMPRESSED_MARKER` 开头
- [x] 1.3 在 `ToolOutputBudgetMiddleware` 中新增 `_make_marker(method, original_length, archive_path)` 方法：生成 `<!-- compressed:{method}:{original_length}:{path} -->` 标记字符串

## 2. 重构归档方法

- [x] 2.1 在 `ToolOutputBudgetMiddleware` 中新增 `_archive_original(content, tool_name, session_id)` 方法：将原始 content 写入归档文件，返回相对路径；写入失败返回 None
- [x] 2.2 移除旧 `_archive_output` 方法（被 `_archive_original` + `_make_archived_content` 替代）

## 3. 新增压缩内容生成方法

- [x] 3.1 在 `ToolOutputBudgetMiddleware` 中新增 `_make_archived_content(original, archive_path, strategy)` 方法：生成 archived 策略替换内容（标记头 + 归档引用 + ~500 token 头部 + 精确省略量 + ~200 token 尾部）
- [x] 3.2 在 `ToolOutputBudgetMiddleware` 中新增 `_make_truncated_content(original, budget, strategy, archive_path)` 方法：生成 truncated 策略替换内容（标记头 + 归档引用 + 按 strategy 分配头尾预算）
- [x] 3.3 移除旧 `_compress` 方法（被 `_make_truncated_content` 替代）

## 4. 重构 abefore_model

- [x] 4.1 在 `abefore_model` 的 ToolMessage 处理循环中加入 Step 1 幂等检测：`_is_compressed(content)` 为 True 时 `continue` 跳过
- [x] 4.2 修改压缩流程：在预算检测后、策略选择前，调用 `_archive_original()` 保全原始数据
- [x] 4.3 将超大输出分支从 `_archive_output()` 替换为 `_make_archived_content()`
- [x] 4.4 将中等输出分支从 `_compress()` 替换为 `_make_truncated_content()`
- [x] 4.5 确认归档失败时降级：`archive_path` 为 None 时 `_make_truncated_content` 标记中路径为 `none`
- [x] 4.6 在 `abefore_model` 末尾 `changed=True` 时记录 info 级别统计日志（处理数量、策略、保护组数）

## 5. 清理废弃方法

- [x] 5.1 移除模块级 `_truncate_with_summary` 函数（如确认无外部引用）

## 6. 验证

- [x] 6.1 启动后端，发送多轮对话触发工具输出压缩，确认标记正确嵌入 content
- [x] 6.2 构造长对话触发第二轮压缩，确认已标记消息被跳过（无嵌套 `[省略]`）
- [x] 6.3 确认归档文件保存的是原始数据（非截断后数据）
- [x] 6.4 模拟归档写入失败（权限不足），确认降级为轻截断且不抛出异常
- [x] 6.5 确认旧格式消息（无标记）仍被正常处理
