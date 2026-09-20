<template>
  <div class="block">
    <div class="block-header">
      <div class="block-title">参数</div>
      <a-space size="small">
        <a-button size="mini" type="outline" :disabled="disabled || !value?.length" :loading="comparingParams" @click="handleCompareParams">影响预览</a-button>
        <a-button size="mini" class="ai-btn-outline" :disabled="disabled || !value?.length" :loading="loadingTuning" @click="handleSuggestTuning">AI 调优</a-button>
        <a-button size="mini" class="ai-btn-outline" :disabled="disabled" :loading="derivingThresholds" @click="handleDeriveThresholds">AI 推导阈值</a-button>
        <a-button size="mini" type="outline" :disabled="disabled" @click="addRow">+ 添加参数</a-button>
      </a-space>
    </div>

    <div v-if="!localParams.length" class="block-empty">暂无参数，点击上方添加</div>

    <div class="param-table" v-else>
      <div class="pt-head">
        <span class="pt-col name">参数名</span>
        <span class="pt-col val">默认值</span>
        <span class="pt-col desc">说明</span>
        <span class="pt-col act"></span>
      </div>
      <div v-for="(p, i) in localParams" :key="i" class="pt-row">
        <a-input v-model="p.name" size="mini" class="pt-col name" placeholder="参数名" :disabled="disabled" @input="emitUpdate" />
        <a-input v-model="p.default_value_str" size="mini" class="pt-col val" placeholder="值" :disabled="disabled" @input="emitUpdate" />
        <a-input v-model="p.description" size="mini" class="pt-col desc" placeholder="说明" :disabled="disabled" @input="emitUpdate" />
        <a-button size="mini" type="text" class="pt-col act" :disabled="!skillId || !p.name" title="§4.5 证据卡" :loading="loadingEvidence === p.name" @click="loadParamEvidence(p.name)">
          <icon-bar-chart :size="13" />
        </a-button>
        <a-button size="mini" type="text" status="danger" class="pt-col act" :disabled="disabled" @click="removeRow(i)">
          <icon-delete :size="13" />
        </a-button>
      </div>
    </div>

    <!-- §4.5 参数证据卡 -->
    <div v-if="paramEvidence" class="evidence-card">
      <div class="ec-head">
        <span class="ec-title">参数证据 · <strong>{{ paramEvidence.param_name }}</strong></span>
        <a-button type="text" size="mini" @click="paramEvidence = null">关闭</a-button>
      </div>
      <div class="ec-row">
        <span class="ec-label">当前值</span>
        <span class="ec-val">{{ paramEvidence.current_value ?? '未设置' }}</span>
      </div>
      <div v-if="paramEvidence.recommended != null" class="ec-row highlight">
        <span class="ec-label">推荐值</span>
        <span class="ec-val">{{ paramEvidence.recommended }}</span>
        <a-tag :color="paramEvidence.confidence >= 0.8 ? 'green' : paramEvidence.confidence >= 0.5 ? 'orange' : 'gray'" size="small">
          置信 {{ Math.round((paramEvidence.confidence || 0) * 100) }}%
        </a-tag>
        <a-button size="mini" type="primary" class="ec-apply" @click="applyEvidenceRecommendation()">应用</a-button>
      </div>
      <div class="ec-row">
        <span class="ec-label">样本量</span>
        <span class="ec-val">{{ paramEvidence.sample_size }} 条（最近 {{ paramEvidence.window_days }} 天）</span>
      </div>
      <div class="ec-row">
        <span class="ec-label">引用此参数</span>
        <span class="ec-val">{{ paramEvidence.referenced_count }} 条决策</span>
      </div>
      <div v-if="paramEvidence.sample_size > 0" class="ec-distribution">
        <div class="ec-dist-title">决策结果分布</div>
        <div class="ec-dist-bars">
          <div class="ec-bar ec-bar-completed" :style="{ flex: paramEvidence.distribution.completed || 0.01 }"
               :title="`采纳 ${paramEvidence.distribution.completed} (${Math.round((paramEvidence.adoption_rate || 0) * 100)}%)`">
            <span v-if="paramEvidence.adoption_rate > 0.1">采纳 {{ Math.round(paramEvidence.adoption_rate * 100) }}%</span>
          </div>
          <div class="ec-bar ec-bar-rejected" :style="{ flex: paramEvidence.distribution.rejected || 0.01 }"
               :title="`驳回 ${paramEvidence.distribution.rejected} (${Math.round((paramEvidence.rejection_rate || 0) * 100)}%)`">
            <span v-if="paramEvidence.rejection_rate > 0.1">驳回 {{ Math.round(paramEvidence.rejection_rate * 100) }}%</span>
          </div>
          <div v-if="paramEvidence.distribution.pending > 0" class="ec-bar ec-bar-pending" :style="{ flex: paramEvidence.distribution.pending || 0.01 }"
               :title="`待处理 ${paramEvidence.distribution.pending}`">
            <span v-if="paramEvidence.distribution.pending / paramEvidence.sample_size > 0.1">待处理</span>
          </div>
        </div>
      </div>
      <div v-if="paramEvidence.evidence_text" class="ec-evidence">
        <div class="ec-evidence-head">AI 证据</div>
        <div class="ec-evidence-body">{{ paramEvidence.evidence_text }}</div>
      </div>
      <div v-if="!paramEvidence.enough_data" class="ec-warning">
        <icon-exclamation-circle-fill :size="12" /> 样本不足，建议先让 Skill 运行更多决策
      </div>
    </div>

    <!-- 影响预览结果 -->
    <div v-if="compareResult" class="tuning-result">
      <div class="tuning-title">影响预览</div>
      <div v-if="compareResult.impact" class="impact-summary">
        <a-tag color="green" size="small">改善 {{ compareResult.impact.improved || 0 }}</a-tag>
        <a-tag color="red" size="small">退化 {{ compareResult.impact.regressed || 0 }}</a-tag>
        <a-tag color="gray" size="small">不变 {{ compareResult.impact.unchanged || 0 }}</a-tag>
      </div>
      <div v-if="compareResult.details?.length" class="impact-details">
        <div v-for="(d, i) in compareResult.details.slice(0, 5)" :key="i" class="impact-item">
          <span class="impact-label">{{ d.run_id || `执行 ${Number(i) + 1}` }}</span>
          <span class="impact-old">{{ d.old_result }}</span>
          <span class="impact-arrow">→</span>
          <span class="impact-new">{{ d.new_result }}</span>
        </div>
      </div>
      <a-button size="mini" type="text" @click="compareResult = null">关闭</a-button>
    </div>

    <!-- AI 调优建议 -->
    <div v-if="tuningSuggestions?.length" class="tuning-result">
      <div class="tuning-title">AI 调优建议</div>
      <div v-for="(s, i) in tuningSuggestions" :key="i" class="tuning-item">
        <div class="tuning-param">
          <strong>{{ s.param || s.name }}</strong>
          <span class="tuning-change">{{ s.current }} → {{ s.suggested }}</span>
          <a-tag :color="s.confidence >= 0.8 ? 'green' : s.confidence >= 0.5 ? 'orange' : 'gray'" size="small">
            {{ Math.round((s.confidence || 0) * 100) }}%
          </a-tag>
        </div>
        <div v-if="s.evidence" class="tuning-evidence">{{ s.evidence }}</div>
        <a-button size="mini" type="primary" @click="applyTuning(s)">应用</a-button>
      </div>
      <a-button size="mini" type="text" @click="tuningSuggestions = null">关闭</a-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import type { PropType } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconDelete, IconBarChart, IconExclamationCircleFill } from '@arco-design/web-vue/es/icon'
