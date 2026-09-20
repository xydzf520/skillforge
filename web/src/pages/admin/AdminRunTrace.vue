<template>
  <div class="page-container page-wide admin-run-trace-page">
    <div class="page-header">
      <div>
        <div class="page-kicker">管理后台 · 数据与服务</div>
        <h2 class="page-title">Run Trace</h2>
        <p class="page-subtitle">按 run_id 追溯采集、AI 分析、成本与决策日志</p>
      </div>
      <a-space>
        <a-button
          class="ai-btn-like"
          :loading="trainingCandidateLoading"
          :disabled="!currentRunId || !traceSkillId"
          @click="createTrainingCandidate"
        >
          <template #icon><icon-experiment /></template>
          训练候选
        </a-button>
        <a-button class="ai-btn-like" :loading="analysisLoading" :disabled="!currentRunId" @click="analyzeTrace">
          <template #icon><icon-robot /></template>
          AI 复盘
        </a-button>
        <a-button class="ai-btn-like primary" type="primary" :loading="loading" :disabled="!searchRunId.trim()" @click="loadTrace">
          查询
        </a-button>
      </a-space>
    </div>

    <a-card class="page-list-card trace-search-card">
      <div class="trace-search">
        <a-input
          v-model="searchRunId"
          placeholder="输入 run_id"
          allow-clear
          @press-enter="loadTrace"
        />
        <a-button class="ai-btn-like" :loading="loading" :disabled="!searchRunId.trim()" @click="loadTrace">查询</a-button>
      </div>
      <a-alert v-if="error" type="warning" show-icon class="trace-alert">
        {{ error }}
      </a-alert>
    </a-card>

    <SfEmptyState v-if="!trace && !loading" title="Run Trace" description="输入 run_id 后查看完整追溯链路" hint="可追踪采集 Proof、AI 分析、成本和决策日志。" icon="search" />

    <a-spin v-else :loading="loading">
      <template v-if="trace">
        <div class="summary-grid">
          <div class="summary-tile">
            <span>Run</span>
            <strong class="mono-id">{{ trace.run_id }}</strong>
          </div>
          <div class="summary-tile">
            <span>Skill</span>
            <strong class="mono-id">{{ stringValue(trace.execution_run?.skill_id) }}</strong>
          </div>
          <div class="summary-tile">
            <span>状态</span>
            <strong>
              <span class="ai-pill" :class="statusPillTone(trace.execution_run?.status)">
                {{ stringValue(trace.execution_run?.status) }}
              </span>
            </strong>
          </div>
          <div class="summary-tile">
            <span>采集 Proof</span>
            <strong class="mono-id">{{ collectionRows.length }}</strong>
          </div>
          <div class="summary-tile">
            <span>AI 调用</span>
            <strong class="mono-id">{{ analyzeRows.length }}</strong>
          </div>
          <div class="summary-tile">
            <span>Usage Logs</span>
            <strong class="mono-id">{{ usageRows.length }}</strong>
          </div>
        </div>

        <a-card v-if="runAnalysis || analysisLoading || analysisError" class="page-list-card trace-section" title="AI 运行复盘">
          <a-spin :loading="analysisLoading">
            <a-alert v-if="analysisError" type="warning" show-icon class="trace-alert">
              {{ analysisError }}
            </a-alert>
            <template v-if="runAnalysis">
              <div class="analysis-meta">
                <a-tag color="arcoblue">{{ stringValue(runAnalysis.model) }}</a-tag>
                <a-tag>1M 上下文</a-tag>
                <a-tag>steps {{ rawCount('execution_steps') }}</a-tag>
                <a-tag>decisions {{ rawCount('decision_logs') }}</a-tag>
                <a-tag>proofs {{ rawCount('collection_proofs') }}</a-tag>
              </div>
              <pre class="analysis-output code-block">{{ analysisText }}</pre>
              <a-collapse v-if="runAnalysis.raw_data" class="analysis-raw">
                <a-collapse-item header="脱敏原始上下文" key="raw">
                  <pre class="code-block">{{ formatJson(runAnalysis.raw_data) }}</pre>
                </a-collapse-item>
              </a-collapse>
            </template>
          </a-spin>
        </a-card>

        <a-card class="page-list-card trace-section" title="ExecutionRun">
          <div class="kv-grid">
            <div><span>trigger_type</span><strong class="mono-id">{{ stringValue(trace.execution_run?.trigger_type) }}</strong></div>
            <div><span>run_mode</span><strong class="mono-id">{{ stringValue(trace.execution_run?.run_mode) }}</strong></div>
            <div><span>instance</span><strong class="mono-id">{{ stringValue(trace.execution_run?.source_instance_id) }}</strong></div>
            <div><span>started_at</span><strong class="mono-id">{{ stringValue(trace.execution_run?.started_at) }}</strong></div>
            <div><span>completed_at</span><strong class="mono-id">{{ stringValue(trace.execution_run?.completed_at) }}</strong></div>
          </div>
        </a-card>

        <a-card class="page-list-card trace-section" title="Collection Proofs">
          <a-table class="page-list-table" :data="collectionRows" :pagination="{ pageSize: 12 }" row-key="proof_id" size="small">
            <template #columns>
              <a-table-column title="proof_id" data-index="proof_id" :width="170">
                <template #cell="{ record }">
                  <span class="cell-mono">{{ record.proof_id }}</span>
                </template>
              </a-table-column>
              <a-table-column title="tool" data-index="tool_name" :width="220">
                <template #cell="{ record }">
                  <span class="cell-mono">{{ record.tool_name }}</span>
                </template>
              </a-table-column>
              <a-table-column title="scope" data-index="data_scope" :width="170" />
              <a-table-column title="status" data-index="status" :width="100" />
              <a-table-column title="credential_alias" data-index="credential_alias" :width="180">
                <template #cell="{ record }">
                  <span class="cell-mono">{{ record.credential_alias }}</span>
                </template>
              </a-table-column>
              <a-table-column title="rows" data-index="row_count" :width="80" align="right">
                <template #cell="{ record }">
                  <span class="cell-mono">{{ record.row_count }}</span>
                </template>
              </a-table-column>
              <a-table-column title="created_at" data-index="created_at" :width="180">
                <template #cell="{ record }">
                  <span class="cell-mono">{{ record.created_at }}</span>
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-card>

        <a-card class="page-list-card trace-section" title="Intelligence Analyze Runs">
          <a-table class="page-list-table" :data="analyzeRows" :pagination="{ pageSize: 10 }" row-key="id" size="small">
            <template #columns>
              <a-table-column title="cache_key" data-index="cache_key" :width="180">
                <template #cell="{ record }">
                  <span class="cell-mono">{{ record.cache_key }}</span>
                </template>
              </a-table-column>
              <a-table-column title="model" data-index="model" :width="150">
                <template #cell="{ record }">
                  <span class="cell-mono">{{ record.model }}</span>
                </template>
              </a-table-column>
              <a-table-column title="backend" :width="120">
                <template #cell="{ record }">
                  <a-tag :color="analysisBackendColor(record)" size="small">
                    {{ analysisBackendLabel(record) }}
                  </a-tag>
                </template>
              </a-table-column>
              <a-table-column title="Agent" :width="160">
                <template #cell="{ record }">
                  <span class="cell-mono">{{ analysisAgentLabel(record) }}</span>
                </template>
              </a-table-column>
              <a-table-column title="route" :width="150">
                <template #cell="{ record }">
                  {{ analysisRouteLabel(record) }}
                </template>
              </a-table-column>
              <a-table-column title="cache_hit" data-index="cache_hit" :width="110">
                <template #cell="{ record }">
                  <a-tag :color="record.cache_hit ? 'green' : 'gray'" size="small">
                    {{ record.cache_hit ? 'true' : 'false' }}
                  </a-tag>
                </template>
              </a-table-column>
              <a-table-column title="cache_id" data-index="cache_id" :width="100">
                <template #cell="{ record }">
                  <span class="cell-mono">{{ record.cache_id }}</span>
                </template>
              </a-table-column>
              <a-table-column title="hit_of" data-index="cache_hit_of_run_id" :width="160">
                <template #cell="{ record }">
                  <span class="cell-mono">{{ record.cache_hit_of_run_id }}</span>
                </template>
              </a-table-column>
              <a-table-column title="tokens" :width="110" align="right">
                <template #cell="{ record }"><span class="cell-mono">{{ record.total_tokens ?? '-' }}</span></template>
              </a-table-column>
              <a-table-column title="cost" :width="100" align="right">
                <template #cell="{ record }"><span class="cell-mono">{{ money(record.cost_usd) }}</span></template>
              </a-table-column>
              <a-table-column title="commit" data-index="skill_git_commit_full" :width="130">
                <template #cell="{ record }"><span class="cell-mono">{{ shortHash(record.skill_git_commit_full) }}</span></template>
              </a-table-column>
              <a-table-column title="degraded" :width="110">
                <template #cell="{ record }">
                  <a-tag :color="record.degraded ? 'orange' : 'green'" size="small">
                    {{ record.degraded ? 'true' : 'false' }}
                  </a-tag>
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-card>

        <a-card class="page-list-card trace-section" title="Usage Logs">
          <a-table class="page-list-table" :data="usageRows" :pagination="{ pageSize: 10 }" row-key="id" size="small">
            <template #columns>
              <a-table-column title="ts" data-index="ts" :width="180">
                <template #cell="{ record }"><span class="cell-mono">{{ record.ts }}</span></template>
              </a-table-column>
              <a-table-column title="source" data-index="call_source" :width="150" />
              <a-table-column title="model" data-index="model" :width="150">
                <template #cell="{ record }"><span class="cell-mono">{{ record.model }}</span></template>
              </a-table-column>
              <a-table-column title="input" data-index="input_tokens" :width="90" align="right">
                <template #cell="{ record }"><span class="cell-mono">{{ record.input_tokens }}</span></template>
              </a-table-column>
              <a-table-column title="output" data-index="output_tokens" :width="90" align="right">
                <template #cell="{ record }"><span class="cell-mono">{{ record.output_tokens }}</span></template>
              </a-table-column>
              <a-table-column title="cost" :width="100" align="right">
                <template #cell="{ record }"><span class="cell-mono">{{ money(record.cost_usd) }}</span></template>
              </a-table-column>
              <a-table-column title="metadata_json">
                <template #cell="{ record }">
                  <code class="inline-code">{{ compactJson(record.metadata_json) }}</code>
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-card>

        <a-card class="page-list-card trace-section" title="Platform Cookie Audit">
          <a-table class="page-list-table" :data="cookieRows" :pagination="{ pageSize: 10 }" row-key="id" size="small">
            <template #columns>
              <a-table-column title="created_at" data-index="created_at" :width="180">
                <template #cell="{ record }"><span class="cell-mono">{{ record.created_at }}</span></template>
              </a-table-column>
              <a-table-column title="platform" data-index="platform" :width="110" />
              <a-table-column title="shop_id" data-index="shop_id" :width="140">
                <template #cell="{ record }"><span class="cell-mono">{{ record.shop_id }}</span></template>
              </a-table-column>
              <a-table-column title="action" data-index="action" :width="160" />
              <a-table-column title="auth_source" data-index="auth_source" :width="130" />
              <a-table-column title="detail">
                <template #cell="{ record }">
                  <code class="inline-code">{{ compactJson(record.detail) }}</code>
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-card>

        <a-card class="page-list-card trace-section" title="Decision Log">
          <SfEmptyState v-if="!decisionRows.length" title="决策日志" description="暂无决策日志" hint="该 run 尚未写入系统决策记录。" icon="archive" />
          <div v-else class="decision-list">
            <div v-for="row in decisionRows" :key="row.id || row.created_at" class="decision-item">
              <div class="decision-head">
                <strong>{{ stringValue(row.suggested_action || row.approval_status || row.id) }}</strong>
                <span class="cell-mono">{{ stringValue(row.created_at) }}</span>
              </div>
              <pre class="code-block">{{ formatJson(row.output_result) }}</pre>
            </div>
          </div>
        </a-card>
      </template>
    </a-spin>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { IconExperiment, IconRobot } from '@arco-design/web-vue/es/icon'
