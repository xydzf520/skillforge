# SkillForge 智能闭环规格：知识库 / Agent / 训练 / SF / Skill 正向循环

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


日期：2026-06-01

状态：研发规格与落地拆分文档；需求入口见 `docs/spec/intelligence-positive-loop-prd.md`。

## 1. 一句话目标

把 Skill 运行结果、平台回推数据、用户反馈、SF 调用、Agent 会话和训练结果统一纳入一条可治理、可追踪、可观察的智能数据流，让部门知识库自动更新、Agent 自动吸收经验、训练样本自动积累、Skill 和 SF 自动产生迭代候选，最终形成持续提升业务效果的正向循环。

核心链路：

```text
Skill / Agent / SF / 用户反馈 / 外部回推
  -> 标准学习事件
  -> 脱敏、去重、分类、质量评分、权限治理
  -> 部门知识库 / 训练样本 / Agent 记忆 / Skill 优化候选 / SF 迭代候选
  -> 审核、训练、部署、发布
  -> 再次运行与调用
  -> 新反馈、新知识、新样本、新候选
```

## 2. 设计原则

1. **统一事件层，不做模块间硬连**
   - 不让知识库、Agent、训练、SF、Skill 两两直接耦合。
   - 所有跨模块数据流先写入统一学习事件和血缘边，再由策略决定下游落点。

2. **SkillForge 仍是控制面**
   - SkillForge 负责编辑、Git 版本、审核、配置下发、节点观测、兜底补偿和闭环观测。
   - 真正的 Skill 定时执行仍优先在 Bridge / OpenClaw / AIClaw 节点侧发生。

3. **自动生成候选，不自动越权发布**
   - 自动入库知识、自动创建训练候选、自动生成 Skill / SF 优化建议可以做。
   - 但 Skill 代码、默认界面、发布态、模型部署、生产策略变更必须保留审核、灰度和回滚。

4. **部门边界和证据优先**
   - 每条知识、样本、模型、建议都必须有来源、证据、部门 / org scope、脱敏状态和血缘。
   - 不把 Cookie、Token、API Key、平台密钥、原始敏感数据写入知识库、overlay、run trace 或日志。

5. **先可观察，再自动化**
   - 第一优先级不是“自动训练”或“自动改 Skill”，而是让数据怎么流、在哪里卡住、哪些数据被用过可以看见。

## 3. 当前项目基础与缺口

### 3.1 已有基础

| 能力 | 当前基础 | 代码位置 |
| --- | --- | --- |
| Skill 执行记录 | `ExecutionRun` 记录运行，`DecisionLog` 记录输入、输出、审批、反馈、评分、业务影响 | `app/execution/models.py` |
| 报告与待办 | Skill 输出 `reports` / `todos` 后进入 Inbox、待办和派发任务 | `app/inbox/service.py`、`app/todos/models.py` |
| 知识库 | 部门知识库、LightRAG 兼容检索、图片资产、查询日志、索引任务 | `app/knowledge/models.py`、`app/knowledge/service.py` |
| 训练 | 训练任务、训练网关、训练候选、模型部署、部署审批和回滚 | `app/training/models.py`、`app/training/service.py` |
| SF 调用观测 | MCP 调用审计、Debug Run、SF Dashboard、SF Trace | `app/codex/models.py`、`app/sf/service.py` |
| Agent 运行 | LangGraph Agent Runtime、stream/resume、用户线程隔离 | `app/agent_core/router.py`、`app/agent_core/runtime.py` |
| 运行追溯 | Run Trace 聚合 collection proof、LLM analyze、usage、decision log | `app/execution/run_trace_router.py` |
| 优化器 | 可从 DecisionLog 构建 BenchmarkPack，生成 Skill 优化候选 | `app/optimizer/` |

### 3.2 主要缺口

1. 知识库当前主要自动同步 Skill / DataSource 元数据，未系统接入执行报告、待办完成结果、SF 调用结果、Agent 会话和训练产物。
2. 训练候选已可从 `DecisionLog` 和单次 Run Trace 创建，但缺少统一样本池、质量评分和跨模块血缘。
3. Agent 有运行态，但缺少长期部门记忆、策略版本和“用了哪些知识 / 产生了哪些反馈”的持久观测。
4. SF 页面能看调用和报告，但不能自动识别高频能力缺口、失败模式、重复手工流程并形成迭代候选。
5. TaskTree、Run Trace、SF Trace、Inbox、Knowledge、Training 都有局部观测，但没有一张跨页面的数据流图。
6. 缺少统一策略决定：什么数据自动进知识库、什么只做训练样本、什么需要审核、什么必须忽略。

## 4. 目标架构

新增一个统一领域模块：

