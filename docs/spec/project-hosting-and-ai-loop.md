# 项目宿主与 AI 闭环规格

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


> 日期：2026-06-03
>
> 目标：把“项目”从单一 Playbook 扩展为类似虚拟主机的轻量应用分类，让部门自己用 Codex 做的小网页/工具能在 SkillForge 内点击即用，并把调用、输出、AI 分析状态统一纳入平台记录。

## 1. 边界

- 不为每个小网页开 Docker 容器。几十/几百个小项目默认通过浏览器 iframe、静态资源或外部 URL 运行。
- SkillForge 是控制面与网关：负责项目登记、部门可见性、运行态、能力调用审计、输出回传、AI 分析和学习闭环。
- Codex 开发期的 AI 能力不能直接迁移为生产密钥。上线后项目只能通过平台 Project Gateway / Web SDK 消息协议调平台 AI、数据能力、MCP 能力。
- Playbook 保留原入口，同时作为 `type=playbook` 项目展示在一级导航“项目”内。

## 2. 当前平台实现

- 一级导航“项目”打开 `/projects`，展示项目宿主页。
- 后端新增 `/api/projects`：项目列表、自动注册、静态包上传、打开运行、运行心跳、失活修复、输出回传、AI 分析、能力调用记录。
- 新增表：`projects`、`project_versions`、`project_runs`、`project_ingress_events`、`project_capability_calls`；其中 `project_ingress_events.event_type=input/output` 同时承载项目内输入记录和输出回传记录。
- 每次项目打开会创建 `ProjectRun` 和 `ExecutionRun`，`ExecutionRun.skill_id=project:<project_id>`。
- 项目内输入可先通过 `skillforge.project.input` / SDK `input()` 记录到 `ProjectRun.input_snapshot` 和 `project_ingress_events`，不会结束运行；项目输出回传会写入 `DecisionLog`；`reports[]` 可被收件报告解析，`todos[]` 可进入待办，运行、输入、输出与决策会进入 Learning Loop。
- 平台项目不要求用户手工填写项目字段；sf/Codex 上传包或调用自动注册接口时由 manifest 决定 `project_id/name/department/entry/capabilities`；静态包未声明 `entry` 时自动识别 `web/index.html`、`dist/index.html`、`build/index.html`、`out/index.html`、`public/index.html` 或根 `index.html`；缺失后缀的静态资产路径会 fallback 到当前版本入口，支持 Vite/React/Vue/Next export 等 browser history SPA 路由刷新。
- 平台项目上传支持 tar/tar.gz/zip 静态包；未显式传 `manifest_json` 时会自动读取包内 `projectforge.yaml/.yml/.json`、`skillforge-project.yaml/.yml` 或 `manifest.yaml/.yml/.json`；`sf project submit` 同样兼容这些 manifest 别名，避免 Codex 产物必须手工改名。
- 上传后的静态网页会解包到 `SKILL_REPO_PATH/project-assets/<project_id>/<asset_version>/`，新版本入口使用 `/api/projects/assets/<project_id>/<asset_version>/...` 鉴权访问；历史 `/project-assets/...` 兼容入口也走同一鉴权解析。HTML/SVG 等可执行文档返回 CSP sandbox（不含 `allow-same-origin`）、`connect-src 'self'`、`nosniff` 和 `no-referrer`，确保部门/私有项目的页面资产不绕过平台权限、不能直连外部模型/数据接口，也不能在顶层打开时凭平台 Cookie 变成同源脚本。
- 项目包上传执行稳定性保护：压缩包大小受 `MAX_UPLOAD_SIZE` 控制，解包文件数、manifest 大小和总解包字节数有平台上限，避免 zip bomb / tar bomb 影响企业级可用性。
- 静态入口必须是可直接由浏览器运行的构建产物；平台上传和 `sf project doctor` 会阻断入口 HTML 引用 `.ts/.tsx/.jsx/.vue/.svelte` 等未构建源码的包，提示先上传 `dist/build/out`。
- 为适配 Codex/Vite/React/Next/CRA/Astro/SvelteKit/Angular 等生成网页，平台上传静态包后会把已存在于包内的根绝对静态资源引用（如 `/assets/...`、`/static/...`、`/_next/static/...`、`srcset`、CSS `url()`、无引号 `href=/...`）重写为当前项目版本的鉴权资产 URL，避免生成页在 `/api/projects/assets/...` 子路径下运行时丢失 JS/CSS；若入口 HTML 没有 Project Gateway SDK，平台会自动注入 `/project-gateway-sdk.js` 和 `/project-autowire.js`，自动建立心跳、记录页面加载/表单/按钮输入，并把页面加载、表单提交和普通按钮动作后的输出快照自动 `ingest(auto_analyze=true)` 进入 AI 循环，同时暴露 `window.SkillForgeProjectBridge` 供生成代码回传 output/report/todo/AI 调用。Autowire 还会包装项目页内 `fetch`/`XMLHttpRequest`/`navigator.sendBeacon`/`WebSocket`/`EventSource`，并桥接同源 Web Worker 内的 `fetch`，同时安全接管生成项目的 Service Worker 注册，并采集 `localStorage`/`sessionStorage`/`IndexedDB` 状态写入和运行异常，对非静态 API 请求记录脱敏后的 request input，对 response、队列结果或 WebSocket/EventSource message 记录脱敏后的 output trace（默认 `auto_analyze=false`，避免高频接口重复扣费），让 Codex 生成应用即便未手写 SDK，或把请求放进 Worker/PWA 初始化/本地状态或 IndexedDB 离线缓存，也能把关键输入、输出和异常信号经过平台且不会让 Service Worker 控制平台宿主。上传完成后平台会生成 `/api/projects/{project_id}/service` 服务契约和 `POST /api/projects/{project_id}/service/invoke` API 服务调用入口；服务调用会创建 `ProjectRun`、记录 input、通过声明的 Project Gateway AI/数据能力执行、ingest 输出并进入 DecisionLog/Learning Loop。后台可在 `ai.cheap.*` 与 `project.service_conversion.ai_enabled` 配置低成本模型，对服务契约做 AI 优化，但密钥仍只在平台侧。
- 项目包上传会扫描静态前端资源，阻断明显的前端 AI key、`sk-*` 密钥；OpenAI/Anthropic/Gemini/DashScope/DeepSeek 等公开模型端点不含密钥时允许作为 Project Autowire 接管候选并写入 `package_security.direct_ai_endpoints`。本地 `sf project doctor` 同样只阻断前端密钥，对公开端点输出 `ai_takeover.gateway_proxy_candidate`，确保用户通过 `sf project submit` 上传的 Codex 项目能交给平台接管底层 AI、数据能力与审计。
- 外部项目入口只允许 `http/https` URL 或平台相对路径，平台创建、自动注册和 `sf project init/doctor` 会拒绝 `javascript:`、`data:`、协议相对 URL、含凭据 URL 和隐藏/穿越路径，保证点击打开入口可审计且不携带前端密钥。
- 外部 URL 项目由 `POST /api/projects/auto-register` 读取 manifest 自动登记；静态项目由 `POST /api/projects/upload` 上传包并自动登记。
- 用户在项目列表中点击项目卡片或“打开使用”即可进入 `view=app` 的平台内运行页；每个项目卡片和详情页都有“分享项目”按钮，复制 `/project-run/<project_id>?share=1` 登录保护链接，Playbook 兼容项目分享其 `/playbook/<name>?share=1` 入口，只分享项目入口不携带当前用户 `run_id`；未登录访问分享链接会先进入 `/login?scan=1&reason=project_share`，提示钉钉扫码登录，登录后再打开分享项目。列表与详情只暴露当前用户可读的 latest run，若全公司/部门项目的全局最新 run 属于他人，普通用户会 fallback 到自己的最新 run，保证点击可续用自己的项目会话且不泄露他人 run；若可续用运行仍处于 `opening/running/stale/waiting_ai`，入口会带上 `run_id` 继续该运行，否则平台自动创建新的 `ProjectRun` 并在 iframe 内运行项目；直接使用页保留运行状态条，展示 run、AI/能力状态、调用数、报告/待办和心跳；“详情”优先打开最新 run 的 Run Trace/SDK/输出看板，不再为看详情误开空运行，详情页从终态 run 点击“直接打开”也必须新开一次可追踪运行。
- 项目运行页必须先创建运行上下文再加载 iframe：入口 URL 自动追加 `sf_project_id`、`sf_run_id`、`sf_gateway=postMessage`、`sf_gateway_origin`、`sf_gateway_sdk`，并通过 `skillforge.project.context` postMessage 下发 project、run、capabilities、outputs、limits、platform 和 gateway 消息协议。
- iframe 内网页可用 `/project-gateway-sdk.js` 或 `sf_gateway_sdk` 绝对地址（`window.PlatformProjectGateway`）回传结果，不需要拿平台 Cookie、AI key 或 MCP env；若不用 SDK 手写 `postMessage`，`input/ingest/heartbeat/analyze/capability` 必须携带当前 `skillforge.project.context` 下发的 `gateway_token`；项目网页通过 Gateway `ingest()` 回传输出时默认 `auto_analyze=true`，输出会立即进入平台 AI 分析循环，只有显式传 `auto_analyze:false`/`autoAnalyze:false` 时才只记录不自动分析。
- 项目列表必须服务端分页、部门筛选、搜索和运行态筛选，`page_size` 上限 100；项目页查询使用当前用户可读的最新 ProjectRun 子查询做运行态过滤、统计和分页，隐藏的他人 run 不得影响普通用户的 running/waiting/failed 过滤结果；即使混入历史 Playbook 项目，也只能按当前 mixed page 所需前缀有界读取项目行，面向几百/上千项目时不能一次性把全量项目或运行历史拉到前端。
- `project_runs` 持久化 `report_count`、`todo_count`、`capability_call_count`，列表统计通过最新运行窗口读取，避免从 JSON 输出反复解析。
- iframe 内网页可发送 `skillforge.project.capability` 消息请求 `ai.generate` / `ai.chat` 等平台 AI 能力，以及 manifest 已声明且平台白名单允许的只读数据/MCP 能力；平台服务端读取统一配置和权限，记录 `project_capability_calls`，再把结果回包给 iframe。
- 项目打开运行、输入记录、输出回传和能力调用都支持 `request_id` 幂等键；输入/输出按 `event_type + request_id` 分通道幂等，同一个业务 request_id 可先记录输入再回传输出；同一 run/request_id/channel 会先通过事务级 advisory lock 串行化，再回放已存在记录，重复点击、刷新重试或重复 postMessage 不应重复创建 ProjectRun、ExecutionRun、ingress 事件、待办或模型调用；输出回传触发的自动 AI 分析使用 `ingress:<event_id>:analyze` 作为能力调用幂等键，确保重试不会重复扣费，且异常中断后再次回放可补齐 AI 分析。
- 平台打开项目会先通过 `project_run_capacity:global` 事务级 advisory lock 串行化容量临界区，再按 `PROJECT_TARGET_CONCURRENT_RUNS` 做运行容量保护；容量满时返回 `PROJECT_CAPACITY_EXCEEDED`/429，但同一 `request_id` 的重复打开仍返回已存在 run，不因容量满重复创建。
- iframe 可主动发送 `skillforge.project.context.request` / `skillforge.project.ready` 重新获取上下文；平台会为当前 iframe/run 下发短会话 `gateway_token`，`input`、`ingest`、`heartbeat`、`close`、`analyze`、`capability` 都必须带 token 且有对应 `.result` 回包，便于网页内展示 AI 运行状态；`close/analyze/capability` 支持 `request_id` 幂等或终态回放，避免刷新或 postMessage 重试造成重复模型调用/重复释放；SDK 已封装 `ready/startHeartbeat/close/input/ingest/capability/ai/analyze` 并自动携带 token。
- Project Gateway 对输入记录、输出回传、能力输入、单次 reports/todos/proofs 数量和单个 run 的能力调用/AI 分析次数做平台级限额；限额由 `PROJECT_INPUT_MAX_BYTES`、`PROJECT_INGEST_MAX_BYTES`、`PROJECT_CAPABILITY_MAX_INPUT_BYTES`、`PROJECT_CAPABILITY_MAX_CALLS_PER_RUN`、`PROJECT_MAX_*_PER_INGEST` 配置控制，并通过列表 `runtime_policy.gateway_limits` 和 `/api/projects/runtime/status.gateway.gateway_limits` 暴露。
- iframe 内网页或项目详情页会调用 `/api/projects/runs/{project_run_id}/heartbeat` 刷新 `last_heartbeat_at`；终态运行不会被心跳改回 running，失活运行可被新心跳恢复为 running；网页关闭或 SDK `close()` 会调用 `/api/projects/runs/{project_run_id}/close`，把仍处于 `opening/running/stale` 的运行转成 `completed` 并同步完成 `ExecutionRun`，立即释放并发运行槽位。
- 平台列表和 `POST /api/projects/runs/reconcile-stale` 会把超过 120 秒未心跳的 `opening/running` 运行标记为 `stale`，并同步更新对应 `ExecutionRun`。
- `/api/projects/runtime/status` 输出当前可见项目容量、100 并发目标、千级项目目标、活跃运行、等待 AI、能力调用状态、部门分布和最近错误；项目页用该接口展示项目运行容量与 AI/Gateway 健康态。
- `/api/projects/runs/{project_run_id}/trace` 返回脱敏后的输出回传、能力调用和统一时间线，支持 `limit/offset` 和 keyset cursor 分页与总数；输入/输出 trace 与能力调用 trace 都有 `project_run_id + created_at + id` 复合索引支撑高频 run 续页；项目详情页 Gateway Trace 支持“加载更多”游标续页，用于审计和故障排查，避免大型项目 run 只能查看前 100 条记录。

