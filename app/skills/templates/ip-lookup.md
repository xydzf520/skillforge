---
name: ip-lookup
description: "查询IP地址的归属地和网络信息。Use when: 用户要查IP地址、查自己的公网IP、或需要IP归属地信息。NOT for: DNS解析、端口扫描。"
compatibility: "Requires curl"
metadata: { "aiclaw": { "emoji": "🌐", "requires": { "bins": ["curl"] } } }
---

# IP Lookup Skill

查询IP地址归属地信息。

## 使用方法

### 查询指定IP
```bash
curl -s "http://ip-api.com/json/{IP}?lang=zh-CN" | jq .
```

### 查询自己的公网IP
```bash
curl -s ifconfig.me
```

### 输出格式

返回结果包含：
- `query`: IP地址
- `country`: 国家
- `regionName`: 省份
- `city`: 城市
- `isp`: 运营商
- `org`: 组织

## 示例

用户说"查一下 8.8.8.8 是哪里的"，执行：
```bash
curl -s "http://ip-api.com/json/8.8.8.8?lang=zh-CN"
```
返回：Google LLC, 美国
