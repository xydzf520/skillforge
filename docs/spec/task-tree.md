# 任务树（Task Tree）规格

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


> 原项目状态记录：Phase 1 / 1.5 / 2 已上线（Release 2.0.2，2026-04-14），Phase 3 延后评估
> 对应实现：`app/tasktree/`、`web/src/pages/tasktree/`、migrations 049-054
> 运维 Runbook：`docs/plans/2026-04-14-tasktree-runbook.md`
> 数据流：`docs/architecture/tasktree-data-flow.md`
> 设计过程（已归档）：`docs/archive/plans/2026-04-13-task-tree-{prd-claude,codex-review,implementation-spec,dev-spec}.md`

---

## 核心共识

> 部门是任务树的展示入口，不是存储层聚合根；存储层以 root execution 为树边界。
> Phase 1 用 projection 验证需求，Phase 1.5 用 task_nodes_light 固化拓扑与状态投影。

---

## 1. 产品定位

**SkillForge 做 Skill 控制平面 + 价值度量平面，不做执行引擎。**

| 做 | 不做 |
|----|------|
| 运行状态可视化 | 自动故障转移 |
| 执行链路追踪 | 中心调度器 |
| **价值度量（ROI/节省工时/成本归因）** | 通用运维监控（Grafana 的事） |
| **部门驾驶舱** | 通用审批流引擎（钉钉的事） |
| 一键重试 / 跳转编辑 | 通用项目管理 |
| 回写链路展示 | |
| **人工接管率追踪** | |

---

## 2. 分阶段方案

### Phase 1：探针式 MVP（12 人日）

**目标**：验证任务树有没有人用，产品价值是否成立

**后端：**
- 新建 `app/tasktree/service.py`，核心类 `TaskTreeProjection`
- **唯一规则：所有树形数据只通过 `TaskTreeProjection` 读取**
- 内部实现：3 步查询（节点列表 → 活跃 run → 按需查回写链路）
- Redis 缓存：`sf:tasktree:{department}` TTL 10s
- **不建新表**

**前端：**
- `TaskTree.vue` — 部门 → 节点 → Skill → 执行 树形视图
- **不做 5s 全量轮询**，改为 10s + version/etag 增量拉取
- 展示 `projected_at` 时间戳（让用户知道数据新鲜度）

**节点状态 3 级缓冲：**

| 状态 | 条件 | 展示 |
|------|------|------|
| 在线 | heartbeat < 2min | 绿色 |
| 可能离线 | 2min < heartbeat < 5min | 黄色 + "上次在线 X 分钟前" |
| 离线 | heartbeat > 5min | 红色 |

**监控埋点（Phase 1 上线时必须有）：**
- `GET /task-tree` 的 p50 / p95 响应时间
- 缓存命中率
- 活跃 root_id 数量
- 节点状态误判次数

**API：**
```
GET  /api/task-tree?department={id}          # 树形结构（带 version/etag）
GET  /api/task-tree/node/{instance_id}       # 节点详情
GET  /api/task-tree/run/{run_id}/chain       # 回写链路
```

---

### Phase 1.5：固化拓扑投影（5 人日）

> **状态：2026-04-14 完成 + 补偿机制扩展。** 除原设计的 `task_nodes_light` 投影表与双写外，新增 `tasktree_repair_queue` 失败补偿表、按 30s 轮询的 `repair_worker`、drift_scan 对账闭环，以及 writer → repair_queue 的自愈链路。迁移版本 050（投影表）+ 051（价值字段）+ 052（补偿队列）。详见 `docs/architecture/tasktree-data-flow.md` 与 `docs/reviews/2026-04-14-tasktree-optimization-audit.md`。

**触发条件（任一达到即启动，不再讨论）：**
- `GET /task-tree` 连续 3 天 p95 > 80ms
- 缓存命中率 < 70%
- 5 分钟内活跃 root_id > 20
- 出现一次父子关系错误或状态抖动事故
- **或者**：Phase 1 上线后 2 周内强制启动（以先到者为准）

**新建 `task_nodes_light` 表：**

```sql
CREATE TABLE task_nodes_light (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    root_id         UUID NOT NULL,            -- 顶层 execution_run_id（树边界）
    parent_id       UUID REFERENCES task_nodes_light(id),
    source_run_id   VARCHAR(100),             -- 关联 execution_runs.id
    source_instance_id VARCHAR(100),          -- 关联 openclaw_instances.id
    department_id   VARCHAR(100) NOT NULL,
    node_type       VARCHAR(30) NOT NULL,     -- department_root / instance / skill_run
    title           TEXT NOT NULL,
    status          VARCHAR(20) NOT NULL,     -- queued / running / completed / failed / idle / stale
    sort_key        VARCHAR(100) NOT NULL DEFAULT '',
    last_heartbeat_at TIMESTAMP,
    started_at      TIMESTAMP,
    finished_at     TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_tnl_tree ON task_nodes_light(department_id, root_id, parent_id, sort_key);
CREATE INDEX ix_tnl_updated ON task_nodes_light(department_id, updated_at DESC);
CREATE INDEX ix_tnl_run ON task_nodes_light(source_run_id);
CREATE INDEX ix_tnl_active ON task_nodes_light(department_id, status)
    WHERE status IN ('queued', 'running', 'stale');
```