## 3. 企业级规模要求

- **项目规模**：同一租户至少支持上千项目，列表按部门名称/部门 ID、创建人、类型、可见性、搜索词和运行态筛选。
- **并发运行**：默认无容器项目依赖浏览器/静态宿主，可承载至少 100 个用户同时打开项目；每次打开都要创建独立 `ProjectRun` 与 `ExecutionRun`。
- **容量保护**：创建新 `ProjectRun` 前先拿事务锁并全局修复失活运行，再按目标并发数保护入口；打开时把容量快照和锁信息写入 `ExecutionRun.metadata_json.capacity_at_open`，方便定位拥塞。
- **部门与运行隔离**：输入、输出、报告、待办、AI 分析与学习事件都必须带 `department_id/department/user_id/project_id/project_run_id` 线索；普通用户只能查看/写入自己的 `ProjectRun`，项目 owner、部门管理员/可写角色或全局管理员才能代查代处理其它用户运行；项目列表、Runtime Status、失活修复和最近错误都必须按 run 可读范围过滤，避免同部门/公司项目 run_id 被猜到后越权回传输出、消耗 AI，或通过运行态统计侧信道看到他人的项目输入输出状态。
- **平台接管 AI/数据**：项目内 AI、数据、MCP 调用统一走 Project Gateway，记录到 `project_capability_calls`；当前已接入平台 AI 和白名单只读数据/MCP 能力，所有数据/MCP 能力必须通过项目包声明后再授权接入，不得在网页中保存生产密钥。
- **可观测性**：列表只显示最新运行摘要；项目页展示运行容量、AI 队列和 Gateway 健康态；详情页可追踪 run、ExecutionRun、DecisionLog、能力调用计数、最新能力调用摘要（key/type/status/耗时/脱敏 output_summary）、报告/待办计数和错误状态。
- **运行存活**：`project_runs.last_heartbeat_at` 表示网页最近一次与平台通信时间；超过平台阈值未刷新时标记为 `stale`，但不能删除历史运行，重新收到心跳后可恢复；项目网页显式 close 或平台运行页卸载时要完成仍活跃的 run，减少 120 秒失活等待带来的容量占用。
- **审计脱敏**：Gateway Trace 可看见 request_id、输入/输出摘要和能力结果，但 gateway_token、token、cookie、password、secret、api key 等敏感字段必须脱敏；网关会话 token 只用于当前 iframe/run 的 postMessage 特权调用校验，不得持久化。
- **幂等稳定性**：浏览器刷新、iframe 重试、网络抖动导致的重复 `request_id` 要返回已记录结果；输入/输出按 `project_run_id + event_type + request_id` 唯一，能力调用按 `project_run_id + request_id` 唯一，并按 `project_gateway:<channel>:<run_id>:<request_id>` 串行化，不能重复扣费、重复待办或重复进入学习事件。
- **网关限额**：过大的输入、输出或能力载荷直接返回 `PROJECT_PAYLOAD_TOO_LARGE`，能力循环超过单 run 次数上限返回 `PROJECT_CAPABILITY_DENIED`/429；过大的输入/输出请求不会保存原始大 payload，但会写入仅含大小摘要的 failed `project_ingress_events` 并进入 Learning Loop，过大的能力请求同样只写入大小摘要到 failed `project_capability_calls`，同一 `request_id` 回放保持幂等；超过次数上限的能力请求也会写入 failed `project_capability_calls` 并进入 Learning Loop，便于审计网页内无限循环或异常重试，防止单个小网页拖垮平台或无限消耗 AI/数据成本。
- **学习闭环**：项目打开输入（`project.run.opened`）、项目内输入记录（`project.input.received`）、输出回传（`project.ingress.received`）、DecisionLog、ExecutionRun 和项目能力调用都必须进入 Learning Loop；输出 `reports[]` 会直接沉淀为 `report_summary` 学习资产，输出/待办会形成 `training_sample`，成功能力调用会形成 `agent_memory`，失败输入/输出/能力调用会形成 `ai_system_gap_candidate` 改进候选。能力调用失败也必须先持久化 `project_capability_calls` 并返回 `call_id/status`，让项目内状态条、Trace 和改进候选能看见失败原因。

