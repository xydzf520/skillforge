# SkillStudio AI Explain Pack 设计

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


> 日期：2026-04-11
> 分支：`feature/aiclawcode-deep-fusion-base`
> 原项目状态记录：已完成（不代表公开版验收）
> 目标：把 Explain 视角从“前端静态推导”升级为“后端结构化 Explain Pack + AI 增强”。

---

## 1. 问题定义

当前 Explain 视角已经具备结构化外观，但本质仍是：

1. 前端基于 `doc` 直接拼字符串
2. 解释质量取决于模板逻辑，而非 AI 理解
3. 没有 explain 专用缓存
4. 生产式 smoke 还没有覆盖 explain API

这会带来两个问题：

1. 文档结构一变，Explain 视角容易退化成机械摘要
2. Explain 视角没有真正形成“前端/后端/AI/缓存”完整产品链路

---

## 2. 目标状态

新增后端 Explain Pack：

```ts
ExplainPack = {
  executive_summary: string
  trigger_summary: string
  decision_ladder: Array<{ id: string, name: string, summary: string }>
  parameter_impacts: Array<{ name: string, value: unknown, impact: string }>
  output_contract: Array<{ name: string, recipient: string, format: string }>
  test_confidence: {
    total_cases: number
    covered_rules: number
    uncovered_rules: number
    summary: string
  }
  source: 'ai' | 'fallback'
  prompt_hash: string | null
}
```

原则：

1. 后端先构建 deterministic base pack
2. AI 再对 base pack 做增强解释
3. AI 失败时回退到 deterministic pack
4. 前端 ExplainView 优先展示后端 pack

---

## 3. 前端设计

### 3.1 Explain 视角

`WorkbenchExplainView.vue` 新增：

- `explainPack` prop

优先级：

1. 有 `explainPack` → 直接渲染
2. 无 `explainPack` → 回退到本地推导

### 3.2 页面编排

`SkillStudio.vue` 新增：

1. `explainPack`
2. `explainPackLoading`
3. `loadExplainPack()`

策略：

1. 切换到 `explain` 视角时自动加载
2. 同一 Skill 切回 explain 时复用已加载数据
3. 如果 skill 变更或重新加载，explainPack 失效后重取

---

## 4. 后端设计

新增接口：

- `GET /api/skills/{skill_id}/explain-pack`

位置：

- `app/skills/router.py`
- `app/skills/ai_service.py`

流程：

1. 读取 Skill 详情
2. 构建 deterministic base pack
3. 用 PromptRegistry 渲染 `skill_explainer@v1`
4. 调 `call_llm_cached()`
5. 合并 AI 结果
6. 写缓存并返回

---

## 5. AI 设计

新增 Prompt：

- `app/common/prompts/skill_explainer@v1.md`

AI 目标：

1. 用业务语言重写 executive summary
2. 强化 parameter impact 与 test confidence 的解释
3. 不改结构，只填内容

返回格式严格 JSON。

---

## 6. 缓存设计

新增 explain pack 缓存：

- key: `skills:explain_pack:{skill_id}:{version_or_updated_at}`

TTL：

- 10 分钟

AI 子结果仍复用 `call_llm_cached` 自带缓存。

---

## 7. 数据库设计

本轮 **无 migration**。

Explain Pack 不落库，只走：

1. 现有 Skill 数据
2. Prompt hash
3. Redis 缓存

---

## 8. 测试设计

### 前端

1. `WorkbenchExplainView`
   - 有 explainPack 时优先渲染 explainPack

### 后端

1. `tests/test_skill_explain_pack.py`
   - deterministic fallback
   - AI 成功增强
   - 部门隔离

### 真实运行

在 smoke 中：

1. 创建一个最小 Skill
2. 调 `/api/skills/{id}/explain-pack`
3. 验证返回 `executive_summary`

---

## 9. 完成标准

1. Explain 视角默认走后端 Explain Pack
2. AI 失败时仍有 fallback 内容
3. Explain Pack 带 `source` 和 `prompt_hash`
4. 前端测试、后端测试、生产式 smoke 全过
