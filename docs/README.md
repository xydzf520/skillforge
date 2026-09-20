# 文档导航

产品介绍从[中文 README](../README.md)或[English README](../README.en.md)开始。当前为公开预览版，先看可用范围，再选择部署或开发路径。

## 按目的阅读

| 目的 | 推荐顺序 |
|---|---|
| 了解产品与企业价值 | README → [能力地图](public/CAPABILITIES.md)／[English](public/CAPABILITIES.en.md) → [资产与训练架构](public/ARCHITECTURE.md) |
| 评估是否值得采用 | [企业价值评审与试点方案](public/ENTERPRISE_VALUE_REVIEW_20260920.md)：收益条件、总成本、替代选择及当前限制 |
| 理解数据与训练 | [资产与完整数据流](public/DATA_AND_TRAINING.md)／[English](public/DATA_AND_TRAINING.en.md)：样本、评估、训练、部署和回滚 |
| 浏览全部产品界面 | [11 张核心截图与复现说明](screenshots/README.md)，均为实际前端与合成示例数据 |
| 看懂组织协作与运行流程 | [企业运行图解](public/OPERATING_MODEL.md)／[English](public/OPERATING_MODEL.en.md)：角色交接、业务交付、Harness 调用与改进决策 |
| 在本机部署 | [部署指南](public/GETTING_STARTED.md)／[English](public/GETTING_STARTED.en.md) → [实际验证范围](public/VALIDATION.md) → 需要执行节点时再读 [Bridge 部署](../bridge/README.md) |
| 编写或复用 Skill | [创建路径与限制](spec/skill-creation-paths.md) → [编写参考](guides/skill-authoring-best-practices.md) → [统一权限要求](spec/skill-permission-unification.md) |
| 集成应用或自建 Harness | [项目、共享与 Harness](public/PROJECTS_AND_INTEGRATION.md)／[English](public/PROJECTS_AND_INTEGRATION.en.md) → [Project SDK](guides/project-gateway-sdk.md) → [编程 Harness 替换评估](public/HARNESS_REPLACEMENT.md)／[English](public/HARNESS_REPLACEMENT.en.md) |
| 维护平台 | [开发约束](../AGENTS.md) → [业务不变量](architecture/skillforge-business-invariants.md) → [迁移约束](architecture/database-migrations.md) |
| 查看产品案例与个人职责 | [产品案例](public/PORTFOLIO.md)／[English](public/PORTFOLIO.en.md)：项目与职责、设计取舍、交付证据和简历衔接 |
| 准备公开发布 | [发布边界](public/RELEASE_BOUNDARY.md) → [文档复核](public/DOCUMENTATION_REVIEW.md)／[English](public/DOCUMENTATION_REVIEW.en.md) |

## 文档类型与解释方式

| 类型 | 范围 | 如何使用 |
|---|---|---|
| 当前版本说明 | 根 README、`docs/public/` | 可用范围和验收以代码与具体验证记录为依据；候选方案不等于已接入 |
| 操作参考 | `guides/`、`bridge/README.md` | 先满足各自权限和服务前提；示例地址与模型 ID 要换成自己的配置 |
| 设计与演进记录 | `architecture/`、`spec/` 中的方案 | 状态词绑定原文版本，不能直接作为公开版安装步骤或生产验收结果 |
| 强制约束 | AGENTS、业务不变量、权限、角色与迁移规范 | 保留其权限、审核和版本要求；不能因文档历史标签而绕过 |
| 执行提示词 | `app/common/prompts/` 等运行时加载文件 | 属于产品行为配置，修改需走版本与相应回归，不能当作普通介绍文案随意润色 |
| 合成示例 | `docs/examples/projects/`、编写指南的演示案例 | 用于理解接口约定和设计方法，不作为企业实测结果、收益或性能基准 |

同名概念要区分：需求采访与骨架合成、可执行代码生成、节点执行是不同能力；知识检索、样本收集、模型训练、部署是不同阶段。旧编程运行时已经移除，相关自动文件生成路径暂不可用，但普通模型调用和手动编辑仍有独立路径。

内部运营、人员清单、线上截图及评审原件不随此版本分发。历史正文中的内部路径仅用于溯源，不是公开部署的先决条件。后续更新文档时，说明适用版本、代码入口、配置前提、真实验证范围及未完成项；中英文 README 的产品与可用状态同步更新。
