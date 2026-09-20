<template>
  <div class="report-page ai-main" :class="{ 'report-page-designed': Boolean(activeReportDesign) }" :style="reportDesignStyle">
    <a-spin :loading="loading" style="width: 100%">
      <div class="report-detail-bar">
        <button type="button" class="ai-btn sm" @click="router.push('/inbox?tab=reports')">
          <SfShellIcon name="arrowl" />
          返回
        </button>
        <span class="report-detail-crumb">/ 收件 / 报告 /</span>
        <span class="report-detail-name">{{ detail.title || '报告详情' }}</span>
        <label class="report-design-picker" data-testid="report-design-picker">
          <span>报告模板</span>
          <select v-model="selectedDesignId" @change="persistSelectedDesign">
            <option value="">默认</option>
            <optgroup label="Open Design · 设计系统">
              <option v-for="tpl in designSystemTemplates" :key="tpl.id" :value="tpl.id">
                {{ tpl.name }}
              </option>
            </optgroup>
            <optgroup label="Open Design · Skill 工作流">
              <option v-for="tpl in skillWorkflowTemplates" :key="tpl.id" :value="tpl.id">
                {{ tpl.name }}
              </option>
            </optgroup>
          </select>
        </label>
        <span class="report-detail-spacer" />
        <button type="button" class="ai-btn sm" :disabled="!detail.id" @click="exportReport">
          <SfShellIcon name="download" />
          导出
        </button>
        <button type="button" class="ai-btn sm" :disabled="!detail.id" @click="shareReport">
          <SfShellIcon name="link" />
          分享
        </button>
        <button
          type="button"
          class="ai-btn primary sm"
          :disabled="!detail.related_todo_count"
          @click="openRelatedTodos"
        >
          查看全部 {{ detail.related_todo_count || 0 }} 个待办
        </button>
      </div>

      <div class="report-detail-body ai-pagebody">
        <section class="report-hero">
          <div class="report-hero-main">
            <div class="report-kicker">
              <span>{{ reportChannelLabel(detail.channel) }}</span>
              <span>{{ reportTriggerLabel(detail.trigger_type) }}</span>
              <span v-if="detail.skill_department">{{ detail.skill_department }}</span>
              <span v-if="activeReportDesign">模板 {{ activeReportDesignName }}</span>
            </div>
            <h1 class="report-title">{{ detail.title || '报告详情' }}</h1>
            <p class="report-summary">{{ detail.summary || '暂无摘要' }}</p>
            <div class="report-meta">
              <span>{{ detail.skill_name || detail.skill_id || '-' }}</span>
              <span>运行 ID {{ detail.run_id || '-' }}</span>
              <span>{{ relatedLabel }}</span>
            </div>
            <div class="report-tag-row">
              <a-tag v-for="tag in detail.tags" :key="tag" size="small" color="arcoblue">#{{ tag }}</a-tag>
              <a-tag v-if="isDegradedReport" size="small" color="orange">降级</a-tag>
              <a-tag v-if="isMixedCredentialsReport" size="small" color="gray">多账号</a-tag>
              <a-tag v-if="isStaleReport" size="small" color="red">数据过期</a-tag>
              <a-tag v-if="activeReportDesign" size="small" color="purple">设计模板 · {{ activeReportDesignName }}</a-tag>
            </div>
          </div>
          <div class="report-hero-side">
            <div class="hero-side-label">生成时间</div>
            <div class="hero-side-time">{{ formatTime(detail.created_at) }}</div>
          </div>
          <div class="hero-metric-grid">
            <article v-for="metric in heroMetrics" :key="metric.label" class="hero-metric-card">
              <span class="metric-label">{{ metric.label }}</span>
              <strong>{{ metric.value }}</strong>
              <span v-if="metric.note" class="metric-note">{{ metric.note }}</span>
            </article>
          </div>
        </section>

        <div class="rd-layout">
        <main class="rd-main">
          <a-card v-if="hasDiagnosticPayload" class="report-card report-judgement-card rd-card" :bordered="false">
            <template #title>今日主线判断</template>
          <div class="judgement-head">
            <div>
              <div class="judgement-main">{{ judgementView.mainCause || '未归因' }}</div>
              <div class="judgement-sub">
                次因 {{ judgementView.secondaryCause || '-' }} · 置信度 {{ judgementView.confidence || '-' }}
              </div>
            </div>
            <div class="judgement-tags">
              <a-tag color="arcoblue">{{ judgementView.sourceLabel }}</a-tag>
              <a-tag :color="confidenceColor(judgementView.confidence)">
                {{ judgementView.summary || '待补充判断' }}
              </a-tag>
            </div>
          </div>
          <p class="judgement-direction">{{ judgementView.direction || '暂无团队方向' }}</p>

          <div v-if="judgementWhy.length" class="section-block">
            <div class="section-eyebrow">为什么这样判断</div>
            <ul class="line-list">
              <li v-for="item in judgementWhy" :key="item">{{ item }}</li>
            </ul>
          </div>

          <div v-if="teamActions.length" class="section-block">
            <div class="section-eyebrow">团队动作</div>
            <div class="action-grid">
              <article v-for="item in teamActions" :key="`${item.role}-${item.action}`" class="action-card">
                <span class="action-role">{{ item.role }}</span>
                <p>{{ item.action }}</p>
              </article>
            </div>
          </div>

          <div v-if="blockedActions.length" class="section-block">
            <div class="section-eyebrow">当前禁止动作</div>
            <div class="tag-cloud">
              <a-tag v-for="item in blockedActions" :key="item" color="red">{{ item }}</a-tag>
            </div>
          </div>

          <div v-if="dataGapRows.length" class="section-block">
            <div class="section-eyebrow">数据缺口分布</div>
            <div class="gap-list">
              <div v-for="item in dataGapRows" :key="item.dimension" class="gap-row">
                <span>{{ item.dimension }}</span>
                <div class="gap-bar">
                  <div class="gap-bar-fill" :style="{ width: `${item.ratio}%` }" />
                </div>
                <strong>{{ item.count }}</strong>
              </div>
            </div>
          </div>
          </a-card>

          <a-card v-if="focusItems.length" class="report-card rd-card" :bordered="false">
            <template #title>重点关注商品</template>
        <div class="focus-grid">
          <article v-for="item in focusItems" :key="item.item_id" class="focus-card">
            <div class="focus-head">
              <a-tag color="arcoblue">#{{ item.rank }}</a-tag>
              <a-tag :color="priorityColor(item.focus_reason)">{{ item.focus_reason || '重点商品' }}</a-tag>
            </div>
            <h3>{{ item.item_title }}</h3>
            <div class="focus-meta">
              <span>ID {{ item.item_id }}</span>
              <span>成交 {{ formatCurrency(item.pay_amt) }}</span>
              <span>访客 {{ formatInt(item.visitor_count) }}</span>
            </div>
            <div class="focus-metrics">
              <span>转化 {{ formatPercent(item.conversion_rate, 2) }}</span>
              <span>访客环比 {{ formatSignedPercent(item.visitor_change_rate, 1) }}</span>
              <span>转化环比 {{ formatSignedPercent(item.conversion_change_rate, 1) }}</span>
            </div>
          </article>
        </div>
      </a-card>

          <a-card v-if="top5Items.length" class="report-card rd-card" :bordered="false">
            <template #title>Top5 下滑链接</template>
        <div class="top5-list">
          <article v-for="item in top5Items" :key="item.item_id" class="top5-card">
            <div class="top5-rank">NO.{{ item.rank }}</div>
            <div class="top5-body">
              <h3>{{ item.item_title }}</h3>
              <div class="top5-meta">
                <span>ID {{ item.item_id }}</span>
                <span>成交 {{ formatCurrency(item.pay_amt) }}</span>
                <span>下滑系数 {{ formatDecimal(item.decline_coefficient) }}</span>
              </div>
              <div class="top5-change-grid">
                <div>
                  <span>访客</span>
                  <strong>{{ formatSignedPercent(item.visitor_change_rate, 1) }}</strong>
                </div>
                <div>
                  <span>转化</span>
                  <strong>{{ formatSignedPercent(item.conversion_change_rate, 1) }}</strong>
                </div>
                <div>
                  <span>成交</span>
                  <strong>{{ formatSignedPercent(item.revenue_change_rate, 1) }}</strong>
                </div>
              </div>
            </div>
          </article>
        </div>
      </a-card>

          <a-card v-if="productCards.length" class="report-card rd-card" :bordered="false">
            <template #title>商品诊断卡</template>
        <template #extra>
          <div class="product-card-toolbar">
            <a-radio-group v-model="productCardFilter" type="button" size="small" class="product-card-filter">
              <a-radio value="all">全部 {{ productCards.length }}</a-radio>
              <a-radio v-if="productCardCounts.P0" value="P0">P0 {{ productCardCounts.P0 }}</a-radio>
              <a-radio v-if="productCardCounts.P1" value="P1">P1 {{ productCardCounts.P1 }}</a-radio>
              <a-radio v-if="productCardCounts.P2" value="P2">P2 {{ productCardCounts.P2 }}</a-radio>
            </a-radio-group>
            <a-button
              v-if="filteredProductCards.length > 6"
              size="small"
              @click="showAllProductCards = !showAllProductCards"
            >
              {{ showAllProductCards ? '收起' : `展开 (${filteredProductCards.length})` }}
            </a-button>
          </div>
        </template>
        <div v-if="!visibleProductCards.length" class="product-empty">当前优先级没有诊断卡。</div>
        <div v-else class="product-card-list">
          <article v-for="card in visibleProductCards" :key="`${card.item_id}-${card.data_time}`" class="product-card">
            <div class="product-card-head">
              <div>
                <div class="product-card-title">{{ card.item_name }}</div>
                <div class="product-card-meta">
                  <span>{{ card.priority }}</span>
                  <span>{{ card.type }}</span>
                  <span>ID {{ card.item_id }}</span>
                  <span>{{ card.data_time }}</span>
                </div>
              </div>
              <a-tag :color="priorityColor(card.priority)">{{ card.priority }}</a-tag>
            </div>

            <div v-if="card.metrics.length" class="product-metric-grid">
              <div v-for="metric in card.metrics" :key="`${card.item_id}-${metric.name}`" class="product-metric-card">
                <span class="metric-label">{{ metric.name }}</span>
                <strong>{{ metric.value }}</strong>
                <span class="metric-note">{{ metric.status }}</span>
              </div>
            </div>

            <div class="product-two-column">
              <div v-if="card.analysis_basis.length" class="product-subsection">
                <div class="section-eyebrow">分析依据</div>
                <ul class="line-list compact">
                  <li v-for="item in card.analysis_basis" :key="`${card.item_id}-${item.dimension}`">
                    <strong>{{ item.dimension }}</strong>：{{ item.basis }}
                  </li>
                </ul>
              </div>
              <div v-if="card.top_actions.length" class="product-subsection">
                <div class="section-eyebrow">优先动作</div>
                <ul class="line-list compact">
                  <li v-for="item in card.top_actions" :key="`${card.item_id}-${item.action}`">
                    <strong>{{ item.dimension }}</strong>：{{ item.action }}
                  </li>
                </ul>
              </div>
            </div>
          </article>
        </div>
      </a-card>

          <a-card v-if="renderedMarkdown" class="report-card rd-card rd-markdown-card" :bordered="false">
            <template #title>报告正文</template>
            <template #extra>
              <a-button size="small" @click="markdownExpanded = !markdownExpanded">
                {{ markdownExpanded ? '收起' : '展开全文' }}
              </a-button>
            </template>
            <div :class="['markdown-body', 'rd-markdown', markdownExpanded ? '' : 'rd-markdown-fold']" v-html="renderedMarkdown" />
            <div v-if="!markdownExpanded" class="rd-markdown-mask">
              <a-button size="small" type="text" @click="markdownExpanded = true">展开全文（折叠以减少干扰）</a-button>
            </div>
          </a-card>
        </main>

        <aside class="rd-aside">
          <div class="rd-aside-sticky">
            <a-card v-if="overviewMetrics.length" class="report-card rd-aside-card" :bordered="false">
              <template #title>全店概览</template>
              <ul class="rd-overview-list">
                <li v-for="metric in overviewMetrics" :key="metric.label">
                  <span>{{ metric.label }}</span>
                  <strong>{{ metric.value }}</strong>
                </li>
              </ul>
            </a-card>

            <a-card title="关联待办" class="report-card rd-aside-card" :bordered="false">
              <RelatedTodosList :items="detail.related_todos || []" @open="openTodo" />
            </a-card>

            <a-card v-if="hasSourceMeta" title="数据来源" class="report-card rd-aside-card" :bordered="false">
              <div class="source-meta-tags">
                <a-tag :color="isDegradedReport ? 'orange' : 'green'" size="small">
                  {{ isDegradedReport ? '降级' : '未降级' }}
                </a-tag>
                <a-tag :color="isMixedCredentialsReport ? 'gray' : 'green'" size="small">
                  {{ isMixedCredentialsReport ? '多账号' : '单凭证' }}
                </a-tag>
                <a-tag v-if="isStaleReport" color="red" size="small">过期 scope {{ staleScopes.length }}</a-tag>
                <a-tag v-if="dataHealthRatio !== '-'" color="arcoblue" size="small">健康 {{ dataHealthRatio }}</a-tag>
              </div>
              <div v-if="textValue(reportMeta.degraded_reason)" class="source-warning">
                {{ textValue(reportMeta.degraded_reason) }}
              </div>
              <a-collapse :bordered="false">
                <a-collapse-item header="Proof 摘要" key="proofs">
                  <div v-if="!proofRows.length" class="source-empty">暂无 proof。</div>
                  <div v-else class="proof-list">
                    <div v-for="proof in proofRows" :key="proof.key" class="proof-row">
                      <div class="proof-head">
                        <strong>{{ proof.data_scope || proof.tool_name || proof.proof_id || 'proof' }}</strong>
                        <a-tag :color="proofStatusColor(proof.status)" size="small">{{ proof.status || '-' }}</a-tag>
                      </div>
                      <div class="proof-meta">
                        <span>alias {{ proof.credential_alias || '-' }}</span>
                        <span>slot {{ proof.slot_id || '-' }}</span>
                        <span>{{ proof.egress_group || '-' }}</span>
                      </div>
                    </div>
                  </div>
                </a-collapse-item>
                <a-collapse-item v-if="staleScopes.length" header="过期数据范围" key="stale">
                  <div class="tag-cloud">
                    <a-tag v-for="scope in staleScopes" :key="scope" color="red">{{ scope }}</a-tag>
                  </div>
                </a-collapse-item>
                <a-collapse-item v-if="credentialPlanRows.length" header="Credential Plan" key="plan">
                  <div class="proof-list">
                    <div v-for="row in credentialPlanRows" :key="row.key" class="proof-row">
                      <div class="proof-head">
                        <strong>{{ row.data_scope || row.scope || 'scope' }}</strong>
                        <span class="source-muted">{{ row.credential_alias || '-' }}</span>
                      </div>
                      <div class="proof-meta">
                        <span>{{ row.platform || '-' }}</span>
                        <span>{{ row.endpoint_family || '-' }}</span>
                      </div>
                    </div>
                  </div>
                </a-collapse-item>
              </a-collapse>
            </a-card>

            <a-card v-if="marketStatusRows.length" class="report-card rd-aside-card" :bordered="false">
              <template #title>市场 Top300</template>
              <template v-if="marketStatusRows.length > MARKET_PREVIEW_COUNT" #extra>
                <a-button size="mini" @click="marketExpanded = !marketExpanded">
                  {{ marketExpanded ? '收起' : `共 ${marketStatusRows.length}` }}
                </a-button>
              </template>
              <ul class="rd-market-list">
                <li v-for="item in visibleMarketRows" :key="item.item_id">
                  <div class="rd-market-head">
                    <a-tag size="small" :color="marketStatusColor(item.status)">{{ item.current_presence || item.status }}</a-tag>
                    <strong>{{ item.item_title }}</strong>
                  </div>
                  <div v-if="item.detail" class="rd-market-detail">{{ item.detail }}</div>
                </li>
              </ul>
            </a-card>

            <a-card title="调试" class="report-card rd-aside-card" :bordered="false">
              <a-collapse :bordered="false">
                <a-collapse-item header="结构化数据" key="payload">
                  <pre class="payload-block">{{ prettyPayload }}</pre>
                </a-collapse-item>
                <a-collapse-item v-if="detail.debug_context" header="调试上下文" key="debug">
                  <div class="debug-grid" data-testid="report-debug">
                    <div>
                      <div class="debug-title">input_snapshot</div>
                      <pre class="payload-block">{{ prettyInputSnapshot }}</pre>
                    </div>
                    <div>
                      <div class="debug-title">raw_output_result</div>
                      <pre class="payload-block">{{ prettyRawOutput }}</pre>
                    </div>
                  </div>
                </a-collapse-item>
              </a-collapse>
            </a-card>
          </div>
        </aside>
        </div>
      </div>
    </a-spin>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { renderMd } from '@/utils/renderMd'
