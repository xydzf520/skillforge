<template>
  <div class="overview">
    <!-- 头部 -->
    <div class="ov-header">
      <div class="ov-icon">
        <icon-bulb :size="24" />
      </div>
      <div class="ov-title-area">
        <h2 class="ov-name">{{ meta.name || skillId }}</h2>
        <p class="ov-desc">{{ meta.description || goal?.slice(0, 120) || '暂无描述' }}</p>
        <div class="ov-tags">
          <a-tag v-if="meta.department" size="small">{{ meta.department }}</a-tag>
          <a-tag v-if="meta.trigger_type" size="small" color="arcoblue">{{ meta.trigger_type }}</a-tag>
          <a-tag v-if="meta.risk_level" size="small" :color="meta.risk_level === 'R4' ? 'red' : meta.risk_level === 'R3' ? 'orange' : 'green'">{{ meta.risk_level }}</a-tag>
          <a-tag v-if="meta.status" size="small" :color="({ active: 'green', draft: 'blue', shadow: 'purple', deprecated: 'gray' } as Record<string, string>)[meta.status]">{{ meta.status }}</a-tag>
        </div>
      </div>
      <!-- 可发布性：固定位置 + 状态栏风格（即使没运行也显示） -->
      <div class="ov-readiness">
        <div class="ov-readiness-label">可发布性</div>
        <div class="ov-readiness-row">
          <template v-if="readiness">
            <a-tag v-if="readiness.can_publish" color="green" size="large">
              ✓ 通过质检
            </a-tag>
            <a-tag v-else color="red" size="large">
              ✗ {{ readiness.blocker_count }} 个阻断
            </a-tag>
          </template>
          <a-tag v-else color="gray" size="large">
            ✨ 未检查
          </a-tag>
          <a-button
            size="mini"
            type="outline"
            :loading="linting"
            @click="$emit('run-lint')"
          >
            {{ readiness ? '重新检查' : '运行质检' }}
          </a-button>
        </div>
        <!-- §3.5 历史回放 sanity 状态：独立一行 -->
        <div v-if="readiness && readiness.replay && readiness.replay.status" class="ov-readiness-row ov-readiness-replay">
          <a-tag size="small"
                 :color="replayStatusColor[readiness.replay.status] || 'gray'"
                 :title="readiness.replay.message || ''">
            回放 {{ replayStatusLabel[readiness.replay.status] || readiness.replay.status }}
            <span v-if="readiness.replay.sample_size">
              · {{ readiness.replay.sample_size }} 样本
            </span>
            <span v-if="readiness.replay.adoption_rate != null">
              · 采纳 {{ Math.round(readiness.replay.adoption_rate * 100) }}%
            </span>
          </a-tag>
        </div>
      </div>
    </div>

    <div v-if="taskContract || taskGateItems.length" class="ov-v7">
      <div class="ov-v7-head">
        <span class="ov-v7-title">v7 任务合同</span>
        <a-tag v-if="taskRiskLevel" size="small" :color="taskRiskLevel === 'R3' ? 'red' : 'orange'">{{ taskRiskLevel }}</a-tag>
      </div>
      <div class="ov-v7-grid">
        <div v-if="taskContract" class="ov-v7-card">
          <div class="ov-v7-label">原始意图</div>
          <div class="ov-v7-value">{{ taskContract.goal || '未记录' }}</div>
          <div class="ov-v7-meta">
            <span>{{ taskContract.trigger?.description || taskContract.trigger?.type || 'manual' }}</span>
            <span>{{ taskContract.output?.adapter || 'adapter' }} → {{ taskContract.output?.recipient || '待确认' }}</span>
          </div>
        </div>
        <div v-if="taskGateItems.length" class="ov-v7-card">
          <div class="ov-v7-label">60 分门禁</div>
          <ConfirmBar :items="taskGateItems" />
        </div>
        <div v-if="previewContent" class="ov-v7-card ov-v7-card-wide">
          <div class="ov-v7-label">预演快照</div>
          <PreviewCard
            :adapter="taskContract?.output?.adapter || 'preview'"
            :content="previewContent"
            title="最近一次确认的预演结果"
          />
        </div>
      </div>
    </div>

    <!-- 模块摘要卡片 -->
    <div class="ov-grid">
      <div class="ov-card" :class="{ 'ov-card-complete': isComplete('goal') }" @click="$emit('go-module', 'goal')">
        <div class="ov-card-head">
          <icon-bulb :size="16" class="ov-card-icon" />
          <span class="ov-card-title">目标</span>
          <span class="ov-card-count">{{ goal ? goal.length + ' 字' : '未填写' }}</span>
        </div>
        <p class="ov-card-preview">{{ goal?.slice(0, 80) || '点击编写 Skill 目标...' }}</p>
      </div>

      <div class="ov-card" :class="{ 'ov-card-complete': isComplete('rules') }" @click="$emit('go-module', 'rules')">
        <div class="ov-card-head">
          <icon-list :size="16" class="ov-card-icon" />
          <span class="ov-card-title">规则</span>
          <span class="ov-card-count">{{ rules.length ? rules.length + ' 条' : '未填写' }}</span>
        </div>
        <p class="ov-card-preview">
          <template v-if="rules.length">
            <span v-for="(r, i) in rules.slice(0, 3)" :key="i" class="ov-chip">{{ r.name || r.id }}</span>
            <span v-if="rules.length > 3" class="ov-more">+{{ rules.length - 3 }}</span>
          </template>
          <template v-else>点击添加决策规则...</template>
        </p>
      </div>

      <div class="ov-card" :class="{ 'ov-card-complete': isComplete('params') }" @click="$emit('go-module', 'params')">
        <div class="ov-card-head">
          <icon-settings :size="16" class="ov-card-icon" />
          <span class="ov-card-title">参数</span>
          <span class="ov-card-count">{{ params.length ? params.length + ' 个' : '未填写' }}</span>
        </div>
        <p class="ov-card-preview">
          <template v-if="params.length">
            <span v-for="(p, i) in params.slice(0, 4)" :key="i" class="ov-chip">{{ p.name }}={{ p.default_value ?? p.value ?? '' }}</span>
            <span v-if="params.length > 4" class="ov-more">+{{ params.length - 4 }}</span>
          </template>
          <template v-else>点击配置参数阈值...</template>
        </p>
      </div>

      <div class="ov-card" :class="{ 'ov-card-complete': isComplete('output_table') }" @click="$emit('go-module', 'output_table')">
        <div class="ov-card-head">
          <icon-file :size="16" class="ov-card-icon" />
          <span class="ov-card-title">输出</span>
          <span class="ov-card-count">{{ outputTable.length ? outputTable.length + ' 个字段' : '未填写' }}</span>
        </div>
        <p class="ov-card-preview">
          <template v-if="outputTable.length">
            <span v-for="(o, i) in outputTable.slice(0, 3)" :key="i" class="ov-chip">{{ o.name }}</span>
          </template>
          <template v-else>点击定义输出字段...</template>
        </p>
      </div>

      <div class="ov-card" :class="{ 'ov-card-complete': isComplete('todos') }" @click="$emit('go-module', 'todos')">
        <div class="ov-card-head">
          <icon-check-circle :size="16" class="ov-card-icon" />
          <span class="ov-card-title">待办</span>
          <span class="ov-card-count">{{ todos.length ? todos.length + ' 个模板' : '未配置' }}</span>
        </div>
        <p class="ov-card-preview">
          <template v-if="todos.length">
            <span v-for="(todo, i) in todos.slice(0, 3)" :key="i" class="ov-chip">{{ todo.title || todo.kind }}</span>
          </template>
          <template v-else>点击配置收件中心待办...</template>
        </p>
      </div>

      <div class="ov-card" :class="{ 'ov-card-complete': isComplete('test_cases') }" @click="$emit('go-module', 'test_cases')">
        <div class="ov-card-head">
          <icon-experiment :size="16" class="ov-card-icon" />
          <span class="ov-card-title">测试</span>
          <span class="ov-card-count">{{ testCases.length ? testCases.length + ' 个用例' : '未填写' }}</span>
        </div>
        <p class="ov-card-preview">
          <template v-if="testCases.length">
            <span v-for="(t, i) in testCases.slice(0, 3)" :key="i" class="ov-chip">{{ t.name }}</span>
          </template>
          <template v-else>点击添加测试用例...</template>
        </p>
      </div>

      <div class="ov-card" :class="{ 'ov-card-complete': isComplete('workflow') }" @click="$emit('go-module', 'workflow')">
        <div class="ov-card-head">
          <icon-share-alt :size="16" class="ov-card-icon" />
          <span class="ov-card-title">工作流</span>
          <span class="ov-card-count">{{ workflowNodes ? workflowNodes + ' 个节点' : '未填写' }}</span>
        </div>
        <p class="ov-card-preview">{{ workflowNodes ? '已配置工作流编排' : '点击配置工作流...' }}</p>
      </div>
    </div>

    <!-- 提示 -->
    <div class="ov-tip">
      点击卡片进入模块编辑 · 右侧 AI 助手可协助优化 · 切换「源码」直接编辑 SKILL.md
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import {
  IconBulb, IconList, IconSettings, IconFile, IconExperiment, IconShareAlt,
  IconCheckCircle,
} from '@arco-design/web-vue/es/icon'
import PreviewCard from '@/components/chat/PreviewCard.vue'
import ConfirmBar from '@/components/chat/ConfirmBar.vue'

