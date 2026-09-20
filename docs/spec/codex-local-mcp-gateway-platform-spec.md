# Codex 本地真实 MCP 调用平台改造规格

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


> 日期：2026-05-14
> 原项目状态记录：implemented（不代表公开版验收）
> 范围：SkillForge 后端、SkillForge SDK、CLI / 本地 MCP proxy、权限与审计
> 关联约束：`docs/spec/skill-permission-unification.md`

实现入口：

- 后端 API：`app/codex/router.py`
- 认证、MCP Gateway、打包提交服务：`app/codex/service.py`
- 数据表与迁移：`app/codex/models.py`、`migrations/versions/095_create_codex_gateway_tables.py`
- 本地 CLI / MCP stdio proxy：`scripts/sf.py`
- Skill 运行期 SDK 自动切换 Gateway：`app/skill_runtime_sdk/skillforge_sdk.py`

## 1. 核心结论

本地 Codex / 本地 Skill 调试看见的是 MCP，真实执行发生在 SkillForge 后端。

```text
Codex / 本地 Skill
  -> sf mcp stdio / SkillForge SDK
  -> SkillForge MCP Gateway API
  -> 平台侧真实 MCP server
  -> 业务系统 / Cookie / API
```

本地允许调用真实 MCP 和真实数据，但不下发 MCP env、Cookie、API key、店铺密钥或业务系统 token。

用户可能在上海、温州或其他办公地点开发，SkillForge / 浏览器 Cookie / 业务系统白名单 / 内网 MCP 运行环境可能在另一台服务器或机房。因此真实 MCP/API 调用必须走平台，不允许依赖开发者本机网络、Cookie 或本地 MCP 脚本直连业务系统。平台是唯一真实数据出口和权限审计入口。

## 2. 目标

1. 用户扫码授权后，本地 30 天内可用 Codex 调平台 MCP。
2. 每次本地启动都检查用户是否 active、是否离职、是否禁用、权限版本是否变化。
3. 每次 MCP call 都由 SkillForge 后端实时按用户、部门、Skill、shop_id、scope 判权。
4. 本地调试 Skill 时可调用真实 MCP/API，便于验证真实字段、真实口径和真实错误。
5. 所有真实调用留下审计、data proof 和 local_debug 标记。
6. 不要求开发者本机具备业务系统网络、浏览器登录态、Cookie 或 MCP 依赖；跨城市办公也必须能通过 SkillForge 平台完成真实联调。

## 3. 非目标

- 不把钉钉 access_token 返回给本地。
- 不把平台 MCP server 的 command/env/headers 返回给本地。
- 不允许本地绕过 SkillForge 直接拿 Cookie 或业务 API key。
- 不支持把生产 MCP server 整套复制到开发者电脑后直连业务系统。
- 不改变 SkillForge 是控制面的边界；生产 Skill 执行仍优先在 Bridge/OpenClaw/AIClaw 节点侧发生。

## 4. 登录与 30 天 CLI Session

本地登录走浏览器扫码 + PKCE：

```text
sf auth login
  -> 本地开启 127.0.0.1 随机端口 callback
  -> 打开 SkillForge 授权页
  -> 钉钉扫码
  -> SkillForge 校验 dingtalk_user_id 对应 users 表
  -> 回跳本地 callback，返回一次性 auth_code
  -> 本地用 code + code_verifier 换 CLI session token
```

CLI session token 是 30 天有效的 opaque token。后端只保存 token hash。当前 CLI 实现把原始 token 保存到用户级 `~/.skillforge/codex-cli.json`，目录权限 `0700`、文件权限 `0600`；企业部署可把同一接口替换为系统凭据库：

- macOS：Keychain。
- Windows：Credential Manager。
- Linux：优先 Secret Service / libsecret。
- 无可用系统凭据库时，fallback 到 `$CODEX_HOME/skillforge/auth/session.json`，目录权限必须是 `0700`，文件权限必须是 `0600`，并禁止同步到 Skill 包、submission package、日志和 catalog cache。

`sf auth status` 只输出账号、过期时间和权限摘要，不输出 token 原文。

可用性要求：CLI session 默认 30 天，不降到 8-24 小时。只要用户仍 active、权限版本未变化且 session 未吊销，`sf` CLI 自动续用该 session 换取调试用短期 `run_token`，不得要求用户频繁扫码。

安全边界：30 天 CLI token 是本机 `sf` CLI 的登录态，不是 Skill 运行凭证。CLI token 不注入 `scripts/main.py` 子进程，不写入 Skill 包，不出现在调试日志、stdout/stderr、proof 或 submission payload 中。Skill 本地调试子进程只接收短期 `SKILLFORGE_RUN_TOKEN`。

PKCE / callback 安全要求：

- 本地 callback server 只能绑定 `127.0.0.1` 或 `[::1]`，不得绑定 `0.0.0.0`。
- callback 只接受一次有效请求，收到 `auth_code` 后立即关闭 listener。
- callback listener 最长存活 10 分钟，超时后销毁 `state`、`nonce`、`code_verifier`。
- 后端必须严格校验 `redirect_uri`、`state`、`nonce`、`code_challenge`、`code_verifier`，一次性 `auth_code` 用后即废。
- `device_name` 由客户端自报时必须限制长度不超过 64 字符，只允许普通可显示字符；平台 UI 展示前必须 HTML escape。

表结构建议：

```sql
codex_cli_sessions(
  id varchar primary key,
  user_id varchar not null,
  token_hash varchar not null unique,
  device_name varchar,
  scopes_json jsonb not null,
  permissions_rev_snapshot int not null,
  created_at timestamp not null,
  expires_at timestamp not null,
  last_seen_at timestamp,
  revoked_at timestamp,
  revoked_reason varchar
);
```

默认 `expires_at = created_at + 30 days`。

### 按需弹出网页登录

可以实现“调用时自动弹网页登录”，但只在交互式命令里触发。`/sf`、`sf skill test --real-mcp`、`sf skill submit`、`sf capabilities` 这类用户主动调用的命令，如果发现没有有效 CLI session，应自动进入登录流程：

```text
1. sf command 调 ensure_auth(interactive=true)
2. POST /api/codex/auth/login-intent 创建 login_intent_id、state、PKCE code_challenge
3. 本地开启 127.0.0.1 随机端口 callback
4. 自动打开默认浏览器到 SkillForge 授权页
5. 用户在网页钉钉扫码
6. SkillForge 校验账号、部门、离职/禁用状态、权限版本
7. 浏览器回跳本地 callback，带一次性 auth_code
8. CLI 用 auth_code + code_verifier 换 30 天 CLI session
9. 原命令继续执行
```