import {
  runTraceApi,
  trainingApi,
  type CollectionProofRow,
  type IntelligenceAnalyzeRunRow,
  type RunTraceAnalyzeResponse,
  type RunTraceResponse,
} from '@/api'
import { SfEmptyState } from '@/components/common'

const route = useRoute()
const router = useRouter()

const searchRunId = ref('')
const loading = ref(false)
const analysisLoading = ref(false)
const trainingCandidateLoading = ref(false)
const error = ref('')
const analysisError = ref('')
const trace = ref<RunTraceResponse | null>(null)
const runAnalysis = ref<RunTraceAnalyzeResponse | null>(null)

const collectionRows = computed<CollectionProofRow[]>(() => trace.value?.collection_proofs || [])
const analyzeRows = computed<IntelligenceAnalyzeRunRow[]>(() => trace.value?.intelligence_analyze_runs || [])
const usageRows = computed<any[]>(() => (trace.value?.usage_logs || []) as any[])
const cookieRows = computed<any[]>(() => (trace.value?.platform_cookie_audit || []) as any[])
const decisionRows = computed<any[]>(() => {
  const value = trace.value?.decision_log
  if (!value) return []
  return Array.isArray(value) ? value : [value]
})
const currentRunId = computed(() => (trace.value?.run_id || searchRunId.value || routeRunId()).trim())
const traceSkillId = computed(() => stringValue(trace.value?.execution_run?.skill_id) === '-' ? '' : stringValue(trace.value?.execution_run?.skill_id))
const analysisText = computed(() => {
  const value = runAnalysis.value?.analysis
  if (value === null || value === undefined) return ''
  return typeof value === 'string' ? value : JSON.stringify(redact(value), null, 2)
})

