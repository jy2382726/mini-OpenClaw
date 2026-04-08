---
name: get-date
description: 获取当前日期和时间信息。Use when users ask about the current date, time, day of week, year, month, or any time-related queries. 当用户询问"今天几号"、"现在几点"、"星期几"、"当前年份/月份"等任何与时间相关的问题时，立即使用此技能。不要猜测日期，始终使用此技能获取准确的时间信息。
---

# Get Date Skill

## Goal
准确获取并呈现当前系统的日期和时间信息，确保用户获得可靠的时间数据。

## Workflow

1. **解析用户请求**：识别用户需要的时间信息类型
   - 完整日期时间
   - 仅日期
   - 仅时间
   - 星期几
   - 特定格式

2. **执行命令**：根据用户需求选择合适的脚本或命令格式
   - 优先使用 `scripts/get_date_info.py` 获取中文友好输出
   - 或使用 `scripts/advanced_date.py` 进行时区/相对日期计算
   - 或直接使用 `date` 命令

3. **格式化输出**：以清晰的中文格式呈现结果

## Decision Tree

```
用户请求类型？
├─ "今天是什么日期" / "几号" → python scripts/get_date_info.py date
├─ "现在几点" / "当前时间" → python scripts/get_date_info.py time
├─ "星期几" / "周几" → python scripts/get_date_info.py weekday
├─ "今年是哪一年" → python scripts/get_date_info.py year
├─ "现在是几月" → python scripts/get_date_info.py month
├─ "前几天/后几天的日期" → python scripts/advanced_date.py relative <days>
├─ "某时区的时间" → python scripts/advanced_date.py current <timezone>
└─ 完整时间信息 → python scripts/get_date_info.py full
```

## Constraints

- **必须使用** `date` 命令或提供的脚本获取时间，禁止猜测或硬编码日期
- 输出格式应适应用户的语言偏好（默认中文）
- 如果用户指定时区需求，使用 `advanced_date.py` 的时区功能
- 不在技能中缓存时间信息，每次调用都重新获取

## Validation

成功标准：
- [ ] 返回的日期时间与系统时间一致
- [ ] 格式清晰易读
- [ ] 响应用户的具体问题（而非总是返回完整信息）
- [ ] 无硬编码或猜测的日期值
- [ ] 中文星期输出正确

验证方法：
```bash
# 对比系统时间
date
# 检查技能返回是否与上述命令一致
python scripts/get_date_info.py full
```

## Examples

### 示例 1：获取完整日期时间
**用户**：今天是什么日期？现在几点？
**执行**：`python scripts/get_date_info.py full`
**输出**：
```
2026 年 03 月 23 日 10:03:57
今天是：星期一
```

### 示例 2：仅获取日期
**用户**：今天几号？
**执行**：`python scripts/get_date_info.py date`
**输出**：`2026 年 03 月 23 日`

### 示例 3：获取星期
**用户**：今天星期几？
**执行**：`python scripts/get_date_info.py weekday`
**输出**：`星期一`

### 示例 4：获取相对日期
**用户**：三天后是哪天？
**执行**：`python scripts/advanced_date.py relative 3`
**输出**：`2026-03-26`

### 示例 5：获取时区时间
**用户**：现在伦敦时间是多少？
**执行**：`python scripts/advanced_date.py current Europe/London`
**输出**：`2026-03-23 02:03:57 GMT`

## Resources

### 脚本说明

#### get_date_info.py
- `full` - 完整日期时间 + 中文星期
- `date` - 仅日期
- `time` - 仅时间
- `weekday` - 中文星期
- `year` - 年份
- `month` - 月份

#### advanced_date.py
- `current [timezone]` - 当前时间（可选时区）
- `relative <days>` - 相对日期（正数=未来，负数=过去）
- `format [timestamp]` - 格式化时间戳

### 常用 date 命令格式

```bash
# 完整日期时间
date +"%Y年%m月%d日 %H:%M:%S"

# 仅日期
date +"%Y-%m-%d"

# 仅时间
date +"%H:%M:%S"

# 星期几（英文）
date +"%A"

# 星期几（数字 1-7）
date +"%u"

# 年份
date +"%Y"

# 月份
date +"%m"

# 日
date +"%d"

# 带时区（例如 UTC）
TZ=UTC date +"%Y-%m-%d %H:%M:%S %Z"
```

### 中文星期转换

由于 `date` 命令返回英文星期，如需中文星期，可在输出时转换：
- Monday → 星期一
- Tuesday → 星期二
- Wednesday → 星期三
- Thursday → 星期四
- Friday → 星期五
- Saturday → 星期六
- Sunday → 星期日