浏览器打开方式：

```text
Linux:   xdg-open <login_url>
macOS:   open <login_url>
Windows: start <login_url>
```

兼容 SSH / 无桌面环境：

- 自动打开浏览器失败时，CLI 在终端打印短链接和二维码。
- 支持 `sf auth login --no-browser`，只打印 URL / QR。
- `login_intent` 默认 5-10 分钟过期，过期后重新发起。
- `state`、`nonce`、`code_verifier` 只保存在本机临时内存或本机 CLI auth cache，不进入 Skill 包。

后台进程不要无提示抢焦点：

- `sf mcp stdio` 启动时如果未登录，默认不自动弹浏览器，只返回 `AUTH_REQUIRED` 和脱敏 `login_url`，让 Codex 在用户调用 `/sf auth` 或真实 MCP tool 时触发登录。
- 用户明确执行 `/sf auth`、`/sf test-real`、`/sf submit`、`sf auth login` 时，可以自动弹浏览器。
- 非交互模式或 CI 中必须要求显式 token / service credential，不允许弹浏览器等待人工扫码。

### Host 与 TLS

`SKILLFORGE_HOST` 必须使用 `https://`。`sf` CLI 必须验证 TLS 证书链，默认拒绝自签名证书、过期证书和 hostname mismatch。企业内网自签或私有 CA 只能通过企业安装配置显式导入受信 CA 或固定 platform host allowlist。

`sf` CLI 不允许从 Skill 包、`skillforge.yaml`、`contract.json` 或当前工作区 `.env` 读取并覆盖 `SKILLFORGE_HOST`。允许来源优先级建议为：命令行显式参数、企业托管配置、用户级 CLI 配置、环境变量。发生 host 变化时，CLI 必须重新 introspect，并在交互式模式向用户展示目标 host。

## 5. 每次启动检查

本地 `sf mcp stdio`、`sf skill test --real-mcp`、`sf skill submit` 启动前必须调用：

```text
POST /api/codex/auth/introspect
Authorization: Bearer <cli_session_token>
```

通过条件：

- token_hash 存在。
- session 未过期。
- `revoked_at is null`。
- `users.state == active`。
- `users.is_active == true`。
- `permissions_rev_snapshot == users.permissions_rev`。

失败时本地必须停止调用真实 MCP，并提示重新扫码或联系管理员。

用户离职 / 禁用时：

- 管理后台把 `users.state` 改为 `disabled`。
- `users.permissions_rev += 1`。
- 批量 revoke 该用户所有 `codex_cli_sessions`。
- 即使漏 revoke，下一次 introspect 和敏感接口实时校验也必须失败。

## 6. 平台 MCP Gateway API

### 6.1 Catalog

```text
GET /api/codex/mcp-catalog
Authorization: Bearer <cli_session_token>
```

返回当前用户可见、可调用的 MCP tool schema，不返回 server env。

示例：

```json
{
  "servers": [
    {
      "name": "skillforge",
      "tools": [
        {
          "name": "tmall_sycm_item_rank_top",
          "description": "获取生意参谋商品排行 TopN",
          "inputSchema": {
            "type": "object",
            "properties": {
              "limit": {"type": "integer", "default": 20},
              "dateType": {"type": "string", "default": "today"},
              "shop_id": {"type": "string"}
            }
          },
          "meta": {
            "platform": "sycm",
            "data_scope": "sycm.item_rank",
            "risk_level": "R2",
            "write": false
          }
        }
      ]
    }
  ]
}
```

### 6.2 Call

```text
POST /api/codex/mcp/call
Authorization: Bearer <cli_session_token>
```

请求：

```json
{
  "server": "skillforge",
  "tool": "tmall_sycm_item_rank_top",
  "arguments": {
    "limit": 20,
    "dateType": "today",
    "shop_id": "shop_001"
  },
  "skill_id": "ec-daily-check",
  "run_mode": "local_debug",
  "run_id": "debug_001",
  "dry_run": true,
  "idempotency_key": "required-for-write-tools"
}
```

响应：

```json
{
  "ok": true,
  "tool": "tmall_sycm_item_rank_top",
  "data": {},
  "proof": {
    "call_source": "codex_cli",
    "run_mode": "local_debug",
    "user_id": "u_123",
    "skill_id": "ec-daily-check",
    "platform": "sycm",
    "data_scope": "sycm.item_rank",
    "credential_location": "platform_only"
  }
}
```

响应必须脱敏：

- 不返回 Cookie。
- 不返回 Authorization header。
- 不返回 app secret。
- 不返回浏览器完整 session。
- 原始平台响应如果含敏感字段，按 data_scope 脱敏后返回。

写操作 MCP 规则：

- tool catalog 中 `meta.write=true` 的工具，在 `local_debug` 和 `sandbox` 默认强制 `dry_run=true`。
- 客户端传 `dry_run=false` 时，服务端必须忽略或拒绝，除非当前 run 明确具备平台签发的写操作授权。
- 写操作必须带 `idempotency_key`，缺失时返回 `PACKAGE_INVALID` 或专用 `IDEMPOTENCY_KEY_REQUIRED`。
- 写操作的 proof 必须记录 `dry_run`、`idempotency_key`、审批来源和操作者。

## 6.3 Codex API Surface

平台需要给 `sf` CLI 和 Codex skill 提供稳定接口。接口可以内部复用现有 `app/pipeline/`、`app/execution/`、`app/todos/`、`app/inbox/` 能力，但对本地只暴露以下边界。

### Auth

```text
GET /api/codex/auth/authorize
POST /api/codex/auth/login-intent
POST /api/codex/auth/token
POST /api/codex/auth/introspect
POST /api/codex/auth/logout
```

- `authorize`：浏览器扫码入口，使用 PKCE，不返回 token 给网页脚本。
- `login-intent`：CLI 创建一次登录意图，返回授权 URL、过期时间、state 和 PKCE 所需参数；用于调用时自动弹网页登录。
- `token`：一次性 `auth_code` 换 30 天 CLI session token。
- `introspect`：每次 CLI 启动、真实 MCP 调用、提交审核前必须调用。
- `logout`：吊销当前设备 token。

### Capability

```text
GET /api/codex/capabilities
GET /api/codex/mcp-catalog
GET /api/codex/platform-capabilities
GET /api/codex/skill-editor-capabilities
GET /api/codex/catalogs/manifest
GET /api/codex/catalogs/bundle
```

`/api/codex/capabilities` 返回用户维度能力：

