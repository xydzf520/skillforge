# sf / Codex / Claude 使用流程

这份文档是给 Codex、Claude Code 和其它 AI 编程助手看的。只要用户提到 `sf`、`/sf`、SkillForge Codex 插件、MCP 能力、真实数据验证、提交审核、我的插件、我的部门插件，就先按这里执行。

## 一句话原则

`sf` 是 SkillForge 控制面的本地 CLI 和 Codex 插件入口。Codex/Claude 可以用它登录、发现能力、调用授权 MCP、调试 Skill、提交审核和检查更新，但不能绕过 SkillForge 去读平台密钥、写 `skills-repo/`、直连业务系统或直接发布生产。

## 先做什么

1. 确认插件和 CLI 是最新：

```bash
sf update --check
sf update
sf plugin status
```

2. 确认登录：

```bash
sf auth status
```

如果未登录或过期：

```bash
sf auth login
```

浏览器会打开 SkillForge 授权页。用户完成钉钉登录后，CLI 会拿到 30 天 token。不要手写 token、数据库 session、Cookie 或 `.env`。

3. 刷新并查看能力：

```bash
sf catalog refresh
sf capabilities
sf platform capabilities
sf editor capabilities
sf mcp catalog
```

## 新电脑安装流程

如果机器上没有 `sf`，让 Codex 读取生产入口：

```text
请安装或更新 SkillForge Codex 插件：http://skillforge.example.com/api/codex/catalog/manifest。
读取这个入口提供的 plugin_update 信息，校验 sha256，安装到本地 Codex 插件目录，写入 Codex marketplace/config 和 sf 命令，刷新 Codex 插件缓存，让 /sf 注册成命令按钮，然后运行 sf update --check、sf plugin status、sf auth login、sf plugin doctor --fix、sf 我的插件、sf mcp catalog 验证。
```

安装后应具备：

- 插件目录：`~/plugins/skillforge-codex`
- Codex cache：`~/.codex/plugins/cache/<marketplace>/skillforge-codex/<version>/`
- 本地命令：`~/.local/bin/sf`
- Codex 插件启用项：`~/.codex/config.toml`
- marketplace：`~/.agents/plugins/marketplace.json`

如果 `/sf` 没出现在 Codex 命令按钮里，执行：

```bash
sf update --force
codex plugin add skillforge-codex@skillforge-local
```

如果 marketplace 不是 `skillforge-local`，先查：

```bash
codex plugin marketplace list
codex plugin list
```

再用当前 selector 执行 `codex plugin add skillforge-codex@<marketplace>`。重开 Codex 会话后再看 `/sf`。

## Codex 怎么用

Codex 插件已经提供 `/sf` 命令。用户输入 `/sf ...` 时，Codex 不要解释命令文本，而是映射到本地 `sf` CLI。

常用路由：

