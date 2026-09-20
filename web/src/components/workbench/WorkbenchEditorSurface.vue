<template>
  <div class="wb-surface">
    <WorkbenchExplainView
      v-if="studioMode === 'explain'"
      :doc="doc"
      :skill-id="skillId"
      :readiness="readiness"
      :explain-pack="explainPack"
      :can-start-review="!isCreate && !!skillId"
      @start-review="$emit('start-review')"
    />
    <WorkbenchReviewView
      v-else-if="studioMode === 'review'"
      :doc="doc"
      :skill-id="skillId"
      :readiness="readiness"
      :health-score="healthScore"
      :guardian-items="guardianItems"
      :review-context="reviewContext"
      :static-check-override-detail="staticCheckOverrideDetail"
      :review-loading="reviewLoading"
      :review-acting="reviewActing"
      :can-act-review="canActReview"
      :can-comment-review="canCommentReview"
      @open-tab="tab => $emit('open-tab', tab)"
      @open-review-detail="id => $emit('open-review-detail', id)"
      @approve-review="payload => $emit('approve-review', payload)"
      @reject-review="payload => $emit('reject-review', payload)"
      @comment-review="payload => $emit('comment-review', payload)"
      @resolve-review-comment="commentId => $emit('resolve-review-comment', commentId)"
      @ask-ai-fix="payload => $emit('ask-ai-fix', payload)"
    />
    <!-- Block 视图 -->
    <div v-else-if="viewMode === 'block'" class="surface-block">
      <a-spin :loading="loading" style="height:100%; width:100%; display:block">
        <WorkbenchBlueprint v-if="isCreate && !hasContent && !chatStarted"
          @confirm="skill => $emit('blueprint-confirm', skill)"
          @startManual="$emit('start-manual')"
        />
        <WorkbenchOverview v-else-if="activeModule === 'overview'"
          :doc="doc" :skill-id="skillId"
          :readiness="readiness"
          :linting="linting"
          @go-module="mod => $emit('go-module', mod)"
          @run-lint="$emit('run-lint')"
          style="height:100%;overflow-y:auto"
        />
        <!-- 规则模块：决策表 + 画布 -->
        <div v-else-if="activeModule === 'rules'" class="surface-rule-editor" :class="{ 'focus-mode': moduleFocusMode }">
          <IDEPatchOverlay v-if="pendingPatch"
            :patch="pendingPatch" :applying="applyingPatch"
            @apply="$emit('apply-patch')" @reject="$emit('reject-patch')"
            style="position:absolute;top:8px;left:8px;z-index:10"
          />
          <nav v-if="moduleFocusMode" class="rule-mini-nav" aria-label="Skill 模块">
            <button
              v-for="item in ruleMiniModules"
              :key="item.key"
              type="button"
              class="rule-mini-nav-item"
              :class="{ active: item.key === 'rules' }"
              :title="item.label"
              @click="$emit('go-module', item.key)"
            >
              <icon-list v-if="item.icon === 'list'" :size="14" />
              <icon-code v-else-if="item.icon === 'code'" :size="14" />
              <icon-experiment v-else-if="item.icon === 'experiment'" :size="14" />
              <span v-else class="rule-mini-glyph">{{ item.glyph }}</span>
            </button>
          </nav>
          <header class="rule-editor-head">
            <div class="surface-canvas-back">
              <button class="be-back" @click="$emit('go-module', 'overview')"><icon-left :size="12" /> 概览</button>
              <span style="color:var(--ai-ink-4);font-size:13px">/</span>
              <span style="font-size:13px;font-weight:500;color:var(--ai-ink-1)">规则</span>
            </div>
            <div class="rule-editor-topline">
              <div>
                <div class="rule-editor-title">
                  <icon-list :size="18" />
                  <span>规则</span>
                  <span class="ai-pill warn">顺序敏感</span>
                </div>
                <div class="rule-editor-sub">
                  从上到下评估 {{ ruleSteps.length }} 个步骤、{{ ruleRows.length }} 条分支；保存后进入 Skill Git 与审核链路。
                </div>
              </div>
              <div class="rule-editor-actions">
                <input
                  ref="ruleImportInput"
                  class="rule-import-input"
                  type="file"
                  accept="application/json,.json"
                  @change="handleRuleImport"
                />
                <button class="rule-ai-btn" type="button" :disabled="readOnly" @click="openRuleImport">
                  <icon-upload :size="14" />
                  从 JSON 导入
                </button>
                <button class="rule-ai-btn primary" type="button" :disabled="readOnly" @click="handleAddStep">
                  <icon-plus :size="14" />
                  新增规则
                </button>
              </div>
            </div>
            <div class="rule-editor-mode-tabs" role="tablist" aria-label="规则视图">
              <button
                type="button"
                class="rule-editor-mode"
                :class="{ active: ruleViewMode === 'table' }"
                role="tab"
                :aria-selected="ruleViewMode === 'table'"
                @click="ruleViewMode = 'table'"
              >
                决策表
              </button>
              <button
                type="button"
                class="rule-editor-mode"
                :class="{ active: ruleViewMode === 'canvas' }"
                role="tab"
                :aria-selected="ruleViewMode === 'canvas'"
                @click="ruleViewMode = 'canvas'"
              >
                画布
              </button>
            </div>
          </header>

          <div v-if="ruleViewMode === 'table'" class="rule-editor-scroll">
            <section class="rule-table-card">
              <div class="rule-table-head">
                <div>
                  <div class="rule-card-title">决策表</div>
                  <div class="rule-card-sub">拖拽位、顺序、WHEN、THEN 与风险等级统一在一张表里维护。</div>
                </div>
                <span class="rule-table-count">{{ ruleRows.length }} rows</span>
              </div>
              <div v-if="ruleRows.length" class="rule-table-wrap">
                <table class="rule-table">
                  <thead>
                    <tr>
                      <th class="rule-col-drag"></th>
                      <th class="rule-col-order">顺序</th>
                      <th class="rule-col-id">ID</th>
                      <th>WHEN</th>
                      <th>THEN</th>
                      <th class="rule-col-risk">风险</th>
                      <th class="rule-col-enabled">启用</th>
                      <th class="rule-col-actions">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="row in ruleRows"
                      :key="row.key"
                      class="rule-row"
                      :class="{ active: selectedCanvasNode === row.stepId, dragging: draggingRuleStepId === row.stepId }"
                      :draggable="!readOnly"
                      @click="selectedCanvasNode = row.stepId"
                      @dragstart="startRuleDrag(row)"
                      @dragover.prevent
                      @drop="dropRuleRow(row)"
                      @dragend="draggingRuleStepId = ''"
                    >
                      <td class="rule-drag-cell"><icon-drag-dot-vertical :size="16" /></td>
                      <td class="rule-order-cell">{{ row.order }}</td>
                      <td>
                        <div class="rule-id-main">{{ row.stepId }}</div>
                        <div v-if="row.branchCount > 1" class="rule-id-sub">branch {{ row.branchIndex + 1 }}/{{ row.branchCount }}</div>
                      </td>
                      <td>
                        <div class="rule-when" :class="{ empty: !row.condition }">
                          {{ row.condition || '待填写触发条件' }}
                        </div>
                      </td>
                      <td>
                        <div class="rule-then">
                          <span class="rule-verdict-chip" :class="`rule-verdict-chip--${row.verdictKind}`">
                            {{ row.conclusion || '待判定' }}
                          </span>
                          <span class="rule-action-text">{{ row.action || '待填写建议动作' }}</span>
                        </div>
                      </td>
                      <td>
                        <span class="rule-risk" :class="`rule-risk--${row.riskKind}`">{{ row.risk }}</span>
                      </td>
                      <td @click.stop>
                        <a-switch
                          size="small"
                          :model-value="row.enabled"
                          :disabled="readOnly"
                          @change="checked => toggleRuleEnabled(row, Boolean(checked))"
                        />
                      </td>
                      <td @click.stop>
                        <div class="rule-row-actions">
                          <button class="rule-icon-btn" type="button" title="上移" :disabled="readOnly || row.stepIndex === 0" @click="moveRuleStep(row.stepId, -1)">
                            <icon-arrow-up :size="13" />
                          </button>
                          <button class="rule-icon-btn" type="button" title="下移" :disabled="readOnly || row.stepIndex === ruleSteps.length - 1" @click="moveRuleStep(row.stepId, 1)">
                            <icon-arrow-down :size="13" />
                          </button>
                          <button class="rule-icon-btn" type="button" title="编辑" :disabled="readOnly" @click="editRuleRow(row)">
                            <icon-edit :size="13" />
                          </button>
                          <button class="rule-icon-btn danger" type="button" title="删除" :disabled="readOnly" @click="deleteRuleStep(row.stepId)">
                            <icon-delete :size="13" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div v-else class="rule-empty">
                <icon-experiment :size="28" />
                <div class="rule-empty-title">暂无规则</div>
                <div class="rule-empty-sub">当前 Skill 没有解析到规则。可以新增一条规则，或从 JSON 导入规则数组。</div>
                <button class="rule-ai-btn primary" type="button" :disabled="readOnly" @click="handleAddStep">
                  <icon-plus :size="14" />
                  新增规则
                </button>
              </div>
            </section>

            <section class="rule-preview-card">
              <div class="rule-preview-head">
                <div>
                  <div class="rule-card-title">预览影响</div>
                  <div class="rule-card-sub">基于当前规则文本实时统计，不注入示例数据。</div>
                </div>
                <button class="rule-ai-btn subtle" type="button" @click="$emit('preview-output')">预览输出</button>
              </div>
              <div class="rule-preview-grid">
                <div v-for="item in rulePreviewStats" :key="item.key" class="rule-preview-metric">
                  <div class="rule-preview-label">{{ item.label }}</div>
                  <div class="rule-preview-value" :class="item.tone">{{ item.value }}</div>
                  <div class="rule-preview-hint">{{ item.hint }}</div>
                </div>
              </div>
            </section>
          </div>

          <div v-else class="rule-canvas-split">
            <div class="surface-canvas-main">
              <DecisionCanvas
                :steps="doc?.rules || []"
                :params="doc?.params || []"
                :selected-node="selectedCanvasNode"
                :highlighted-path="props.highlightedPath"
                :failed-nodes="props.failedNodes"
                @select-node="id => selectedCanvasNode = id"
                @add-step="handleAddStep"
                @edit-param="p => $emit('edit-param', p)"
              />
            </div>
            <div class="surface-canvas-inspector">
              <NodeInspector
                :node="selectedNodeData"
                :all-params="doc?.params || []"
                @update="handleNodeUpdate"
                @edit-param="p => $emit('edit-param', p)"
                @ai-action="action => $emit('ai-action', { action, nodeId: selectedCanvasNode })"
                @delete-node="handleDeleteStep"
                @edit-rule="payload => $emit('edit-rule', payload)"
              />
            </div>
          </div>
        </div>
        <!-- 其他模块：表单编辑 -->
        <div v-else class="surface-scroll">
          <IDEPatchOverlay v-if="pendingPatch"
            :patch="pendingPatch"
            :applying="applyingPatch"
            @apply="$emit('apply-patch')"
            @reject="$emit('reject-patch')"
          />
          <IDEBlockEditor
            :active-module="activeModule"
            :doc="doc"
            :is-create="isCreate"
            :skill-id="skillId"
            @update="(key, value) => $emit('update-module', key, value)"
            @openCanvas="$emit('open-canvas')"
            @goOverview="$emit('go-module', 'overview')"
            @preview-output="$emit('preview-output')"
          />
        </div>
      </a-spin>
    </div>

    <!-- Grid 九宫格视图 -->
    <div v-else-if="viewMode === 'grid'" class="surface-block">
      <IDEGridEditor
        :doc="doc"
        :skill-id="skillId"
        :is-create="isCreate"
        :disabled="readOnly"
        :dirty-modules="dirtyModulesSet"
        @select-module="(mod: string) => $emit('go-module', mod)"
      />
    </div>

    <!-- Code 视图（Monaco） -->
    <div v-else-if="viewMode === 'code'" class="surface-code">
      <a-spin :loading="loading" class="surface-code-inner">
        <div class="code-placeholder" v-if="!monacoReady">
          <icon-code :size="24" style="color:var(--ai-ink-4)" />
          <span>Monaco Editor 加载中...</span>
        </div>
        <template v-else>
          <IDEPatchOverlay v-if="pendingPatch"
            :patch="pendingPatch"
            :applying="applyingPatch"
            @apply="$emit('apply-patch')"
            @reject="$emit('reject-patch')"
            style="position:absolute;top:8px;right:8px;z-index:10"
          />
          <component
            :is="codePanelComponent"
            ref="monacoRef"
            :model-value="fileContent !== null ? fileContent : localMarkdown"
            :language="fileLanguage"
            :theme="editorTheme === 'dark' ? 'skill-dark' : 'vs'"
            :read-only="readOnly || (fileContent !== null && fileReadOnly)"
            :enableAI="!readOnly && fileContent === null"
            :font-size="editorFontSize"
            :filename="fileName"
            height="100%"
            @update:modelValue="handleMonacoChange"
            @editorReady="handleEditorReady"
          />
        </template>
      </a-spin>
    </div>

    <!-- 统一状态栏：Block/Code 共用同一结构 -->
    <div class="surface-statusbar" @click="$emit('toggle-bottom-panel')">
      <span v-if="viewMode === 'code'" class="sb-item sb-muted">行 {{ cursorLine }}, 列 {{ cursorCol }}</span>
      <span v-if="errorCount" class="sb-item sb-error">{{ errorCount }} 错误</span>
      <span v-else-if="warningCount" class="sb-item sb-warn">{{ warningCount }} 警告</span>
      <span v-else class="sb-item sb-ok">✓ 无问题</span>
      <span class="sb-spacer" />
      <span class="sb-item sb-muted">{{ fileName }}</span>
      <span class="sb-item sb-muted">{{ readOnly ? '只读' : '编辑中' }}</span>
      <span class="sb-item sb-hint">{{ metaKey }}J 面板</span>
      <span class="sb-item sb-hint">{{ metaKey }}P 命令</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch, defineAsyncComponent, shallowRef, onBeforeUnmount } from 'vue'
