<template>
  <div class="page-container admin-compliance-page">
    <div class="page-header">
      <div>
        <div class="page-kicker">管理后台 · 治理与合规</div>
        <h2 class="page-title">合规规则</h2>
        <p class="page-subtitle">敏感词与禁用表达 · Skill 输出执行前自动校验</p>
      </div>
      <a-space>
        <a-button class="ai-btn-like" @click="openTestModal">规则测试</a-button>
        <a-button class="ai-btn-like" @click="handleExport">导出</a-button>
        <a-upload :action="'/api/compliance/import'" :with-credentials="true" accept=".csv"
          :show-file-list="false" @success="loadRules">
          <a-button class="ai-btn-like">导入CSV</a-button>
        </a-upload>
        <a-button class="ai-btn-like primary" type="primary" @click="openCreateModal"><icon-plus /> 新建规则</a-button>
      </a-space>
    </div>

    <a-card class="page-list-card">
      <div class="filter-bar"><a-space>
        <a-select v-model="filters.platform" placeholder="平台" allow-clear class="filter-select-sm" @change="loadRules">
          <a-option value="淘宝">淘宝</a-option>
          <a-option value="京东">京东</a-option>
          <a-option value="抖音">抖音</a-option>
        </a-select>
        <a-select v-model="filters.category" placeholder="类别" allow-clear class="filter-select-sm" @change="loadRules">
          <a-option value="投放">投放</a-option>
          <a-option value="促销">促销</a-option>
          <a-option value="定价">定价</a-option>
        </a-select>
      </a-space></div>

      <a-spin :loading="loading">
        <a-table class="page-list-table" :scroll="{ x: '100%' }" :data="rules" :pagination="false" row-key="id">
          <template #empty>
            <SfEmptyState
              title="合规规则"
              description="暂无合规规则"
              hint="合规规则会在 Skill 执行前检查输出文本中的敏感词 / 禁用表达。点击右上「新建规则」或「点击上传」批量导入。"
              action-label="新建规则"
              @action="openCreateModal"
            />
          </template>
          <template #columns>
            <a-table-column title="规则ID" data-index="id">
              <template #cell="{ record }">
                <span class="rule-id-cell">{{ record.id }}</span>
              </template>
            </a-table-column>
            <a-table-column title="平台" data-index="platform">
              <template #cell="{ record }">
                <span class="dim-cell">{{ record.platform || '-' }}</span>
              </template>
            </a-table-column>
            <a-table-column title="投放面" data-index="surface">
              <template #cell="{ record }">
                <span class="dim-cell">{{ record.surface || '-' }}</span>
              </template>
            </a-table-column>
            <a-table-column title="匹配规则" data-index="pattern_value">
              <template #cell="{ record }">
                <span class="pattern-cell">{{ record.pattern_value || '-' }}</span>
              </template>
            </a-table-column>
            <a-table-column title="状态" :width="80">
              <template #cell="{ record }">
                <a-tag size="small" :color="record.is_active ? 'green' : 'gray'">{{ record.is_active ? '启用' : '停用' }}</a-tag>
              </template>
            </a-table-column>
            <a-table-column title="操作" :width="170">
              <template #cell="{ record }">
                <a-space>
                  <a-button type="text" size="small" @click="editRule(record)">编辑</a-button>
                  <a-button type="text" size="small" @click="openHistory(record)">历史</a-button>
                  <a-popconfirm content="确定删除？" @ok="deleteRule(record.id)">
                    <a-button type="text" size="small" status="danger">删除</a-button>
                  </a-popconfirm>
                </a-space>
              </template>
            </a-table-column>
          </template>
        </a-table>
      </a-spin>

      <div class="bottom-bar">
        <div class="stats-inline">
          <span class="stat-chip">总规则 <b>{{ rules.length }}</b></span>
          <span class="stat-chip stat-green">启用 <b>{{ rules.filter(r => r.is_active).length }}</b></span>
          <span class="stat-chip stat-red">停用 <b>{{ rules.filter(r => !r.is_active).length }}</b></span>
        </div>
        <a-pagination :current="pagination.current" :page-size="pagination.pageSize" :total="pagination.total" @change="onPageChange" size="small" />
      </div>
    </a-card>

    <a-modal v-model:visible="showCreate" :title="editingRule ? '编辑规则' : '新建规则'" @ok="handleSave" :ok-loading="saving">
      <a-form :model="ruleForm" layout="vertical">
        <a-form-item label="规则ID" required><a-input v-model="ruleForm.id" :disabled="!!editingRule" /></a-form-item>
        <a-form-item label="平台" required><a-input v-model="ruleForm.platform" /></a-form-item>
        <a-form-item label="投放面" required><a-input v-model="ruleForm.surface" /></a-form-item>
        <a-form-item label="触发类型" required><a-input v-model="ruleForm.trigger_type" /></a-form-item>
        <a-form-item label="匹配规则" required><a-textarea v-model="ruleForm.pattern_value" :auto-size="{ minRows: 2 }" /></a-form-item>
        <a-form-item label="严重程度"><a-select v-model="ruleForm.severity">
          <a-option value="low">低</a-option>
          <a-option value="medium">中</a-option>
          <a-option value="high">高</a-option>
        </a-select></a-form-item>
        <a-form-item label="决策"><a-input v-model="ruleForm.decision" /></a-form-item>
      </a-form>
    </a-modal>

    <!-- 规则测试对话框：粘贴文本 → /compliance/check 命中报告 -->
    <a-modal v-model:visible="showTest" title="规则测试" :footer="false" :width="'min(90vw, 640px)'">
      <a-form :model="testForm" layout="vertical">
        <a-form-item label="平台">
          <a-select v-model="testForm.platform" placeholder="不指定则全平台" allow-clear>
            <a-option value="淘宝">淘宝</a-option>
            <a-option value="京东">京东</a-option>
            <a-option value="抖音">抖音</a-option>
            <a-option value="拼多多">拼多多</a-option>
          </a-select>
        </a-form-item>
        <a-form-item label="待测试文案" required>
          <a-textarea v-model="testForm.content" :auto-size="{ minRows: 5, maxRows: 10 }"
                      placeholder="粘贴需要校验的标题/详情/视频文案..." />
        </a-form-item>
        <a-button type="primary" :loading="testing" @click="runTest">立即检查</a-button>
      </a-form>
      <div v-if="testResult" class="test-result">
        <a-divider />
        <div class="test-summary">
          命中规则：<strong>{{ testResult.violation_count }}</strong> 条 ·
          内容长度：{{ testResult.content_length }}
        </div>
        <a-list v-if="testResult.violations?.length" :data="testResult.violations" :bordered="false" size="small">
          <template #item="{ item }">
            <a-list-item>
              <a-space direction="vertical" size="mini">
                <span><a-tag size="small" :color="severityColor(item.severity)">{{ item.severity || 'P?' }}</a-tag>
                  <strong class="hit-rule-id">{{ item.rule_id || item.id }}</strong> · {{ item.decision || '—' }}</span>
                <span class="test-pattern">{{ item.pattern_value || item.matched }}</span>
              </a-space>
            </a-list-item>
          </template>
        </a-list>
        <SfEmptyState v-else title="规则测试" description="未命中任何规则" hint="当前测试文本没有触发敏感词或禁用表达。" icon="shield" />
      </div>
    </a-modal>

    <!-- 历史版本抽屉 -->
    <a-drawer
      :visible="historyVisible"
      :width="'min(90vw, 620px)'"
      :title="`版本历史 · ${historyTarget?.id || ''}`"
      @cancel="historyVisible = false"
      :footer="false"
    >
      <a-spin :loading="historyLoading">
        <SfEmptyState v-if="!historyVersions.length" title="版本历史" description="暂无历史版本" hint="规则被编辑更新后会自动产生可回滚的版本记录。" icon="archive" />
        <div v-else class="history-list">
          <div v-for="v in historyVersions" :key="v.id" class="history-item">
            <div class="history-item-head">
              <a-tag size="small" color="arcoblue">v{{ v.version_no }}</a-tag>
              <span class="history-time">{{ v.created_at }}</span>
              <span class="history-author">by {{ v.author || '-' }}</span>
              <a-popconfirm
                :content="`确定将规则回滚到 v${v.version_no}？会产生一条新的版本。`"
                @ok="doRollback(v.version_no)"
              >
                <a-button size="mini" status="warning">回滚为当前</a-button>
              </a-popconfirm>
            </div>
            <div class="history-item-body">
              <span v-if="v.snapshot?.platform" class="history-kv">platform: <code>{{ v.snapshot.platform }}</code></span>
              <span v-if="v.snapshot?.surface" class="history-kv">surface: <code>{{ v.snapshot.surface }}</code></span>
              <span v-if="v.snapshot?.trigger_type" class="history-kv">trigger: <code>{{ v.snapshot.trigger_type }}</code></span>
              <span v-if="v.snapshot?.pattern_value" class="history-kv">pattern: <code>{{ truncate(v.snapshot.pattern_value, 40) }}</code></span>
              <span v-if="v.snapshot?.severity" class="history-kv">severity: <code>{{ v.snapshot.severity }}</code></span>
              <span v-if="v.snapshot?.decision" class="history-kv">decision: <code>{{ v.snapshot.decision }}</code></span>
              <span v-if="v.snapshot?.category_scope" class="history-kv">scope: <code>{{ v.snapshot.category_scope }}</code></span>
              <span v-if="v.snapshot?.owner" class="history-kv">owner: <code>{{ v.snapshot.owner }}</code></span>
            </div>
            <div v-if="v.reason" class="history-reason">备注：{{ v.reason }}</div>
          </div>
        </div>
      </a-spin>
    </a-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconPlus } from '@arco-design/web-vue/es/icon'