```json
{
  "user": {"id": "u_123", "name": "张三", "state": "active"},
  "skill_permissions": {
    "can_create_skill": true,
    "can_submit_review": true,
    "can_publish": false,
    "departments": ["传统电商"]
  },
  "mcp_permissions": {
    "visible_scopes": ["sycm.item_rank"],
    "callable_tools": ["tmall_sycm_item_rank_top"],
    "shop_ids": ["shop_001"]
  },
  "api_permissions": {
    "callable_apis": ["mcp://tmall_sycm_item_rank_top"]
  }
}
```

`/api/codex/skill-editor-capabilities` 返回 SkillStudio 编辑侧契约，让 Codex skill 知道平台当前如何表示、保存、校验和提交 Skill：

```json
{
  "structured_modules": [
    {"key": "meta", "label": "基础信息", "writable": true},
    {"key": "goal", "label": "目标", "writable": true},
    {"key": "rules", "label": "规则", "writable": true},
    {"key": "params", "label": "参数", "writable": true},
    {"key": "output_table", "label": "输出", "writable": true},
    {"key": "todos", "label": "待办", "writable": true},
    {"key": "test_cases", "label": "测试", "writable": true},
    {"key": "workflow", "label": "工作流", "writable": true}
  ],
  "file_contract": {
    "required": ["SKILL.md", "contract.json", "scripts/main.py", "tests/test_main.py", "fixtures/sample_input.json"],
    "platform_normalized": ["skillforge.yaml"],
    "recommended": ["intent.md", "policy.yaml", "policy_pack.yaml", "source_contract.yaml", "metric_registry.yaml"],
    "runtime_entrypoint": "scripts/main.py"
  },
  "editor_apis": {
    "read": "GET /api/skills/{skill_id}",
    "lock": "POST /api/skills/{skill_id}/lock",
    "save_structured": "PUT /api/skills/{skill_id}/structured",
    "save_file": "PUT /api/skills/{skill_id}/files/{file_path}",
    "validate_all": "POST /api/skills/{skill_id}/validate-all",
    "publish_readiness": "POST /api/skills/{skill_id}/publish-readiness",
    "history": "GET /api/skills/{skill_id}/history",
    "diff": "GET /api/skills/{skill_id}/diff",
    "review_create": "POST /api/reviews/"
  },
  "gates": {
    "require_git_commit": true,
    "require_test_or_sandbox_run": true,
    "require_output_schema": true,
    "require_runtime_data_acquisition": true,
    "require_static_check": true,
    "require_cross_skill_conflict_check": true
  }
}
```

`skillforge.yaml` 可以由本地提交，用于声明运行元数据和 scope；平台入库时会规范化该文件。字段权威规则：

- 本地可声明：`skill_id`、`display_name`、`department`、`trigger_type`、`trigger_expression`、`risk_level`、`approval_level`、`mcp_scopes`、`data_contracts`、`entrypoint`。
- 平台覆盖或补齐：`owner`、发布版本、审核状态、节点部署状态、平台内部调度下发状态。
- 冲突时以平台数据库和审核通过的 release 配置为准，同时把规范化后的 `skillforge.yaml` 写入 Skill Git commit。

Codex skill 必须把这个接口当作 SkillStudio 兼容层的真源。平台后续如果新增模块、门禁或文件要求，只改这个 catalog，Codex skill 不应该依赖模型记忆。

### Catalog Auto Update

Codex skill 和 `sf` CLI 不应把平台接口写死在本地文档里。平台需要提供一个版本化 catalog manifest，用于自动发现新接口、新 MCP、新输出能力和废弃字段：

```text
GET /api/codex/catalogs/manifest
Authorization: Bearer <cli_session_token>
If-None-Match: <etag>
```

返回：

```json
{
  "platform_version": "2026.05.14",
  "schema_version": "codex-catalog.v1",
  "catalog_rev": "rev_20260514_001",
  "etag": "W/\"rev_20260514_001\"",
  "generated_at": "2026-05-14T16:00:00+08:00",
  "cache_ttl_seconds": 3600,
  "min_cli_version": "0.8.0",
  "recommended_cli_version": "0.9.2",
  "publisher_skill": {
    "min_version": "0.3.0",
    "recommended_version": "0.4.1",
    "update_mode": "catalog_first",
    "auto_install": false
  },
  "catalogs": {
    "capabilities": "/api/codex/capabilities",
    "mcp": "/api/codex/mcp-catalog",
    "platform": "/api/codex/platform-capabilities",
    "editor": "/api/codex/skill-editor-capabilities"
  },
  "deprecations": [
    {
      "kind": "api",
      "name": "old_endpoint",
      "replacement": "new_endpoint",
      "remove_after": "2026-08-01"
    }
  ],
  "breaking_changes": [],
  "reference_bundle": {
    "url": "/api/codex/catalogs/bundle?rev=rev_20260514_001",
    "sha256": "sha256:...",
    "signature": "base64-ed25519-signature",
    "signature_alg": "ed25519-jcs-sha256",
    "key_id": "skillforge-catalog-2026q2",
    "format": "json",
    "contains_executable": false
  }
}
```

`/api/codex/catalogs/bundle` 只返回 JSON / Markdown 参考资料，不允许下发可执行脚本。`sf` CLI 可以把 bundle 缓存在本地，例如：

```text
$CODEX_HOME/skillforge/cache/catalog-manifest.json
$CODEX_HOME/skillforge/cache/platform-catalog.json
$CODEX_HOME/skillforge/cache/editor-capabilities.json
```

自动更新规则：

- 每次 `sf mcp stdio`、`sf skill test --real-mcp`、`sf skill submit` 启动时先 introspect，再检查 catalog manifest。
- ETag 未变化时使用本地缓存。
- ETag 变化时自动刷新 catalog；这类刷新不修改用户 Skill 包。
- `min_cli_version` 高于本地版本时，CLI 必须 fail closed，提示升级。
- `deprecations` 必须在 doctor / submit 输出中提示。
- `breaking_changes` 非空时，提交审核前必须显示阻断项或迁移建议。
- 参考 bundle 必须校验 `sha256` 和签名；校验失败不得使用。
- 签名算法使用 Ed25519 + JCS canonical JSON + SHA-256。签名输入必须精确定义为：复制 manifest，删除 `reference_bundle.signature` 字段，对剩余 manifest 做 JCS canonical JSON 序列化，再签名该字节串。`reference_bundle.sha256` 已在 manifest 内，必须参与签名。
- CLI 内置 SkillForge 平台 catalog 公钥或由企业安装配置固定公钥；`key_id` 只能在受信任公钥集合内轮换。未知 `key_id` 必须 fail closed。
- 公钥集合必须有明确格式，例如 `$CODEX_HOME/skillforge/trust/catalog-keys.json`，包含 `key_id`、`public_key_ed25519`、`not_before`、`not_after`、`status`。轮换时新旧 key 至少重叠一个 catalog 发布周期；撤销 key 后所有由该 key 签名的新 bundle fail closed。
- `publisher_skill.min_version` 高于当前 Codex skill 版本时，CLI 继续允许 `sf catalog refresh` 和 `sf auth status`，但阻断提交并提示更新 `skillforge-publisher` skill。
- 自动更新只能更新 catalog / reference 数据，不能远程执行平台下发代码。

