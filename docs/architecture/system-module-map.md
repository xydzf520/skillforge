# SkillForge 系统模块地图

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


> 更新时间：2026-04-14
> 目标：给维护者一份“模块职责、入口、依赖、数据层、前端映射”的技术地图。

## 1. 总装配方式

系统的 composition root 在 `app/main.py`。

- 统一创建 `FastAPI` 应用。
- 启动时做数据库、缓存、PromptRegistry、自检、缓存预热、Bridge 残留清理。
- 通过 `include_router(...)` 挂载多域路由。
- 同时提供 `/metrics`、异常处理、CORS、安全响应头、SPA 静态资源。

从静态统计看，当前后端大约有：

| 指标 | 数量 |
|---|---:|
| 路由模块 | 35 |
| 模型文件 | 17 |
| HTTP 端点 | 377 |
| WebSocket 端点 | 14 |

这说明 `app/main.py` 不是简单入口，而是整个系统边界、权限和运行期策略的统一收口点。

## 2. 后端域模型总览

| 域 | 主要目录 | 入口路由 | 关键表 / 状态 | 前端对应 | 关键依赖 |
|---|---|---|---|---|---|
| 认证与权限 | `app/auth/` | `/api/auth`、`/api/abac` | `users`、`abac_policies` | 登录、改密、Admin | `bcrypt`、DingTalk OAuth |
| Skill 管理 | `app/skills/` | `/api/skills` | `skills`、`skill_locks`、资产/成员/提交相关表 | SkillHall、SkillList、SkillStudio | GitPython、模板、质量检查 |
| Workbench / Studio | `app/workbench/` | `/api/skills/*/workbench`、`/api/skills/workbench/*` | `skill_workbench_*`、`skill_drafts`、`skill_runs`、`skill_previews`、`skill_reviews` | SkillStudio 内部 AI 层 | prompts、验证器、patch/apply、coach/architect |
| Reviews / Approval / Todos | `app/reviews/`、`app/approval/`、`app/todos/` | `/api/reviews`、`/api/approval`、`/api/todos` | review、approval、todo、dispatch 相关表 | ReviewList、TodoCenter、派发页 | 审核动作、DingTalk、SLA |
| Execution | `app/execution/` | `/api/executions` | `execution_runs`、`execution_steps`、`decision_log`、`execution_workers` | 执行列表/详情、Studio 底部执行区 | Scheduler、队列、OpenClaw |
| OpenClaw / AIClaw | `app/aiclaw/` | `/api/aiclaw`、`/api/aiclaw/departments/agent-coverage`、bridge websocket | `openclaw_instances` | Agent、AdminAgentDevices 部门覆盖度、AIClawWorkspace、Agent 对话/列表/详情训练能力入口；Bridge 版本只展示自动更新状态，不提供人工更新主按钮 | Bridge、实例同步、密钥轮换 |
| Training | `app/training/` | `/api/training/resources`、`/api/training/datasets`、`/api/training/jobs*`、`/api/training/deployments*`、`/api/training/runs/{run_id}/candidate` | `training_jobs`、`training_job_tasks`、`model_deployments`、`decision_log` 统计 | 训练、数据资产、模型部署、部署详情、Run Trace 训练候选、`/training?gateway=` 网关置顶和任务过滤 | Agent/Bridge 训练能力、统一 Skill 读/编辑权限、`training.submit_job`、部署灰度/回滚 |
| Codex / sf Gateway | `app/codex/`、`scripts/sf.py` | `/api/codex/*`、`sf mcp/*`、`sf ai/*`、`sf raw/*` | `codex_cli_sessions`、`codex_mcp_call_audit`、运行原始表只读查询 | Codex 插件入口、安装页 | CLI session、MCP Gateway、平台 AI、Skill 权限 |
| Capability Hall | `app/hall/` | `/api/hall/*`、`/api/hall/direct-capabilities/*` | 直连能力元数据、`direct_capability_*` 历史/任务、直连能力个人 UI overlay | 能力大厅、Hall 直连能力页；`gpt-imagegen` 右下角悬浮 AI 图标可对话调整个人页面，页面模式切换收进更多菜单 | 统一权限、直连 runner、上传代理、声明式 UI overlay |
| TaskTree | `app/tasktree/` | `/api/task-tree*` | `task_nodes_light`、`tasktree_repair_queue`、`drift_scan_cursor` | TaskTree 页 | dispatcher、writer、repair、drift_scan |
| 数据源与治理 | `app/datasources/`、`app/browser/`、`app/compliance/` | `/api/data-sources`、`/api/browser`、`/api/data-governance`、`/api/compliance` | source、asset、policy、grant 等表 | Datasource、Governance、BrowserConnect | 文件 I/O、浏览器采集、API 发现 |
| Playbooks | `app/playbooks/` | `/api/playbooks`、playbook live WS | playbook 文件系统 + live 状态 | Playbook 列表/查看/编辑/live | iframe 子应用、WebSocket |
| 门户与组织 | `app/portal/`、`app/org/`、`app/users/` | `/api/portal`、`/api/org`、`/api/users` | market / org / user 相关表 | Portal、AdminUsers、AdminOrg | 市场化、组织隔离 |
| Dashboard / Audit / Notifications | `app/dashboard/`、`app/audit/`、`app/notifications/` | `/api/dashboard`、`/api/audit`、`/api/notifications` | usage、audit、notification 相关数据 | Dashboard、AdminAudit | 统计聚合、审计导出 |
| Agent Runtime | `app/agent_core/`、`app/coding_agent/` | `/api/agent-core`、`/api/admin/coding-agent/*` | checkpoint、session、prompt config | Agent stream、AdminSettings | LangGraph、Node 子进程 |

