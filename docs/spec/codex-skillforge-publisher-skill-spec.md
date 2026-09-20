# Codex Skill：SkillForge Publisher 规格

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


> 日期：2026-05-14
> 原项目状态记录：implemented（不代表公开版验收）
> 类型：Codex skill 设计文档
> 依赖：`docs/spec/codex-local-mcp-gateway-platform-spec.md`

实现入口：

- Codex skill：`/home/skillforge/.codex/skills/skillforge-publisher/SKILL.md`
- `/sf` slash prompt：`/home/skillforge/.codex/prompts/sf.md`
- CLI：`scripts/sf.py` / `scripts/sf`
- 后端能力目录、权限、提交审核 API：`app/codex/router.py`

当前面向 Codex / Claude 的操作流程以 `docs/guides/sf-codex-claude-workflow.md` 为准；本规格保留设计背景和接口约束。

## 1. Skill 定位

`skillforge-publisher` 是给本地 Codex 使用的 Skill。它负责把本地开发好的 Skill 检查、调试、打包并提交到 SkillForge。

它不直接发布生产，不直接写 `skills-repo/`，不直接拿平台 MCP 密钥。所有权限、真实 MCP、API、审核、发布都走 SkillForge 后端。

用户可能在上海、温州或其他地点开发，而 SkillForge 服务器、浏览器登录态、Cookie、业务 API 白名单和真实 MCP 运行环境在平台侧。因此本 Skill 必须默认走平台 Gateway，不能假设开发者本机能直连业务系统或运行真实 MCP server。

## 2. 触发场景

当用户要求：

- 使用 `/sf ...` 入口，例如 `/sf 查看哪些技能`、`/sf 哪些未上传`、`/sf test-real`、`/sf submit`、`/sf project submit`。
- 检查当前目录 Skill 是否符合 SkillForge 规范。
- 本地调试 Skill。
- 本地调用真实平台 MCP/API。
- 查询自己是否可新增 / 可发布 / 可查看部门 MCP。
- 查询 SkillForge 支持哪些输出能力，例如待办、报告、性能指标、data proof。
- 把本地 Skill 提交到 SkillForge 审核。
- 把 Codex 开发的小网页/工具作为项目包提交到 SkillForge 项目宿主，并通过 Project Gateway 记录运行、输入、能力调用和输出。
- 查看提交状态和审核结果。

Codex 应使用本 Skill。

## 3. 推荐目录结构

```text
skillforge-publisher/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
│   └── sf_publish.py
└── references/
    └── api-contract.md
```

`SKILL.md` 只保留工作流和边界；确定性的登录、MCP proxy、打包、提交、轮询逻辑放在 `scripts/sf_publish.py` 或外部 `sf` CLI 中。

`agents/openai.yaml` 只放 UI metadata 和默认 prompt。`/sf` 原生命令由插件包里的 `commands/sf.md` 提供；不要在 `openai.yaml` 里发明私有 slash-command 字段。

安装到 Codex 本地时，建议同时放置 skill 和 slash prompt：

```text
$CODEX_HOME/
├── skills/
│   └── skillforge-publisher/
│       ├── SKILL.md
│       └── agents/
│           └── openai.yaml
└── prompts/
    └── sf.md
```

`prompts/sf.md` 是 `/sf` 的本地入口；内容只负责把 `/sf` 后面的中文或英文命令转交给 `$skillforge-publisher`，不保存 token、不访问平台接口。

## 4. SKILL.md 草案

```markdown
---
name: skillforge-publisher
description: Use when a user wants Codex to validate, locally debug, call authorized real SkillForge MCP/API tools, package, submit, list visible SkillForge Skills, find local Skills not uploaded, or check review status for a SkillForge Skill from a local workspace. Also use this skill for slash-style commands starting with /sf, such as /sf auth, /sf doctor, /sf 查看哪些技能, /sf 哪些未上传, /sf test-real, and /sf submit. This skill must use SkillForge auth, MCP gateway, and submission APIs; it must not access platform secrets directly or bypass SkillForge review.
---

# SkillForge Publisher

## Core Rule

SkillForge is the control plane. Codex may edit and validate the local Skill package, but all authorization, real MCP/API calls, Git ingestion, review, publish, and node deployment must go through SkillForge.

Do not read, request, persist, or print platform MCP env, Cookie, API key, DingTalk access_token, or business-system token.

## Workflow

1. Run `sf auth status`; if missing or expired, run `sf auth login`.
2. Run `sf catalog refresh` to update platform API/MCP/editor catalogs when the server has a newer catalog revision.
3. Run `sf capabilities`, `sf platform capabilities`, and `sf editor capabilities` to learn the user's allowed departments, Skill actions, MCP tools, platform APIs, SkillForge output capabilities, and SkillStudio editor contract.
4. Inspect the current Skill package. Required files: `SKILL.md`, `skillforge.yaml`, `contract.json`, `scripts/main.py`, `tests/`, and fixtures when tests need them.
5. Run offline validation first: `sf skill doctor --path .` and `sf skill test --local --path .`.
6. If the user asks for real data or field validation, run `sf skill test --real-mcp --path .` after confirming auth is valid.
7. Submit with `sf skill submit --path .`; this creates or updates a SkillForge submission and review request.
8. Report the submission id, Git commit/hash returned by SkillForge, review status, blockers, and next action.

## MCP

For local Codex MCP tools, use `sf mcp stdio`. Local tools are proxies; real execution happens in SkillForge.

For Skill runtime code, use `skillforge_sdk.SkillForge.fetch_api("mcp://tool_name", ...)`. In local real-MCP mode the SDK routes through SkillForge Gateway.

Never try to make the user's laptop call production MCP scripts directly. Different office locations and networks must behave the same because the platform is the only real MCP execution endpoint.

## Boundaries

- Never modify the main SkillForge repository for Skill content.
- Never add `skills-repo/` to the main project Git.
- Never directly write production publish state.
- Never directly push to node deployment directories.
- Never send DingTalk messages from `scripts/main.py`; return `todos` / `reports` and let SkillForge distribute.
```