| 用户输入 | 实际执行 |
|---|---|
| `/sf update`、`/sf 更新`、`/sf 升级` | `sf update`；本机 manifest 预览用 `sf update --check --base-url <url>`，默认不持久化 |
| `/sf plugin status`、`/sf 插件状态` | `sf plugin status`；可用 `--base-url <url>` 做一次性检查 |
| `/sf plugin doctor`、`/sf 插件诊断`、`/sf 修复插件` | `sf plugin doctor`；修复本地注册时加 `--fix` |
| `/sf auth`、`/sf 我是谁` | `sf auth status`，失败再 `sf auth login` |
| `/sf 登录`、`/sf 授权` | `sf auth login` |
| `/sf mcp` | `sf mcp catalog`（默认可读摘要；原始 JSON 用 `sf mcp catalog --json`） |
| `/sf data list` | `sf data list` |
| `/sf data latest tmall` | `sf data latest tmall` |
| `/sf notify users --query 示例成员甲` | `sf notify users --query 示例成员甲` |
| `/sf notify send --title 标题 --markdown 正文 --query 示例成员4` | `sf notify send --title 标题 --markdown 正文 --query 示例成员4`，真实推送需加 `--real --idempotency-key <key>` |
| `/sf run <skill_id>` | `sf run <skill_id>` |
| `/sf runs status <run_id>` | `sf runs status <run_id>` |
| `/sf runs result <run_id>` | `sf runs result <run_id>` |
| `/sf runs logs <run_id>` | `sf runs logs <run_id>` |
| `/sf 我的插件` | `sf my` |
| `/sf 我的技能` | `sf skill list --scope mine` |
| `/sf 我的部门插件` | `sf skill list --scope department` |
| `/sf 安装 <skill_id>` | `sf skill install <skill_id>` |
| `/sf 查看哪些技能` | `sf skill list --scope visible` |
| `/sf 拉取 <skill_id>` | `sf skill pull <skill_id> --path .` |
| `/sf 查看我能编辑哪些技能` | `sf skill list --scope editable` |
| `/sf 哪些可发布` | `sf skill list --scope publishable` |
| `/sf 哪些未上传` | `sf skill scan --path . --compare-remote` |
| `/sf 当前目录有没有上传` | `sf skill status --path .` |
| `/sf doctor` | `sf skill doctor --path .` |
| `/sf doctor --remote` | `sf doctor --remote --path .` |
| `/sf test` | `sf skill test --local --path .` |
| `/sf test-real --shop-id <id>` | `sf skill test --real-mcp --path . --shop-id <id>` |
| `/sf sandbox` | `sf skill sandbox --path .` |
| `/sf preview output.json` | `sf preview output.json` |
| `/sf preview apply <preview_id>` | `sf preview apply <preview_id>`，真实落库需加 `--real --idempotency-key <key>` |
| `/sf output validate output.json` | `sf output validate output.json` |
| `/sf skill init <skill_id>` | `sf skill init <skill_id>` |
| `/sf skill diff --path .` | `sf skill diff --path .` |
| `/sf skill publish-status <skill_id>` | `sf skill publish-status <skill_id>` |
| `/sf schedule get <skill_id>` | `sf schedule get <skill_id>` |
| `/sf schedule set <skill_id> --cron '50 7 * * *'` | `sf schedule set <skill_id> --cron '50 7 * * *'` |
| `/sf schedule stop <skill_id>` | `sf schedule stop <skill_id>` |
| `/sf submit`、`/sf 上传`、`/sf 发布` | `sf skill submit --path .`；只有用户明确确认强制提交时才加 `--force-submit --force-reason "<原因>"` |
| `/sf review <submission_id>` | `sf submission status <submission_id>` |
| `/sf review list` | `sf review list` |
| `/sf market list` | `sf market list` |
| `/sf market install <skill_id>` | `sf market install <skill_id>` |
| `/sf pipeline run --path . --remote` | `sf pipeline run --path . --remote` |
| `/sf project spec --recipe short-video-analysis` | `sf project spec --recipe short-video-analysis --json`，返回 Codex 生成项目所需的 Project Host contract、Gateway SDK、运行资产、视频视觉兜底和短视频分析配方 |
| `/sf project org resolve --department "部门" --owner "姓名"` | `sf project org resolve --department "部门" --owner "姓名" --visibility company`，解析部门/owner 和当前账号项目提交权限，用于生成 `projectforge.yaml` |
| `/sf project init` | `sf project init --path .` 生成静态网页项目；已有 Codex/Vite 产物会自动识别 `dist/index.html`、`public/index.html`、根 `index.html`；外部 URL 用 `sf project init --path . --kind external_web --entry-url <url>`，都会生成 projectforge.yaml 与 Project Gateway SDK 示例 |
| `/sf project doctor` | `sf project doctor --path .`，校验入口、能力声明与输出 contract；短视频分析项目使用 `sf project doctor --path . --recipe short-video-analysis --json` 返回可由 Codex 修复的 `checks[]` |
| `/sf project submit`、`/sf project 上传` | `sf project submit --path .`；含 `entry_url` 走 `/api/codex/projects/auto-register`，本地静态包走 `/api/codex/projects/upload`，都用 CLI token，不能直写平台目录 |
| `/sf project verify <project_id>` | `sf project verify <project_id> --recipe short-video-analysis --json`，调用上传项目服务并读取脱敏 logs/trace，验证 Codex 生成项目是否完成输入、能力调用、输出和 AI 分析闭环 |
| `/sf project status <project_id>` | `sf project status <project_id>`，通过 `/api/codex/projects/{project_id}/status` 查看版本、运行、AI 状态和可点击 `app_url/trace_url` |
| `/sf project invoke <project_id>` | `sf project invoke <project_id> --input-json '{...}' --prompt '...'`，通过 `/api/codex/projects/{project_id}/service/invoke` 把上传项目作为平台 API 服务调用，input/output/能力调用/AI 循环都进入 Project Gateway Trace |
| `/sf project asset upload <project_run_id> <file>` | `sf project asset upload <project_run_id> <file> --asset-type visual_frame --role material_a --material-role material_a --frame-time 2 --frame-label opening`，通过 `/api/codex/projects/runs/{project_run_id}/assets` 上传运行资产并写入 Project Gateway Trace |
| `/sf project asset list <project_run_id>` | `sf project asset list <project_run_id>`，列出当前账号有权读取的 ProjectRunAsset，用于确认视频/关键帧是否进入 run |
| `/sf project logs <project_run_id>` | `sf project logs <project_run_id> [--limit N] [--offset N|--ingress-cursor C --capability-cursor C]`，通过 `/api/codex/projects/runs/{project_run_id}/logs` 分页/游标续页查看脱敏运行输入、输出、能力调用记录和对应平台 `trace_url` |

