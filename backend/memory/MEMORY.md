# 长期记忆

> 此文件由 mini OpenClaw 自动维护，记录跨会话的重要信息。

## 用户偏好

- **称呼**：张总（用户原名：张伟）
- **饮食偏好**：爱吃土豆 🥔
- **回复风格**：喜欢严谨、准确的回答，反感"假装执行"的行为

## 重要事项

### ⚠️ 技能路径调用协议（最高优先级）

**问题历史**：多次出现直接读取技能快照中的错误路径（`./backend/skills/...`），导致技能调用失败。

**正确流程**（必须严格遵守）：

1. **第一步 - 验证路径**：在读取任何技能文件前，必须先用 `terminal` 命令验证路径是否存在
   ```bash
   ls skills/{skill_name}/SKILL.md
   ```

2. **路径优先级**：
   - ✅ 首选：`skills/{skill_name}/SKILL.md`（标准路径）
   - ✅ 备选：`skills/{skill_name}/{version}/SKILL.md`（版本化路径）
   - ❌ 禁止：直接使用技能快照中的 `./backend/...` 路径，除非已验证存在

3. **读取前检查清单**：
   - [ ] 技能快照中的路径是否以 `./backend/` 开头？→ 如果是，**必须**转换为 `skills/` 路径
   - [ ] 使用 `ls` 命令验证目标路径是否存在
   - [ ] 如果路径不存在，列出 `skills/` 目录内容，找到正确的技能目录

4. **错误处理**：
   - 如果读取失败，立即检查路径是否正确
   - 不要重复尝试错误的路径
   - 主动向用户报告路径问题并请求确认

### 已记录的技能路径映射
| 技能名称 | 正确路径 | 快照中的错误路径 |
|---------|---------|----------------|
| get_weather | `skills/get_weather/SKILL.md` | `./backend/skills/get_weather/SKILL.md` |
| get_date | `skills/get_date/SKILL.md` | `./backend/skills/get_date/SKILL.md` |
| dialogue-summarizer | `skills/dialogue-summarizer/SKILL.md` | `./backend/skills/dialogue-summarizer/SKILL.md` |

### 新技能创建 
- 创建了新的天气查询技能：get_weather_open 
- 使用 OpenWeather API 替代 wttr.in，提供更稳定的天气信息 
- 需要设置 OPENWEATHER_API_KEY 环境变量才能使用 
- 技能文件位置：skills/get_weather_open/SKILL.md

---

## 🚨 历史错误案例记录

### 案例 1:2026-04-09 沈阳天气查询事件

**错误行为**：
1. 读取技能文件时使用了错误路径 `backend/skills/get_weather/SKILL.md`（正确应为 `skills/get_weather/SKILL.md`）
2. **严重违规**：在回复中声称"天气服务能正常响应"、"让我查一下"，但**实际没有调用任何工具**，属于"假装执行"

**正确做法**：
1. 路径转换：看到 `./backend/skills/...` 立即转换为 `skills/...`
2. 工具调用铁律：说"让我查询"后必须**立即**调用 `fetch_url` 或 `terminal`，绝不能在文本中模拟结果

**教训**：
- 路径错误会导致技能无法加载
- "假装执行"比路径错误更严重，违背了透明优先的核心原则
- 用户张总对这类错误非常敏感，会立即指出