## 5. 本地命令体验

首次登录：

```bash
sf auth login
```

按需登录：

```bash
/sf 查看哪些技能
/sf test-real --shop-id xxx
/sf submit
```

如果上述命令发现未登录、30 天 session 过期或 token 被吊销，`sf` CLI 应自动打开 SkillForge 授权网页，让用户钉钉扫码；扫码成功后继续执行原命令。无桌面或远程 SSH 环境下，CLI 打印登录 URL / QR，用户在任意浏览器完成扫码。

查询能力：

```bash
sf catalog refresh
sf capabilities
sf mcp catalog
sf mcp catalog --json
sf platform capabilities
sf editor capabilities
```

给 Codex 暴露平台 MCP：

```bash
sf mcp stdio
```

Codex MCP 配置：

```json
{
  "mcpServers": {
    "skillforge": {
      "command": "sf",
      "args": ["mcp", "stdio"],
      "env": {
        "SKILLFORGE_HOST": "https://skillforge.company.com"
      }
    }
  }
}
```

本地 Skill 检查：

```bash
sf skill doctor --path .
sf skill test --local --path .
```

本地真实 MCP 调试：

```bash
sf skill test --real-mcp --path . --shop-id xxx
```

提交审核：

```bash
sf skill submit --path . --message "feat: add daily item rank diagnosis"
sf submission status <submission_id>
```

### `/sf` slash-style 入口

推荐把用户入口定为 `/sf`。如果 Codex 客户端支持原生 slash command registry，则把 `/sf` 注册到 `skillforge-publisher`；如果当前客户端不支持原生注册，也必须把用户输入的 `/sf ...` 当作普通文本触发本 skill。两种模式的语义保持一致。

Codex 本地注册文件：

```markdown
---
description: SkillForge 中文入口。处理 /sf 命令，转交 $skillforge-publisher 执行权限、技能列表、未上传扫描、调试和提交。
---

Use $skillforge-publisher.

这是 SkillForge 的 `/sf` 中文入口。请把用户在 `/sf` 后面的文字当作 SkillForge 命令参数解析，并按 `$skillforge-publisher` 的命令表执行。
```

中文帮助表：

| 命令 | 干什么 | 实际动作 |
|---|---|---|
| `/sf` / `/sf help` | 显示中文帮助。 | 展示命令表，不访问平台。 |
| `/sf auth` | 查看登录状态，失效时扫码登录。 | `sf auth status`，必要时 `sf auth login`。 |
| `/sf whoami` | 查看当前账号、部门、权限摘要。 | `sf auth status`、`sf capabilities`。 |
| `/sf 权限` | 查看新增、发布、MCP/API 权限。 | `sf capabilities`、`sf platform capabilities`、`sf editor capabilities`。 |
| `/sf 查看哪些技能` | 列出当前用户有权查看的 Skills。 | `sf skill list --scope visible`。 |
| `/sf 拉取 <skill_id>` | 将当前用户有权读取的 Skill 拉取到本地工作区。 | `sf skill pull <skill_id> --path .`。 |
| `/sf 查看我能编辑哪些技能` | 列出当前用户可编辑的 Skills。 | `sf skill list --scope editable`。 |
| `/sf 查看我能发布哪些技能` | 列出当前用户可提交审核或发布的 Skills。 | `sf skill list --scope publishable`。 |
| `/sf 我负责哪些技能` | 列出自己创建、负责或参与维护的 Skills。 | `sf skill list --scope mine`。 |
| `/sf 哪些未上传` | 扫描本地 Skill，比较哪些还没提交平台。 | `sf skill scan --path . --compare-remote`。 |
| `/sf 当前目录有没有上传` | 查看当前目录 Skill 的本地/平台状态。 | `sf skill status --path .`。 |
| `/sf mcp` | 查看当前用户可用 MCP 工具摘要。 | `sf mcp catalog`；原始 JSON 用 `sf mcp catalog --json`。 |
| `/sf capabilities` | 刷新并查看平台能力目录。 | `sf catalog refresh` 后读取 capabilities。 |
| `/sf doctor` | 检查当前 Skill 包。 | `sf skill doctor --path .`。 |
| `/sf test` | 离线测试，不调用真实 MCP。 | `sf skill test --local --path .`。 |
| `/sf test-real --shop-id xxx` | 本地运行 Skill，真实 MCP/API 在 SkillForge 后端执行。 | `sf skill test --real-mcp --path . --shop-id xxx`。 |
| `/sf sandbox` | 平台沙箱运行，不影响生产。 | `sf skill sandbox --path .`。 |
| `/sf submit` | 打包并提交审核，不直接发布生产。 | `sf skill submit --path .`；强制提交必须由用户二次确认后显式加 `--force-submit --force-reason "<原因>"`。 |
| `/sf review <submission_id>` | 查看审核状态。 | `sf submission status <submission_id>`。 |
| `/sf project init` | 初始化轻量网页/外部 URL 项目包。 | `sf project init --path .`，自动识别 `web/dist/public/root index.html`；可加 `--kind external_web --entry-url <url>` 或 `--kind dashboard/internal_tool --entry <file>`，生成 `projectforge.yaml` 和 Project Gateway SDK 示例。 |
| `/sf project doctor` | 校验项目入口、能力声明和输出 contract。 | `sf project doctor --path .`。 |
| `/sf project submit` | 提交网页/外部 URL 到项目宿主。 | `sf project submit --path .`；含 `entry_url` 的项目走 `/api/codex/projects/auto-register`，本地静态包走 `/api/codex/projects/upload`，都使用 CLI token，不得绕过平台写静态目录或密钥。 |
| `/sf project status <project_id>` | 查看项目版本、最近运行和 AI 状态。 | `sf project status <project_id>`，读取 `/api/codex/projects/{project_id}/status`，响应必须包含可点击的 `app_url` 与 `trace_url`。 |
| `/sf project invoke <project_id>` | 把上传项目作为平台 API 服务调用。 | `sf project invoke <project_id> --input-json '{...}' --prompt '...'`，调用 `/api/codex/projects/{project_id}/service/invoke`；平台必须创建 ProjectRun、记录 input、通过 Project Gateway 调 AI/数据能力、ingest output，并返回 `project_run_id/app_url/trace_url`。 |
| `/sf project logs <project_run_id>` | 查看脱敏项目运行、输入、能力调用和输出。 | `sf project logs <project_run_id> [--limit N] [--offset N|--ingress-cursor C --capability-cursor C]`，读取 `/api/codex/projects/runs/{project_run_id}/logs`，响应必须包含分页脱敏日志、`pagination.next_*_cursor` 及该 run 的平台 `trace_url`。 |
| `/sf update` | 检查 manifest、校验 sha256、更新本地插件、写入 marketplace/config、安装 `sf` shim，并刷新 Codex 插件缓存。 | `sf update`；本机或灰度 manifest 预览用 `sf update --check --base-url <url>`，默认不持久化。 |