`/sf`、`/sf help`、`sf` 和 `sf help` 都只输出本地中文帮助，不访问平台，也不触发登录。

## 项目宿主（Codex 小网页上传平台）

“项目”不是每个功能一个 Docker 容器，而是类似虚拟主机的轻量应用宿主：Codex 开发出来的小网页/工具通过 `projectforge.yaml` 声明入口、部门可见性、能力和输出 contract；上线后在 SkillForge `/projects` 点击即用。网页自身不能读取平台 AI key、MCP env、Cookie，也不能直接调用业务系统；需要 AI/数据/MCP 时走 Project Gateway 或 sf SDK，平台记录 `project_runs`、`execution_runs`、`decision_log`、`project_ingress_events`、`project_capability_calls`，并把输入、reports/todos/proofs 纳入 Trace、收件、待办和 Learning Loop。详细规格见 `docs/spec/project-hosting-and-ai-loop.md`。

`/sf 拉取 <skill_id>` 会校验平台返回包的 `package_hash` 和 `skillforge.yaml.skill_id`，并拒绝覆盖已有的不同本地 Skill 目录；如需更新同一个本地 Skill，再显式使用 `--force`。

## Claude Code 怎么用

Claude Code 不一定加载 Codex 插件，但可以直接用本项目的 `sf` CLI。用户输入 `/sf ...` 时，Claude 应按上面的路由表执行对应 shell 命令。

项目内也提供 `.claude/commands/sf.md` 作为 Claude slash command 提示。即使 slash command 未加载，也按本文档执行。

## 真实 MCP 调用

查看可用工具：

```bash
sf mcp catalog
sf mcp catalog --json   # 需要机器读取时输出原始 JSON
```

`sf mcp catalog` 的默认摘要会额外标出“本地 CLI 已知但当前平台 catalog 未返回”的内置工具。看到这一段时，通常表示平台 catalog 元数据未刷新或当前服务与本地 `sf` CLI 版本不一致；对应 `sf agent coverage`、`sf ai analyze`、`sf raw query`、`sf run analyze` 仍会通过 `/api/codex/mcp/call` 发起调用，若后端未部署会返回明确错误。`sf mcp catalog --json` 保持输出平台原始 JSON，便于机器做严格比对。

直接调用读工具：