import { complianceApi as rawComplianceApi } from '@/api'
import { SfEmptyState } from '@/components/common'

type ComplianceRule = {
  id: string
  platform?: string
  surface?: string
  trigger_type?: string
  pattern_value?: string
  severity?: string
  decision?: string
  is_active?: boolean
}

const complianceApi: any = rawComplianceApi
const loading = ref(false)
const saving = ref(false)
const rules = ref<ComplianceRule[]>([])
const showCreate = ref(false)
const editingRule = ref<ComplianceRule | null>(null)

// 版本历史抽屉
const historyVisible = ref(false)
const historyLoading = ref(false)
const historyTarget = ref<ComplianceRule | null>(null)
const historyVersions = ref<any[]>([])

async function openHistory(rule: ComplianceRule) {
  historyTarget.value = rule
  historyVisible.value = true
  historyLoading.value = true
  try {
    const res = await complianceApi.versions(rule.id)
    historyVersions.value = (res as any)?.items || []
  } catch (e: any) {
    Message.error(e?._message || '加载历史版本失败')
    historyVersions.value = []
  } finally {
    historyLoading.value = false
  }
}

function truncate(s: string, n: number): string {
  if (!s) return ''
  return s.length > n ? `${s.slice(0, n)}…` : s
}

async function doRollback(versionNo: number) {
  if (!historyTarget.value) return
  try {
    await complianceApi.rollback(historyTarget.value.id, versionNo)
    Message.success(`已回滚到 v${versionNo}`)
    await loadRules()
    // 重载历史（会多出来一条）
    await openHistory(historyTarget.value)
  } catch (e: any) {
    Message.error(e?._message || '回滚失败')
  }
}
const filters = reactive<{ platform?: string; category?: string }>({ platform: undefined, category: undefined })
const pagination = reactive({ current: 1, pageSize: 20, total: 0 })
const ruleForm = reactive({ id: '', platform: '', surface: '', trigger_type: '', pattern_value: '', severity: 'medium', decision: '' })