const props = defineProps({
  doc: { type: Object as PropType<any>, default: () => ({}) },
  skillId: { type: String, default: '' },
  readiness: { type: Object as PropType<any>, default: null },
  linting: { type: Boolean, default: false },
})

defineEmits(['go-module', 'run-lint'])

const meta = computed(() => props.doc?.meta || {})
const goal = computed(() => props.doc?.goal || '')
const rules = computed(() => props.doc?.rules || [])
const params = computed(() => props.doc?.params || [])
const outputTable = computed(() => props.doc?.output_table || [])
const todos = computed(() => props.doc?.todos || [])
const testCases = computed(() => props.doc?.test_cases || [])
const workflowNodes = computed(() => props.doc?.workflow?.nodes?.length || 0)
const taskContract = computed(() => props.doc?.custom_sections?.__task_contract || null)
const taskGate = computed(() => props.doc?.custom_sections?.__task_gate || {})
const taskGateItems = computed(() => taskGate.value?.items || [])
const taskRiskLevel = computed(() => taskContract.value?.risks?.level || '')
const previewContent = computed(() => {
  const artifacts = props.doc?.custom_sections?.__artifacts || {}
  const reviewRaw = artifacts['task-review-state.json']
  if (reviewRaw) {
    try {
      const payload = JSON.parse(reviewRaw)
      const passed = (payload.gate?.items || []).filter((item: Record<string, unknown>) => item.passed).map((item: Record<string, unknown>) => item.label)
      if (passed.length) return `已确认：${passed.join('、')}`
    } catch { /* noop */ }
  }
  const intentRaw = artifacts['intent.md']
  if (!intentRaw) return ''
  return intentRaw.split('\n').slice(0, 10).join('\n')
})

