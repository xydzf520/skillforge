# Skills 导航信息架构

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


> 日期：2026-04-05
> 目的：明确顶部一级导航 `Skills` 的域边界、信息架构和菜单归属

## 1. 结论

顶部一级导航统一使用：

- `Skills`

不使用：

- `Skill`
- `Skill 工作台`

原因：

- `Skills` 表示一个完整业务域
- 它不仅包含单个 Skill 编辑，还包含生成、调优、测试、历史、Shadow、回放和与工作流相关的入口
- `Skill 工作台` 更适合作为 `Skills` 域内的核心页面名称

## 2. 顶部导航建议

建议的顶部一级导航结构：

- `Dashboard`
- `Skills`
- `Reviews`
- `Playbooks`
- `Executions`
- `Admin`

说明：

- `Skills` 负责一切“单 Skill 生命周期”和“Skill 生成/调优”相关能力
- `Reviews` 负责跨 Skill 的审核中心
- `Playbooks` 负责全局工作流资产管理
- `Executions` 负责全局执行监控
- `Admin` 负责系统级配置

## 3. Skills 域边界

## 3.1 必须归入 Skills 的内容

下面这些页面或能力都应收口到 `Skills`：

- Skill 列表
- 新建 Skill
- 对话生成 Skill
- Skill 工作台
- 参数调优
- 输出表格编辑
- 测试样例
- 版本历史
- Shadow
- Replay
- 与当前 Skill 直接相关的工作流入口

## 3.2 不应归入 Skills 的内容

下面这些不应并入 `Skills` 顶部域：

- 全局审核中心
- 全局 Dashboard
- 全局 Playbook 列表管理
- 全局执行监控
- 系统设置
- 用户管理
- 审计日志

原因：

- 这些能力的管理粒度已经超过“单 Skill 域”
- 如果混进 `Skills`，导航语义会变得模糊

## 4. Skills 域内信息架构

建议 `Skills` 域内按以下层次组织：

```text
Skills
├── All Skills
├── New
├── Generate
├── Workbench
├── Tests
├── History
├── Shadow
├── Replay
└── Workflow Entry
```

说明：

- `All Skills`
  全量 Skill 资产入口
- `New`
  常规新建入口
- `Generate`
  对话生成完整 Skill 的入口
- `Workbench`
  核心调优工作台
- `Tests`
  测试样例、验证、回放入口
- `History`
  版本与 patch 历史
- `Shadow`
  Shadow 相关能力
- `Replay`
  历史回放能力
- `Workflow Entry`
  当前 Skill 关联工作流入口，不等于全局 Playbook 管理

## 5. 用户视角下的导航理解

## 5.1 小白用户

小白用户进入 `Skills` 后，最关心的是：

- 我怎么做一个 Skill
- 我怎么补充内容
- 我怎么应用建议

因此小白用户在 `Skills` 首页优先看到的应该是：

- `新建`
- `对话生成`
- `最近编辑`

## 5.2 专业用户

专业用户进入 `Skills` 后，最关心的是：

- 我怎么定位某个 Skill
- 我怎么调规则
- 我怎么做验证
- 我怎么进工作流

因此专业用户在 `Skills` 首页优先看到的应该是：

- `Skill 列表`
- `Skill 工作台`
- `测试 / 回放`
- `最近 patch`

## 5.3 审核用户

审核用户虽然关心 Skill，但他的主入口仍然应是：

- `Reviews`

而不是 `Skills`

`Skills` 域只保留单 Skill 的变更上下文查看能力。

## 6. 页面归属原则

## 6.1 All Skills

功能：

- 查看 Skill 列表
- 搜索
- 状态筛选
- 快速进入某个 Skill

归属：

- `Skills`

## 6.2 New

功能：

- 手工创建 Skill
- 设置基本字段
- 创建初始结构

归属：

- `Skills`

## 6.3 Generate

功能：

- 对话生成完整 Skill
- 补全缺失信息
- 生成初稿

归属：

- `Skills`

## 6.4 Workbench

功能：

- 定向对话修改
- 结构化编辑
- patch 预览
- 验证与应用

归属：

- `Skills`

## 6.5 Tests / History / Shadow / Replay

这些页面都属于单 Skill 生命周期的一部分，因此统一归属：

- `Skills`

## 6.6 Workflow Entry

这里指的是：

- 从当前 Skill 出发查看和调整关联工作流

归属：

- `Skills`

但下面这些仍然归属 `Playbooks`：

- 全局 Playbook 列表
- 全局 Playbook 模板
- 跨 Skill 的流程资产管理

## 7. 推荐导航交互

## 7.1 顶部导航点击行为

点击 `Skills` 后默认进入：

- `All Skills`

如果后续把“生成 Skill”作为主推入口，也可以配置成进入 `Skills Home`，但不建议一开始就改。

## 7.2 Skills 域内二级导航

建议使用左侧域内导航或顶部二级导航，二选一统一即可。

推荐项：

- `All Skills`
- `Generate`
- `Workbench`
- `Tests`
- `History`

其中：

- `Workbench`、`Tests`、`History` 在未选择具体 Skill 时展示空状态或最近项

## 7.3 Breadcrumb 建议

示例：

- `Skills > All Skills`
- `Skills > Generate`
- `Skills > Skill Workbench > EC-投放-01`
- `Skills > Tests > EC-投放-01`
- `Skills > History > EC-投放-01`
- `Skills > Skill Workbench > Workflow > EC-投放-01`

## 8. 首页推荐内容

建议 `Skills` 首页展示以下区块：

- 最近编辑的 Skills
- 最近 patch
- 最近生成的草稿
- 最近验证失败项
- 快速开始：
  - 新建 Skill
  - 对话生成 Skill
  - 打开 Skill 工作台

## 9. 与现有系统的衔接

现有仓库里与 `Skills` 域最接近的能力包括：

- Skill 列表
- Skill 详情
- Skill 编辑
- Skill 测试
- Skill Chat
- Skill History
- Skill Shadow
- Skill Replay

建议做法：

- 不立即替换现有页面
- 先在顶部补 `Skills` 域
- 逐步把分散入口收口到 `Skills`

## 10. 一句话总结

导航上最重要的结论是：

`顶部一级叫 Skills，所有单 Skill 生命周期能力统一收口到 Skills 域里，Skill 工作台只是这个域里的核心页面。`