```bash
sf mcp call skillforge_org_search_users --args '{"query":"张三","limit":5}'
sf mcp call skillforge_org_list_members --args '{"department":"本部门","limit":20}'
sf agent coverage
sf mcp call skillforge_agent_coverage --args '{"department":"EC"}'
sf raw query --source execution_runs --skill-id <skill_id> --limit 20
sf raw query --source decision_logs --run-id <execution_run_id> --limit 20
sf ai analyze --prompt "分析这次 Skill 运行结果" --context-json '{"run_id":"<execution_run_id>"}'
sf run analyze --run-id <execution_run_id> --include-raw
sf run candidate --run-id <execution_run_id> --analysis-prompt-hash <hash>
sf training resources
sf training datasets
sf training readiness <skill_id>
sf training candidate <skill_id> --model-family <model_family>
sf training create --title "训练新模型" --target-skill-id <skill_id> --dataset-ref <dataset_ref>
sf training jobs --status running
sf training job <training_job_id>
sf training approve <training_job_id>
sf training dispatch <training_job_id>
sf training collect <training_job_id>
sf training collect-due --gateway <training_gateway_id>
sf training evaluate <training_job_id>
sf training deploy-request <training_job_id> --target-skill-id <skill_id> --model-family <model_family>
sf training deployments
sf training deployment-approve <deployment_id>
sf training deployment-reject <deployment_id> --reason "说明原因"
sf training deployment-activate <deployment_id>
sf training deployment-rollback <deployment_id> --reason "说明原因"
sf training download-artifact <training_job_id> <artifact_id> --output model.bin
sf media status <media_instance_id>
sf media bootstrap <media_instance_id> --dry-run
sf media bootstrap <media_instance_id> --accept-license
sf media cancel <media_instance_id>
sf mcp call skillforge_data_capability_list --args '{}'
sf mcp call skillforge_tmall_link_decline_data_latest --args '{}'
sf mcp call skillforge_yuyidata_customer_service_data_latest --args '{}'
sf mcp call skillforge_execution_artifact_latest --args '{"skill_id":"tmall-link-decline-collector-v1"}'
sf mcp call skillforge_execution_artifact_summary --args '{"run_id":"<run_id>"}'
sf mcp call skillforge_cloud_video_session --args '{"force_refresh":true}'
sf mcp call skillforge_cloud_video_accounts --args '{"max_people":20}'
sf mcp call skillforge_cloud_video_categories --args '{"max_items":20}'
sf mcp call skillforge_cloud_video_videos --args '{"search":"示例品牌","page_size":5}'
sf mcp call skillforge_cloud_video_ad_report --args '{"tab":"明细","start_date":"2026-05-01","end_date":"2026-06-09","limit":20}'
sf mcp call skillforge_cloud_video_daily_person_video_report --args '{"start_date":"2026-05-01","end_date":"2026-06-09","top_videos_per_person":5,"low_videos_per_person":5,"include_daily_rows":false}'
sf mcp call skillforge_cloud_video_material_report --args '{"start_date":"2026-05-11","end_date":"2026-06-09","search_type":1}'
sf mcp call skillforge_cloud_video_video_usage_report --args '{"start_date":"2026-05-11","end_date":"2026-06-09","data_type":3,"ids":["4016"]}'
sf mcp call skillforge_cloud_video_audit_rejects --args '{"video_id":"100715117","platform_type":2}'
```

sf 也提供了更短的数据能力命令：

```bash
sf data list
sf data latest tmall
sf data latest yuyi
sf data get tmall --include-content --max-bytes 5242880
```

`sf raw query` 底层走 `skillforge_raw_data_query` MCP 工具，可查 `execution_runs`、`execution_steps`、`decision_logs`、`collection_proofs`、`api_schema_snapshots`。普通账号必须提供 `skill_id`、`run_id` 或 `proof_id`，平台按 Skill 读取权限校验；返回的 input/output/metadata 是用于排障和复盘的原始记录，但 token、cookie、authorization、secret 等字段会统一脱敏。

`sf ai analyze` 底层走 `skillforge_ai_analyze` MCP 工具，使用服务端后台 `ai.*` 配置调用 `deepseek-v4-pro`（1M 上下文能力）。本地 CLI、Codex 和 Skill 包都拿不到模型 key。这个入口用于 Codex/sf 调试与一次性分析；生产 Skill 运行期的大模型分析仍使用 `sf.analyze(context_pack=...)` / `/api/intelligence/analyze`。