## 4. 推荐项目包约定（平台上传）

```text
projectforge.yaml
web/ 或 dist/ 或 public/ 或根 index.html  # Codex/Vite/静态产物
README.md
```

`projectforge.yaml` 示例：

```yaml
project_id: ec-dashboard
name: EC Dashboard
kind: web_static        # web_static / external_web / dashboard / internal_tool
visibility: department  # department / company / private
entry: web/index.html      # 可省略；平台/sf 会自动识别 web/dist/public/root index.html
capabilities:
  - ai.analyze
  - mcp://skillforge_org_search_users
outputs:
  reports: true
  todos: true
  proofs: true
```

可运行示例已放在 `docs/examples/projects/`：

- `ai-report-dashboard/`：上传后在项目页点击运行，验证 `input -> ai.generate -> ingest(reports/todos/proofs) -> 平台 AI 自动分析`。
- `org-data-search/`：上传后在项目页点击运行，验证 `input -> mcp://skillforge_org_search_users -> ingest(reports/proofs)`。
- `scripts/register_github_codex_project_samples.py --limit 100 --no-auto-analyze`：从真实 GitHub Codex/AI 项目语料下载仓库签名，扩展为 100 个不同入口、资源引用、框架输出和应用形态的上传项目，逐个验证静态资源重写、Gateway 注入、运行评估、报告/待办/proofs，并通过 `/service/invoke` 证明上传后可自动转 API 服务；`docs/examples/projects/github-codex-registration-result.json` 保存源仓库、变体和服务 trace 证据。
- `scripts/register_codex_project_type_matrix.py --limit 100`：生成并上传 100 个 Codex 风格项目类型（20 个业务域 × 5 种应用形态，覆盖 Vite/Next 静态导出/Svelte/Monorepo 等入口和根路径资源语法），逐个创建 `ProjectRun`、记录部门输入、回传输出/报告/待办/proofs，并通过 `/service/invoke` 再完成一次上传项目转 API 服务调用，把 `docs/examples/projects/codex-project-type-matrix-result.json` 作为企业级适配证据；`scripts/verify_platform_capability_matrix.py` 会读取该结果，要求 100 个类型全部上传、运行评估通过、结果评估通过、转服务调用通过。
- `scripts/register_project_ai_takeover_matrix.py --limit 100`：生成 100 个包含 OpenAI/Anthropic/Gemini/DashScope/DeepSeek 公开模型端点的 Codex 项目变体（fetch/XHR/beacon/Worker/module），不包含任何前端密钥；上传后验证静态扫描发现直连模型端点、Project Autowire 已注入、`ai_provider_gateway_proxy` 接管通过，并通过 `/service/invoke` 证明输入输出仍经过平台。
- `scripts/register_project_enterprise_scale_inventory.py --project-count 1000`：创建/更新 1000 个部门隔离项目目录样本和 100 个完成运行样本，验证服务端分页查询、部门可见性、输入/输出 ingress 留痕和运行状态统计；`docs/examples/projects/project-enterprise-scale-inventory-result.json` 作为千级项目目录证据并纳入平台能力矩阵。

