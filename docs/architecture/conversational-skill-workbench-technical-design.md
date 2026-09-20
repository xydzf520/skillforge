# 对话式 Skill 工作台技术设计

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


> 日期：2026-04-05
> 配套文档：
> - `docs/archive/conversational-skill-workbench-proposal.md`
> - `docs/spec/conversational-skill-workbench-functional-spec.md`

## 1. 设计目标

本技术方案用于支撑以下产品形态：

- 小白用户：通过对话形成完整 Skill
- 专业用户：对 Skill 进行结构化编辑、深度调优和工作流编排

技术设计的核心原则：

1. Skill 必须是结构化对象，而不是只靠长文本 prompt
2. 模型返回必须是 patch，而不是整份文档覆盖
3. 所有 AI 修改都必须经过验证和确认后才落盘
4. 单个 Skill 调优和工作流编排使用同一套底层对象

## 2. 总体架构

## 2.1 架构分层

建议分为 5 层：

### L1. 对话层

职责：

- 接收用户自然语言输入
- 维护会话上下文
- 识别本轮意图

现有基础可复用：

- `app/testing/agent_chat.py`
- `web/src/pages/skill/SkillChat.vue`

### L2. Skill 结构层

职责：

- 表示当前 Skill 的结构化对象
- 提供模块级访问能力
- 负责 Markdown / YAML / 结构化对象互转

现有基础可复用：

- `app/skills/parser.py`

### L3. Patch 生成层

职责：

- 基于用户意图和目标模块生成结构化 patch
- 支持引用已有 Skill 模块
- 支持 workflow patch

建议新增：

- `app/workbench/intent_service.py`
- `app/workbench/patch_service.py`
- `app/workbench/workflow_patch_service.py`

### L4. 验证层

职责：

- 结构验证
- 样例验证
- 历史回放验证
- 影响分析

现有基础可复用：

- `app/skills/validation_service.py`
- `app/testing/replay.py`
- `app/execution/*`

### L5. 应用层

职责：

- 把 patch 应用到 Skill 结构对象
- 渲染回 `SKILL.md` / `policy_pack.yaml`
- 如涉及工作流，渲染回 Playbook YAML
- 形成变更记录、提交 Git、触发后续审核/发布流程

## 2.2 页面关系

建议新增一个统一工作台页面：

- `web/src/pages/skill/SkillWorkbench.vue`

页面内包含：

- 对话面板
- 模块导航
- 模块编辑区
- diff 预览区
- 验证结果区
- 工作流预览 / 工作流入口

工作流重型编辑仍复用现有 Playbook 编辑器，但工作台内需要有轻量 workflow patch 预览能力。

## 2.2.1 顶部导航要求

工作台必须通过顶部一级导航 `Skills` 进入。

建议导航结构：

- 顶部一级导航：`Skills`
- `Skills` 域下页面：
  - `All Skills`
  - `New`
  - `Generate`
  - `Skill 工作台`
  - `Tests`
  - `History`

## 2.3 全链路系统视图

一次完整操作建议按下面链路运行：

```text
前端对话输入
→ 前端发起 intent 请求
→ 后端识别 target_module / intent
→ 前端确认或补充
→ 后端构建模块上下文
→ 调用 AICLaw 生成结构化 patch
→ 后端做 schema 校验
→ 后端计算 diff 预览
→ 后端运行验证
→ 前端展示 diff + 验证结果
→ 用户确认应用
→ 后端写数据库 / 文件 / Git
→ 返回最新 Skill / Workflow 状态
```

涉及的五条主线分别是：

- 前端：会话、模块切换、patch 预览、验证展示
- 后端：意图解析、patch 生成、patch 应用、验证编排
- 数据库：session、message、patch、validation run、引用关系
- 缓存：上下文缓存、AI 返回缓存、验证缓存、引用缓存
- AI：intent 识别、完整 SkillDraft 生成、模块 patch 生成、workflow patch 生成

## 3. 领域对象设计

## 3.1 SkillDocument

建议在后端定义一个统一 Skill 文档对象：

```python
SkillDocument = {
  "meta": {},
  "goal": "",
  "rules": [],
  "params": [],
  "output_table": [],
  "test_cases": [],
  "custom_sections": {},
}
```

说明：

- `meta`：基础信息，如 name、department、trigger_type、risk_level
- `goal`：用户可理解的一句话目标
- `rules`：规则步骤、条件、结论、动作
- `params`：参数列表
- `output_table`：输出字段定义
- `test_cases`：测试样例