`sf agent coverage` 底层走 `skillforge_agent_coverage` MCP 工具，再由平台读取 `/api/aiclaw/departments/agent-coverage` 同一套服务逻辑。它只返回当前账号可见部门的执行/分析/训练 Agent 覆盖度、在线数和平台兜底摘要，不暴露跨部门实例控制权、gateway URL、密钥或脚本。

`sf run analyze` 底层走 `skillforge_run_analyze` MCP 工具，是 `raw query -> ai analyze` 的一键闭环：给定 `execution_run_id` 后，平台按权限读取本次 run、steps、decision_log、collection proofs 和 schema snapshots，按平台路径脱敏后调用已配置模型生成运行复盘。当前代码有 DeepSeek 默认模型与上下文预算，实际模型和预算以响应字段为准；不能把预算值当作上游服务已支持或已经实测的容量。需要排查为什么 Skill 输出异常、缺少 proof、审批/反馈不合理时优先用这个命令。

平台内置 `skillforge_internal` MCP 也暴露 `skillforge_agent_coverage`、`skillforge_ai_analyze`、`skillforge_raw_data_query`、`skillforge_run_analyze`，供 Skill 编辑/创建 Agent 先判断部门执行/分析/训练 Agent 缺口，再做受控分析。它使用 `SKILLFORGE_USER_ID` 映射真实 active 用户并复用同一套 Skill 读权限、脱敏和服务端 AI 配置；生产 Skill 运行期仍使用 SDK 的 `sf.analyze()`，不能把这些调试 MCP 当成运行期 LLM 入口。

同一能力也暴露给 Run Trace 页面和 HTTP API：`POST /api/admin/runs/{run_id}/trace/analyze` 或 `POST /api/executions/runs/{run_id}/trace/analyze`。页面按钮只调用平台后端，不在浏览器暴露模型 key；后端先复用 Run Trace 可见性校验，再按 Skill 读取权限收集脱敏上下文。

`sf run candidate` 调用 `POST /api/training/runs/{run_id}/candidate`，用于把某次运行复盘沉淀为训练候选任务。它不会读取或上传未脱敏原始数据，只保存 run lineage、raw_counts、prompt hash 和可选复盘摘要；平台要求当前用户可编辑目标 Skill 且有训练任务创建权限，生成的 job 仍是 `awaiting_review`，后续必须走训练审批、下发、评估和部署审批。

`sf training resources` / `sf training datasets` / `sf training readiness <skill_id>` / `sf training jobs` / `sf training job` 读取训练控制面：资源命令展示当前账号可见训练 Agent/GPU、平台兜底和运行中训练摘要；数据资产命令展示当前账号可见 Skill 的样本量、门禁缺口和候选能力，不返回 DecisionLog 原始 input/output；任务命令展示模型名、参数摘要、路由、进度、预计耗时、目标网关和部署线索。`sf training candidate <skill_id>` 会基于通过门禁的 Skill 历史数据资产创建 `awaiting_review` 候选，`sf training create` 也只创建待审批任务，二者都不会绕过审批；`sf training approve|dispatch|retry|collect|evaluate|cancel <job_id>` 和 `sf training collect-due` 只调用 SkillForge 后端训练状态机，权限、部门边界、训练 Agent 路由、回调 token、日志/产物脱敏仍由平台统一处理。评估通过后用 `sf training deploy-request` 发起模型部署审批，再用 `sf training deployments` / `sf training deployment-approve|deployment-reject|deployment-activate|deployment-rollback` 管理驳回、灰度、激活和回滚；驳回必须带人工原因，Web 端同样要求输入原因再提交；`sf training download-artifact` 只走平台训练产物代理，不读取训练节点文件系统。

`sf media status|bootstrap|cancel` 只面向系统管理员，通过 SkillForge 调用在线 Bridge 的受控媒体安装操作。安装配置固定为 `h3_all_modes_v1` 和 `3 MB/s`，正式安装必须显式传 `--accept-license`；不接受任意 URL、Shell 命令或工作流。详细清单、提示词策略和验收条件见 [MiniMax H3 媒体节点运维指南](minimax-h3-media-operations.md)。

