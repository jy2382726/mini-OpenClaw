---
name: get_weather
description: 获取指定城市的实时天气信息
---

# 获取天气技能

## 使用场景
当用户询问某个城市的天气情况时使用此技能。

## 执行步骤

1. 从用户消息中提取城市名称
2. 使用 `fetch_url` 工具访问 wttr.in 天气服务：
   ```
   fetch_url("https://wttr.in/{城市名}?format=j1&lang=zh")
   ```
3. 解析返回的 JSON 数据，提取关键信息：
   - 当前温度
   - 天气状况
   - 湿度
   - 风速
4. 用自然语言向用户汇报天气情况

## 示例

用户：「查询北京的天气」

执行流程：
1. 提取城市：北京 (Beijing)
2. 调用：`fetch_url("https://wttr.in/Beijing?format=j1&lang=zh")`
3. 解析 JSON 结果
4. 回复：「北京当前天气：晴，温度 25°C，湿度 40%，东南风 3 级。」

## 注意事项
- 城市名建议使用英文拼写以提高准确性
- 如果 wttr.in 不可用，可尝试使用 `python_repl` 调用备用 API