`sf` 无参数、`sf help`、`/sf` 和 `/sf help` 必须只输出本地中文帮助，不访问平台、不触发登录、不读取 token。

项目包命令的边界：SkillForge 不给每个小网页开 Docker 容器；`sf project submit` 只登记/上传轻量网页资产或外部 URL，并要求网页通过 Project Gateway 回传 `reports/todos/proofs`。AI key、MCP env、业务 Cookie 均留在平台侧，Codex 开发期能力不能直接带到浏览器生产代码；不含密钥的公开模型端点可作为 `Project Autowire` 接管候选继续上传，由平台在运行时代理到底层 AI Gateway。平台侧记录 `project_runs`、`execution_runs`、`decision_log` 和 `project_capability_calls`，再进入收件、待办与 Learning Loop。

常用示例：

```text
/sf help
  -> 展示本地可用 SkillForge 命令，不展示 token。

/sf auth
  -> sf auth status；未登录或失效时自动打开网页执行 sf auth login。

/sf 查看哪些技能
/sf skills
  -> sf skill list --scope visible
  -> 列出当前用户有权查看的 SkillForge Skills。

/sf 查看我能编辑哪些技能
/sf skills --scope editable
  -> sf skill list --scope editable
  -> 只列出当前用户可编辑的 Skills。

/sf 查看我能发布哪些技能
/sf skills --scope publishable
  -> sf skill list --scope publishable
  -> 只列出当前用户可提交审核或发布的 Skills。

/sf 哪些未上传
/sf local pending
  -> sf skill scan --path . --compare-remote
  -> 扫描当前工作区下的本地 Skill 包，并和平台状态比较。

/sf 当前目录有没有上传
/sf status
  -> sf skill status --path .
  -> 展示当前 Skill 的本地 hash、remote commit、review 状态和是否有未提交内容。

/sf doctor
  -> sf catalog refresh
  -> sf skill doctor --path .

/sf test
  -> sf skill test --local --path .

/sf test-real --shop-id xxx
  -> sf skill test --real-mcp --path . --shop-id xxx

/sf submit
  -> sf skill submit --path .

/sf capabilities
  -> sf capabilities
  -> sf mcp catalog
  -> sf platform capabilities
  -> sf editor capabilities
```

`/sf 查看哪些技能` 输出建议字段：

```text
skill_id | name | department | visibility | can_edit | can_submit | can_publish | remote_commit | latest_review | updated_at
```

`/sf 哪些未上传` 输出必须按状态分组：

```text
never_uploaded          平台没有同名 skill_id，本地包从未提交。
modified_not_submitted  平台已有该 Skill，但本地 package_hash 与 remote 不一致。
remote_newer            平台 commit 比本地 base_commit 新，需要先合并或重新导出。
synced                  本地 package_hash 与平台当前版本一致。
missing_metadata        本地目录像 Skill，但缺少 skillforge.yaml / SKILL.md / contract.json，无法判断。
no_permission           平台有该 Skill，但当前用户无权查看或提交。
```

`/sf 哪些未上传` 只上传文件摘要和 package hash 做比较，不上传完整包；只有 `/sf submit` 才上传 package。

### CLI 与接口对应关系

Codex skill 应优先调用 `sf` CLI；CLI 内部再调用平台接口。这样 Codex 不需要关心 token 存储和接口细节，也不会把 token 打印到对话里。

```text
sf auth login
  -> POST /api/codex/auth/login-intent
  -> GET /api/codex/auth/authorize
  -> POST /api/codex/auth/token

sf auth status
  -> POST /api/codex/auth/introspect

sf catalog refresh
  -> GET /api/codex/catalogs/manifest
  -> GET /api/codex/catalogs/bundle

sf capabilities
  -> GET /api/codex/capabilities

sf mcp catalog
  -> GET /api/codex/mcp-catalog

sf platform capabilities
  -> GET /api/codex/platform-capabilities

sf editor capabilities
  -> GET /api/codex/skill-editor-capabilities

sf skill list --scope visible|editable|publishable|mine
  -> GET /api/codex/skills?scope=<scope>

sf skill pull <skill_id> --path .
  -> GET /api/codex/skills/{skill_id}/package
  -> 写入本地 ./<skill_id>，并校验 package_hash、skillforge.yaml.skill_id
  -> 若目标目录已经是不同 Skill，即使带 --force 也拒绝覆盖

sf skill scan --path . --compare-remote
  -> POST /api/codex/skills/local-status

sf skill status --path .
  -> GET /api/codex/skills/{skill_id}/remote-status
  -> POST /api/codex/skills/local-status

sf skill test --real-mcp --path .
  -> POST /api/codex/skill/debug-runs
  -> POST /api/codex/mcp/call
  -> POST /api/codex/skill/debug-runs/{run_id}/complete

sf skill sandbox --path .
  -> POST /api/codex/skill/sandbox-runs

sf skill submit --path .
  -> GET /api/codex/skills/{skill_id}/remote-status
  -> POST /api/codex/skills/{skill_id}/conflict-check
  -> POST /api/codex/skill/submissions

sf submission status <submission_id>
  -> GET /api/codex/skill/submissions/{submission_id}
```

