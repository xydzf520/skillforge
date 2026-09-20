<template>
  <section class="learning-flow-mini ai-card" :class="{ compact }">
    <div class="lfm-head">
      <div>
        <span class="ai-pill accent">智能闭环</span>
        <h3>{{ title || '数据流血缘' }}</h3>
        <p>{{ subtitle || defaultSubtitle }}</p>
      </div>
      <div class="lfm-actions">
        <button class="ai-btn sm" type="button" :disabled="loading || !canLoad" @click="loadLineage">
          <SfShellIcon name="refresh" />刷新
        </button>
        <button class="ai-btn sm primary" type="button" :disabled="!canLoad" @click="openFullFlow">
          <SfShellIcon name="flow" />打开数据流
        </button>
      </div>
    </div>

    <div class="lfm-stats">
      <span><em>流入</em><strong>{{ inboundCount }}</strong></span>
      <span><em>流出</em><strong>{{ outboundCount }}</strong></span>
      <span><em>关系</em><strong>{{ relationCount }}</strong></span>
      <span><em>原始数据</em><strong>false</strong></span>
    </div>

    <div v-if="loading" class="lfm-empty">正在加载血缘…</div>
    <div v-else-if="edges.length" class="lfm-flow" aria-label="实体数据流血缘">
      <article v-for="edge in displayEdges" :key="edgeKey(edge)" class="lfm-edge" :class="edgeTone(edge)">
        <button type="button" class="lfm-node" @click="openEntity(edge.from_type, edge.from_id)">
          <span>{{ entityText(edge.from_type) }}</span>
          <strong>{{ shortId(edge.from_id) }}</strong>
        </button>
        <div class="lfm-stream">
          <i></i><i></i><i></i>
          <em>{{ relationText(edge.relation) }}</em>
        </div>
        <button type="button" class="lfm-node" @click="openEntity(edge.to_type, edge.to_id)">
          <span>{{ entityText(edge.to_type) }}</span>
          <strong>{{ shortId(edge.to_id) }}</strong>
        </button>
      </article>
    </div>
    <div v-else class="lfm-empty">
      暂无血缘。触发运行、检索、训练、部署或点击“捕获事件”后会在这里流动。
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { learningApi } from '@/api'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

defineOptions({ name: 'LearningFlowMini' })

const props = withDefaults(defineProps<{
  entityType: string
  entityId: string
  title?: string
  subtitle?: string
  compact?: boolean
  limit?: number
}>(), {
  title: '',
  subtitle: '',
  compact: false,
  limit: 12,
})

type LineageEdge = {
  id?: string | number
  from_type?: string
  from_id?: string
  to_type?: string
  to_id?: string
  relation?: string
  created_at?: string | null
  metadata?: Record<string, unknown>
}

const router = useRouter()
const loading = ref(false)
const edges = ref<LineageEdge[]>([])
const canLoad = computed(() => Boolean(props.entityType && props.entityId))
const displayEdges = computed(() => edges.value.slice(0, Math.max(1, props.limit || 12)))
const inboundCount = computed(() => edges.value.filter((edge) => edge.to_type === props.entityType && edge.to_id === props.entityId).length)
const outboundCount = computed(() => edges.value.filter((edge) => edge.from_type === props.entityType && edge.from_id === props.entityId).length)
const relationCount = computed(() => new Set(edges.value.map((edge) => edge.relation || '-')).size)
const defaultSubtitle = computed(() => `${entityText(props.entityType)} · ${shortId(props.entityId)} 的上下游流动`)

async function loadLineage() {
  if (!canLoad.value) return
  loading.value = true
  try {
    const result = await learningApi.lineage(props.entityType, props.entityId, { limit: props.limit }) as { edges?: LineageEdge[] }
    edges.value = result?.edges || []
  } finally {
    loading.value = false
  }
}

function openFullFlow() {
  if (!canLoad.value) return
  router.push({ path: '/learning-flow', query: { entity_type: props.entityType, entity_id: props.entityId } })
}

function openEntity(type?: string, id?: string) {
  if (!type || !id) return
  router.push({ path: '/learning-flow', query: { entity_type: type, entity_id: id } })
}

function edgeKey(edge: LineageEdge) {
  return String(edge.id || `${edge.from_type}:${edge.from_id}-${edge.relation}-${edge.to_type}:${edge.to_id}`)
}

function edgeTone(edge: LineageEdge) {
  const relation = String(edge.relation || '')
  if (relation.includes('failed') || relation.includes('rollback')) return 'warn'
  if (['deployed_to', 'indexed_as', 'used_as_context', 'used_as_model', 'feedback_on_model_decision', 'submitted_feedback', 'requested_deployment_review'].includes(relation)) return 'good'
  if (relation.startsWith('controlled_') || relation === 'served_model_inference') return 'accent'
  if (relation.includes('candidate') || relation === 'proposed_change') return 'accent'
  return ''
}

