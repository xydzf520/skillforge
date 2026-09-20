# Skill 行业模板与格式参考

> **示例边界**：本页用于设计与测试方法说明；示例数值、阈值和行业判断不代表公开版的真实业务成果或通用标准。使用前以自己的授权数据、业务口径和验收结果替换。创建入口的当前限制见[创建路径](../../spec/skill-creation-paths.md)。


> 电商 / 客服 / 营销 / 财务 Skill 模板 + SKILL.md 完整格式
> 最后更新：2026-04-14
> 相关：[核心原则](core.md) · [测试与质量](testing.md)

---

## 1. 电商运营 — 投放 ROI 评估

```markdown
---
name: ad-roi-evaluator
department: EC
trigger_type: cron
risk_level: R2
description: >
  每日评估各投放渠道ROI，自动生成预算调整建议。
  当ROI低于盈亏线时告警，高于安全线时建议加预算。
params:
  roi_green_threshold:
    type: number
    description: "ROI超过此值判定为绿灯（可加预算）"
    default: 1.5
  roi_yellow_threshold:
    type: number
    description: "ROI超过此值但低于绿灯线判定为黄灯（维持观察）"
    default: 1.2
  roi_red_threshold:
    type: number
    description: "ROI低于此值判定为红灯（建议减预算或暂停）"
    default: 0.8
  min_spend:
    type: number
    description: "最小消耗金额，低于此值的渠道不参与评估（样本不足）"
    default: 1000
  evaluation_days:
    type: number
    description: "评估窗口天数"
    default: 7
---

# 投放ROI评估

## 目的

每日自动评估各广告渠道的投入产出比，识别高效和低效渠道，生成具体的预算调整建议。
避免人工逐一检查几十个渠道的低效操作，确保异常ROI能及时被发现。

## 数据输入

| 数据名称 | 来源 | 刷新频率 |
|---|---|---|
| 各渠道消耗与转化 | 广告平台API | 每日 |
| 历史ROI基线 | 数据仓库 | 每周 |
| 渠道预算配置 | 运营系统 | 按需 |

## 执行步骤

### Step 1: 数据完整性检查

  ├─ 所有渠道昨日数据已回传 且 消耗 > 0 → 数据就绪
      动作: 进入ROI计算
      → 进入 Step 2
  ├─ 部分渠道数据延迟（缺失 < 30%） → 数据不完整但可用
      动作: 标注缺失渠道，使用可用数据继续
      → 进入 Step 2
  └─ 数据缺失 >= 30% 或 数据源不可访问 → 无法评估
      动作: 告警数据团队，跳过本次评估

### Step 2: 过滤有效渠道

  ├─ 渠道消耗 >= {min_spend} → 纳入评估
      动作: 计算ROI = 收入 / 消耗
      → 进入 Step 3
  └─ 渠道消耗 < {min_spend} → 样本不足
      动作: 标记为"数据不足，暂不评估"，不参与排名

### Step 3: ROI分级判定

  ├─ ROI > {roi_green_threshold} → 绿灯：表现优秀
      动作: 建议加预算10-20%，标记为可扩量渠道
  ├─ ROI > {roi_yellow_threshold} → 黄灯：表现合格
      动作: 维持当前预算，持续观察
  ├─ ROI > {roi_red_threshold} → 橙灯：表现欠佳
      动作: 建议减预算20-30%，排查素材和定向
  └─ 其他情况（ROI <= {roi_red_threshold}） → 红灯：严重亏损
      动作: 建议暂停投放，人工排查原因

## 反例

1. **误判场景**: 新渠道刚启动3天，ROI=0.5被判红灯要求暂停。
   **正确做法**: 新渠道冷启动期ROI天然偏低，应看消耗是否达到{min_spend}。未达标不应判定。
   来源: 投放运营经验

2. **误判场景**: 大促期间ROI=0.9（低于日常的1.5），被判为表现欠佳。
   **正确做法**: 大促期间获客成本天然升高，应对比同期基线而非日常基线。
   来源: 2025年双11复盘

3. **误判场景**: 某渠道ROI=3.0（极高），建议大幅加预算。
   **正确做法**: 极高ROI可能是数据延迟（转化已回传但消耗未结算），应等T+2数据确认。
   来源: 广告平台结算延迟

## 输出

| 输出项 | 格式 | 接收人 | 审批级别 |
|---|---|---|---|
| ROI日报 | dingtalk_card | 投放运营群 | R2 |
| 预算调整建议 | json | 投放优化系统 | R3 |
| 异常渠道告警 | text | 投放负责人 | R2 |

## 测试用例

### 正常_全部渠道绿灯

**输入:**
```json
{
  "channels": [
    {"name": "抖音信息流", "spend": 50000, "revenue": 85000},
    {"name": "百度搜索", "spend": 30000, "revenue": 48000}
  ],
  "roi_green_threshold": 1.5,
  "min_spend": 1000
}
```

**期望输出:**
```json
{
  "evaluated_channels": 2,
  "green_count": 2,
  "red_count": 0,
  "suggestions": [
    {"channel": "抖音信息流", "roi": 1.7, "level": "green", "action": "建议加预算10-20%"},
    {"channel": "百度搜索", "roi": 1.6, "level": "green", "action": "建议加预算10-20%"}
  ]
}
```

### 边界_ROI恰好在阈值上

**输入:**
```json
{
  "channels": [
    {"name": "渠道A", "spend": 10000, "revenue": 15000}
  ],
  "roi_green_threshold": 1.5,
  "roi_yellow_threshold": 1.2,
  "min_spend": 1000
}
```

**期望输出:**
```json
{
  "suggestions": [
    {"channel": "渠道A", "roi": 1.5, "level": "yellow", "action": "维持当前预算"}
  ]
}
```

### 异常_数据大面积缺失

**输入:**
```json
{
  "channels": [
    {"name": "渠道A", "spend": null, "revenue": null},
    {"name": "渠道B", "spend": null, "revenue": null},
    {"name": "渠道C", "spend": 10000, "revenue": 18000}
  ]
}
```

**期望输出:**
```json
{
  "status": "data_incomplete",
  "missing_ratio": 0.67,
  "alert": "数据缺失超过30%，跳过本次评估",
  "evaluated_channels": 0
}
```
```

