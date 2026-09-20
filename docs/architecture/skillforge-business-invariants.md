# SkillForge 业务不变量

> 目的：把最容易被 AI 误改的业务边界压缩成一页。这里不是方案集，而是必须长期成立的判断标准。

## 1. 控制面和运行面

SkillForge 平台是控制面，负责编辑、审核、版本、配置、下发、观测和补偿。运行节点是运行面，Bridge/OpenClaw/AIClaw 负责真正执行 Skill。

定时任务的主路径是节点定时：

```text
平台保存 cron -> 审核/发布 -> 生成运行配置 -> 下发到节点
节点 Bridge 收到 schedule -> 到点触发 node_scheduler -> 回传 execution_runs
平台 watchdog 发现漏跑 -> scheduler:fallback 远程补触发或按策略中心兜底
```

中心调度不能抢已经下发且在线的节点任务。`scheduler:fallback` 只能表示补偿路径，不能被当作主调度路径。

## 2. 停止定时的语义

停止一个节点定时任务时，正确结果是：

- `skills.trigger_type = manual`
- `skills.trigger_expression = NULL`
- 对应 `NodeScheduleConfig` 删除或清空
- 向节点下发空 schedule 快照
- 历史 `ExecutionRun` 保留，用于统计和排障

因此 UI 不能因为历史 run 还存在就显示“运行中”。“运行中”只描述某次执行 run 的状态，不描述定时配置。

## 3. Git 边界

主项目 Git 和 Skill Git 是两条线：

```text
skillforge 主仓库:
  管 app/ web/ bridge/ migrations/ tests/ docs/

skills-repo 独立仓库:
  管 skills-repo/<skill_id>/ 的 Skill 文件、模板、发布包历史

本地 Docker/Gitea:
  是 Skill Git 服务，平台保存/审核/发布都写这里
```

`skills-repo/` 必须继续被主项目 `.gitignore` 排除。测试环境、生产环境可以各自配置不同的 Skill Gitea 远端或 mirror，不能把 Skill 历史塞进主项目 Git 来解决备份问题。

## 4. 版本展示

凡是涉及运行、节点、定时的页面，都要区分：

- 平台当前 Skill Git HEAD
- 审核/发布引用的 Git commit 或 tag
- 节点实际部署的 Git commit
- 最近执行 run 的状态和来源

不能只读数据库旧字段显示版本；版本来源不确定时要显示“节点版本未知”，不能伪造一致。

## 5. 状态词

定时配置状态：

| 内部值 | UI 文案 | 含义 |
|---|---|---|
| `active` | 已启用 | 节点配置已确认或已有节点定时执行 |
| `stopped` | 已停止 | 当前无 cron、无节点配置 |
| `pending` | 下发中 | 已生成配置但节点未确认 |
| `sync_failed` | 下发失败 | 推送或确认失败 |
| `not_scheduled` | 未下发 | Skill 仍是 cron 但没有节点配置 |
| `archived` | 已归档 | Skill 已归档 |
| `deleted` | 已删除 | Skill 已删除但保留历史 |

执行 run 状态：

| 内部值 | UI 文案 |
|---|---|
| `running` | 运行中 |
| `completed` | 成功 |
| `failed` | 失败 |
| `timeout` | 超时 |

## 6. 标准命名

产品和技术名按以下写法展示：

- `Linux`
- `macOS`
- `Windows`
- `OpenClaw`
- `AIClaw`
- `Bridge`
- `Gitea`
- `Skill`

节点类型展示优先使用真实运行时字段：`runtime_type`、`bridge_gateway_kind`，不要优先拿历史兼容字段 `agent_type`。

## 7. Agent 用途边界

`openclaw_instances.agent_purpose` 是运行边界，不只是 UI 标签：

- `skill_runtime` / `mixed` 才能承接 Skill 文件同步、节点定时快照和 `bridge_script` / `openclaw_agent` 执行。
- `analysis` 只承接 `/api/intelligence/analyze` 的受控分析委托。
- `training` 只承接 `/api/training/*` 的训练任务下发、取消、补偿和 artifact 下载。

把 Agent 用途从 `skill_runtime` 改成 `analysis` 或 `training` 时，平台必须向该节点下发空 schedule 快照，避免历史 Skill 定时继续在专用 Agent 上触发。