import type { PropType } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  IconArrowDown,
  IconArrowUp,
  IconCode,
  IconDelete,
  IconDragDotVertical,
  IconEdit,
  IconExperiment,
  IconLeft,
  IconList,
  IconPlus,
  IconUpload,
} from '@arco-design/web-vue/es/icon'
import { metaKey } from '@/utils/shortcut'
import { documentToMarkdown, markdownToDocument } from '@/utils/markdownParser'
import IDEBlockEditor from '@/components/ide/IDEBlockEditor.vue'
import IDEGridEditor from '@/components/ide/IDEGridEditor.vue'
import IDEWelcome from '@/components/ide/IDEWelcome.vue'
import WorkbenchBlueprint from './WorkbenchBlueprint.vue'
import IDEPatchOverlay from '@/components/ide/IDEPatchOverlay.vue'
import WorkbenchOverview from './WorkbenchOverview.vue'
import WorkbenchExplainView from './WorkbenchExplainView.vue'
import WorkbenchReviewView from './WorkbenchReviewView.vue'
import DecisionCanvas from './DecisionCanvas.vue'
import NodeInspector from './NodeInspector.vue'
import IntentPanel from '@/components/editor/IntentPanel.vue'
import SkillMdPanel from '@/components/editor/SkillMdPanel.vue'
import PolicyPanel from '@/components/editor/PolicyPanel.vue'