---

## 2. 客服运营 — 服务质量监控

```markdown
---
name: service-quality-monitor
department: 客服
trigger_type: cron
risk_level: R2
description: >
  监控客服服务质量核心指标（首响时间、满意度、解决率），
  自动识别异常客服和异常时段，生成改进建议。
params:
  frt_warning_sec:
    type: number
    description: "首响警戒线（秒），超过此值标记为需改善"
    default: 30
  frt_extreme_sec:
    type: number
    description: "极端首响线（秒），超过此值视为排班问题而非个人问题"
    default: 1800
  satisfaction_warning:
    type: number
    description: "满意度警戒线（0-100），低于此值触发关注"
    default: 85
  resolution_warning:
    type: number
    description: "一次解决率警戒线（%），低于此值触发关注"
    default: 70
---

# 客服服务质量监控

## 目的

自动监控客服团队的核心服务指标，识别需要改善的个人和时段，避免人工翻看报表遗漏问题。
重点防范「均值陷阱」— 不能只看平均值，必须看分布和极端值。

## 执行步骤

### Step 1: 数据完整性与极端值预处理

  ├─ 数据完整 且 有效会话 > 100 → 样本充足
      动作: 识别并标记极端值（> {frt_extreme_sec}），单独统计
      → 进入 Step 2
  ├─ 有效会话 10~100 → 样本偏少
      动作: 标注"样本不足，结论仅供参考"
      → 进入 Step 2
  └─ 有效会话 < 10 或数据缺失 → 无法分析
      动作: 跳过本次监控，不产出报告

### Step 2: 首响时间分析

  ├─ 剔除极端值后有效均值 <= {frt_warning_sec} 且 中位数 <= 10s → 服务优秀
      动作: 无需干预
  ├─ 有效均值 > {frt_warning_sec} 或 中位数 > 30s → 服务需改善
      动作: 列出响应最慢的TOP5客服 + 慢响应集中的时段
  └─ 极端值占比 > 10% → 排班缺口
      动作: 建议增加夜间自动回复或安排轮值
      → 进入 Step 3

### Step 3: 综合评估与输出

  ├─ 所有指标正常 → 生成简报
      动作: 简短周报，标注"服务水平正常"
  └─ 存在异常指标 → 生成详细报告
      动作: 包含异常明细 + 改进建议 + 原始数据

## 反例

1. **误判场景**: 合成的 20 条首响记录中，19 条为 10 秒、1 条为 4610 秒，全量均值为 240 秒。
   **正确做法**: 保留全量结果并核对长等待原因。只有符合事先确认的统计口径时才另列排除该条后的 10 秒均值，不能据此直接认定实际客服表现。
   来源: 本文构造的响应时长示例；不对应某家企业或平台的实测结果。

2. **误判场景**: 某客服首响平均120秒，远高于团队均值，判定该客服需要培训。
   **正确做法**: 检查该客服的班次。如果是夜班唯一值班人员，接到大量积压消息导致均值偏高，不应追责。
   来源: 客服运营管理经验

## 输出

| 输出项 | 格式 | 接收人 | 审批级别 |
|---|---|---|---|
| 服务质量日报 | dingtalk_card | 客服主管群 | R2 |
| 异常客服明细 | json | 客服管理系统 | R2 |
| 排班优化建议 | text | 运营经理 | R2 |
```