## 3.2 WorkflowDocument

```python
WorkflowDocument = {
  "nodes": [],
  "edges": [],
  "bindings": [],
}
```

说明：

- `nodes`：节点定义
- `edges`：连线关系
- `bindings`：节点输出到下游输入的字段映射

## 3.3 Patch 对象

一个 patch 只允许修改一个模块。

建议统一结构：

```json
{
  "target_module": "params",
  "intent": "tune_threshold",
  "summary": "将 ROI 红灯阈值从 1.2 调整到 1.0",
  "patch": {},
  "warnings": [],
  "needs_confirmation": true
}
```

## 3.4 ValidationReport

建议统一验证结果结构：

```json
{
  "structural_checks": [],
  "sample_case_checks": [],
  "historical_replay_checks": [],
  "impact_summary": {
    "improved": 0,
    "regressed": 0,
    "unchanged": 0
  },
  "can_apply": true
}
```

## 4. 模块映射设计

为了让“小白对话”和“专业编辑”使用同一套对象，建议定义统一模块枚举：

- `goal`
- `rules`
- `params`
- `output_table`
- `test_cases`
- `workflow`

前端展示名称：

- `@目标`
- `@规则`
- `@参数`
- `@输出表格`
- `@测试样例`
- `@工作流`

内部模块名和展示名必须固定映射，不允许自由拼写。

## 5. 后端服务设计

## 5.1 建议新增模块

建议新增目录：

```text
app/workbench/
├── __init__.py
├── router.py
├── schemas.py
├── intent_service.py
├── patch_service.py
├── workflow_patch_service.py
├── context_builder.py
├── apply_service.py
├── validate_service.py
└── diff_service.py
```

## 5.2 服务职责

### `intent_service.py`

职责：

- 根据用户输入判断当前目标模块
- 识别当前是“生成完整 Skill”还是“修改某个模块”
- 在不确定时返回澄清建议

输入：

- 用户消息
- 当前 Skill 状态
- 当前页面上下文

输出：

- `target_module`
- `intent`
- `confidence`
- `need_clarification`

### `context_builder.py`

职责：

- 为不同模块构建精简上下文
- 支持引用已有 Skill 或模块
- 避免把整份 Skill 全量塞给模型

### `patch_service.py`

职责：

- 生成 `goal / rules / params / output_table / test_cases` patch

要求：

- 只输出结构化对象
- 不直接输出整份 `SKILL.md`

### `workflow_patch_service.py`

职责：

- 生成工作流 patch
- 支持节点插入、节点删除、边条件修改、字段绑定

### `apply_service.py`

职责：

- 把 patch 应用到当前 SkillDocument 或 WorkflowDocument
- 生成新的结构化状态
- 渲染为最终文件

### `validate_service.py`

职责：

- 运行结构验证
- 运行样例验证
- 运行历史回放验证

### `diff_service.py`

职责：

- 计算模块级 diff
- 计算文本级 diff
- 生成前端可直接展示的数据

## 5.3 Router 设计

建议新增一个独立 router：

- `app/workbench/router.py`

该 router 只负责工作台能力，不直接混入现有 `skills/router.py` 的普通 CRUD 路径。

建议挂载方式：

- `app.include_router(workbench_router, prefix="/api/skills", tags=["Skill工作台"])`

这样路径仍然围绕 Skill，但工作台逻辑和普通 Skill 管理逻辑保持解耦。

## 5.4 后端服务编排顺序

后端一次 patch 生成建议走固定编排顺序：

1. `intent_service`
2. `context_builder`
3. `patch_service` 或 `workflow_patch_service`
4. `schema_validator`
5. `diff_service`
6. `validate_service`
7. `apply_service`

说明：

- `intent_service` 决定目标模块
- `context_builder` 只组装目标模块需要的最小上下文
- `patch_service` 只生成结构化 patch
- `schema_validator` 负责严格拦截异常 patch
- `diff_service` 给前端提供可读预览
- `validate_service` 负责 patch 风险评估
- `apply_service` 才能真正写状态

## 6. 接口设计

## 6.1 Skill 工作台接口

建议新增这些接口：

### `POST /api/skills/{skill_id}/workbench/session`

用途：

- 创建工作台会话

返回：

- `session_id`
- 当前 Skill 结构化状态
- 当前模块状态摘要

### `POST /api/skills/{skill_id}/workbench/intent`

用途：

- 解析用户本轮意图

请求：

```json
{
  "message": "这个结果太激进了",
  "active_module": "rules"
}
```