## 3. 关键模块职责拆解

### 3.1 `app/skills/`

这是业务定义层，不只是 Skill 列表。

- 管理 Skill 元数据、模板、成员、可见性、市场状态、Fork 关系。
- 提供文件读写、运行时发布、影子运行、lint、publish readiness、health score。
- 通过多组子路由把“管理 / 质量 / 运行 / 文件 / 大厅 / AI 辅助”拆开。

`Skill` 模型本身同时承载：

- 业务定义字段
- 组织与可见性
- 市场与展示
- 运行版本与 git commit
- fork 血缘

这意味着 Skill 在本系统里是“业务资产”，不是普通配置文件。

### 3.2 `app/workbench/`

Workbench 是系统复杂度最高的核心域。

它负责：

- 创建 session
- 解析 intent
- 生成 patch
- 验证 patch
- 应用 patch
- 保存 AI 对话与引用
- 维护草稿、预览、Review 状态

数据层至少包含：

- `skill_workbench_sessions`
- `skill_workbench_messages`
- `skill_workbench_patches`
- `skill_workbench_validation_runs`
- `skill_workbench_references`
- `skill_workbench_ai_runs`
- `skill_drafts`
- `skill_runs`
- `skill_previews`
- `skill_reviews`

这套表结构说明 Workbench 已经是一套完整的“AI 协作编辑子系统”。

### 3.3 `app/execution/` + `app/aiclaw/`

两者合起来才是完整执行域。

- `execution` 记录编排、步骤、决策、worker、对比、实例测试。
- `aiclaw` 负责实例注册、Bridge enroll、公钥轮换、能力同步、Skill 导入导出。
- Web 进程本身不直接充当最终执行器，而是控制面。

这是一个典型的“控制平面 / 运行平面”分离结构。

### 3.4 `app/tasktree/`

TaskTree 不是简单 tree view。

它实际上做了三件事：

1. 维护轻量投影读表 `task_nodes_light`
2. 对写入失败做 repair queue 补偿
3. 定时做 drift scan，追查漏写与长窗口缺口

因此它具备：

- 性能优化属性
- 一致性补偿属性
- 运维可观测属性

这也是为什么它同时需要模型、writer、dispatcher、repair_worker、metrics、ai_diagnose、value_service。

## 4. 数据层地图

### 4.1 核心业务表