### Debug Run

```text
POST /api/codex/skill/debug-runs
GET /api/codex/skill/debug-runs/{run_id}
GET /api/codex/skill/debug-runs/{run_id}/events
POST /api/codex/skill/debug-runs/{run_id}/complete
```

创建本地真实调试 run：

```json
{
  "skill_id": "ec-daily-check",
  "run_mode": "local_debug",
  "package_hash": "sha256:...",
  "manifest": {
    "mcp_scopes": ["sycm.item_rank"],
    "data_contracts": ["sycm_item_rank_v1"]
  },
  "input": {"shop_id": "shop_001"},
  "requested_tools": ["tmall_sycm_item_rank_top"]
}
```

返回短期本地调试 token：

```json
{
  "run_id": "debug_001",
  "run_token": "opaque-short-lived-token",
  "expires_at": "2026-05-14T18:30:00+08:00",
  "gateway_url": "https://skillforge.company.com/api/codex/mcp/call",
  "output_mode": "preview_only",
  "limits": {
    "max_tool_calls": 50,
    "timeout_seconds": 300
  }
}
```

`run_token` 必须短期有效，且只允许当前 `skill_id`、`run_id`、`run_mode=local_debug`、manifest 声明的 scope 和当前用户授权范围。

`run_token` 格式要求：

- 默认使用 opaque token，后端只保存 hash，并关联 `cli_session_id`、`user_id`、`skill_id`、`run_id`、`run_mode`、scope、shop_ids、expires_at、revoked_at。
- 默认有效期建议 15-60 分钟，不得超过当前 CLI session 剩余有效期。
- CLI session 被 revoke、用户禁用、`permissions_rev` 变化时，关联的未过期 `run_token` 必须立即失效。
- 如未来改用 JWT，必须使用企业固定算法 allowlist、包含 `jti`、`sub`、`sid`、`run_id`、`skill_id`、`run_mode`、`scope`、`exp`、`iat`，并在服务端保留 `jti` 状态以支持撤销；不得使用不可撤销的纯自包含 token。

短期 `run_token` 由 30 天 CLI session 自动换取，不需要用户重新扫码。`run_token` 过期只影响当前调试进程；CLI session 仍有效时，下一次 `sf skill test --real-mcp` 自动创建新的 debug run。

`complete` 上报本地执行结果：

```text
POST /api/codex/skill/debug-runs/{run_id}/complete
Authorization: Bearer <run_token>
```

```json
{
  "status": "success",
  "duration_ms": 12450,
  "output": {
    "reports": [],
    "todos": [],
    "performance": {}
  },
  "stdout_tail": "...",
  "stderr_tail": "",
  "test_summary": {"passed": 8, "failed": 0}
}
```

平台只保存脱敏后的 stdout/stderr tail，不保存 token、Cookie、Authorization header。CLI 侧必须先截断 stdout/stderr tail，单字段默认不超过 64 KiB；服务端必须再次限制请求体大小并做敏感字段脱敏。

### Sandbox Run

```text
POST /api/codex/skill/sandbox-runs
GET /api/codex/skill/sandbox-runs/{run_id}
GET /api/codex/skill/sandbox-runs/{run_id}/events
```

sandbox 由 SkillForge 后端或运行节点执行完整包，用于验证正式运行环境。sandbox 也走 Gateway MCP，但输出默认只进入 sandbox artifact，不创建正式待办、不推正式消息。

### Submission

```text
POST /api/codex/skill/submissions
GET /api/codex/skill/submissions/{submission_id}
GET /api/codex/skill/submissions/{submission_id}/checks
```

如果平台已有 `/api/pipeline/submissions`，可以作为内部实现；Codex 对外仍建议保留 `/api/codex/skill/submissions` 这一层，便于做本地来源、权限、包校验和错误码收敛。

提交使用 `multipart/form-data`：

```text
package=@skillforge-package.tar.gz
manifest_json=<json string>
package_hash=sha256:<normalized package hash>
base_commit=<remote git_commit_full or empty for new skill>
message=<commit/review message>
```

`manifest_json` 至少包含：

```json
{
  "skill_id": "ec-daily-check",
  "source": "codex_cli",
  "entrypoint": "scripts/main.py",
  "mcp_scopes": ["sycm.item_rank"],
  "data_contracts": ["sycm_item_rank_v1"],
  "package_format": "tar.gz",
  "file_count": 8,
  "files": [
    {"path": "SKILL.md", "sha256": "sha256:...", "bytes": 1234},
    {"path": "contract.json", "sha256": "sha256:...", "bytes": 2048}
  ]
}
```

包规则：

- `package_hash` 不是压缩文件 hash，而是规范化文件树 hash：按 UTF-8 相对路径排序，逐项拼接 `path\0bytes\0sha256(content)\n` 后再 SHA-256。
- 允许路径：`SKILL.md`、`contract.json`、`skillforge.yaml`、`policy_pack.yaml`、`policy.yaml`、`intent.md`、`source_contract.yaml`、`metric_registry.yaml`、`scripts/**`、`tests/**`、`fixtures/**`、`references/**`、`assets/**`。
- 禁止路径：绝对路径、`..`、symlink、hardlink、`.git/**`、`.env*`、`node_modules/**`、`.venv/**`、`__pycache__/**`、`skills-repo/**`、`$CODEX_HOME/**`、本地 cache、任何包含 token/secret/cookie 的文件。
- 默认包大小上限 20 MiB，单文件上限 5 MiB；超过时必须走平台 artifact 上传策略，不走普通 submission。
- 平台解包后必须重新计算 `package_hash`，与请求字段不一致时拒绝。
- 写入 `skills-repo/<skill_id>/` 前必须在服务端再次运行路径校验、secret scan、manifest scope 校验、output schema 校验。
- `base_commit` 只接受完整 Git object id，当前 SHA-1 仓库为 40 位十六进制；不得接受分支名、tag、短 hash 或空白字符串伪装。

