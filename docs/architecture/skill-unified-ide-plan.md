# Skill 统一 IDE — 三合一实现计划

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


## Context

当前 Skill 编辑有三条割裂路径：
- `/skill/new` → **表单创建**（SkillNew.vue，4步向导）
- `/skill/:id/edit` → **代码编辑**（SkillEdit.vue，Monaco + 结构化表单）
- `/skills/workbench/:id` → **对话编辑**（SkillWorkbench.vue，AI Chat + Patch）

用户指出这就像 VS Code 手动改代码 vs Agent 对话 vs CLI 命令行，应该像 Cursor 一样融合为一个界面。

## 目标架构

```
┌─ 顶栏 ──────────────────────────────────────────────────────┐
│ ← Skills / 投放优化 Skill         [验证] [保存] ● 未保存     │
├──────┬──────────────────────────────┬────────────────────────┤
│      │                              │                        │
│ 模块 │   结构化 Block 编辑器         │   AI 对话面板          │
│ 导航 │   ┌──────────────────┐       │   (可折叠)             │
│      │   │ 目标             │       │                        │
│ @目标│   │ [textarea]       │       │   用户: 把阈值改成1.2  │
│ @规则│   └──────────────────┘       │   AI: 已修改 params    │
│ @参数│   ┌──────────────────┐       │       ┌─ diff ──┐      │
│ @输出│   │ 参数 ← diff 高亮 │       │       │roi: 1.2│      │
│ @测试│   │ roi: 1.2 (was 1.5│       │       └────────┘      │
│ @工作│   └──────────────────┘       │       [应用] [放弃]    │
│  流  │                              │                        │
├──────┴──────────────────────────────┴────────────────────────┤
│ 结构校验通过 │ 5 个参数 │ 3 条规则 │ 上次保存: 2 分钟前      │
└──────────────────────────────────────────────────────────────┘
```

核心原则（Codex 分析结论）：
- **`skillDocument` 是唯一数据源** — Block 编辑和 AI Patch 都写入同一个 store 对象
- **SKILL.md 是渲染产物** — 保存时由结构化数据渲染，不是编辑的一手来源
- **一个保存管线** — 直接编辑走 `PUT /structured`，AI Patch 走 `apply_patch` → 内部也调 `save_skill_content`

## 路由设计

| 新路由 | 用途 |
|--------|------|
| `/skills/ide` | 创建新 Skill（无 id） |
| `/skills/ide/:id` | 编辑现有 Skill |

旧路由全部 redirect：
```
/skill/new, /skills/new        → /skills/ide
/skills/generate               → /skills/ide
/skills/workbench/:id          → /skills/ide/:id
/skill/:id/edit                → /skills/ide/:id
```

## 组件树

```
src/pages/skill/SkillIDE.vue              ← 顶层页面
src/stores/ide.js                         ← IDE 专用 Pinia store
src/utils/schemaMapper.js                 ← SkillStructure ↔ SkillStructured 映射

src/components/ide/
├── IDEHeader.vue                         ← 面包屑 + 保存按钮 + 脏状态
├── IDEModuleNav.vue                      ← 左侧模块导航（基于现有 WorkbenchModuleNav）
├── IDEBlockEditor.vue                    ← 中间区域：按 activeModule 渲染对应 Block
├── IDEChatPanel.vue                      ← 右侧对话面板（基于现有 chat 逻辑）
├── IDEWelcome.vue                        ← 创建模式欢迎屏
├── IDEPatchOverlay.vue                   ← Block 上的 AI diff 浮层
├── IDEValidationBadge.vue                ← 内联校验状态
└── blocks/
    ├── MetaBlock.vue                     ← 基础信息表单
    ├── GoalBlock.vue                     ← 目标 textarea
    ├── RulesBlock.vue                    ← 规则步骤+分支嵌套编辑
    ├── ParamsBlock.vue                   ← 参数键值表
    ├── OutputTableBlock.vue              ← 输出字段表格
    ├── TestCasesBlock.vue                ← 测试用例卡片
    └── WorkflowBlock.vue                ← 工作流预览 + 打开画布
```

复用现有组件（不修改）：
- `WorkbenchReferencePicker.vue` — 引用选择器
- `WorkflowPreviewPanel.vue` — 工作流预览
- `ConditionEditor.vue` — 规则条件构建器

## Store 设计

### `stores/ide.js`（新建）

```js
state: {
  mode: 'create' | 'edit',        // 由路由决定
  skillId: '',
  loaded: false,
  saving: false,
  dirty: false,
  dirtyModules: Set(),             // 哪些模块有未保存修改
  activeModule: 'meta',            // 当前展示的 Block
  chatPanelOpen: true,
  blockValidation: {},             // 每模块验证状态
  createForm: { skill_id, name, department, ... },  // 创建模式专用
}
```