async function loadRules() {
  loading.value = true
  try {
    const res = await complianceApi.list({ ...filters, page: pagination.current })
    rules.value = res.items || (Array.isArray(res) ? res : [])
    if (res.total !== undefined) pagination.total = res.total
  } finally { loading.value = false }
}

function editRule(r: ComplianceRule) {
  editingRule.value = r
  Object.assign(ruleForm, { id: r.id, platform: r.platform, surface: r.surface, trigger_type: r.trigger_type, pattern_value: r.pattern_value, severity: r.severity || 'medium', decision: r.decision || '' })
  showCreate.value = true
}

function openCreateModal() {
  editingRule.value = null
  Object.assign(ruleForm, { id: '', platform: '', surface: '', trigger_type: '', pattern_value: '', severity: 'medium', decision: '' })
  showCreate.value = true
}

async function handleSave() {
  saving.value = true
  try {
    if (editingRule.value) {
      await complianceApi.update(editingRule.value.id, ruleForm)
    } else {
      await complianceApi.create(ruleForm)
    }
    showCreate.value = false
    editingRule.value = null
    Message.success('保存成功')
    await loadRules()
  } catch (e: any) { Message.error(e._message || '操作失败') }
  finally { saving.value = false }
}

async function deleteRule(id: string) {
  try {
    await complianceApi.delete(id)
    Message.success('已删除')
    await loadRules()
  } catch (e: any) { Message.error(e._message || '删除失败') }
}

