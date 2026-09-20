# Skill Studio / Workbench 状态机与 composables 图谱（v2.9.0+）

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


> **更新纪要**（截至 v2.9.0）
> - ✅ D1 `app/skills` 分层：31 文件进 5 package，根目录 shim 已物理删除
> - ✅ B2 `useSkillStudio()` facade store：`session / ui / content` sub-store + 聚合属性
> - ✅ B3 3 个 aggregate barrel：`useSkillPublishing` / `useStudioObservability` / `useStudioLifecycleUnified`
> - ⚠️ SkillStudio.vue **call 点**仍独立调 9 个原子 composable（仅 import 路径收敛），完整 mega-deps 迁移留给 v2.9.x
> - ⚠️ 4 个小 composable（Recent/Draft/Blocks/ViewHandlers）合并留 v2.9.x

## 一、store 合一 facade（v2.8.3+）

```
┌──────────────────────────────────────────────────────────┐
│ useSkillStudio()  ←── 唯一入口（src/stores/skillStudio.ts）│
│                                                          │
│   session (= useWorkbenchStore)    ← AI 对话 / refs / patch │
│   ui      (= useUIStore)           ← 布局 / viewMode / mode │
│   content (= useDocumentStore)     ← doc / dirtyModules     │
│                                                          │
│   聚合属性（单一真相源）:                                │
│     isDirty     = content.dirtyModules.size > 0          │
│     isCreate    = ui.studioMode === 'create'             │
│     isReadOnly  = !ui.editMode                           │
│     saving      = !!session.studioSaving                 │
│     skillId     = session.skillId                        │
│     skillLoaded = !!session.studioLoaded                 │
└──────────────────────────────────────────────────────────┘
```

**边界约定**：
- `session` 与 API 对接（发请求 / 存响应）；不碰 doc 内容
- `content` 只管 Skill 内容的增删改 + dirty 标记
- `ui` 只管"页面长啥样"；跟内容无关

**兼容**：老 `useWorkbenchStore` / `useUIStore` / `useDocumentStore` 仍可直接 import（同一 Pinia 实例），但新代码**一律走 facade**。

## 二、composable aggregate barrel（v2.9.0+）

v2.8.2 时是 22 个独立 composable 的扁平导入；v2.9.0 收敛成 3 个 aggregate barrel + 保留独立的小 composable：

```
web/src/composables/
├── useSkillPublishing.ts  ── aggregate: 发布相关
│   re-exports: { useStudioShadow, useStudioPublish, useStudioReviewSubmit }
│   facade    : useSkillPublishing(deps) → { shadow, publish, review }
│
├── useStudioObservability.ts  ── aggregate: 观测相关
│   re-exports: { useStudioValidation, useStudioCommitHistory, useSkillStudioTelemetry }
│   facade    : useStudioObservability(deps) → { validation, history, telemetry }
│
├── useStudioLifecycleUnified.ts  ── aggregate: 生命周期 + 保存
│   re-exports: { useStudioSave, useStudioContentActions, useSkillStudioLifecycle }
│   facade    : useStudioLifecycleUnified(deps) → { save, content }
│
├── skillstudio/              ── 主干 5 个（保留独立）
│   ├── useSkillStudioDocument.ts   — doc 模块计算 / dirty 聚合
│   ├── useSkillStudioSession.ts    — AI session / lock / perspective
│   ├── useSkillStudioFiles.ts      — 非 SKILL.md 文件的 CRUD
│   ├── useSkillStudioReview.ts     — 审核详情展示（不同于 ReviewSubmit）
│   └── useSkillStudioAI.ts         — 工具调用 / permission / stream turn
│
└── [独立小件，未入 aggregate，后续 v2.9.x 可能合并]
    ├── useStudioKeyboard.ts        — 全局快捷键
    ├── useStudioVisualization.ts   — Mermaid / Flow
    ├── useStudioExplain.ts         — 讲解 Perspective 视图
    ├── useStudioCommand.ts         — ⌘K Palette
    ├── useSkillStudioRecent.ts     — localStorage 最近访问（可合）
    ├── useStudioDraft.ts           — 编辑态 autosave（当前唯一保留方案）
    ├── useStudioBlocks.ts          — block CRUD（可并入 ContentActions）
    └── useStudioViewHandlers.ts    — 杂项 UI 事件（可并入 Keyboard）
```