**不另造 skillDocument** — 直接引用 `workbenchStore.skillDocument` 作为数据源。两个 store 组合使用：
- `workbench.js` → 管理 skillDocument / messages / patch / validation / session
- `ide.js` → 管理 IDE 页面状态 / 脏标记 / 直接保存逻辑

### 关键方法

| 方法 | 作用 |
|------|------|
| `initForCreate()` | 重置为空文档，显示欢迎屏 |
| `initForEdit(id)` | 调 `skillApi.get(id)` 加载，填充 skillDocument |
| `updateModule(key, data)` | 写入 skillDocument 对应字段，标记 dirty |
| `saveDirectEdits()` | 收集脏模块 → schemaMapper 转换 → `PUT /structured` |
| `createSkill()` | `POST /skills/` → `PUT /structured` → 跳转编辑模式 |

## 数据流

### 直接编辑流

```
用户改 ParamsBlock 的一行
  → emit('update', newParams)
  → ideStore.updateModule('params', newParams)
  → workbenchStore.skillDocument.params = newParams
  → dirtyModules.add('params'), dirty = true
  → 防抖校验 validateBlock('params')
  → 用户点"保存"
  → schemaMapper.toStructured(skillDocument) → PUT /structured
  → dirty = false
```

### AI 对话流

```
用户在 IDEChatPanel 发消息"把阈值改成 1.2"
  → workbenchApi.parseIntent() → target_module = 'params'
  → workbenchApi.createPatch() → 返回 patch
  → IDEPatchOverlay 在 ParamsBlock 上显示 diff
  → 用户点"应用"
  → workbenchApi.applyPatch() → 后端保存 → 返回新 skill
  → workbenchStore.setSkillDocument(response.skill)
  → Block 自动刷新，dirty = false（后端已保存）
```

### Schema 映射（`schemaMapper.js`）

```
SkillStructure (IDE内存)          SkillStructured (后端API)
────────────────────────         ────────────────────────
meta.name/department/...   ↔     frontmatter
goal                       ↔     purpose
rules[{id,name,branches}]  ↔     steps[DecisionStep]
params[{name,value,desc}]  ↔     frontmatter (policy_pack)
output_table               ↔     output_definition
test_cases                 ↔     test_cases
workflow                   →     (不经过 parser，单独处理)
```

## Block 组件统一接口

```vue
<script setup>
defineProps({
  value: { required: true },            // 模块数据
  disabled: { type: Boolean },           // 只读
  validation: { type: Object },          // 校验状态
  patchDiff: { type: Object },           // 待应用的 AI diff
})
defineEmits(['update'])
</script>
```

## 分期实施

### P0：Block 编辑器骨架（本期）
- 创建 SkillIDE.vue + 三栏布局
- 创建 ide.js store
- 创建 7 个 Block 组件（从 SkillEdit 结构化 tab 移植）
- 创建 schemaMapper.js
- 编辑模式可用：加载 → 编辑 → 保存
- 添加路由（不 redirect，新旧并存）

### P1：对话集成
- 创建 IDEChatPanel.vue（移植现有 chat 逻辑）
- 创建 IDEPatchOverlay.vue（Block 上的 diff 浮层）
- 创建模式可用：欢迎屏 → 对话生成 → 填充 Block → 保存
- AI 编辑可用：对话 → patch → diff 预览 → 应用

### P2：校验 + 打磨
- 内联校验（debounced validateBlock）
- 脏状态警告（beforeRouteLeave）
- Ctrl+S 保存
- 响应式布局

### P3：切换
- 旧路由 redirect
- 全站导航链接更新
- 旧页面标记 deprecated

## 需修改的现有文件

| 文件 | 改动 |
|------|------|
| `web/src/router/index.js` | 添加 SkillIDE 路由 + redirect |
| `web/src/stores/workbench.js` | MODULES 加 'meta'，暴露 toStructuredPayload() |
| `web/src/pages/skill/SkillList.vue` | "新建"按钮链接改为 `/skills/ide` |
| `web/src/pages/skill/SkillsHome.vue` | 创建入口改为 `/skills/ide` |
| `web/src/pages/skill/SkillWorkspace.vue` | "编辑"tab 改为 `/skills/ide/:id` |

## 验证方案

1. **P0 完成后**：`/skills/ide/:id` 加载现有 Skill → 各 Block 正确显示 → 编辑 → 保存 → 刷新确认持久化
2. **P1 完成后**：`/skills/ide` 进入 → 对话生成草稿 → Block 填充 → 继续对话修改 → 应用 patch → 保存为新 Skill
3. **全量测试**：`python3 -m pytest tests/ -x` 后端不回归；`npx vitest run` 前端不回归
4. **E2E**：手动走通 创建 → 编辑 → 对话修改 → 保存 完整链路
