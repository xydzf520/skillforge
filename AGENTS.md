# Public preparation edition

This is an independent publication-preparation repository. Keep company data,
private deployment details, credentials and source repository history out of it.
Run `python scripts/check_public_distribution.py --staged` before committing.
Do not restore the excluded third-party runtime. The project uses Apache-2.0;
preserve third-party licenses and review ownership/publication scope in
`docs/public/RELEASE_BOUNDARY.md`. Historical references below do not authorize
publishing confidential material or installing optional runtimes.

# SkillForge AI 开发硬约束

这些规则用于约束 Codex / Claude / 其它 AI 编程助手。改代码前先按这里判断业务边界；如果需求与这里冲突，先问人，不要自行“简化架构”。

## 一句话边界

SkillForge 是控制面：负责 Skill 编辑、Git 版本、审核、配置下发、节点观测和兜底补偿。真正的 Skill 定时执行优先在运行节点 Bridge/OpenClaw/AIClaw 侧发生。

## sf / Codex / Claude 入口

- 用户提到 `sf`、`/sf`、SkillForge Codex 插件、MCP 能力、真实数据验证、我的插件、我的部门插件、提交审核时，先阅读 `docs/guides/sf-codex-claude-workflow.md`。
- Codex 里 `/sf ...` 应映射到本地 `sf` CLI；Claude Code 也按同一份路由表执行对应 shell 命令。
- 先跑 `sf update --check`、`sf auth status`、`sf mcp catalog`，确认插件版本、登录和 MCP 能力。
- 真实 MCP、钉钉推送、Skill 提交审核都必须走 SkillForge；不要读取平台密钥、Cookie、MCP env 或直接写 `skills-repo/`。

## Git 与仓库边界

- `skillforge` 主仓库只管理平台代码：`app/`、`web/`、`bridge/`、`migrations/`、`tests/`、`docs/` 等。
- `skills-repo/` 是 Skill 本地工作区和独立 Git 仓库，已被主仓库 `.gitignore` 排除。不要把 `skills-repo/` 加进主仓库，也不要删除这个 ignore。
- 本地 Docker/Gitea 是 Skill 仓库服务，用来记录平台上编辑、保存、审核、发布产生的 Skill 版本历史；它不是主项目 Git 的替代品。
- 测试、生产可以各自有不同 Skill 远端仓库或 mirror。不要把测试 Skill 历史和生产 Skill 历史混成一个主项目分支策略。

## Skill 保存、审核、发布

- 编辑器保存代码必须落到 `skills-repo/<skill_id>/`，并通过 Skill Git 记录 commit。
- 提交审核前必须保证当前工作区内容已进入 Skill Git；审核通过后运行配置引用明确的 Git commit/tag。
- 运行终端、节点定时页需要同时能看出平台当前 Skill Git 版本和节点已部署版本，不能只显示数据库旧字段。
- 不要绕过审核状态机直接改发布态；变更要保留审计、版本和回滚线索。

## Skill 图形化界面与个人 overlay

- Skill 默认图形化界面属于 Skill 展示 contract；修改默认界面必须走 Skill Git、验证、审核、发布链路。
- 用户个人图形化调整只允许保存为 `user_id + skill_id + surface` 维度的 UI overlay，只影响当前用户当前 Skill，不影响其他用户或其他 Skill。
- 用户自定义 AI 调整提示词只允许作为该 `user_id + skill_id + surface` 的个人默认提示词保存，不得变成全局默认或 Skill 默认。
- AI 只能基于当前用户对当前 Skill 已授权可见的底座信息调整界面：Skill 元数据、当前 commit、input/output contract、`param_ui_schema`、`result_ui_schema`、组件白名单、脱敏样例输出和当前个人 overlay。
- AI 输出只能是声明式 UI schema / JSON Patch / overlay；禁止生成或执行任意 Vue/JS/HTML/CSS，禁止 iframe/script/style/事件处理器或远程组件。
- AI key 只能由服务端统一 AI 封装从后台 `SystemConfig ai.*` 配置读取并调用模型；个人 UI AI 不得读取或写入 `.env`，不得把 key 返回前端、写入 overlay、run trace、执行参数或日志。
- 个人 overlay 不能新增数据源、MCP scope、隐藏权限校验、trigger、工作流、Skill 逻辑、必填校验、可见性、发布态或节点部署版本。
- 提交执行时必须记录 `ui_pref_id`、`ui_pref_version`、`base_skill_commit`、`merged_ui_schema_hash` 和实际 `params`，让 run trace 可还原用户当时看到的界面、默认值和最终提交参数。
- 详细规格见 `docs/spec/skill-personalized-ui-overlay.md`。

## 定时与执行

- 节点定时是主路径：`node_scheduler` 表示节点 Bridge 按下发 cron 触发。
- 平台中心调度只做管理和兜底：`scheduler:fallback` 是节点漏跑后的补偿，不是替代节点定时的主路径。
- 已下发且在线的节点首次到点也不能被平台中心抢跑。
- 停止定时的含义是：`trigger_type=manual`、`trigger_expression=None`、删除/清空对应 `NodeScheduleConfig`，并向节点下发空 schedule 快照。历史 `ExecutionRun` 保留，但不能让 UI 显示为正在运行。
- 定时页状态词：
  - `active` = `已启用`
  - `stopped` = `已停止`
  - `pending` = `下发中`
  - `sync_failed` = `下发失败`
  - `not_scheduled` = `未下发`
  - `运行中` 只用于真实执行中的 run，不用于定时配置。

## Bridge 与节点

- Bridge 应自动保持最新；UI 不提供人工“可更新”主按钮，只展示自动更新状态。
- 节点类型优先展示真实运行时/网关类型：`runtime_type`、`bridge_gateway_kind` 优先于历史 `agent_type`。
- 标准写法：`Linux`、`macOS`、`Windows`、`OpenClaw`、`AIClaw`、`Bridge`、`Gitea`、`Skill`。

## 修改这些区域前必须验证

- 权限系统、Skill 列表/详情/Hall/Portal 可见性：先阅读 `docs/spec/skill-permission-unification.md`，不得新增散落的角色判断或绕过统一 Skill 权限目标。
- 调度、节点、Bridge、同步链路：至少运行 `pytest tests/test_node_scheduling.py`。
- Skill Git、保存、审核链路：至少运行相关 `tests/test_*git*` / `tests/test_*review*`，没有现成测试时补一个聚焦测试。
- 前端状态文案或节点页：运行 `cd web && npm run typecheck`，涉及构建行为时再跑 `npm run build`。
- 每次改动后运行 `git diff --check`。

## 禁止事项

- 不要把“Skill 可用/已发布状态”当成“节点定时正在运行”。
- 不要为了方便把节点定时改回平台中心定时主导。
- 不要让主项目 Git 接管 `skills-repo/`。
- 不要删除历史执行记录来实现“停止定时”的显示效果。
- 不要在未确认业务含义时重命名状态、字段、触发类型或 Git remote 语义。
