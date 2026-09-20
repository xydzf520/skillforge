# SkillStudio 三视图 + 单一文档真相源设计

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


> 日期：2026-04-11
> 分支：`feature/aiclawcode-deep-fusion-base`
> 原项目状态记录：已完成（不代表公开版验收）
> 目标：把 `SkillStudio` 从“过渡态 IDE 页面”收口为统一工作台，落地：
> 1. 单一文档真相源
> 2. 三视图（编辑 / 讲解 / 审批）
> 3. AI 模式感知

---

## 1. 问题定义

当前 `SkillStudio` 已经有大量能力，但仍存在 3 个结构性问题：

1. 文档状态分裂
   - `document.ts` 已存在
   - `ide.ts` 仍直接操作 `workbench.skillDocument`
   - `workbench.ts` 还持有一份自己的 `skillDocument`

2. 三视图未真正落地
   - 页面有 `studioMode`
   - 但主要仍表现为 `block / code`
   - `review` 只是“禁发消息/禁保存”的半收口状态

3. AI 只感知 `edit/review`
   - `review` prompt 已存在
   - 没有正式的 `explain` 模式
   - 讲解视图无法稳定向 AI 传达“解释优先”的系统约束

---

## 2. 目标状态

### 2.1 工作台视图层次

`SkillStudio` 需要分成两层：

1. **工作视角**
   - `edit`
   - `explain`
   - `review`

2. **编辑表现**
   - `block`
   - `code`

关系：

- `block / code` 只属于 `edit`
- `explain / review` 是独立视角，不与 `block / code` 混为一个开关

### 2.2 单一文档真相源

目标约束：

1. `document.ts` 是唯一事实源
2. `ide.ts` 变为 façade，只负责页面级 orchestration
3. `workbench.ts.skillDocument` 降级为兼容镜像，不再作为主写入源
4. 页面和组件默认只从 `document.ts` 或 `ide.ts` 读写文档

### 2.3 三视图语义

| 视图 | 主要用户 | 可写 | 主要目标 |
|------|---------|------|---------|
| `edit` | `admin / ai_engineer / aibp` | 是 | 修改 Skill |
| `explain` | 任意有查看权限的用户 | 否 | 理解业务逻辑 |
| `review` | `biz_owner / director / reviewer` | 否 | 看变更、风险、验证摘要 |

---

## 3. 前端设计

### 3.1 Store 分层

#### `document.ts`

职责：

- 持有模块化文档对象
- 提供 `flatDocument` 投影
- 负责 `load / reset / update / applyPatch / toStructuredPayload`

新增要求：

1. 提供 `flatDocument` 计算属性
2. 提供 `setFromFlatDocument(doc)` 与 `syncToCompatibilityStore(fn)`
3. 所有模块更新都只改 `document.ts`

#### `ide.ts`

职责调整：

- 保留页面级状态：`mode / loaded / saving / createForm / activeModule`
- 不再持有真实文档
- 所有 `doc / updateModule / saveDirectEdits / createSkill` 改为委托 `document.ts`

#### `ui.ts`

新增：

- `studioPerspective: 'edit' | 'explain' | 'review'`

保留：

- `viewMode: 'block' | 'code'`

规则：

1. `studioPerspective !== 'edit'` 时忽略 `viewMode`
2. `restore/persist` 统一保存 `studioPerspective`

### 3.2 页面结构

`SkillStudio.vue` 中央区按 `studioPerspective` 切换：

1. `edit`
   - 现有 `WorkbenchEditorSurface`
   - 内部继续支持 `block / code`

2. `explain`
   - 新增 `WorkbenchExplainView`
   - 展示：
     - 业务目标
     - 决策规则的自然语言解释
     - 参数与影响
     - 输出与测试摘要

3. `review`
   - 新增 `WorkbenchReviewView`
   - 展示：
     - 变更摘要
     - 质量/验证摘要
     - 风险与发布门禁
     - 审批建议入口

### 3.3 顶栏交互