async function handleExport() {
  try {
    const res = await complianceApi.export(filters)
    const blob = res.data
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const disposition = res.headers['content-disposition']
    const match = disposition?.match(/filename="?(.+?)"?$/)
    a.download = match ? match[1] : 'compliance_rules.csv'
    a.click()
    URL.revokeObjectURL(url)
  } catch (e: any) { Message.error(e._message || '导出失败') }
}

function onPageChange(page: number) { pagination.current = page; loadRules() }

// ── 规则测试 ──
const showTest = ref(false)
const testing = ref(false)
const testResult = ref<any>(null)
const testForm = reactive<{ content: string; platform?: string }>({ content: '', platform: undefined })

function openTestModal() {
  testResult.value = null
  testForm.content = ''
  testForm.platform = undefined
  showTest.value = true
}

async function runTest() {
  if (!testForm.content.trim()) {
    Message.warning('请输入待测试文案')
    return
  }
  testing.value = true
  try {
    testResult.value = await complianceApi.checkContent({
      content: testForm.content,
      platform: testForm.platform || null,
    })
  } catch (e: any) {
    Message.error(e._message || '检查失败')
  } finally { testing.value = false }
}

function severityColor(s: string | undefined) {
  if (s === 'P0' || s === 'high') return 'red'
  if (s === 'P1' || s === 'medium') return 'orange'
  return 'gray'
}

onMounted(loadRules)
</script>

<style scoped>
/* 设计稿 page chrome 覆盖 —— 与 AdminUsers / SystemSettings 同模式 */
.admin-compliance-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.admin-compliance-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  margin: 0;
}
.admin-compliance-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.admin-compliance-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}