// 完成度判定：用于卡片的灰/蓝着色
function isComplete(key: string) {
  const d = props.doc || {}
  if (key === 'goal') return !!(d.goal && String(d.goal).trim())
  if (key === 'rules') return (d.rules || []).length > 0
  if (key === 'params') return (d.params || []).length > 0
  if (key === 'output_table') return (d.output_table || []).length > 0
  if (key === 'todos') return (d.todos || []).length > 0
  if (key === 'test_cases') return (d.test_cases || []).length > 0
  if (key === 'workflow') return (d.workflow?.nodes || []).length > 0
  return false
}

// §3.5 replay sanity 状态标签
const replayStatusColor: Record<string, string> = {
  ok: 'green',
  insufficient: 'orange',
  degraded: 'red',
  skipped: 'gray',
}
const replayStatusLabel: Record<string, string> = {
  ok: '正常',
  insufficient: '样本不足',
  degraded: '偏差',
  skipped: '跳过',
}
</script>

<style scoped>
.overview { padding: 32px 40px; max-width: 800px; margin: 0 auto; }

.ov-header { display: flex; gap: 16px; margin-bottom: 28px; }
.ov-icon {
  width: 48px; height: 48px; border-radius: 12px; flex-shrink: 0;
  background: var(--sf-brand-action);
  color: var(--sf-on-action); display: flex; align-items: center; justify-content: center;
  box-shadow: var(--ai-shadow-1);
}
.ov-title-area { flex: 1; }
.ov-name { font-size: 22px; font-weight: 800; color: var(--ai-ink-1); margin: 0 0 4px; letter-spacing: -0.01em; }
.ov-desc { font-size: 13px; color: var(--ai-ink-2); margin: 0 0 8px; line-height: 1.5; font-weight: 600; }
.ov-tags { display: flex; gap: 4px; }