function entityText(value?: string) {
  const map: Record<string, string> = {
    run: '运行',
    execution_run: '运行',
    execution_artifact: '执行数据',
    decision_log: '决策',
    learning_artifact: '学习资产',
    knowledge_document: '知识',
    knowledge_query: '检索',
    training_sample: '训练样本',
    training_job: '训练任务',
    model_artifact: '模型产物',
    model_deployment: '模型部署',
    improvement_candidate: '改进候选',
    agent: 'Agent',
    dingtalk_feedback: '钉钉反馈',
    sf_call: 'SF 调用',
    sf_usage_aggregate: 'SF 聚合',
    agent_thread: 'Agent 会话',
    agent_tool: 'Agent 工具',
    skill: 'Skill',
    todo: '待办',
    decision_request: '评审待办',
    todo_dispatch_task: '派发任务',
    governance_queue: '治理队列',
  }
  return map[String(value || '')] || String(value || '-')
}

function relationText(value?: string) {
  const map: Record<string, string> = {
    produced: '生成',
    indexed_as: '入库',
    used_as_context: '引用',
    created_sample: '样本',
    proposed_change: '提出候选',
    reviewed_by: '治理',
    trained_from: '训练来源',
    produced_artifact: '产出数据/模型',
    captured_data: '记录数据',
    deployed_to: '部署',
    rollback_target: '回滚',
    remembered_as: '记忆',
    used_as_model: '使用模型',
    feedback_for_decision: '反馈决策',
    feedback_on_model_decision: '反馈模型决策',
    submitted_feedback: '反馈回传',
    controlled_run: '运行 Agent 控制',
    controlled_analysis: '分析 Agent 控制',
    controlled_inference: '推理 Agent 控制',
    controlled_training: '训练 Agent 控制',
    served_model_inference: '模型推理服务',
    requested_deployment_review: '提交部署审核',
    used_skill: '使用 Skill',
    called_tool: '调用工具',
  }
  return map[String(value || '')] || String(value || '-')
}

function shortId(value?: string) {
  const text = String(value || '-')
  if (text.length <= 18) return text
  return `${text.slice(0, 8)}…${text.slice(-6)}`
}

onMounted(loadLineage)
watch(() => [props.entityType, props.entityId, props.limit], loadLineage)
</script>

<style scoped>
.learning-flow-mini {
  border: 1px solid rgba(99, 102, 241, 0.18);
  background:
    radial-gradient(circle at 12% 18%, rgba(99, 102, 241, 0.1), transparent 32%),
    linear-gradient(135deg, rgba(255,255,255,0.96), rgba(248,250,252,0.92));
}
.lfm-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.lfm-head h3 {
  margin: 8px 0 4px;
  font-size: 17px;
  color: #0f172a;
}
.lfm-head p {
  margin: 0;
  color: #64748b;
  font-size: 13px;
}
.lfm-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.lfm-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-top: 14px;
}
.lfm-stats span {
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 14px;
  padding: 10px;
  background: rgba(255,255,255,0.74);
}
.lfm-stats em {
  display: block;
  font-style: normal;
  color: #64748b;
  font-size: 12px;
}
.lfm-stats strong {
  display: block;
  margin-top: 4px;
  color: #111827;
  font-size: 18px;
}
.lfm-flow {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}
.lfm-edge {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 120px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  padding: 10px;
  border-radius: 16px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: rgba(255,255,255,0.78);
}
.lfm-edge.good { border-color: rgba(16, 185, 129, 0.24); }
.lfm-edge.warn { border-color: rgba(245, 158, 11, 0.3); }
.lfm-edge.accent { border-color: rgba(99, 102, 241, 0.28); }
.lfm-node {
  min-width: 0;
  border: 0;
  border-radius: 12px;
  padding: 9px 10px;
  text-align: left;
  background: #f8fafc;
  color: #0f172a;
  cursor: pointer;
}
.lfm-node:hover { background: #eef2ff; }
.lfm-node span {
  display: block;
  color: #64748b;
  font-size: 12px;
}
.lfm-node strong {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-top: 3px;
  font-size: 13px;
}
.lfm-stream {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 36px;
  color: #6366f1;
  font-size: 12px;
}
.lfm-stream::before {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  top: 50%;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(99,102,241,0.48), transparent);
}
.lfm-stream i {
  position: absolute;
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: #6366f1;
  animation: lfm-stream 1.45s linear infinite;
}
.lfm-stream i:nth-child(2) { animation-delay: .35s; }
.lfm-stream i:nth-child(3) { animation-delay: .7s; }
.lfm-stream em {
  position: relative;
  z-index: 1;
  padding: 2px 8px;
  border-radius: 999px;
  font-style: normal;
  background: #eef2ff;
}
.lfm-empty {
  margin-top: 14px;
  padding: 18px;
  border: 1px dashed rgba(148, 163, 184, 0.34);
  border-radius: 16px;
  color: #64748b;
  background: rgba(248,250,252,0.68);
  text-align: center;
}
.learning-flow-mini.compact .lfm-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.learning-flow-mini.compact .lfm-edge { grid-template-columns: minmax(0, 1fr); }
.learning-flow-mini.compact .lfm-stream { min-height: 24px; }
@keyframes lfm-stream {
  0% { left: 8%; opacity: 0; transform: translateY(-50%) scale(.6); }
  20% { opacity: 1; }
  80% { opacity: 1; }
  100% { left: 88%; opacity: 0; transform: translateY(-50%) scale(1); }
}
@media (max-width: 760px) {
  .lfm-head { flex-direction: column; }
  .lfm-actions { justify-content: flex-start; }
  .lfm-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .lfm-edge { grid-template-columns: minmax(0, 1fr); }
}
</style>