function routeRunId() {
  const raw = route.params.runId
  return String(Array.isArray(raw) ? raw[0] || '' : raw || '')
}

function stringValue(value: unknown) {
  if (value === null || value === undefined || value === '') return '-'
  return String(value)
}

function shortHash(value: unknown) {
  const text = stringValue(value)
  return text === '-' ? '-' : text.slice(0, 12)
}

function money(value: unknown) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '-'
  return `$${n.toFixed(6)}`
}

function statusPillTone(status: unknown): string {
  const s = String(status || '').toLowerCase()
  if (['success', 'succeeded', 'ok', 'completed', 'done'].includes(s)) return 'ok'
  if (['failed', 'error', 'errored'].includes(s)) return 'bad'
  if (['warning', 'partial', 'skipped'].includes(s)) return 'warn'
  if (['running', 'pending', 'queued'].includes(s)) return 'info'
  return ''
}

function analysisBackendLabel(record: IntelligenceAnalyzeRunRow) {
  if (record.analysis_backend === 'agent') return 'Agent'
  if (record.analysis_backend === 'platform') return '平台 AI'
  return record.analysis_backend || '-'
}

function analysisBackendColor(record: IntelligenceAnalyzeRunRow) {
  if (record.analysis_backend === 'agent') return 'green'
  if (record.analysis_backend === 'platform') return 'arcoblue'
  return 'gray'
}