```text
app/learning/
  models.py      # 学习事件、学习资产、血缘边、入库任务、改进候选
  service.py     # 事件捕获、归一化、策略判断、物化到下游
  router.py      # 智能闭环 API
  policy.py      # 入库/训练/候选策略
  lineage.py     # 血缘写入和查询
  extractors.py  # report/todo/run/sf/agent/training 抽取器
```

前端新增页面：

```text
web/src/pages/learning/LearningFlow.vue
```

建议路由：

```text
/learning-flow    顶部一级导航：智能闭环 / 数据流
```

也可以先作为 SF 或管理后台二级页灰度，但目标态应该是横跨知识库、Agent、训练、SF、Skill 的独立观测面。

## 5. 核心数据模型

### 5.1 `learning_events`

统一记录所有可学习事件，append-only。

建议字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 事件 ID |
| `event_type` | `skill.run.completed`、`decision.feedback`、`sf.mcp.called` 等 |
| `source_type` | `execution_run`、`decision_log`、`todo`、`sf_audit`、`agent_thread`、`training_job` |
| `source_id` | 来源对象 ID |
| `source_hash` | 去重 hash |
| `department` | 部门 |
| `org_unit_id` | 组织单元 |
| `skill_id` | 关联 Skill |
| `user_id` | 触发用户 |
| `run_id` | 关联运行 |
| `modality` | `text`、`image`、`table`、`json`、`mixed` |
| `payload_ref` | 原始数据引用，不直接塞大内容 |
| `redacted_summary` | 脱敏摘要 |
| `sensitivity_level` | `public`、`internal`、`restricted`、`secret` |
| `status` | `captured`、`classified`、`materialized`、`ignored`、`failed` |
| `policy_result_json` | 策略结果 |
| `created_at` | 创建时间 |

### 5.2 `learning_artifacts`

事件抽取后的标准资产。

| `artifact_kind` | 用途 |
| --- | --- |
| `knowledge_note` | 可进入知识库的知识片段 |
| `report_summary` | 报告摘要 |
| `qa_pair` | 问答对 |
| `training_sample` | SFT / 偏好 / 评估样本 |
| `eval_case` | 固定评测样本 |
| `agent_memory` | Agent 部门记忆 |
| `agent_policy_candidate` | Agent 策略优化候选 |
| `agent_creation_candidate` | AI 判断某个高频/失败/缺口流程应沉淀为可审核 Agent 候选，并携带影响面 |
| `ai_system_gap_candidate` | AI 自迭代缺口候选，例如缺 AI 配置、候选缺草稿、采纳缺实施交接、物化失败 |
| `skill_improvement_candidate` | Skill 优化候选 |
| `sf_iteration_candidate` | SF 能力迭代候选 |
| `knowledge_review_candidate` | 需要人工确认的知识入库候选 |
| `training_improvement_candidate` | 训练任务、评估门禁、模型部署驳回或回滚后的训练治理候选 |

关键字段：

- `event_id`
- `artifact_kind`
- `target_type`
- `target_id`
- `quality_score`
- `confidence`
- `labels_json`
- `content_json`
- `status`
- `review_status`

### 5.3 `learning_flow_edges`

统一血缘边，支持跨页面画数据流图。

示例关系：

| relation | 示例 |
| --- | --- |
| `produced` | Skill Run produced Report |
| `indexed_as` | Report indexed_as KnowledgeDocument |
| `used_as_context` | KnowledgeDocument used_as_context Agent Thread |
| `labeled_by` | DecisionLog labeled_by User Feedback |
| `trained_from` | TrainingJob trained_from training_sample |
| `deployed_to` | ModelDeployment deployed_to Skill |
| `used_as_model` | ModelDeployment used_as_model Agent Thread |
| `rollback_target` | Active/RolledBack ModelDeployment rollback_target previous ModelDeployment |
| `proposed_change` | Failure proposed_change Skill Candidate |
| `reviewed_by` | Candidate reviewed_by Review / Todo |
| `drafted_as` | Agent 候选 drafted_as Workbench 治理草稿 |
| `improved` | Deployment improved Skill Success Rate |

建议字段：

- `from_type`
- `from_id`
- `to_type`
- `to_id`
- `relation`
- `department`
- `skill_id`
- `weight`
- `metadata_json`
- `created_at`

### 5.4 `learning_ingestion_jobs`

把事件 / 资产物化到知识库、训练样本、Agent 记忆等下游的任务轨迹。

| 字段 | 说明 |
| --- | --- |
| `event_id` | 来源事件 |
| `artifact_id` | 来源资产 |
| `sink_type` | `knowledge`、`training`、`agent_memory`、`skill_candidate`、`sf_candidate` |
| `sink_id` | 下游对象 ID |
| `status` | `pending`、`running`、`completed`、`failed`、`needs_review` |
| `attempts` | 重试次数 |
| `error` | 错误 |
| `policy_json` | 入库策略 |

### 5.5 `improvement_candidates`

