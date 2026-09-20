# 报告设计模板与 Open Design 设计标准

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


SkillForge 的 Inbox 报告可以绑定 `report_design_template_id`，让报告生成和展示按选定设计标准组织首屏、指标、风险、证据与待办。

## 模板来源

- `app/inbox/report_design_catalog.json` 来自 `nexu-io/open-design` 的压缩索引。
- 当前索引包含 150 个 `DESIGN.md` 设计系统和 154 个 `SKILL.md` 工作流模板。
- 仅导入短摘要、色板、布局规则和 prompt 约束；不把 upstream Skill 直接发布为 SkillForge Skill。
- 来源和许可证会保存在模板元数据中：Apache-2.0、upstream URL、content hash。

## API

- `GET /api/inbox/report-design-templates`
  - 参数：`q`、`source_type=design_system|skill`、`category`、`page`、`page_size`
- `GET /api/inbox/report-design-templates/{template_id}`
  - 返回完整 `design_standard.prompt/colors/layout`。

## 报告生成接入

项目或 Skill 在生成报告时可以传：

```json
{
  "report_design_template_id": "od-design-meta",
  "reports": [
    {"title": "运营报告", "summary": "今日重点", "payload": {}}
  ]
}
```

项目 Gateway 也支持从运行输入继承：

```json
{
  "input": {"report_design_template_id": "od-skill-ui-skills"}
}
```

平台会给每条报告补充：

```json
{
  "payload": {
    "_report_design": {
      "template_id": "od-design-meta",
      "source_type": "design_system",
      "license": "Apache-2.0",
      "design_standard": {
        "prompt": "按该设计标准生成/渲染报告...",
        "colors": ["#0064E0"],
        "layout": ["首屏先给一句话结论和关键指标"]
      }
    }
  },
  "tags": ["设计模板", "design:meta"]
}
```

## 前端展示

`/inbox/reports/:id` 顶部提供“报告模板”选择器。用户可临时切换 Open Design 设计系统或 Skill 工作流模板；选择会保存为当前浏览器默认值。报告自身已绑定 `_report_design` 时优先使用报告绑定模板。