`WorkbenchTopBar.vue` 新增一组视角切换：

- `编辑`
- `讲解`
- `审批`

规则：

1. `create` 模式固定 `edit`
2. `biz_owner / director` 打开页面默认进入 `review`
3. `block / code` 开关只在 `edit` 时显示
4. `review` 和 `explain` 下不显示编辑锁入口与保存入口

---

## 4. 后端设计

本轮后端不新增新的核心数据表，主要做协议与 AI 模式对齐：

1. `coding_agent` 会话模式扩展
   - 现有：`edit / review`
   - 新增：`explain`

2. `session_service` 根据模式选择 Prompt section
   - `edit` → `coding_agent_workbench`
   - `review` → `coding_agent_review`
   - `explain` → `coding_agent_explain`

3. `workbench` 与 `skills` API 无需新增主接口
   - 三视图主要复用现有读取接口
   - 文档统一发生在前端 store 层，不改变保存协议

---

## 5. AI 设计

### 5.1 Prompt 分层

新增：

- `coding_agent_explain@v1`

目标：

1. 优先解释和归纳，而不是修改
2. 输出业务语言
3. 允许做引用、比较、总结
4. 默认不主动发起写操作

### 5.2 前端模式透传

现状：

- `SkillStudio` 已把 `studioMode` 透传给 coding-agent

本轮调整：

1. 透传值改为真实视角：`edit / explain / review`
2. 解释视图下 assistant placeholder 与交互文案切换
3. assistant header 显式显示当前视角

---

## 6. 缓存设计

本轮不引入新缓存层，但需要收口两个点：

1. `ui.ts` 本地缓存新增 `studioPerspective`
2. 由于 `document.ts` 成为真相源，页面级 draft 恢复和 `sessionStorage` 快照要以 `document.ts` 为基础

不新增 Redis key。

---

## 7. 数据库设计

本轮数据库目标是“确认不需要 schema 改动”：

1. 三视图是前端视角，不是新实体
2. Explain 模式不需要新表
3. 审批模式沿用 review/todo/audit 既有数据
4. AI explain mode 的 prompt 版本仍通过现有 `prompt_hash` / `usage_logs` / `audit_logs` 追踪

因此：

- **无 migration**

---

## 8. 测试设计

### 8.1 前端

新增/调整：

1. `ui.ts`
   - `studioPerspective` 默认值
   - `persist/restore`

2. `document.ts`
   - `flatDocument`
   - `setFromFlatDocument`
   - `updateModuleStructured`

3. `workbench.test.ts`
   - 顶栏三视图切换
   - `review/explain` 下隐藏编辑入口

4. `SkillStudio.test.ts`
   - create 默认 `edit`
   - `biz_owner/director` 默认 `review`
   - explain 视图只读

### 8.2 后端

新增：

1. `tests/test_coding_agent_bridge.py`
   - explain mode 改变 prompt hash
   - explain mode 不复用 review prompt

### 8.3 真实运行

复用：

- `scripts/smoke_local_production_stack.py`

扩展目标：

1. 页面路由返回正常
2. 登录后 `Skills` 与 `SkillStudio` 主路由正常
3. Explain/Review 视图在生产式构建下可进入

---

## 9. 实施顺序

1. 先落文档与测试约束
2. 再把 `document.ts` 提升为真相源
3. 再引入 `studioPerspective`
4. 再接 Explain / Review 视图组件
5. 最后补 AI explain mode 与 smoke

---

## 10. 完成标准

满足以下条件才算完成：

1. `SkillStudio` 文档写入只经由 `document.ts`
2. `ide.ts` 不再直接以 `workbench.skillDocument` 为主状态
3. `workbench.ts` 不再保存第二份文档副本，只镜像 `document.ts`
4. 顶栏存在 `编辑 / 讲解 / 审批` 三视图
5. `explain` 和 `review` 有实际中央视图，不只是 tag
6. `coding-agent` 支持 explain mode
7. 前端测试、后端 explain mode 测试、本地生产式 smoke 全过