---

## 3. 营销 — 内容效果分析

```markdown
---
name: content-performance-analyzer
department: 社媒
trigger_type: cron
risk_level: R1
description: >
  分析社媒内容发布效果，识别高效内容类型和发布时段，
  生成内容策略优化建议。仅做数据分析和建议，不执行任何修改操作。
params:
  engagement_baseline:
    type: number
    description: "互动率基线（%），低于此值的内容标记为低效"
    default: 2.0
  viral_threshold:
    type: number
    description: "爆款阈值（互动率%），超过此值的内容标记为爆款"
    default: 10.0
  min_impression:
    type: number
    description: "最小曝光量，低于此值不参与效果评估"
    default: 500
  analysis_days:
    type: number
    description: "分析窗口天数"
    default: 7
---

# 内容效果分析

## 目的

自动分析近期社媒内容的互动数据，找出什么类型的内容在什么时段发布效果最好，
为内容团队提供数据驱动的选题和排期建议。

## 执行步骤

### Step 1: 数据拉取与清洗

  ├─ 窗口内有效内容 >= 10 条 → 样本充足
      动作: 按内容类型、发布时段分组统计
      → 进入 Step 2
  └─ 有效内容 < 10 条 → 样本不足
      动作: 提示"近{analysis_days}天发布量不足，建议扩大窗口"

### Step 2: 效果分级

  ├─ 互动率 > {viral_threshold} → 爆款内容
      动作: 提取爆款特征（类型/时段/话题标签），加入复用库
  ├─ 互动率 > {engagement_baseline} → 合格内容
      动作: 标记为正常表现
  └─ 互动率 <= {engagement_baseline} → 低效内容
      动作: 分析低效原因（发布时段不佳？内容类型不匹配？标题不吸引？）

### Step 3: 生成优化建议

  ├─ 爆款占比 > 20% → 内容策略有效
      动作: 总结爆款共性，建议保持当前方向
  ├─ 低效占比 > 50% → 内容策略需调整
      动作: 给出具体优化建议（调整发布时间/内容类型/互动引导）
  └─ 其他情况 → 表现中等
      动作: 标注待改进方向

## 输出

| 输出项 | 格式 | 接收人 | 审批级别 |
|---|---|---|---|
| 内容效果周报 | dingtalk_card | 社媒运营群 | R1 |
| 爆款内容特征 | json | 选题系统 | 自动 |
| 低效内容分析 | markdown | 内容负责人 | R1 |
```

