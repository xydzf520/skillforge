# SkillForge AI 开发硬约束

日期：2026-06-01

状态：长期有效的 AI 编程助手协作规范。`AGENTS.md` 是执行入口，本文件是可检索、可链接的文档版。

## 1. 一句话边界

SkillForge 是控制面：负责 Skill 编辑、Git 版本、审核、配置下发、节点观测和兜底补偿。真正的 Skill 定时执行优先在运行节点 Bridge / OpenClaw / AIClaw 侧发生。

如果需求与本文冲突，先确认业务含义，不要为了方便自行简化架构。

## 2. 工作方式

- 默认直接执行：收集上下文、改代码、跑验证、给出结果。
- 不在开头输出大段计划或进度说明，除非用户明确要求。
- 只有在破坏性操作、真实部署、真实外部副作用、付费调用、缺少凭证或业务含义关键不明确时才停下来问。
- 改完代码或配置后必须运行最相关验证。
- 默认最终回复：改了什么、怎么验证、还有什么风险或阻塞。

## 3. sf / Codex / Claude 入口

用户提到 `sf`、`/sf`、SkillForge Codex 插件、MCP 能力、真实数据验证、我的插件、我的部门插件、提交审核时，先阅读：

- `docs/guides/sf-codex-claude-workflow.md`

执行边界：

1. Codex 里的 `/sf ...` 映射到本地 `sf` CLI。
2. 先确认插件版本、登录和 MCP 能力：

   ```bash
   sf update --check
   sf auth status
   sf mcp catalog
   ```

3. 真实 MCP、钉钉推送、Skill 提交审核都必须走 SkillForge。
4. 不读取平台密钥、Cookie、MCP env。
5. 不直接写 `skills-repo/` 绕过平台链路。

## 4. Git 与仓库边界

| 仓库 | 管理内容 | 禁止事项 |
| --- | --- | --- |
| `skillforge` 主仓库 | `app/`、`web/`、`bridge/`、`migrations/`、`tests/`、`docs/` 等平台代码 | 不接管 `skills-repo/` |
| `skills-repo/` | Skill 本地工作区和独立 Git 仓库 | 不加入主仓库、不删除 `.gitignore` 排除规则 |
| 本地 Docker / Gitea | Skill 仓库服务，记录平台编辑、保存、审核、发布产生的版本历史 | 不当作主项目 Git 替代品 |

测试、生产可以各自有不同 Skill 远端仓库或 mirror，不能把测试 Skill 历史和生产 Skill 历史混成一个主项目分支策略。

## 5. Skill 保存、审核、发布

- 编辑器保存代码必须落到 `skills-repo/<skill_id>/`，并通过 Skill Git 记录 commit。
- 提交审核前必须保证当前工作区内容已进入 Skill Git。
- 审核通过后运行配置引用明确的 Git commit 或 tag。
- 运行终端、节点定时页需要同时展示平台当前 Skill Git 版本和节点已部署版本。
- 不要绕过审核状态机直接改发布态；变更必须保留审计、版本和回滚线索。

## 6. Skill 图形化界面与个人 overlay

Skill 默认图形化界面属于 Skill 展示 contract。修改默认界面必须走 Skill Git、验证、审核、发布链路。

个人 overlay 规则：

- 只允许保存为 `user_id + skill_id + surface` 维度。
- 只影响当前用户当前 Skill，不影响其他用户或其他 Skill。
- 用户自定义 AI 调整提示词只允许作为该维度的个人默认提示词保存。
- AI 只能基于当前用户已授权可见的信息调整界面：Skill 元数据、当前 commit、input/output contract、`param_ui_schema`、`result_ui_schema`、组件白名单、脱敏样例输出和当前个人 overlay。
- AI 输出只能是声明式 UI schema / JSON Patch / overlay。
- 禁止生成或执行任意 Vue / JS / HTML / CSS，禁止 iframe / script / style / 事件处理器或远程组件。
- AI key 只能由服务端统一 AI 封装从后台 `SystemConfig ai.*` 配置读取并调用模型。
- 个人 UI AI 不得读取或写入 `.env`，不得把 key 返回前端、写入 overlay、run trace、执行参数或日志。
- 个人 overlay 不能新增数据源、MCP scope、隐藏权限校验、trigger、工作流、Skill 逻辑、必填校验、可见性、发布态或节点部署版本。