返回：

```json
{
  "target_module": "params",
  "intent": "tune_threshold",
  "confidence": 0.82,
  "need_clarification": false
}
```

### `POST /api/skills/{skill_id}/workbench/patch`

用途：

- 生成单模块 patch

请求：

```json
{
  "session_id": "wb_xxx",
  "message": "把 ROI 阈值改成 1.8",
  "target_module": "params",
  "references": []
}
```

返回：

```json
{
  "patch_id": "patch_xxx",
  "target_module": "params",
  "summary": "修改 ROI 阈值",
  "patch": {},
  "diff_preview": {}
}
```

### `POST /api/skills/{skill_id}/workbench/validate`

用途：

- 验证一个 patch

请求：

```json
{
  "patch_id": "patch_xxx"
}
```

返回：

- 统一 `ValidationReport`

### `POST /api/skills/{skill_id}/workbench/apply`

用途：

- 应用 patch 到 Skill

请求：

```json
{
  "patch_id": "patch_xxx",
  "confirm": true
}
```

返回：

- 新的 Skill 状态
- 新版本 diff

## 6.2 工作流接口

### `POST /api/skills/{skill_id}/workbench/workflow-patch`

用途：

- 基于用户描述生成 workflow patch

### `POST /api/playbooks/{name}/workbench/node-patch`

用途：

- 对工作流中某个节点发起局部修改

## 7. AICLaw 调用协议

## 7.1 不允许直接输出全文

AICLaw 返回结果不能是：

- 整份 `SKILL.md`
- 整份 Playbook YAML

必须是：

- 单模块 patch
- 结构化结果

## 7.2 建议协议

### 规则 patch

```json
{
  "target_module": "rules",
  "intent": "tighten_rule",
  "summary": "增加 ROI 下限判断",
  "patch": {
    "rules": []
  }
}
```

### 参数 patch

```json
{
  "target_module": "params",
  "intent": "tune_threshold",
  "summary": "调整 ROI 红灯阈值",
  "patch": {
    "params": []
  }
}
```

### 工作流 patch

```json
{
  "target_module": "workflow",
  "intent": "insert_node",
  "summary": "在结果输出后插入审批节点",
  "patch": {
    "nodes": [],
    "edges": []
  }
}
```

## 7.3 AI 子能力拆分

建议把 AI 调用拆成 4 类能力，而不是一个大 prompt：

### A. Intent 模型

职责：

- 判断当前是生成完整 Skill，还是修改某个模块
- 返回 `target_module` 和 `intent`

输入：

- 用户消息
- 当前 active module
- 当前页面上下文

输出：

- 小 JSON，不能带 patch

### B. SkillDraft 模型

职责：

- 从需求描述生成完整 Skill 草稿

输出：

- 完整 SkillDocument 结构

### C. ModulePatch 模型

职责：

- 对 `goal / rules / params / output_table / test_cases` 生成单模块 patch

输出：

- 单模块 patch

### D. WorkflowPatch 模型

职责：

- 对工作流节点和边生成 patch

输出：

- workflow patch

## 7.4 AI Prompt 设计

建议每一类 AI 能力都使用独立 prompt 模板，不共用一个超级 prompt。

建议模板：

- `intent_classification.prompt`
- `skill_draft_generation.prompt`
- `module_patch_generation.prompt`
- `workflow_patch_generation.prompt`
- `validation_explanation.prompt`

Prompt 设计要求：

1. 只允许修改指定模块
2. 不允许输出全文
3. 强制输出 JSON
4. 明确 patch schema
5. 明确禁止跨模块隐式修改

## 7.5 AI 输出约束

所有 AI 返回结果进入后端后必须经过：

1. JSON parse
2. Pydantic schema 校验
3. 模块边界校验
4. patch 合法性校验

失败时不能静默降级为“直接应用模型文本”，必须报错并提示用户重试。

## 7.6 AI 运行时控制

建议统一由系统配置控制：

- 默认模型
- fallback 模型
- 超时时间
- 单次最大 token
- 月度预算
- 是否允许自动生成 workflow patch

同时建议记录：

- prompt 类型
- 模型名
- token 数
- 耗时
- 成功/失败状态

## 7.7 AI 降级策略

AI 不可用时系统不能整体不可用。

建议降级：

- 可继续查看和编辑现有 Skill 结构
- 可继续手工编排 workflow
- AI 入口显示“当前不可用”
- 已生成但未应用的 patch 仍可查看

## 8. 前端设计

## 8.1 新页面建议