**写入时机（仅 3 个点，不扩散）：**

1. **ExecutionRun 创建时**：upsert 节点（id/root_id/parent_id/title/status/sort_key）
2. **ExecutionRun 状态跃迁时**：更新 status/started_at/finished_at/updated_at
3. **心跳对账 cron job**：批量同步 last_heartbeat_at，标记长时间无心跳为 stale

**不在 Phase 1.5 做的事：**
- todo/decision 状态不写入 task_nodes_light（查询时动态聚合）
- 不建 task_edges 表（Phase 2 按需）

**TaskTreeProjection 切换**：内部实现从 "3 步查询" 切换到 "查 task_nodes_light"，对外 API 不变。

---

### Phase 2：价值度量层（8 人日，Phase 1.5 完成后启动）

> **状态：2026-04-14 完成（含 value_service + ai_diagnose）。** `app/tasktree/value_service.py` 已落 KPI 基线录入 + 部门驾驶舱聚合 + Token 成本追踪；`app/tasktree/ai_diagnose.py` 已落失败诊断。前端 `TaskTreeDashboard.vue` 已接入 `/api/task-tree/dashboard`。本次优化合并了 N+1 查询、按部门缓存授权域隔离与 AI 诊断失败率告警。原文设计目标未变，仅状态变更。

> **来源：企业 AI 战略评估指出"价值经营"是平台最大短板（4.8/10）。
> 任务树不应该只是"运行监控"，而应该成为"价值度量的基础设施"。**

**新增能力：**

**a) KPI 基线录入（每个 Skill）：**
```
execution_runs 新增字段：
  - manual_baseline_minutes: 人工完成同样工作的耗时基线
  - business_value_tag: 业务价值标签（GMV/转化/时效/风险规避/成本节约）
  - business_ref_id: 关联的业务单据 ID（订单号/工单号）
```
AIBP 为每个 Skill 录入"如果没有 AI，人工做这件事需要多少分钟"作为基线。

**b) 部门驾驶舱（任务树上方统计区升级）：**
```
部门看板 = {
  本月 AI 执行次数,
  本月节省工时 = Σ(manual_baseline_minutes - actual_duration),
  本月成功率,
  本月 Token 成本,
  ROI = 节省工时价值 / Token 成本,
  Top 5 价值 Skill 排行,
  人工接管次数,
}
```

**c) 人工接管标记：**
- 任务树中每个执行节点标记"是否有人工接管"
- 数据来源：ai_todos 中 `status=rejected` 或 `user_action` 非自动完成
- 人工接管率 = 需要人工介入的执行次数 / 总执行次数

**d) 执行成本追踪：**
- 利用 decision_log 已有的 `model_id` + `token_count` 字段
- 按部门/Skill 聚合 Token 消耗
- 成本 = token_count * 模型单价（从 system_config 读取）

**Phase 2 同时包含（原有方向）：**
- todo/decision 摘要物化到 task_nodes_light
- task_edges 表（按需）
- SSE/WebSocket 实时推送
- decision_log 按月分区

### Phase 3：评测回归 + AIBP 工作台（按需，12 个月后评估）

**可能的方向：**
- 批量回归测试框架（对所有 test_cases 批量执行 + 结果断言）
- 上线门禁（回归测试不通过 → 阻止发布）
- 需求台账 / 机会池（AIBP 立项工具）
- 案例库 + 横向复制路径
- 版本 / 灰度 / 回滚

---

## 3. 关键架构决策

### 3.1 root_id 语义

| 方案 | Claude 原案 | Codex 建议 | **共识** |
|------|------------|-----------|---------|
| root_id | department | root execution_run_id | **root execution_run_id** |

**理由**：部门是长寿命实体，作为 root 会导致树无限增长，缓存/重建/归档困难。root execution 有天然边界（一次执行一棵树），适合 etag、增量同步、rebuild。

**前端处理**：后端提供 `GET /task-tree?department={id}` 返回该部门下最近的多个 root tree。前端渲染 department 为 synthetic 顶层节点。

### 3.2 数据架构路线

| 阶段 | 方案 | 复杂度 | 技术债 |
|------|------|--------|--------|
| Phase 1 | 聚合现有表（方案 A） | 低 | 中（封装在 Projection 类中可控） |
| Phase 1.5 | task_nodes_light（方案 B-lite） | 中 | 低 |
| Phase 2 | 按需物化/分区 | 按需 | -- |

### 3.3 节点异构统一

Phase 1 不统一协议，只在 `TaskTreeProjection` 内部做适配。Phase 2 考虑 `RuntimeAdapter` 接口。

---

## 4. 路由与导航

```
一级 Tab：大厅 | Skills | 任务树 | 应用 | 智脑 | 待办
路由：/task-tree
Meta：{ primary: 'tasktree' }
```