/* 顶部按钮统一外形 —— 30px / 6px / 12.5px，对应 .ai-btn */
.admin-compliance-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary)) {
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
.admin-compliance-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary):hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
.admin-compliance-page :deep(.arco-btn-primary.ai-btn-like) {
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
.admin-compliance-page :deep(.arco-btn-primary.ai-btn-like:hover) {
  background: var(--ai-ink-2);
  border-color: var(--ai-ink-2);
}

/* 表头 —— 11.5px / uppercase / ink-4 */
.admin-compliance-page :deep(.arco-table-th) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 8px 12px !important;
}
.admin-compliance-page :deep(.arco-table-th .arco-table-th-title) {
  color: var(--ai-ink-4);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* 单元格 —— 12.5px / ink-1 */
.admin-compliance-page :deep(.arco-table-td) {
  font-size: 12.5px !important;
  color: var(--ai-ink-1) !important;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 10px 12px !important;
  background: var(--ai-surface) !important;
}

/* 行 hover —— surface-2 */
.admin-compliance-page :deep(.arco-table-tr:hover .arco-table-td),
.admin-compliance-page :deep(.arco-table-tr-hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

/* tag pills —— 20px / 4px / 11px/500 五档配色 */
.admin-compliance-page :deep(.arco-tag.arco-tag-size-small),
.admin-compliance-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
}
.admin-compliance-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft) !important;
  color: var(--ai-info) !important;
  border-color: transparent !important;
}
.admin-compliance-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft) !important;
  color: var(--ai-ok) !important;
  border-color: transparent !important;
}
.admin-compliance-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft) !important;
  color: var(--ai-bad) !important;
  border-color: transparent !important;
}
.admin-compliance-page :deep(.arco-tag-color-orange),
.admin-compliance-page :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft) !important;
  color: var(--ai-warn) !important;
  border-color: transparent !important;
}
.admin-compliance-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-2) !important;
  border-color: transparent !important;
}

/* 筛选区 */
.filter-bar {
  display: flex;
  align-items: center;
  margin-bottom: 14px;
  flex-wrap: wrap;
  gap: 8px;
}

/* 规则单元格 —— 规则ID / 平台 / surface 用 mono；pattern 普通 */
.rule-id-cell {
  font-family: var(--ai-font-mono);
  font-size: 12px;
  color: var(--ai-ink-1);
  font-weight: 500;
}
.dim-cell {
  font-size: 12.5px;
  color: var(--ai-ink-2);
}
.pattern-cell {
  font-family: var(--ai-font-mono);
  font-size: 12px;
  color: var(--ai-ink-2);
  word-break: break-all;
}

/* 历史版本列表 —— ai-card + hover border-2 */
.history-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.history-item {
  padding: 12px 14px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.history-item:hover {
  border-color: var(--ai-border-2);
  box-shadow: var(--ai-shadow-1);
}
.history-item-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.history-item-head > :last-child { margin-left: auto; }
.history-time {
  font-size: 11.5px;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
}
.history-author {
  font-size: 12px;
  color: var(--ai-ink-3);
}
.history-item-body {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 12px;
}
.history-kv {
  color: var(--ai-ink-3);
  font-size: 11.5px;
}
.history-kv code {
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  padding: 1px 6px;
  border-radius: 4px;
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-1);
  font-size: 11px;
}
.history-reason {
  margin-top: 8px;
  font-size: 12px;
  color: var(--ai-ink-3);
  font-style: italic;
}

/* 规则测试结果 */
.test-result { margin-top: 12px; }
.test-summary {
  margin-bottom: 8px;
  color: var(--ai-ink-2);
  font-size: 12.5px;
}
.test-pattern {
  color: var(--ai-ink-3);
  font-size: 11.5px;
  font-family: var(--ai-font-mono);
}
.hit-rule-id {
  font-family: var(--ai-font-mono);
  font-size: 12px;
  color: var(--ai-ink-1);
}

/* 底部统计条 —— 与 AdminUsers 同模式（ai-pill 风格） */
.bottom-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--ai-border);
}
.stats-inline {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.stat-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  color: var(--ai-ink-2);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  cursor: default;
}
.stat-chip b {
  font-size: 11px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.stat-green {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}
.stat-red {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}
.stat-orange {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
.stat-blue {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  border-color: transparent;
}

/* Admin sweep utilities */
.filter-select-sm {
  width: 120px;
}
@media (max-width: 900px) {
  .admin-compliance-page .filter-bar :deep(.arco-space),
  .admin-compliance-page .filter-bar :deep(.arco-space-item),
  .filter-select-sm {
    width: 100%;
  }
  .bottom-bar {
    align-items: flex-start;
  }
}

</style>