**双模式调用**（调用方按需选）：

```ts
// 模式 A：按需抓（当前 SkillStudio.vue 的方式）
import { useStudioShadow } from '@/composables/useSkillPublishing'
const shadow = useStudioShadow({ wb, skillApi })

// 模式 B：一锅端（新代码推荐，自动处理 dep ordering）
import { useSkillPublishing } from '@/composables/useSkillPublishing'
const publishing = useSkillPublishing({ wb, studioDoc, ui, router, skillApi, reviewApi, validationResult, refreshAfterSubmitReview })
const { shadow, publish, review } = publishing
```

9 个被 aggregate 覆盖的 composable 顶部保留"推荐从 barrel 导入"说明；
不再使用 `@deprecated` 标记，避免把仍在 aggregate 内部复用的实现误报成"不可用"。

## 三、数据流（典型编辑场景）

```
用户键入 → Monaco change
         → studio.content.doc.meta.xxx 更新（原 documentStore.doc.meta）
         → studio.content.dirtyModules 加该 module
         → studio.isDirty 变 true（聚合属性，自动触发 topBar 显示 *）
         → [autosave] useStudioDraft.save 300ms debounce → localStorage

点"保存"
         → useStudioSave.handleSave()（现在从 @/composables/useStudioLifecycleUnified 导入）
         → diff 预览 / 本地校验 / skillApi.saveStructured(skillId, doc)
         → 后端 git commit + parser.validate
         → studio.content.lastSavedAt 更新 + dirtyModules.clear()
         → useStudioDraft.clear() 清 localStorage
         → useStudioCommitHistory 刷新 commit log
         → useStudioValidation 重跑 lint
         → studio.isDirty 回 false，topBar 去 *
```

## 四、创建模式 vs 编辑模式

v2.8.2 起：**创建模式**由独立 `SkillCreationWizard.vue` 负责（`/skills/new`），**编辑模式**由 `SkillStudio.vue` 负责（`/skills/:id`）。不再同页切 `isCreate` 分支。

| 模式 | 路由 | 主组件 | 状态源 |
|------|------|-------|--------|
| 创建 | `/skills/new` | `SkillCreationWizard.vue` | 组件内部 ref + localStorage |
| 编辑 | `/skills/:id` | `SkillStudio.vue` | `useSkillStudio()` facade |
| Legacy 创建（已弃） | `/skills/new-legacy` | `SkillStudio.vue`（isCreate=true） | 兼容老行为，不推荐 |

## 五、v2.9.x 计划中的收敛

1. **SkillStudio.vue call 点**：9 处独立 call → 3 处 aggregate 一锅端（用 mega-deps 对象 + aggregate 内部处理 ordering），预计 SkillStudio.vue 从 900 行 → < 600 行
2. **4 个小 composable 合并**：Recent/Draft/Blocks/ViewHandlers 并入现有 barrel 或内联
3. **物理删除 9 个 deprecated 文件**：call 点全部切到 aggregate 后，原子 composable 可从磁盘删除（barrel 内部保留实现）
4. **更新对应测试**

触发条件：给 SkillStudio.vue 一个专门 sprint（预计 2-3 天）

## 六、反例

- ❌ 新代码**不要**直接 import `useWorkbenchStore / useUIStore / useDocumentStore` —— 走 `useSkillStudio()`
- ❌ 新代码**不要**用 `from '@/composables/useStudioShadow'` —— 走 `from '@/composables/useSkillPublishing'`
- ❌ 绕过 `documentStore.updateModule(...)` 直接改 `doc.xxx` —— 破坏 dirty 标记
- ❌ Composable 文件里直接 `import { skillApi } from '@/api'` —— 应从 deps 注入，方便测试
