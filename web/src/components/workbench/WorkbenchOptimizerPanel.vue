<template>
  <div class="opt-panel">
    <!-- 工具栏 -->
    <div class="opt-toolbar">
      <a-button size="mini" class="ai-btn" @click="showCreateModal = true">
        <template #icon><icon-plus :size="12" /></template>新建会话
      </a-button>
      <a-button size="mini" @click="loadSessions" :loading="loading">刷新</a-button>
      <span class="opt-spacer" />
      <span v-if="sessions.length" class="opt-count">{{ sessions.length }} 个会话</span>
    </div>

    <!-- 无会话或会话列表 + 详情 -->
    <div v-if="!activeSession" class="opt-list">
      <div v-if="!sessions.length && !loading" class="opt-empty">
        <icon-trophy :size="20" />
        <span>创建优化会话，AI 自动搜索最优参数组合</span>
      </div>
      <div v-for="s in sessions" :key="s.session_id" class="opt-session" @click="viewSession(s)">
        <div class="opt-session-top">
          <span class="opt-session-name">{{ s.name }}</span>
          <a-badge :status="statusBadge(s.status)" :text="statusLabel(s.status)" />
        </div>
        <div class="opt-session-meta">
          <span>{{ s.goal }}</span>
          <span>迭代 {{ s.current_iteration }}/{{ s.max_iterations }}</span>
          <span>{{ formatTime(s.created_at) }}</span>
        </div>
        <div v-if="s.status === 'failed' && s.failure_reason" class="opt-session-error">
          <icon-info-circle :size="12" />
          <span>{{ s.failure_reason }}</span>
        </div>
        <div class="opt-session-actions" @click.stop>
          <a-button v-if="s.status === 'created' || s.status === 'paused'" size="mini" class="ai-btn" @click="handleStart(s)" :loading="starting">启动</a-button>
          <a-button v-if="s.status === 'running'" size="mini" status="warning" @click="handlePause(s)">暂停</a-button>
        </div>
      </div>
    </div>

    <!-- 会话详情 -->
    <div v-else class="opt-detail">
      <div class="opt-detail-header">
        <a-button size="mini" @click="activeSession = null"><icon-left :size="12" /> 返回列表</a-button>
        <span class="opt-detail-name">{{ activeSession.name }}</span>
        <a-badge :status="statusBadge(activeSession.status)" :text="statusLabel(activeSession.status)" />
        <span class="opt-spacer" />
        <a-button size="mini" @click="loadReport" :loading="reportLoading">报告</a-button>
        <a-button v-if="activeSession.status === 'running'" size="mini" @click="pollLiveStatus">刷新</a-button>
      </div>

      <!-- 报告摘要 -->
      <div v-if="report" class="opt-report">
        <div class="opt-report-grid">
          <div class="rpt-item"><div class="rpt-val">{{ report.total_iterations }}</div><div class="rpt-key">迭代</div></div>
          <div class="rpt-item"><div class="rpt-val">{{ report.candidates_accepted }}</div><div class="rpt-key">接受</div></div>
          <div class="rpt-item"><div class="rpt-val">{{ report.candidates_rejected }}</div><div class="rpt-key">拒绝</div></div>
          <div class="rpt-item"><div class="rpt-val">{{ (report.total_tokens || 0).toLocaleString() }}</div><div class="rpt-key">Token</div></div>
        </div>
        <div v-if="report.best_improvement" class="rpt-best">
          最佳: +{{ (report.best_improvement.accuracy_gain * 100).toFixed(2) }}% — {{ report.best_improvement.diff_summary }}
        </div>
      </div>

      <!-- 迭代进度条 -->
      <div v-if="candidates.length" class="opt-iter-chart">
        <div class="iter-bars">
          <div v-for="c in candidates" :key="c.candidate_id"
            class="iter-bar" :class="c.decision"
            :title="`#${c.iteration} ${decisionLabel(c.decision)} ${c.benchmark_score ? (c.benchmark_score.accuracy * 100).toFixed(1) + '%' : ''}`"
            :style="{ height: c.benchmark_score ? Math.max(8, c.benchmark_score.accuracy * 100) + '%' : '8%' }"
          >
            <span class="iter-label">{{ c.iteration }}</span>
          </div>
        </div>
      </div>

      <!-- 候选列表 -->
      <div class="opt-candidates">
        <div v-for="c in candidates" :key="c.candidate_id" class="cand-card" :class="{ expanded: expandedCand === c.candidate_id }">
          <div class="cand-header" @click="toggleCandidate(c)">
            <span class="cand-iter">#{{ c.iteration }}</span>
            <a-badge :status="decisionBadge(c.decision)" :text="decisionLabel(c.decision)" />
            <span v-if="c.benchmark_score" class="cand-score">
              {{ (c.benchmark_score.accuracy * 100).toFixed(1) }}%
              <template v-if="activeSession.baseline_score">
                <span :class="c.benchmark_score.accuracy > activeSession.baseline_score.accuracy ? 'delta-up' : 'delta-down'">
                  {{ ((c.benchmark_score.accuracy - activeSession.baseline_score.accuracy) * 100).toFixed(2) }}%
                </span>
              </template>
            </span>
            <span class="opt-spacer" />
            <icon-down v-if="expandedCand !== c.candidate_id" :size="11" style="color:var(--ai-ink-4)" />
            <icon-up v-else :size="11" style="color:var(--ai-ink-4)" />
          </div>

          <template v-if="expandedCand === c.candidate_id && expandedDetail">
            <div v-if="expandedDetail.generation_rationale" class="cand-section">
              <div class="cand-label">AI 分析</div>
              <div class="cand-text">{{ expandedDetail.generation_rationale }}</div>
            </div>
            <div v-if="expandedDetail.changes?.length" class="cand-section">
              <div class="cand-label">变更</div>
              <div class="cand-changes">
                <div v-for="(ch, i) in expandedDetail.changes" :key="i" class="ch-row">
                  <a-tag size="small" color="arcoblue">{{ ch.step }}.{{ ch.field }}</a-tag>
                  <span class="ch-old">{{ ch.old }}</span>
                  <span class="ch-arrow">→</span>
                  <span class="ch-new">{{ ch.new }}</span>
                </div>
              </div>
            </div>
            <div v-if="expandedDetail.benchmark_score" class="cand-section">
              <div class="cand-label">Benchmark</div>
              <div class="bm-grid">
                <div v-for="(val, key) in expandedDetail.benchmark_score" :key="key" class="bm-item">
                  <div class="bm-key">{{ benchmarkLabels[key] || key }}</div>
                  <div class="bm-val">{{ typeof val === 'number' && val < 1 && String(key).includes('accuracy') ? (val * 100).toFixed(1) + '%' : val }}</div>
                </div>
              </div>
            </div>
            <div class="cand-actions">
              <a-button v-if="expandedDetail.decision === 'accepted'" size="mini" class="ai-btn" @click="handlePromote(expandedDetail)">晋升 Git</a-button>
              <a-button v-if="['pending','accepted'].includes(expandedDetail.decision)" size="mini" status="danger" @click="handleReject(expandedDetail)">拒绝</a-button>
            </div>
          </template>
        </div>
        <div v-if="!candidates.length && !candidatesLoading" class="opt-empty" style="padding:16px 0">暂无候选版本</div>
      </div>
    </div>

    <!-- 创建弹窗 -->
    <a-modal v-model:visible="showCreateModal" title="新建优化会话" @ok="handleCreate" :ok-loading="creating" :width="480">
      <a-form :model="createForm" layout="vertical" size="small">
        <a-form-item label="名称"><a-input v-model="createForm.name" placeholder="如：提高命中率-0404" /></a-form-item>
        <a-form-item label="目标">
          <a-select v-model="createForm.goal">
            <a-option value="提高任务正确率">提高任务正确率</a-option>
            <a-option value="降低误判率">降低误判率</a-option>
            <a-option value="降低成本">降低成本</a-option>
          </a-select>
        </a-form-item>
        <a-form-item label="可编辑区域">
          <a-checkbox-group v-model="createForm.editable_zones">
            <a-checkbox value="branch_conditions">分支条件</a-checkbox>
            <a-checkbox value="thresholds">阈值参数</a-checkbox>
            <a-checkbox value="branch_order">分支顺序</a-checkbox>
            <a-checkbox value="antipatterns">反例</a-checkbox>
          </a-checkbox-group>
        </a-form-item>
        <a-row :gutter="8">
          <a-col :span="8"><a-form-item label="最大迭代"><a-input-number v-model="createForm.max_iterations" :min="1" :max="100" style="width:100%" /></a-form-item></a-col>
          <a-col :span="8"><a-form-item label="Token 预算"><a-input-number v-model="createForm.max_tokens" :min="10000" :step="50000" style="width:100%" /></a-form-item></a-col>
          <a-col :span="8"><a-form-item label="沙箱次数"><a-input-number v-model="createForm.max_sandbox_runs" :min="10" :step="10" style="width:100%" /></a-form-item></a-col>
        </a-row>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconPlus, IconLeft, IconDown, IconUp, IconTrophy, IconInfoCircle } from '@arco-design/web-vue/es/icon'