接口错误必须原样保留后端拒绝原因，但不要输出敏感字段。Codex 最终回复只报告权限缺口、调试失败点、submission id、run id、proof id 和下一步。

## 6. 本地真实 MCP 调试规则

本地真实 MCP 调试必须显式开启：

```bash
sf skill test --real-mcp
```

CLI 执行前必须调用：

```text
POST /api/codex/auth/introspect
```

如果 introspect 返回 `AUTH_REQUIRED` / `TOKEN_EXPIRED`，且当前是用户主动发起的交互式命令，CLI 自动打开网页登录并在登录完成后重试一次原命令。后台 MCP stdio 初始化、CI、非交互模式不得无提示弹窗。

执行时注入：

```text
SKILLFORGE_PLATFORM_URL
SKILLFORGE_RUN_TOKEN
SKILLFORGE_SKILL_ID
SKILLFORGE_RUN_MODE=local_debug
```

30 天 CLI session 必须保留，用于避免频繁扫码。它只保存在 `sf` CLI 登录态中，用来 introspect、刷新 catalog、创建 debug run 和提交包；不会注入 `scripts/main.py`。本地 Skill 子进程只拿短期 `SKILLFORGE_RUN_TOKEN`。短期 token 过期时，CLI 用 30 天 session 自动换新，不要求用户重新登录。

真实 MCP 调用必须经 SkillForge Gateway 执行。即使本机有同名 MCP 脚本，也不能在普通本地调试中绕过平台。这样上海、温州、外地办公和服务器内网环境的调试结果才一致。

Skill 代码不需要区分本地和平台：

```python
from skillforge_sdk import SkillForge

sf = SkillForge("ec-daily-check")

def main(payload: dict) -> dict:
    rank = payload.get("item_rank")
    if not rank:
        rank = sf.fetch_api(
            "mcp://tmall_sycm_item_rank_top",
            body={"limit": 20, "dateType": "today"},
            shop_id=payload.get("shop_id"),
        )
    return {
        "reports": [
            {
                "channel": "dingtalk_card",
                "title": "商品排行诊断",
                "summary": "已完成商品排行检查。",
                "recipients": {"roles": ["biz_owner"], "departments": ["传统电商"]},
                "payload": {"rank": rank},
            }
        ]
    }
```

### 调试模式与行为

Codex skill 必须区分三种调试，不要把真实数据调试和正式输出混在一起。

```text
offline local
  命令：sf skill test --local --path .
  行为：只用 fixtures / mocks，不调用真实 MCP/API。

local real MCP
  命令：sf skill test --real-mcp --path . --shop-id xxx
  行为：本机运行 scripts/main.py；真实 MCP/API 由 SkillForge Gateway 执行。
  输出：reports / todos / performance 只做 preview，不正式推送。

platform sandbox
  命令：sf skill sandbox --path . --dataset xxx
  行为：SkillForge 平台或节点运行完整 Skill 包。
  输出：生成 sandbox run、日志、proof、报告预览，不影响生产发布态。
```

`sf skill test --real-mcp` 标准流程：

```text
1. sf auth status
2. sf capabilities
3. sf platform capabilities
4. sf skill doctor --path .
5. POST /api/codex/skill/debug-runs 获取短期 run_token
6. 注入 SKILLFORGE_RUN_MODE=local_debug 和 SKILLFORGE_RUN_TOKEN
7. 本地执行 scripts/main.py
8. SDK 通过 /api/codex/mcp/call 调真实 MCP/API
9. POST /api/codex/skill/debug-runs/{run_id}/complete 上报脱敏结果
10. Codex 展示 run_id、proof ids、schema 校验和输出预览
```

调试完成后，Codex 的结论必须至少包含：

- 离线测试是否通过。
- 真实 MCP 调用是否通过，调用了哪些 tool。
- `reports`、`todos`、`performance` 是否符合平台能力目录。
- 是否产生 proof id。
- 是否存在权限缺口、字段缺口或发布前必须处理的问题。

## 7. SkillForge Skill 包规范

本地待提交 Skill 至少包含：

```text
SKILL.md
skillforge.yaml
contract.json
policy_pack.yaml
scripts/main.py
tests/
fixtures/sample_input.json
```

`SKILL.md` 必须说明：

- 解决什么问题。
- 触发条件。
- 输入数据与来源。
- 判断逻辑。
- 输出动作。
- 反例。
- 参数。
- 关联 Skill。

`skillforge.yaml` 建议包含：

```yaml
skill_id: ec-daily-check
display_name: 每日商品排行诊断
department: 传统电商
owner: zhangsan
trigger_type: manual
trigger_expression: ""
risk_level: R2
approval_level: 1
mcp_scopes:
  - sycm.item_rank
data_contracts:
  - sycm_item_rank_v1
entrypoint: scripts/main.py
```

`skillforge.yaml` 字段权威：

- 本地可声明运行意图和 scope：`skill_id`、`display_name`、`department`、`trigger_type`、`trigger_expression`、`risk_level`、`approval_level`、`mcp_scopes`、`data_contracts`、`entrypoint`。
- 平台入库后会规范化该文件，并覆盖或补齐 `owner`、发布版本、审核状态、节点部署状态等平台字段。
- Codex 不应把 `skillforge.yaml` 当成发布态真源；发布态以平台审核通过的 release 配置为准。

