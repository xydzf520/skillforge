<template>
  <div class="page-container admin-prompts-page">
    <div class="page-header">
      <div>
        <div class="page-kicker">管理后台 · 治理与合规</div>
        <h2 class="page-title">Prompt 管理</h2>
        <p class="page-subtitle">系统 Prompt 版本、默认版本、对比与回滚</p>
      </div>
      <a-space>
        <a-input-search
          v-model="searchKey"
          placeholder="按名称 / 中文别名搜索"
          allow-clear
          class="filter-search"
        />
        <a-button class="ai-btn-like" @click="loadPrompts">
          <icon-refresh /> 刷新
        </a-button>
      </a-space>
    </div>

    <a-card class="page-list-card">
      <a-spin :loading="loading">
        <a-table
          class="page-list-table"
          :data="filteredPrompts"
          :pagination="false"
          row-key="name"
        >
          <template #columns>
            <a-table-column title="Prompt 名称" data-index="name">
              <template #cell="{ record }">
                <div>
                  <span class="prompt-name">{{ record.name }}</span>
                  <div v-if="PROMPT_ALIAS[record.name]" class="prompt-alias">{{ PROMPT_ALIAS[record.name] }}</div>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="默认版本" :width="120">
              <template #cell="{ record }">
                <a-tag color="arcoblue">{{ record.default_version }}</a-tag>
              </template>
            </a-table-column>
            <a-table-column title="版本数" :width="80">
              <template #cell="{ record }">
                <span class="version-count">{{ record.versions.length }}</span>
              </template>
            </a-table-column>
            <a-table-column title="当前 hash" :width="140">
              <template #cell="{ record }">
                <code class="hash-cell">
                  {{ getCurrentHash(record).slice(0, 12) }}
                </code>
              </template>
            </a-table-column>
            <a-table-column title="预览" :ellipsis="true">
              <template #cell="{ record }">
                <span class="content-preview">
                  {{ getCurrentPreview(record) }}
                </span>
              </template>
            </a-table-column>
            <a-table-column title="操作" :width="180">
              <template #cell="{ record }">
                <a-space>
                  <a-button type="text" size="small" @click="viewDetail(record.name)">
                    详情
                  </a-button>
                  <a-button
                    v-if="record.versions.length > 1"
                    type="text"
                    size="small"
                    @click="openSwitchModal(record)"
                  >
                    切换版本
                  </a-button>
                </a-space>
              </template>
            </a-table-column>
          </template>
        </a-table>
      </a-spin>
    </a-card>

    <!-- 详情抽屉 -->
    <a-drawer
      :visible="detailVisible"
      :width="'min(90vw, 800px)'"
      :title="`Prompt: ${detail?.name || ''}`"
      @cancel="detailVisible = false"
      :footer="false"
    >
      <div v-if="detail">
        <div class="detail-meta">
          <span>默认版本: <a-tag color="arcoblue">{{ detail.default_version }}</a-tag></span>
          <span>共 {{ detail.versions.length }} 个版本</span>
          <span v-if="detail.versions.length > 1" class="detail-compare">
            <a-checkbox v-model="compareMode">版本对比</a-checkbox>
            <a-select
              v-if="compareMode"
              v-model="compareBaseline"
              placeholder="基准版本"
              size="small"
              class="version-select"
            >
              <a-option
                v-for="v in detail.versions"
                :key="v.version"
                :value="v.version"
              >
                {{ v.version }}
              </a-option>
            </a-select>
          </span>
        </div>

        <a-tabs v-model:active-key="activeVersion">
          <a-tab-pane
            v-for="v in detail.versions"
            :key="v.version"
            :title="`${v.version}${v.version === detail.default_version ? ' (默认)' : ''}`"
          >
            <div class="version-meta">
              <code class="hash-block">hash: {{ v.hash }}</code>
              <span v-if="v.description" class="version-desc">{{ v.description }}</span>
              <a-button
                v-if="v.version !== detail.default_version"
                size="mini"
                type="primary"
                status="warning"
                :loading="rollbackLoading"
                @click="rollbackTo(v.version)"
              >
                回滚为默认
              </a-button>
            </div>
            <div v-if="compareMode && compareBaseline && compareBaseline !== v.version" class="compare-grid">
              <div class="compare-pane">
                <div class="compare-pane-head">基准 {{ compareBaseline }}</div>
                <pre class="prompt-content">{{ getVersionContent(compareBaseline) }}</pre>
              </div>
              <div class="compare-pane">
                <div class="compare-pane-head">当前 {{ v.version }}</div>
                <pre class="prompt-content">{{ v.content }}</pre>
              </div>
            </div>
            <pre v-else class="prompt-content">{{ v.content }}</pre>
          </a-tab-pane>
        </a-tabs>
      </div>
    </a-drawer>

    <!-- 切换版本 Modal -->
    <a-modal
      :visible="switchModalVisible"
      :title="`切换 ${switchTarget?.name || ''} 默认版本`"
      @cancel="switchModalVisible = false"
      @ok="confirmSwitch"
      :ok-loading="switching"
    >
      <a-form :model="switchForm" auto-label-width>
        <a-form-item label="当前默认">
          <a-tag color="arcoblue">{{ switchTarget?.default_version }}</a-tag>
        </a-form-item>
        <a-form-item label="切换为">
          <a-select v-model="switchForm.version" placeholder="选择新版本">
            <a-option
              v-for="v in (switchTarget?.versions || [])"
              :key="v.version"
              :value="v.version"
              :disabled="v.version === switchTarget?.default_version"
            >
              {{ v.version }} ({{ v.hash.slice(0, 8) }})
            </a-option>
          </a-select>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconRefresh } from '@arco-design/web-vue/es/icon'