建议新增：

- `web/src/pages/skill/SkillWorkbench.vue`

建议新增组件：

```text
web/src/components/workbench/
├── WorkbenchChatPanel.vue
├── WorkbenchModuleNav.vue
├── WorkbenchPatchPreview.vue
├── WorkbenchValidationPanel.vue
├── WorkbenchReferencePicker.vue
├── ParamsEditor.vue
├── RulesEditor.vue
├── OutputTableEditor.vue
├── TestCaseEditor.vue
└── WorkflowPreviewPanel.vue
```

## 8.2 状态管理

建议新增 store：

- `web/src/stores/workbench.js`

管理内容：

- 当前 session
- 当前 Skill 结构
- 当前模块
- 当前 patch
- 当前验证结果
- 当前引用对象

## 8.3 前端路由与入口

建议新增的顶部导航域：

- `Skills`

建议新增路由：

- `/skills`
- `/skills/new`
- `/skills/generate`
- `/skills/workbench/:id`
- `/skills/workflow/:id`

建议保留现有：

- `skill/:id/edit`
- `skill/:id/chat`
- `playbook/:name/edit`

工作台和现有页面并存，避免一次性替换现有流程。

## 8.3.1 顶部导航集成

建议在现有主布局导航中增加一级菜单项：

- `Skills`

点击后默认进入：

- `/skills`

`/skills` 首页建议展示：

- 新建 Skill
- 最近编辑的 Skill
- 最近 patch
- 最近工作流草稿
- 快速进入已有 Skill

## 8.4 前端状态流

前端状态流建议固定：

1. 加载 `session + skill structure`
2. 进入某模块
3. 发送用户消息
4. 获取 intent
5. 获取 patch
6. 获取 validation
7. 预览 diff
8. 用户点击应用
9. 刷新工作台状态

需要明确区分以下状态：

- `loading_structure`
- `generating_patch`
- `validating_patch`
- `applying_patch`
- `patch_error`
- `validation_error`

## 8.5 前端组件职责

### `WorkbenchChatPanel`

- 输入消息
- 展示系统建议
- 展示澄清问题

### `WorkbenchModuleNav`

- 模块切换
- 模块状态展示

### `WorkbenchPatchPreview`

- patch 摘要
- before/after
- diff 视图

### `WorkbenchValidationPanel`

- 展示结构验证
- 展示样例验证
- 展示历史回放结果

### `WorkflowPreviewPanel`

- 展示 workflow patch 变化
- 提供跳转到完整画布能力

## 8.6 页面切换策略

建议保留现有：

- `SkillEdit.vue`
- `SkillChat.vue`
- `PlaybookEdit.vue`

新工作台先以新增入口方式接入，不立即替换现有页面。

## 9. 验证设计

## 9.1 结构验证

复用或扩展现有 `validation_service`：

- 参数合法性
- 规则可解析性
- 输出字段完整性
- workflow 结构完整性

## 9.2 样例验证

使用现有测试样例定义：

- before
- after
- 对比结果

## 9.3 历史回放验证

对最近 N 条真实执行记录做 before / after 对比。

建议默认：

- 20 条
- 50 条

可切换

## 10. 数据库与缓存设计

## 10.1 数据库总体原则

数据库不存“最终 Markdown”作为唯一来源，数据库主要存：

- 会话状态
- patch 草稿
- 验证结果
- 引用关系
- AI 调用元数据

真正的 Skill 文件仍通过现有 Git / 文件系统体系落地。

## 10.2 建议新增表：`skill_workbench_sessions`

字段建议：

- `id`
- `skill_id`
- `user_id`
- `mode`
  - `novice`
  - `pro`
- `current_module`
- `status`
  - `active`
  - `archived`
- `created_at`
- `updated_at`

用途：

- 标识一个工作台会话
- 保存当前工作状态

## 10.3 建议新增表：`skill_workbench_messages`

字段建议：

- `id`
- `session_id`
- `role`
  - `user`
  - `assistant`
  - `system`
- `content`
- `intent_json`
- `created_at`

用途：

- 保存工作台对话历史
- 支持回显和追溯

## 10.4 建议新增表：`skill_workbench_patches`

字段建议：

- `id`
- `session_id`
- `skill_id`
- `target_module`
- `intent`
- `summary`
- `patch_json`
- `diff_preview_json`
- `status`
  - `draft`
  - `validated`
  - `applied`
  - `discarded`
- `created_by`
- `created_at`
- `applied_at`

用途：