import { formatTime } from '@/utils/format'
import { copyText } from '@/utils/clipboard'
import type { ReportDesignTemplate, ReportDetailResponse } from '@/types/inbox'
import RelatedTodosList from './components/RelatedTodosList.vue'
import { inboxApi } from '@/api'
import { prettyJson, reportChannelLabel, reportTriggerLabel } from './presentation'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

const route: any = useRoute()
const router: any = useRouter()
const userStore = useUserStore()

const loading = ref(false)
const showAllProductCards = ref(false)
const markdownExpanded = ref(false)
const marketExpanded = ref(false)
const MARKET_PREVIEW_COUNT = 5
const detail = ref<ReportDetailResponse>({
  id: '',
  decision_log_id: 0,
  report_index: 0,
  run_id: '',
  skill_id: '',
  skill_name: '',
  skill_department: '',
  channel: '',
  title: '',
  summary: '',
  content_markdown: '',
  metrics: [],
  tags: [],
  trigger_type: '',
  payload: null,
  debug_context: null,
  related_todos: [],
  related_todo_count: 0,
  related_pending_request_count: 0,
  created_at: '',
  report_design: null,
})

const REPORT_DESIGN_STORAGE_KEY = 'skillforge:inbox:report-design-template'
const designTemplates = ref<ReportDesignTemplate[]>([])
const designTemplatesLoaded = ref(false)
const selectedDesignId = ref('')