| 表组 | 代表表 | 作用 |
|---|---|---|
| Skill 资产 | `skills`、`skill_locks`、成员/资产/提交相关表 | 管理 Skill 主资产 |
| Workbench | `skill_workbench_*`、`skill_drafts`、`skill_runs` | AI 协作编辑过程数据 |
| 执行 | `execution_runs`、`execution_steps`、`decision_log` | 正式运行与决策留痕 |
| Agent/Device | `openclaw_instances`、worker/queue 相关表 | 设备、实例、执行 worker；`agent_purpose` 区分部门执行、分析 Agent、训练 Agent 和混合 Agent |
| TaskTree | `task_nodes_light`、`tasktree_repair_queue`、`drift_scan_cursor` | 投影、补偿、追赶 |
| 治理 | org / approval / governance / compliance / audit / usage | 企业治理与审计 |

### 4.2 迁移阶段特征

从迁移命名可以看出系统演化重点：

- `001-011`：基础表、缺失索引、早期优化
- `012-015`：Workbench / Conversation / Studio 基础设施
- `016-024`：usage/audit/todo/bridge/审批链路补强
- `025-039`：Skill Studio runtime、组织、配额、市场、成员、审批策略
- `040-049`：治理、fork 跟踪、worker、portal、execution queue、TaskTree 索引
- `050-054`：TaskTree 轻量投影、指标、repair queue、drift scan cursor

这条迁移轨迹能直观看出：近阶段研发重心明显在 `Studio runtime + enterprise governance + tasktree stabilization`。

## 5. 前端结构地图

### 5.1 路由与导航

前端主路由在 `web/src/router/index.ts`，主域可归纳为：

| 域 | 主要路由 |
|---|---|
| Skills | `/skills`、`/skills/hall`、`/skills/list`、`/skills/:id` |
| Portal | `/portal*` |
| Reviews | `/reviews`、`/review/:id` |
| Executions | `/executions`、`/execution/:id` |
| Datasources | `/datasources*` |
| Playbooks | `/playbooks`、`/playbook/:name*` |
| Agent | `/agent`、`/aiclaw/instances/:id`、`/admin/agent-devices/:id`；Agent 终端可按部门维护多个实例，并用 `agent_purpose` 标记部门执行、分析、训练或混合用途；`/agent` 右侧直接展示当前部门执行/分析/训练覆盖度，管理按钮可跳到已预填部门与用途的新建 Agent 表单；`/api/aiclaw/departments/agent-coverage` 汇总每个部门执行/分析/训练 Agent 是否齐备、在线以及是否走平台默认 Agent 兜底，覆盖度项可直接预填部门和用途打开新建 Agent 表单 |
| 训练 | `/training`、`/training?gateway=<instance_id>`（置顶网关并过滤任务池）、`/training/jobs/:id`（回到目标网关任务池和 Agent 终端）、`/training/datasets`、`/training/models`、`/training/deployments/:id`，后端 `/api/training/resources`、`/api/training/datasets`、`/api/training/jobs*`、`/api/training/deployments*`，CLI `sf training resources/datasets/readiness/candidate/create/jobs/job/logs/artifacts/download-artifact/approve/dispatch/collect/collect-due/evaluate/deploy-request/deployments/deployment-*`；Agent 列表/详情同步展示平台侧运行中训练任务摘要；`sf training datasets/readiness/candidate` 让 Codex/sf 可按权限查看数据资产门禁并创建待审批训练候选，不返回 DecisionLog 原始正文；`sf training collect-due` 让 Codex/sf 可按网关批量补偿回调丢失的到期训练任务；模型部署支持审批、驳回、灰度激活和回滚，驳回只关闭 deployment 审批请求，不改 training job 状态；`/api/training/resources` 对部门账号返回本部门训练 Agent 与平台默认训练 Agent 兜底摘要，创建/审批/下发会在缺少目标网关时自动选择同部门训练 Agent，缺口时可使用 `agent_purpose=training + is_platform_default=true` 的平台训练 Agent 兜底 |
| Todos | `/todos*` |
| TaskTree | `/task-tree` |
| Admin | `/admin/*` |

一级导航固定为：

- `hall`
- `skills`
- `tasktree`
- `agent`
- `training`
- `inbox`

`portal` 已被并入 `skills` 的导航语义，而不是独立一级导航。

### 5.2 状态分层

