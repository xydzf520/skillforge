# Skills 域路由与页面归属定义

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


> 日期：2026-04-05
> 目的：明确 `Skills` 顶部域下的前端路由、页面归属、跳转规则和 breadcrumb 规范

## 1. 路由设计目标

路由设计需要满足 4 个目标：

1. 顶部一级导航统一叫 `Skills`
2. 所有 Skill 相关页面收口到 `/skills/*`
3. 兼容当前已有旧路由，不一次性打断现有流程
4. 后续可逐步把旧入口迁移到新域

## 2. 顶部域定义

顶部一级域：

- `/skills`

导航名：

- `Skills`

这是前端用户看到的统一入口。

## 3. 路由分层

建议分成 4 层：

### L1. 域首页层

- `/skills`

用途：

- Skills 域首页
- 展示最近编辑、最近 patch、快速入口

### L2. 资产列表层

- `/skills/all`

用途：

- Skill 列表页

### L3. 生成与创建层

- `/skills/new`
- `/skills/generate`

用途：

- 手工新建
- 对话生成完整 Skill

### L4. 单 Skill 工作层

- `/skills/workbench/:id`
- `/skills/tests/:id`
- `/skills/history/:id`
- `/skills/shadow/:id`
- `/skills/replay/:id`
- `/skills/workflow/:id`

用途：

- 针对某个 Skill 的工作台、测试、历史、Shadow、Replay、工作流入口

## 4. 建议最终路由表

## 4.1 域首页

- `/skills`
  - Skills 首页

## 4.2 列表与创建

- `/skills/all`
  - Skill 列表
- `/skills/new`
  - 手工新建 Skill
- `/skills/generate`
  - 对话生成完整 Skill

## 4.3 单 Skill 工作区

- `/skills/workbench/:id`
  - Skill 工作台
- `/skills/tests/:id`
  - 测试与验证
- `/skills/history/:id`
  - 版本与 patch 历史
- `/skills/shadow/:id`
  - Shadow 运行
- `/skills/replay/:id`
  - 历史回放
- `/skills/workflow/:id`
  - 与该 Skill 直接相关的工作流入口

## 4.4 旧路由兼容

当前已有旧路由大概率仍保留一段时间。

建议兼容映射：

- `/skill/:id` -> 可保留
- `/skill/:id/edit` -> 可保留
- `/skill/:id/test` -> 可保留
- `/skill/:id/chat` -> 可保留
- `/skill/:id/history` -> 可保留
- `/skill/:id/shadow` -> 可保留
- `/skill/:id/replay` -> 可保留

但产品入口和新导航应全部指向 `/skills/*`

## 5. 跳转策略

## 5.1 顶部导航点击

点击顶部 `Skills`：

- 默认进入 `/skills`

## 5.2 Skills 首页内跳转

从首页可跳：

- 最近 Skill -> `/skills/workbench/:id`
- 新建 -> `/skills/new`
- 对话生成 -> `/skills/generate`
- 查看全部 -> `/skills/all`

## 5.3 生成后的跳转

从 `/skills/generate` 生成草稿后：

- 默认跳转 `/skills/workbench/:id`

## 5.4 列表后的跳转

从 `/skills/all` 点击某一项后：

- 默认跳转 `/skills/workbench/:id`

## 5.5 工作台内跳转

从 `/skills/workbench/:id` 内可跳：

- 测试 -> `/skills/tests/:id`
- 历史 -> `/skills/history/:id`
- Shadow -> `/skills/shadow/:id`
- Replay -> `/skills/replay/:id`
- 工作流 -> `/skills/workflow/:id`

## 6. Breadcrumb 规范

建议统一规则：

- 顶部域永远显示 `Skills`
- 当前页面显示功能名
- 如果是单 Skill 页面，最后一段显示 Skill ID

## 6.1 示例

- `/skills`
  - `Skills`
- `/skills/all`
  - `Skills > All Skills`
- `/skills/new`
  - `Skills > New`
- `/skills/generate`
  - `Skills > Generate`
- `/skills/workbench/EC-投放-01`
  - `Skills > Skill Workbench > EC-投放-01`
- `/skills/tests/EC-投放-01`
  - `Skills > Tests > EC-投放-01`
- `/skills/history/EC-投放-01`
  - `Skills > History > EC-投放-01`
- `/skills/workflow/EC-投放-01`
  - `Skills > Workflow > EC-投放-01`

## 7. 页面归属表

| 页面 | 路由 | 顶部域 | 是否单 Skill 上下文 |
|---|---|---|---|
| Skills 首页 | `/skills` | Skills | 否 |
| Skill 列表 | `/skills/all` | Skills | 否 |
| 手工新建 | `/skills/new` | Skills | 否 |
| 对话生成 | `/skills/generate` | Skills | 否 |
| Skill 工作台 | `/skills/workbench/:id` | Skills | 是 |
| 测试与验证 | `/skills/tests/:id` | Skills | 是 |
| 历史 | `/skills/history/:id` | Skills | 是 |
| Shadow | `/skills/shadow/:id` | Skills | 是 |
| Replay | `/skills/replay/:id` | Skills | 是 |
| 工作流入口 | `/skills/workflow/:id` | Skills | 是 |

## 8. 与其他顶部域的边界

## 8.1 Reviews

归属：

- 跨 Skill 的审核中心

不进 `Skills`。

## 8.2 Playbooks

归属：

- 全局工作流资产管理

不进 `Skills`。

但从 `Skills` 的某个 Skill 可以跳到对应 workflow 入口。

## 8.3 Executions

归属：

- 全局执行监控

不进 `Skills`。

## 8.4 Admin

归属：

- 系统设置、用户、审计、全局规则

不进 `Skills`。

## 9. 权限建议

## 9.1 域级访问

默认只要登录即可进入 `Skills` 域。

## 9.2 页面级权限

### 可所有登录用户访问

- `/skills`
- `/skills/all`
- `/skills/workbench/:id`

前提：必须通过部门权限或对象权限校验。

### 需要更高权限

- `/skills/new`
- `/skills/generate`

建议：

- `admin`
- `ai_engineer`
- `aibp`

### 需要更细粒度权限

- `/skills/workflow/:id`

建议：

- `admin`
- `ai_engineer`
- `aibp`

## 10. 前端实现建议

建议在现有前端中新增：

- 顶部一级导航 `Skills`
- `SkillsLayout` 或 `SkillsDomainHome`
- `/skills/*` 路由组

同时保留旧 `/skill/:id/*` 路由一段时间，做兼容跳转或平滑迁移。

## 11. 迁移策略

建议分两步：

### Step 1

- 新增 `/skills/*` 路由
- 顶部导航改为进入 `/skills`
- 旧路由继续可用

### Step 2

- 页面内部的主要按钮都改为跳新路由
- 旧路由只保留兼容跳转

## 12. 一句话总结

路由层面最重要的规则是：

`顶部一级是 Skills，所有单 Skill 生命周期页面统一进入 /skills/*，旧 /skill/* 路由只作为兼容层存在。`