import { optimizerApi as rawOptimizerApi } from '@/api'
import { formatTime as formatBjtTime } from '@/utils/format'

const props = defineProps({
  skillId: { type: String, required: true },
})
const optimizerApi: any = rawOptimizerApi

const sessions = ref<any[]>([])
const loading = ref(false)
const starting = ref(false)
const activeSession = ref<any>(null)
const candidates = ref<any[]>([])
const candidatesLoading = ref(false)
const expandedCand = ref<string | null>(null)
const expandedDetail = ref<any>(null)
const report = ref<any>(null)
const reportLoading = ref(false)
const showCreateModal = ref(false)
const creating = ref(false)
const createForm = reactive({
  name: '', goal: '提高任务正确率',
  editable_zones: ['branch_conditions', 'thresholds'],
  max_iterations: 20, max_tokens: 500000, max_sandbox_runs: 100,
})

const benchmarkLabels: Record<string, string> = { accuracy: '准确率', passed: '通过', failed: '失败', total: '总数', regression_count: '回归', weighted_accuracy: '加权' }

function statusBadge(s: string) { return ({ created: 'default', running: 'processing', paused: 'warning', completed: 'success', failed: 'error' } as Record<string, string>)[s] || 'default' }
function statusLabel(s: string) { return ({ created: '待启动', running: '运行中', paused: '已暂停', completed: '已完成', failed: '失败' } as Record<string, string>)[s] || s }
function decisionBadge(d: string) { return ({ pending: 'default', accepted: 'success', rejected: 'error', promoted: 'success' } as Record<string, string>)[d] || 'default' }
function decisionLabel(d: string) { return ({ pending: '待定', accepted: '已接受', rejected: '已拒绝', promoted: '已晋升' } as Record<string, string>)[d] || d }
function formatTime(iso: string) { return iso ? formatBjtTime(iso) : '-' }