云视频能力用于短视频分析项目的数据输入。`skillforge_cloud_video_daily_person_video_report` 会按天返回 `people[]`、`daily_person_rows[]`、`daily_video_rows[]` 和每个人的 `top_cost_videos` / `low_roi_videos`，默认查询 2026-05-01 到 2026-06-09 的巨量千川汇总。`skillforge_cloud_video_material_report` 对齐云视频广告平台分析页素材统计，可按 `search_type=1/2/3/4` 查询上传人、上传分组、上传团队或分类维度的素材数、消耗和标签项；`skillforge_cloud_video_video_usage_report` 对齐云视频视频统计报表，可按 `data_type=1/2/3` 查询个人/分组/团队上传条数、下载、推送、剪映和爆款统计；`skillforge_cloud_video_audit_rejects` 对齐卡审原因弹窗，可按 `video_id`、`platform_type=2` 查询巨量千川拒因。核对总上传条数时优先用 `video_usage_report`，核对当前素材明细/卡审视频列表时用 `videos` 的 `system_auto_label_type=1` 和 raw 字段，核对卡审原因时用 `audit_rejects`，核对投放消耗时用 `ad_report` / `daily_person_video_report`，不要把这些口径混算。Codex 生成类似 `short-video-analysis-mvp` 的项目时，前端声明并调用 `mcp://skillforge_cloud_video_daily_person_video_report`、`mcp://skillforge_cloud_video_video_usage_report`、`mcp://skillforge_cloud_video_audit_rejects` 和按需调用 `mcp://skillforge_cloud_video_material_report`，不要直连云视频站点；视频视觉诊断继续走 Project Gateway 的运行资产/关键帧/平台 AI 能力，再把投放数据里的 `video_id`、`video_name`、ROI、CTR、转化率、完播率、上传统计、素材统计、卡审拒因和视觉诊断合并，输出每个人的改进方案。云视频账号、密码、token、签名只在 SkillForge 服务端配置和调用链中使用，不返回浏览器或 Codex。

写工具默认必须 dry-run。真实写入必须显式加 `--real` 和幂等键：

```bash
sf mcp call skillforge_dingtalk_send_work_notice \
  --args '{"user_ids":["<user_id>"],"title":"标题","markdown":"正文"}'

sf mcp call skillforge_dingtalk_send_work_notice \
  --real \
  --idempotency-key "<stable-key>" \
  --args '{"user_ids":["<user_id>"],"title":"标题","markdown":"正文"}'
```

不要在本地脚本里保存或打印钉钉 token、业务 Cookie、MCP env、API key。

平台会对执行完成的 `raw-input` / `raw-output` 原始 JSON 做 gzip Artifact 归档。sf 默认只能通过
`skillforge_execution_artifact_latest` / `skillforge_execution_artifact_summary` 查看摘要、大小、sha256、
schema 和 run 引用，不默认拉完整原文。面向业务复用的数据已拆成受控数据能力：
`skillforge_data_capability_list`、`skillforge_data_capability_latest` 和 `skillforge_data_artifact_get`。
Tmall 链接下滑缓存数据可用 `skillforge_tmall_link_decline_data_latest/get` 获取，语艺客服会话缓存数据可用
`skillforge_yuyidata_customer_service_data_latest/get` 获取。`get` 默认仍只返回摘要，只有显式
`include_content=true` 且未超过 `max_bytes` 才返回解压后的 JSON。

## 编写和提交 Skill 的标准流程

1. 更新插件并登录：

```bash
sf update --check
sf auth status || sf auth login
sf mcp catalog
```

2. 创建或检查 Skill 包。新建时可以用：

```bash
sf skill init <skill_id> --name "<名称>" --department "<部门>"
```

常见文件：

```text
SKILL.md
skillforge.yaml
contract.json
scripts/main.py
fixtures/sample_input.json
tests/test_main.py
```

3. Skill 运行时调用 MCP 必须走 SkillForge SDK：