import { skillApi as rawSkillApi } from '@/api'

const props = defineProps({
  value: { type: Array as PropType<any[]>, default: () => [] },
  disabled: { type: Boolean, default: false },
  skillId: { type: String, default: '' },
})
const emit = defineEmits(['update'])
const skillApi: any = rawSkillApi

const localParams = ref<any[]>([])
const comparingParams = ref(false)
const compareResult = ref<any>(null)
const loadingTuning = ref(false)
const tuningSuggestions = ref<any[] | null>(null)
const derivingThresholds = ref(false)
const paramEvidence = ref<any>(null)         // §4.5 证据卡数据
const loadingEvidence = ref('')         // 正在加载的参数名

watch(() => props.value, (v) => {
  localParams.value = (v || []).map(p => ({
    name: p.name || '',
    default_value_str: p.default_value != null ? String(p.default_value) : '',
    description: p.description || '',
  }))
}, { immediate: true, deep: true })

function emitUpdate() {
  emit('update', localParams.value.map(p => {
    let dv = p.default_value_str
    try { dv = JSON.parse(dv) } catch { /* 保持字符串 */ }
    return { name: p.name, default_value: dv, description: p.description }
  }))
}

function addRow() {
  localParams.value.push({ name: '', default_value_str: '', description: '' })
  emitUpdate()
}
function removeRow(i: unknown) { localParams.value.splice(Number(i), 1); emitUpdate() }