async function loadSessions() {
  if (!props.skillId) return
  loading.value = true
  try { sessions.value = await optimizerApi.listSessions(props.skillId) } catch { /* 静默 */ }
  finally { loading.value = false }
}

async function viewSession(s: any) {
  activeSession.value = s; report.value = null
  candidatesLoading.value = true
  try {
    activeSession.value = await optimizerApi.getSession(s.session_id)
    candidates.value = await optimizerApi.listCandidates(s.session_id)
  } catch (e: any) { Message.error(e._message || '加载详情失败') }
  finally { candidatesLoading.value = false }
}

async function handleCreate() {
  if (!createForm.name) { Message.warning('请输入名称'); return }
  creating.value = true
  try {
    await optimizerApi.createSession(props.skillId, createForm)
    Message.success('创建成功'); showCreateModal.value = false; createForm.name = ''
    await loadSessions()
  } catch (e: any) { Message.error(e._message || '创建失败') }
  finally { creating.value = false }
}

async function handleStart(s: any) {
  starting.value = true
  try { await optimizerApi.startSession(s.session_id); Message.success('已启动'); await loadSessions() }
  catch (e: any) { Message.error(e._message || '启动失败') }
  finally { starting.value = false }
}

async function handlePause(s: any) {
  try { await optimizerApi.pauseSession(s.session_id); Message.success('已暂停'); await loadSessions() }
  catch (e: any) { Message.error(e._message || '暂停失败') }
}