跨 Agent / Skill / SF / 知识库 / 训练的改进候选池。

| 字段 | 说明 |
| --- | --- |
| `target_type` | `skill`、`agent`、`sf`、`knowledge`、`training` |
| `target_id` | 目标对象 |
| `title` | 标题 |
| `proposal` | 改进建议 |
| `evidence_event_ids_json` | 证据事件 |
| `risk_level` | 风险等级 |
| `expected_impact_json` | 预期收益 |
| `status` | `open`、`reviewing`、`accepted`、`rejected`、`implemented` |
| `review_id` | 审核 ID |
| `todo_id` | 待办 ID |
| `git_commit` | 若最终产生 Skill Git 变更，记录 commit |

## 6. 标准事件类型

### 6.1 输入事件

| 事件类型 | 来源 | 说明 |
| --- | --- | --- |
| `skill.run.completed` | `ExecutionRun` | Skill 正常完成 |
| `skill.run.failed` | `ExecutionRun` | Skill 失败、超时、异常 |
| `decision.created` | `DecisionLog` | 执行产生决策结果 |
| `decision.feedback` | `DecisionLog` | 用户评分、驳回、采纳、业务影响 |
| `inbox.report.created` | Inbox | 报告生成 |
| `decision.feedback` | `AITodo` | 审批待办通过 / 驳回形成个人反馈信号 |
| `todo.completed` | `TodoDispatchTask` | 派发任务完成 / 回执 |
| `sf.mcp.called` | `CodexMcpCallAudit` | SF MCP 调用 |
| `sf.report.generated` | SF / Codex Debug Run | SF 形成报告 |
| `agent.thread.completed` | Agent Runtime | Agent 会话完成 |
| `knowledge.query.used` | `KnowledgeQueryLog` | 知识被检索 / 用作上下文 |
| `training.job.completed` | `TrainingJob` | 训练完成 |
| `model.deployed` | `TrainingModelDeployment` | 模型灰度 / 激活 |
| `skill.review.approved` | Review / Skill Git | Skill 变更通过审核 |

### 6.2 输出事件 / 下游结果

| 事件类型 | 说明 |
| --- | --- |
| `knowledge.document.upserted` | 自动或审核后写入知识库 |
| `training.sample.created` | 生成训练样本 |
| `eval.case.created` | 生成评测样本 |
| `agent.memory.created` | 生成 Agent 记忆 |
| `agent.policy.proposed` | 生成 Agent 策略候选 |
| `skill.improvement.proposed` | 生成 Skill 改进候选 |
| `sf.iteration.proposed` | 生成 SF 能力迭代候选 |
| `training.job.proposed` | 自动生成训练候选任务 |

## 7. 各模块串联规则

### 7.1 Skill -> 知识库

来源：

- `ExecutionRun.summary`
- `DecisionLog.output_result.reports`
- `DecisionLog.output_result.todos`
- 成功完成的 `TodoDispatchTask.ack_note`
- 用户确认过的 `user_feedback`

入库规则：

| 数据 | 默认动作 |
| --- | --- |
| 报告摘要、结论、指标解释 | 自动入库 |
| 用户确认完成的任务复盘 | 自动入库 |
| 原始业务明细、客户对话、订单级数据 | 待审核或只做引用 |
| 失败堆栈、traceback | 不入知识库，只做改进候选 |
| sandbox / dry-run 输出 | 默认忽略 |

知识库文档来源类型建议新增：

```text
execution / report / todo / feedback / sf / agent / training
```

### 7.2 Skill / 用户反馈 -> 训练

来源：

- `DecisionLog.input_snapshot`
- `DecisionLog.output_result`
- `DecisionLog.user_action`
- `DecisionLog.rating`
- `DecisionLog.reject_reason`
- `DecisionLog.business_impact`
- 派发任务完成结果
- Run Trace AI 复盘

样本类型：

| 样本 | 生成条件 |
| --- | --- |
| SFT 样本 | 输入和目标输出完整，质量分达标 |
| 偏好样本 | 同一任务有采纳 / 驳回 / 评分 |
| 动作结果样本 | 有建议动作和真实执行结果 |
| 评测样本 | 用户明确纠正、失败案例、低评分案例 |

训练任务策略：

- 达到样本阈值后可以自动创建 `awaiting_review` 训练候选。
- 训练执行必须走训练 Agent / Bridge / Gateway。
- 模型部署必须走 `TrainingModelDeployment` 审批、灰度、激活、回滚链路。

### 7.3 知识库 -> Agent / Skill

已落地补充：知识检索日志中的 `document_ids` / `sources` 会统一写入 `KnowledgeDocument -> KnowledgeQuery` 的 `used_as_context` 血缘；0 命中检索会自动生成 `knowledge_review_candidate`，进入知识缺口治理候选，不返回原始 query payload 之外的敏感内容。非 Skill 候选无法落到 `DecisionRequest.skill_id` 时，会进入 `learning_<target_type>_review` 治理队列并写 `reviewed_by` 血缘，避免页面治理动作失败。