const MonacoEditor = defineAsyncComponent(() => import('@/components/editor/MonacoEditor.vue'))

const props = defineProps({
  viewMode: { type: String, default: 'block' },
  studioMode: { type: String, default: 'edit' },
  activeModule: { type: String, default: 'meta' },
  doc: { type: Object as PropType<any>, default: () => ({}) },
  isCreate: { type: Boolean, default: false },
  hasContent: { type: Boolean, default: false },
  chatStarted: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  readOnly: { type: Boolean, default: false },
  pendingPatch: { type: Object as PropType<any>, default: null },
  applyingPatch: { type: Boolean, default: false },
  editorTheme: { type: String, default: 'light' },
  editorFontSize: { type: Number, default: 14 },
  skillId: { type: String, default: '' },
  fileContent: { type: String as PropType<string | null>, default: null },  // null = doc-based SKILL.md，string = 直接显示文件
  fileReadOnly: { type: Boolean, default: false },
  fileLanguage: { type: String, default: 'skill-md' },
  fileName: { type: String, default: 'SKILL.md' },
  highlightedPath: { type: Array as PropType<string[]>, default: () => [] },
  failedNodes: { type: Array as PropType<string[]>, default: () => [] },
  readiness: { type: Object as PropType<any>, default: null },
  explainPack: { type: Object as PropType<any>, default: null },
  healthScore: { type: Object as PropType<any>, default: null },
  guardianItems: { type: Array as PropType<any[]>, default: () => [] },
  reviewContext: { type: Object as PropType<any>, default: null },
  staticCheckOverrideDetail: { type: [Object, String] as PropType<any>, default: null },
  reviewLoading: { type: Boolean, default: false },
  reviewActing: { type: Boolean, default: false },
  canActReview: { type: Boolean, default: false },
  canCommentReview: { type: Boolean, default: false },
  linting: { type: Boolean, default: false },
  errorCount: { type: Number, default: 0 },
  warningCount: { type: Number, default: 0 },
  dirtyModules: { type: Object as PropType<Set<string>>, default: () => new Set<string>() },
  moduleFocusMode: { type: Boolean, default: false },
})

const emit = defineEmits([
  'update-module', 'open-canvas', 'start-manual', 'start-chat', 'apply-template', 'blueprint-confirm', 'edit-param', 'ai-action', 'run-lint',
  'apply-patch', 'reject-patch',
  'monaco-change', 'selection-change', 'cursor-block-change',
  // 右键菜单命令
  'toggle-bottom-panel', 'open-tab', 'go-module',
  'open-review-detail', 'approve-review', 'reject-review',
  'comment-review', 'resolve-review-comment',
  'start-review', 'ask-ai-fix',
  'cmd-save', 'cmd-run-sandbox', 'cmd-validate-block', 'cmd-validate-all',
  'cmd-mermaid', 'cmd-flow', 'cmd-discover-antipatterns', 'cmd-suggest-branches',
  'cmd-generate-tests', 'cmd-drift-check', 'cmd-submit-review', 'cmd-inline-ai',
  'cmd-validate-antipattern', 'preview-output',
  'edit-rule',
])

const monacoRef = shallowRef<any>(null)
const monacoReady = ref(false)
const localMarkdown = ref('')
const cursorLine = ref(1)
const cursorCol = ref(1)