训练任务路由必须同时满足能力与用途：只有 `training` / `mixed` Agent 的训练能力才可进入训练任务池路由；普通执行 Agent 即使误上报 GPU 或 `training.submit_job`，也只能作为资源观测线索，不能被创建、审批或下发选中。

部门 Agent 覆盖度是观测与规划入口，不是权限放大入口：`/api/aiclaw/departments/agent-coverage`、`sf agent coverage` 和 `skillforge_agent_coverage` MCP 只能基于 `openclaw_instances`、Bridge 在线状态和脱敏能力摘要统计执行/分析/训练 Agent 是否齐备；普通部门账号只看本部门覆盖度，平台默认兜底 Agent 只以兜底摘要出现，不能暴露密钥、脚本、跨部门实例控制权或绕过后续路由权限。

平台内置 `skillforge_internal` MCP 可以暴露同一个 `skillforge_agent_coverage` 只读工具，供 Skill 编辑/创建 Agent 在生成或改造方案前判断部门是否缺执行、分析或训练 Agent。该工具仍以 `SKILLFORGE_USER_ID` 映射真实 active 用户并复用可见部门范围；不得因为处在编辑期就返回跨部门 Agent 控制信息、gateway URL、auth token 或本地脚本路径。

模型对话入口不能信任前端 URL 或 WebSocket payload 里的 `model_context`。后端必须按 `model_deployments` / `training_jobs` 权威记录重新解析 deployment、job、artifact 和模型名称，只允许当前用户可访问部门内且状态为 `canary` / `active` 的部署进入 Agent 对话；跨部门、未审批部署、job/deployment 不匹配或 artifact 不匹配必须拒绝。

## 8. LLM 调用边界

Skill 运行期不能直接调用外部 LLM 服务。所有运行期 LLM 调用必须经过平台 `app/intelligence/` 入口，由平台负责 run token 鉴权、预算、cache、evidence 校验和降级；如果执行 Agent 上报 `intelligence.analyze`，平台可以把分析负载通过 Bridge 下发给该部门 Agent 执行，并保留平台 trace/cache。

采集 MCP 和 LLM analyze 是两条边界：MCP 只收口采集类工具；运行期 LLM 入口使用 `sf.analyze()` / `/api/intelligence/analyze`，不要在 Skill 运行链路里再包一层 AI analyze MCP tool。Agent 侧分析也只能作为该入口的受控执行后端，不能让 Skill 代码直接拿模型密钥。

例外边界：`skillforge_ai_analyze` 是 Codex/sf 调试入口，也可作为 `skillforge_internal` MCP 的编辑期辅助分析工具，但不是 Skill 运行期入口。它只能由已登录 CLI 经 SkillForge Gateway 调用，或由平台内置 MCP 以 `SKILLFORGE_USER_ID` 映射到真实 active 用户后调用；使用服务端后台 `ai.*` 配置的 `deepseek-v4-pro` 1M 上下文模型，做调试上下文、运行结果和原始数据的一次性分析；不得把模型 key 下发到本地、Skill 包、run trace 或 MCP env。

`skillforge_raw_data_query` 也是 Codex/sf 和 `skillforge_internal` MCP 的调试入口，用于按权限查看 Skill 运行后的 `execution_runs`、`execution_steps`、`decision_log`、`collection_proofs`、`platform_api_schema_snapshots`。普通账号必须通过 `skill_id`、`run_id` 或 `proof_id` 映射到可读 Skill；返回内容要统一脱敏 token、cookie、authorization、secret 等字段，不能作为绕过业务系统授权的原始采集通道。

`skillforge_run_analyze` 是 Codex/sf 和 `skillforge_internal` MCP 的一键运行复盘入口，只能组合上述两类调试能力：先按 Skill 读取权限收集并脱敏某个 `execution_run_id` 的运行数据，再调用平台 AI 生成诊断。它不能成为生产 Skill 运行期 LLM 入口，不能扩大原始数据可见范围，也不能把未脱敏数据或模型凭证返回给本地。