来源：

- `POST /api/knowledge/context`
- `KnowledgeQueryLog`

目标：

- Agent 回答时注入部门知识库上下文。
- Skill 创建 / 编辑 / 运行复盘时引用部门 SOP、历史报告和失败案例。
- 每次引用都写 `learning_flow_edges.used_as_context`，保证回答可以追溯来源。

要求：

- 回答必须带引用。
- 低置信度或依据不足时拒答或要求补充数据。
- Agent 不得读取超出当前用户 / 部门权限的知识。

### 7.4 Agent -> 知识库 / 训练 / Skill

已落地补充：`agent_core` 的 run/resume/stream 会把 Agent 会话写入 `learning_events(agent.thread.*)`；完成态会生成 `agent_memory`，失败态会生成 `agent_policy_candidate`。当 Agent state 中包含 `document_ids` / `sources` / `knowledge_context` 时，系统写入 `KnowledgeDocument -> AgentThread` 的 `used_as_context` 血缘；包含模型部署、工具调用、关联 Skill 时，分别写入 `used_as_model`、`called_tool`、`used_skill` 血缘。若 Agent 已生成任务合同或 Skill 草案，仅沉淀为 `skill_improvement_candidate` / 改进候选，后续仍必须进入 Skill Git / Review / 发布链路，不能自动发布。

已落地补充：学习流会自动对高频 SF 调用、审批驳回、知识 0 命中、运行/Agent 失败和任务完成回执做“是否应 Agent 化”判断。生产环境优先调用后台 `SystemConfig ai.*` 配置的 AI，未配置时使用保守规则兜底；判断结果会生成 `agent_creation_candidate` 和 `ImprovementCandidate(target_type=agent)`，携带 `affected_modules`、最小权限、触发条件、工作流、风险和预期影响，并自动进入 `learning_agent_review` 治理队列。系统还会为候选生成只读、无外部副作用的 Workbench Agent 治理草稿，写入 `drafted_as` 血缘，供人工评审后再进入 Skill Git / Agent 发布链路，形成“自动发现 -> 自动添加候选 -> 自动生成治理草稿 -> 自动进入治理循环”。系统不自动发布 Agent、不绕过审核、不执行真实外部副作用。

已落地补充：候选被采纳、驳回或落地后会反向写入 `learning_events(source_type=improvement_candidate)`，并生成 `training_sample` / `eval_case` 作为候选生成器、Agent 化判断和排序策略的监督样本。采纳 / 落地会额外生成可入库的 `knowledge_note` 正样本；驳回会生成 `agent_policy_candidate`，要求 AI 判断器学习拒绝原因、降低同类误判或补齐证据，形成“评审结果 -> 学习样本 -> 策略修正候选 -> 再治理”的二次循环。

已落地补充：Agent 候选被采纳或落地后，会自动写入 `learning_agent_implementation` 实施治理队列，并生成 `ready_for_implementation` 血缘（候选 -> 队列、治理草稿 -> 队列）。这一步只做交接和可追踪，不自动创建生产 Agent、不发布 Skill、不下发节点，确保“自动推进下一步”和“必须人工实施 / 审核 / 发布”同时成立。

已落地补充：学习流每次自动运行都会执行 AI 自迭代缺口审计，也可通过 `/api/learning/self-audit/run` 手动触发。审计会检查平台 AI 配置是否缺失、Agent 候选是否缺治理草稿、已采纳 Agent 候选是否缺实施队列、候选驳回是否偏多、学习资产物化是否失败、治理任务是否超过 SLA 仍停留在 `pending` / `in_progress`、治理任务终态是否缺少 `accepted/rejected/implemented` 候选决策反馈、自动循环运行是否失败或缺少近期成功运行；能安全补救的控制面动作会自动补齐，无法自动补救的会生成 `ai_system_gap_candidate` 并进入 `learning_ai_system_review` 治理队列。若后台 `SystemConfig ai.*` 已配置，审计还会把脱敏摘要交给 AI 生成额外系统缺口建议（如成本闸门、覆盖率、评测、权限或可观测性缺口），AI 建议同样只作为治理候选，不自动发布或执行外部副作用。

已落地补充：自动生成的 Agent 治理草稿会做静态校验并写入 `agent_draft.validation.passed/failed` 事件和 `validated_by` 血缘。校验只读不执行生成代码，检查草稿是否仍是 `draft_only`、是否包含 `no_auto_publish` 防护、是否保留候选血缘、是否缺必要文件、是否出现网络/子进程/钉钉等外部副作用关键词；校验失败会生成 `ai_system_gap_candidate`，阻止不合规草稿继续进入实施链路。Agent 候选即使被人工采纳，如果草稿未通过校验，也只会记录 `blocked_by_agent_draft_validation`，不会进入 `learning_agent_implementation`。