async function toggleCandidate(c: any) {
  if (expandedCand.value === c.candidate_id) { expandedCand.value = null; expandedDetail.value = null; return }
  expandedCand.value = c.candidate_id
  try { expandedDetail.value = await optimizerApi.getCandidate(c.candidate_id) }
  catch { expandedDetail.value = c }
}

async function loadReport() {
  if (!activeSession.value) return
  reportLoading.value = true
  try { report.value = await optimizerApi.getReport(activeSession.value.session_id) }
  catch (e: any) { Message.error(e._message || '加载报告失败') }
  finally { reportLoading.value = false }
}

async function pollLiveStatus() {
  if (!activeSession.value) return
  try {
    const d = await optimizerApi.getLiveStatus(activeSession.value.session_id)
    if (d.iteration) {
      activeSession.value.current_iteration = d.iteration
      Message.info(`迭代 ${d.iteration}/${d.max_iterations}`)
    }
  } catch { /* ignore */ }
}

async function handlePromote(c: unknown) {
  try { await optimizerApi.promoteCandidate((c as Record<string, unknown>).candidate_id); Message.success('已晋升'); await viewSession(activeSession.value) }
  catch (e) { Message.error(String((e as Record<string, unknown>)?._message || '晋升失败')) }
}

async function handleReject(c: unknown) {
  try { await optimizerApi.rejectCandidate((c as Record<string, unknown>).candidate_id, '人工拒绝'); Message.success('已拒绝'); await viewSession(activeSession.value) }
  catch (e) { Message.error(String((e as Record<string, unknown>)?._message || '拒绝失败')) }
}

onMounted(loadSessions)
</script>

<style scoped>
.opt-panel { height: 100%; display: flex; flex-direction: column; overflow: hidden; }

.opt-toolbar { display: flex; align-items: center; gap: 6px; padding: 8px 12px; flex-shrink: 0; border-bottom: 1px solid var(--ai-border); }
.opt-spacer { flex: 1; }
.opt-count { font-size: 11px; color: var(--ai-ink-3); font-weight: 700; }
.opt-empty { display: flex; flex-direction: column; align-items: center; gap: 6px; color: var(--ai-ink-3); font-size: 12px; padding: 24px 0; font-weight: 700; }

/* 会话列表 */
.opt-list { flex: 1; overflow-y: auto; padding: 8px 12px; }
.opt-session {
  padding: 10px 12px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  margin-bottom: 6px;
  cursor: pointer;
  transition: all var(--sf-transition);
  background: var(--ai-surface);
}
.opt-session:hover {
  border-color: var(--ai-warn);
  box-shadow: 0 4px 12px var(--ai-surface-2);
  transform: translateY(-1px);
}
.opt-session-top { display: flex; align-items: center; gap: 8px; }
.opt-session-name { font-size: 13px; font-weight: 800; color: var(--ai-ink-1); }
.opt-session-meta { display: flex; gap: 12px; font-size: 11px; color: var(--ai-ink-3); margin-top: 4px; font-weight: 700; }
.opt-session-error {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  margin-top: 6px;
  padding: 6px 8px;
  border-radius: var(--sf-radius-xs);
  background: rgba(191, 63, 63, 0.10);
  border: 1px solid rgba(191, 63, 63, 0.22);
  color: var(--ai-bad);
  font-size: 11px;
  line-height: 1.4;
  font-weight: 700;
}
.opt-session-error .arco-icon { flex-shrink: 0; margin-top: 1px; }
.opt-session-actions { margin-top: 6px; }