const reportId = computed(() => String(route.params.id || ''))
const payload = computed(() => asRecord(detail.value.payload) || {})
const reportDesignFromPayload = computed(() =>
  asRecord(payload.value._report_design) || asRecord((detail.value as any).report_design),
)
const designSystemTemplates = computed(() =>
  designTemplates.value.filter((item) => item.source_type === 'design_system'),
)
const skillWorkflowTemplates = computed(() =>
  designTemplates.value.filter((item) => item.source_type === 'skill'),
)
const selectedDesignTemplate = computed(() =>
  designTemplates.value.find((item) => item.id === selectedDesignId.value) || null,
)
const activeReportDesign = computed<Record<string, any> | ReportDesignTemplate | null>(() =>
  selectedDesignTemplate.value || reportDesignFromPayload.value || null,
)
const activeReportDesignName = computed(() =>
  textValue((activeReportDesign.value as any)?.name) || textValue((activeReportDesign.value as any)?.template_id) || '默认模板',
)
const reportDesignStyle = computed(() => {
  const design = activeReportDesign.value as any
  const colors = arrayValue<string>(design?.preview?.colors || design?.design_standard?.colors)
    .map((item) => textValue(item))
    .filter(Boolean)
  const accent = colors[0] || '#5062ff'
  const accent2 = colors[1] || '#1f2840'
  return {
    '--rd-design-accent': accent,
    '--rd-design-accent-2': accent2,
  } as Record<string, string>
})
const reportMeta = computed(() => {
  const payloadMeta =
    asRecord(payload.value._skillforge_meta) ||
    asRecord(payload.value.skillforge_meta) ||
    asRecord(asRecord(payload.value.meta)?._skillforge_meta)
  if (payloadMeta) return payloadMeta
  const rawOutput = asRecord(detail.value.debug_context?.raw_output_result)
  return asRecord(rawOutput?._skillforge_meta) || {}
})
const storeMainJudgement = computed(() => asRecord(payload.value.store_main_judgement))
const aiAnalysis = computed(() => asRecord(payload.value.ai_analysis))
const storeOverview = computed(() => asRecord(payload.value.store_overview))
const renderedMarkdown = computed(() => renderMd(detail.value.content_markdown || detail.value.summary || ''))
const prettyPayload = computed(() => prettyJson(detail.value.payload || {}))
const prettyInputSnapshot = computed(() => prettyJson(detail.value.debug_context?.input_snapshot || {}))
const prettyRawOutput = computed(() => prettyJson(detail.value.debug_context?.raw_output_result || {}))
const relatedLabel = computed(() => {
  const total = detail.value.related_todo_count || 0
  const pending = detail.value.related_pending_request_count || 0
  if (!total) return '暂无关联待办'
  return `${total} 个关联待办，其中 ${pending} 个未决请求`
})
const isDegradedReport = computed(() => Boolean(reportMeta.value.degraded || payload.value.degraded))
const isMixedCredentialsReport = computed(() => Boolean(reportMeta.value.mixed_credentials || payload.value.mixed_credentials))
const staleScopes = computed(() => stringList(reportMeta.value.stale_data_scopes || payload.value.stale_data_scopes))
const isStaleReport = computed(() => Boolean(reportMeta.value.stale_data || payload.value.stale_data || staleScopes.value.length))
const dataHealthRatio = computed(() => {
  const value = numberValue(reportMeta.value.data_health_ratio ?? payload.value.data_health_ratio)
  return value == null ? '-' : `${Math.round((value > 1 ? value / 100 : value) * 100)}%`
})
const proofRows = computed(() =>
  arrayValue(reportMeta.value.data_proofs || reportMeta.value.data_provenance || reportMeta.value.collection_proofs)
    .map((item, index) => sanitizeProofRow(asRecord(item) || {}, index))
    .slice(0, 12),
)
const credentialPlanRows = computed(() =>
  arrayValue(reportMeta.value.credential_plan_summary || reportMeta.value.credential_plan)
    .map((item, index) => sanitizeCredentialPlanRow(asRecord(item) || {}, index))
    .slice(0, 12),
)
const hasSourceMeta = computed(() => Boolean(
  Object.keys(reportMeta.value).length ||
  proofRows.value.length ||
  staleScopes.value.length ||
  isDegradedReport.value ||
  isMixedCredentialsReport.value,
))