| 层 | 主要文件 | 说明 |
|---|---|---|
| API 封装 | `web/src/api/request.ts`、`web/src/api/index.ts` | 统一请求、错误处理、按域导出 API |
| 文档状态 | `web/src/stores/document.ts` | Skill 文档模型与 Markdown/结构化互转 |
| Workbench 状态 | `web/src/stores/workbench.ts` 及其子 store | session / patch / validation / reference |
| UI 状态 | `web/src/stores/ui.ts` | 视图模式、布局、字号、面板开关 |
| 过渡层 | `web/src/stores/ide.ts` | 仍在使用，但已标注 deprecated |
| 页面编排 | `web/src/composables/skillstudio/*` | 把 SkillStudio 的复杂交互拆开 |

### 5.3 `SkillStudio.vue`

`SkillStudio.vue` 是前端复杂度最集中的页面。

它同时包含：

- TopBar / Navigator / EditorSurface / Assistant / BottomPanel 五大区域
- 视角切换：edit / explain / review
- 模块导航、文件树、Mermaid、Flow、inline AI、review action
- test / sandbox / shadow / optimizer / diff 等底部面板

它本质上是“前端总装配页”，相当于后端的 `app/main.py` 在 UI 层的镜像。

## 6. 子应用与外围运行时

### 6.1 `playbook-editor/`

- React + React Flow 独立子应用
- 构建到 `web/public/playbook-editor/`
- 通过 iframe 嵌入 Vue 主站
- 主站与画布通过 `postMessage` 通讯

因此 Playbook 不是主站内部组件，而是“被主站托管的独立画布 runtime”。

### 6.2 `chrome-extension/`

- MV3 扩展
- 同步登录 cookie 到 `/api/data-sources/{id}/push-cookies`
- 捕获页面 API 请求并上报 `/api/data-sources/api-discovery`

它是数据接入链路的一部分，不参与主站 UI。

### 6.3 `bridge/skillforgebridge.py`

- 部署在外部主机
- 负责 enrollment、签名重连、自动更新、自启动
- 与本地 gateway/OpenClaw 通讯
- 与 SkillForge 通过 websocket 建立控制链路

它是“设备侧代理”，不是后端进程的一部分。

### 6.4 可替换的编程 Harness（待接入）

公开版已经移除旧运行时及启动路径，不再要求构建 Node vendor 产物。
`app/coding_agent/` 保留平台编排和历史事件兼容能力；新会话明确返回“替代运行时待接入”。
选型与验收见 [Harness 替换评估](../public/HARNESS_REPLACEMENT.md)。

## 7. 关键依赖链路

### 7.1 Skill 编辑链

`SkillStudio -> workbenchApi -> app/workbench -> skill_drafts / workbench tables -> document/file services`

### 7.2 执行链

`Execution API -> execution_runs / decision_log -> OpenClaw instance -> bridge/device`

Skill Runtime Agent 路由边界：

`Skill sync / node schedules / bridge_script / openclaw_agent -> agent_purpose in skill_runtime|mixed only; analysis/training-only Agent receives no Skill files or schedules`

训练模型的可感知闭环：

`Training Jobs / Model Deployments -> artifact manifest -> /api/training/jobs/{job_id}/artifacts/{artifact_id}/download -> Bridge training.download_artifact -> local training gateway`

训练任务路由闭环：

`POST /api/training/jobs -> same-department agent_purpose=training|mixed Agent -> platform training Agent fallback -> approve freezes gateway payload -> dispatch Bridge training.submit_job`

模型对话闭环：

`Training Detail / Models -> /agent?model_deployment_id=...&department=... -> BrainHome prefers same-department analysis/mixed Agent -> model_context -> /api/aiclaw/.../chat/.../stream -> backend rehydrates model_context from model_deployments/training_jobs and enforces department + canary/active + artifact match -> chat.send(modelContext) -> department Agent`

分析 Agent 闭环：

`Skill Runtime sf.analyze -> /api/intelligence/analyze -> current execution Agent if intelligence.analyze -> same-department analysis Agent -> platform analysis Agent fallback -> platform LLM fallback -> intelligence_analyze_runs route trace`

Run Trace 会展示 `intelligence_analyze_runs.analysis_backend`、`analysis_agent_id` 和 `analysis_delegate_route`，让用户能确认本次分析是由执行节点、部门分析 Agent、平台兜底 Agent 还是平台 LLM 承接。

Agent 覆盖度闭环：

