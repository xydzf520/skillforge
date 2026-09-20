# 产品截图 / Product screenshots

2026-09-20 从公开版实际 Vue / React 前端截取。原图为 1280 × 720 JPEG；没有用设计稿、图片生成或图片编辑替代产品界面。每张图片底部均标记“合成示例数据”。

Captured from the actual public-edition Vue / React frontend on 2026-09-20. The original JPEGs are 1280 × 720; no generated mockups or post-capture image edits were used. Every image carries a synthetic-data notice.

| 图片 / Image | 页面 / Page | 展示范围 / Scope |
|---|---|---|
| [能力大厅](capability-hall.jpg) | `/hall` | 部门、业务场景与能力入口 / Discovery and launch points |
| [项目应用](project-applications.jpg) | `/projects?view=list` | 部门应用、可见范围和未运行状态 / Department applications, visibility and not-run states |
| [技能建设](skill-authoring.jpg) | `/skills/new` | 业务需求、输出约定与采访路径 / Requirements and authoring paths |
| [流程画布](workflow-canvas.jpg) | `/playbook-editor/index.html?name=replenishment-check&readonly=1` | 依赖、超时、失败策略 / Dependencies, timeouts and failure policies |
| [共享 Skill 与项目命令](shared-skill-commands.jpg) | `/sf` → 命令中心 | 个人与部门 Skill 发现、安装命令和项目接入 / Skill discovery, installation commands and project integration |
| [共享 MCP 能力](shared-mcp-capabilities.jpg) | `/sf` → MCP 能力 | 工具说明、读写属性与调用入口 / Tool descriptions, access attributes and invocation entry points |
| [组织管理](organization-management.jpg) | `/admin/org`，展开示例企业并选择客服部 | 部门、项目组、主要和兼任归属 / Departments, project groups and memberships |
| [角色权限](role-permissions.jpg) | `/admin/users/demo-cs-lead`，选择权限 | 用户详情中的角色能力摘要 / Role capability summary in user details |
| [审计追溯](audit-trail.jpg) | `/admin/audit` | 审核、编辑与拒绝的合成事件 / Synthetic approval, editing and denial events |
| [数据与训练流转](learning-flow.jpg) | `/learning-flow` | 原始记录、清洗、训练、评估、部署 / Records through deployment |
| [节点管理](node-operations.jpg) | `/task-tree?anomaly=0&inst=inst-rd-1&tab=schedules&run=run-rd-active` | 节点、调度、版本、执行结果 / Nodes, schedules, versions and results |

## 完整界面浏览 / Full visual tour

### 1. 员工使用：找到能力，打开业务应用

| 能力大厅：从哪里开始 | 项目应用：如何交付给业务人员 |
|---|---|
| [![能力大厅：按部门与场景发现业务能力，合成示例](capability-hall.jpg)](capability-hall.jpg) | [![项目应用：客服质检、经营分析和知识整理的合成示例，展示部门与可见范围](project-applications.jpg)](project-applications.jpg) |
| 按部门和业务场景发现能力，让员工找到适合当前任务的入口。 | 将轻量网页、看板和内部工具组织为项目，展示部门归属、可见范围与运行状态。图中应用是未运行的目录示例。 |

### 2. 团队建设：把方法写成技能，再组合成流程

| Skill 建设：明确业务标准 | 流程编排：明确步骤与例外 |
|---|---|
| [![Skill 建设：业务描述、采访补全和输出约定，合成示例](skill-authoring.jpg)](skill-authoring.jpg) | [![Playbook 画布：步骤依赖、超时与失败策略，合成示例](workflow-canvas.jpg)](workflow-canvas.jpg) |
| 写清触发条件、判断标准、输出对象和人工确认要求；图中展示需求录入，未执行模型生成。 | 查看技能依赖、步骤顺序、超时和失败策略，让团队能够检查和维护执行方法；图中为只读预览。 |

### 3. 管理治理：明确归属、权限和操作依据

| 组织架构：谁属于哪个团队 | 角色权限：谁能在什么范围做什么 |
|---|---|
| [![组织管理：部门、项目协作组、主要及兼任成员，合成示例](organization-management.jpg)](organization-management.jpg) | [![用户权限：部门管理员的资源、操作、范围和来源摘要，合成示例](role-permissions.jpg)](role-permissions.jpg) |
| 展示部门与项目组层级、成员归属和管理者，支持理解跨部门协作关系。成员归属数包含兼任，不等于去重人数。 | 查看角色能力摘要、账号状态与部门范围。实际访问还由账号状态、组织归属及对象权限共同校验；这不是逐项勾选授权的界面。 |

**审计追溯：管理员检查谁在何时操作了哪个对象，以及拒绝或处理原因。**

[![审计日志：审核、权限拒绝和技能编辑记录，合成示例](audit-trail.jpg)](audit-trail.jpg)