function analysisAgentLabel(record: IntelligenceAnalyzeRunRow) {
  return record.analysis_agent_id || '-'
}

function analysisRouteLabel(record: IntelligenceAnalyzeRunRow) {
  const route = record.analysis_delegate_route || {}
  const scope = String(route.scope || '')
  if (scope === 'department') return '部门 Agent'
  if (scope === 'platform_fallback') return '平台兜底'
  if (scope === 'execution_instance') return '执行节点'
  if (scope === 'platform_llm') return '平台 LLM'
  return scope || '-'
}

function redact(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redact)
  if (!value || typeof value !== 'object') return value
  const blocked = new Set(['cookie_pool_id', 'owner_user_id', 'cookie', 'cookies', 'egress_ip', 'ip'])
  const out: Record<string, unknown> = {}
  Object.entries(value as Record<string, unknown>).forEach(([key, item]) => {
    out[key] = blocked.has(key) ? '[redacted]' : redact(item)
  })
  return out
}

function compactJson(value: unknown) {
  if (!value || (typeof value === 'object' && !Object.keys(value as Record<string, unknown>).length)) return '{}'
  return JSON.stringify(redact(value))
}

function formatJson(value: unknown) {
  if (!value) return '{}'
  return JSON.stringify(redact(value), null, 2)
}

function rawCount(key: string) {
  return Number(runAnalysis.value?.raw_counts?.[key] || 0)
}

async function loadTrace() {
  const runId = searchRunId.value.trim()
  if (!runId) return
  loading.value = true
  error.value = ''
  analysisError.value = ''
  trace.value = null
  runAnalysis.value = null
  try {
    trace.value = await runTraceApi.get(runId)
    if (route.name === 'AdminRunTraceSearch' || routeRunId() !== runId) {
      router.replace({ name: 'AdminRunTrace', params: { runId } })
    }
  } catch (e: any) {
    try {
      trace.value = await runTraceApi.getExecutionTrace(runId)
    } catch {
      error.value = e?._message || 'Run Trace 接口未就绪或无权查看'
      Message.warning(error.value)
    }
  } finally {
    loading.value = false
  }
}