`contract.json` 建议声明：

- 输入 schema。
- 输出 schema。
- 依赖 MCP / API / DataContract。
- sample input。
- data proof 要求。
- 写操作是否只允许 dry_run。

## 8. `scripts/main.py` 硬规则

1. 暴露 `main(payload: dict) -> dict`。
2. stdin JSON 可运行，stdout 输出 JSON。
3. payload 有真实数据时优先分析 payload，保证 fixture 离线测试可跑。
4. payload 缺数据且需要真实数据时，通过 `SkillForge SDK` 调 MCP/API。
5. 不直接 `requests` 访问业务系统。
6. 不直接 import 或启动平台 MCP server。
7. 不直接发钉钉、邮件或 webhook。
8. 输出使用 `reports` / `todos` / 业务字段。
9. 失败不能静默返回空对象；要抛明确错误或返回 `data_errors`。

## 9. 自动切换规则：Codex 调试到平台正式运行

本地调试和正式运行必须使用同一份 Skill 代码。不要在 Skill 里写 `if codex then ... else production ...`。

唯一稳定入口：

```python
sf.fetch_api("mcp://tool_name", body={...})
```

切换由环境变量和 token 决定：

```text
local_debug
  -> sf skill test --real-mcp 注入 local_debug run token
  -> SDK 走 SkillForge Gateway

sandbox
  -> SkillForge 沙箱运行注入 sandbox run token
  -> SDK 走 SkillForge Gateway

production
  -> Bridge/OpenClaw/AIClaw 节点注入 production run token
  -> SDK 走 SkillForge Gateway
```

Codex 提交后，Codex 不参与生产运行；本地 CLI token 也不参与生产运行。平台按审核通过的 commit/tag、manifest scope 和节点 run 签发 production run token。

提交前必须检查：

- `scripts/main.py` 没有直接 import MCP server。
- `scripts/main.py` 没有直接启动平台 MCP 脚本。
- `scripts/main.py` 没有直接 requests 业务系统。
- 所有 `mcp://tool_name` 都在 `skillforge.yaml` 或 `contract.json` 声明 scope。
- 输出包含平台可消费的 `reports` / `todos` / metrics / data proof 信息。

## 10. SkillForge 平台能力认知

Codex skill 不能只知道 MCP/API，还必须先知道 SkillForge 能消费什么输出能力。

进入生成、调试或提交前运行：

```bash
sf platform capabilities
```

或调用：

```text
GET /api/codex/platform-capabilities
```

Codex 应把返回的能力当作当前真源，至少关注：

- `todos`：形成平台待办中心任务。
- `reports`：形成 `/inbox` 报告卡片。
- `performance_metrics`：形成报告指标、主指标、业务效果和后续性能报告材料。
- `data_proofs`：记录 MCP/API 数据来源和真实调用证明。
- `sandbox` / `production_gateway_mcp`：决定是否可做真实联调和正式运行。

### 待办输出

适合有人要审批、派发、执行的场景。

```python
return {
    "整改建议清单": items,
    "todos": [
        {
            "kind": "dispatch",
            "title": "TM001 流量下滑处理",
            "summary": "UV 下滑 18%，建议检查主图点击率和搜索词排名。",
            "payload": {
                "object_id": "TM001",
                "priority": "high",
                "data_overview": {"uv_delta": "-18%"},
                "analysis_basis": ["生意参谋商品排行", "商品 360"],
                "operation_actions": ["检查主图点击率", "排查搜索词排名"],
                "recommended_decision": "派发运营复核",
            },
            "reviewer_role": "biz_owner",
            "sla_hours": 24,
            "tasks": [
                {
                    "content": "检查 TM001 主图点击率并反馈",
                    "deadline": "2026-05-15T18:00:00+08:00",
                }
            ],
        }
    ],
}
```

规则：

- `review` / `dispatch` 都先进入平台待办中心。
- `dispatch` 子任务审批通过后才推给执行人。
- 不在 Skill 里直接发钉钉。
- `payload` 写运营可读决策卡片，不塞完整原始 API JSON。

### 报告与性能指标输出

适合日报、周报、性能报告、效果复盘。

```python
return {
    "日报正文": report_md,
    "reports": [
        {
            "channel": "dingtalk_card",
            "title": "每日商品排行诊断",
            "summary": "Top20 中 3 个商品出现明显流量下滑。",
            "content_markdown": report_md,
            "recipients": {
                "roles": ["biz_owner", "aibp"],
                "departments": ["传统电商"],
            },
            "payload": {"affected_items": 3},
            "metrics": [
                {"label": "异常商品", "value": "3", "trend": "up", "delta": "+2"},
                {"label": "最大 UV 下滑", "value": "18%", "trend": "down"},
            ],
            "primary_indicator": {
                "label": "异常商品",
                "value": "3",
                "severity": "high",
                "tone": "warning",
            },
            "tags": ["商品", "流量", "日报"],
        }
    ],
    "performance": {
        "business_metrics": [
            {"label": "覆盖商品数", "value": 20},
            {"label": "生成建议数", "value": 3},
        ],
        "quality_metrics": [
            {"label": "数据源成功率", "value": "100%"},
        ],
    },
}
```

规则：

- `reports` 成功执行后沉淀为 `/inbox` 报告卡片。
- `metrics` 和 `primary_indicator` 是性能报告和看板的优先信号。
- `performance` 用于保留业务效果、质量、耗时等指标，即使当前 UI 未完全消费也应输出。

### Data Proof

真实 MCP/API 调用由 SDK / Gateway 自动生成 proof。Codex skill 应避免手写伪 proof，但要在 `contract.json` 中声明需要哪些数据来源和 scope。

## 11. SkillStudio 编辑侧能力认知