// Grid 视图的 dirtyModules 透传
const dirtyModulesSet = computed(() => props.dirtyModules instanceof Set ? props.dirtyModules : new Set<string>())

// Decision Canvas state
const selectedCanvasNode = ref('')
const ruleViewMode = ref<'table' | 'canvas'>('table')
const ruleImportInput = ref<HTMLInputElement | null>(null)
const draggingRuleStepId = ref('')
const ruleMiniModules = [
  { key: 'overview', label: '概览', glyph: '概', icon: 'code' },
  { key: 'goal', label: '目标', glyph: '目', icon: 'experiment' },
  { key: 'rules', label: '规则', glyph: '规', icon: 'list' },
  { key: 'params', label: '参数', glyph: '参', icon: 'code' },
  { key: 'output_table', label: '输出', glyph: '输', icon: 'code' },
  { key: 'todos', label: '待办', glyph: '待', icon: 'experiment' },
  { key: 'test_cases', label: '测试', glyph: '测', icon: 'experiment' },
  { key: 'workflow', label: '工作流', glyph: '流', icon: 'list' },
]

type RuleRow = {
  key: string
  stepId: string
  stepName: string
  stepIndex: number
  branchIndex: number
  branchCount: number
  order: string
  condition: string
  conclusion: string
  action: string
  risk: string
  riskKind: 'low' | 'mid' | 'high'
  verdictKind: 'ok' | 'warn' | 'bad' | 'default'
  enabled: boolean
}

const selectedNodeData = computed(() => {
  if (!selectedCanvasNode.value || !props.doc?.rules) return null
  return props.doc.rules.find((r: Record<string, unknown>) => r.id === selectedCanvasNode.value) || null
})

const codePanelComponent = computed(() => {
  if (props.fileContent !== null) {
    if (props.fileName === 'intent.md') return IntentPanel
    if (props.fileName === 'policy.yaml') return PolicyPanel
  }
  return SkillMdPanel
})

const ruleSteps = computed<any[]>(() => {
  const rules = props.doc?.rules
  return Array.isArray(rules) ? rules : []
})

const ruleRows = computed<RuleRow[]>(() => {
  return ruleSteps.value.flatMap((step, stepIndex) => {
    const branches = Array.isArray(step?.branches) && step.branches.length
      ? step.branches
      : [{ condition: '', conclusion: '', action: '', next_step: null }]
    return branches.map((branch: any, branchIndex: number) => {
      const condition = String(branch?.condition || '')
      const conclusion = String(branch?.conclusion || '')
      const action = String(branch?.action || '')
      const risk = normalizeRuleRisk(branch?.risk || step?.risk || inferRuleRisk(condition, conclusion, action))
      return {
        key: `${step?.id || stepIndex}-${branchIndex}`,
        stepId: String(step?.id || `step_${stepIndex + 1}`),
        stepName: String(step?.name || ''),
        stepIndex,
        branchIndex,
        branchCount: branches.length,
        order: `${String(stepIndex + 1).padStart(2, '0')}.${branchIndex + 1}`,
        condition,
        conclusion,
        action,
        risk,
        riskKind: ruleRiskKind(risk),
        verdictKind: ruleVerdictKind(conclusion),
        enabled: branch?.enabled !== false && step?.enabled !== false,
      }
    })
  })
})

const rulePreviewStats = computed(() => {
  const enabledRows = ruleRows.value.filter(row => row.enabled)
  const textOf = (row: RuleRow) => `${row.condition} ${row.conclusion} ${row.action}`.toLowerCase()
  const p1 = enabledRows.filter(row => row.riskKind === 'high' || /p1|红灯|阻断|高危|严重|critical|block/.test(textOf(row))).length
  const p2 = enabledRows.filter(row => row.riskKind === 'mid' || /p2|黄灯|关注|中危|复核|warning|review/.test(textOf(row))).length
  const pass = enabledRows.filter(row => /绿灯|通过|豁免|免审|pass|ok|allow/.test(textOf(row))).length
  const missing = enabledRows.filter(row => !row.condition.trim() || !row.conclusion.trim() || !row.action.trim()).length
  return [
    { key: 'p1', label: 'P1 阻断', value: p1, hint: '高风险分支', tone: 'bad' },
    { key: 'p2', label: 'P2 复核', value: p2, hint: '需人工关注', tone: 'warn' },
    { key: 'pass', label: '通过 / 豁免', value: pass, hint: '放行分支', tone: 'ok' },
    { key: 'missing', label: '数据缺口', value: missing, hint: '待补全字段', tone: missing ? 'bad' : 'neutral' },
  ]
})

function handleAddStep() {
  const rules = [...(props.doc?.rules || [])]
  const nextIndex = rules.length + 1
  const id = `R-${String(nextIndex).padStart(2, '0')}`
  rules.push({
    id,
    name: `规则 ${nextIndex}`,
    enabled: true,
    branches: [{ condition: '', conclusion: '黄灯', action: '', next_step: null, enabled: true, risk: 'L2' }],
  })
  emit('update-module', 'rules', rules)
  selectedCanvasNode.value = id
  ruleViewMode.value = 'table'
}

function handleDeleteStep(stepId: string) {
  const rules = (props.doc?.rules || []).filter((r: Record<string, unknown>) => r.id !== stepId)
  emit('update-module', 'rules', rules)
  selectedCanvasNode.value = ''
}

function handleNodeUpdate(node: any) {
  const rules = [...(props.doc?.rules || [])]
  const idx = rules.findIndex(r => r.id === node.id)
  if (idx >= 0) { rules[idx] = { ...node }; emit('update-module', 'rules', rules) }
}

function normalizeRuleRisk(value: unknown) {
  const raw = String(value || '').trim()
  if (!raw) return 'L1'
  const upper = raw.toUpperCase()
  if (/P1|L3|HIGH|高|红|严重|阻断/.test(upper)) return 'L3'
  if (/P2|L2|MID|MEDIUM|中|黄|复核|关注/.test(upper)) return 'L2'
  if (/P0|L0|LOW|低|绿|通过|豁免/.test(upper)) return 'L0'
  if (/L1|P3/.test(upper)) return upper.includes('P3') ? 'L1' : 'L1'
  return raw
}

function inferRuleRisk(condition: string, conclusion: string, action: string) {
  const text = `${condition} ${conclusion} ${action}`
  if (/红灯|阻断|高危|严重|P1|L3|critical|block/i.test(text)) return 'L3'
  if (/黄灯|复核|关注|中危|P2|L2|warning|review/i.test(text)) return 'L2'
  if (/绿灯|通过|豁免|免审|L0|pass|allow/i.test(text)) return 'L0'
  return 'L1'
}