已落地补充：非 Skill 候选（Agent、知识、SF、AI 系统缺口等）不再只是写 `external_ref.governance_queue`，还会创建 `learning_governance_tasks` 治理任务，保存队列、候选、负责人、状态、优先级和脱敏 payload，并写入 `queued_as` / `reviewed_by` 血缘。`/api/learning/governance-tasks` 可查询治理任务，`/api/learning/governance-tasks/{task_id}/status` 可更新任务并提交 `accepted/rejected/implemented` 决策，决策会回写候选状态、生成反馈事件 / 样本 / 后续治理血缘。这样自动发现出的非 Skill 问题也有真实可观察、可分配、可幂等恢复、可闭环反馈的治理对象。

已落地补充：`/api/learning/summary` 返回 `self_iteration` 健康指标，包括 Agent 候选数、治理草稿数、草稿校验通过 / 失败、实施就绪 / 阻断、AI 系统缺口、治理任务总数 / 待处理数 / 状态分布、反馈事件和自审事件，并计算 `health_score`；待处理治理任务会降低健康分。`/api/learning/automation-status` 额外返回 `governance_pending`、`governance_stale`、`governance_decisionless`、`governance_tasks_by_status`、治理 SLA 阈值、`automation_runs_by_status`、`automation_failed`、最近成功运行时间和自动循环 stale 阈值，用于判断自动循环是否卡在人工治理队列、决策反馈回流或自动流动引擎自身。`/api/learning/bottlenecks` 会把 `learning_governance_tasks` 中 `pending` / `in_progress` 的任务作为“待处理治理任务”卡点返回，`/api/learning/flow-journeys` 会在候选旅程里展示治理任务节点。前端脉动页 KPI 展示“AI 自迭代健康”，自动处理面板展示治理待办 / 超时 / 缺决策反馈数和自动循环失败数，用于直接发现自动循环是否卡在缺口、草稿校验、治理任务、反馈回流、自动处理引擎或实施交接。

来源：

- Agent 会话结果
- 用户对 Agent 结果的采纳 / 驳回
- Agent 使用过的工具和知识来源
- Agent 失败节点、风险中断、人工恢复点

落点：

| 情况 | 落点 |
| --- | --- |
| 高频成功回答 | Agent 记忆 / 知识库候选 |
| 用户修正 Agent 输出 | 偏好样本 / 评测样本 |
| 反复手工流程 | Skill 创建候选 |
| 工具选择错误 | Agent 策略候选 |
| 低置信度缺知识 | 知识缺口候选 |

### 7.5 SF -> 知识库 / Skill / SF 迭代

来源：

- `CodexMcpCallAudit`
- `CodexDebugRun`
- SF Dashboard reports
- `sf run analyze`
- `sf run candidate`

落点：

| 现象 | 自动生成 |
| --- | --- |
| 某 MCP 工具高频失败 | SF 工具修复候选 |
| 多人重复手工组合调用 | 新 SF 命令 / 新 Skill 候选 |
| SF 分析生成稳定报告模板 | 知识库模板 / 报告模板 |
| 某类 run 反复被 SF 复盘 | Skill 优化候选 / 评测包 |
| 高频真实 MCP 数据被消费 | 部门数据资产热度 |

约束：

- 写工具真实执行仍必须走 dry-run / real / idempotency key 规则。
- SF 自动迭代只能创建候选、待办或审核，不直接改生产能力。

### 7.6 训练 -> Agent / Skill

来源：

- `TrainingJobTask.metrics_json`
- `TrainingModelDeployment`
- 模型评估结果
- 灰度后的业务指标
- `TrainingJob.spec_json.lineage`
- `TrainingJob.spec_json.dataset.learning_artifact_ids`

落点：

- Agent 可用模型列表。
- Skill 分析路由中的 `active_model_deployment`。
- 训练效果报告进入知识库。
- 模型效果不达标生成回滚候选。

当前落地口径：

1. 训练任务捕获时从 `spec_json.lineage` 和 `dataset` 读取来源，写入以下 `trained_from` 血缘：
   - `improvement_candidate:<learning_candidate_id> -> training_job:<job_id>`
   - `run:<run_id> -> training_job:<job_id>`
   - `skill:<skill_id> -> training_job:<job_id>`
   - `learning_artifact:<artifact_id> -> training_job:<job_id>`