const heroMetrics = computed(() => {
  const overview = storeOverview.value || {}
  const items = [
    { label: '扫描商品', value: formatInt(overview.item_count), note: '全店覆盖规模' },
    { label: '成交金额', value: formatCurrency(overview.total_pay_amt), note: '当日成交' },
    { label: '全店转化', value: formatPercent(overview.store_pay_conversion_rate), note: '支付转化率' },
    { label: '数据缺口', value: formatInt(payload.value.data_quality_count || storeMainJudgement.value?.data_gap_count), note: '仍待补采' },
  ]
  return items.filter((item) => item.value && item.value !== '-')
})

const overviewMetrics = computed(() => {
  const overview = storeOverview.value || {}
  return [
    { label: '商品数', value: formatInt(overview.item_count), note: '' },
    { label: '全店访客', value: formatInt(overview.total_visitors), note: '' },
    { label: '成交金额', value: formatCurrency(overview.total_pay_amt), note: '' },
    { label: '支付件数', value: formatInt(overview.total_pay_item_count), note: '' },
    { label: '全店转化率', value: formatPercent(overview.store_pay_conversion_rate), note: '' },
    { label: '转化中位数', value: formatPercent(overview.item_conversion_rate_median), note: '' },
    { label: '转化均值', value: formatPercent(overview.item_conversion_rate_mean), note: '' },
    { label: '访客中位数', value: formatInt(overview.visitor_median), note: '' },
    { label: '成交中位数', value: formatCurrency(overview.pay_amt_median), note: '' },
    { label: '下滑商品数', value: formatInt(overview.down_item_count), note: '' },
  ].filter((item) => item.value && item.value !== '-')
})

const judgementView = computed(() => {
  const rule = storeMainJudgement.value || {}
  const ai = aiAnalysis.value || {}
  const aiNarrativeRecord = asRecord(ai.narrative)
  const aiNarrativeText = textValue(ai.narrative)
  return {
    sourceLabel: aiAnalysis.value ? 'AI 综合' : '规则判断',
    mainCause: firstText(aiNarrativeRecord?.main_cause, ai.main_cause, rule.main_cause),
    secondaryCause: firstText(aiNarrativeRecord?.secondary_cause, ai.secondary_cause, rule.secondary_cause),
    confidence: firstText(ai.confidence, aiNarrativeRecord?.confidence, rule.confidence),
    summary: firstText(ai.summary, aiNarrativeRecord?.summary, rule.summary),
    direction: firstText(
      aiNarrativeText,
      aiNarrativeRecord?.decision,
      aiNarrativeRecord?.recommendation,
      ai.decision,
      ai.recommendation,
      rule.today_team_direction,
    ),
  }
})

const aiRiskSignals = computed(() =>
  arrayValue(aiAnalysis.value?.risk_signals)
    .map((item) => {
      const record = asRecord(item)
      if (!record) return textValue(item)
      const title = firstText(record.title, record.signal, record.name)
      const detail = firstText(record.detail, record.summary, record.reason)
      return title && detail ? `${title}：${detail}` : title || detail
    })
    .filter(Boolean),
)
const judgementWhy = computed(() => {
  const ruleWhy = stringList(storeMainJudgement.value?.why)
  return aiRiskSignals.value.length ? [...aiRiskSignals.value, ...ruleWhy] : ruleWhy
})
const blockedActions = computed(() => stringList(storeMainJudgement.value?.blocked_actions))
const aiNextActions = computed(() =>
  arrayValue(aiAnalysis.value?.next_actions)
    .map((item) => {
      const record = asRecord(item)
      if (!record) {
        const action = textValue(item)
        return action ? { role: '建议下一步', action } : null
      }
      const owner = firstText(record.owner, record.role, record.team, record.assignee) || '建议下一步'
      const action = firstText(record.action, record.title, record.detail, record.summary)
      const deadline = firstText(record.deadline, record.due_at)
      return action ? { role: owner, action: deadline ? `${action}（${deadline}）` : action } : null
    })
    .filter((item): item is { role: string; action: string } => Boolean(item)),
)
const teamActions = computed(() =>
  aiNextActions.value.length ? aiNextActions.value : arrayValue(storeMainJudgement.value?.team_actions)
    .map((item) => {
      const record = asRecord(item) || {}
      return {
        role: textValue(record.role) || '角色',
        action: textValue(record.action) || '',
      }
    })
    .filter((item) => item.action),
)