## 5. 平台上传与后续命令规划

- 平台项目页只提供项目包接入；不把“手工填写项目字段”作为主路径。
- 静态项目上传 API：`POST /api/projects/upload`，字段为 `package`，兼容 sf 传可选 `manifest_json/package_hash`；上传只写数据库和静态资产目录，不需要重启平台服务。若同一 `project_id` 重复上传相同 manifest `version` 但包 hash 已变化，平台自动把实际版本记为 `version+hash前缀`，避免 sf/Codex 反复提交同一语义版本时因唯一键冲突导致 500。
- 外部项目自动注册 API：`POST /api/projects/auto-register`，字段为 `manifest`；适合 sf/Codex 产物登记外部入口。
- sf/Codex CLI 项目入口使用 CLI token 调 `/api/codex/projects/upload`、`/api/codex/projects/auto-register`、`/api/codex/projects/{project_id}/status`、`/api/codex/projects/{project_id}/service/invoke` 和 `/api/codex/projects/runs/{project_run_id}/logs`；`sf project submit` 对含 `entry_url` 的外部/仪表盘项目走自动登记，对本地静态包走上传；`sf project invoke <project_id> --input-json '{...}'` 可把上传项目作为平台 API 服务调用并返回 `project_run_id/app_url/trace_url`；若入口仍引用 Vite/React/Vue 等未构建源码且 `package.json` 有 `scripts.build`，sf 会先自动执行本地构建并上传 `dist/build/out/public` 中可运行入口（可用 `--no-build` 关闭），自动构建命令、原始入口和构建入口会写入项目/版本 metadata 供审计；响应返回 `app_url/trace_url` 供用户从 Codex 结果直接点击进入平台运行或审计页面；`logs` 支持 `limit/offset` 与 `next_*_cursor` 分页读取大型 run 的脱敏 trace；不能依赖浏览器 Cookie 或直接写平台静态目录。
- 后续可扩展平台 CLI/SDK，但所有入口都必须调用平台 Project Gateway，不得绕过项目权限、运行记录和能力审计。

## 6. 平台 Web SDK

- 平台前端构建产物提供 `/project-gateway-sdk.js`，上传项目可直接 `<script src="/project-gateway-sdk.js"></script>` 引入。
- SDK 暴露 `window.PlatformProjectGateway`，兼容别名 `window.SkillForgeProject` / `window.SFProjectGateway`，但项目主路径应使用平台命名。
- 详细用法见 `docs/guides/project-gateway-sdk.md`。

## 7. 禁止事项

- 项目网页不得写入或读取平台 `.env`、AI key、MCP env、业务 Cookie；静态项目资产即使被顶层打开也必须保持沙箱隔离，不能以 SkillForge 同源身份调用平台 API。
- 项目网页不得直接发钉钉、直连业务系统或绕过 SkillForge MCP Gateway。
- 个人/部门项目不得越权读取其它部门数据；列表和详情必须按项目可见性过滤，运行详情和输入/输出/能力调用还必须按 ProjectRun 所属用户隔离。
- 不得把项目运行状态等同于 Skill 发布状态；项目运行态以 `project_runs.status` 为准。
