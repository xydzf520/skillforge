---
name: air-quality
description: "查询多个城市的实时空气质量指数（AQI）。Use when: 用户需要手动查询多个指定城市的空气质量。"
compatibility: "Requires curl and jq"
metadata:
  author: skillforge
  department: EC
  role: 运营
  risk-level: R2---

本 Skill 用于查询多个城市的实时空气质量指数（AQI）。它使用 Open-Meteo 的免费空气质量 API。

**操作步骤：**

1.  用户需要提供要查询的城市名称列表。例如："北京,上海,广州"。
2.  Skill 会依次处理每个城市：
    a.  首先，使用 Open-Meteo 的地理编码 API 根据城市名称查询其经纬度坐标。
    b.  然后，使用获取到的经纬度坐标，调用 Open-Meteo 的空气质量 API 获取该位置的当前 AQI 等数据。
3.  将每个城市的查询结果（城市名、AQI指数、主要污染物）整理并输出。

**示例命令：**

假设用户输入的城市是 "Beijing,Shanghai,Guangzhou"。

对于单个城市（例如北京）的查询流程如下：

```bash
# 1. 地理编码，获取北京的坐标
LOCATION_DATA=$(curl -s "https://geocoding-api.open-meteo.com/v1/search?name=Beijing&count=1")

# 提取经纬度（假设API返回有效数据）
LAT=$(echo $LOCATION_DATA | jq -r '.results[0].latitude')
LON=$(echo $LOCATION_DATA | jq -r '.results[0].longitude')
CITY_NAME=$(echo $LOCATION_DATA | jq -r '.results[0].name')

# 2. 使用坐标查询空气质量
AIR_DATA=$(curl -s "https://air-quality-api.open-meteo.com/v1/air-quality?latitude=$LAT&longitude=$LON&current=us_aqi,pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone")

# 提取关键信息
AQI=$(echo $AIR_DATA | jq -r '.current.us_aqi')
DOMINANT_POLLUTANT="N/A"
# 简单逻辑：找出当前值最高的污染物（示例，实际可根据需要调整）
# 注意：此部分逻辑在完整脚本中需更严谨地实现

echo "城市: $CITY_NAME, AQI: $AQI, 主要污染物: $DOMINANT_POLLUTANT"
```

**完整脚本逻辑（伪代码/说明）：**

1.  接收一个以逗号分隔的城市名字符串作为输入。
2.  将字符串按逗号分割成数组。
3.  遍历城市数组，对每个城市执行：
    a.  调用 `https://geocoding-api.open-meteo.com/v1/search?name={城市名}&count=1` 获取坐标。
    b.  检查返回结果，如果 `results` 数组为空，则输出“未找到城市 [城市名]”并继续下一个。
    c.  从结果中提取 `latitude`, `longitude`, `name`。
    d.  调用 `https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=us_aqi,pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone` 获取空气质量数据。
    e.  从空气质量数据中提取 `current.us_aqi` (AQI指数)。
    f.  （可选）实现逻辑，从 `current` 对象下的各种污染物浓度（pm10, pm2_5等）中找出数值最高的一个作为主要污染物提示。
    g.  格式化输出该城市的信息。
4.  遍历结束后，输出所有成功查询城市的结果摘要。

**注意：**
*   确保系统已安装 `curl` 和 `jq` 命令。
*   Open-Meteo API 有免费使用限制，请勿高频请求。
*   地理编码 API 可能无法识别某些城市的中文名或别名，建议使用通用的英文名或拼音可能成功率更高（例如 Beijing 而非 北京）。在实际部署时，可能需要一个城市名到标准名的映射表。