const dataGapRows = computed(() => {
  const rows = arrayValue(storeMainJudgement.value?.data_gap_summary)
    .map((item) => {
      const record = asRecord(item) || {}
      return {
        dimension: textValue(record.dimension) || '未命名维度',
        count: numberValue(record.count) || 0,
      }
    })
    .filter((item) => item.count > 0)
  const maxCount = Math.max(...rows.map((item) => item.count), 1)
  return rows.map((item) => ({
    ...item,
    ratio: Math.max(12, Math.round((item.count / maxCount) * 100)),
  }))
})

const focusItems = computed(() =>
  arrayValue(payload.value.focus_items)
    .map((item) => asRecord(item) || {})
    .map((item) => ({
      rank: textValue(item.rank),
      item_id: textValue(item.item_id),
      item_title: textValue(item.item_title),
      pay_amt: item.pay_amt,
      visitor_count: item.visitor_count,
      conversion_rate: item.conversion_rate,
      visitor_change_rate: item.visitor_change_rate,
      conversion_change_rate: item.conversion_change_rate,
      focus_reason: textValue(item.focus_reason),
    }))
    .slice(0, 8),
)

const top5Items = computed(() =>
  arrayValue(payload.value.top5)
    .map((item) => asRecord(item) || {})
    .map((item) => ({
      rank: textValue(item.rank),
      item_id: textValue(item.item_id),
      item_title: textValue(item.item_title),
      pay_amt: item.pay_amt,
      decline_coefficient: item.decline_coefficient,
      visitor_change_rate: item.visitor_change_rate,
      conversion_change_rate: item.conversion_change_rate,
      revenue_change_rate: item.revenue_change_rate,
    }))
    .slice(0, 5),
)

const marketStatusRows = computed(() =>
  arrayValue(payload.value.market_top300_status)
    .map((item) => asRecord(item) || {})
    .map((item) => ({
      item_id: textValue(item.item_id),
      item_title: textValue(item.item_title),
      status: textValue(item.status),
      detail: textValue(item.detail),
      current_presence: textValue(item.current_presence),
    }))
    .slice(0, 12),
)

const visibleMarketRows = computed(() =>
  marketExpanded.value ? marketStatusRows.value : marketStatusRows.value.slice(0, MARKET_PREVIEW_COUNT),
)

const productCards = computed(() =>
  arrayValue(payload.value.product_cards)
    .map((item) => asRecord(item) || {})
    .map((card) => ({
      priority: textValue(card.priority) || 'P2',
      type: textValue(card.type) || '商品诊断',
      item_id: textValue(asRecord(card.item)?.id),
      item_name: textValue(asRecord(card.item)?.name) || '未命名商品',
      data_time: textValue(card.data_time),
      metrics: arrayValue(asRecord(card.data_overview)?.metrics)
        .map((metric) => asRecord(metric) || {})
        .map((metric) => ({
          name: textValue(metric.name) || '指标',
          value: textValue(metric.value) || '-',
          status: textValue(metric.status) || '',
        }))
        .slice(0, 6),
      analysis_basis: arrayValue(card.analysis_basis)
        .map((item) => asRecord(item) || {})
        .map((item) => ({
          dimension: textValue(item.dimension),
          basis: textValue(item.basis),
        }))
        .filter((item) => item.dimension || item.basis)
        .slice(0, 4),
      top_actions: arrayValue(card.top_actions)
        .map((item) => asRecord(item) || {})
        .map((item) => ({
          dimension: textValue(item.dimension),
          action: textValue(item.action),
        }))
        .filter((item) => item.action)
        .slice(0, 4),
    })),
)

const productCardFilter = ref<'all' | 'P0' | 'P1' | 'P2'>('all')

const productCardCounts = computed(() => {
  const counts = { P0: 0, P1: 0, P2: 0 }
  for (const card of productCards.value) {
    const p = String(card.priority || '').toUpperCase()
    if (p.includes('P0')) counts.P0 += 1
    else if (p.includes('P1')) counts.P1 += 1
    else counts.P2 += 1
  }
  return counts
})

const filteredProductCards = computed(() => {
  if (productCardFilter.value === 'all') return productCards.value
  return productCards.value.filter((card) => {
    const p = String(card.priority || '').toUpperCase()
    if (productCardFilter.value === 'P0') return p.includes('P0')
    if (productCardFilter.value === 'P1') return p.includes('P1')
    return !p.includes('P0') && !p.includes('P1')
  })
})

const visibleProductCards = computed(() =>
  showAllProductCards.value ? filteredProductCards.value : filteredProductCards.value.slice(0, 6),
)

const hasDiagnosticPayload = computed(() => Boolean(
  aiAnalysis.value ||
  storeMainJudgement.value ||
  storeOverview.value ||
  focusItems.value.length ||
  top5Items.value.length ||
  marketStatusRows.value.length ||
  productCards.value.length,
))

async function loadDesignTemplates() {
  if (designTemplatesLoaded.value) return
  try {
    const [designs, skills]: any[] = await Promise.all([
      inboxApi.listReportDesignTemplates({ source_type: 'design_system', page_size: 200 }),
      inboxApi.listReportDesignTemplates({ source_type: 'skill', page_size: 200 }),
    ])
    designTemplates.value = [...(designs.items || []), ...(skills.items || [])]
    designTemplatesLoaded.value = true
  } catch {
    designTemplates.value = []
  }
}

function syncSelectedDesignFromDetail() {
  const reportTemplateId = textValue(reportDesignFromPayload.value?.template_id || reportDesignFromPayload.value?.id)
  const saved = textValue(window.localStorage.getItem(REPORT_DESIGN_STORAGE_KEY))
  selectedDesignId.value = reportTemplateId || saved
}

function persistSelectedDesign() {
  if (selectedDesignId.value) {
    window.localStorage.setItem(REPORT_DESIGN_STORAGE_KEY, selectedDesignId.value)
  } else {
    window.localStorage.removeItem(REPORT_DESIGN_STORAGE_KEY)
  }
}

async function loadDetail() {
  if (!reportId.value) return
  loading.value = true
  showAllProductCards.value = false
  productCardFilter.value = 'all'
  markdownExpanded.value = false
  marketExpanded.value = false
  try {
    await loadDesignTemplates()
    detail.value = await inboxApi.getReport(reportId.value, userStore.canViewAll ? { include_debug: 1 } : undefined) as any
    syncSelectedDesignFromDetail()
  } catch (error: any) {
    Message.error(error._message || '加载报告详情失败')
  } finally {
    loading.value = false
  }
}


function openTodo(todoId: number) {
  router.push(`/inbox/todos/${todoId}`)
}

