# skill-output-report-v1

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


> 适用范围：Skill `main.py` 返回值中的 `output.reports[i]` 单条报告对象。

## 1. 语义

- 一条 `output.reports[i]` = 一张 `/inbox` 报告卡片。
- 只有成功、非 sandbox 的执行会把 `reports` 沉淀到 `/inbox`。
- 当前 `/inbox` 可见性跟随 Skill 自身权限 / 可见性配置，**不读取** `reports[i].recipients`。
- `reports[i].recipients` 仍然保留且必填；它是给未来主动推送预留的声明字段，不是当前推送触发器。
- `recipients.roles` 文档示例统一使用 role-matrix-v2 角色：`system_admin` / `dept_admin` / `aibp` / `observer`。兼容期运行时旧角色别名仍兼容。
- `metrics` / `tags` 是可选增强字段：不写也合法，只是 `/inbox` 卡片少了指标芯片和标签筛选。

## 2. 标准对象

```json
{
  "channel": "dingtalk_card",
  "title": "华南区 ROI 周报",
  "summary": "华南区本周 ROI 1.2，低于盈亏线 1.5，建议下周预算下调 30%。",
  "content_markdown": "## 周报正文\n\n- ROI: 1.2\n- 建议: 下调预算 30%",
  "recipients": {
    "roles": ["dept_admin", "aibp"],
    "departments": ["电商运营部"]
  },
  "payload": {
    "region": "华南",
    "roi": 1.2,
    "suggestion": "下调预算 30%"
  },
  "metrics": [
    {"label": "ROI", "value": "1.2", "trend": "down", "delta": "-15%"},
    {"label": "消耗", "value": "¥82k", "trend": "flat"}
  ],
  "primary_indicator": {
    "label": "ROI",
    "value": "1.2",
    "severity": "high",
    "tone": "danger"
  },
  "tags": ["告警", "预算", "华南"]
}
```

## 3. JSON Schema

下面 schema 定义的是**单条 report item**。在 `contract.json` 中，`reports` 应声明为 `{"type": "array", "items": <本对象 schema>}`。

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "SkillOutputReportV1",
  "type": "object",
  "additionalProperties": false,
  "required": ["channel", "title", "summary", "recipients"],
  "properties": {
    "channel": {
      "type": "string",
      "enum": ["dingtalk_card", "dingtalk_markdown", "email", "feishu"]
    },
    "title": {
      "type": "string",
      "minLength": 1,
      "maxLength": 80
    },
    "summary": {
      "type": "string",
      "minLength": 1
    },
    "content_markdown": {
      "type": "string"
    },
    "recipients": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "users": {
          "type": "array",
          "items": {"type": "string", "minLength": 1}
        },
        "roles": {
          "type": "array",
          "description": "推荐使用 role-matrix-v2 角色名：system_admin / dept_admin / aibp / observer。",
          "items": {"type": "string", "minLength": 1}
        },
        "departments": {
          "type": "array",
          "items": {"type": "string", "minLength": 1}
        }
      },
      "anyOf": [
        {
          "required": ["users"],
          "properties": {
            "users": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}
          }
        },
        {
          "required": ["roles"],
          "properties": {
            "roles": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}
          }
        },
        {
          "required": ["departments"],
          "properties": {
            "departments": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}
          }
        }
      ]
    },
    "payload": {
      "type": "object"
    },
    "metrics": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["label", "value"],
        "properties": {
          "label": {
            "type": "string",
            "minLength": 1,
            "maxLength": 40
          },
          "value": {
            "type": "string",
            "minLength": 1,
            "maxLength": 80
          },
          "trend": {
            "type": "string",
            "enum": ["up", "down", "flat"]
          },
          "delta": {
            "type": "string",
            "maxLength": 40
          }
        }
      }
    },
    "primary_indicator": {
      "type": "object",
      "description": "本报告的主指标，`/inbox` 待办卡片会独立大号展示；缺省时前端回退到 `metrics[0]`。",
      "additionalProperties": false,
      "required": ["label", "value"],
      "properties": {
        "label": {
          "type": "string",
          "minLength": 1,
          "maxLength": 40
        },
        "value": {
          "type": "string",
          "minLength": 1,
          "maxLength": 80
        },
        "severity": {
          "type": "string",
          "enum": ["critical", "high", "medium", "low"]
        },
        "tone": {
          "type": "string",
          "enum": ["danger", "warning", "success", "info", "neutral"]
        }
      }
    },
    "tags": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1,
        "maxLength": 30
      }
    }
  }
}
```

## 4. 字段约束

| 字段 | 必填 | 当前 `/inbox` 用途 | 备注 |
|---|---|---|---|
| `channel` | 是 | 右上角渠道标签 | 也是未来主动推送的渠道声明 |
| `title` | 是 | 卡片主标题 | 建议含日期 / 关键指标 |
| `summary` | 是 | 卡片摘要 | 建议 2-3 句，自包含 |
| `content_markdown` | 否 | 详情页 markdown | 长文放这里 |
| `recipients` | 是 | 当前忽略 | 至少一个非空；不是 ACL 输入；若填写 `roles`，示例统一使用 `system_admin` / `dept_admin` / `aibp` / `observer` |
| `payload` | 否 | 详情页结构化数据区块 | 供前端渲染 |
| `metrics` | 否 | 卡片指标芯片 | 推荐 1-3 条，避免堆满 |
| `primary_indicator` | 否 | 待办卡片大号主指标 | 缺省时前端 fallback 到 `metrics[0]`；`severity` 决定排序权重，`tone` 决定着色 |
| `tags` | 否 | 标签展示 / 列表筛选 | 推荐 1-5 个稳定短标签 |

## 5. Authoring Rules

- 业务字段仍然保留在顶层 return dict，例如 `日报正文`、`报告摘要`；不要把全部内容只塞进 `reports[i]`。
- 一次执行可以产出多条 report；每条 report 都会单独展平成一个 `/inbox` 卡片。
- `recipients` 必填，但不要把它当作"谁能看见这张卡片"的权限源。
- 新写 `recipients.roles` 时统一使用 `system_admin` / `dept_admin` / `aibp` / `observer`；不要再示范 `biz_owner` 等旧角色值。
- 不需要卡片增强时，直接省略 `metrics` / `tags`，不要塞空占位文本。
- 想让运营一眼看到核心结论时，显式声明 `primary_indicator`。它不是 `metrics[0]` 的复制品——推荐独立组织，比如 `metrics` 继续放多维指标，`primary_indicator` 只放"本次报告最重要的一项"。前端会在 `/inbox` 待办卡片用大号字号显示；未声明时 fallback 到 `metrics[0]`。