2. 训练网关 callback / 手动评估 / 后台回填都会触发训练任务捕获；任务的 task 状态、进度、脱敏 metrics key、模型产物 ID 会进入事件 metadata 和训练知识摘要。
3. 训练完成后生成 `knowledge_note`，把目标 Skill、数据集引用、训练目标、任务 ID、指标 key 和模型产物引用沉淀为可追溯训练知识。
4. 训练失败时生成 `training_improvement_candidate`，目标类型为 `training`，进入训练治理队列；候选不能直接重跑训练或修改模型路由。
5. 训练产物写入 `training_job:<job_id> -> model_artifact:<artifact_id>`，relation=`produced_artifact`，后续模型部署会接上 `model_artifact -> model_deployment`。
6. 模型部署请求、审批、激活、驳回、回滚都捕获为 `model_deployment` 学习事件：
   - `training_job:<job_id> -> model_deployment:<deployment_id>` 写 `deployed_to`。
   - `model_artifact:<artifact_id> -> model_deployment:<deployment_id>` 写 `deployed_to`。
   - `model_deployment:<deployment_id> -> skill:<skill_id>` 在 canary / active 时写 `deployed_to`。
   - `model_deployment:<deployment_id> -> model_deployment:<rollback_to>` 在存在回滚目标时写 `rollback_target`。
7. 模型部署状态会生成训练知识摘要；部署被 `rejected` 或 `rolled_back` 时生成 `training_improvement_candidate`，用于补充评测样本、调整门禁或修复数据集。
8. Agent / Skill 实际调用模型时必须补写 `used_as_model` 血缘，确保能从回答或报告追溯到模型部署版本。

## 8. 自动化策略

### 8.1 入库策略

| 策略结果 | 说明 |
| --- | --- |
| `auto_index` | 自动写入知识库并索引 |
| `review_first` | 生成知识候选，进入 Inbox / 审核 |
| `training_candidate_only` | 只进入训练样本池，不作为知识展示 |
| `agent_memory_only` | 只作为 Agent 内部记忆 |
| `improvement_candidate` | 只生成改进候选 |
| `ignore` | 忽略 |

### 8.2 默认规则

1. `run_mode in sample_preview / sandbox_test`：默认 `ignore`。
2. `dry_run=true`：默认不入知识库，可作为 SF 使用统计。
3. 报告摘要、结论、已确认业务指标：`auto_index`。
4. 用户驳回、低评分、失败：`eval_case + improvement_candidate`。
5. 含敏感字段、客户原始数据、订单明细：`review_first` 或 `training_candidate_only`。
6. 训练产物和模型指标：自动进训练血缘，摘要可进知识库。
7. Skill 代码变更候选：必须进入 Skill Git / Review，不允许直接发布。

### 8.3 去重与版本

去重 key：

```text
source_type + source_id + source_version + content_hash
```

要求：

- 同一报告更新只更新同一知识文档，不重复创建。
- 同一训练样本只入池一次。
- 同一改进建议多次命中时增加 evidence 和 priority，而不是创建重复候选。

## 9. 可观察页面规格

### 9.1 页面定位

页面名称建议：`智能闭环` 或 `数据流`。

目标：让用户能看到数据如何在知识库、Agent、训练、SF、Skill 之间流动，以及哪些自动化动作已经发生、哪些卡在审核、哪些产生了业务收益。

### 9.2 页面模块

#### A. 总览 KPI

- 捕获事件数
- 已入库知识数
- 待审核知识候选数
- 新增训练样本数
- 训练候选任务数
- Agent 使用知识次数
- Skill 优化候选数
- SF 迭代候选数
- 自动化成功率
- 平均入库延迟

#### B. 数据流图

示例节点：

```text
Skill Run
  -> DecisionLog
  -> Report
  -> KnowledgeDocument
  -> Agent Context
  -> User Feedback
  -> TrainingSample
  -> TrainingJob
  -> ModelDeployment
  -> Skill / Agent Runtime
```

节点类型：

- Skill
- Run
- Report
- Todo
- Knowledge Doc
- Agent Thread
- SF Call
- Training Sample
- Training Job
- Model Deployment
- Improvement Candidate
- Review

边类型使用 `learning_flow_edges.relation`。

#### C. 时间线

每条事件显示状态：

```text
捕获 -> 脱敏 -> 分类 -> 去重 -> 质量评分 -> 入库/样本/候选 -> 审核 -> 生效
```

#### D. 详情抽屉

点击任意节点展示：

- 来源对象
- 脱敏摘要
- 原始对象链接 / 原页面深链
- 部门 / 权限范围
- 策略决策
- 质量分
- 证据列表
- 下游对象
- 相关血缘一键加载
- 错误 / 卡点
- 可执行操作：一键物化、忽略、采纳、驳回、创建评审、创建训练候选、失败物化重试、未抽取事件重捕获

#### E. 筛选器

- 部门 / 组织单元
- Skill
- Agent
- SF 工具
- 事件类型
- 资产类型
- 状态
- 时间范围
- 是否需要审核

### 9.3 现有页面嵌入