async function analyzeTrace() {
  const runId = currentRunId.value
  if (!runId) return
  analysisLoading.value = true
  analysisError.value = ''
  runAnalysis.value = null
  try {
    runAnalysis.value = await runTraceApi.analyze(runId, {
      include_raw: true,
      max_output_tokens: 4096,
      step_limit: 20,
      decision_limit: 50,
      proof_limit: 50,
      snapshot_limit: 20,
    })
    Message.success('AI 复盘已生成')
  } catch (e: any) {
    try {
      runAnalysis.value = await runTraceApi.analyzeExecutionTrace(runId, {
        include_raw: true,
        max_output_tokens: 4096,
        step_limit: 20,
        decision_limit: 50,
        proof_limit: 50,
        snapshot_limit: 20,
      })
      Message.success('AI 复盘已生成')
    } catch {
      analysisError.value = e?._message || 'AI 复盘失败或无权分析该运行'
      Message.warning(analysisError.value)
    }
  } finally {
    analysisLoading.value = false
  }
}

async function createTrainingCandidate() {
  const runId = currentRunId.value
  const skillId = traceSkillId.value
  if (!runId || !skillId) return
  trainingCandidateLoading.value = true
  try {
    if (!runAnalysis.value) {
      await analyzeTrace()
    }
    if (!runAnalysis.value) {
      Message.warning('先完成 AI 复盘后再生成训练候选')
      return
    }
    const result: any = await trainingApi.createRunCandidate(runId, {
      model_family: `${skillId}:run_trace_improvement`,
      analysis_summary: analysisText.value.slice(0, 4000),
      analysis_model: runAnalysis.value.model,
      analysis_prompt_hash: runAnalysis.value.prompt_hash,
      raw_counts: runAnalysis.value.raw_counts || {},
    })
    Message.success('训练候选已创建')
    if (result?.id) {
      router.push({ name: 'TrainingJobDetail', params: { id: result.id } })
    }
  } catch (e: any) {
    Message.warning(e?._message || '训练候选创建失败或无权编辑该 Skill')
  } finally {
    trainingCandidateLoading.value = false
  }
}

watch(
  () => route.params.runId,
  () => {
    const runId = routeRunId()
    if (runId && runId !== searchRunId.value) {
      searchRunId.value = runId
      loadTrace()
    }
  },
)

onMounted(() => {
  const runId = routeRunId()
  if (runId) {
    searchRunId.value = runId
    loadTrace()
  }
})
</script>

<style scoped>
/* ────────── 页面 chrome —— 对齐 AdminUsers / AdminAudit ────────── */
.admin-run-trace-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.admin-run-trace-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  margin: 0;
}
.admin-run-trace-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.admin-run-trace-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}

/* ────────── 顶部按钮统一外形（30px / 6px / 12.5px） ────────── */
.admin-run-trace-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary)) {
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 500;
  box-shadow: none;
}
.admin-run-trace-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary):hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
.admin-run-trace-page :deep(.arco-btn-primary.ai-btn-like) {
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
  color: var(--ai-surface);
  font-size: 12.5px;
  font-weight: 500;
  box-shadow: none;
}
.admin-run-trace-page :deep(.arco-btn-primary.ai-btn-like:hover) {
  background: var(--ai-ink-2);
  border-color: var(--ai-ink-2);
}

/* ────────── 卡片标题（a-card title） ────────── */
.admin-run-trace-page :deep(.page-list-card .arco-card-header) {
  border-bottom: 1px solid var(--ai-border);
  padding: 12px 16px;
}
.admin-run-trace-page :deep(.page-list-card .arco-card-header-title) {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}
.admin-run-trace-page :deep(.page-list-card .arco-card-body) {
  padding: 16px;
}

/* ────────── 搜索条 —— flatten 到 ai-input 风格 ────────── */
.trace-search-card {
  margin-bottom: 16px;
}
.trace-search {
  display: grid;
  grid-template-columns: minmax(260px, 520px) auto;
  gap: 10px;
  align-items: center;
}
.admin-run-trace-page :deep(.trace-search .arco-input-wrapper) {
  height: 30px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface);
}
.admin-run-trace-page :deep(.trace-search .arco-input-wrapper:hover) {
  border-color: var(--ai-border-2);
}
.admin-run-trace-page :deep(.trace-search .arco-input-wrapper:focus-within) {
  border-color: var(--ai-ink-3);
}
.admin-run-trace-page :deep(.trace-search .arco-input) {
  font-size: 12.5px;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--ai-ink-1);
  background: transparent;
}

.trace-alert {
  margin-top: 12px;
}