---

## 4. 财务 — 文档结构化抽取

```markdown
---
name: invoice-extractor
department: 财务
trigger_type: webhook
risk_level: R3
description: >
  从发票/合同PDF中自动抽取关键字段（金额、日期、供应商、税号等），
  推送到财务系统。抽取不确定的字段标记为需人工复核。
params:
  confidence_threshold:
    type: number
    description: "字段抽取置信度阈值（0-1），低于此值标记为需复核"
    default: 0.85
  required_fields:
    type: string
    description: "必填字段列表，逗号分隔"
    default: "invoice_number,amount,date,supplier_name"
  max_file_size_mb:
    type: number
    description: "最大文件大小（MB），超过则拒绝处理"
    default: 50
---

# 发票/合同结构化抽取

## 目的

自动从上传的发票、合同PDF中抽取关键业务字段，减少财务人员手工录入。
对于抽取置信度不够的字段，不自动推送而是标记为需人工复核，确保准确性。

## 执行步骤

### Step 1: 文件验证

  ├─ 文件类型在白名单（pdf/docx/xlsx）且大小 < {max_file_size_mb}MB 且可读 → 通过
      动作: 加载文档进入抽取
      → 进入 Step 2
  ├─ 文件加密 → 需解密
      动作: 通知上传人提供密码
  ├─ 文件损坏或格式不支持 → 终止
      动作: 写错误日志，通知上传人重新上传
  └─ 文件过大 → 拒绝
      动作: 提示"文件超过{max_file_size_mb}MB，请压缩后重新上传"

### Step 2: 字段抽取与置信度评估

  ├─ 所有必填字段（{required_fields}）抽取成功 且 置信度 >= {confidence_threshold} → 直接推送
      动作: 构造payload推送到财务系统
  ├─ 必填字段完整 但 部分置信度 < {confidence_threshold} → 需复核
      动作: 高亮低置信度字段，进入复核队列
  └─ 必填字段缺失 → 需人工补录
      动作: 标记缺失字段，通知上传人补充

## 反例

1. **误判场景**: 扫描件中金额"￥12,345.00"被OCR识别为"￥12.345.00"（小数点和千分位混淆）。
   **正确做法**: 金额字段必须做格式校验（中国大陆千分位用逗号，小数点用句号），异常格式走人工复核。
   来源: 历史OCR错误案例

2. **误判场景**: 合同中有多个日期（签约日、生效日、到期日），系统抽取了到期日作为合同日期。
   **正确做法**: 当文档中存在多个日期时，需要结合上下文判断。无法确定时标记所有候选日期供人工选择。
   来源: 合同抽取实际案例

## 输出

| 输出项 | 格式 | 接收人 | 审批级别 |
|---|---|---|---|
| 抽取结果 | json | 财务系统 | R3 |
| 复核任务 | json | 复核队列 | R3 |
| 抽取失败通知 | text | 上传人 | R1 |
```

---

## 附录 A：SKILL.md 完整格式参考