Codex skill 需要知道 SkillStudio 编辑页面的对象模型，否则本地生成的 Skill 容易和平台编辑、校验、审核脱节。

进入生成、调试或提交前运行：

```bash
sf editor capabilities
```

或调用：

```text
GET /api/codex/skill-editor-capabilities
```

至少需要知道以下内容。

### 结构化模块

SkillStudio 当前按这些模块编辑：

| 模块 key | 含义 | Codex 生成要求 |
|---|---|---|
| `meta` | 基础信息 | `name`、`department`、`trigger_type`、`risk_level`、`approval_level` 必须能映射到 `SKILL.md` frontmatter / `skillforge.yaml`。 |
| `goal` | 目标 | 一句话说明业务决策、触发场景和闭环动作。 |
| `rules` | 规则 | 使用步骤、分支、结论、动作、`next_step`；每步要有兜底分支。 |
| `params` | 参数 | 同步到 `policy_pack.yaml`，不要只写在代码常量里。 |
| `output_table` | 输出字段 | 字段名要和 `contract.json.output_schema.required`、`main.py return` 对齐。 |
| `todos` | 待办模板 | 对应运行时 `output.todos`，平台先入待办中心，不直推钉钉。 |
| `test_cases` | 测试用例 | 覆盖正常、边界、缺失、反例，不用 `normal/happy_path` 这类泛名。 |
| `workflow` | 工作流预览 | 只描述节点、连线、字段绑定；复杂编排仍由平台管理。 |

不要发明新的模块 key。新增平台模块必须来自 `sf editor capabilities`。

### 文件契约

Codex 本地 Skill 包至少要和 SkillStudio 能互相理解：

```text
SKILL.md                 # 结构化说明真源，平台可解析为 meta/goal/rules/output/test_cases
contract.json            # 输入输出 schema、mcp/api scope、output_schema
skillforge.yaml          # 平台运行元数据；本地可声明，平台提交后可规范化
policy_pack.yaml         # 参数默认值，来自 params
scripts/main.py          # runtime entrypoint，stdin JSON -> stdout JSON
tests/test_main.py       # pytest 回归
fixtures/sample_input.json
intent.md                # 推荐，记录需求理解和权限
policy.yaml              # 推荐，记录 trigger/output/permissions/risk/failure_policy
```

创建路径里 `custom_sections.__artifacts` 会被 SkillStudio 保存成文件，例如 `intent.md`、`policy.yaml`、`task-contract.json`。Codex 生成时如果写这些文件，需要确保内容和 `SKILL.md`、`contract.json` 不冲突。

### 编辑 API 对齐

Codex skill 不直接写平台 `skills-repo/`，但要知道编辑页背后的 API 和语义：

```text
GET    /api/skills/{skill_id}
POST   /api/skills/{skill_id}/lock
DELETE /api/skills/{skill_id}/lock
PUT    /api/skills/{skill_id}/structured
GET    /api/skills/{skill_id}/files/{file_path}
PUT    /api/skills/{skill_id}/files/{file_path}
POST   /api/skills/{skill_id}/validate-all
POST   /api/skills/{skill_id}/validate-block
POST   /api/skills/{skill_id}/publish-readiness
GET    /api/skills/{skill_id}/history
GET    /api/skills/{skill_id}/diff
POST   /api/reviews/
```

本地发布路径仍走 `/api/codex/skill/submissions`。上面的 API 是 Codex 需要理解的兼容语义：锁、结构化模块、文件树、历史、diff、验证、审核门禁。

### 提交前门禁

Codex skill 提交前必须按 SkillStudio 同等口径检查：

- 当前包已经形成 Git commit / package hash。
- 离线测试或 sandbox / local_debug 真实调试至少跑过一次。
- `contract.json` 有合法 JSON Schema Draft-07 `output_schema`。
- `scripts/main.py` 有真实数据采集路径：payload 优先，缺数据时用 SkillForge SDK / `mcp://` Gateway。
- 不读取 fixture 当线上数据。
- 不直连业务系统、Cookie、LLM SDK、钉钉或邮件。
- 跨 Skill 冲突、静态检测、TaskContract gate 结果要原样报告。
- `output_table`、`contract.output_schema.required`、`main.py return` 顶层 key 必须一致。

当前实现口径：`sf skill submit` 上传前会自动跑本地 `doctor` 和离线测试，并校验 `main.py` stdout JSON 顶层字段满足 `contract.json.output_schema.required`；服务端 `/api/codex/skills/submissions` 会再次校验 `contract.json.output_schema` 为合法 Draft-07，并校验 `output_table` / `SKILL.md ## 输出定义` 与 `output_schema.required` 的非平台字段一致。更完整的真实数据证明仍由 `local_debug` 或 sandbox proof 产生，不通过直接提交绕过。

如果平台返回 `can_submit_review=false`，Codex 不应默认强制提交。只有用户明确要求强制提交时，才传 `force_submit=true`，并把 gate 缺口写入 `force_reason`。

强制提交必须满足：

- 用户在当前对话中明确二次确认，例如输入“确认强制提交”。
- 请求里带 `force_reason`，包含被绕过的 gate、风险说明和用户确认文本摘要。
- 平台写审计事件 `codex.skill.force_submit`，记录 user、skill_id、package_hash、base_commit、被绕过 gate、request_id / submission_id、review_id；同时写通用 `review.force_submit`，记录 review、force_reason 和 gate。
- 强制提交只允许进入审核队列，不允许直接发布生产。

### 和平台编辑并发

如果本地目录来自已存在 Skill，提交前必须拿平台版本做冲突判断：

```bash
sf skill status --remote <skill_id>
sf skill diff --remote <skill_id> --path .
```

Codex 需要关注：

- `git_commit_full`
- `git_head_commit`
- `git_commit_synced`
- 最近一次 review 状态
- 是否有编辑锁持有人