- 保存单模块 patch
- 支持 diff 回显
- 支持历史回退

## 10.5 建议新增表：`skill_workbench_validation_runs`

字段建议：

- `id`
- `patch_id`
- `skill_id`
- `structural_report`
- `sample_case_report`
- `replay_report`
- `impact_summary`
- `can_apply`
- `created_at`

用途：

- 保存 patch 的验证结果
- 用于审核和历史追踪

## 10.6 建议新增表：`skill_workbench_references`

字段建议：

- `id`
- `patch_id`
- `source_type`
  - `skill_module`
  - `workflow_node`
  - `template`
- `source_id`
- `source_module`
- `reference_mode`
  - `copy_structure`
  - `copy_format`
  - `copy_logic`

用途：

- 记录 patch 的引用来源

## 10.7 建议新增表：`skill_workbench_ai_runs`

字段建议：

- `id`
- `session_id`
- `patch_id`
- `run_type`
  - `intent`
  - `draft_generation`
  - `module_patch`
  - `workflow_patch`
  - `validation_summary`
- `model_id`
- `prompt_key`
- `token_count`
- `latency_ms`
- `success`
- `error_message`
- `created_at`

用途：

- 追踪 AI 成本和质量
- 支持后续优化

## 10.8 Redis 缓存设计

建议使用 Redis 存以下缓存：

- session 快照缓存
- 引用模块缓存
- AI patch 缓存
- validation 结果缓存
- workflow 预览缓存

## 10.9 Redis Key 设计

建议 key 规范：

- `sf:wb:session:{session_id}`
- `sf:wb:skill:{skill_id}:structure`
- `sf:wb:patch:{patch_id}`
- `sf:wb:validate:{patch_id}`
- `sf:wb:ref:{source_type}:{source_id}:{module}`
- `sf:wb:ai:{prompt_key_hash}`

## 10.10 Redis TTL 建议

- session 快照：30 分钟
- Skill 结构缓存：5 分钟
- patch 缓存：30 分钟
- validation 缓存：30 分钟
- 引用模块缓存：10 分钟
- AI 返回缓存：10 分钟到 1 小时，按 prompt 类型区分

## 10.11 缓存失效策略

以下情况必须失效相关缓存：

- patch 被应用
- Skill 文件被外部修改
- workflow 被重新保存
- 引用源 Skill 更新

## 10.12 缓存降级策略

Redis 不可用时：

- session 和 patch 仍可走数据库
- AI 结果不缓存但仍可调用
- validation 结果不缓存但仍可返回
- 前端不应出现整体不可用

## 11. 与现有代码的集成策略

## 11.1 可直接复用的部分

- `app/skills/parser.py`
- `app/testing/agent_chat.py`
- `app/skills/validation_service.py`
- `app/skills/generation_service.py`
- `web/src/pages/playbook/PlaybookEdit.vue`

## 11.2 不建议直接复用的部分

以下部分不建议直接拿来当最终工作台逻辑：

- 纯聊天页的简单消息收发逻辑
- 直接生成全文 Markdown 的生成模式
- 页面内分散的零散 AI 调用

## 12. 分阶段实施建议

### Phase 1

- 新建 workbench router 和 session
- 实现 intent 解析
- 实现 params / rules patch
- 实现 diff 预览
- 建表：session / message / patch
- 建 Redis key 基线

### Phase 2

- 实现 output_table patch
- 接入样例验证
- 增加 patch 存储
- 增加 validation run 表
- 接入 AI run 日志

### Phase 3

- 实现 workflow patch
- 对接现有 Playbook 编辑器
- 增加历史回放验证
- 增加 workflow 预览缓存
- 增加引用关系存储

### Phase 4

- 增加跨 Skill 模块引用
- 增加节点级调优
- 增加模板化复用
- 增加 AI fallback 和预算控制

## 13. 风险与约束

### 风险一：模型输出不稳定

应对：

- 强制结构化 patch 输出
- 增加严格 schema 校验

### 风险二：对话过度膨胀

应对：

- 每轮只改一个模块
- 不确定时只追问一次

### 风险三：工作流先于 Skill 稳定

应对：

- 先完成单 Skill 可调闭环
- 再做工作流 patch

### 风险四：验证成本过高

应对：

- 验证分层
- 默认先跑轻量验证，再允许用户触发深度回放

## 14. 一句话总结

这套技术设计的本质是：

`把 Skill 从黑盒对话能力，升级为一个可被对话驱动、但必须通过结构化 patch、验证和编排来落地的系统。`