import { promptApi as rawPromptApi } from '@/api'

// Prompt 技术名 → 中文别名映射（便于非工程师理解）
const PROMPT_ALIAS: Record<string, string> = {
  agent_chat: 'Skill 测试助手',
  architect: 'Skill 创建向导（任务契约）',
  coding_agent_browser_policy: '浏览器采集权限策略',
  coding_agent_explain: 'Skill 讲解模式',
  coding_agent_review: 'Skill 审批模式',
  coding_agent_skill_creation: 'Skill 编写指引',
  coding_agent_todo_contract: '待办协议约束',
  coding_agent_test_runner: '测试骨架生成',
  intent_extract: '用户意图抽取',
  execution_summary: '执行结果摘要',
  error_diagnose: '执行失败归因',
}

const searchKey = ref('')

const promptApi: any = rawPromptApi
const loading = ref(false)
const prompts = ref<any[]>([])
const filteredPrompts = computed(() => {
  const k = searchKey.value.trim().toLowerCase()
  if (!k) return prompts.value
  return prompts.value.filter((p: any) => {
    const name = String(p.name || '').toLowerCase()
    const alias = (PROMPT_ALIAS[p.name] || '').toLowerCase()
    return name.includes(k) || alias.includes(k)
  })
})
const detail = ref<any>(null)
const detailVisible = ref(false)
const activeVersion = ref('')

const switchTarget = ref<any>(null)
const switchModalVisible = ref(false)
const switchForm = ref({ version: '' })
const switching = ref(false)

// 版本对比 + 回滚
const compareMode = ref(false)
const compareBaseline = ref<string>('')
const rollbackLoading = ref(false)

function getVersionContent(version: string): string {
  if (!detail.value) return ''
  return detail.value.versions.find((v: any) => v.version === version)?.content || ''
}

async function rollbackTo(version: string) {
  if (!detail.value) return
  rollbackLoading.value = true
  try {
    await promptApi.setDefault(detail.value.name, version)
    Message.success(`已回滚 ${detail.value.name} 默认版本为 ${version}`)
    // 更新本地视图
    detail.value.default_version = version
    await loadPrompts()
  } catch (e: any) {
    Message.error(e?._message || '回滚失败')
  } finally {
    rollbackLoading.value = false
  }
}

async function loadPrompts() {
  loading.value = true
  try {
    const r = await promptApi.list()
    prompts.value = r.items || []
  } catch (e: any) {
    Message.error(e._message || '加载 Prompt 列表失败')
  } finally {
    loading.value = false
  }
}

function getCurrentHash(record: any) {
  const v = (record.versions || []).find((v: Record<string, unknown>) => v.version === record.default_version)
  return v?.hash || ''
}

function getCurrentPreview(record: any) {
  const v = (record.versions || []).find((v: Record<string, unknown>) => v.version === record.default_version)
  // 优先用完整 content（列表靠 ellipsis 视觉截断）；后端没返 content 时兜底用 content_preview
  return (v?.content as string) || (v?.content_preview as string) || ''
}

async function viewDetail(name: string) {
  try {
    const r = await promptApi.get(name)
    detail.value = r
    activeVersion.value = r.default_version
    compareBaseline.value = r.default_version
    compareMode.value = false
    detailVisible.value = true
  } catch (e: any) {
    Message.error(e._message || '加载详情失败')
  }
}

function openSwitchModal(record: any) {
  switchTarget.value = record
  switchForm.value = { version: '' }
  switchModalVisible.value = true
}