按用户、操作、结果和时间定位已记录事件，为排错和复核提供线索。截图中的审核及权限拒绝是合成记录，不是本次执行或安全验收的结果。

### 4. 运行改进：观察交付，积累下一次改进依据

| 节点管理：任务在哪里执行 | 数据与训练：哪些经验值得保留 |
|---|---|
| [![节点管理：在线状态、调度、Git 版本与执行记录，合成示例](node-operations.jpg)](node-operations.jpg) | [![学习与训练数据流：原始数据、清洗、微调、测试与部署，合成示例](learning-flow.jpg)](learning-flow.jpg) |
| 对照平台版本与节点版本，查看定时状态、失败记录和运行结果。 | 将原始记录、清洗资产、训练、评估和部署放在同一视图，并保留人工审核入口；训练与部署仍需实际环境配置及验收。 |

### 5. 开发协作：共享 Skill 与工具 / Shared Skills and tools

| 在开发工具中发现共享 Skill | 在平台查看共享工具能力 |
|---|---|
| [![SF 命令中心：我的 Skill、部门 Skill 与项目接入命令，合成目录](shared-skill-commands.jpg)](shared-skill-commands.jpg) | [![MCP 能力目录：授权组织查询、数据能力与运行分析工具，合成目录](shared-mcp-capabilities.jpg)](shared-mcp-capabilities.jpg) |
| `sf` 插件把技能发现、项目接入等操作带进开发工作流；图片只展示命令，没有执行上传或安装。 | 查找工具、了解读写属性及调用入口；图片中的目录为选取的合成示例，调用数为零。 |

## 数据来源与边界 / Data and scope

- 页面由生产构建资源渲染；数据来自仓库中已有的公开 E2E / 单元测试夹具及 `scripts/screenshot_enterprise_fixtures.mjs` 的人工合成企业示例。技能需求文案为人工编写的客服质检示例。
- `serve_screenshot_preview.mjs` 只监听本机回环地址，提供合成 API 响应，不连接真实后端、企业系统、模型或运行节点。
- 技能需求评估请求仅返回固定的合成示例；其他写入请求均返回 405。没有执行技能生成、训练、审核发布或定时任务。
- 数字、日期、成功率、模型状态及建议置信度只用于展示界面。不同页面的测试夹具是独立示例，不是同一批任务的端到端证据。
- 图片不包含账号凭据、公司数据或私有部署地址。公开版限制仍以 README 的版本范围和发布复核记录为准。
- 新增项目仅展示目录与可见性，不附带可运行的业务应用；权限键与 `app/users/service.py` 的角色摘要保持一致，未修改或执行真实授权。组织页的 8 条归属对应 4 个示例用户，包含跨部门兼任。审计记录全部合成，未执行审核或制造权限拒绝。
- `sf` 页只展示命令与工具目录的合成子集，命令名称及读写属性已对照 `scripts/sf.py`、`app/codex/service.py`。没有调用模型、安装 Skill、上传项目或发放凭据；调用统计为零。SDK 能力依据源码说明，不以合成截图替代集成验收。

The production frontend renders public test fixtures and hand-authored examples from `scripts/screenshot_enterprise_fixtures.mjs`. The preview binds to loopback only and does not connect to a real backend, enterprise system, model or execution node. Requirement assessment returns a fixed synthetic response; other write requests return 405. Dates, metrics, states and confidence values are illustrative. Fixtures on different pages are independent examples, not end-to-end evidence. No credentials, company records or private deployment addresses are included.

The new projects illustrate the directory, not runnable applications. Permission keys follow the role summaries in `app/users/service.py`; no real access was changed or exercised. Eight organization memberships represent four synthetic users, including secondary assignments. All audit events are synthetic; no review or denial was executed.

The `sf` screen shows a synthetic subset of commands and tools, checked against `scripts/sf.py` and `app/codex/service.py`. No models, Skill installation, project submission or credential creation were invoked; call counts are zero. SDK documentation is based on source code, not integration acceptance inferred from screenshots.

## 本地复现 / Reproduce locally

在仓库根目录安装依赖并构建（Node 22）：

```bash
npm ci --prefix web
npm ci --prefix playbook-editor
npm run build --prefix playbook-editor
npm run build --prefix web
node scripts/serve_screenshot_preview.mjs
```

打开终端显示的本机地址，再进入上表页面。技能建设页填写合成业务需求即可；不点击生成、运行、发布等操作。节点管理页选择示例节点的“定时任务”。组织页展开“示例企业”并选择“客服部”；用户详情切到“权限”；`sf` 页打开“命令中心”或“MCP 能力”。演示仅覆盖上表页面，不是完整产品后端。

Run the commands from the repository root, then open the loopback URL printed by the server and visit the listed routes. Enter synthetic business requirements on the authoring page. Expand the example company and select the customer-service department on the organization page; choose the permissions tab in user details. Do not invoke generation, execution or publication. This preview covers the listed screens only; it is not a replacement backend. Stop it with Ctrl+C.