Run Trace 页面的“AI 复盘”按钮与 `POST /api/admin|executions/runs/{run_id}/trace/analyze` 只能复用同一条复盘能力：先通过 Run Trace 可见性校验，再按 Skill 读权限和脱敏规则组装上下文，最后由服务端后台 AI 配置调用模型。前端不得直接拿模型 key，也不得请求未脱敏原始数据。

运行复盘可以生成训练候选，但只能进入 `training_jobs.awaiting_review`：`POST /api/training/runs/{run_id}/candidate` 和 `sf run candidate` 必须要求目标 Skill 编辑权限与训练任务创建权限，只保存脱敏后的计数、lineage、AI 复盘摘要和 prompt hash，不保存原始 input/output/cookie/token；sandbox/sample 运行默认不能作为训练候选来源，不能绕过训练审批、评估、部署审批和回滚链路。

Skill 编辑期的辅助分析走 `app/skills/intelligence/`，二者可以共享 `app/common/ai.py` 底层客户端，但不能让 Skill 仓库里的 `main.py` 直接 `import anthropic / openai / requests` 调模型接口。

## 9. Inbox 报告可见性

`output.reports[i].recipients` 是未来主动推送声明字段，不是 `/inbox` ACL。报告列表、详情、未读数、筛选和缓存失效必须按 Skill 权限、组织归属、visibility、`SkillMember` 等统一权限规则判断可见性。

当前 report-v1 schema 仍要求 `recipients` 必填；它的存在只表示"如果未来接入主动推送，可以参考此声明"，不能被路由或前端当成谁能看见报告的依据。

## 10. MCP 运行时归属

生产运行时的业务 MCP 归平台所有，不归单个 Skill 所有：

```text
Skill main.py
  -> sf.fetch_api("mcp://tmall_xxx")
  -> 平台共享 SkillForge SDK
  -> 平台共享 MCP
  -> collection_service
  -> cookie pool / slot / rate / proof / warning
```

长期边界：

- 平台共享 MCP 是生产主实现；除平台尚未提供的能力外，Skill 仓库禁止内置 `scripts/*_mcp_server.py`、`scripts/collection_client.py` 或读取 cookie 的采集实现。
- `scripts/skillforge_sdk.py` 只允许由平台受信任 SDK 注入或同步覆盖；Skill 作者不得复制旧 SDK、自行实现 `fetch_api` 或绕过平台 MCP Gateway。
- 新增业务采集能力时，先补平台 MCP/collection scope，并用真实原始 JSON、MCP audit 和 collection proofs 验证通过，再在 Skill 中通过 `sf.fetch_api("mcp://tool_name")` 消费。不能为了当前 Skill 快速落地而把私有 MCP server 放进 Skill Git。
- 生产环境命中 Skill 内置 MCP 时，必须在 run trace / `_skillforge_meta` 写 `mcp_runtime_source=skill_bundled`、runtime hash 和 `MCP_RUNTIME_DRIFT` 告警，不能静默继续。
- MCP 不能自己读取 Chrome cookie 后直接请求业务域名；所有业务采集必须进入 `collection_service`，由平台负责授权 cookie 选择、slot 隔离、限速、proof 和 warning 分类。
- `mcp://tmall_*` 等工具必须有平台维护的 tool scope 映射，至少能得到 `platform/shop_id/data_scope/endpoint_family/warning_group`；无法归类时不能按普通 URL 静默抓取。
- Skill Git 仍管理 Skill 业务代码、prompt、fixtures 和声明的 MCP tool 列表；平台 runtime 注入或覆盖共享 SDK/MCP 不应改写 Skill Git 历史。

AI 编程助手改 Skill 时必须按这个顺序判断：

1. `SKILL.md runtime.tools` 已声明 `mcp://...` 且平台有实现：只改 `scripts/main.py` 调 `SkillForge.fetch_api("mcp://...")`，不要创建本地 MCP 文件。
2. 平台无实现但业务需要新增采集：先在主仓库 `scripts/*_mcp_server.py` 和 `collection_service` 增加平台能力、验证 raw JSON 正常，再更新 Skill 声明和业务逻辑。
3. 平台 MCP 返回空、HTTP 200 但业务 `ok=false`、cookie 缺失或 collection proof 失败：输出待补采/数据源失败，禁止用其他汇总口径、0 值或旧 fixture 替代真实商品级数据。