/* 详情 */
.opt-detail { flex: 1; overflow-y: auto; padding: 8px 12px; }
.opt-detail-header { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.opt-detail-name { font-size: 13px; font-weight: 800; color: var(--ai-ink-1); }

/* 报告 */
.opt-report { margin-bottom: 12px; }
.opt-report-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; }
.rpt-item {
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: 4px; padding: 8px; text-align: center;
}
.rpt-val { font-size: 16px; font-weight: 800; color: var(--ai-ink-1); }
.rpt-key { font-size: 10px; color: var(--ai-ink-3); margin-top: 1px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.4px; }
.rpt-best {
  margin-top: 6px; padding: 6px 10px;
  border-radius: var(--sf-radius-xs);
  background: rgba(15, 143, 111, 0.12);
  border: 1px solid rgba(15, 143, 111, 0.22);
  color: var(--ai-ok);
  font-size: 12px; font-weight: 700;
}

/* 迭代图 */
.opt-iter-chart { margin-bottom: 10px; }
.iter-bars { display: flex; align-items: flex-end; gap: 2px; height: 50px; border-bottom: 1px solid var(--ai-border); padding-bottom: 2px; }
.iter-bar { flex: 1; min-width: 10px; border-radius: var(--sf-radius-xs) var(--sf-radius-xs) 0 0; display: flex; align-items: flex-end; justify-content: center; }
.iter-bar.accepted { background: var(--sf-success-solid); }
.iter-bar.rejected { background: var(--sf-danger-solid); }
.iter-bar.pending { background: var(--ai-border-2); }
.iter-bar.promoted { background: var(--sf-warning-solid); }
.iter-label { font-size: 9px; color: #fff; font-weight: 800; }
.iter-bar.pending .iter-label { color: var(--ai-ink-1); }

/* 候选 */
.opt-candidates { }
.cand-card {
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  padding: 8px 10px;
  margin-bottom: 4px;
  background: var(--ai-surface);
  transition: all var(--sf-transition);
}
.cand-card.expanded {
  border-color: var(--ai-warn);
  background: var(--ai-surface);
}
.cand-header { display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 12px; }
.cand-iter { font-weight: 800; color: var(--ai-ink-1); }
.cand-score { font-weight: 800; color: var(--ai-ok); }
.delta-up { color: var(--ai-ok); font-weight: 800; } .delta-up::before { content: '↑'; }
.delta-down { color: var(--ai-bad); font-weight: 800; } .delta-down::before { content: '↓'; }

.cand-section { margin-top: 8px; }
.cand-label { font-size: 11px; font-weight: 800; color: var(--ai-ink-3); margin-bottom: 3px; text-transform: uppercase; letter-spacing: 0.3px; }
.cand-text { font-size: 12px; color: var(--ai-ink-2); line-height: 1.5; font-weight: 600; }
.cand-changes { display: flex; flex-direction: column; gap: 3px; }
.ch-row { display: flex; align-items: center; gap: 6px; font-size: 11px; font-family: var(--ai-font-mono); }
.ch-old { color: var(--ai-bad); text-decoration: line-through; font-weight: 700; }
.ch-arrow { color: var(--ai-ink-3); }
.ch-new { color: var(--ai-ok); font-weight: 800; }
.bm-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px; }
.bm-item {
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: var(--sf-radius-xs);
  padding: 4px 6px; text-align: center;
}
.bm-key { font-size: 10px; color: var(--ai-ink-3); font-weight: 800; text-transform: uppercase; }
.bm-val { font-size: 13px; font-weight: 800; color: var(--ai-ink-1); }
.cand-actions { display: flex; gap: 6px; margin-top: 8px; padding-top: 6px; border-top: 1px solid var(--ai-border); }
</style>