```python
from skillforge_sdk import SkillForge

sf = SkillForge("your-skill-id")
data = sf.fetch_api(
    "mcp://yuyidata_create_sessions_retrieve_task",
    body={"dateType": "yesterday", "limit": 200},
    dry_run=False,
)
```

4. 需要大模型分析时用 `sf.analyze(context_pack=...)`，不要在 Skill 里直连模型或携带模型 key。平台会校验 run token 和 commit，按“当前执行 Agent -> 同部门分析 Agent -> 平台分析 Agent -> 平台 LLM”的顺序路由，并把命中的分析路由写入响应和 `intelligence_analyze_runs` 追踪表。

5. Skill 输出 `todos` 和 `reports`，由平台处理待办、报告、钉钉 outbox、审计和 data proof。脚本不要直接发钉钉。

6. 先本地检查，再真实网关验证：

```bash
sf skill doctor --path .
sf skill test --local --path .
sf skill test --real-mcp --path . --shop-id <shop_id>
```

没有店铺上下文的 Yuyi / 组织类工具，可以按具体 Skill 要求省略 `--shop-id` 或使用 MCP call 先验证能力。

6. 输出先预览、校验，再决定是否正式落库：

```bash
sf preview output.json
sf output validate output.json
sf preview apply <preview_id>
sf preview apply <preview_id> --real --idempotency-key "<stable-key>" --skill-id <skill_id>
```

`sf preview apply` 默认 dry-run，只返回会生成多少报告、待办、动作和通知。真实落库必须提供幂等键，平台会生成正式执行记录、DecisionLog、Artifact、报告和待办。

7. 查看运行结果：

```bash
sf runs list --skill-id <skill_id>
sf runs status <run_id>
sf runs result <run_id>
sf runs artifacts <run_id> --kind raw-output
sf runs logs <run_id>
sf runs diagnose <run_id>
```

8. 提交审核：

```bash
sf skill submit --path . --message "<变更说明>"
sf submission status <submission_id>
```

提交审核必须走 SkillForge API。当前 `sf skill submit` 会先执行本地 `doctor` 和离线 `scripts/main.py` 测试，并校验 stdout JSON 顶层字段满足 `contract.json.output_schema.required`；平台收到包后还会阻断缺失 `contract.json`、缺失/非法 `output_schema`、`output_table` / `SKILL.md` 输出定义与 `output_schema.required` 不一致的包。若审核 gate 未通过，只有用户在当前对话中明确确认后才允许 `sf skill submit --force-submit --force-reason "<被绕过 gate、风险和用户确认摘要>"`，平台会写 Codex 提交审计和通用 `review.force_submit` 审计，且仍只进入审核队列。不要直接写 `skills-repo/`，不要直接改审核状态或发布态。

## 查看和安装共享 Skill

sf 可以按平台统一权限列出可见 Skill、我的 Skill、部门共享 Skill，并下载安装到本地工作区：

```bash
sf skill list --scope department
sf skill install <skill_id> --path ./skills
sf market list
sf market install <skill_id> --path ./skills
sf market rate <skill_id> --rating 5 --comment "稳定好用"
```

`install` 只允许下载当前账号具备 `read` 权限的 Skill。下载包会写入 `skillforge.yaml` 的
`skill_id` 和 `base_commit`，后续可以用：

```bash
sf skill status --path ./skills/<skill_id>
sf skill submit --path ./skills/<skill_id> --message "修改说明"
```

安装到本地不等于发布；修改后的上传仍必须走 `sf skill submit`、平台 Git、审核和发布流程。

## 定时、审核与流水线

节点定时仍是主路径。sf 只能通过 SkillForge 更新配置并下发到 Bridge/OpenClaw/AIClaw，不把平台中心调度改成主路径：

```bash
sf schedule get <skill_id>
sf schedule set <skill_id> --cron '50 7 * * *'
sf schedule sync <skill_id>
sf schedule stop <skill_id>
```

审核记录可以直接从 Codex 侧查看和处理：

```bash
sf review list --skill-id <skill_id>
sf review get <review_id>
sf review comment <review_id> --content "修改建议"
sf review request-changes <review_id> --reason "缺少测试"
sf review approve <review_id>
```