function ruleRiskKind(risk: string): RuleRow['riskKind'] {
  if (/L3|P1|高|红|阻断/.test(risk)) return 'high'
  if (/L2|P2|中|黄|复核/.test(risk)) return 'mid'
  return 'low'
}

function ruleVerdictKind(value: string): RuleRow['verdictKind'] {
  if (/绿|通过|豁免|成功|ok|pass|allow/i.test(value)) return 'ok'
  if (/红|失败|拒绝|阻断|错误|bad|block|deny/i.test(value)) return 'bad'
  if (/黄|等待|关注|复核|warn|review/i.test(value)) return 'warn'
  return 'default'
}

function cloneRules() {
  return ruleSteps.value.map(step => ({
    ...step,
    branches: Array.isArray(step?.branches) ? step.branches.map((branch: any) => ({ ...branch })) : [],
  }))
}

function editRuleRow(row: RuleRow) {
  selectedCanvasNode.value = row.stepId
  emit('edit-rule', { nodeId: row.stepId, branchIndex: row.branchIndex })
}

function deleteRuleStep(stepId: string) {
  handleDeleteStep(stepId)
}

function moveRuleStep(stepId: string, delta: -1 | 1) {
  const rules = cloneRules()
  const idx = rules.findIndex(rule => rule?.id === stepId)
  const nextIdx = idx + delta
  if (idx < 0 || nextIdx < 0 || nextIdx >= rules.length) return
  const [target] = rules.splice(idx, 1)
  rules.splice(nextIdx, 0, target)
  emit('update-module', 'rules', rules)
  selectedCanvasNode.value = stepId
}

function startRuleDrag(row: RuleRow) {
  draggingRuleStepId.value = row.stepId
  selectedCanvasNode.value = row.stepId
}

function dropRuleRow(row: RuleRow) {
  const sourceId = draggingRuleStepId.value
  draggingRuleStepId.value = ''
  if (!sourceId || sourceId === row.stepId) return
  const rules = cloneRules()
  const from = rules.findIndex(rule => rule?.id === sourceId)
  const to = rules.findIndex(rule => rule?.id === row.stepId)
  if (from < 0 || to < 0) return
  const [target] = rules.splice(from, 1)
  rules.splice(to, 0, target)
  emit('update-module', 'rules', rules)
  selectedCanvasNode.value = sourceId
}

function toggleRuleEnabled(row: RuleRow, enabled: boolean) {
  const rules = cloneRules()
  const step = rules[row.stepIndex]
  if (!step) return
  const branches = Array.isArray(step.branches) ? step.branches : []
  const branch = branches[row.branchIndex]
  if (!branch) return
  branches[row.branchIndex] = { ...branch, enabled }
  step.branches = branches
  emit('update-module', 'rules', rules)
}

function openRuleImport() {
  ruleImportInput.value?.click()
}

async function handleRuleImport(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  try {
    const content = await file.text()
    const parsed = JSON.parse(content)
    const rules = Array.isArray(parsed) ? parsed : parsed?.rules
    if (!Array.isArray(rules)) throw new Error('rules must be an array')
    emit('update-module', 'rules', rules)
    selectedCanvasNode.value = rules[0]?.id ? String(rules[0].id) : ''
    Message.success(`已导入 ${rules.length} 条规则`)
  } catch (err) {
    Message.error(err instanceof Error ? `导入失败：${err.message}` : '导入失败')
  }
}

let syncingFromMonaco = false
let docSyncTimer: ReturnType<typeof setTimeout> | null = null
let editorInstance: any = null

const monacoOptions = computed(() => ({
  minimap: { enabled: false },
  wordWrap: 'on',
  scrollBeyondLastLine: false,
  automaticLayout: true,
  tabSize: 2,
  readOnly: props.readOnly,
}))

onBeforeUnmount(() => {
  clearTimeout(docSyncTimer)
  clearTimeout(monacoDebounceTimer)
  syncingFromMonaco = false
  editorInstance = null
})

// ── 视图切换同步 ──
watch(() => props.viewMode, (v) => {
  if (v === 'code') {
    monacoReady.value = true
    if (!syncingFromMonaco) {
      localMarkdown.value = documentToMarkdown(props.doc)
    }
  }
}, { immediate: true })

// ── doc 变化同步到 Monaco（去抖）──
watch(() => props.doc, () => {
  if (props.viewMode !== 'code' || syncingFromMonaco) return
  clearTimeout(docSyncTimer)
  docSyncTimer = setTimeout(() => {
    if (!syncingFromMonaco) localMarkdown.value = documentToMarkdown(props.doc)
  }, 500)
}, { deep: true })