```markdown
---
# === 必填 ===
name: my-skill-name              # Skill唯一标识，英文中划线命名
description: >                    # 一句话描述（Agent触发依据）
  做什么 + 适用场景 + 不适用场景

# === 推荐 ===
department: EC                   # 所属部门（EC/SEM/社媒/品牌/CRM/公共）
trigger_type: cron               # 触发方式（manual/cron/webhook/event）
risk_level: R2                   # 风险等级（R1/R2/R3/R4）
approval_level: 1                # 审批层级

# === 可选 ===
trigger_expression: "0 18 * * *" # cron表达式（trigger_type=cron时）
reviewer: ["张三"]               # 审核人列表
reviewer_role: "ai_engineer"     # 审核角色
decision_mode: any_of            # 审核模式（any_of/all_of/independent）

# === 参数 ===
params:
  my_threshold:
    type: number                 # string/number/boolean
    description: "参数说明"
    default: 1.5
    required: false              # 是否必填

# === 元信息 ===
metadata:
  version: "1.0.0"
---

# Skill 名称

## 目的

[用2-3句话说清这个Skill解决什么业务问题]

## 数据输入

| 数据名称 | 来源 | 刷新频率 |
|---|---|---|
| 数据A | API/数仓/用户输入 | 每日/实时/按需 |

## 执行步骤

### Step 1: [步骤名称]

  ├─ [条件A] → [结论A]
      动作: [具体动作]
      → 进入 Step 2
  ├─ [条件B] → [结论B]
      动作: [具体动作]
  └─ 其他情况 → [兜底结论]
      动作: [兜底动作]

### Step 2: [步骤名称]
...

## 参数影响

| 参数 | 影响的步骤 | 说明 |
|------|-----------|------|
| my_threshold | Step 1 | 调大→更宽松，调小→更严格 |

## 反例

1. **误判场景**: [描述]
   **正确做法**: [描述]
   来源: [来源]

## 输出

| 输出项 | 格式 | 接收人 | 审批级别 |
|---|---|---|---|
| 报告 | dingtalk_card | 业务群 | R2 |

## 测试用例

### 正常_场景描述

**输入:**
```json
{"key": "value"}
```

**期望输出:**
```json
{"result": "expected"}
```

**断言:**
- `output.result == "expected"`
```

---

## 附录 B：SkillForge 内置质量工具速查

| 工具 | API | 用途 |
|------|-----|------|
| Lint 检查 | `POST /api/skills/{id}/lint` | 8 项静态规则检查 |
| AI 验证 | `POST /api/skills/{id}/verify-ai` | LLM 模拟执行验证逻辑一致性 |
| 发布就绪 | `POST /api/skills/{id}/publish-readiness` | 完整质量门禁 |
| 健康评分 | `GET /api/skills/{id}/health-score` | 5维健康评分（0-100） |
| 漂移检测 | `GET /api/skills/{id}/drift-check` | 数据源参数漂移检测 |
| 参数调优 | `POST /api/skills/{id}/param-tune` | 网格搜索最优参数 |
| 影响预估 | `POST /api/skills/{id}/impact-estimate` | 变更前后差异预估 |
| 异常检测 | `GET /api/skills/{id}/guardian/anomalies` | 运行时异常检测 |
| 冲突检测 | `GET /api/skills/{id}/guardian/conflicts` | 跨 Skill 规则冲突 |
| Motif 库 | `GET /api/skills/motifs` | 可复用的决策模式库 |

---

## 附录 C：Lint 规则速查

| 规则 ID | 级别 | 说明 |
|---------|------|------|
| `metadata.name` | error | 缺少 Skill 名称 |
| `metadata.description` | error | 缺少目标描述 |
| `metadata.department` | warning | 未指定所属部门 |
| `metadata.risk_level` | warning | 未指定风险等级 |
| `branch_completeness` | error/warning | 缺少兜底分支 |
| `unreachable_branch` | error | 存在不可达分支 |
| `threshold_conflict` | warning | 阈值顺序可能导致重叠 |
| `threshold_gap` | warning | 阈值区间有未覆盖的空洞 |
| `test_coverage` | error/warning | 测试用例不足 |
| `test_coverage.antipatterns` | warning | 没有定义反例 |
| `param_usage` | info | 有 magic number 建议参数化 |