function openRelatedTodos() {
  if (!detail.value.decision_log_id) return
  router.push({
    path: '/inbox',
    query: {
      tab: 'pending',
      decision_log_id: String(detail.value.decision_log_id),
      status: 'pending',
    },
  })
}

function exportReport() {
  const body = [
    `# ${detail.value.title || '报告详情'}`,
    '',
    `- 报告 ID：${detail.value.id || '-'}`,
    `- 生成时间：${formatTime(detail.value.created_at)}`,
    `- Skill：${detail.value.skill_name || detail.value.skill_id || '-'}`,
    `- 运行 ID：${detail.value.run_id || '-'}`,
    `- 关联待办：${detail.value.related_todo_count || 0}`,
    '',
    detail.value.summary || '',
    '',
    detail.value.content_markdown || '',
  ].join('\n')
  const blob = new Blob([body], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${safeFilename(detail.value.title || detail.value.id || 'report')}.md`
  link.click()
  URL.revokeObjectURL(url)
  Message.success('已导出报告')
}

async function shareReport() {
  const url = window.location.href
  if (await copyText(url)) {
    Message.success('报告链接已复制')
  } else {
    Message.warning('复制失败，请手动复制地址栏链接')
  }
}

function safeFilename(value: string): string {
  return String(value || 'report')
    .trim()
    .replace(/[\\/:*?"<>|]+/g, '-')
    .replace(/\s+/g, '-')
    .slice(0, 80) || 'report'
}

function asRecord(value: unknown): Record<string, any> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, any>
    : null
}

function arrayValue<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : []
}

function textValue(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value.trim()
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return ''
}

function firstText(...values: unknown[]): string {
  for (const value of values) {
    const text = textValue(value)
    if (text) return text
  }
  return ''
}

function numberValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => textValue(item))
    .filter(Boolean)
}

function sanitizeProofRow(record: Record<string, any>, index: number) {
  return {
    key: textValue(record.proof_id) || textValue(record.id) || `proof-${index}`,
    proof_id: textValue(record.proof_id) || textValue(record.id),
    tool_name: textValue(record.tool_name),
    data_scope: textValue(record.data_scope),
    status: textValue(record.status) || textValue(record.warning_group),
    credential_alias: textValue(record.credential_alias),
    slot_id: textValue(record.slot_id),
    egress_group: textValue(record.egress_group),
    row_count: numberValue(record.row_count),
  }
}

function sanitizeCredentialPlanRow(record: Record<string, any>, index: number) {
  return {
    key: textValue(record.data_scope) || textValue(record.scope) || `plan-${index}`,
    platform: textValue(record.platform),
    data_scope: textValue(record.data_scope),
    scope: textValue(record.scope),
    endpoint_family: textValue(record.endpoint_family),
    credential_alias: textValue(record.credential_alias),
  }
}

function proofStatusColor(value?: string): string {
  const normalized = String(value || '').toLowerCase()
  if (['ok', 'success', 'valid', 'passed'].includes(normalized)) return 'green'
  if (['degraded', 'warning', 'stale'].includes(normalized)) return 'orange'
  if (['failed', 'error', 'expired', 'blocked'].includes(normalized)) return 'red'
  return 'gray'
}

function formatInt(value: unknown): string {
  const num = numberValue(value)
  return num == null ? '-' : Math.round(num).toLocaleString('zh-CN')
}

function formatDecimal(value: unknown): string {
  const num = numberValue(value)
  return num == null ? '-' : num.toFixed(2)
}

function formatCurrency(value: unknown): string {
  const num = numberValue(value)
  return num == null ? '-' : `¥${num.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
}

function formatPercent(value: unknown, digits = 1): string {
  const num = numberValue(value)
  return num == null ? '-' : `${(num * 100).toFixed(digits)}%`
}

function formatSignedPercent(value: unknown, digits = 1): string {
  const num = numberValue(value)
  if (num == null) return '-'
  const formatted = `${(num * 100).toFixed(digits)}%`
  return num > 0 ? `+${formatted}` : formatted
}

function confidenceColor(value: string): string {
  if (value.includes('高')) return 'green'
  if (value.includes('中')) return 'arcoblue'
  if (value.includes('低')) return 'orange'
  return 'gray'
}

function priorityColor(value: string): string {
  if (String(value).includes('P0')) return 'red'
  if (String(value).includes('P1') || String(value).includes('高')) return 'orange'
  if (String(value).includes('P2') || String(value).includes('中')) return 'arcoblue'
  return 'gray'
}

function marketStatusColor(value: string): string {
  if (value.includes('上升') || value.includes('进榜')) return 'green'
  if (value.includes('未进') || value.includes('下滑')) return 'orange'
  return 'gray'
}

watch(() => route.params.id, loadDetail, { immediate: true })
</script>

<style scoped>
.report-page {
  min-height: calc(100vh - 52px);
  padding: 0;
  background: var(--ai-bg);
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}

.report-detail-body {
  flex: 1;
  min-width: 0;
  overflow: auto;
}

.report-detail-bar {
  height: 44px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 24px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface);
  min-width: 0;
}

.report-detail-bar .ai-btn {
  flex: 0 0 auto;
}

.report-detail-bar .ai-btn svg {
  width: 12px;
  height: 12px;
}

.report-detail-crumb {
  font-size: 11px;
  color: var(--ai-ink-4);
  white-space: nowrap;
}

.report-detail-name {
  min-width: 0;
  max-width: 520px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ai-ink-1);
}

.report-design-picker {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 360px;
  padding: 3px 7px;
  border: 1px solid var(--ai-border);
  border-radius: 999px;
  background: color-mix(in srgb, var(--ai-surface) 86%, var(--rd-design-accent, var(--ai-accent)) 14%);
  color: var(--ai-ink-2);
  font-size: 11px;
  font-weight: 700;
}

.report-design-picker select {
  max-width: 220px;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--ai-ink-1);
  font-size: 11px;
  font-weight: 600;
}

.report-detail-spacer {
  flex: 1 1 auto;
  min-width: 8px;
}