运行提交必须记录：

- `ui_pref_id`
- `ui_pref_version`
- `base_skill_commit`
- `merged_ui_schema_hash`
- 实际 `params`

详细规格见：

- `docs/spec/skill-personalized-ui-overlay.md`

## 7. 定时与执行

节点定时是主路径：`node_scheduler` 表示节点 Bridge 按下发 cron 触发。

平台中心调度只做管理和兜底：`scheduler:fallback` 是节点漏跑后的补偿，不是替代节点定时的主路径。

已下发且在线的节点首次到点也不能被平台中心抢跑。

停止定时的含义：

- `trigger_type = manual`
- `trigger_expression = None`
- 删除或清空对应 `NodeScheduleConfig`
- 向节点下发空 schedule 快照
- 历史 `ExecutionRun` 保留

UI 不得因为历史 `ExecutionRun` 还存在就显示定时仍在运行。

状态词：

| 内部值 | UI 文案 | 说明 |
| --- | --- | --- |
| `active` | 已启用 | 当前定时配置有效 |
| `stopped` | 已停止 | 当前无 cron、无节点配置 |
| `pending` | 下发中 | 已生成配置但节点未确认 |
| `sync_failed` | 下发失败 | 推送或确认失败 |
| `not_scheduled` | 未下发 | Skill 仍是 cron 但没有节点配置 |
| `running` | 运行中 | 只用于真实执行中的 run，不用于定时配置 |

## 8. Bridge 与节点

- Bridge 应自动保持最新；UI 不提供人工“可更新”主按钮，只展示自动更新状态。
- 节点类型优先展示真实运行时 / 网关类型：`runtime_type`、`bridge_gateway_kind`。
- 不优先使用历史兼容字段 `agent_type`。
- 标准写法：`Linux`、`macOS`、`Windows`、`OpenClaw`、`AIClaw`、`Bridge`、`Gitea`、`Skill`。

## 9. 修改前必须阅读和验证的区域

| 修改区域 | 必读文档 / 验证 |
| --- | --- |
| 权限系统、Skill 列表 / 详情 / Hall / Portal 可见性 | 先读 `docs/spec/skill-permission-unification.md`；不得新增散落角色判断或绕过统一 Skill 权限目标 |
| 调度、节点、Bridge、同步链路 | 至少运行 `pytest tests/test_node_scheduling.py` |
| Skill Git、保存、审核链路 | 至少运行相关 `tests/test_*git*` / `tests/test_*review*`；没有现成测试时补聚焦测试 |
| 前端状态文案或节点页 | 运行 `cd web && npm run typecheck`；涉及构建行为时再跑 `npm run build` |
| 每次改动 | 运行 `git diff --check` |

## 10. 禁止事项

- 不要把“Skill 可用 / 已发布状态”当成“节点定时正在运行”。
- 不要为了方便把节点定时改回平台中心定时主导。
- 不要让主项目 Git 接管 `skills-repo/`。
- 不要删除历史执行记录来实现“停止定时”的显示效果。
- 不要在未确认业务含义时重命名状态、字段、触发类型或 Git remote 语义。
- 不要把 API Key、Cookie、Token、平台密钥或客户原始敏感数据写入知识库、overlay、run trace、执行参数或日志。

## 11. 与智能闭环文档的关系

智能闭环、部门知识库、Agent、训练、SF、Skill 串联相关设计见：

- `docs/spec/intelligence-positive-loop-prd.md`
- `docs/spec/intelligence-learning-loop.md`
- `docs/architecture/intelligence-data-flow-positive-loop.md`
- `docs/spec/department-knowledge-base.md`

这些闭环能力必须遵守本文约束：自动生成知识、样本和候选可以做；Skill 发布、模型部署、SF 能力上线和真实外部副作用必须保留审核、灰度、审计和回滚。
