---
name: xuyi-weather
description: "查询江苏省盱眙县的当前天气，包括温度、湿度、风速和天气状况。Use when: 用户问盱眙天气、盱眙温度、盱眙下不下雨。"
compatibility: "Requires curl"
metadata:
  author: skillforge
  location: "盱眙县, 江苏省"
  coordinates: "33.01, 118.54"
  api: Open-Meteo
---

# 盱眙天气查询

使用 Open-Meteo API（免费无需 key）获取盱眙县当前天气。

盱眙坐标：纬度 33.01，经度 118.54

## 查询方法

获取当前天气数据：
```bash
curl -s "https://api.open-meteo.com/v1/forecast?latitude=33.01&longitude=118.54&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code&timezone=Asia/Shanghai"
```

## 解析结果

从 JSON 响应中提取字段：
- `current.temperature_2m` — 当前温度（°C）
- `current.relative_humidity_2m` — 相对湿度（%）
- `current.wind_speed_10m` — 风速（km/h）
- `current.weather_code` — WMO 天气代码

## WMO 天气代码对照表

| 代码 | 天气 |
|------|------|
| 0 | 晴天 |
| 1-3 | 多云 |
| 45, 48 | 有雾 |
| 51-57 | 毛毛雨/冻毛毛雨 |
| 61-67 | 雨/冻雨 |
| 71-77 | 雪 |
| 80-82 | 阵雨 |
| 85-86 | 阵雪 |
| 95-99 | 雷暴 |

## 输出格式

用中文输出，格式如下：

```
🌤️ 盱眙天气（{时间}）
- 温度：{temperature}°C
- 湿度：{humidity}%
- 风速：{wind_speed} km/h
- 天气：{对应中文描述}
```