服务端解包顺序必须固定：

```text
1. 接收 tar.gz 到隔离临时目录。
2. 读取 tar headers，但不直接覆盖目标工作区。
3. 对每个 entry 做规范化路径校验、UTF-8 / Unicode NFC 归一、白名单校验、大小校验、symlink/hardlink 拒绝。
4. 全部 entry 校验通过后解包到隔离临时目录。
5. 重新计算 normalized package_hash、运行 secret scan、schema 校验和 scope 校验。
6. 获取 skill_id advisory lock。
7. 再次检查 base_commit 与远端 HEAD。
8. 原子写入 skills-repo/<skill_id>/ 并创建 Git commit / 审核单。
```

secret scan 至少覆盖：

- `AKIA` / 云厂商 access key。
- `Bearer `、`Authorization:`、`access_token`、`refresh_token`、`api_key`、`secret_key`、`client_secret`。
- PEM 私钥头：`BEGIN PRIVATE KEY`、`BEGIN RSA PRIVATE KEY`、`BEGIN OPENSSH PRIVATE KEY`。
- 高熵长字符串、JWT、Cookie、钉钉 token、业务系统 session。
- `.env*`、本地 auth cache、浏览器 cookie 导出、MCP server env dump。

实现可使用 gitleaks / truffleHog / detect-secrets 或平台等价扫描器。误报只能走显式 allowlist 文件或人工审核，allowlist 本身必须进入审计；不得允许 Codex 自动添加 secret scan 豁免。

写入原子性：

- 对同一个 `skill_id` 获取服务端 advisory lock。
- 锁内重新读取远端 HEAD。
- 已存在 Skill 时，`base_commit` 必须等于当前 `git_commit_full`；不一致返回 `PACKAGE_CONFLICT`，不得写入。
- 新 Skill 时，`base_commit` 必须为空，且 `skill_id` 不存在。
- 写入文件、Git commit、创建审核单必须在一个业务事务中保证可追溯；失败时不得留下半写入工作区。

### Remote Edit State

本地提交已存在 Skill 前，需要先读取平台编辑态，避免覆盖 SkillStudio 刚保存的内容。

```text
GET /api/codex/skills?scope=visible|editable|publishable|mine
POST /api/codex/skills/local-status
GET /api/codex/skills/{skill_id}/remote-status
POST /api/codex/skills/{skill_id}/conflict-check
POST /api/codex/skills/{skill_id}/diff-package
```

`GET /api/codex/skills` 用于 `/sf 查看哪些技能` 这类查询。返回结果必须按当前用户权限裁剪，不允许把无权查看的 Skill 元数据泄露给本地。

`scope` 语义：

```text
visible      当前用户可查看的 Skills。
editable     当前用户可编辑的 Skills。
publishable  当前用户可提交审核或发布的 Skills。
mine         当前用户创建、负责或参与维护的 Skills。
```

返回建议：

```json
{
  "items": [
    {
      "skill_id": "ec-daily-check",
      "name": "每日商品排行诊断",
      "department": "传统电商",
      "visibility": "department",
      "can_edit": true,
      "can_submit": true,
      "can_publish": false,
      "git_commit_full": "abc...",
      "latest_review": {"id": 123, "status": "pending"},
      "updated_at": "2026-05-14T10:00:00+08:00"
    }
  ],
  "next_cursor": null
}
```

`POST /api/codex/skills/local-status` 用于 `/sf 哪些未上传`。CLI 本地扫描目录后只提交摘要，不提交完整 package：

```json
{
  "items": [
    {
      "client_ref": "scan_item_001",
      "skill_id": "ec-daily-check",
      "base_commit": "abc...",
      "package_hash": "sha256:...",
      "file_count": 8,
      "manifest_sha256": "sha256:..."
    }
  ]
}
```

平台返回：

```json
{
  "items": [
    {
      "client_ref": "scan_item_001",
      "skill_id": "ec-daily-check",
      "status": "modified_not_submitted",
      "remote_head": "def...",
      "remote_package_hash": "sha256:...",
      "can_submit": true,
      "reason": "local package differs from remote"
    }
  ],
  "status_values": [
    "never_uploaded",
    "modified_not_submitted",
    "remote_newer",
    "synced",
    "missing_metadata",
    "no_permission"
  ]
}
```

`local-status` 不接收源码内容，不接收绝对本地路径，不落库本地路径，只用于当前请求比较；真正上传必须走 `/api/codex/skill/submissions`。如果 CLI 需要在本地 UI 关联路径，只能使用 `client_ref`，平台不得记录或回显开发者机器绝对路径；请求日志必须脱敏该字段。

`remote-status` 返回：

```json
{
  "skill_id": "ec-daily-check",
  "git_commit_full": "abc...",
  "git_head_commit": {"hash_full": "abc...", "message": "结构化编辑更新 SKILL.md"},
  "git_commit_synced": true,
  "lock": {"locked": false, "locked_by": null},
  "latest_review": {"id": 123, "status": "pending"},
  "can_submit": true,
  "can_edit": true
}
```

`conflict-check` 输入本地包基于的 `base_commit`：

```json
{"base_commit": "abc..."}
```

返回：

```json
{
  "conflict": false,
  "remote_head": "abc...",
  "message": "remote unchanged"
}
```

`diff-package` 接收本地 package manifest / hash / 文件摘要，返回平台能展示给 Codex 和用户看的差异摘要；不要要求本地直接读 `skills-repo/`。

## 7. 本地 MCP Proxy

`sf mcp stdio` 实现标准 MCP stdio 协议：

```text
initialize -> 返回 serverInfo
tools/list -> 调 /api/codex/mcp-catalog 后组装 tools
tools/call -> 调 /api/codex/mcp/call
```

`sf mcp stdio` 的 `tools/call` 也必须有运行边界，不允许无限制使用 30 天 CLI session 直接调真实 MCP：

- 第一次真实 `tools/call` 前，CLI 自动创建 `mcp_session` 类型的 local_debug run，或要求调用方传入已有 `run_id`。
- 后续 `tools/call` 必须带 `run_id` 和短期 `run_token`，服务端按该 run 的 scope、shop_id、max_tool_calls、timeout_seconds 判权。
- 所有 proof 必须关联到 `run_id`、user、tool、scope、shop_id 和 `call_source=codex_mcp_stdio`。
- `sf mcp stdio` 启动未登录时不弹浏览器；用户首次真实调用时才通过 `/sf auth` 或交互式登录流程补齐登录态。
- `tools/list` 只需要 read/catalog 权限；`tools/call` 必须具备 execute/debug_mcp 权限。