.report-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) 220px;
  gap: 16px;
  margin: 0 0 18px;
  padding: 28px;
  border-radius: 12px;
  background:
    radial-gradient(circle at 18% 10%, color-mix(in srgb, var(--rd-design-accent, #5062ff) 30%, transparent) 0, transparent 32%),
    linear-gradient(135deg, color-mix(in srgb, var(--rd-design-accent-2, #1f2840) 82%, #111827 18%), #1f2840);
  color: white;
  border: 0;
  box-shadow: none;
}

.report-kicker,
.report-meta,
.report-tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.report-kicker {
  font-size: 12px;
  letter-spacing: 0;
  text-transform: none;
  color: rgba(255, 255, 255, 0.65);
  font-weight: 500;
}
.report-kicker > span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.report-kicker > span + span::before {
  content: '·';
  margin-right: 6px;
  color: rgba(255, 255, 255, 0.36);
}

.report-title {
  margin: 6px 0 6px;
  font-size: 28px;
  line-height: 1.25;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: white;
}

.report-summary {
  margin: 0;
  max-width: 920px;
  color: rgba(255, 255, 255, 0.75);
  font-size: 13px;
  line-height: 1.6;
}

.report-meta {
  margin-top: 12px;
  color: rgba(255, 255, 255, 0.7);
  font-size: 11.5px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.report-meta > span {
  display: inline-flex;
  align-items: center;
}
.report-meta > span + span::before {
  content: '·';
  margin-right: 8px;
  color: rgba(255, 255, 255, 0.32);
}

.report-hero-side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
}

.hero-side-label {
  font-size: 10.5px;
  letter-spacing: 0;
  text-transform: uppercase;
  color: rgba(255, 255, 255, 0.55);
  font-weight: 500;
}

.hero-side-time {
  font-size: 14px;
  font-weight: 500;
  color: white;
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
}

/* Arco a-tag 在 hero 内贴近 .ai-pill */
.report-hero :deep(.arco-tag) {
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.1);
  color: rgba(255, 255, 255, 0.78);
}
.report-hero :deep(.arco-tag-color-arcoblue) {
  background: rgba(255, 255, 255, 0.1);
  color: rgba(255, 255, 255, 0.78);
  border-color: transparent;
}
.report-hero :deep(.arco-tag-color-orange) {
  background: rgba(251, 191, 36, 0.16);
  color: #fbbf24;
  border-color: transparent;
}
.report-hero :deep(.arco-tag-color-red) {
  background: rgba(248, 113, 113, 0.16);
  color: #fca5a5;
  border-color: transparent;
}
.report-hero :deep(.arco-tag-color-gray) {
  background: rgba(255, 255, 255, 0.1);
  color: rgba(255, 255, 255, 0.78);
}

.hero-metric-grid {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 10px;
}

.hero-metric-card {
  padding: 14px 16px;
  border-radius: 12px;
  background: color-mix(in srgb, rgba(255, 255, 255, 0.08) 80%, var(--rd-design-accent, #5062ff) 20%);
  border: 1px solid color-mix(in srgb, rgba(255, 255, 255, 0.1) 70%, var(--rd-design-accent, #5062ff) 30%);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.metric-label {
  font-size: 11px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: rgba(122, 137, 160, 0.78);
}

.hero-metric-card .metric-label,
.hero-metric-card .metric-note {
  color: rgba(235, 241, 248, 0.7);
}

.hero-metric-card strong,
.overview-tile strong,
.product-metric-card strong {
  font-size: 22px;
  line-height: 1.1;
  color: inherit;
}

.metric-note {
  font-size: 12px;
  line-height: 1.5;
  color: #697487;
}

.report-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(320px, 0.85fr);
  gap: 18px;
  margin-bottom: 18px;
}

/* ── 报告详情 双栏：左主体 + 右 sticky 侧栏 ── */
.rd-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 18px;
  align-items: start;
}

.rd-main {
  display: grid;
  gap: 16px;
  min-width: 0;
}

.rd-card {
  margin-bottom: 0 !important;
}

.rd-aside {
  position: sticky;
  top: 16px;
  align-self: start;
  min-width: 0;
}

.rd-aside-sticky {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
  /* 主内容滚动容器是 100vh - 顶栏 52px；预留 16+12 上下 sticky 余量 */
  max-height: calc(100vh - 80px);
  overflow-y: auto;
  padding-right: 4px;
  scrollbar-gutter: stable;
  scrollbar-width: thin;
  scrollbar-color: rgba(98, 115, 142, 0.32) transparent;
}

.rd-aside-sticky::-webkit-scrollbar {
  width: 6px;
}

.rd-aside-sticky::-webkit-scrollbar-thumb {
  background: rgba(98, 115, 142, 0.32);
  border-radius: 999px;
}

.rd-aside-sticky::-webkit-scrollbar-track {
  background: transparent;
}

.rd-aside-card {
  margin-bottom: 0 !important;
  border-radius: 16px !important;
  box-shadow: 0 8px 18px rgba(18, 29, 46, 0.06) !important;
}

.rd-aside-card :deep(.arco-card-header) {
  padding: 14px 16px 0 !important;
}

.rd-aside-card :deep(.arco-card-body) {
  padding: 14px 16px 16px !important;
}

.rd-aside-card :deep(.arco-card-head-title) {
  font-size: 13px !important;
  font-weight: 800 !important;
}

.rd-overview-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 6px;
}

.rd-overview-list li {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 0;
  /* webkit 1px dashed 渲染成 solid，用 background gradient pattern 代替 */
  background-image: linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%);
  background-position: bottom;
  background-size: 6px 1px;
  background-repeat: repeat-x;
}

.rd-overview-list li:last-child {
  background-image: none;
}

.rd-overview-list li span {
  color: #6b7280;
  font-size: 12px;
  font-weight: 600;
}

.rd-overview-list li strong {
  color: #1b2940;
  font-size: 14px;
  font-weight: 800;
  text-align: right;
}

.source-meta-tags,
.proof-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.source-warning {
  margin: 10px 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(245, 154, 35, 0.12);
  color: #9a5b00;
  font-size: 12px;
  line-height: 1.5;
  font-weight: 700;
}

.source-empty,
.source-muted {
  color: #6b7280;
  font-size: 12px;
}

.proof-list {
  display: grid;
  gap: 8px;
}

.proof-row {
  padding: 8px 10px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: rgba(248, 250, 252, 0.9);
}

.proof-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.proof-head strong {
  min-width: 0;
  color: #1b2940;
  font-size: 12px;
  font-weight: 800;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.proof-meta {
  margin-top: 5px;
  color: #6b7280;
  font-family: var(--ai-font-mono);
  font-size: 11px;
}

.rd-market-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
}

.rd-market-list li {
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(248, 250, 252, 0.85);
  border: 1px solid rgba(98, 115, 142, 0.08);
}

.rd-market-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.rd-market-head strong {
  color: #1b2940;
  font-size: 12.5px;
  font-weight: 700;
  line-height: 1.4;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rd-market-detail {
  color: #6b7280;
  font-size: 11.5px;
  line-height: 1.5;
}

/* ── 报告正文折叠 ── */
.rd-markdown-card {
  position: relative;
}

.rd-markdown {
  transition: max-height 0.25s ease;
}

.rd-markdown-fold {
  max-height: 320px;
  overflow: hidden;
  -webkit-mask-image: linear-gradient(180deg, #000 65%, transparent 100%);
  mask-image: linear-gradient(180deg, #000 65%, transparent 100%);
}

.rd-markdown-mask {
  display: flex;
  justify-content: center;
  margin-top: -32px;
  padding-top: 16px;
  position: relative;
  z-index: 1;
}

.rd-markdown-mask :deep(.arco-btn) {
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid var(--ai-border);
  color: var(--ai-ink-1);
  font-weight: 600;
}

@media (max-width: 1180px) {
  .rd-layout {
    grid-template-columns: 1fr;
  }
  .rd-aside {
    position: static;
  }
  .rd-aside-sticky {
    max-height: none;
    overflow: visible;
  }
}

.report-card {
  border-radius: 20px;
  box-shadow: 0 14px 32px rgba(18, 29, 46, 0.08);
  background: rgba(255, 255, 255, 0.86);
}

.report-card :deep(.arco-card-header) {
  border-bottom: none;
  padding: 22px 24px 0;
}

.report-card :deep(.arco-card-body) {
  padding: 22px 24px 24px;
}

.section-gap {
  margin-bottom: 18px;
}

.report-judgement-card {
  background: linear-gradient(180deg, rgba(255, 248, 239, 0.96), rgba(255, 255, 255, 0.96));
}

.judgement-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.judgement-tags {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  max-width: min(520px, 48%);
}

.judgement-main {
  font-size: 24px;
  font-weight: 800;
  color: #1b2940;
}

.judgement-sub {
  margin-top: 6px;
  color: #6b7280;
  font-size: 13px;
  font-weight: 700;
}

.judgement-direction {
  margin: 18px 0 0;
  padding: 16px 18px;
  border-radius: 16px;
  background: rgba(27, 41, 64, 0.05);
  color: #27364f;
  font-size: 15px;
  font-weight: 700;
  line-height: 1.8;
}

.section-block + .section-block {
  margin-top: 22px;
}

.section-eyebrow {
  margin-bottom: 12px;
  color: #7b8493;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.line-list {
  margin: 0;
  padding-left: 18px;
  color: #425066;
  line-height: 1.75;
}

.line-list.compact {
  font-size: 13px;
}

.action-grid,
.focus-grid,
.market-list,
.product-card-list {
  display: grid;
  gap: 12px;
}

.action-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.action-card,
.focus-card,
.top5-card,
.market-card,
.product-card,
.overview-tile,
.product-metric-card {
  padding: 16px;
  border-radius: 18px;
  background: rgba(248, 250, 252, 0.9);
  border: 1px solid rgba(98, 115, 142, 0.08);
}

.action-role {
  display: inline-flex;
  padding: 4px 8px;
  border-radius: 999px;
  background: rgba(47, 105, 236, 0.09);
  color: #2f69ec;
  font-size: 11px;
  font-weight: 800;
}

.action-card p {
  margin: 10px 0 0;
  color: #425066;
  line-height: 1.7;
}

.tag-cloud {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.gap-list {
  display: grid;
  gap: 10px;
}

.gap-row {
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr) 50px;
  align-items: center;
  gap: 10px;
  color: #425066;
  font-size: 13px;
  font-weight: 700;
}

.gap-bar {
  height: 8px;
  border-radius: 999px;
  background: rgba(27, 41, 64, 0.08);
  overflow: hidden;
}

.gap-bar-fill {
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #f59e0b, #ef4444);
}

.overview-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.focus-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.focus-head,
.market-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}

.focus-card h3,
.top5-body h3,
.product-card-title {
  margin: 0;
  color: #1b2940;
  font-size: 16px;
  line-height: 1.5;
}

.focus-meta,
.focus-metrics,
.top5-meta,
.product-card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 10px;
  color: #6b7280;
  font-size: 12px;
  font-weight: 700;
}

.top5-list {
  display: grid;
  gap: 12px;
}

.top5-card {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr);
  gap: 16px;
  align-items: flex-start;
}

.top5-rank {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 84px;
  border-radius: 18px;
  /* 设计稿 hero pillar 用 ink-1 黑色，避免 arcoblue → teal 渐变与全站不一致 */
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  font-size: 20px;
  font-weight: 800;
}

.top5-change-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
}

.top5-change-grid div {
  padding: 12px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.8);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.top5-change-grid span {
  color: #6b7280;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.top5-change-grid strong {
  color: #1b2940;
  font-size: 18px;
}

.market-list {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.market-title {
  color: #1b2940;
  font-size: 14px;
  font-weight: 700;
  line-height: 1.6;
}

.market-detail {
  margin-top: 8px;
  color: #697487;
  line-height: 1.65;
}

.product-card-list {
  gap: 14px;
}

.product-card-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.product-card-filter :deep(.arco-radio-button-content) {
  font-weight: 700;
}

.product-empty {
  padding: 28px 0;
  color: #7b8493;
  text-align: center;
  font-size: 13px;
}

.product-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.product-metric-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 14px;
}

.product-two-column {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-top: 16px;
}

.product-subsection {
  padding: 14px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid rgba(98, 115, 142, 0.08);
}

.payload-block {
  margin: 0;
  padding: 12px;
  border-radius: 12px;
  background: #131a27;
  color: #e9edf5;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 480px;
  overflow: auto;
  font-size: 12px;
  font-family: var(--ai-font-mono);
}

.debug-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.debug-title {
  margin-bottom: 8px;
  color: #7b8493;
  font-size: 12px;
  font-weight: 800;
}

.markdown-body :deep(*) {
  font-family: inherit;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  color: #1b2940;
}

.markdown-body :deep(p),
.markdown-body :deep(li) {
  color: #425066;
  line-height: 1.8;
}

.markdown-body :deep(pre) {
  border-radius: 12px;
  background: rgba(19, 26, 39, 0.96);
}

@media (max-width: 1080px) {
  .report-hero,
  .report-grid,
  .hero-metric-grid,
  .market-list,
  .product-two-column,
  .product-metric-grid,
  .focus-grid {
    grid-template-columns: 1fr;
  }

  .report-hero-side {
    align-items: flex-start;
  }
}

@media (max-width: 768px) {
  .report-detail-bar {
    height: auto;
    min-height: 44px;
    flex-wrap: wrap;
    padding: 8px 12px;
  }

  .report-detail-crumb {
    display: none;
  }

  .report-detail-name {
    flex: 1 1 160px;
    max-width: none;
  }

  .report-detail-spacer {
    display: none;
  }

  .report-detail-body {
    padding: 16px 12px;
  }

  .report-hero {
    margin: 0 0 16px;
    padding: 18px;
  }

  .report-title {
    font-size: 22px;
  }

  .top5-card {
    grid-template-columns: 1fr;
  }

  .top5-rank {
    min-height: 56px;
  }

  .overview-grid,
  .action-grid,
  .debug-grid,
  .top5-change-grid {
    grid-template-columns: 1fr;
  }

  .gap-row {
    grid-template-columns: 1fr;
  }
}
</style>