async function confirmSwitch() {
  if (!switchForm.value.version) {
    Message.warning('请选择新版本')
    return
  }
  switching.value = true
  try {
    await promptApi.setDefault(switchTarget.value.name, switchForm.value.version)
    Message.success(`已切换 ${switchTarget.value.name} 默认版本为 ${switchForm.value.version}`)
    switchModalVisible.value = false
    await loadPrompts()
  } catch (e: any) {
    Message.error(e._message || '切换失败')
  } finally {
    switching.value = false
  }
}

onMounted(loadPrompts)
</script>

<style scoped>
/* 设计稿 page chrome 覆盖 —— 与 AdminUsers / SkillList 同模式 */
.admin-prompts-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.admin-prompts-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  margin: 0;
}
.admin-prompts-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.admin-prompts-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}

/* 顶部按钮统一外形 —— 30px / 6px 圆角 / 12.5px 字号，对应 .ai-btn */
.admin-prompts-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary)) {
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
.admin-prompts-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary):hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}

/* 表头 —— 11.5px / uppercase / ink-4 */
.admin-prompts-page :deep(.arco-table-th) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 8px 12px !important;
}
.admin-prompts-page :deep(.arco-table-th .arco-table-th-title) {
  color: var(--ai-ink-4);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* 单元格 —— 12.5px / ink-1 / ai-border 底分隔线 */
.admin-prompts-page :deep(.arco-table-td) {
  font-size: 12.5px !important;
  color: var(--ai-ink-1) !important;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 10px 12px !important;
  background: var(--ai-surface) !important;
}

/* 行 hover：surface-2 */
.admin-prompts-page :deep(.arco-table-tr:hover .arco-table-td),
.admin-prompts-page :deep(.arco-table-tr-hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

/* tag pills —— 20px 高 / 4px 方角 / 11px/500 —— 按 arcoblue / green / red / orange / gray 五档 */
.admin-prompts-page :deep(.arco-tag.arco-tag-size-small),
.admin-prompts-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
  font-family: var(--ai-font-mono);
}
.admin-prompts-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft) !important;
  color: var(--ai-info) !important;
  border-color: transparent !important;
}
.admin-prompts-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft) !important;
  color: var(--ai-ok) !important;
  border-color: transparent !important;
}
.admin-prompts-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft) !important;
  color: var(--ai-bad) !important;
  border-color: transparent !important;
}
.admin-prompts-page :deep(.arco-tag-color-orange),
.admin-prompts-page :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft) !important;
  color: var(--ai-warn) !important;
  border-color: transparent !important;
}
.admin-prompts-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-2) !important;
  border-color: transparent !important;
}

/* 输入框 / 搜索框 —— 30px / 6px 圆角，对齐 .ai-input */
.admin-prompts-page :deep(.arco-input-wrapper),
.admin-prompts-page :deep(.arco-input-search) {
  border-radius: 6px;
  border-color: var(--ai-border);
  background: var(--ai-surface);
}
.admin-prompts-page :deep(.arco-input-wrapper .arco-input) {
  font-size: 12.5px;
  color: var(--ai-ink-1);
}

/* Prompt 名称：mono，强化技术名属性 */
.prompt-name {
  font-family: var(--ai-font-mono);
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ai-ink-1);
}
.prompt-alias {
  font-size: 11.5px;
  color: var(--ai-ink-4);
  margin-top: 2px;
}
.version-count {
  font-family: var(--ai-font-mono);
  font-size: 12.5px;
  color: var(--ai-ink-2);
  font-variant-numeric: tabular-nums;
}

/* hash 单元格 —— mono / surface-2 底 / 4px 方角 */
.hash-cell {
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid var(--ai-border);
}

/* 内容预览 —— ink-3，标准 12px */
.content-preview {
  color: var(--ai-ink-3);
  font-size: 12px;
}

/* 抽屉里的 meta 行 */
.detail-meta {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 12px 0;
  font-size: 12.5px;
  color: var(--ai-ink-2);
}
.version-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 12px 0;
}

/* hash block —— 同 hash-cell 风格但留更多 padding 给详情面板使用 */
.hash-block {
  font-family: var(--ai-font-mono);
  font-size: 12px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  padding: 4px 8px;
  border-radius: 4px;
  border: 1px solid var(--ai-border);
}
.version-desc {
  color: var(--ai-ink-4);
  font-size: 12px;
}

/* Prompt 正文 —— mono / surface-2 卡片 */
.prompt-content {
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  padding: 16px;
  border-radius: var(--ai-radius);
  font-family: var(--ai-font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: var(--ai-ink-1);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 60vh;
  overflow-y: auto;
}
.detail-compare {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}
.compare-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.compare-pane {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.compare-pane-head {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--ai-ink-3);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* Admin sweep utilities */
.filter-search {
  width: 260px;
}
.version-select {
  width: 110px;
}
@media (max-width: 900px) {
  .filter-search,
  .version-select {
    width: 100%;
  }
}

</style>