平台已有更新时，不要覆盖 SkillStudio 用户刚保存的内容；应提示先 rebase / merge 本地包，或重新从平台导出后再改。

## 12. 自动更新与新接口发现

可以做自动更新，但要分清“更新接口认知”和“更新可执行逻辑”。

Codex skill 的 `SKILL.md` 应只写稳定工作流，不能硬编码完整接口清单。新的 MCP、平台输出能力、编辑模块、提交门禁、废弃字段都从平台 catalog 动态获取：

```bash
sf catalog refresh
sf mcp catalog
sf platform capabilities
sf editor capabilities
```

`sf catalog refresh` 调：

```text
GET /api/codex/catalogs/manifest
GET /api/codex/catalogs/bundle
```

自动更新规则：

- 每次真实 MCP 调试、sandbox、submit 前自动检查 catalog `etag/catalog_rev`。
- 只要 catalog 有新版本，CLI 自动刷新本地缓存，Codex skill 下一步就能看到新接口。
- `sf update --check` / `sf plugin status` 必须区分 `install_required` 和 `update_available`：未安装插件但平台版本低于本地 CLI 时，只提示需要安装，不得误报“有新版本”或诱导降级。
- `sf update --check --base-url <url>` / `sf plugin status --base-url <url>` 用于本机或灰度 manifest 预览，默认只影响本次命令，不得改写 `~/.skillforge/codex-cli.json` 的持久化 `base_url`，也不得用临时 manifest 覆盖缓存；只有显式 `--persist-base-url` 才允许持久化该地址。
- 自动刷新不改用户正在开发的 Skill 包，不改 `scripts/main.py`，不改 `contract.json`。
- 如果新接口需要新增 scope，Codex 只能建议修改 `skillforge.yaml` / `contract.json`，然后走 doctor、调试、审核。
- 如果平台返回 `min_cli_version` 高于本地版本，停止提交并提示升级 CLI。
- 如果接口被废弃，doctor / submit 必须显示 replacement 和 remove_after。
- 如果有 breaking change，提交前必须阻断或要求用户确认迁移方案。

安全边界：

- 平台下发的 catalog / reference bundle 只能是 JSON / Markdown 数据。
- bundle 必须校验 `sha256` 和签名；签名算法为 Ed25519 + JCS canonical JSON + SHA-256。
- CLI 必须只信任内置或企业安装时固定的 SkillForge catalog 公钥；未知 `key_id`、签名失败、hash 不一致都必须停止使用该 bundle。
- 不允许通过自动更新下发或执行远程脚本。
- 不允许自动绕过用户权限；catalog 也必须按当前用户权限裁剪。
- 如果平台返回 `publisher_skill.min_version` 高于当前 `skillforge-publisher` 版本，允许继续 `sf auth status` / `sf catalog refresh`，但阻断提交并提示更新 Codex skill。

推荐本地缓存：

```text
$CODEX_HOME/skillforge/cache/catalog-manifest.json
$CODEX_HOME/skillforge/cache/mcp-catalog.json
$CODEX_HOME/skillforge/cache/platform-capabilities.json
$CODEX_HOME/skillforge/cache/editor-capabilities.json
```

这样“新的接口”不需要改 Codex skill 的提示词；平台发布新 catalog 后，Codex 通过 `sf` CLI 自动知道。

## 13. 提交流程

```text
本地目录
  -> sf skill doctor
  -> sf skill test --local
  -> 可选 sf skill test --real-mcp
  -> 读取 remote-status / base_commit
  -> 打包并计算 normalized package_hash
  -> POST /api/codex/skill/submissions
  -> SkillForge 写入 skills-repo/<skill_id> 并 commit
  -> SkillForge 创建审核单
  -> 审核通过后 SkillForge tag / 发布 / 下发节点
```

提交请求使用 `multipart/form-data`：

```text
package=@skillforge-package.tar.gz
manifest_json=<json string>
package_hash=sha256:<normalized package hash>
base_commit=<remote git_commit_full or empty for new skill>
message=<commit/review message>
```

打包规则：

- `package_hash` 按规范化文件树计算，不按压缩包字节计算：相对路径排序后拼接 `path\0bytes\0sha256(content)\n` 再 SHA-256。
- 允许提交：`SKILL.md`、`contract.json`、`skillforge.yaml`、`policy_pack.yaml`、`policy.yaml`、`intent.md`、`source_contract.yaml`、`metric_registry.yaml`、`scripts/**`、`tests/**`、`fixtures/**`、`references/**`、`assets/**`。
- 禁止提交：绝对路径、`..`、symlink、hardlink、`.git/**`、`.env*`、`node_modules/**`、`.venv/**`、`__pycache__/**`、`skills-repo/**`、本地 cache、任何 token/secret/cookie。
- 默认包上限 20 MiB，单文件上限 5 MiB。
- 已存在 Skill 必须带 `base_commit`；平台写入前在服务端锁内再次比较 HEAD，不一致返回 `PACKAGE_CONFLICT`。
- 新 Skill 的 `base_commit` 必须为空。

本地扫描规则：

- `/sf 哪些未上传` / `sf skill scan --path . --compare-remote` 默认最大递归深度为 4。
- 默认跳过 `.git/`、`.venv/`、`node_modules/`、`__pycache__/`、`skills-repo/`、`$CODEX_HOME/`、缓存目录和隐藏大型目录。
- 不跟随 symlink，不解析 hardlink。
- 只把包含 `SKILL.md`、`skillforge.yaml`、`contract.json` 中至少两个文件的目录识别为候选 Skill；缺字段时标记为 `missing_metadata`。
- 发送给平台的是 package hash、manifest hash、file_count、client_ref，不发送绝对本地路径和源码内容。

`manifest_json` 建议：

