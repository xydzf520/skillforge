---
name: json-tool
description: "格式化、查询、转换JSON数据。Use when: 用户要格式化JSON、用jq查询JSON字段、转换JSON格式、或需要从API响应中提取数据。NOT for: XML处理、CSV处理。"
compatibility: "Requires jq"
metadata: { "aiclaw": { "emoji": "📋", "requires": { "bins": ["jq"] } } }
---

# JSON Tool Skill

使用 jq 处理 JSON 数据。

## 常用操作

### 格式化 JSON
```bash
echo '{"a":1,"b":2}' | jq .
```

### 提取字段
```bash
echo '{"name":"test","value":42}' | jq '.name'
```

### 数组操作
```bash
echo '[{"id":1,"name":"a"},{"id":2,"name":"b"}]' | jq '.[].name'
```

### 过滤
```bash
echo '[{"id":1,"score":80},{"id":2,"score":95}]' | jq '[.[] | select(.score > 90)]'
```

### 转换格式
```bash
# JSON → CSV 风格
echo '[{"a":1,"b":2},{"a":3,"b":4}]' | jq -r '.[] | [.a,.b] | @csv'
```

## 注意事项

- 处理大文件时用 `jq -c` 紧凑输出
- 管道输入时确保是合法 JSON
- 复杂查询建议分步骤执行