Codex 配置：

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

本地 MCP proxy 不允许读取平台 MCP 配置原文，不允许落盘 tools/call 的敏感响应。

## 8. SkillForge SDK 本地真实 MCP 模式

本地调试 Skill 时，`scripts/main.py` 仍然只写：

```python
from skillforge_sdk import SkillForge

sf = SkillForge("ec-daily-check")

rank = sf.fetch_api(
    "mcp://tmall_sycm_item_rank_top",
    body={"limit": 20, "dateType": "today"},
    shop_id="shop_001",
)
```

CLI 注入：

```text
SKILLFORGE_PLATFORM_URL=https://skillforge.company.com
SKILLFORGE_RUN_TOKEN=<short_lived_local_debug_run_token>
SKILLFORGE_SKILL_ID=ec-daily-check
SKILLFORGE_RUN_MODE=local_debug
```

SDK 调用优先级：

1. 有 `SKILLFORGE_RUN_TOKEN`：Skill 子进程走 SkillForge MCP Gateway。
2. 无 `SKILLFORGE_RUN_TOKEN` 且需要真实 MCP/API：拒绝调用并提示先 `sf auth login` 或用 `sf skill test --real-mcp` 启动。
3. 本地测试默认只允许 fixture / mock。任何真实业务数据调用都必须走 SkillForge MCP Gateway。
4. 仅平台服务器内部兼容场景可显式启用共享 scripts 直连；普通开发者本机不走该路径。

30 天 `SKILLFORGE_CLI_TOKEN` 只供 `sf` CLI 和 `sf mcp stdio` 进程内部使用，用来 introspect、刷新 catalog、创建 debug run、提交 package。它不进入 Skill runtime 环境。

## 9. 本地调试到正式运行的自动切换

Skill 代码不得区分“Codex MCP”和“SkillForge MCP”。从开发期开始就只能写 SkillForge SDK 的稳定入口：

```python
sf.fetch_api("mcp://tmall_sycm_item_rank_top", body={...})
```

自动切换靠运行环境和 token，不靠改代码：

```text
本地真实调试
  -> SKILLFORGE_RUN_MODE=local_debug
  -> 30 天 CLI session 自动换 local_debug run token
  -> 子进程只注入 SKILLFORGE_RUN_TOKEN
  -> SDK 调 SkillForge MCP Gateway

沙箱运行
  -> SKILLFORGE_RUN_MODE=sandbox
  -> sandbox run token
  -> SDK 调 SkillForge MCP Gateway

正式运行
  -> SKILLFORGE_RUN_MODE=production
  -> production run token
  -> SDK 调 SkillForge MCP Gateway
```

Codex 提交到 SkillForge 后，Codex 不参与正式运行，本地 30 天 CLI token 也不参与正式运行。正式运行由 SkillForge 为当前发布版本、当前 run、当前节点签发短期 `SKILLFORGE_RUN_TOKEN`。

正式 `run_token` 至少绑定：

- `skill_id`
- `run_id`
- `instance_id`
- 发布 `commit/tag`
- `run_mode=production`
- manifest 声明的 `mcp_scopes`
- manifest 声明的 `data_contracts`
- 可选 `shop_ids`
- 过期时间

发布门禁必须检查 `scripts/main.py`：

- 禁止直接 import 平台 MCP server。
- 禁止直接启动 MCP 脚本。
- 禁止直接 `requests` 调业务系统。
- `mcp://` 调用必须能在 `skillforge.yaml` / `contract.json` 中找到对应 scope 声明。
- `reports` / `todos` / metrics 等输出能力必须符合平台契约。

## 10. SkillForge 平台能力 Catalog

Codex skill 需要知道的不只是 MCP/API，还包括 SkillForge 能接收和治理哪些输出能力。平台必须提供一个能力目录：

```text
GET /api/codex/platform-capabilities
Authorization: Bearer <cli_session_token>
```

返回内容按当前用户、部门和平台版本裁剪：

```json
{
  "output_capabilities": {
    "todos": {
      "enabled": true,
      "description": "Skill output.todos 会进入平台待办中心；dispatch 待办审批通过后才会推执行人钉钉。",
      "kinds": ["review", "dispatch"],
      "decision_modes": ["any_of", "all_of", "independent"],
      "reviewer_roles": ["biz_owner", "operator", "ai_engineer", "admin", "director"],
      "required_fields": ["kind", "title"],
      "dispatch_required_fields": ["tasks[].content"],
      "doc": "docs/spec/codex-local-mcp-gateway-platform-spec.md#todo-capability"
    },
    "reports": {
      "enabled": true,
      "description": "Skill output.reports 会沉淀为 /inbox 报告卡片。",
      "channels": ["dingtalk_card", "dingtalk_markdown", "email", "feishu"],
      "required_fields": ["channel", "title", "summary", "recipients"],
      "supports_metrics": true,
      "supports_primary_indicator": true,
      "doc": "docs/spec/skill-output-report-v1.md"
    },
    "performance_metrics": {
      "enabled": true,
      "description": "Skill 可以在 reports.metrics / reports.primary_indicator / 顶层 performance 中声明业务和运行指标，供 inbox、看板、复盘和发布评估使用。",
      "metric_fields": ["label", "value", "trend", "delta"],
      "primary_indicator_fields": ["label", "value", "severity", "tone"]
    },
    "data_proofs": {
      "enabled": true,
      "description": "MCP/API 调用 proof 会进入 execution metadata，用于审核、回放和数据来源追踪。"
    }
  },
  "runtime_capabilities": {
    "local_debug_real_mcp": true,
    "sandbox": true,
    "production_gateway_mcp": true,
    "node_scheduler": true,
    "fallback_scheduler": true
  }
}
```

Codex skill 在生成或审查 Skill 时必须先读取该目录，按平台当前能力决定输出结构，不能凭模型记忆臆造字段。

### Todo Capability

Skill 返回：

```json
{
  "todos": [
    {
      "kind": "dispatch",
      "title": "商品 TM001 流量下滑处理",
      "summary": "近 7 日 UV 下滑 18%，建议检查主图和搜索词。",
      "payload": {
        "object_id": "TM001",
        "priority": "high",
        "data_overview": {"uv_delta": "-18%"},
        "analysis_basis": ["生意参谋商品排行", "商品 360 流量来源"],
        "operation_actions": ["检查主图点击率", "排查搜索词排名"]
      },
      "reviewer_role": "biz_owner",
      "sla_hours": 24,
      "tasks": [
        {"content": "检查 TM001 主图点击率并反馈", "deadline": "2026-05-15T18:00:00+08:00"}
      ]
    }
  ]
}
```