| 页面 | 增强 |
| --- | --- |
| 知识库 | 文档详情显示来源 Run / Report / Todo / SF / Agent，显示被哪些 Agent / Skill 使用过 |
| Agent | 展示本次回答使用了哪些知识、模型、Skill，哪些结果被沉淀 |
| 训练 | 数据资产页显示样本来源、质量分、血缘和部署效果 |
| SF | 展示 MCP 调用 -> 报告 -> 知识库 -> 候选能力的链路 |
| Skill Studio | 展示该 Skill 贡献的知识、样本、评测、优化候选 |
| Inbox | 报告 / 待办详情支持“纳入知识库”“标记为训练样本”“生成改进候选” |
| TaskTree / Run Trace | 链接到完整闭环数据流 |

## 10. API 设计

### 10.1 事件与图谱

```text
GET  /api/learning/summary
GET  /api/learning/events
GET  /api/learning/artifacts
GET  /api/learning/flow-graph
GET  /api/learning/flow-topology
GET  /api/learning/flow-journeys
GET  /api/learning/bottlenecks
GET  /api/learning/entities/{type}/{id}/lineage
GET  /api/learning/candidates
```

### 10.2 操作接口

```text
POST /api/learning/events/backfill
POST /api/learning/artifacts/{id}/materialize
POST /api/learning/artifacts/{id}/ignore
POST /api/learning/candidates/{id}/accept
POST /api/learning/candidates/{id}/reject
POST /api/learning/candidates/{id}/create-review
POST /api/learning/candidates/{id}/create-training-job
```

### 10.3 页面跳转协议

数据流页面必须支持 URL 查询态回放：`days`、`department`、`skill_id`、`run_id`、`source_type`、`event_type`、`artifact_kind`、`target_type`、`sf_tool`、`artifact_status`、`candidate_status`、`q`，并支持 `artifact_id`、`candidate_id`、`event_id`、`entity_type + entity_id` 直接打开详情抽屉。

所有实体建议统一 deep link：

```json
{
  "entity_type": "knowledge_document",
  "entity_id": "kdoc_xxx",
  "title": "天猫链接下滑复盘",
  "url": "/knowledge?doc_id=kdoc_xxx"
}
```

## 11. 权限与合规

1. 读权限按现有组织 / 部门 / Skill 权限继承。
2. 管理员可看跨部门统计，但普通用户只能看自己可见部门的数据流。
3. 原始 payload 不直接暴露，页面默认展示脱敏摘要。
4. 需要查看原始运行数据时，复用 Run Trace / SF raw query 的权限和脱敏逻辑。
5. 训练样本池不能返回未脱敏原始 input / output。
6. 任何 Skill 变更必须通过 Skill Git、测试、审核、发布。
7. 任何真实外部副作用仍走现有 MCP / DingTalk / SkillForge 审计规则。

## 12. 指标体系

### 12.1 数据流指标

- 事件捕获量
- 入库成功率
- 入库延迟 P50 / P95
- 去重率
- 待审核积压量
- 策略命中分布

### 12.2 知识库指标

- 自动生成文档数
- 文档被检索次数
- 引用覆盖率
- 低置信度问答率
- 过期知识比例

### 12.3 Agent 指标

- Agent 使用知识次数
- 知识引用后采纳率
- 用户修正率
- 工具选择失败率
- 策略候选转化率

### 12.4 训练指标

- 新增训练样本数
- 样本质量分布
- 训练候选通过率
- 模型评估 win rate
- 灰度后业务指标提升
- 回滚率

### 12.5 Skill / SF 指标

- Skill 优化候选数
- 候选采纳率
- 发布后成功率变化
- 高频 SF 工具调用量
- 高频失败工具数
- 自动生成 SF 需求转化率

## 13. 分阶段落地

### Phase 1：事件总线与可观察 MVP

目标：先看见数据流。

后端：

- 新增 `learning_events`、`learning_artifacts`、`learning_flow_edges`、`learning_ingestion_jobs`、`improvement_candidates`、`learning_automation_runs`。
- 接入 `ExecutionRun`、`DecisionLog`、Inbox 报告、`CodexMcpCallAudit`、`KnowledgeQueryLog`、`TrainingJob`。
- 提供 `/api/learning/summary`、`/api/learning/events`、`/api/learning/flow-graph`、`/api/learning/flow-topology`、`/api/learning/flow-journeys`、`/api/learning/bottlenecks`、`/api/learning/automation-status`、`/api/learning/automation/run`；自动流动运行写入 `learning_automation_runs`，重启后仍可观察最近运行、失败原因、捕获/物化结果。
- `flow-journeys` 必须以单条来源事实为中心返回 `来源 -> 标准事件 -> 学习资产 -> 自动物化 -> 迭代候选 -> 治理闭环` 的脱敏步骤、进度、状态和 blockers，用于证明“某份数据现在流到哪、卡在哪”，不得返回原始 payload。
- 所有观察 API 支持按部门、Skill、Run、来源、事件、资产、候选类型、状态和 SF 工具筛选，避免只能看全量数据流。