// 影响预览
async function handleCompareParams() {
  if (!props.skillId) return Message.warning('请先保存 Skill')
  comparingParams.value = true
  try {
    const params = {}
    localParams.value.forEach(p => {
      if (p.name) {
        let v = p.default_value_str
        try { v = JSON.parse(v) } catch { /* 保持 */ }
        (params as Record<string, unknown>)[p.name] = v
      }
    })
    compareResult.value = await skillApi.compareParams(props.skillId, { params })
  } catch (e: any) { Message.error(e._message || '影响预览失败') }
  finally { comparingParams.value = false }
}

// AI 调优
async function handleSuggestTuning() {
  if (!props.skillId) return Message.warning('请先保存 Skill')
  loadingTuning.value = true
  try {
    const r = await skillApi.suggestParamTuning(props.skillId, 30)
    tuningSuggestions.value = r.suggestions || r.tuning_suggestions || []
    if (!tuningSuggestions.value?.length) Message.info('暂无调优建议')
  } catch (e: any) { Message.error(e._message || '获取调优建议失败') }
  finally { loadingTuning.value = false }
}

// AI 推导阈值
async function handleDeriveThresholds() {
  if (!props.skillId) return Message.warning('请先保存 Skill')
  derivingThresholds.value = true
  try {
    const r = await skillApi.deriveThresholds(props.skillId, {})
    const derived = r.thresholds || r.params || []
    if (derived.length) {
      derived.forEach((d: Record<string, unknown>) => {
        const idx = localParams.value.findIndex(p => p.name === d.name)
        if (idx >= 0) {
          localParams.value[idx].default_value_str = String(d.value ?? d.suggested ?? d.default_value)
        } else {
          localParams.value.push({ name: d.name, default_value_str: String(d.value ?? ''), description: d.description || 'AI 推导' })
        }
      })
      emitUpdate()
      Message.success(`已推导 ${derived.length} 个阈值`)
    } else {
      Message.info('未推导出新阈值')
    }
  } catch (e: any) { Message.error(e._message || '阈值推导失败') }
  finally { derivingThresholds.value = false }
}

// 应用调优建议
function applyTuning(suggestion: any) {
  const idx = localParams.value.findIndex(p => p.name === (suggestion.param || suggestion.name))
  if (idx >= 0) {
    localParams.value[idx].default_value_str = String(suggestion.suggested)
    emitUpdate()
    Message.success(`已应用: ${suggestion.param || suggestion.name} → ${suggestion.suggested}`)
  }
}

// §4.5 参数证据卡
async function loadParamEvidence(paramName: string) {
  if (!props.skillId || !paramName) return
  loadingEvidence.value = paramName
  try {
    paramEvidence.value = await skillApi.paramEvidence(props.skillId, paramName, 30)
  } catch (e: any) { Message.error(e._message || '加载证据失败') }
  finally { loadingEvidence.value = '' }
}

function applyEvidenceRecommendation() {
  if (!paramEvidence.value || paramEvidence.value.recommended == null) return
  const idx = localParams.value.findIndex(p => p.name === paramEvidence.value.param_name)
  if (idx >= 0) {
    localParams.value[idx].default_value_str = String(paramEvidence.value.recommended)
    emitUpdate()
    Message.success(`已应用: ${paramEvidence.value.param_name} → ${paramEvidence.value.recommended}`)
    paramEvidence.value = null
  }
}
</script>

