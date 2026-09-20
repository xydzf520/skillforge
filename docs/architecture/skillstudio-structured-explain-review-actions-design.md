# SkillStudio 结构化讲解 + 审批动作设计

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


> 日期：2026-04-11
> 分支：`feature/aiclawcode-deep-fusion-base`
> 原项目状态记录：已完成（不代表公开版验收）
> 目标：在三视图基础上，把 `Explain` 和 `Review` 两个视角从“能看”推进到“能用”。

---

## 1. 问题定义

上一轮已经完成：

1. `SkillStudio` 三视图骨架
2. `document.ts` 作为单一文档真相源
3. `coding-agent explain mode`

但当前 Explain / Review 仍有两个明显缺口：

1. **Explain 结果还不够结构化**
   - 当前主要是静态摘要卡
   - 对“触发条件 / 决策路径 / 参数影响 / 测试信心”展示还不够集中
   - 缺少面向业务的结构化解释 pack

2. **Review 动作还没内聚到 SkillStudio**
   - 当前审批动作仍主要在独立的 `ReviewDetail.vue`
   - `SkillStudio` 的 review 视角只能看门禁、风险、验证摘要
   - 缺少“当前待审单是什么 / 谁提交的 / 改了什么 / 我现在能直接批准还是驳回”的结构化动作面板

---

## 2. 目标状态

### 2.1 Explain 视角

Explain 视角需要提供一个稳定的 **Explain Pack**，至少包含：

1. `executive_summary`
2. `decision_ladder`
3. `parameter_impacts`
4. `output_contract`
5. `test_confidence`

用户应该能在不看源码、不看规则表单的前提下，回答以下问题：

- 这个 Skill 在解决什么问题？
- 它先看什么，再看什么？
- 哪几个参数最影响结果？
- 它输出什么给谁？
- 目前测试覆盖看起来是否可靠？

### 2.2 Review 视角

Review 视角需要提供一个稳定的 **Review Action Pack**，至少包含：

1. `current_review`
2. `semantic_diff_summary`
3. `ai_review_summary`
4. `comment_timeline`
5. `approval_controls`

用户应该能在同一页完成：

- 识别当前待审单
- 看到主要变化
- 理解 AI 风险意见
- 查看与补充评论
- 解决已处理评论
- 直接批准或驳回

---

## 3. 前端设计

### 3.1 Explain Pack

Explain Pack 本轮先由前端基于 `doc` + `readiness` 直接派生，不新增后端接口。

结构：

```ts
ExplainPack = {
  executive_summary: string
  trigger_summary: string
  decision_ladder: Array<{
    id: string
    title: string
    summary: string
  }>
  parameter_impacts: Array<{
    name: string
    value: unknown
    impact: string
  }>
  output_contract: Array<{
    name: string
    recipient: string
    format: string
  }>
  test_confidence: {
    total_cases: number
    covered_rules: number
    uncovered_rules: number
    summary: string
  }
}
```

组件：

- `WorkbenchExplainView.vue`

变化：

1. 头部维持高层信息
2. 新增 `Explain Pack` 四块卡片
3. “决策逻辑”从普通列表升级为 `decision_ladder`
4. “测试摘要”升级为 `test_confidence`

### 3.2 Review Action Pack

Review Action Pack 由 `SkillStudio.vue` 负责加载，`WorkbenchReviewView.vue` 负责展示与触发动作。

结构：

```ts
ReviewContext = {
  review: ReviewDetail | null
  semantic_diff: SemanticDiff | null
  ai_review: AiReview | null
}
```

数据来源：

1. `reviewApi.list({ skill_id, status: 'pending' })`
2. 取当前最相关 review（优先 `reviewer=me`）
3. `reviewApi.get(review.id)`
4. `reviewApi.semanticDiff(review.id)`

组件：

- `WorkbenchReviewView.vue`

变化：

1. 新增“当前待审单”卡片
2. 新增“主要变化”卡片
3. 新增“AI 风险意见”卡片
4. 新增“评论时间线”卡片
5. 新增“结构化审批动作”表单：
   - approve: `rating`, `feedback_type`
   - reject: `reject_reason`, `reason`

### 3.3 页面编排

`SkillStudio.vue` 新增：