前端：

- 新增 `智能闭环 / 数据流` 页面。
- 展示 KPI、时间线、基础流图、流动式拓扑画布、详情抽屉。

### Phase 2：自动知识库入库

目标：把确认过的运行结果和报告沉淀为部门知识。

- 扩展 `KnowledgeDocument.source_type` 支持 `execution/report/todo/feedback/sf/agent/training`。
- 实现入库策略、脱敏、去重、质量评分。
- 报告和完成待办自动生成知识文档。
- 知识库文档详情显示来源和使用血缘。

### Phase 3：训练样本池

目标：让训练数据自动累积，但训练仍需审核。

- 从 `DecisionLog`、用户反馈、任务完成结果生成 `training_sample`。
- 自动生成 dataset manifest。
- 达到阈值时创建 `awaiting_review` 训练候选。
- 训练任务详情展示样本血缘和质量分布。

### Phase 4：Agent 记忆与策略候选

目标：Agent 能吸收部门知识和反馈。

- 增加 `agent_memory_items` 或复用 `learning_artifacts(agent_memory)`。
- 记录 Agent 使用知识、工具和模型的血缘。
- 用户修正和失败会话生成 Agent 策略候选。
- Agent 页面展示“本次使用了哪些经验”。

### Phase 5：Skill / SF 自动迭代候选

目标：从数据流中发现可产品化、可自动化的高频需求。

- 高频 SF 调用组合 -> SF 命令 / MCP 包装候选。
- 高频手工流程 -> Skill 新建候选。
- 高频失败 / 低评分 -> Skill 优化候选和评测包。
- 候选进入现有 Skill Git / Review / Optimizer 链路。

### Phase 6：效果闭环与自动优先级

目标：让系统根据业务收益自动排序。

- 关联发布前后成功率、业务指标、人工耗时节省。
- 改进候选按收益、风险、证据强度排序。
- 自动推荐下一个最值得优化的 Skill / Agent / SF 功能。

## 14. MVP 验收标准

MVP 完成后至少满足：

1. Skill 完成一次真实运行后，能在智能闭环页面看到：Run -> DecisionLog -> Report / Todo。
2. 报告被自动或手动纳入知识库后，能看到：Report -> KnowledgeDocument。
3. Agent / 知识库检索使用该文档后，能看到：KnowledgeDocument -> Query / Agent Context。
4. 用户反馈一次结果后，能看到：DecisionLog -> TrainingSample / EvalCase。
5. 某次 SF MCP 调用能显示到 SF 事件流，并能关联报告或改进候选。
6. 任意节点点开详情，能看到来源、脱敏摘要、权限范围、策略结果和下游对象。
7. 所有自动生成内容都有血缘，不出现“凭空生成的知识 / 样本 / 候选”。

## 15. 风险与防呆

| 风险 | 防呆设计 |
| --- | --- |
| 无脑入库导致知识污染 | 策略分流、质量评分、人工审核、去重 |
| 敏感数据泄漏 | 统一脱敏、source ref 引用、权限校验、禁止写密钥 |
| 自动训练低质量模型 | 样本阈值、评估门禁、人工审批、灰度和回滚 |
| 自动改 Skill 绕过审核 | 只能生成候选，必须走 Skill Git / Review |
| 页面信息过载 | 总览 + 筛选 + 详情抽屉 + 实体跳转 |
| 血缘缺失 | 所有物化动作必须写 `learning_flow_edges` |
| 重复候选泛滥 | source_hash 去重，重复命中增加证据和优先级 |
| 部门越权 | 所有事件和资产带 department / org_unit_id，查询复用权限体系 |

## 16. 与现有文档关系

本规格与以下文档互补：

- `docs/architecture/intelligence-data-flow-positive-loop.md`：需求讨论沉淀和总体架构，定义正向循环的数据流、可观察页面和分阶段路线。
- `docs/spec/department-knowledge-base.md`：定义部门知识库和 RAG 能力，本规格定义自动知识来源和跨模块血缘。
- `docs/next/training/gpu-training-gateway-and-data-asset-plan.md`：定义训练控制面和网关，本规格定义训练样本如何从闭环事件产生。
- `docs/guides/sf-codex-claude-workflow.md`：定义 SF / Codex / MCP 使用边界，本规格定义 SF 调用如何进入学习闭环。
- `docs/spec/skill-personalized-ui-overlay.md`：定义个人 UI overlay 边界，本规格遵守其禁止 AI 越权修改 Skill 默认界面的约束。
- `docs/spec/skill-permission-unification.md`：定义 Skill 权限统一口径，本规格的 Skill 事件和候选必须复用该权限体系。
