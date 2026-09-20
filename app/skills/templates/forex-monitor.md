---
name: forex-monitor
description: "监控跨境电商常用货币汇率。Use when: 需要手动查询当前汇率。"
compatibility: "Requires curl"
metadata:
  author: skillforge
  department: EC
  risk-level: R2---

本 Skill 用于监控跨境电商运营中常用的货币汇率，帮助投放团队进行广告预算和商品定价的实时估算。

操作步骤：

1. 使用免费汇率 API 获取实时汇率数据。这里使用 exchangerate-api.com 的免费端点（无需 API 密钥，但有速率限制）。
2. 我们将查询美元（USD）对人民币（CNY）、欧元（EUR）、英镑（GBP）和日元（JPY）的汇率。

在终端中执行以下命令：

```bash
# 获取美元对主要货币的汇率
curl -s "https://api.exchangerate-api.com/v4/latest/USD" | python3 -c "
import sys, json
data = json.load(sys.stdin)
rates = data['rates']
print('=== 跨境电商汇率监控 ===')
print(f"基准货币: {data['base']}")
print(f"更新时间: {data['date']}")
print('---')
print(f"美元兑人民币 (USD/CNY): {rates.get('CNY', 'N/A'):.4f}")
print(f"美元兑欧元 (USD/EUR): {rates.get('EUR', 'N/A'):.4f}")
print(f"美元兑英镑 (USD/GBP): {rates.get('GBP', 'N/A'):.4f}")
print(f"美元兑日元 (USD/JPY): {rates.get('JPY', 'N/A'):.2f}")
"
```

如果系统没有安装 Python3，可以使用 `jq` 工具解析 JSON（需提前安装 jq）：

```bash
curl -s "https://api.exchangerate-api.com/v4/latest/USD" | jq -r '
  \"=== 跨境电商汇率监控 ===\",
  \"基准货币: \" + .base,
  \"更新时间: \" + .date,
  \"---\",
  \"美元兑人民币 (USD/CNY): \" + (.rates.CNY | tostring),
  \"美元兑欧元 (USD/EUR): \" + (.rates.EUR | tostring),
  \"美元兑英镑 (USD/GBP): \" + (.rates.GBP | tostring),
  \"美元兑日元 (USD/JPY): \" + (.rates.JPY | tostring)
'
```

3.  结果将显示当前美元兑这四种货币的汇率。投放团队可以根据这些数据快速估算不同货币区的广告花费和收入。