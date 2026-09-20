---
name: ec-roi-check
description: "每日检查投放ROI，自动判断加预算/暂停/维持。Use when: 用户要求分析投放效果、查看ROI报告、需要投放决策建议。"
compatibility: "Requires Python 3.12"
metadata:
  author: skillforge
  department: EC
  risk-level: R2
allowed-tools: Bash(python:*) Read
---

# 投放 ROI 决策

## 目的

每日自动检查各渠道投放ROI，根据阈值判断加预算、维持或暂停。

## 判断逻辑

### step_1: ROI 判断
  ├─ ROI > {roi_green} → 绿灯
      动作: 加预算50%
  ├─ ROI > {roi_yellow} → 黄灯
      动作: 维持现状
  └─ 其他 → 红灯
      动作: 建议暂停

### step_2: 时段修正
  ├─ 白天(8-22点) → 正常投放
      动作: 按ROI结论执行
  └─ 夜间(22-8点) → 降低出价
      动作: 出价降低30%

## 反例

1. **误判场景**: 大促后3天内ROI剧烈波动，系统误判为红灯暂停投放
   **正确做法**: 设置"大促冷却期"参数，冷却期内不做暂停决策
   **来源**: 双11投放复盘

2. **误判场景**: 新渠道初期ROI低于阈值被暂停，但实际处于正常爬坡期
   **正确做法**: 新渠道前7天使用独立阈值，或标记为"观察期"不暂停

## 输出定义

| 名称 | 格式 | 接收人 | 审批级别 |
| :--- | :--- | :--- | :--- |
| 投放决策报告 | JSON | 投放组长 | L1 |
| 异常告警 | 钉钉消息 | 运营总监 | L0 |

## 数据输入

| 名称 | 来源 | 频率 |
| :--- | :--- | :--- |
| campaign_roi | ad_platform_api | 每日 |
| budget_status | finance_db | 每日 |

## 测试用例

### 用例_高ROI绿灯
```yaml
input:
  roi: 2.3
  time: "10:00"
  channel: "抖音"
expected_output:
  conclusion: "绿灯"
  suggested_action: "加预算50%"
```

### 用例_低ROI红灯
```yaml
input:
  roi: 0.5
  time: "14:00"
expected_output:
  conclusion: "红灯"
  suggested_action: "建议暂停"
```

### 用例_夜间降价
```yaml
input:
  roi: 1.8
  time: "23:00"
expected_output:
  conclusion: "绿灯"
  suggested_action: "出价降低30%"
```