平台行为：

- `review` / `dispatch` 都先进入平台待办中心。
- `dispatch` 子任务只有在管理者审批通过后才推执行人钉钉。
- Skill 不能在 `main.py` 里直接推钉钉。
- `payload` 放运营可读决策卡片，不放完整原始 API 响应。

### Report And Performance Capability

Skill 返回：

```json
{
  "reports": [
    {
      "channel": "dingtalk_card",
      "title": "每日商品排行诊断",
      "summary": "Top20 中 3 个商品出现明显流量下滑，需要运营复核。",
      "content_markdown": "## 诊断结果\n\n- TM001 UV -18%\n- TM009 CVR -12%",
      "recipients": {
        "roles": ["biz_owner", "aibp"],
        "departments": ["传统电商"]
      },
      "payload": {"affected_items": 3},
      "metrics": [
        {"label": "异常商品", "value": "3", "trend": "up", "delta": "+2"},
        {"label": "最大 UV 下滑", "value": "18%", "trend": "down"}
      ],
      "primary_indicator": {
        "label": "异常商品",
        "value": "3",
        "severity": "high",
        "tone": "warning"
      },
      "tags": ["商品", "流量", "日报"]
    }
  ],
  "performance": {
    "business_metrics": [
      {"label": "覆盖商品数", "value": 20},
      {"label": "生成建议数", "value": 3}
    ],
    "quality_metrics": [
      {"label": "数据源成功率", "value": "100%"}
    ]
  }
}
```

平台行为：

- `reports` 在成功、非 sandbox 执行后沉淀为 `/inbox` 报告卡片。
- `metrics` / `primary_indicator` 用于报告卡片、待办关联卡片和后续看板。
- `performance` 是 Codex/Skill 作者的建议输出区；平台可按版本逐步接入价值卡、性能报告或效果看板。即使当前 UI 未完整消费，也应保留在 output 中供后续分析。

## 11. 权限模型

MCP call 必须同时满足：

1. 用户 active，CLI session 有效。
2. `tools/list` 要求用户对 `skill_id` 至少有 `read`；`tools/call` / `sf skill test --real-mcp` 要求 `execute`、`edit` 或显式 `debug_mcp` 权限。
3. 用户所属部门 / 管辖部门允许访问该 tool 的 owner org 或 visibility。
4. tool 的 `data_scope` 在用户授权范围内。
5. `shop_id` / 店铺 / 平台账号在用户授权范围内。
6. 写操作 tool 默认强制 `dry_run=true`；真实写操作必须走审核或额外一次性授权。
7. `can_view_all` 只扩大读取范围，不自动授予 MCP 写操作或发布权限。

权限实现必须复用 Skill 统一权限 helper，不新增散落的角色判断。

## 12. 标准错误响应

所有 `/api/codex/*` 接口失败时返回同一结构：

```json
{
  "ok": false,
  "error": {
    "code": "MCP_SCOPE_DENIED",
    "message": "无权调用该 MCP tool",
    "detail": {
      "skill_id": "ec-daily-check",
      "tool": "tmall_sycm_item_rank_top",
      "missing_scope": "sycm.item_rank"
    },
    "request_id": "req_..."
  }
}
```

标准错误码：

| code | HTTP | 触发场景 | CLI 行为 |
|---|---:|---|---|
| `AUTH_REQUIRED` | 401 | 缺少 CLI session | 交互式命令自动打开网页登录；非交互模式提示 `sf auth login --no-browser` |
| `TOKEN_EXPIRED` | 401 | 30 天 session 到期 | 交互式命令自动打开网页登录 |
| `TOKEN_REVOKED` | 401 | session 被吊销 | 停止真实调用 |
| `USER_DISABLED` | 403 | 用户离职/禁用 | 停止真实调用，提示联系管理员 |
| `PERMISSION_REV_CHANGED` | 403 | 权限版本变化 | 清本地 session，提示重新登录 |
| `MCP_SCOPE_DENIED` | 403 | tool/scope 无权 | 展示缺失 scope |
| `SHOP_SCOPE_DENIED` | 403 | shop_id 无权 | 展示缺失店铺 |
| `DEBUG_RUN_EXPIRED` | 401 | local_debug run_token 过期 | 自动用 30 天 CLI session 换新 run |
| `PACKAGE_CONFLICT` | 409 | base_commit 落后平台 HEAD | 提示先拉取/合并 |
| `PACKAGE_INVALID` | 422 | 包路径/hash/schema 不合法 | 展示校验失败项 |
| `OUTPUT_SCHEMA_INVALID` | 422 | output_schema 不合法 | 阻断提交 |
| `CATALOG_VERSION_UNSUPPORTED` | 426 | CLI 或 publisher skill 版本过低 | 提示升级 |
| `CATALOG_SIGNATURE_INVALID` | 409 | catalog/bundle 签名失败 | 禁用缓存并停止使用 |
| `RATE_LIMITED` | 429 | 调用过频 | 按 `retry_after_seconds` 退避 |
| `MCP_CALL_FAILED` | 502 | 平台侧 MCP 失败 | 展示脱敏错误和 proof id |

## 13. 审计与 Data Proof

每次 `/api/codex/mcp/call` 写审计：

```text
action = codex.mcp.call
target_type = mcp_tool
target_id = <tool_name>
detail = {
  user_id,
  skill_id,
  server,
  tool,
  run_mode: "local_debug",
  call_source: "codex_cli",
  platform,
  data_scope,
  shop_id,
  dry_run,
  denied_reason,
  proof_id
}
```

真实数据调用还应写入 collection proof / data proof，用于后续审核和回放。

## 14. 本地调试模式

命令分级：

```text
sf skill test --local
  只跑 fixture / 单元测试，不调真实 MCP。

sf skill test --real-mcp --path . --shop-id xxx
  本地执行 scripts/main.py，可调真实 MCP/API，run_mode=local_debug。
  真实调用经 SkillForge MCP Gateway 到平台侧执行，不依赖本机网络或 Cookie。

sf skill sandbox --path . --dataset xxx
  提交到 SkillForge 沙箱，由平台调用 OpenClaw/AIClaw 执行，记录正式 sandbox run。
```

`--real-mcp` 必须显式传入，不能作为默认行为。

调试链路：