---

## 5. 工时汇总

| 阶段 | 范围 | 人日 | 交付时间 |
|------|------|------|---------|
| Phase 1 | TaskTreeProjection + 前端 + 缓存 + 监控 | 12 | 立即开始 |
| Phase 1.5 | task_nodes_light + 写通路 + Projection 切换 | 5 | Phase 1 上线后 ≤2 周 |
| Phase 2 | **价值度量层**：KPI 基线 + 部门驾驶舱 + 人工接管 + 成本追踪 | 8 | Phase 1.5 完成后 |
| Phase 3 | 评测回归 + AIBP 工作台 | TBD | 12 个月后 |
| **总计（确定部分）** | | **25** | |

---

## 6. 触发迁移的硬阈值

以下任一条达到，Phase 1.5 自动启动，无需重新讨论：

| 指标 | 阈值 |
|------|------|
| `GET /task-tree` 连续 3 天 p95 | > 80ms |
| 缓存命中率 | < 70% |
| 5 分钟内活跃 root_id 数 | > 20 |
| 状态抖动/父子关系错误 | 出现 1 次 |
| Phase 1 上线后时间 | 2 周 |

---

## 7. 不做清单（长期边界）

| 不做 | 理由 |
|------|------|
| 自动故障转移 | 属于 OpenClaw/AIClaw 运维职责 |
| 通用运维监控 | 属于 Grafana |
| 审批流引擎 | 属于钉钉 |
| 通用项目管理 | 属于飞书/JIRA |
| 完整 event sourcing | Phase 2 按需 |
| 跨系统双向同步 | 只做单向展示+跳转 |

---

## 8. 2026-04-14 优化增量

基于 Phase 1.5 + Phase 2 全量落地后的代码审计（19 项 / 6 P0 + 3 P1 + 2 P2 + 3 战略）做的收敛增量。设计原则未变，以下列出新引入的 9 项补丁。详见 `docs/reviews/2026-04-14-tasktree-optimization-audit.md`。

| # | 变更 | 位置 | 目的 |
|---|------|------|------|
| 1 | 双写补偿队列 | `tasktree_repair_queue` 表 + `repair_worker` + `drift_scan` | writer 写失败不再静默；repair_worker 每 30s 指数退避重试；drift_scan 扫描 execution_runs 与 task_nodes_light 缺口自愈 |
| 2 | fire-and-forget dispatcher | execution_service / bridge_router / scheduler → dispatcher → writer | 主链路与 tasktree 写入解耦；writer 异常不阻塞执行，仅落 repair_queue |
| 3 | 缓存按 department + auth_scope 分域 | `_tasktree_cache_scope` + scope_by | 跨部门/跨用户缓存互不覆盖；admin/普通用户视图分离；失效按 department 粒度 |
| 4 | get_task_tree 切 task_nodes_light 主路径 | feature flag `USE_TNL_READ`（默认 True） | 读路径由 3 步聚合 → 单表树查询；可回退到 Phase 1 JOIN fallback |
| 5 | Department ABAC 收口 | router / service | `department` 不再从请求参数原样信任；所有授权走 `current_user.department` + admin 检查；越权路径记录审计 |
| 6 | value_service N+1 合并 | `value_service.get_department_value_summary` | 按 skill 批聚合执行次数 / 成本 / 节省工时，单次查询返回完整驾驶舱数据 |
| 7 | 前端轮询退避 + 导航迁移 | `useTaskTree.ts` + `AppLayout.vue` | 10s 轮询改为指数退避（空闲延长 TTL）+ 请求去重；"任务树"升级为一级 Tab |
| 8 | Prometheus 告警扩充 | `deploy/prometheus.tasktree.rules.yml` | 新增 writer_failure_rate / repair_queue_size / repair_lag_seconds / drift_scan_gap / get_tree_p99 / ai_diagnose_failure_rate 6 组指标告警 |
| 9 | Runbook + Data-flow 文档 | `docs/plans/2026-04-14-tasktree-runbook.md`、`docs/architecture/tasktree-data-flow.md` | 运维排障路径固化 + 写/读/补偿三张数据流图入库 |

回滚优先级：feature flag `USE_TNL_READ` → 关 repair_worker → migration 052 → 051 → 050 逐个 downgrade。回滚顺序见 `docs/architecture/tasktree-data-flow.md#回滚策略`。

---

## 附录：设计过程摘要

Phase 1/1.5 方案是 Claude 与 Codex 三轮评审后收敛的结果，核心取舍：
- **root_id = root execution_run_id**（非 department）：部门长寿命会导致树无限增长；root execution 有天然边界。
- **Phase 1 不建表 + Projection 封装**：先验证产品价值，避免模型过度设计。
- **Phase 1.5 task_nodes_light**（非 task_edges）：3 个写入点 + 硬阈值触发迁移机制。

完整评审记录见 `docs/archive/plans/2026-04-13-task-tree-codex-review.md`。