1. `reviewContext`
2. `reviewLoading`
3. `reviewActing`
4. `reviewCommentDraft`
5. `loadReviewContext()`
6. `handleApproveCurrentReview()`
7. `handleRejectCurrentReview()`
8. `handleReviewComment()`
9. `handleResolveReviewComment()`

策略：

1. 进入 `review` 视角时自动加载 review context
2. 审批动作成功后自动刷新 review context
3. 提交审核成功后，如果当前在 `review` 视角，也立即刷新 review context
4. 评论新增或 resolve 后自动刷新 review context
5. semantic diff 允许查看更多条目，而不是固定 6 条

---

## 4. 后端设计

本轮后端原则：**优先复用现有接口，不新建 schema。**

### 4.1 复用接口

直接复用：

1. `GET /api/reviews/`
2. `GET /api/reviews/{id}`
3. `POST /api/reviews/{id}/semantic-diff`
4. `POST /api/reviews/{id}/approve`
5. `POST /api/reviews/{id}/reject`
6. `POST /api/reviews/{id}/comment`
7. `POST /api/reviews/{id}/comments/{comment_id}/resolve`

### 4.2 不新增接口的原因

Explain Pack：

- 当前文档真相源已在前端
- 不需要再走一层纯聚合 API

Review Action Pack：

- 现有 review API 已足够组合
- 当前需要先把产品闭环走通，而不是过早抽象新的聚合端点

---

## 5. AI 设计

### 5.1 Explain 视角

Explain 视角继续复用上一轮的：

- `coding_agent_explain@v1`

当前作用：

1. 用户在 explain 视角打开 assistant 时，系统 prompt 已切到 explain mode
2. assistant 默认语言是解释/归纳，不是修改

### 5.2 Review 视角

Review 视角继续复用：

- `coding_agent_review@v1`

本轮不改 prompt 结构，只改 SkillStudio 产品交互。

---

## 6. 缓存设计

本轮不新增 Redis key。

复用：

1. `reviews:list:*`
2. `reviews:detail:*`
3. semantic diff 仍走已有 review detail/AI review 缓存链路

前端只新增页面内的短生命周期状态：

- `reviewContext`
- `reviewLoading`
- `reviewActing`

Explain Pack 完全前端本地计算，不进 Redis。

---

## 7. 数据库设计

本轮数据库目标：

- 明确 **无 migration**

原因：

1. review 审批字段已足够
2. semantic diff 和 ai review 已能通过现有接口获取
3. 评论与 resolve 已有模型和接口
4. Explain Pack 不需要落库

---

## 8. 测试设计

### 8.1 前端

新增：

1. `WorkbenchExplainView`
   - 展示 explain pack
   - 参数/测试摘要存在时正确渲染

2. `WorkbenchReviewView`
   - review context 渲染
   - 无 review 时 empty state
   - 有权限时显示 approve/reject 动作区
   - 评论时间线渲染
   - comment / resolve 动作

3. `SkillStudio`
   - 切到 review 视角触发 review context 加载

### 8.2 后端

本轮不新增新的 review route，只复用现有 API。
后端测试主要仍依赖：

- `tests/test_reviews.py`

如发现组合层行为不稳定，再补 API 级测试。

### 8.3 真实运行

生产式 smoke 需要扩展：

1. `GET /api/reviews/`
2. `GET /api/reviews/?reviewer=me`
3. 维持 explain/review 路由页面访问

---

## 9. 风险

1. `reviewer=me` 可能在空测试库下返回空列表
   - 允许 empty，但必须返回 `200`

2. `WorkbenchReviewView` 如果承担太多 fetch 逻辑，会变成肥组件
   - 所以本轮把 fetch 留在 `SkillStudio.vue`

3. Explain Pack 若完全靠前端推导，业务语义可能偏模板化
   - 本轮先求稳定结构
   - 后续若需要更强解释能力，再加 explain summary API

---

## 10. 完成标准

满足以下条件才算完成：

1. Explain 视角有清晰 Explain Pack
2. Review 视角能展示当前 review context
3. Review 视角能直接 approve / reject
4. Review 视角能直接 comment / resolve
5. Review 视角能显示 AI Reviewer 深层结果与结果分布变化
6. semantic diff drilldown 不再只显示前 6 条，且支持 diff 行号定位
7. `ReviewList` / `Todo` / 旧 `/review/:id` 已收口到 `SkillStudio` review 视角
8. 前端测试通过
9. 本地生产式 smoke 覆盖 review API 与 explain/review 页面