// ── Monaco Ready：注册右键菜单 + 光标跟踪 ──
function handleEditorReady({ editor, monaco }: { editor: any; monaco: any }) {
  editorInstance = editor

  // 光标位置跟踪
  editor.onDidChangeCursorPosition((e: any) => {
    cursorLine.value = e.position.lineNumber
    cursorCol.value = e.position.column
    // 判断光标所在 block，通知父组件做实时验证
    emitCursorBlock(e.position.lineNumber)
  })

  // 选区跟踪
  editor.onDidChangeCursorSelection((e: any) => {
    const sel = e.selection
    const model = editor.getModel()
    if (!model || !sel) return
    const text = model.getValueInRange(sel)
    if (text) {
      emit('selection-change', {
        moduleId: props.activeModule,
        startLine: sel.startLineNumber,
        endLine: sel.endLineNumber,
        text,
      })
    }
  })

  // ── 右键菜单注册 ──
  if (!monaco) return

  // 1_ai: AI 编辑 + AI 补全
  editor.addAction({
    id: 'wb-inline-ai-edit', label: 'AI 编辑选中内容',
    keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyK],
    contextMenuGroupId: '1_ai', contextMenuOrder: 1,
    precondition: 'editorHasSelection',
    run: () => emit('cmd-inline-ai'),
  })
  editor.addAction({
    id: 'wb-ai-completion', label: 'AI 补全',
    contextMenuGroupId: '1_ai', contextMenuOrder: 2,
    run: () => editor.trigger('contextMenu', 'editor.action.inlineSuggest.trigger', null),
  })

  // 2_file: 保存 + 运行沙箱
  editor.addAction({
    id: 'wb-save', label: '保存',
    keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS],
    contextMenuGroupId: '2_file', contextMenuOrder: 1,
    run: () => emit('cmd-save'),
  })
  editor.addAction({
    id: 'wb-run-sandbox', label: '运行沙箱',
    keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyR],
    contextMenuGroupId: '2_file', contextMenuOrder: 2,
    run: () => emit('cmd-run-sandbox'),
  })

  // 3_skill: 验证当前块 + 全量验证 + Mermaid
  editor.addAction({
    id: 'wb-validate-block', label: '校验当前块',
    contextMenuGroupId: '3_skill', contextMenuOrder: 1,
    run: () => emit('cmd-validate-block'),
  })
  editor.addAction({
    id: 'wb-validate-all', label: '全量校验',
    contextMenuGroupId: '3_skill', contextMenuOrder: 2,
    run: () => emit('cmd-validate-all'),
  })
  editor.addAction({
    id: 'wb-mermaid', label: '决策树可视化',
    contextMenuGroupId: '3_skill', contextMenuOrder: 3,
    run: () => emit('cmd-mermaid'),
  })

  // 4_ai_tools: 反例 + 分支 + 测试 + 漂移
  editor.addAction({
    id: 'wb-validate-antipattern', label: '验证当前反例',
    contextMenuGroupId: '4_ai_tools', contextMenuOrder: 0.5,
    run: () => {
      // 从光标位置提取反例的 scenario 和 correct_action
      const model = editor.getModel()
      const pos = editor.getPosition()
      if (!model || !pos) return
      const antipattern = extractAntipatternAtCursor(model, pos.lineNumber)
      if (antipattern) {
        emit('cmd-validate-antipattern', antipattern)
      }
    },
  })
  editor.addAction({
    id: 'wb-discover-ap', label: 'AI 发现反例',
    contextMenuGroupId: '4_ai_tools', contextMenuOrder: 1,
    run: () => emit('cmd-discover-antipatterns'),
  })
  editor.addAction({
    id: 'wb-suggest-branches', label: 'AI 分支补全',
    contextMenuGroupId: '4_ai_tools', contextMenuOrder: 2,
    run: () => emit('cmd-suggest-branches'),
  })
  editor.addAction({
    id: 'wb-generate-tests', label: '生成测试用例',
    contextMenuGroupId: '4_ai_tools', contextMenuOrder: 3,
    run: () => emit('cmd-generate-tests'),
  })
  editor.addAction({
    id: 'wb-drift-check', label: '数据源漂移检测',
    contextMenuGroupId: '4_ai_tools', contextMenuOrder: 4,
    run: () => emit('cmd-drift-check'),
  })

  // 5_workflow: 提交审核
  editor.addAction({
    id: 'wb-submit-review', label: '提交审核',
    contextMenuGroupId: '5_workflow', contextMenuOrder: 1,
    run: () => emit('cmd-submit-review'),
  })
}