/* ────────── 摘要 tiles —— ai-surface + ai-border + ai-radius ────────── */
.summary-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}
.summary-tile {
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  padding: 12px 14px;
  min-width: 0;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.summary-tile:hover {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
.summary-tile span {
  display: block;
  color: var(--ai-ink-4);
  font-size: 10.5px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.summary-tile strong {
  display: block;
  margin-top: 6px;
  overflow: hidden;
  color: var(--ai-ink-1);
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.01em;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.summary-tile strong.mono-id {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 13px;
}

.trace-section {
  margin-bottom: 16px;
}

/* ────────── AI 复盘 meta tags + 输出 ────────── */
.analysis-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}
.analysis-output {
  max-height: 520px;
}
.analysis-raw {
  margin-top: 12px;
}
.admin-run-trace-page :deep(.analysis-raw.arco-collapse) {
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface);
}
.admin-run-trace-page :deep(.analysis-raw .arco-collapse-item-header) {
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 500;
  border-bottom: 1px solid var(--ai-border);
}
.admin-run-trace-page :deep(.analysis-raw .arco-collapse-item-content-box) {
  background: var(--ai-surface);
}

/* ────────── KV grid ────────── */
.kv-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
}
.kv-grid > div {
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  padding: 10px 12px;
  min-width: 0;
}
.kv-grid span {
  display: block;
  color: var(--ai-ink-4);
  font-size: 10.5px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.kv-grid strong {
  display: block;
  margin-top: 6px;
  overflow: hidden;
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.kv-grid strong.mono-id {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

/* ────────── 决策日志 ────────── */
.decision-list {
  display: grid;
  gap: 10px;
}
.decision-item {
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  padding: 12px 14px;
  border-left: 2px solid var(--ai-ink-1);
}
.decision-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}
.decision-head strong {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ai-ink-1);
}
.decision-head .cell-mono {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 11.5px;
  color: var(--ai-ink-4);
}

/* ────────── 表头 / 单元格 ────────── */
.admin-run-trace-page :deep(.arco-table-th) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 8px 10px !important;
}
.admin-run-trace-page :deep(.arco-table-th .arco-table-th-title) {
  color: var(--ai-ink-4);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.admin-run-trace-page :deep(.arco-table-td) {
  font-size: 12.5px !important;
  color: var(--ai-ink-1) !important;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 10px 10px !important;
  background: var(--ai-surface) !important;
}
.admin-run-trace-page :deep(.arco-table-tr:hover .arco-table-td),
.admin-run-trace-page :deep(.arco-table-tr-hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

/* ────────── a-tag → ai-pill 风格映射 ────────── */
.admin-run-trace-page :deep(.arco-tag.arco-tag-size-small),
.admin-run-trace-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
}
.admin-run-trace-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft) !important;
  color: var(--ai-info) !important;
  border-color: transparent !important;
}
.admin-run-trace-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft) !important;
  color: var(--ai-ok) !important;
  border-color: transparent !important;
}
.admin-run-trace-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft) !important;
  color: var(--ai-bad) !important;
  border-color: transparent !important;
}
.admin-run-trace-page :deep(.arco-tag-color-orange),
.admin-run-trace-page :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft) !important;
  color: var(--ai-warn) !important;
  border-color: transparent !important;
}
.admin-run-trace-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-2) !important;
  border-color: transparent !important;
}

/* ────────── mono 单元格（run_id / trace_id / commit / 时间 / 数值） ────────── */
.cell-mono {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  color: var(--ai-ink-1);
}
.mono-id {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

/* ────────── JSON / 代码块 ────────── */
pre.code-block {
  margin: 0;
  padding: 12px 14px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-1);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 420px;
  overflow: auto;
}
pre.code-block.analysis-output {
  max-height: 520px;
}
code.inline-code {
  display: inline-block;
  max-width: 100%;
  padding: 2px 6px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: 3px;
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-2);
  word-break: break-all;
  white-space: pre-wrap;
}

/* ────────── 响应式 ────────── */
@media (max-width: 960px) {
  .summary-grid,
  .kv-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .trace-search,
  .summary-grid,
  .kv-grid {
    grid-template-columns: 1fr;
  }
}

/* Admin sweep utilities */
@media (max-width: 900px) {
  .summary-grid,
  .kv-grid {
    grid-template-columns: 1fr;
  }
  .trace-search {
    flex-direction: column;
    align-items: stretch;
  }
  .analysis-meta {
    flex-wrap: wrap;
  }
}

</style>
