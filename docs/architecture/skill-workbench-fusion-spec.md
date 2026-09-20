# Skill Workbench 融合方案 — 需求与开发规格书

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


> **版本**: v1.0 | **日期**: 2026-04-06 | **状态**: 方案评审
>
> 本文档是 Skill 编辑界面 Cursor 式融合重构的完整开发规格书，覆盖前端、后端、数据库、缓存、AI、测试验证六大维度。

---

## 目录

1. [项目背景与目标](#1-项目背景与目标)
2. [现状分析与问题诊断](#2-现状分析与问题诊断)
3. [融合方案总览](#3-融合方案总览)
4. [前端方案](#4-前端方案)
5. [后端方案](#5-后端方案)
6. [数据库方案](#6-数据库方案)
7. [缓存方案](#7-缓存方案)
8. [AI 方案](#8-ai-方案)
9. [测试验证方案](#9-测试验证方案)
10. [实施计划与里程碑](#10-实施计划与里程碑)
11. [风险与降级策略](#11-风险与降级策略)

---

## 1. 项目背景与目标

### 1.1 背景

SkillForge 当前的 Skill 编辑体验存在严重碎片化问题。同一份 Skill 文档被分散到 **6 个独立页面** 中编辑：

| 页面 | 路由 | 职责 | 行数 |
|------|------|------|------|
| SkillDetail.vue | `/skill/:id` | 传统 IDE（Monaco + 文件树 + Chat 抽屉） | ~2000 |
| SkillIDE.vue | `/skills/ide/:id` | 新统一 IDE（Block Editor + Chat Panel） | ~800 |
| SkillNew.vue | `/skills/ide` | 4步创建向导 + 批量创建 | ~600 |
| SkillEdit.vue | `/skill/:id/edit` | 结构化参数编辑（Tab式） | ~600 |
| SkillChat.vue | `/skill/:id/chat` | 独立 Agent 对话页 | ~300 |
| SkillWorkspace.vue | `/skill/:id` | 8-Tab 容器（detail/edit/test/sandbox/chat/history/shadow） | ~200 |

用户在这些页面之间频繁跳转，编辑上下文不断丢失，AI 能力分散在 3 个不同入口。

### 1.2 目标

参考 **Cursor** 的交互模式，将所有 Skill 编辑能力融合到一个统一工作台：

| 目标 | 衡量标准 |
|------|---------|
| **单页完成所有操作** | 创建、编辑、对话、测试、验证、发布均在一个页面完成 |
| **Agent 深度联动** | 对话可感知当前模块/选中内容，修改以 Inline Diff 展示 |
| **双视图编辑** | Block Editor（新手）与 Monaco（高级）共享同一文档模型 |
| **上下文不丢失** | 切换模块/视图/面板时保持编辑状态 |
| **工具栏精简** | 顶栏 ≤ 6 个动作组，高级功能收入命令面板和 slash commands |

### 1.3 非目标（明确排除）

- 不重写后端 workbench 模块（已有良好基础，增强即可）
- 不新增数据库表（现有 6 张 workbench 表足够）
- 不改变 SKILL.md 文件格式标准
- 不改变 Git 版本管理机制
- 不改变认证/RBAC 体系

---

## 2. 现状分析与问题诊断

### 2.1 根因分析

| # | 根因 | 具体表现 |
|---|------|---------|
| R1 | **按任务建页面，不是按文档建工作台** | SkillNew 负责创建，SkillEdit 负责结构编辑，SkillDetail 负责 IDE，SkillChat 负责对话，SkillWorkspace 用 Tab 拼接 |
| R2 | **缺少统一 SkillDocument 模型** | `ide.js` store 和 `workbench.js` store 同时持有文档内容，Block Editor / Monaco / Chat / 测试各操作不同副本 |
| R3 | **AI 是外挂而非主编辑路径** | SkillIDE 有 patch 流程，SkillDetail 有 Chat 抽屉，SkillChat 是独立页 — 三套 AI 入口互不关联 |
| R4 | **工具栏暴露系统能力而非上下文动作** | SkillDetail 工具栏 20+ 按钮全部平铺，与当前操作无关的按钮也可见 |

### 2.2 现有资产盘点

#### 前端组件（可复用）

| 类别 | 组件 | 数量 | 复用策略 |
|------|------|------|---------|
| IDE Block 编辑器 | MetaBlock / GoalBlock / RulesBlock / ParamsBlock / OutputTableBlock / TestCasesBlock / WorkflowBlock | 7 | **直接复用**，接入新 Document Store |
| IDE 容器组件 | IDEBlockEditor / IDEWelcome / IDEPatchOverlay | 3 | **重构**，适配新 EditorSurface |
| Workbench 面板 | WorkbenchChatPanel / WorkbenchModuleNav / WorkbenchPatchPreview / WorkbenchValidationPanel / WorkbenchReferencePicker / WorkflowPreviewPanel | 6 | **重构**，融入统一工作台 |
| Flow 可视化 | SkillFlowPanel + 7 节点类型 + ConditionEdge + NodePropertyPanel + ExecutionOverlay + 3 utils | 15 | **直接复用**，按需加载 |
| 编辑器基础 | MonacoEditor / FileTree / StatusBar / MermaidPanel / DiffPreviewModal / InlineAIEdit | 6 | **直接复用** |

#### 后端服务（可复用）

| 服务 | 文件 | 复用策略 |
|------|------|---------|
| 总编排 | `workbench/service.py` (35K) | **增强**，增加 draft context 和 hunk-level apply |
| Patch 应用 | `workbench/apply_service.py` (8.4K) | **增强**，增加部分 accept 能力 |
| API 路由 | `workbench/router.py` (6.2K) | **增强**，新增 3 个端点 |
| 上下文构建 | `workbench/context_builder.py` (5.9K) | **增强**，接收 draft + selection |
| 意图识别 | `workbench/intent_service.py` (2.8K) | **保持**，仅 free-form chat 使用 |
| 数据模型 | `workbench/models.py` (5.1K) | **保持** |
| Schema | `workbench/schemas.py` (3.9K) | **增强**，新增请求/响应模型 |

#### Store（需统一）

| Store | 文件 | 当前职责 | 融合后 |
|-------|------|---------|--------|
| `ide.js` | 2.5K | IDE 页面状态 + 部分文档内容 | **废弃**，职责拆分到 document + ui store |
| `workbench.js` | 35K+ | Session + 消息 + Patch + 验证 + **文档内容** | **重构**，去掉文档持有，保留 session/patch/validation |
| `user.js` | 3K | 用户认证 + 主题 | **保持不变** |

---

## 3. 融合方案总览

### 3.1 统一工作台布局

```
┌─ TopBar ──────────────────────────────────────────────────────────┐
│ ← Skills > [SkillName] [status]  │  ⌘P  │ Block│Code │ ✓ │ Save │
├──────────┬────────────────────────────┬───────────────────────────┤
│ Navigator│     Editor Surface          │    Assistant Pane         │
│          │                             │    (常驻，可折叠/展开)      │
│ ┌──────┐ │  ┌───────────────────────┐  │  ┌───────────────────┐   │
│ │@目标  │ │  │                       │  │  │ 💬 对话消息流       │   │
│ │@规则  │ │  │  Block Editor         │  │  │                   │   │
│ │@参数  │ │  │  ─── 或 ───           │  │  │ ┌───────────────┐ │   │
│ │@输出  │ │  │  Monaco Editor        │  │  │ │ @当前模块      │ │   │
│ │@测试  │ │  │                       │  │  │ │ 📎 引用选中    │ │   │
│ │@工作流 │ │  │  ┌── Inline Diff ──┐  │  │  │ └───────────────┘ │   │
│ │       │ │  │  │ ✓ Accept  ✗ Rej │  │  │  │                   │   │
│ ├──────┤ │  │  └──────────────────┘  │  │  │ ┌─────────────────┐│   │
│ │ 引用  │ │  └───────────────────────┘  │  │ │ 输入 + /commands ││   │
│ │ 版本  │ │                             │  │ └─────────────────┘│   │
│ │ 文件  │ │                             │  └───────────────────┘   │
├──────────┴────────────────────────────┴───────────────────────────┤
│ BottomPanel:  Validation │ Test │ Sandbox │ Logs                  │
└───────────────────────────────────────────────────────────────────┘
```

### 3.2 核心设计原则

| 原则 | 说明 |
|------|------|
| **单文档真相源** | `SkillDocument` 是唯一数据源，Block Editor 和 Monaco 都读写同一个 reactive 对象 |
| **SKILL.md 是渲染产物** | 编辑时操作结构化数据，保存时由 parser.render() 生成 SKILL.md |
| **AI 是一等公民** | Agent 不是外挂抽屉，而是常驻面板；Inline Diff 是默认修改展示方式 |
| **上下文感知** | 每条 AI 消息绑定 moduleId / selectionRange / draftRevision |
| **渐进式复杂度** | 新手看 Block + 引导，高级用户切 Monaco + 命令面板 |
| **面板即功能** | 测试/沙箱/历史/Shadow 不再是独立页面，而是工作台内的面板 |

### 3.3 页面合并映射

| 原页面 | 命运 | 去向 |
|--------|------|------|
| SkillDetail.vue | **拆解** | Monaco/锁/Mermaid/Flow 提取为独立组件 |
| SkillIDE.vue | **升级为主页面** | 成为 `SkillWorkbench.vue` |
| SkillNew.vue | **吸收** | 创建逻辑并入 Workbench create mode |
| SkillEdit.vue | **吸收** | 参数表单并入 Block 模块 |
| SkillChat.vue | **降级** | 成为 AssistantPane 组件 |
| SkillWorkspace.vue | **废弃** | Tab 容器不再需要 |
| SkillTest.vue | **降级** | 成为 BottomPanel 的 Test Tab |
| SkillSandbox.vue | **降级** | 成为 BottomPanel 的 Sandbox Tab |
| SkillHistory.vue | **降级** | 成为 Navigator 的版本区域 |
| SkillShadow.vue | **降级** | 成为 BottomPanel 的 Shadow Tab |

---

## 4. 前端方案

### 4.1 路由设计

```js
// ===== 新路由 =====
{
  path: '/skills/new',
  name: 'SkillWorkbenchCreate',
  component: () => import('@/pages/skill/SkillWorkbench.vue'),
  meta: { requiresAuth: true, roles: ['admin', 'ai_engineer', 'aibp'] }
},
{
  path: '/skills/:id',
  name: 'SkillWorkbench',
  component: () => import('@/pages/skill/SkillWorkbench.vue'),
  meta: { requiresAuth: true, roles: ['admin', 'ai_engineer', 'aibp', 'biz_owner'] }
},

// ===== 旧路由重定向 =====
{ path: '/skill/:id',           redirect: to => `/skills/${to.params.id}` },
{ path: '/skill/:id/edit',      redirect: to => `/skills/${to.params.id}?view=block` },
{ path: '/skill/:id/chat',      redirect: to => `/skills/${to.params.id}?panel=assistant` },
{ path: '/skill/:id/test',      redirect: to => `/skills/${to.params.id}?bottom=test` },
{ path: '/skill/:id/sandbox',   redirect: to => `/skills/${to.params.id}?bottom=sandbox` },
{ path: '/skill/:id/history',   redirect: to => `/skills/${to.params.id}?nav=versions` },
{ path: '/skill/:id/shadow',    redirect: to => `/skills/${to.params.id}?bottom=shadow` },
{ path: '/skills/ide',          redirect: '/skills/new' },
{ path: '/skills/ide/:id',      redirect: to => `/skills/${to.params.id}` },
```

**列表页入口变更**：
- SkillList.vue "新建" 按钮 → `/skills/new`
- SkillList.vue 行点击 → `/skills/:id`
- AppLayout.vue 导航保持 "Skills" 一级入口

### 4.2 组件树

```
web/src/pages/skill/
├── SkillList.vue                    # 保留，修改链接指向
├── SkillWorkbench.vue               # ★ 新主页面（替代 SkillIDE + SkillDetail + SkillWorkspace）
└── (旧页面保留文件但不再路由，过渡期后删除)

web/src/components/workbench/
├── WorkbenchShell.vue               # ★ 四区域布局框架（顶栏+左侧+中央+右侧+底部）
├── WorkbenchTopBar.vue              # ★ 顶栏（面包屑 + 视图切换 + 动作按钮）
├── WorkbenchNavigator.vue           # ★ 左侧导航（模块大纲 + 引用 + 版本 + 文件树）
├── WorkbenchEditorSurface.vue       # ★ 中央编辑面（Block/Monaco 双视图容器）
├── WorkbenchAssistantPane.vue       # ★ 右侧 Agent 面板（对话 + 引用 + slash commands）
├── WorkbenchBottomPanel.vue         # ★ 底部面板（Validation/Test/Sandbox/Logs/Shadow）
├── WorkbenchCommandPalette.vue      # ★ 命令面板（⌘P 触发）
├── WorkbenchInlineDiff.vue          # ★ 编辑器内 Diff Accept/Reject 组件
├── WorkbenchInlinePrompt.vue        # ★ Cmd+K 内联提示输入条
├── WorkbenchModuleNav.vue           # 重构：模块导航（接入新 Document Store）
├── WorkbenchChatPanel.vue           # 重构：对话消息流（增加上下文引用）
├── WorkbenchPatchPreview.vue        # 重构：Patch Diff 预览
├── WorkbenchValidationPanel.vue     # 保留：验证结果面板
├── WorkbenchReferencePicker.vue     # 保留：引用选择器
└── WorkflowPreviewPanel.vue         # 保留：工作流预览

web/src/components/ide/
├── blocks/                          # 保留全部 7 个 Block 组件
│   ├── MetaBlock.vue
│   ├── GoalBlock.vue
│   ├── RulesBlock.vue
│   ├── ParamsBlock.vue
│   ├── OutputTableBlock.vue
│   ├── TestCasesBlock.vue
│   └── WorkflowBlock.vue
├── IDEBlockEditor.vue               # 重构：适配 EditorSurface
├── IDEWelcome.vue                   # 保留：创建模式欢迎页
└── IDEPatchOverlay.vue              # 重构：融入 InlineDiff

web/src/components/flow/             # 全部保留，按需加载
web/src/components/editor/           # MonacoEditor 等基础组件保留
```

### 4.3 Store 设计

#### 4.3.1 Document Store（新建）— 唯一文档真相源

```js
// web/src/stores/document.js
import { defineStore } from 'pinia'

export const useDocumentStore = defineStore('document', {
  state: () => ({
    // ===== 文档内容 =====
    skillDocument: {
      meta: {
        structuredValue: {
          id: '', name: '', description: '', department: '',
          owner: '', status: 'draft', version: '',
          trigger_type: '', trigger_expression: '',
          risk_level: '', approval_level: ''
        },
        rawMarkdown: '',      // YAML frontmatter 原始文本
        sourceRange: null     // { startLine, endLine } 在完整 SKILL.md 中的位置
      },
      goal: {
        structuredValue: '',
        rawMarkdown: '',
        sourceRange: null
      },
      rules: {
        structuredValue: [],   // DecisionStep[]
        rawMarkdown: '',
        sourceRange: null
      },
      params: {
        structuredValue: [],   // { name, value, description }[]
        rawMarkdown: '',
        sourceRange: null
      },
      output_table: {
        structuredValue: [],   // OutputItem[]
        rawMarkdown: '',
        sourceRange: null
      },
      test_cases: {
        structuredValue: [],   // TestCase[]
        rawMarkdown: '',
        sourceRange: null
      },
      workflow: {
        structuredValue: { nodes: [], edges: [], bindings: [] },
        rawMarkdown: '',
        sourceRange: null
      },
      custom_sections: {}      // 未识别章节 { sectionName: rawMarkdown }
    },

    // ===== 文件资产 =====
    files: {
      // 'policy_pack.yaml': { content: '', dirty: false },
      // 'scripts/main.py': { content: '', dirty: false },
    },

    // ===== 文档状态 =====
    parseState: 'idle',         // idle | parsing | parsed | error
    parseErrors: [],            // 解析失败的模块列表
    dirty: false,               // 文档是否有未保存更改
    dirtyModules: new Set(),    // 哪些模块有更改
    lastSavedAt: null,          // 上次保存时间

    // ===== 编辑上下文 =====
    activeModule: 'meta',       // 当前活跃模块
    selection: {                // 当前选区（Monaco 模式用）
      moduleId: null,
      startLine: null,
      endLine: null,
      text: ''
    },
    draftRevision: 0            // 每次编辑 +1，用于追踪版本
  }),

  getters: {
    // 当前模块的结构化数据
    activeModuleData: (state) => state.skillDocument[state.activeModule],

    // 模块完成度统计
    moduleStates: (state) => {
      const modules = ['meta', 'goal', 'rules', 'params', 'output_table', 'test_cases', 'workflow']
      return modules.map(key => {
        const mod = state.skillDocument[key]
        const sv = mod.structuredValue
        let status = 'empty'
        if (Array.isArray(sv) ? sv.length > 0 : (typeof sv === 'string' ? sv.length > 0 : Object.keys(sv).some(k => sv[k]))) {
          status = state.dirtyModules.has(key) ? 'modified' : 'ready'
        }
        return { key, status, dirty: state.dirtyModules.has(key) }
      })
    },

    // 完整 SKILL.md 渲染文本（供 Monaco 使用）
    fullMarkdown: (state) => {
      // 由 schemaMapper 生成，此处返回缓存值
      return state._cachedMarkdown || ''
    },

    // 脏文件列表
    dirtyFileList: (state) => Object.entries(state.files).filter(([, f]) => f.dirty).map(([k]) => k)
  },

  actions: {
    // ===== 文档加载 =====

    // 从 API 响应加载 Skill（编辑模式）
    loadFromApi(apiResponse) {
      // apiResponse = GET /api/skills/:id 的返回
      // 映射 frontmatter → meta, purpose → goal, steps → rules, ...
      // 同时保存 rawMarkdown 和 sourceRange
      // 设置 parseState = 'parsed', dirty = false
    },

    // 从 AI Draft 加载（创建模式）
    loadFromDraft(draftResponse) {
      // draftResponse = POST /api/workbench/generate-draft 的返回
      // 映射结构化数据到各模块
    },

    // ===== 模块编辑 =====

    // Block Editor 更新某模块的结构化数据
    updateModuleStructured(moduleKey, newValue) {
      // 1. 更新 skillDocument[moduleKey].structuredValue
      // 2. 通过 schemaMapper 同步到 rawMarkdown
      // 3. dirtyModules.add(moduleKey), dirty = true
      // 4. draftRevision++
      // 5. 触发去抖验证
    },

    // Monaco 编辑更新原始文本
    updateModuleRaw(moduleKey, newRawMarkdown) {
      // 1. 更新 skillDocument[moduleKey].rawMarkdown
      // 2. 尝试通过 parser 解析为 structuredValue
      // 3. 解析失败 → parseErrors 记录，保持 raw block 不丢内容
      // 4. 解析成功 → 同步 structuredValue
      // 5. dirtyModules.add(moduleKey), dirty = true
      // 6. draftRevision++
    },

    // Monaco 全文编辑（整个 SKILL.md）
    updateFullMarkdown(newMarkdown) {
      // 1. 调用 parser 重新拆分为各模块
      // 2. 逐模块更新 rawMarkdown + structuredValue + sourceRange
      // 3. 未识别章节存入 custom_sections
    },

    // ===== Patch 应用 =====

    // AI Patch 应用到文档
    applyPatch(patch) {
      // patch = { target_module, patch_json, ... }
      // 1. 只更新 target_module 的 structuredValue
      // 2. 同步 rawMarkdown
      // 3. dirtyModules.add(target_module)
      // 4. draftRevision++
    },

    // ===== 选区管理 =====

    setSelection(moduleId, startLine, endLine, text) {
      // 更新 selection，用于 AI 上下文
    },

    setActiveModule(key) {
      // 切换活跃模块
    },

    // ===== 保存 =====

    // 生成保存载荷（供 API 调用）
    toStructuredPayload() {
      // 将 skillDocument 映射回 API 所需的 structured 格式
      // frontmatter, purpose, steps, antipatterns, output_definition, data_inputs, test_cases
    },

    // 保存完成后的清理
    markSaved() {
      // dirty = false, dirtyModules.clear(), lastSavedAt = now
    }
  }
})
```

#### 4.3.2 Workbench Store（重构）— Session + AI + 验证

```js
// web/src/stores/workbench.js（重构后）
export const useWorkbenchStore = defineStore('workbench', {
  state: () => ({
    // ===== Session =====
    sessionId: null,
    skillId: null,
    mode: 'edit',              // 'create' | 'edit'

    // ===== 编辑锁 =====
    lock: {
      held: false,
      lockedBy: null,
      expiresAt: null
    },

    // ===== AI 对话 =====
    messages: [],               // { role, content, moduleId, selectionRange, draftRevision, timestamp }[]
    chatLoading: false,

    // ===== Patch =====
    currentPatch: null,         // 当前待确认的 patch
    patchHistory: [],           // 已 apply/reject 的 patch 列表

    // ===== 验证 =====
    validationReport: null,
    validating: false,

    // ===== 引用 =====
    references: [],
    selectedReferenceIds: [],

    // ===== AI 运行记录 =====
    aiRuns: [],

    // ===== 状态标记 =====
    saving: false,
    publishing: false,
    generatingDraft: false,
    generatingPatch: false,
    applyingPatch: false
  }),

  getters: {
    hasUnappliedPatch: (state) => state.currentPatch?.status === 'draft',
    validationStatus: (state) => {
      if (!state.validationReport) return 'idle'
      if (state.validationReport.errors?.length) return 'error'
      if (state.validationReport.warnings?.length) return 'warning'
      return 'success'
    }
  },

  actions: {
    // ===== Session 管理 =====
    async initSession(skillId) {},
    async resumeSession(sessionId) {},

    // ===== AI 对话（上下文感知） =====
    async sendMessage(content, options = {}) {
      // options: { targetModule, selectionRange, intent, references }
      // 1. 从 documentStore 获取当前 draft 上下文
      // 2. 附带 activeModule, selection, draftRevision
      // 3. 发送到后端 → 返回 patch 或纯回答
      // 4. 若返回 patch → 设置 currentPatch，前端展示 Inline Diff
    },

    // ===== Patch 管理 =====
    async acceptPatch() {
      // 1. 调 apply_service → 后端落库
      // 2. documentStore.applyPatch(currentPatch)
      // 3. currentPatch = null
      // 4. 触发局部验证
    },
    rejectPatch() {},
    async acceptHunk(hunkIndex) {},    // 部分接受
    async rejectHunk(hunkIndex) {},

    // ===== 验证 =====
    async runValidation(moduleKey = null) {},  // null = 全量验证
    async runTest(testCaseId = null) {},
    async runSandbox(params) {},

    // ===== 保存与发布 =====
    async save() {
      // 1. documentStore.toStructuredPayload()
      // 2. PUT /api/skills/:id/structured
      // 3. documentStore.markSaved()
    },
    async publish() {},
    async submitReview() {},

    // ===== 锁管理 =====
    async acquireLock() {},
    async releaseLock() {},

    // ===== 引用 =====
    async loadReferences(filters) {},
    toggleReference(id) {}
  }
})
```

#### 4.3.3 UI Store（新建）— 纯 UI 状态

```js
// web/src/stores/ui.js
export const useUIStore = defineStore('ui', {
  state: () => ({
    // ===== 视图模式 =====
    viewMode: 'block',           // 'block' | 'code'（localStorage 持久化）
    editorTheme: 'light',        // 'light' | 'dark'（localStorage 持久化）
    editorFontSize: 14,          // 12-24（localStorage 持久化）

    // ===== 面板状态 =====
    assistantPaneOpen: true,     // 右侧 AI 面板
    assistantPaneWidth: 380,     // 可拖拽宽度
    bottomPanelOpen: false,      // 底部面板
    bottomPanelHeight: 250,      // 可拖拽高度
    bottomPanelTab: 'validation', // validation | test | sandbox | logs | shadow
    navigatorOpen: true,         // 左侧导航
    navigatorWidth: 220,         // 可拖拽宽度
    navigatorSection: 'modules', // modules | references | versions | files

    // ===== 命令面板 =====
    commandPaletteOpen: false,

    // ===== 全屏 =====
    fullscreen: false,

    // ===== 内联提示 =====
    inlinePromptVisible: false,
    inlinePromptPosition: null
  }),

  actions: {
    toggleAssistant() {},
    toggleBottomPanel(tab) {},
    toggleNavigator() {},
    toggleCommandPalette() {},
    toggleFullscreen() {},
    setViewMode(mode) {},

    // 从 localStorage 恢复所有 UI 状态
    restoreFromStorage() {},
    // 保存到 localStorage
    persistToStorage() {}
  }
})
```

#### 4.3.4 IDE Store（废弃计划）

`ide.js` store 的职责拆分：

| 原字段/动作 | 新归属 |
|------------|--------|
| `mode` (create/edit) | `workbench.mode` |
| `skillId` | `workbench.skillId` |
| `dirty / dirtyModules` | `document.dirty / document.dirtyModules` |
| `activeModule` | `document.activeModule` |
| `chatPanelOpen` | `ui.assistantPaneOpen` |
| `createForm` | `document.skillDocument.meta.structuredValue` |
| `blockValidation` | `workbench.validationReport` |
| `saving` | `workbench.saving` |
| `initForCreate()` | `workbench.initSession()` + `document` 初始化 |
| `initForEdit(id)` | `workbench.initSession(id)` + `document.loadFromApi()` |
| `updateModule()` | `document.updateModuleStructured()` |
| `saveDirectEdits()` | `workbench.save()` |

### 4.4 SchemaMapper（前端双视图桥梁）

```js
// web/src/utils/schemaMapper.js

/**
 * API 响应 → Document Store 格式
 * 将 GET /api/skills/:id 的响应映射到 skillDocument 各模块
 */
export function apiResponseToDocument(apiResp) {
  return {
    meta: {
      structuredValue: {
        id: apiResp.id,
        name: apiResp.name,
        description: apiResp.frontmatter?.description || '',
        department: apiResp.department,
        owner: apiResp.frontmatter?.owner || '',
        status: apiResp.status,
        version: apiResp.version,
        trigger_type: apiResp.trigger_type,
        trigger_expression: apiResp.frontmatter?.trigger_expression || '',
        risk_level: apiResp.risk_level,
        approval_level: apiResp.frontmatter?.approval_level || ''
      }
    },
    goal: { structuredValue: apiResp.purpose || '' },
    rules: { structuredValue: apiResp.steps || [] },
    params: { structuredValue: extractParams(apiResp.policy_pack) },
    output_table: { structuredValue: apiResp.output_definition || [] },
    test_cases: { structuredValue: apiResp.test_cases || [] },
    workflow: { structuredValue: apiResp.workflow || { nodes: [], edges: [], bindings: [] } },
    custom_sections: apiResp.custom_sections || {}
  }
}

/**
 * Document Store → API 保存载荷
 * 将 skillDocument 映射回 PUT /api/skills/:id/structured 所需格式
 */
export function documentToStructuredPayload(skillDocument) {
  return {
    frontmatter: buildFrontmatter(skillDocument.meta.structuredValue),
    purpose: skillDocument.goal.structuredValue,
    steps: skillDocument.rules.structuredValue,
    antipatterns: [],  // 从 custom_sections 提取
    output_definition: skillDocument.output_table.structuredValue,
    data_inputs: [],   // 只读，不提交
    test_cases: skillDocument.test_cases.structuredValue,
    custom_sections: skillDocument.custom_sections,
    policy_pack: buildPolicyPack(skillDocument.params.structuredValue)
  }
}

/**
 * Document Store → 完整 SKILL.md 文本
 * 供 Monaco 编辑器使用
 */
export function documentToMarkdown(skillDocument) {
  // 调用与后端 parser.render() 对等的前端逻辑
  // 生成 YAML frontmatter + 各章节的 Markdown
}

/**
 * SKILL.md 文本 → Document Store 各模块
 * Monaco 编辑后同步回结构化数据
 */
export function markdownToDocument(markdown) {
  // 调用与后端 parser.parse() 对等的前端逻辑
  // 拆分为各模块的 rawMarkdown + 尝试解析 structuredValue
}
```

### 4.5 主页面组件设计

#### SkillWorkbench.vue（主页面）

```vue
<!-- web/src/pages/skill/SkillWorkbench.vue -->
<template>
  <WorkbenchShell>
    <template #topbar>
      <WorkbenchTopBar
        :skill-name="documentStore.skillDocument.meta.structuredValue.name"
        :status="documentStore.skillDocument.meta.structuredValue.status"
        :dirty="documentStore.dirty"
        :view-mode="uiStore.viewMode"
        @toggle-view="uiStore.setViewMode"
        @save="workbenchStore.save()"
        @publish="workbenchStore.publish()"
        @command-palette="uiStore.toggleCommandPalette()"
        @validate="workbenchStore.runValidation()"
      />
    </template>

    <template #navigator>
      <WorkbenchNavigator
        :modules="documentStore.moduleStates"
        :active-module="documentStore.activeModule"
        :section="uiStore.navigatorSection"
        @select-module="documentStore.setActiveModule"
        @change-section="uiStore.navigatorSection = $event"
      />
    </template>

    <template #editor>
      <WorkbenchEditorSurface
        :view-mode="uiStore.viewMode"
        :active-module="documentStore.activeModule"
        :module-data="documentStore.activeModuleData"
        :patch="workbenchStore.currentPatch"
        :theme="uiStore.editorTheme"
        :font-size="uiStore.editorFontSize"
        @update-structured="documentStore.updateModuleStructured"
        @update-raw="documentStore.updateModuleRaw"
        @selection-change="documentStore.setSelection"
        @inline-prompt="uiStore.inlinePromptVisible = true"
        @accept-patch="workbenchStore.acceptPatch()"
        @reject-patch="workbenchStore.rejectPatch()"
      />
    </template>

    <template #assistant>
      <WorkbenchAssistantPane
        v-if="uiStore.assistantPaneOpen"
        :messages="workbenchStore.messages"
        :loading="workbenchStore.chatLoading"
        :active-module="documentStore.activeModule"
        :selection="documentStore.selection"
        :references="workbenchStore.references"
        @send="workbenchStore.sendMessage"
        @select-reference="workbenchStore.toggleReference"
      />
    </template>

    <template #bottom>
      <WorkbenchBottomPanel
        v-if="uiStore.bottomPanelOpen"
        :active-tab="uiStore.bottomPanelTab"
        :validation="workbenchStore.validationReport"
        :skill-id="workbenchStore.skillId"
        @change-tab="uiStore.bottomPanelTab = $event"
      />
    </template>
  </WorkbenchShell>

  <!-- 命令面板（全局浮层） -->
  <WorkbenchCommandPalette
    v-if="uiStore.commandPaletteOpen"
    @close="uiStore.commandPaletteOpen = false"
    @execute="handleCommand"
  />

  <!-- 内联提示条（覆盖在编辑器上） -->
  <WorkbenchInlinePrompt
    v-if="uiStore.inlinePromptVisible"
    :position="uiStore.inlinePromptPosition"
    @submit="handleInlinePrompt"
    @cancel="uiStore.inlinePromptVisible = false"
  />
</template>

<script setup>
import { onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useDocumentStore } from '@/stores/document'
import { useWorkbenchStore } from '@/stores/workbench'
import { useUIStore } from '@/stores/ui'

const route = useRoute()
const documentStore = useDocumentStore()
const workbenchStore = useWorkbenchStore()
const uiStore = useUIStore()

onMounted(async () => {
  uiStore.restoreFromStorage()

  const skillId = route.params.id
  if (skillId) {
    // 编辑模式
    workbenchStore.mode = 'edit'
    await workbenchStore.initSession(skillId)
    // initSession 内部会调 document.loadFromApi()
  } else {
    // 创建模式
    workbenchStore.mode = 'create'
    await workbenchStore.initSession(null)
    // 文档为空，显示 Welcome 引导
  }

  // 从 query 恢复面板状态
  if (route.query.panel === 'assistant') uiStore.assistantPaneOpen = true
  if (route.query.bottom) {
    uiStore.bottomPanelOpen = true
    uiStore.bottomPanelTab = route.query.bottom
  }
  if (route.query.view) uiStore.viewMode = route.query.view
  if (route.query.nav) uiStore.navigatorSection = route.query.nav

  // 注册全局快捷键
  window.addEventListener('keydown', handleKeyboard)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeyboard)
  uiStore.persistToStorage()
  // 释放编辑锁
  if (workbenchStore.lock.held) {
    workbenchStore.releaseLock()
  }
})

function handleKeyboard(e) {
  // Cmd+P / Ctrl+P → 命令面板
  if ((e.metaKey || e.ctrlKey) && e.key === 'p') {
    e.preventDefault()
    uiStore.toggleCommandPalette()
  }
  // Cmd+K / Ctrl+K → 内联 AI 提示
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault()
    if (documentStore.selection.text) {
      uiStore.inlinePromptVisible = true
    }
  }
  // Cmd+S / Ctrl+S → 保存
  if ((e.metaKey || e.ctrlKey) && e.key === 's') {
    e.preventDefault()
    workbenchStore.save()
  }
}

function handleCommand(command) {
  // 命令面板动作分发
  const commands = {
    'generate-tests': () => workbenchStore.sendMessage('/generate-tests', { intent: 'generate_tests' }),
    'discover-antipatterns': () => workbenchStore.sendMessage('/discover-antipatterns', { intent: 'discover_antipatterns' }),
    'suggest-branches': () => workbenchStore.sendMessage('/suggest-branches', { intent: 'suggest_branches' }),
    'drift-check': () => workbenchStore.sendMessage('/drift-check', { intent: 'drift_check' }),
    'run-aiclaw': () => workbenchStore.sendMessage('/run-aiclaw', { intent: 'run_aiclaw' }),
    'toggle-mermaid': () => { /* 显示 Mermaid 预览 */ },
    'toggle-flow': () => { /* 显示 Flow 预览 */ },
    'submit-review': () => workbenchStore.submitReview(),
    'deprecate': () => { /* 停用确认 */ },
    'fullscreen': () => uiStore.toggleFullscreen(),
    'toggle-theme': () => uiStore.editorTheme = uiStore.editorTheme === 'dark' ? 'light' : 'dark',
  }
  commands[command]?.()
}

function handleInlinePrompt(prompt) {
  // Cmd+K 提交：将选中内容 + 提示发送给 Agent
  workbenchStore.sendMessage(prompt, {
    targetModule: documentStore.activeModule,
    selectionRange: documentStore.selection,
    intent: 'rewrite_selection'
  })
  uiStore.inlinePromptVisible = false
}
</script>
```

### 4.6 工具栏按钮迁移方案

原 SkillDetail.vue 工具栏 20+ 按钮的完整去向：

| 原按钮 | 新位置 | 触发方式 |
|--------|--------|---------|
| 编辑/取消编辑 | 顶栏 | 进入页面自动 acquireLock |
| 保存 | 顶栏 Save 按钮 + Cmd+S | 按钮/快捷键 |
| 文件 Tab 栏 | EditorSurface 内部（Code 模式时显示） | 点击 |
| 测试 | 底部 Panel → Test Tab | 底部面板 Tab |
| 沙箱 | 底部 Panel → Sandbox Tab | 底部面板 Tab |
| 历史 | 左侧 Navigator → 版本区域 | 导航切换 |
| Agent | 右侧 Assistant Pane（常驻） | 始终可见 |
| Mermaid | 命令面板 `toggle-mermaid` 或 rules 模块时右键菜单 | ⌘P / 右键 |
| Flow 图 | 命令面板 `toggle-flow` 或 workflow 模块时自动显示 | ⌘P / 自动 |
| 执行面板 | 底部 Panel → Sandbox Tab | 底部面板 Tab |
| AI 生成测试 | Assistant `/generate-tests` | slash command |
| 反例发现 | Assistant `/discover-antipatterns` | slash command |
| 分支建议 | Assistant `/suggest-branches` | slash command |
| 漂移检测 | Assistant `/drift-check` | slash command |
| 优化器 | Assistant `/optimize` | slash command |
| AIClaw 执行 | Assistant `/run-aiclaw` | slash command |
| 字体大小 | 命令面板 | ⌘P → "Font Size" |
| 主题切换 | 命令面板 | ⌘P → "Toggle Theme" |
| 全屏 | 命令面板 | ⌘P → "Fullscreen" |
| 提交审核 | 顶栏 Save 下拉 → "Submit Review" | 下拉菜单 |
| 停用 | 顶栏 More → "Deprecate" | 更多菜单 |

### 4.7 键盘快捷键

| 快捷键 | 功能 |
|--------|------|
| `Cmd+S` / `Ctrl+S` | 保存 |
| `Cmd+P` / `Ctrl+P` | 打开命令面板 |
| `Cmd+K` / `Ctrl+K` | 内联 AI 提示（需先选中内容） |
| `Cmd+B` / `Ctrl+B` | 切换左侧导航 |
| `Cmd+J` / `Ctrl+J` | 切换底部面板 |
| `Cmd+Shift+A` | 切换右侧 Assistant |
| `Escape` | 关闭命令面板/内联提示/全屏 |
| `Cmd+1~7` | 快速跳转到对应模块 |

### 4.8 响应式布局

| 断点 | 布局调整 |
|------|---------|
| ≥ 1440px | 四区域全部展开 |
| 1024-1439px | Assistant Pane 默认折叠，点击展开为 Overlay |
| 768-1023px | Navigator 折叠为图标栏，Assistant 为 Overlay |
| < 768px | 不支持（显示提示"请使用桌面浏览器"） |

---

## 5. 后端方案

### 5.1 现有服务改动总览

后端 workbench 模块已有良好基础，**不需要重写**，只需针对性增强。

| 服务文件 | 改动类型 | 改动内容 |
|----------|---------|---------|
| `router.py` | 增强 | 新增 3 个端点 |
| `schemas.py` | 增强 | 新增请求/响应模型 |
| `service.py` | 增强 | draft context 支持、slash command 分发 |
| `context_builder.py` | 增强 | 接收前端 draft + selection + 最近失败 |
| `apply_service.py` | 增强 | hunk 级部分 accept |
| `intent_service.py` | 保持 | 仅 free-form chat 使用 |
| `models.py` | 增强 | messages 表新增字段 |
| `diff_service.py` | 增强 | 模块级 diff 计算 |

### 5.2 新增 API 端点

#### 5.2.1 上下文感知对话

```
POST /api/skills/{skill_id}/workbench/chat
```

与原有 `workbench/patch` 的区别：这个端点整合了"对话"和"patch 生成"，由后端判断是否需要生成 patch。

**请求体**：
```json
{
  "session_id": "sess_xxx",
  "message": "这个参数太宽松了，ROI 阈值改成 1.5",
  "context": {
    "active_module": "params",
    "selection": {
      "module_id": "params",
      "start_line": 12,
      "end_line": 15,
      "text": "roi_threshold: 1.2"
    },
    "draft_revision": 42,
    "draft_snapshot": {
      "params": [
        { "name": "roi_threshold", "value": "1.2", "description": "ROI最低阈值" }
      ]
    },
    "recent_failures": [
      { "type": "validation", "module": "params", "message": "roi_threshold 低于行业基准" }
    ]
  },
  "intent": null,
  "reference_ids": []
}
```

**响应体**：
```json
{
  "type": "patch",
  "message": "好的，我把 ROI 阈值从 1.2 调整到 1.5。这个值更接近行业基准线。",
  "patch": {
    "id": "patch_xxx",
    "target_module": "params",
    "intent": "tune_threshold",
    "summary": "ROI 阈值从 1.2 调整为 1.5",
    "patch_json": {
      "params": [
        { "name": "roi_threshold", "value": "1.5", "description": "ROI最低阈值" }
      ]
    },
    "diff_preview": {
      "before": "roi_threshold: 1.2",
      "after": "roi_threshold: 1.5",
      "hunks": [
        { "index": 0, "type": "modify", "old": "1.2", "new": "1.5", "path": "params[0].value" }
      ]
    },
    "warnings": [],
    "needs_confirmation": true,
    "status": "draft"
  },
  "validation_hint": {
    "affected_modules": ["params"],
    "suggested_checks": ["structural", "sample_case"]
  }
}
```

当不需要生成 patch 时（纯问答）：

```json
{
  "type": "answer",
  "message": "当前 ROI 阈值设为 1.2，这意味着...",
  "patch": null,
  "validation_hint": null
}
```

#### 5.2.2 Slash Command 执行

```
POST /api/skills/{skill_id}/workbench/command
```

**请求体**：
```json
{
  "session_id": "sess_xxx",
  "command": "generate-tests",
  "context": {
    "active_module": "rules",
    "draft_snapshot": { "...": "..." }
  }
}
```

**响应体**（与 chat 端点格式一致）：
```json
{
  "type": "patch",
  "message": "已基于当前规则生成 5 个测试用例。",
  "patch": {
    "target_module": "test_cases",
    "patch_json": { "test_cases": [...] },
    "diff_preview": { "..." },
    "status": "draft"
  }
}
```

支持的 commands：

| Command | 映射后端 | 目标模块 |
|---------|---------|---------|
| `generate-tests` | `generate_tests()` | test_cases |
| `discover-antipatterns` | `discover_antipatterns()` | rules (custom_sections) |
| `suggest-branches` | `suggest_branches()` | rules |
| `drift-check` | `drift_check()` | params |
| `run-aiclaw` | `run_on_aiclaw()` | — (执行结果) |
| `optimize` | `optimizer_session()` | params |

#### 5.2.3 Hunk 级 Patch 部分应用

```
POST /api/skills/{skill_id}/workbench/apply-partial
```

**请求体**：
```json
{
  "session_id": "sess_xxx",
  "patch_id": "patch_xxx",
  "accepted_hunks": [0, 2],
  "rejected_hunks": [1]
}
```

**响应体**：
```json
{
  "status": "partial_applied",
  "applied_hunks": [0, 2],
  "rejected_hunks": [1],
  "updated_module": { "params": [...] },
  "git_commit": "abc1234"
}
```

### 5.3 context_builder.py 增强

```python
# app/workbench/context_builder.py — 增强部分

class WorkbenchContextBuilder:

    async def build_chat_context(
        self,
        skill_id: str,
        session_id: str,
        *,
        active_module: str | None = None,
        selection: dict | None = None,          # { module_id, start_line, end_line, text }
        draft_snapshot: dict | None = None,      # 前端当前未保存的模块数据
        recent_failures: list | None = None,     # 最近的验证/测试失败
        message_history: list | None = None,     # 最近 N 条对话（前端传来）
    ) -> dict:
        """
        构建 AI 对话上下文。

        优先级：
        1. draft_snapshot（未保存的前端状态）> 已落库的 Skill 数据
        2. selection（用户选中的内容）作为重点修改区域
        3. recent_failures 作为修改动机
        4. 已落库 Skill 数据作为完整上下文补充
        """
        # 1. 加载已落库的 Skill 结构（带缓存）
        persisted = await self.load_skill_structure(skill_id)

        # 2. 如果前端传了 draft_snapshot，用它覆盖对应模块
        if draft_snapshot:
            for module_key, module_data in draft_snapshot.items():
                if module_key in persisted:
                    persisted[module_key] = module_data

        # 3. 构建上下文对象
        context = {
            "skill_id": skill_id,
            "skill_structure": persisted,
            "active_module": active_module,
            "selection": selection,
            "recent_failures": recent_failures or [],
            "message_history": message_history or [],
        }

        # 4. 如果有选区，提取选区周围的上下文
        if selection and selection.get("text"):
            context["selection_context"] = self._expand_selection_context(
                persisted, selection
            )

        return context
```

### 5.4 apply_service.py 增强

```python
# app/workbench/apply_service.py — 增强部分

async def apply_partial_patch(
    self,
    skill_id: str,
    patch_id: str,
    accepted_hunks: list[int],
    rejected_hunks: list[int],
    db: AsyncSession,
    user_id: str
) -> dict:
    """
    部分应用 Patch — 只接受用户选择的 hunks。

    1. 从 patch_json.diff_preview.hunks 中筛选 accepted_hunks
    2. 只应用被接受的变更
    3. 更新 patch 状态为 partial_applied
    4. 记录哪些 hunks 被接受/拒绝
    """
    # 获取 patch
    patch = await self._get_patch(patch_id, db)

    hunks = patch.patch_json.get("diff_preview", {}).get("hunks", [])
    accepted_changes = [hunks[i] for i in accepted_hunks if i < len(hunks)]

    # 加载当前 Skill 结构
    skill_doc = await self.context_builder.load_skill_structure(skill_id)
    target = patch.target_module

    # 逐 hunk 应用
    for hunk in accepted_changes:
        skill_doc = self._apply_single_hunk(skill_doc, target, hunk)

    # 渲染回 SKILL.md 并保存
    result = await self._save_skill_document(skill_id, skill_doc, db, user_id)

    # 更新 patch 状态
    patch.status = "partial_applied"
    patch.applied_at = datetime.utcnow()
    await db.commit()

    return {
        "status": "partial_applied",
        "applied_hunks": accepted_hunks,
        "rejected_hunks": rejected_hunks,
        "updated_module": skill_doc.get(target),
        "git_commit": result.get("git_commit")
    }
```

### 5.5 service.py 增强：统一对话入口

```python
# app/workbench/service.py — 新增方法

async def handle_chat(
    self,
    skill_id: str,
    session_id: str,
    message: str,
    context: dict,
    intent: str | None,
    reference_ids: list[str],
    db: AsyncSession,
    user_id: str
) -> dict:
    """
    统一对话入口 — 整合意图识别、patch 生成、纯问答。

    流程：
    1. 若 intent 已由前端明确指定（如 rewrite_selection），直接使用
    2. 否则调 intent_service 识别
    3. 若意图需要修改 → 生成 patch → 返回 type=patch
    4. 若意图是问答 → 生成回答 → 返回 type=answer
    """
    # 1. 意图识别
    if not intent:
        intent_result = await self.intent_service.analyze(message)
        intent = intent_result.get("intent")
        target_module = intent_result.get("target_module") or context.get("active_module")
    else:
        target_module = context.get("active_module")

    # 2. 判断是否需要生成 patch
    needs_patch = intent in [
        "tune_threshold", "add_rule", "modify_rule", "rewrite_selection",
        "add_output", "add_test_case", "modify_workflow",
        "generate_tests", "suggest_branches"
    ]

    if needs_patch:
        # 3a. 构建上下文
        chat_context = await self.context_builder.build_chat_context(
            skill_id, session_id,
            active_module=target_module,
            selection=context.get("selection"),
            draft_snapshot=context.get("draft_snapshot"),
            recent_failures=context.get("recent_failures"),
        )

        # 3b. 生成 patch
        patch = await self.create_patch(
            skill_id, session_id, message,
            target_module=target_module,
            context=chat_context,
            reference_ids=reference_ids,
            db=db, user_id=user_id
        )

        # 3c. 记录消息
        await self._save_message(session_id, "user", message, db)
        await self._save_message(session_id, "assistant", patch.get("summary", ""), db,
                                 intent_json={"type": "patch", "patch_id": patch["id"]})

        return {
            "type": "patch",
            "message": patch.get("summary", ""),
            "patch": patch,
            "validation_hint": {
                "affected_modules": [target_module],
                "suggested_checks": ["structural", "sample_case"]
            }
        }
    else:
        # 4. 纯问答
        answer = await self._generate_answer(skill_id, message, context, db)

        await self._save_message(session_id, "user", message, db)
        await self._save_message(session_id, "assistant", answer, db)

        return {
            "type": "answer",
            "message": answer,
            "patch": None,
            "validation_hint": None
        }


async def handle_command(
    self,
    skill_id: str,
    session_id: str,
    command: str,
    context: dict,
    db: AsyncSession,
    user_id: str
) -> dict:
    """
    Slash command 分发器。
    将 Assistant 面板的 /command 映射到现有服务方法。
    """
    command_map = {
        "generate-tests": self._cmd_generate_tests,
        "discover-antipatterns": self._cmd_discover_antipatterns,
        "suggest-branches": self._cmd_suggest_branches,
        "drift-check": self._cmd_drift_check,
        "run-aiclaw": self._cmd_run_aiclaw,
        "optimize": self._cmd_optimize,
    }

    handler = command_map.get(command)
    if not handler:
        return {"type": "error", "message": f"未知命令: /{command}"}

    return await handler(skill_id, session_id, context, db, user_id)
```

### 5.6 schemas.py 新增模型

```python
# app/workbench/schemas.py — 新增

from pydantic import BaseModel, Field

class SelectionContext(BaseModel):
    module_id: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    text: str = ""

class FailureRecord(BaseModel):
    type: str                    # "validation" | "test" | "execution"
    module: str | None = None
    message: str = ""

class ChatContext(BaseModel):
    active_module: str | None = None
    selection: SelectionContext | None = None
    draft_revision: int = 0
    draft_snapshot: dict | None = None    # 前端未保存的模块数据
    recent_failures: list[FailureRecord] = []

class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(..., min_length=1, max_length=5000)
    context: ChatContext = ChatContext()
    intent: str | None = None
    reference_ids: list[str] = []

class ChatResponse(BaseModel):
    type: str                    # "patch" | "answer" | "error"
    message: str
    patch: dict | None = None
    validation_hint: dict | None = None

class CommandRequest(BaseModel):
    session_id: str
    command: str = Field(..., pattern=r"^[a-z][a-z0-9-]*$")
    context: ChatContext = ChatContext()

class PartialApplyRequest(BaseModel):
    session_id: str
    patch_id: str
    accepted_hunks: list[int]
    rejected_hunks: list[int] = []

class HunkDetail(BaseModel):
    index: int
    type: str                    # "modify" | "add" | "remove"
    path: str                    # JSONPath 如 "params[0].value"
    old: str | None = None
    new: str | None = None

class DiffPreview(BaseModel):
    before: str
    after: str
    hunks: list[HunkDetail]
```

### 5.7 router.py 新增端点

```python
# app/workbench/router.py — 新增部分

@router.post("/{skill_id}/workbench/chat", response_model=ChatResponse)
async def workbench_chat(
    skill_id: str,
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user)
):
    """上下文感知对话 — 统一 AI 交互入口"""
    await require_department_access(skill_id, user, db)
    return await workbench_service.handle_chat(
        skill_id, req.session_id, req.message,
        context=req.context.dict(),
        intent=req.intent,
        reference_ids=req.reference_ids,
        db=db, user_id=user.id
    )


@router.post("/{skill_id}/workbench/command", response_model=ChatResponse)
async def workbench_command(
    skill_id: str,
    req: CommandRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user)
):
    """Slash command 执行"""
    await require_department_access(skill_id, user, db)
    return await workbench_service.handle_command(
        skill_id, req.session_id, req.command,
        context=req.context.dict(),
        db=db, user_id=user.id
    )


@router.post("/{skill_id}/workbench/apply-partial")
async def workbench_apply_partial(
    skill_id: str,
    req: PartialApplyRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user)
):
    """Hunk 级部分应用 Patch"""
    await require_department_access(skill_id, user, db)
    return await workbench_service.apply_partial_patch(
        skill_id, req.patch_id,
        accepted_hunks=req.accepted_hunks,
        rejected_hunks=req.rejected_hunks,
        db=db, user_id=user.id
    )
```

---

## 6. 数据库方案

### 6.1 现有表（不变）

现有 6 张 workbench 表 **完全满足** 融合方案需求，不新增表：

| 表名 | 用途 | 状态 |
|------|------|------|
| `skill_workbench_sessions` | 工作台 session 生命周期 | **保持** |
| `skill_workbench_messages` | 对话历史（含上下文元数据） | **增强字段** |
| `skill_workbench_patches` | Patch 存储与状态追踪 | **增强字段** |
| `skill_workbench_validation_runs` | 验证运行记录 | **保持** |
| `skill_workbench_references` | 引用追踪 | **保持** |
| `skill_workbench_ai_runs` | AI 调用遥测 | **保持** |

### 6.2 字段增强（Alembic 迁移）

#### Migration 014: 增强 workbench messages 和 patches 表

```python
# migrations/versions/014_enhance_workbench_for_fusion.py

"""增强 workbench 表以支持 Cursor 式融合工作台"""

revision = '014'
down_revision = '013'

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def upgrade():
    # ===== skill_workbench_messages 增强 =====

    # 消息绑定的模块上下文
    op.add_column('skill_workbench_messages',
        sa.Column('module_id', sa.String(30), nullable=True,
                  comment='消息关联的模块 ID'))

    # 消息绑定的选区范围
    op.add_column('skill_workbench_messages',
        sa.Column('selection_range', JSONB, nullable=True,
                  comment='消息关联的选区 {module_id, start_line, end_line, text}'))

    # 消息绑定的文档版本号
    op.add_column('skill_workbench_messages',
        sa.Column('draft_revision', sa.Integer, nullable=True,
                  comment='消息发送时的文档版本号'))

    # 消息类型：user/assistant/system/command
    # (role 字段已存在，但扩展其语义)

    # ===== skill_workbench_patches 增强 =====

    # Hunk 级详细 diff（替代笼统的 diff_preview_json）
    op.add_column('skill_workbench_patches',
        sa.Column('hunks_json', JSONB, nullable=True,
                  comment='Hunk 级变更列表 [{index, type, path, old, new}]'))

    # 部分应用时记录哪些 hunk 被接受/拒绝
    op.add_column('skill_workbench_patches',
        sa.Column('accepted_hunks', JSONB, nullable=True,
                  comment='已接受的 hunk 索引列表'))

    op.add_column('skill_workbench_patches',
        sa.Column('rejected_hunks', JSONB, nullable=True,
                  comment='已拒绝的 hunk 索引列表'))

    # Patch 来源上下文
    op.add_column('skill_workbench_patches',
        sa.Column('source_context', JSONB, nullable=True,
                  comment='生成 patch 时的上下文快照 {active_module, selection, draft_revision}'))

    # ===== 索引优化 =====

    # 按模块查询消息
    op.create_index(
        'ix_workbench_messages_module',
        'skill_workbench_messages',
        ['session_id', 'module_id']
    )


def downgrade():
    op.drop_index('ix_workbench_messages_module')
    op.drop_column('skill_workbench_patches', 'source_context')
    op.drop_column('skill_workbench_patches', 'rejected_hunks')
    op.drop_column('skill_workbench_patches', 'accepted_hunks')
    op.drop_column('skill_workbench_patches', 'hunks_json')
    op.drop_column('skill_workbench_messages', 'draft_revision')
    op.drop_column('skill_workbench_messages', 'selection_range')
    op.drop_column('skill_workbench_messages', 'module_id')
```

### 6.3 models.py 更新

```python
# app/workbench/models.py — 更新字段

class SkillWorkbenchMessage(Base):
    __tablename__ = "skill_workbench_messages"

    # ... 现有字段保持 ...

    # 新增：上下文感知字段
    module_id = Column(String(30), nullable=True)
    selection_range = Column(JSONB, nullable=True)
    draft_revision = Column(Integer, nullable=True)


class SkillWorkbenchPatch(Base):
    __tablename__ = "skill_workbench_patches"

    # ... 现有字段保持 ...

    # 新增：Hunk 级操作字段
    hunks_json = Column(JSONB, nullable=True)
    accepted_hunks = Column(JSONB, nullable=True)
    rejected_hunks = Column(JSONB, nullable=True)
    source_context = Column(JSONB, nullable=True)
```

### 6.4 数据生命周期

| 数据 | 保留策略 | 清理方式 |
|------|---------|---------|
| Session | 72h 无活动 → inactive | `cleanup_expired_sessions()` 定时任务 |
| Messages | 跟随 session | session 清理时级联 |
| Patches (draft) | 72h 未 apply → 过期标记 | 定时清理 |
| Patches (applied) | 永久保留 | — |
| Validation Runs | 跟随 patch | patch 清理时级联 |
| AI Runs | 保留 90 天 | 定时清理 |

---

## 7. 缓存方案

### 7.1 现有 Redis 缓存（保持）

基于 `app/common/cache.py`，key 前缀 `sf:`，当前已有：

| Key 模式 | TTL | 用途 |
|----------|-----|------|
| `sf:skill:{id}:structure` | 300s | Skill 结构化数据缓存 |
| `sf:skill:{id}:*` | 各异 | Skill 相关缓存 |
| `sf:dashboard:*` | 各异 | 看板缓存 |
| `sf:compliance:*` | 各异 | 合规缓存 |

### 7.2 新增缓存 Key

| Key 模式 | TTL | 用途 | 失效时机 |
|----------|-----|------|---------|
| `sf:wb:session:{session_id}:ctx` | 1800s (30min) | Session 上下文快照 | Session 关闭 / Skill 保存 |
| `sf:wb:session:{session_id}:draft` | 600s (10min) | 前端 draft 快照（用于 AI 上下文） | 每次前端发送新 draft |
| `sf:wb:patch:{patch_id}:diff` | 1800s (30min) | Patch diff 计算结果 | Patch apply/reject |
| `sf:wb:skill:{id}:intent_hint` | 300s (5min) | 最近意图识别结果（避免重复调 LLM） | Skill 内容变更 |
| `sf:wb:cmd:{skill_id}:{command}` | 600s (10min) | Slash command 结果缓存（如 drift-check） | Skill 内容变更 |

### 7.3 缓存失效策略

```python
# app/workbench/cache.py（新建）

from app.common.cache import cache_delete_pattern

async def invalidate_workbench_cache(skill_id: str, scope: str = "all"):
    """
    工作台缓存失效。

    scope:
    - "all": Skill 保存/发布时，清除所有相关缓存
    - "draft": 前端发送新 draft 时，只清除 draft 缓存
    - "patch": Patch apply/reject 时，清除 patch 缓存
    """
    if scope in ("all", "draft"):
        await cache_delete_pattern(f"sf:wb:*:{skill_id}:*")
        await cache_delete_pattern(f"sf:skill:{skill_id}:structure")

    if scope in ("all", "patch"):
        await cache_delete_pattern(f"sf:wb:patch:*")

    if scope == "all":
        await cache_delete_pattern(f"sf:wb:cmd:{skill_id}:*")
        await cache_delete_pattern(f"sf:wb:skill:{skill_id}:*")
```

### 7.4 前端缓存

| 存储 | Key | 内容 | 策略 |
|------|-----|------|------|
| `localStorage` | `sf-wb-view-mode` | Block/Code 视图偏好 | 持久 |
| `localStorage` | `sf-wb-panels` | 面板开合状态 | 持久 |
| `localStorage` | `sf-editor-dark` | 编辑器主题 | 持久（已有） |
| `localStorage` | `sf-editor-font-size` | 编辑器字号 | 持久（已有） |
| `sessionStorage` | `sf-wb-draft:{skillId}` | 未保存的 draft | 页面关闭清除 |
| 内存 | Pinia store | 所有运行时状态 | 页面刷新清除 |

**Draft 防丢失**：每 30 秒自动将 dirty modules 写入 sessionStorage，刷新页面时恢复。

---

## 8. AI 方案

### 8.1 AI 能力矩阵

| 能力 | 触发方式 | 输入 | 输出 | 使用的模型 |
|------|---------|------|------|-----------|
| **对话问答** | 右侧 Assistant 自由输入 | 消息 + Skill 上下文 | 文本回答 | LiteLLM (通用) |
| **意图识别** | 自由输入（后端自动） | 用户消息 | target_module + intent + confidence | 关键词匹配优先，fallback LLM |
| **Patch 生成** | 对话/Cmd+K/slash command | 意图 + 目标模块 + 当前文档 + 选区 | 结构化 patch + diff | LiteLLM (JSON mode) |
| **Draft 生成** | 创建模式首次对话 | 需求描述 + 引用 | 完整 SkillStructure | LiteLLM (JSON mode) |
| **测试生成** | `/generate-tests` | 当前 rules + params | TestCase[] patch | LiteLLM (JSON mode) |
| **反例发现** | `/discover-antipatterns` | 当前 rules | Antipattern[] | LiteLLM |
| **分支建议** | `/suggest-branches` | 当前 rules 第一个 step | Branch[] patch | LiteLLM |
| **漂移检测** | `/drift-check` | data_inputs + DataSource | 漂移报告 | 规则引擎（无 LLM） |
| **AIClaw 执行** | `/run-aiclaw` | Skill ID + params | 执行结果 | 外部 AIClaw |
| **参数优化** | `/optimize` | params + 历史执行数据 | 优化建议 | LiteLLM |

### 8.2 上下文构建协议

每次 AI 调用的上下文由以下层级组成：

```
System Prompt（角色 + 规则）
├── Skill 完整结构（从 draft_snapshot 或 DB）
├── 当前活跃模块详情
├── 选区内容（如有）
├── 最近 5 条对话历史
├── 最近验证/测试失败（如有）
├── 引用的外部模块（如有）
└── 操作指令（基于 intent）
```

**关键原则**：

1. **Draft 优先于 DB**：AI 看到的永远是用户当前正在编辑的版本，不是已保存版本
2. **最小上下文**：只传目标模块 + 相关模块，不传整个 Skill（节省 token）
3. **JSON 输出约束**：所有 patch 生成必须返回 JSON，不返回 SKILL.md 全文
4. **两阶段生成**：LLM 输出 JSON → 代码确定性渲染（遵循已有规范）

### 8.3 Prompt 模板

#### 8.3.1 上下文感知对话 Prompt

```python
CHAT_SYSTEM_PROMPT = """你是 SkillForge 工作台助手。用户正在编辑一个 AI Skill。

当前 Skill 信息：
- ID: {skill_id}
- 名称: {skill_name}
- 部门: {department}
- 状态: {status}

当前用户正在编辑的模块：{active_module}

{module_context}

{selection_context}

{failure_context}

你的职责：
1. 理解用户的意图（修改参数？调整规则？添加测试？）
2. 如果需要修改，生成结构化 patch（JSON 格式）
3. 如果是问答，直接回答

输出格式要求：
- 如果需要修改，返回 JSON: {{"type": "patch", "target_module": "...", "summary": "...", "patch": {{...}}}}
- 如果是问答，返回 JSON: {{"type": "answer", "message": "..."}}

重要规则：
- 每次只修改一个模块
- patch 中只包含变更的部分，不要返回完整文档
- 用中文回答
"""
```

#### 8.3.2 内联编辑 Prompt（Cmd+K）

```python
INLINE_EDIT_PROMPT = """用户选中了以下内容并要求修改：

选中内容（模块 {module_id}，第 {start_line}-{end_line} 行）：
```
{selected_text}
```

用户指令：{user_instruction}

完整模块上下文：
{module_context}

请生成修改后的内容。只返回修改后的内容片段，不要返回整个模块。
输出 JSON: {{"replacement": "修改后的文本", "explanation": "修改说明"}}
"""
```

### 8.4 AI 调用遥测

每次 AI 调用记录到 `skill_workbench_ai_runs` 表：

| 字段 | 内容 |
|------|------|
| `run_type` | chat / patch / draft / command / inline_edit |
| `model_id` | LiteLLM 模型标识 |
| `prompt_key` | 使用的 prompt 模板名 |
| `token_count` | 消耗 token 数 |
| `latency_ms` | 响应延迟 |
| `success` | 是否成功 |
| `error_message` | 错误信息（如有） |

### 8.5 Slash Commands 与 AI 能力映射

| Slash Command | 后端映射 | LLM 调用 | 缓存 |
|---------------|---------|---------|------|
| `/generate-tests` | `skills/router.py → generate_tests()` | 是（comprehensive strategy） | 否 |
| `/discover-antipatterns` | `skills/router.py → discover_antipatterns()` | 是 | 10min |
| `/suggest-branches` | `skills/router.py → suggest_branches()` | 是 | 10min |
| `/drift-check` | `skills/router.py → drift_check()` | 否（规则引擎） | 10min |
| `/run-aiclaw` | `skills/router.py → run_on_aiclaw()` | 否（外部调用） | 否 |
| `/optimize` | optimizer session | 是 | 否 |
| `/validate` | `workbench/service.py → validate_patch()` | 否 | 否 |
| `/history` | 切换 Navigator 到版本区域 | 否 | 否 |

---

## 9. 测试验证方案

### 9.1 后端测试

#### 9.1.1 单元测试

```python
# tests/test_workbench_chat.py

class TestWorkbenchChat:
    """上下文感知对话端点测试"""

    async def test_chat_with_explicit_intent(self, client, auth_headers, sample_skill):
        """前端传了 intent，不走 intent_service"""
        resp = await client.post(
            f"/api/skills/{sample_skill.id}/workbench/chat",
            headers=auth_headers,
            json={
                "session_id": "test_session",
                "message": "改成 1.5",
                "context": {
                    "active_module": "params",
                    "draft_snapshot": {"params": [{"name": "roi_threshold", "value": "1.2"}]}
                },
                "intent": "tune_threshold"
            }
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "patch"
        assert data["patch"]["target_module"] == "params"

    async def test_chat_free_form_triggers_intent_service(self, client, auth_headers, sample_skill):
        """自由输入走 intent_service"""
        resp = await client.post(
            f"/api/skills/{sample_skill.id}/workbench/chat",
            headers=auth_headers,
            json={
                "session_id": "test_session",
                "message": "这个参数太宽松了",
                "context": {"active_module": "params"},
                "intent": None
            }
        )
        assert resp.status_code == 200
        assert resp.json()["type"] in ("patch", "answer")

    async def test_chat_pure_question_returns_answer(self, client, auth_headers, sample_skill):
        """纯问题返回 answer 类型"""
        resp = await client.post(
            f"/api/skills/{sample_skill.id}/workbench/chat",
            headers=auth_headers,
            json={
                "session_id": "test_session",
                "message": "当前 ROI 阈值是多少？",
                "context": {"active_module": "params"}
            }
        )
        assert resp.status_code == 200
        assert resp.json()["type"] == "answer"

    async def test_chat_with_selection_context(self, client, auth_headers, sample_skill):
        """带选区上下文的 Cmd+K 请求"""
        resp = await client.post(
            f"/api/skills/{sample_skill.id}/workbench/chat",
            headers=auth_headers,
            json={
                "session_id": "test_session",
                "message": "改成更严格但保留兼容性",
                "context": {
                    "active_module": "rules",
                    "selection": {
                        "module_id": "rules",
                        "start_line": 5,
                        "end_line": 10,
                        "text": "├─ ROI > 盈亏线 × 1.2 → 绿灯"
                    }
                },
                "intent": "rewrite_selection"
            }
        )
        assert resp.status_code == 200
        assert resp.json()["type"] == "patch"

    async def test_chat_draft_priority_over_db(self, client, auth_headers, sample_skill):
        """draft_snapshot 优先于 DB 数据"""
        # 前端传的 draft 中 roi_threshold=2.0，DB 中是 1.2
        resp = await client.post(
            f"/api/skills/{sample_skill.id}/workbench/chat",
            headers=auth_headers,
            json={
                "session_id": "test_session",
                "message": "当前阈值是多少？",
                "context": {
                    "active_module": "params",
                    "draft_snapshot": {"params": [{"name": "roi_threshold", "value": "2.0"}]}
                }
            }
        )
        # AI 应该基于 draft 中的 2.0 回答，而非 DB 中的 1.2
        assert resp.status_code == 200


class TestWorkbenchCommand:
    """Slash command 端点测试"""

    async def test_generate_tests_command(self, client, auth_headers, sample_skill):
        resp = await client.post(
            f"/api/skills/{sample_skill.id}/workbench/command",
            headers=auth_headers,
            json={
                "session_id": "test_session",
                "command": "generate-tests",
                "context": {"active_module": "rules"}
            }
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "patch"
        assert data["patch"]["target_module"] == "test_cases"

    async def test_unknown_command_returns_error(self, client, auth_headers, sample_skill):
        resp = await client.post(
            f"/api/skills/{sample_skill.id}/workbench/command",
            headers=auth_headers,
            json={
                "session_id": "test_session",
                "command": "nonexistent",
                "context": {}
            }
        )
        assert resp.status_code == 200
        assert resp.json()["type"] == "error"

    async def test_drift_check_no_llm(self, client, auth_headers, sample_skill):
        """drift-check 不调 LLM，用规则引擎"""
        resp = await client.post(
            f"/api/skills/{sample_skill.id}/workbench/command",
            headers=auth_headers,
            json={
                "session_id": "test_session",
                "command": "drift-check",
                "context": {"active_module": "params"}
            }
        )
        assert resp.status_code == 200


class TestPartialApply:
    """Hunk 级部分应用测试"""

    async def test_accept_some_hunks(self, client, auth_headers, sample_skill, sample_patch):
        """部分接受 patch"""
        resp = await client.post(
            f"/api/skills/{sample_skill.id}/workbench/apply-partial",
            headers=auth_headers,
            json={
                "session_id": "test_session",
                "patch_id": sample_patch.id,
                "accepted_hunks": [0, 2],
                "rejected_hunks": [1]
            }
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "partial_applied"
        assert data["applied_hunks"] == [0, 2]

    async def test_reject_all_hunks(self, client, auth_headers, sample_skill, sample_patch):
        """拒绝所有 hunk"""
        resp = await client.post(
            f"/api/skills/{sample_skill.id}/workbench/apply-partial",
            headers=auth_headers,
            json={
                "session_id": "test_session",
                "patch_id": sample_patch.id,
                "accepted_hunks": [],
                "rejected_hunks": [0, 1, 2]
            }
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "partial_applied"
        assert resp.json()["applied_hunks"] == []
```

#### 9.1.2 context_builder 测试

```python
# tests/test_workbench_context.py

class TestContextBuilder:
    """上下文构建测试"""

    async def test_draft_overrides_db(self, context_builder, sample_skill):
        """draft_snapshot 覆盖 DB 数据"""
        ctx = await context_builder.build_chat_context(
            sample_skill.id, "test_session",
            draft_snapshot={"params": [{"name": "roi_threshold", "value": "99.9"}]}
        )
        params = ctx["skill_structure"]["params"]
        assert params[0]["value"] == "99.9"

    async def test_selection_context_expansion(self, context_builder, sample_skill):
        """选区上下文扩展"""
        ctx = await context_builder.build_chat_context(
            sample_skill.id, "test_session",
            selection={"module_id": "rules", "start_line": 5, "end_line": 10, "text": "some rule"}
        )
        assert "selection_context" in ctx
        assert ctx["selection"]["text"] == "some rule"

    async def test_empty_draft_uses_db(self, context_builder, sample_skill):
        """无 draft 时使用 DB 数据"""
        ctx = await context_builder.build_chat_context(
            sample_skill.id, "test_session",
            draft_snapshot=None
        )
        assert ctx["skill_structure"] is not None

    async def test_recent_failures_included(self, context_builder, sample_skill):
        """验证失败记录包含在上下文中"""
        failures = [{"type": "validation", "module": "params", "message": "阈值过低"}]
        ctx = await context_builder.build_chat_context(
            sample_skill.id, "test_session",
            recent_failures=failures
        )
        assert len(ctx["recent_failures"]) == 1
```

### 9.2 前端测试

#### 9.2.1 Store 测试

```js
// web/src/__tests__/stores/document.test.js

import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useDocumentStore } from '@/stores/document'

describe('Document Store', () => {
  beforeEach(() => setActivePinia(createPinia()))

  describe('模块编辑', () => {
    it('updateModuleStructured 更新结构化数据并标记 dirty', () => {
      const store = useDocumentStore()
      store.updateModuleStructured('goal', '新的目标描述')

      expect(store.skillDocument.goal.structuredValue).toBe('新的目标描述')
      expect(store.dirty).toBe(true)
      expect(store.dirtyModules.has('goal')).toBe(true)
      expect(store.draftRevision).toBe(1)
    })

    it('updateModuleStructured 递增 draftRevision', () => {
      const store = useDocumentStore()
      store.updateModuleStructured('goal', 'v1')
      store.updateModuleStructured('goal', 'v2')

      expect(store.draftRevision).toBe(2)
    })

    it('markSaved 清除所有 dirty 状态', () => {
      const store = useDocumentStore()
      store.updateModuleStructured('goal', '修改')
      store.updateModuleStructured('params', [{ name: 'x', value: '1' }])
      store.markSaved()

      expect(store.dirty).toBe(false)
      expect(store.dirtyModules.size).toBe(0)
      expect(store.lastSavedAt).toBeTruthy()
    })
  })

  describe('模块状态计算', () => {
    it('空模块状态为 empty', () => {
      const store = useDocumentStore()
      const states = store.moduleStates
      expect(states.find(s => s.key === 'goal').status).toBe('empty')
    })

    it('有内容且 dirty 的模块状态为 modified', () => {
      const store = useDocumentStore()
      store.updateModuleStructured('goal', '目标')
      const states = store.moduleStates
      expect(states.find(s => s.key === 'goal').status).toBe('modified')
    })
  })

  describe('Patch 应用', () => {
    it('applyPatch 只更新目标模块', () => {
      const store = useDocumentStore()
      store.updateModuleStructured('goal', '旧目标')
      store.updateModuleStructured('params', [{ name: 'x', value: '1' }])
      store.markSaved()

      store.applyPatch({
        target_module: 'params',
        patch_json: { params: [{ name: 'x', value: '2' }] }
      })

      expect(store.skillDocument.params.structuredValue[0].value).toBe('2')
      expect(store.dirtyModules.has('params')).toBe(true)
      expect(store.dirtyModules.has('goal')).toBe(false)
    })
  })

  describe('API 数据加载', () => {
    it('loadFromApi 正确映射所有模块', () => {
      const store = useDocumentStore()
      store.loadFromApi({
        id: 'EC-投放-01',
        name: '投放决策',
        department: 'EC',
        status: 'draft',
        purpose: '根据ROI判断投放动作',
        steps: [{ id: 'step_1', name: '判断ROI', branches: [] }],
        output_definition: [{ name: '结论', format: 'text' }],
        test_cases: [],
        frontmatter: { trigger_type: 'schedule', risk_level: 'medium' }
      })

      expect(store.skillDocument.meta.structuredValue.id).toBe('EC-投放-01')
      expect(store.skillDocument.goal.structuredValue).toBe('根据ROI判断投放动作')
      expect(store.skillDocument.rules.structuredValue).toHaveLength(1)
      expect(store.parseState).toBe('parsed')
      expect(store.dirty).toBe(false)
    })
  })
})
```

#### 9.2.2 SchemaMapper 测试

```js
// web/src/__tests__/utils/schemaMapper.test.js

import { describe, it, expect } from 'vitest'
import {
  apiResponseToDocument,
  documentToStructuredPayload,
  documentToMarkdown,
  markdownToDocument
} from '@/utils/schemaMapper'

describe('SchemaMapper', () => {
  const sampleApiResponse = {
    id: 'EC-投放-01',
    name: '投放决策',
    department: 'EC',
    status: 'active',
    version: 'v1.0',
    purpose: '根据ROI判断投放动作',
    steps: [{ id: 'step_1', name: '判断ROI', branches: [{ condition: 'ROI > 1.2', conclusion: '绿灯' }] }],
    output_definition: [{ name: '结论', format: 'text', recipient: '运营组', approval_level: '无' }],
    test_cases: [{ name: '标准测试', input_data: { roi: 1.5 }, expected_output: { conclusion: '绿灯' } }],
    frontmatter: { trigger_type: 'schedule', risk_level: 'medium', owner: '张三' },
    policy_pack: { params: { roi_threshold: 1.2 } }
  }

  it('apiResponseToDocument 完整映射', () => {
    const doc = apiResponseToDocument(sampleApiResponse)
    expect(doc.meta.structuredValue.id).toBe('EC-投放-01')
    expect(doc.goal.structuredValue).toBe('根据ROI判断投放动作')
    expect(doc.rules.structuredValue).toHaveLength(1)
    expect(doc.output_table.structuredValue).toHaveLength(1)
    expect(doc.test_cases.structuredValue).toHaveLength(1)
  })

  it('documentToStructuredPayload 反向映射', () => {
    const doc = apiResponseToDocument(sampleApiResponse)
    const payload = documentToStructuredPayload(doc)
    expect(payload.purpose).toBe('根据ROI判断投放动作')
    expect(payload.steps).toHaveLength(1)
    expect(payload.frontmatter.trigger_type).toBe('schedule')
  })

  it('Markdown 双向转换不丢数据', () => {
    const doc = apiResponseToDocument(sampleApiResponse)
    const md = documentToMarkdown(doc)
    const restored = markdownToDocument(md)
    expect(restored.goal.structuredValue).toBe(doc.goal.structuredValue)
    expect(restored.rules.structuredValue).toHaveLength(doc.rules.structuredValue.length)
  })
})
```

#### 9.2.3 组件测试

```js
// web/src/__tests__/components/WorkbenchAssistantPane.test.js

import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import WorkbenchAssistantPane from '@/components/workbench/WorkbenchAssistantPane.vue'

describe('WorkbenchAssistantPane', () => {
  it('发送消息时携带当前模块和选区上下文', async () => {
    const sendFn = vi.fn()
    const wrapper = mount(WorkbenchAssistantPane, {
      global: { plugins: [createPinia()] },
      props: {
        messages: [],
        loading: false,
        activeModule: 'params',
        selection: { moduleId: 'params', text: 'roi_threshold: 1.2' },
        references: []
      }
    })

    wrapper.vm.$emit = sendFn
    // 模拟输入并发送
    // 验证 emit('send', message, { targetModule, selectionRange }) 被调用
  })

  it('slash command 被识别并标记', () => {
    // 输入 "/generate-tests" 时应显示 command 提示
  })

  it('加载状态显示打字动画', () => {
    const wrapper = mount(WorkbenchAssistantPane, {
      global: { plugins: [createPinia()] },
      props: { messages: [], loading: true, activeModule: 'meta', selection: null, references: [] }
    })
    expect(wrapper.find('.typing-indicator').exists()).toBe(true)
  })
})
```

### 9.3 E2E 测试场景

| # | 场景 | 步骤 | 验收标准 |
|---|------|------|---------|
| E1 | **新手创建 Skill** | 访问 /skills/new → 在 Assistant 描述需求 → AI 生成 Draft → Accept → 补全各模块 → Validate → Save | Skill 创建成功，所有模块有内容，通过验证 |
| E2 | **专业用户编辑参数** | 访问 /skills/:id → 选 @params 模块 → Block Editor 修改值 → Save | 参数正确保存到 policy_pack.yaml 和 SKILL.md |
| E3 | **Cmd+K 内联编辑** | 切 Code 模式 → 选中规则文本 → Cmd+K → 输入修改指令 → Inline Diff → Accept | 选中部分被替换，其余不变 |
| E4 | **对话修改参数** | 在 Assistant 输入 "ROI 阈值改成 1.5" → 生成 Patch → Inline Diff → Accept → Auto Validate | 参数更新，验证通过 |
| E5 | **Slash Command** | 在 Assistant 输入 `/generate-tests` → 生成测试用例 Patch → Accept | test_cases 模块填充新用例 |
| E6 | **部分接受 Patch** | AI 生成多 hunk patch → Accept hunk 0,2 → Reject hunk 1 | 只有被接受的 hunk 生效 |
| E7 | **视图切换不丢数据** | Block 模式编辑 → 切 Code 模式 → 检查 Markdown → 切回 Block → 检查结构化数据 | 双视图数据一致 |
| E8 | **底部面板测试** | 打开 Test Tab → 运行测试 → 查看结果 → 切到 Validation Tab → 运行验证 | 面板正常切换，数据独立 |
| E9 | **Draft 防丢失** | 编辑后不保存 → 刷新页面 → 检查是否恢复 | sessionStorage 恢复 dirty 数据 |
| E10 | **旧路由重定向** | 访问 /skill/:id/chat → 自动跳转到 /skills/:id?panel=assistant | 重定向正确，面板打开 |

### 9.4 性能基线

| 指标 | 目标 | 测量方式 |
|------|------|---------|
| 页面首次加载 (LCP) | < 2s | Lighthouse |
| 模块切换响应 | < 100ms | Performance API |
| AI 对话首 token | < 2s | 前端计时 |
| Patch 生成完成 | < 5s | 前端计时 |
| Block↔Code 视图切换 | < 300ms | Performance API |
| 保存操作 | < 1s | 前端计时 |
| Monaco 编辑器初始化 | < 500ms | 延迟加载，不阻塞首屏 |

---

## 10. 实施计划与里程碑

### 10.1 分阶段实施

```
P0 ─────────── P1 ────────────── P2 ──────────── P3 ──────────
基座搭建        AI 深度集成        高级编辑         切换上线
(~2 周)        (~2 周)           (~1.5 周)       (~1 周)
```

### 10.2 P0: 基座搭建（统一路由 + Document Store + 四区域布局）

**目标**：能加载现有 Skill，Block Editor 编辑并保存。

| 任务 | 类型 | 涉及文件 | 产出 |
|------|------|---------|------|
| T01: 创建 Document Store | 前端 | `stores/document.js` | 统一文档模型 |
| T02: 创建 UI Store | 前端 | `stores/ui.js` | UI 状态管理 |
| T03: 创建 SchemaMapper | 前端 | `utils/schemaMapper.js` | API↔Document 映射 |
| T04: 创建 WorkbenchShell | 前端 | `components/workbench/WorkbenchShell.vue` | 四区域布局框架 |
| T05: 创建 WorkbenchTopBar | 前端 | `components/workbench/WorkbenchTopBar.vue` | 精简顶栏 |
| T06: 创建 WorkbenchNavigator | 前端 | `components/workbench/WorkbenchNavigator.vue` | 左侧导航 |
| T07: 创建 WorkbenchEditorSurface | 前端 | `components/workbench/WorkbenchEditorSurface.vue` | Block 视图容器 |
| T08: 创建 SkillWorkbench 主页面 | 前端 | `pages/skill/SkillWorkbench.vue` | 主页面组装 |
| T09: 添加新路由 | 前端 | `router/index.js` | `/skills/new` + `/skills/:id` |
| T10: Block 组件接入新 Store | 前端 | 7 个 Block 组件 | 改用 document store |
| T11: Store 单元测试 | 测试 | `__tests__/stores/` | document + ui store 测试 |
| T12: SchemaMapper 测试 | 测试 | `__tests__/utils/` | 双向映射测试 |

**P0 验收**：打开 `/skills/:id` → 加载 Skill → Block Editor 编辑各模块 → Save → 数据正确持久化。

### 10.3 P1: AI 深度集成（Assistant + Inline Diff + Cmd+K）

**目标**：Agent 对话可感知上下文，修改以 Inline Diff 展示。

| 任务 | 类型 | 涉及文件 | 产出 |
|------|------|---------|------|
| T13: 创建 WorkbenchAssistantPane | 前端 | `components/workbench/WorkbenchAssistantPane.vue` | 右侧 AI 面板 |
| T14: 创建 WorkbenchInlineDiff | 前端 | `components/workbench/WorkbenchInlineDiff.vue` | Inline Diff 组件 |
| T15: 创建 WorkbenchInlinePrompt | 前端 | `components/workbench/WorkbenchInlinePrompt.vue` | Cmd+K 输入条 |
| T16: 重构 Workbench Store | 前端 | `stores/workbench.js` | 去掉文档持有，增加 chat/patch 方法 |
| T17: 后端 chat 端点 | 后端 | `workbench/router.py`, `service.py` | `POST /workbench/chat` |
| T18: 后端 command 端点 | 后端 | `workbench/router.py`, `service.py` | `POST /workbench/command` |
| T19: 增强 context_builder | 后端 | `workbench/context_builder.py` | draft + selection 上下文 |
| T20: 增强 schemas | 后端 | `workbench/schemas.py` | 新请求/响应模型 |
| T21: 创建模式（Welcome + Draft 生成） | 前端 | `IDEWelcome.vue` 复用 | `/skills/new` 完整流程 |
| T22: Slash command 分发 | 后端 | `workbench/service.py` | command 映射到现有方法 |
| T23: 后端单元测试 | 测试 | `tests/test_workbench_chat.py` | chat/command/context 测试 |
| T24: 前端组件测试 | 测试 | `__tests__/components/` | AssistantPane 测试 |

**P1 验收**：在 Assistant 输入需求 → 生成 Patch → Inline Diff 展示 → Accept → 模块更新 → Auto Validate。

### 10.4 P2: 高级编辑（Monaco 双视图 + Hunk Accept + 底部面板）

**目标**：高级用户可用 Monaco，底部面板承载测试/沙箱/验证。

| 任务 | 类型 | 涉及文件 | 产出 |
|------|------|---------|------|
| T25: EditorSurface 增加 Monaco 视图 | 前端 | `WorkbenchEditorSurface.vue` | Block↔Code 切换 |
| T26: 前端 Markdown 解析器 | 前端 | `utils/markdownParser.js` | SKILL.md↔Document 双向转换 |
| T27: 创建 WorkbenchBottomPanel | 前端 | `components/workbench/WorkbenchBottomPanel.vue` | Validation/Test/Sandbox/Logs |
| T28: 创建 WorkbenchCommandPalette | 前端 | `components/workbench/WorkbenchCommandPalette.vue` | ⌘P 命令面板 |
| T29: 后端 apply-partial 端点 | 后端 | `workbench/router.py`, `apply_service.py` | Hunk 级部分应用 |
| T30: 数据库迁移 014 | 数据库 | `migrations/versions/014_...py` | messages/patches 新字段 |
| T31: 缓存层实现 | 后端 | `workbench/cache.py` | 工作台缓存键 + 失效策略 |
| T32: Navigator 版本/文件区域 | 前端 | `WorkbenchNavigator.vue` | 历史 + 文件树 |
| T33: 响应式布局 | 前端 | 各组件 CSS | 适配 1024-1440px |
| T34: 快捷键系统 | 前端 | `SkillWorkbench.vue` | 全部快捷键绑定 |
| T35: E2E 测试 | 测试 | `tests/e2e/` | E1-E9 场景 |
| T36: 性能测试 | 测试 | Lighthouse + 手动 | 性能基线达标 |

**P2 验收**：Monaco 编辑 + Block↔Code 切换数据一致 + Hunk Accept/Reject + 底部面板全功能。

### 10.5 P3: 切换上线（旧路由迁移 + 废弃旧页面）

**目标**：完全切换到新工作台，旧入口全部重定向。

| 任务 | 类型 | 涉及文件 | 产出 |
|------|------|---------|------|
| T37: 旧路由重定向 | 前端 | `router/index.js` | 所有旧路由 → 新路由 |
| T38: SkillList 链接更新 | 前端 | `SkillList.vue` | 新建/编辑链接指向新页面 |
| T39: AppLayout 导航更新 | 前端 | `AppLayout.vue` | Skills 导航项 |
| T40: Draft 防丢失 | 前端 | `stores/document.js` | sessionStorage 自动备份/恢复 |
| T41: 旧 IDE Store 废弃 | 前端 | `stores/ide.js` | 标记 deprecated，下个版本删除 |
| T42: 回归测试 | 测试 | 全量 | 所有旧功能在新页面可用 |
| T43: 旧路由 E2E | 测试 | `tests/e2e/` | E10 重定向测试 |

**P3 验收**：所有旧路由正确重定向 + 新工作台承载全部 Skill 操作 + 无功能回退。

---

## 11. 风险与降级策略

| 风险 | 影响 | 概率 | 降级策略 |
|------|------|------|---------|
| **Block↔Monaco 双向同步数据不一致** | 高 | 中 | P0 只做 Block，P2 才加 Monaco；Monaco 模式下禁用 Block 编辑避免冲突 |
| **前端 SKILL.md 解析器与后端不一致** | 高 | 中 | 前端解析仅用于 Monaco 预览；保存时始终以后端 parser.render() 为准 |
| **AI Patch 生成质量不稳定** | 中 | 高 | Patch 必须经过 Inline Diff 确认才能 apply；fallback 到结构化 heuristic 生成 |
| **工作台页面体积过大** | 中 | 中 | Monaco 和 Flow 组件异步加载；底部面板按需挂载 |
| **旧页面用户习惯迁移** | 低 | 中 | P3 保留旧路由重定向 30 天；旧页面文件不立即删除 |
| **编辑锁冲突** | 低 | 低 | 进入工作台自动 acquireLock；离开时 releaseLock；锁过期 30 分钟不变 |

---

## 附录 A: 文件变更清单

### 新建文件

| 路径 | 类型 |
|------|------|
| `web/src/pages/skill/SkillWorkbench.vue` | 前端主页面 |
| `web/src/stores/document.js` | 前端 Store |
| `web/src/stores/ui.js` | 前端 Store |
| `web/src/utils/schemaMapper.js` | 前端工具 |
| `web/src/utils/markdownParser.js` | 前端工具（P2） |
| `web/src/components/workbench/WorkbenchShell.vue` | 前端组件 |
| `web/src/components/workbench/WorkbenchTopBar.vue` | 前端组件 |
| `web/src/components/workbench/WorkbenchNavigator.vue` | 前端组件 |
| `web/src/components/workbench/WorkbenchEditorSurface.vue` | 前端组件 |
| `web/src/components/workbench/WorkbenchAssistantPane.vue` | 前端组件 |
| `web/src/components/workbench/WorkbenchBottomPanel.vue` | 前端组件 |
| `web/src/components/workbench/WorkbenchCommandPalette.vue` | 前端组件 |
| `web/src/components/workbench/WorkbenchInlineDiff.vue` | 前端组件 |
| `web/src/components/workbench/WorkbenchInlinePrompt.vue` | 前端组件 |
| `app/workbench/cache.py` | 后端缓存 |
| `migrations/versions/014_enhance_workbench_for_fusion.py` | 数据库迁移 |
| `tests/test_workbench_chat.py` | 后端测试 |
| `web/src/__tests__/stores/document.test.js` | 前端测试 |
| `web/src/__tests__/utils/schemaMapper.test.js` | 前端测试 |

### 修改文件

| 路径 | 改动 |
|------|------|
| `web/src/router/index.js` | 新路由 + 旧路由重定向 |
| `web/src/stores/workbench.js` | 去掉文档持有，增强 chat/patch |
| `web/src/pages/skill/SkillList.vue` | 链接指向更新 |
| `web/src/layouts/AppLayout.vue` | 导航项更新 |
| `web/src/api/index.js` | 新增 workbench chat/command/apply-partial API |
| `web/src/components/ide/IDEBlockEditor.vue` | 适配新 Document Store |
| `web/src/components/ide/blocks/*.vue` | 改用 document store（7 个文件） |
| `app/workbench/router.py` | 新增 3 个端点 |
| `app/workbench/service.py` | 增加 handle_chat / handle_command |
| `app/workbench/schemas.py` | 新增请求/响应模型 |
| `app/workbench/context_builder.py` | 增加 draft + selection 支持 |
| `app/workbench/apply_service.py` | 增加 hunk 级部分应用 |
| `app/workbench/models.py` | messages/patches 新字段 |

### 废弃文件（P3 后删除）

| 路径 | 原因 |
|------|------|
| `web/src/pages/skill/SkillDetail.vue` | 功能并入 WorkbenchEditorSurface |
| `web/src/pages/skill/SkillNew.vue` | 功能并入 Workbench create mode |
| `web/src/pages/skill/SkillEdit.vue` | 功能并入 Block 模块 |
| `web/src/pages/skill/SkillChat.vue` | 功能并入 AssistantPane |
| `web/src/pages/skill/SkillWorkspace.vue` | Tab 容器不再需要 |
| `web/src/pages/skill/SkillIDE.vue` | 升级为 SkillWorkbench |
| `web/src/stores/ide.js` | 职责拆分到 document + ui store |

---

## 附录 B: API 端点汇总

### 新增端点

| 方法 | 路径 | 功能 | 阶段 |
|------|------|------|------|
| POST | `/api/skills/{id}/workbench/chat` | 上下文感知对话 | P1 |
| POST | `/api/skills/{id}/workbench/command` | Slash command 执行 | P1 |
| POST | `/api/skills/{id}/workbench/apply-partial` | Hunk 级部分应用 | P2 |

### 复用端点（不变）

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/workbench/generate-draft` | Draft 生成 |
| POST | `/api/workbench/create-skill` | Skill 创建 |
| GET | `/api/workbench/references` | 引用库 |
| POST | `/api/skills/{id}/workbench/session` | 创建 Session |
| GET | `/api/skills/{id}/workbench/session/{sid}` | 获取 Session |
| POST | `/api/skills/{id}/workbench/intent` | 意图分析 |
| POST | `/api/skills/{id}/workbench/patch` | 生成 Patch |
| POST | `/api/skills/{id}/workbench/validate` | 验证 Patch |
| POST | `/api/skills/{id}/workbench/apply` | 应用 Patch（全量） |
| PUT | `/api/skills/{id}/structured` | 保存结构化数据 |
| PUT | `/api/skills/{id}/content` | 保存原始内容 |
| POST | `/api/skills/{id}/lock` | 获取编辑锁 |
| DELETE | `/api/skills/{id}/lock` | 释放编辑锁 |