```text
sf skill test --real-mcp
  -> sf auth introspect
  -> sf skill doctor
  -> POST /api/codex/skill/debug-runs
  -> CLI 注入 SKILLFORGE_RUN_TOKEN
  -> 本地运行 scripts/main.py
  -> SDK 调 /api/codex/mcp/call
  -> POST /api/codex/skill/debug-runs/{run_id}/complete
  -> 输出 reports / todos / performance 预览和 proof 摘要
```

调试输出规则：

- `local_debug` 允许读真实 MCP/API 数据，但所有平台输出能力都是 `preview_only`。
- `local_debug` 不创建正式待办，不推钉钉，不发邮件，不写正式 `/inbox`。
- `sandbox` 可以生成 sandbox artifact 和报告预览，但不影响生产发布态。
- `production` 才按审核通过的输出契约创建正式待办、报告和性能记录。
- CLI 必须展示 `run_id`、调用的 MCP tools、proof ids、输出 schema 校验结果、失败原因。
- 调试日志不得打印 `SKILLFORGE_CLI_TOKEN`、`SKILLFORGE_RUN_TOKEN`、Cookie、Authorization header。

推荐 CLI 输出：

```text
Debug run: debug_001
Mode: local_debug
MCP calls: 2 success, 0 denied
Proofs: proof_001, proof_002
Output validation:
  reports: valid, 1 preview
  todos: valid, 2 preview
  performance: valid
Result: success
```

## 15. 需要修改的代码区域

- `app/codex/`：新增 auth、mcp gateway、capability API。
- `app/codex/debug_runs.py`：新增 local_debug / sandbox 调试 run、事件流、结果上报和脱敏日志。
- `app/codex/editor_capabilities.py`：暴露 SkillStudio 结构化模块、文件契约、编辑 API 和发布门禁给 Codex skill。
- `app/codex/catalogs.py`：暴露版本化 catalog manifest / bundle，支持 CLI 和 Codex skill 自动刷新接口能力。
- `app/codex/submissions.py`：实现 package 上传、路径校验、hash 校验、base_commit 原子冲突检测和审核单创建。
- `app/codex/errors.py`：统一 `/api/codex/*` 错误码和脱敏响应。
- `app/coding_agent/mcp_config.py`：沉淀 MCP registry 元数据，不再只是全局 system_config。
- `app/coding_agent/session_service.py`：保留平台内部 MCP 注入，但与 codex gateway 复用 catalog/tool meta。
- `scripts/skillforge_mcp_server.py`：平台内置 `skillforge_internal` MCP 暴露 `skillforge_agent_coverage`、`skillforge_ai_analyze`、`skillforge_raw_data_query`、`skillforge_run_analyze`，供 Skill 编辑/创建 Agent 调用同一套服务端 Agent 覆盖度、AI、权限和脱敏逻辑。
- `app/skill_runtime_sdk/skillforge_sdk.py`：本地 token 存在时，`mcp://` 走 gateway。
- `app/auth/`：新增 CLI session token 校验依赖。
- `app/execution/` / `app/todos/` / `app/inbox/`：将 todos、reports、metrics、performance 能力暴露到 `/api/codex/platform-capabilities`。
- `migrations/versions/`：新增 CLI session 与 MCP call 审计必要表。
- `tests/`：补本地真实 MCP gateway 权限与吊销测试。

## 16. 验收标准

1. active 用户扫码后，本地 `sf mcp stdio` 能列出有权 MCP tools。
2. disabled 用户或 `permissions_rev` 变化后，旧 token 立即无法 introspect。
3. 本地 Codex tools/call 能拿到真实 MCP 数据，但响应里没有密钥、Cookie、Authorization header。
4. 跨部门无权限用户看不到对应 MCP tool，也无法直接 call。
5. `sf skill test --local` 不触发真实 MCP。
6. `sf skill test --real-mcp` 能真实调用并写 audit / proof。
7. 写操作 MCP 默认 dry_run，未授权真实写操作被拒绝。
8. 用户被禁用后，即使本地 MCP proxy 未重启，下一次 `/api/codex/mcp/call` 也失败。
9. Codex skill 能通过 `/api/codex/platform-capabilities` 看到待办、报告、性能指标、data proof 等平台能力。
10. 同一个 Skill 包在 local_debug、sandbox、production 中不改代码即可通过 SDK 自动走对应 token 和平台 MCP Gateway。
11. `POST /api/codex/skill/debug-runs` 能签发短期 local_debug run token，并把 MCP 调用、proof、输出预览关联到同一个 `run_id`。
12. local_debug 和 sandbox 不创建正式待办、不推正式消息、不写正式 `/inbox`；production 才按发布契约产生正式输出。
13. Codex skill 能通过 `/api/codex/skill-editor-capabilities` 看到 SkillStudio 当前模块、文件契约、编辑 API、锁语义和发布门禁。
14. 对已有 Skill 的本地提交，平台能返回远端 commit、编辑锁和审核状态，用于 Codex 在提交前做冲突判断。
15. 平台新增 MCP/API/输出能力后，`/api/codex/catalogs/manifest` 的 `etag/catalog_rev` 会变化；CLI 自动刷新 catalog 后 Codex skill 能看到新接口。
16. 自动更新不下发可执行代码；reference bundle 必须通过 hash 和签名校验后才能进入本地缓存。
17. 30 天 CLI session 有效期间，本地真实调试不要求重复扫码；CLI 自动换取短期 debug run token。
18. `scripts/main.py` 子进程环境里没有 30 天 CLI token，只能看到短期 `SKILLFORGE_RUN_TOKEN`。
19. submission 包 hash、路径白名单、secret scan、base_commit 冲突检测任一失败时拒绝写入 `skills-repo/`。
20. catalog/bundle 签名算法、`key_id` 和 hash 校验失败时 CLI fail closed。
21. 30 天 CLI session 保存在系统凭据库或 `0700/0600` fallback 文件中，`sf auth status`、日志、proof、submission 都不会输出 token 原文。
22. PKCE callback 只绑定 localhost、单次使用、10 分钟内关闭，并严格校验 `redirect_uri`、`state`、`nonce` 和 `code_verifier`。
23. `sf mcp stdio` 的真实 `tools/call` 必须绑定短期 run token、调用次数限制、timeout 和 proof 归属。
24. `debug-runs/{run_id}/complete` 必须带 `run_token` 认证，stdout/stderr tail 经过长度限制和脱敏。
25. submission 解包必须先进入隔离临时目录，全部 tar entry 校验、hash、secret scan、schema 和 scope 校验通过后才能写入 `skills-repo/`。
26. `local-status` 不接收、不回显、不落库开发者本机绝对路径。