<style scoped>
.block { padding: 4px 0; }
.block-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.block-title { font-size: 15px; font-weight: 700; color: var(--ai-ink-1); }
.block-empty { color: var(--ai-ink-4); font-size: 13px; padding: 20px 0; text-align: center; }

.param-table { border: 1px solid var(--ai-border); border-radius: 8px; overflow: hidden; }
.pt-head { display: flex; gap: 4px; padding: 8px 10px; background: var(--ai-surface-2); font-size: 11px; font-weight: 600; color: var(--ai-ink-3); }
.pt-row { display: flex; gap: 4px; padding: 4px 10px; align-items: center; border-top: 1px solid var(--ai-border); }
.pt-col.name { flex: 2; }
.pt-col.val { flex: 2; }
.pt-col.desc { flex: 3; }
.pt-col.act { width: 32px; flex-shrink: 0; }

/* 调优结果 */
.tuning-result {
  margin-top: 12px; padding: 12px; border: 1px solid var(--ai-border);
  border-radius: 8px; background: var(--ai-surface-2);
}
.tuning-title { font-size: 13px; font-weight: 600; color: var(--ai-ink-2); margin-bottom: 8px; }
.impact-summary { display: flex; gap: 6px; margin-bottom: 8px; }
.impact-details { font-size: 12px; }
.impact-item { display: flex; align-items: center; gap: 6px; padding: 3px 0; }
.impact-label { color: var(--ai-ink-3); min-width: 80px; }
.impact-old { color: var(--ai-ink-4); }
.impact-arrow { color: var(--ai-ink-4); }
.impact-new { font-weight: 500; }

.tuning-item { padding: 8px 0; border-bottom: 1px solid var(--ai-border); }
.tuning-param { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.tuning-change { color: var(--ai-ink-3); font-family: var(--ai-font-mono); }
.tuning-evidence { font-size: 11px; color: var(--ai-ink-4); margin: 4px 0; }

/* §4.5 参数证据卡 */
.evidence-card {
  margin-top: 12px; padding: 14px 16px; border-radius: 10px;
  background: var(--ai-surface-2); border: 1px solid var(--ai-border);
  box-shadow: var(--ai-shadow-1);
}
.ec-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.ec-title { font-size: 13px; font-weight: 600; color: var(--ai-ink-1); }
.ec-row {
  display: flex; align-items: center; gap: 10px; padding: 7px 0;
  font-size: 12px;
  /* webkit 1px dashed 渲染成 solid，用 background gradient pattern 代替 */
  background-image: linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%);
  background-position: bottom;
  background-size: 6px 1px;
  background-repeat: repeat-x;
}
.ec-row.highlight {
  background-image: none;
  background: var(--ai-accent-soft); padding: 8px 10px;
  border-radius: 6px; margin: 4px -10px;
}
.ec-label { color: var(--ai-ink-4); min-width: 80px; }
.ec-val { color: var(--ai-ink-1); font-weight: 500; }
.ec-apply { margin-left: auto; }
.ec-distribution { margin-top: 12px; }
.ec-dist-title { font-size: 11px; font-weight: 600; color: var(--ai-ink-3); margin-bottom: 6px; }
.ec-dist-bars { display: flex; gap: 2px; height: 28px; border-radius: 6px; overflow: hidden; }
.ec-bar {
  display: flex; align-items: center; justify-content: center;
  font-size: 10px; color: var(--ai-surface); font-weight: 600;
  min-width: 0;
}
.ec-bar-completed { background: var(--ai-ok); }
.ec-bar-rejected { background: var(--ai-bad); }
.ec-bar-pending { background: var(--ai-ink-4); }
.ec-evidence { margin-top: 12px; padding: 10px 12px; border-radius: 6px; background: var(--ai-surface); }
.ec-evidence-head { font-size: 11px; font-weight: 600; color: var(--ai-ink-3); margin-bottom: 4px; }
.ec-evidence-body { font-size: 12px; color: var(--ai-ink-2); line-height: 1.5; }
.ec-warning {
  margin-top: 8px; padding: 6px 10px; font-size: 11px;
  color: var(--ai-warn); display: flex; align-items: center; gap: 4px;
}
</style>
