# SkillForge 项目级改造蓝图

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


> 日期：2026-04-11
> 分支：`feature/aiclawcode-deep-fusion-base`
> 状态：实施中
> 模式：归零模式

---

## 1. 目标

本轮不是继续堆功能，而是做项目级收敛。

目标分三层：

1. **P0：热点大文件拆分**
   - `app/skills/router.py`
   - `app/skills/service.py`
   - `app/workbench/service.py`
   - `web/src/pages/skill/SkillStudio.vue`

2. **P1：过渡层与双轨实现清理**
   - `web/src/stores/workbench.ts`
   - `app/skills/ai_service.py`
   - `web/src/pages/review/ReviewDetail.vue`

3. **P2：基础设施收口**
   - 缓存策略
   - 启动治理
   - 测试拆域
   - smoke 脚本模块化

---

## 2. 当前热点

### 后端

| 文件 | 行数 | 问题 |
|---|---:|---|
| `app/workbench/service.py` | 1250 | review_context/patch/session/chat/validation 已拆出，剩 coding-agent orchestration 与少量 façade |
| `app/skills/router.py` | 2131 | CRUD/files/质量/AI/runtime 全混在一起 |
| `app/skills/service.py` | 1969 | Skill 核心能力与文件/锁/回滚等混在一起 |
| `app/skills/ai_service.py` | 82 | 主文件已降为 façade，feature 已全部拆到独立模块 |

### 前端

| 文件 | 行数 | 问题 |
|---|---:|---|
| `web/src/pages/skill/SkillStudio.vue` | 1389 | 剩余 orchestration 仍偏重，但 session/review/files/AI/telemetry 已拆到 composables |
| `web/src/stores/workbench.ts` | 242 | 主 store 已转为 façade，session/patch/validation/reference 已拆到独立模块 |

---

## 3. 改造主题

### P0

1. `skills` 域拆分
   - `skills_core`
   - `skills_files`
   - `skills_quality`
   - `skills_ai`
   - `skills_runtime`

2. `workbench` 域拆分
   - `session_orchestrator`
   - `patch_service`
   - [x] `validation_service`
   - `review_context_service`
   - [x] `coach_service`

3. `SkillStudio.vue` 拆 composables
   - [x] `useSkillStudioSession`
   - [x] `useSkillStudioReview`
   - [x] `useSkillStudioFiles`
   - [x] `useSkillStudioAI`
   - [x] `useSkillStudioTelemetry`

### P1

4. `workbench.ts` 瘦身
   - [x] `workbenchSessionStore`
   - [x] `workbenchPatchStore`
   - [x] `workbenchValidationStore`
   - [x] `workbenchReferenceStore`

5. `ai_service.py` 拆 feature
   - [x] 参数调优
   - [x] 参数证据
   - [ ] 阈值推导
   - [x] 分支建议
   - [x] 模板推荐
   - [x] explain pack

6. [x] `ReviewDetail.vue` 正式退役

### P2

7. [x] 缓存策略统一 facade
8. [x] `main.py` 启动治理拆分
9. 测试拆域
10. [x] `smoke_local_production_stack.py` 模块化

---

## 4. 原则

1. **不改 API 契约，优先改代码组织**
2. **先抽最独立的域，再抽强耦合域**
3. **先让旧文件变 façade，再逐步降体积**
4. **每步都要有测试和生产式 smoke**

---

## 5. 顺序

1. `skills` 路由拆分
2. `skills` 服务拆分
3. `workbench` 服务拆分
4. `SkillStudio` composable 化
5. `workbench.ts` 瘦身
6. `ai_service.py` feature 化
7. `ReviewDetail.vue` 退役
8. 缓存/启动/测试/smoke 基础设施收口