// ── 从光标位置提取反例数据 ──
function extractAntipatternAtCursor(model: any, lineNumber: number): { scenario: string; expected_action: string } | null {
  const totalLines = model.getLineCount()

  // 先确认光标在反例区块内（向上找 ## 反例 / ## 误判）
  let inAntipatternBlock = false
  for (let i = lineNumber; i >= 1; i--) {
    const text = model.getLineContent(i)
    if (/^## 反例|^## 误判/.test(text)) { inAntipatternBlock = true; break }
    if (/^## /.test(text)) break // 碰到其他二级标题，说明不在反例区块
  }
  if (!inAntipatternBlock) return null

  // 找当前反例条目的起止范围（反例列表以 - 或 ### 开头的条目）
  let startLine = lineNumber
  let endLine = lineNumber

  // 向上找条目开头（以 - 或 ### 开头，或 **场景** 开头）
  for (let i = lineNumber; i >= 1; i--) {
    const text = model.getLineContent(i).trim()
    if (/^## 反例|^## 误判/.test(text)) break // 到了区块标题，停止
    if (/^- |^### |^\*\*场景/.test(text)) { startLine = i; break }
  }

  // 向下找条目结尾
  for (let i = lineNumber + 1; i <= totalLines; i++) {
    const text = model.getLineContent(i).trim()
    if (/^## |^- |^### /.test(text) || text === '') { endLine = i - 1; break }
    endLine = i
  }

  // 收集条目文本
  const lines: string[] = []
  for (let i = startLine; i <= endLine; i++) {
    lines.push(model.getLineContent(i))
  }
  const block = lines.join('\n')

  // 尝试提取 scenario 和 expected_action
  // 常见格式：
  //   - **场景**: xxx / **正确做法**: yyy
  //   - 场景：xxx，正确做法：yyy
  let scenario = ''
  let expected_action = ''

  const scenarioMatch = block.match(/\*{0,2}场景\*{0,2}[：:]\s*(.+?)(?=\*{0,2}(?:正确做法|应该|期望行为|correct)[：:]|$)/s)
  if (scenarioMatch) scenario = scenarioMatch[1].trim()

  const actionMatch = block.match(/\*{0,2}(?:正确做法|应该|期望行为|correct(?:_action)?)\*{0,2}[：:]\s*(.+)/s)
  if (actionMatch) expected_action = actionMatch[1].trim()

  // 如果无法精确提取，将整段文本作为 scenario
  if (!scenario) scenario = block.replace(/^[-#*\s]+/, '').trim()

  return scenario ? { scenario, expected_action } : null
}

// ── 判断光标所在 block 类型 ──
const BLOCK_PATTERNS = [
  { pattern: /^---/, block: 'frontmatter' },
  { pattern: /^## 目的/, block: 'purpose' },
  { pattern: /^## 执行步骤|^## 判断逻辑|^## 决策/, block: 'steps' },
  { pattern: /^## 反例|^## 误判/, block: 'antipatterns' },
  { pattern: /^## 输出/, block: 'output' },
  { pattern: /^## 待办|^## Todo|^## Todos/, block: 'todos' },
  { pattern: /^## 数据输入/, block: 'data_inputs' },
  { pattern: /^## 测试用例|^## 测试/, block: 'test_cases' },
]
let lastEmittedBlock = ''

function emitCursorBlock(lineNumber: number) {
  if (!editorInstance) return
  const model = editorInstance.getModel()
  if (!model) return

  // 从光标行向上扫描找到最近的 ## 标题
  let blockType = 'frontmatter'
  for (let i = lineNumber; i >= 1; i--) {
    const lineText = model.getLineContent(i)
    for (const { pattern, block } of BLOCK_PATTERNS) {
      if (pattern.test(lineText)) { blockType = block; i = 0; break }
    }
  }

  if (blockType !== lastEmittedBlock) {
    lastEmittedBlock = blockType
    emit('cursor-block-change', blockType)
  }
}

// ── Monaco 编辑回调 ──
let monacoDebounceTimer: ReturnType<typeof setTimeout> | null = null
function handleMonacoChange(value: string) {
  localMarkdown.value = value
  emit('monaco-change', value)

  if (props.fileContent !== null) return

  clearTimeout(monacoDebounceTimer)
  monacoDebounceTimer = setTimeout(() => {
    syncingFromMonaco = true
    try {
      const parsed = markdownToDocument(value)
      if (parsed) {
        if (parsed.meta) emit('update-module', 'meta', parsed.meta)
        if (parsed.goal !== undefined) emit('update-module', 'goal', parsed.goal)
        if (parsed.rules) emit('update-module', 'rules', parsed.rules)
        if (parsed.params) emit('update-module', 'params', parsed.params)
        if (parsed.output_table) emit('update-module', 'output_table', parsed.output_table)
        if (parsed.todos) emit('update-module', 'todos', parsed.todos)
        if (parsed.test_cases) emit('update-module', 'test_cases', parsed.test_cases)
      }
    } catch { /* 解析失败不同步 */ }
    finally { setTimeout(() => { syncingFromMonaco = false }, 50) }
  }, 800)
}
</script>

<style scoped>
.wb-surface {
  height: 100%;
  display: flex;
  flex-direction: column;
  position: relative;
  background: transparent;
}
.surface-block {
  flex: 1;
  overflow: hidden;
  position: relative;
}
.surface-scroll { height: 100%; overflow-y: auto; padding: 20px 24px; }
.surface-code { flex: 1; display: flex; flex-direction: column; overflow: hidden; position: relative; }
.surface-code-inner { flex: 1; min-height: 0; }
.surface-code-inner :deep(.arco-spin) { height: 100%; }
.code-placeholder {
  height: 100%; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 8px; font-size: 13px; color: var(--ai-ink-3); font-weight: 700;
}

/* 统一状态栏：Block/Code 共用 */
.surface-statusbar {
  display: flex; align-items: center; gap: 10px;
  height: 24px; padding: 0 12px; flex-shrink: 0;
  background: var(--ai-surface-2); border-top: 1px solid var(--ai-border);
  font-size: 11px; color: var(--ai-ink-3);
  cursor: pointer;
  user-select: none;
  transition: background var(--sf-transition);
  font-weight: 700;
}
.surface-statusbar:hover { background: var(--ai-surface-2); }
.sb-ok { color: var(--ai-ok); }
.sb-error { color: var(--ai-bad); }
.sb-warn { color: var(--ai-warn); }
.sb-muted { color: var(--ai-ink-3); }
.sb-hint { color: var(--ai-ink-3); opacity: .6; }
/* Canvas 布局 */
.surface-canvas-layout { display: flex; height: 100%; overflow: hidden; position: relative; }
.surface-canvas-main { flex: 1; display: flex; flex-direction: column; min-width: 0; overflow: hidden; }
.surface-canvas-back { display: flex; align-items: center; gap: 6px; padding: 8px 16px; flex-shrink: 0; border-bottom: 1px solid var(--ai-border); }
.be-back { display: inline-flex; align-items: center; gap: 3px; border: none; background: none; color: var(--ai-ink-3); cursor: pointer; padding: 2px 6px; border-radius: var(--sf-radius-xs); font-size: 13px; font-weight: 700; transition: all var(--sf-transition); }
.be-back:hover { color: var(--ai-warn); background: var(--ai-surface-2); }
.surface-canvas-inspector { width: 300px; flex-shrink: 0; border-left: 1px solid var(--ai-border); overflow: hidden; }

/* L3-C rule editor */
.surface-rule-editor {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
  background: var(--ai-bg);
}
.surface-rule-editor.focus-mode {
  background: var(--ai-bg);
}
.rule-mini-nav {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  z-index: 4;
  width: 56px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 12px 0;
  border-right: 1px solid var(--ai-border);
  background: var(--ai-surface);
}
.rule-mini-nav-item {
  width: 40px;
  height: 40px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--ai-ink-3);
  cursor: pointer;
  transition: background .12s, color .12s;
}
.rule-mini-nav-item:hover {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
}
.rule-mini-nav-item.active {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
}
.rule-mini-glyph {
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
}
.surface-rule-editor.focus-mode .rule-editor-head,
.surface-rule-editor.focus-mode .rule-editor-scroll,
.surface-rule-editor.focus-mode .rule-canvas-split {
  margin-left: 56px;
}
.surface-rule-editor.focus-mode .surface-canvas-back {
  display: none;
}
.surface-rule-editor.focus-mode .rule-editor-head {
  background: transparent;
}
.surface-rule-editor.focus-mode .rule-editor-topline {
  padding: 24px 28px 18px;
}
.surface-rule-editor.focus-mode .rule-editor-mode-tabs {
  padding: 0 28px 12px;
}
.surface-rule-editor.focus-mode .rule-editor-scroll {
  padding: 28px;
}
.rule-editor-head {
  flex-shrink: 0;
  background: var(--ai-surface);
  border-bottom: 1px solid var(--ai-border);
}
.rule-editor-head .surface-canvas-back {
  border-bottom: none;
  padding-bottom: 4px;
}
.rule-editor-topline {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 4px 20px 14px;
}
.rule-editor-title {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--ai-ink-1);
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0;
}
.rule-editor-sub {
  margin-top: 5px;
  color: var(--ai-ink-3);
  font-size: 12.5px;
  line-height: 1.6;
}
.rule-editor-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.rule-import-input {
  display: none;
}
.rule-ai-btn {
  min-height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 0 10px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface);
  color: var(--ai-ink-2);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  transition: border-color .12s, background .12s, color .12s;
  white-space: nowrap;
}
.rule-ai-btn:hover:not(:disabled) {
  border-color: var(--ai-ink-1);
  color: var(--ai-ink-1);
  background: var(--ai-surface-2);
}
.rule-ai-btn:disabled {
  cursor: not-allowed;
  opacity: .45;
}
.rule-ai-btn.primary {
  border-color: var(--ai-ink-1);
  background: var(--ai-ink-1);
  color: var(--ai-surface);
}
.rule-ai-btn.primary:hover:not(:disabled) {
  background: var(--ai-ink-2);
  color: var(--ai-surface);
}
.rule-ai-btn.subtle {
  background: var(--ai-surface-2);
}
.rule-editor-mode-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 20px 12px;
}
.rule-editor-mode {
  height: 28px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--ai-ink-3);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}
.rule-editor-mode.active {
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
  color: var(--ai-surface);
}
.rule-editor-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 16px 20px 28px;
}
.rule-table-card,
.rule-preview-card {
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
}
.rule-preview-card {
  margin-top: 14px;
}
.rule-table-head,
.rule-preview-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--ai-border);
}
.rule-card-title {
  color: var(--ai-ink-1);
  font-size: 14px;
  font-weight: 700;
}
.rule-card-sub {
  margin-top: 3px;
  color: var(--ai-ink-4);
  font-size: 12px;
  line-height: 1.5;
}
.rule-table-count {
  height: 22px;
  display: inline-flex;
  align-items: center;
  padding: 0 8px;
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-family: var(--ai-font-mono);
  font-size: 11px;
  font-weight: 700;
  white-space: nowrap;
}
.rule-table-wrap {
  overflow-x: auto;
}
.rule-table {
  width: 100%;
  min-width: 980px;
  border-collapse: collapse;
  table-layout: fixed;
}
.rule-table th {
  height: 36px;
  padding: 0 10px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-4);
  font-size: 10.5px;
  font-weight: 700;
  text-align: left;
  text-transform: uppercase;
}
.rule-table td {
  padding: 11px 10px;
  border-bottom: 1px solid var(--ai-border);
  vertical-align: top;
  color: var(--ai-ink-2);
  font-size: 12.5px;
}
.rule-table tbody tr:last-child td {
  border-bottom: none;
}
.rule-row {
  cursor: pointer;
  transition: background .12s;
}
.rule-row:hover,
.rule-row.active {
  background: var(--ai-surface-2);
}
.rule-row.dragging {
  opacity: .55;
}
.rule-col-drag { width: 34px; }
.rule-col-order { width: 64px; }
.rule-col-id { width: 132px; }
.rule-col-risk { width: 74px; }
.rule-col-enabled { width: 70px; }
.rule-col-actions { width: 148px; }
.rule-drag-cell {
  color: var(--ai-ink-4);
}
.rule-order-cell,
.rule-id-main,
.rule-id-sub,
.rule-table-count,
.rule-preview-value {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.rule-order-cell {
  color: var(--ai-ink-3);
  font-weight: 700;
}
.rule-id-main {
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 700;
}
.rule-id-sub {
  margin-top: 3px;
  color: var(--ai-ink-4);
  font-size: 10.5px;
}
.rule-when {
  color: var(--ai-ink-2);
  line-height: 1.55;
  word-break: break-word;
}
.rule-when.empty {
  color: var(--ai-ink-4);
}
.rule-then {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}
.rule-verdict-chip,
.rule-risk {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 700;
  white-space: nowrap;
}
.rule-verdict-chip--ok,
.rule-risk--low {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
}
.rule-verdict-chip--warn,
.rule-risk--mid {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
}
.rule-verdict-chip--bad,
.rule-risk--high {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
}
.rule-verdict-chip--default {
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
}
.rule-action-text {
  color: var(--ai-ink-3);
  line-height: 1.5;
  word-break: break-word;
}
.rule-row-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
.rule-icon-btn {
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--ai-border);
  border-radius: 5px;
  background: var(--ai-surface);
  color: var(--ai-ink-3);
  cursor: pointer;
  transition: background .12s, color .12s, border-color .12s;
}
.rule-icon-btn:hover:not(:disabled) {
  border-color: var(--ai-ink-1);
  color: var(--ai-ink-1);
  background: var(--ai-surface-2);
}
.rule-icon-btn.danger:hover:not(:disabled) {
  border-color: var(--ai-bad);
  color: var(--ai-bad);
  background: var(--ai-bad-soft);
}
.rule-icon-btn:disabled {
  cursor: not-allowed;
  opacity: .4;
}
.rule-empty {
  min-height: 260px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 34px;
  color: var(--ai-ink-4);
  text-align: center;
}
.rule-empty-title {
  color: var(--ai-ink-1);
  font-size: 14px;
  font-weight: 700;
}
.rule-empty-sub {
  max-width: 420px;
  color: var(--ai-ink-3);
  font-size: 12.5px;
  line-height: 1.6;
}
.rule-preview-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
.rule-preview-metric {
  min-height: 92px;
  padding: 14px 16px;
  border-right: 1px solid var(--ai-border);
}
.rule-preview-metric:last-child {
  border-right: none;
}
.rule-preview-label {
  color: var(--ai-ink-4);
  font-size: 10.5px;
  font-weight: 700;
  text-transform: uppercase;
}
.rule-preview-value {
  margin-top: 8px;
  color: var(--ai-ink-1);
  font-size: 24px;
  font-weight: 700;
}
.rule-preview-value.ok { color: var(--ai-ok); }
.rule-preview-value.warn { color: var(--ai-warn); }
.rule-preview-value.bad { color: var(--ai-bad); }
.rule-preview-hint {
  margin-top: 4px;
  color: var(--ai-ink-4);
  font-size: 11.5px;
}
.rule-canvas-split {
  flex: 1;
  min-height: 0;
  display: flex;
  overflow: hidden;
}

@media (max-width: 900px) {
  .rule-editor-topline,
  .rule-table-head,
  .rule-preview-head {
    flex-direction: column;
  }
  .rule-editor-actions {
    width: 100%;
    justify-content: flex-start;
    flex-wrap: wrap;
  }
  .rule-preview-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .rule-preview-metric:nth-child(2) {
    border-right: none;
  }
  .rule-canvas-split {
    flex-direction: column;
  }
  .surface-canvas-inspector {
    width: 100%;
    height: 260px;
    border-left: none;
    border-top: 1px solid var(--ai-border);
  }
}

.sb-item { white-space: nowrap; }
.sb-spacer { flex: 1; }

</style>