`BrainHome / AdminAgentDevices / sf agent coverage -> skillforge_agent_coverage MCP -> /api/aiclaw/departments/agent-coverage -> openclaw_instances + Bridge online + sanitized capabilities -> department execution/analysis/training coverage + platform fallback summary -> /admin/agent-devices?department=...&purpose=...&create=1`

Codex/sf 调试分析闭环：

`sf raw query -> skillforge_raw_data_query MCP -> Skill read permission -> execution_runs/steps/decision_log/collection proof/schema snapshot -> redacted raw records -> sf ai analyze -> skillforge_ai_analyze MCP -> server ai.* config -> deepseek-v4-pro 1M context -> codex_mcp_call_audit`

平台内置 MCP 编辑期分析闭环：

`skillforge_internal MCP -> skillforge_agent_coverage / skillforge_raw_data_query / skillforge_ai_analyze / skillforge_run_analyze -> SKILLFORGE_USER_ID active user -> visible department Agent coverage + Skill read permission + redaction -> server ai.* config -> deepseek-v4-pro 1M context -> AuditLog`

Codex/sf 一键运行复盘闭环：

`sf run analyze --run-id ... -> skillforge_run_analyze MCP -> Skill read permission -> redacted run/steps/decision/proof/schema context -> server ai.* config -> deepseek-v4-pro 1M context -> run diagnosis + raw_counts -> codex_mcp_call_audit`

Run Trace 页面复盘闭环：

`AdminRunTrace AI 复盘按钮 -> POST /api/admin|executions/runs/{run_id}/trace/analyze -> Run Trace access check -> same redacted context builder -> deepseek-v4-pro 1M context -> diagnosis panel + optional raw context`

Portal 执行结果复盘闭环：

`PortalSubmissionDetail -> POST /api/executions/runs/{run_id}/trace/analyze -> Run Trace owner/access check -> redacted context builder -> platform/analysis-Agent AI 复盘 -> result page diagnosis panel`

运行复盘到训练候选闭环：

`AdminRunTrace 训练候选按钮 / PortalSubmissionDetail 训练候选按钮 / sf run candidate -> POST /api/training/runs/{run_id}/candidate -> require target Skill edit + create training permission -> store redacted run lineage/raw_counts/analysis summary -> training_jobs.awaiting_review -> /training/jobs/{id}`

### 7.3 任务树链

`execution/aiclaw 事件 -> tasktree.dispatcher -> task_nodes_light`

失败时：

`dispatcher -> tasktree_repair_queue -> repair_worker -> drift_scan`

### 7.4 数据接入链

`Chrome Extension / Browser Connect -> datasources -> governance / portal / skill runtime`

## 8. 验证与工程面

当前仓库的验证表面分成三层：

| 层 | 代表位置 | 特征 |
|---|---|---|
| 后端回归 | `tests/` | 覆盖广，尤其是 skills / workbench / execution / auth / aiclaw |
| 前端主站 | `web/src/__tests__`、`web/e2e/` | 有单测、typecheck、Playwright |
| 子应用与外部包 | `playbook-editor/`、`chrome-extension/` | 以 build/typecheck 为主，自动化深度偏弱 |

另有大量工程脚本承担：

- 初始化
- 冒烟
- E2E
- 压测
- MCP server
- 数据回填

说明项目的工程控制能力并不弱，但验证入口较多，维护者需要熟悉不同子系统的验证方式。

## 9. 如何使用这份模块地图

如果你要改某一类问题，可以按下面方式定位：

| 任务类型 | 优先阅读 |
|---|---|
| Skill/Studio 交互 | `app/workbench/` + `web/src/pages/skill/SkillStudio.vue` + `web/src/stores/workbench.ts` |
| 执行与设备 | `app/execution/` + `app/aiclaw/` + `bridge/` |
| 任务树问题 | `app/tasktree/` + `docs/architecture/tasktree-data-flow.md` |
| 数据接入与治理 | `app/datasources/` + `app/browser/` + `chrome-extension/` |
| Playbook | `app/playbooks/` + `playbook-editor/` + `web/src/pages/playbook/` |
| 运维与部署 | `deploy/` + `docker-compose*.yml` + `scripts/` + `docs/operations/` |

这份文档不追求穷举所有文件，而是帮助你在进入具体实现前先建立正确的“系统分层感”。