```json
{
  "manifest": {
    "skill_id": "ec-daily-check",
    "name": "每日商品排行诊断",
    "department": "传统电商",
    "owner": "zhangsan",
    "risk_level": "R2",
    "entrypoint": "scripts/main.py",
    "data_contracts": ["sycm_item_rank_v1"],
    "mcp_scopes": ["sycm.item_rank"],
    "package_format": "tar.gz",
    "file_count": 8,
    "files": [
      {"path": "SKILL.md", "sha256": "sha256:...", "bytes": 1234},
      {"path": "contract.json", "sha256": "sha256:...", "bytes": 2048}
    ]
  },
  "package_hash": "sha256:...",
  "base_commit": "abc...",
  "source": "codex_cli",
  "message": "feat: add daily item rank diagnosis"
}
```

## 14. 错误处理

Codex skill 只展示后端返回的脱敏错误，不自行猜测原因。常见错误码处理：

| code | Codex 行为 |
|---|---|
| `AUTH_REQUIRED` / `TOKEN_EXPIRED` | 交互式命令自动打开网页登录；无浏览器时打印 URL / QR；非交互模式提示运行 `sf auth login --no-browser`。 |
| `TOKEN_REVOKED` / `USER_DISABLED` / `PERMISSION_REV_CHANGED` | 停止真实 MCP 和提交，提示联系管理员或重新登录。 |
| `MCP_SCOPE_DENIED` | 说明缺少哪个 MCP scope，不建议用户改代码绕过。 |
| `SHOP_SCOPE_DENIED` | 说明缺少哪个 shop_id / 店铺授权。 |
| `DEBUG_RUN_EXPIRED` | 允许 CLI 用 30 天 session 自动创建新 debug run。 |
| `PACKAGE_CONFLICT` | 停止提交，提示先拉取/合并平台最新版本。 |
| `PACKAGE_INVALID` / `OUTPUT_SCHEMA_INVALID` | 展示校验项并要求修复后重新 doctor/test。 |
| `CATALOG_VERSION_UNSUPPORTED` | 提示升级 CLI 或 `skillforge-publisher` skill。 |
| `CATALOG_SIGNATURE_INVALID` | 停止使用本地 catalog 缓存，提示联系平台管理员。 |

最终回复只报告权限缺口、调试失败点、submission id、run id、proof id 和下一步，不输出 token、Cookie、Authorization header、原始业务系统敏感响应。

## 15. 权限行为

Codex skill 只能相信 SkillForge 返回的权限结果。

本地如果遇到无权限：

- 不建议用户改 role。
- 不尝试绕过。
- 提示需要申请部门、MCP tool、shop_id 或 publish 权限。
- 如果是提交审核权限不足，输出后端拒绝原因。

## 16. 验收标准

1. 未登录时，Codex skill 会引导 `sf auth login`。
2. 登录后，Codex 能看到平台返回的本用户能力。
3. `sf mcp stdio` 能作为 Codex MCP server 列出授权工具。
4. `sf skill test --local` 不调用真实 MCP。
5. `sf skill test --real-mcp` 能通过 SkillForge Gateway 调真实 MCP。
6. 本地响应不包含平台密钥、Cookie、Authorization header。
7. `sf skill submit` 返回 submission id 和审核状态。
8. 用户禁用或权限变化后，本地旧 token 无法继续调用 MCP 或提交。
9. Codex skill 会读取平台能力目录，并能按当前平台能力生成 `todos`、`reports`、`metrics`、`performance`。
10. Codex 本地调试后的 Skill 提交到平台后，不改代码即可在 sandbox / production 中通过 SDK 自动走 SkillForge MCP Gateway。
11. Codex skill 能通过 `sf skill test --real-mcp` 拿到 `run_id`、proof ids、输出预览和 schema 校验结果。
12. local_debug / sandbox 只做预览和 artifact，不产生正式待办、正式消息或正式 `/inbox` 报告。
13. Codex skill 能通过 `sf editor capabilities` 知道 SkillStudio 的模块、文件契约、编辑 API、锁和发布门禁。
14. 对已存在 Skill 提交前，Codex skill 会检查远端 commit / 编辑锁 / review 状态，避免覆盖平台编辑页的最新保存。
15. 平台发布新 MCP/API/编辑能力后，Codex skill 通过 `sf catalog refresh` 自动刷新 catalog，不需要改本地提示词。
16. 自动更新只更新能力 catalog / reference 缓存，不下载或执行远程代码。
17. 30 天 CLI session 有效期间，真实调试自动换短期 run token，不要求用户反复扫码。
18. 本地 Skill 子进程看不到 30 天 CLI token，只能看到 `SKILLFORGE_RUN_TOKEN`。
19. `sf skill submit` 使用 package 上传协议，并带 `base_commit` 做服务端原子冲突检测。
20. package 路径白名单、hash 校验、secret scan、output schema 任一失败时，Codex skill 阻断提交并展示后端错误码。
21. `/sf 查看哪些技能` 能列出当前用户可见、可编辑、可提交或可发布的 SkillForge Skills。
22. `/sf 哪些未上传` 能扫描本地目录并按 `never_uploaded`、`modified_not_submitted`、`remote_newer`、`synced`、`missing_metadata`、`no_permission` 分组展示，不上传完整包。
23. 用户主动执行 `/sf auth`、`/sf 查看哪些技能`、`/sf test-real`、`/sf submit` 时，如果未登录或 session 失效，会自动打开 SkillForge 网页扫码登录，登录成功后继续原命令。
24. `sf mcp stdio` 后台启动、CI、非交互模式不会无提示弹浏览器，只返回 `AUTH_REQUIRED` 和登录指引。
25. `/sf 哪些未上传` 不跟随 symlink，不扫描 `.git`、`.venv`、`node_modules`、`skills-repo`、`$CODEX_HOME`，且不把绝对本地路径发给平台。
26. `force_submit=true` 必须经过用户二次确认、写入 `force_reason` 和平台审计事件，且只能进入审核队列。