/* 可发布性：固定高度的状态栏风格，即使没运行也显示 */
.ov-readiness {
  display: flex; flex-direction: column; align-items: flex-end; gap: 6px;
  padding: 10px 14px; border-radius: 8px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  min-width: 180px;
}
.ov-readiness-label { font-size: 11px; color: var(--ai-ink-3); font-weight: 800; text-transform: uppercase; letter-spacing: 0.4px; }
.ov-readiness-row {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap; justify-content: flex-end;
}
.ov-readiness-replay { padding-top: 2px; }

.ov-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
.ov-v7 {
  margin-bottom: 22px;
  padding: 16px 18px;
  border-radius: 8px;
  border: 1px solid rgba(198, 106, 20, 0.18);
  background: var(--ai-surface);
}
.ov-v7-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.ov-v7-title {
  font-size: 13px;
  font-weight: 800;
  color: var(--ai-ink-1);
}
.ov-v7-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.ov-v7-card {
  padding: 14px;
  border-radius: 12px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
}
.ov-v7-card-wide {
  grid-column: 1 / -1;
}
.ov-v7-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: .4px;
  color: var(--ai-ink-3);
  font-weight: 800;
  margin-bottom: 8px;
}
.ov-v7-value {
  font-size: 14px;
  line-height: 1.6;
  color: var(--ai-ink-1);
  font-weight: 700;
  margin-bottom: 8px;
}
.ov-v7-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--ai-ink-2);
  font-weight: 600;
}
/* 默认：灰色边框 + 灰色图标（未填写态） */
.ov-card {
  padding: 16px; border: 1px solid var(--ai-border); border-radius: 8px;
  background: var(--ai-surface);
  cursor: pointer; transition: all var(--sf-transition);
  box-shadow: 0 4px 12px var(--ai-surface-2);
}
.ov-card .ov-card-icon { color: var(--ai-ink-3); }
.ov-card:hover {
  border-color: var(--ai-warn);
  box-shadow: 0 8px 20px var(--ai-border);
  transform: translateY(-1px);
}
.ov-card:hover .ov-card-icon { color: var(--ai-warn); }
/* 已填写态：橙色边框 + 橙色图标（与新版品牌色一致） */
.ov-card-complete {
  border-color: var(--ai-warn);
  background: var(--ai-warn-soft);
}
.ov-card-complete .ov-card-icon { color: var(--ai-warn); }
.ov-card-complete:hover { border-color: var(--ai-warn); }

.ov-card-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.ov-card-icon { flex-shrink: 0; }
.ov-card-title { font-size: 13px; font-weight: 800; color: var(--ai-ink-1); }
.ov-card-count { margin-left: auto; font-size: 11px; color: var(--ai-ink-3); font-weight: 700; }
.ov-card-preview { font-size: 12px; color: var(--ai-ink-2); line-height: 1.5; margin: 0; font-weight: 600; }

.ov-chip {
  display: inline-block; padding: 1px 6px; border-radius: var(--sf-radius-xs); margin-right: 4px; margin-bottom: 2px;
  background: var(--ai-surface-2); font-size: 11px; color: var(--ai-ink-2); font-family: var(--ai-font-mono); font-weight: 700;
}
.ov-more { font-size: 11px; color: var(--ai-ink-3); font-weight: 700; }

.ov-tip { margin-top: 24px; text-align: center; font-size: 12px; color: var(--ai-ink-3); font-weight: 600; }
</style>