完整流水线可以串联本地检查、远端状态检查和提交审核：

```bash
sf pipeline run --path . --remote
sf pipeline run --path . --remote --submit --message "<变更说明>"
```

## 示例：Yuyi 客服对话日报

用户可以直接对 Codex/Claude 说：

```text
帮我写一个 Skill：每天 09:30 调用 Yuyi 能力读取昨天客服对话，分析用户反馈主要问题、负向情绪和需要跟进的事项，输出报告和待办，并把摘要推送到我的钉钉。部门写本部门，先本地测试，再真实 MCP 验证，最后提交审核。
```

AI 应执行：

1. `sf update --check`、`sf auth status`、`sf mcp catalog`。
2. 确认有 `yuyidata_create_sessions_retrieve_task`、`yuyidata_get_task_info`、`yuyidata_download_task_file` 和钉钉 outbox 能力。
3. 生成 Skill 文件，把 `trigger_type=cron`、`trigger_expression="30 9 * * *"`、`department=本部门` 写入 frontmatter。
4. 在 `scripts/main.py` 用 SDK 调用 Yuyi MCP，分析昨天客服对话。
5. 输出 `reports` 和 `todos`。
6. 钉钉推送走平台 outbox 或 `skillforge_dingtalk_send_work_notice`，不写钉钉 token。
7. 运行 `sf skill doctor`、`sf skill test --local`、必要时 `sf skill test --real-mcp`。
8. 运行 `sf skill submit --path .` 提交审核。

## 常见问题

### `/sf` 不显示为 Codex 按钮

```bash
sf update --force
codex plugin add skillforge-codex@skillforge-local
```

然后重开 Codex 会话。新版 `sf update` 会自动刷新 Codex plugin cache；从旧版升级到新版时，可能需要手动跑一次 `codex plugin add`。

### 登录网页显示已登录，但 CLI 还在等

重新运行：

```bash
sf auth login
```

如果 CLI 打印授权 URL，确保浏览器最终打开的是该 `authorize` URL，而不是只停留在首页。不要手写 token。

### 有新版本

任意 `sf` 功能发现新版本时会提示：

```bash
sf update
```

更新会校验 sha256、替换本地插件、写 marketplace/config、安装 `~/.local/bin/sf`，并刷新 Codex 插件缓存。

`sf update --check` 和 `sf plugin status` 的输出里，`install_required=true` 表示本机插件缺失，需要安装；`update_available=true` 只表示平台版本高于本机版本。平台版本低于本机 CLI 时不要按“有新版本”处理，避免误降级。

开发或排查本机服务时，可以临时指定 manifest 来源：

```bash
sf update --check --base-url http://127.0.0.1:8000
sf plugin status --base-url http://127.0.0.1:8000
```

`--base-url` 和 `SKILLFORGE_BASE_URL` 默认只影响本次命令，不会改写 `~/.skillforge/codex-cli.json` 里持久化的平台地址，也不会用本地 manifest 覆盖缓存。只有明确加 `--persist-base-url` 时，才把该地址写成本机默认 SkillForge 平台。

### MCP 权限不足

先查账号和能力：

```bash
sf auth status
sf capabilities
sf mcp catalog
```

不要绕过权限直接调用业务接口。缺权限需要平台授权。

## 修改 sf 相关代码后的验证

涉及 `scripts/sf.py`、`app/codex/plugin_bundle.py`、`app/codex/router.py`、Codex 插件 manifest、MCP gateway、登录授权时，至少运行：

```bash
.venv/bin/python -m pytest tests/test_codex_gateway.py -q
git diff --check
```

如果改了前端页面，再运行：

```bash
cd web && npm run typecheck
```

生产发布后验证：

```bash
systemctl --user restart skillforge.service
curl -sS -H 'Accept: application/json' 'http://skillforge.example.com/api/codex/catalog/manifest?format=json'
sf update --check
sf update --force
sf plugin status
sf auth status
sf plugin doctor --fix
sf mcp catalog
```
