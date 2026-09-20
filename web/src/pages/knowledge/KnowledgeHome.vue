<template>
  <div class="knowledge-page ai-main">
    <div class="knowledge-head ai-pagehead">
      <div>
        <div class="ai-crumbs">组织知识 · 部门知识库</div>
        <h1 class="ai-title">知识库</h1>
        <p class="ai-sub">每个部门自动拥有独立知识库，支持文本、图片、Skill 和数据资产的多模态混合检索</p>
      </div>
      <div class="knowledge-actions">
        <button class="ai-btn" type="button" :disabled="loading" @click="refreshAll">
          <IconRefresh />
          刷新
        </button>
        <button class="ai-btn" type="button" :disabled="!selectedBase?.can_write || uploading" @click="triggerUpload">
          <IconUpload />
          上传
        </button>
        <button class="ai-btn" type="button" :disabled="!selectedBase?.can_write || importing" @click="openImportUrl">
          <IconLink />
          导入链接
        </button>
        <button class="ai-btn" type="button" :disabled="!selectedBase?.can_write || syncing" @click="syncAssets">
          <IconSync />
          同步平台资产
        </button>
        <button class="ai-btn primary" type="button" :disabled="!selectedBase?.can_write" @click="openCreate">
          <IconPlus />
          新增文档
        </button>
        <input ref="uploadInput" class="upload-input" type="file" accept=".txt,.md,.markdown,.html,.htm,.csv,.json,.yaml,.yml,.xml,.docx,.pdf,.png,.jpg,.jpeg,.webp,.gif,text/*,application/json,image/*" @change="handleUpload" />
        <input ref="imageSearchInput" class="upload-input" type="file" accept=".png,.jpg,.jpeg,.webp,.gif,image/*" @change="handleImageSearch" />
      </div>
    </div>

    <nav class="knowledge-section-tabs ai-tabs" aria-label="知识库二级功能">
      <button
        v-for="item in sectionTabs"
        :key="item.key"
        type="button"
        class="knowledge-section-tab ai-tab"
        :class="{ active: activeSection === item.key }"
        :aria-current="activeSection === item.key ? 'page' : undefined"
        @click="jumpToSection(item.key)"
      >
        {{ item.label }}
        <span v-if="item.count !== undefined" class="knowledge-section-tab-count">{{ item.count }}</span>
      </button>
    </nav>

    <div class="knowledge-body ai-pagebody">
      <aside class="knowledge-sidebar ai-card" aria-label="部门知识库列表">
        <div class="ai-side-label">部门知识库</div>
        <button
          v-for="base in bases"
          :key="base.id"
          class="ai-side-item knowledge-side-item"
          :class="{ active: base.id === selectedBaseId }"
          type="button"
          @click="selectBase(base.id)"
        >
          <IconBranch class="ic" />
          <span class="side-name">{{ base.department }}</span>
          <span class="count">{{ safeNumber(base.stats?.document_count) }}</span>
        </button>
        <div class="knowledge-sidebar-note">
          <span class="note-label">当前索引</span>
          <strong>{{ storageLabel }}</strong>
          <span>{{ safeNumber(selectedBase?.stats?.chunk_count) || chunkCount }} chunks · 权限按部门继承</span>
        </div>
      </aside>

      <main class="knowledge-main">
        <section id="knowledge-overview" class="knowledge-metrics" aria-label="知识库关键指标">
          <article
            v-for="card in metricCards"
            :key="card.label"
            class="knowledge-metric ai-card"
            :data-tone="card.tone"
          >
            <span class="metric-label">{{ card.label }}</span>
            <strong class="metric-value">{{ card.value }}</strong>
            <span class="metric-hint">{{ card.hint }}</span>
          </article>
        </section>

        <section id="knowledge-search" class="knowledge-card ai-card query-panel">
          <div class="ai-card-h knowledge-card-head">
            <div>
              <div class="t">检索与问答</div>
              <div class="s">搜索、提问和生成可直接传给 Skill / Agent 的上下文</div>
            </div>
            <span class="ai-pill accent">LightRAG multimodal hybrid</span>
          </div>
          <div class="card-body query-grid">
            <div class="query-compose">
              <div class="query-label">输入业务问题</div>
              <a-input-search
                v-model="query"
                placeholder="搜索 SOP、Skill、数据资产说明"
                allow-clear
                size="large"
                @search="runSearch"
                @press-enter="runSearch"
              />
              <div class="query-filters">
                <span class="filter-label">模态</span>
                <button
                  v-for="item in modalityOptions"
                  :key="item.value"
                  class="mode-chip"
                  :class="{ active: mediaMode === item.value }"
                  type="button"
                  @click="mediaMode = item.value"
                >
                  {{ item.label }}
                </button>
                <button class="ai-btn sm image-search-btn" type="button" :disabled="imageSearching" @click="triggerImageSearch">
                  <IconImage />
                  以图搜图
                </button>
              </div>
              <div class="query-actions">
                <button class="ai-btn" type="button" :disabled="!query.trim() || searching" @click="runSearch">
                  <IconSearch />
                  搜索
                </button>
                <button class="ai-btn primary" type="button" :disabled="!query.trim() || asking" @click="runAsk">
                  <IconRobot />
                  提问
                </button>
                <button class="ai-btn" type="button" :disabled="!query.trim() || contextLoading" @click="runContext">
                  <IconCode />
                  上下文
                </button>
              </div>
              <div class="query-help">
                <span>召回范围：{{ selectedBase?.department || '当前部门' }}</span>
                <span>top_k 8</span>
                <span>{{ modalityLabel(mediaMode) }}</span>
                <span>{{ documentRows.length }} docs · {{ mediaCount }} images</span>
              </div>
            </div>

            <a-spin :loading="searching || asking || contextLoading || imageSearching" class="query-output">
              <div v-if="answer" class="answer-box">
                <div class="answer-head">
                  <span>回答</span>
                  <span class="ai-pill">{{ answerModeText }}</span>
                </div>
                <p>{{ answer }}</p>
              </div>
              <div v-if="promptContext" class="context-box">
                <div class="answer-head">
                  <span>调用上下文</span>
                  <button class="mini-link" type="button" @click="copyPromptContext">
                    <IconCopy />
                    复制
                  </button>
                </div>
                <pre>{{ promptContext }}</pre>
                <div class="result-meta">
                  <span>{{ contextChars }} chars</span>
                  <span>{{ contextSources.length }} sources</span>
                </div>
              </div>
              <div v-if="searchHits.length" class="result-list">
                <article v-for="hit in searchHits" :key="`${hit.document_id}-${hit.chunk_id || hit.score}`" class="result-item">
                  <header>
                    <span class="result-title">{{ hit.document_title }}</span>
                    <span class="ai-pill">{{ hit.department }}</span>
                    <span class="ai-pill" :class="hit.modality === 'image' ? 'accent' : ''">{{ modalityLabel(hit.modality || 'text') }}</span>
                    <span class="ai-pill info">{{ percentScore(hit.score) }}</span>
                  </header>
                  <div class="result-body" :class="{ 'with-media': !!hit.media }">
                    <img
                      v-if="hit.media && mediaUrl(hit.media)"
                      class="result-thumb"
                      :src="mediaUrl(hit.media)"
                      :alt="hit.media.caption || hit.document_title"
                      loading="lazy"
                    />
                    <div class="result-text">
                      <p>{{ hit.snippet }}</p>
                      <div v-if="hit.media?.caption" class="media-caption">{{ hit.media.caption }}</div>
                      <div class="result-meta">
                        <span>{{ sourceTypeLabel(hit.source_type) }}</span>
                        <span v-if="hit.source_ref" class="mono">{{ hit.source_ref }}</span>
                        <span v-if="hit.matched_terms?.length">命中 {{ hit.matched_terms.slice(0, 4).join(' / ') }}</span>
                      </div>
                    </div>
                  </div>
                </article>
              </div>
              <SfEmptyState
                v-else-if="hasSearched && !searching && !asking && !imageSearching"
                icon="search"
                title="没有匹配结果"
                description="当前部门知识库还没有相关内容"
                hint="可以新增文档，或点击同步资产把 Skill 和数据源说明写入知识库。"
              />
              <div v-else class="query-placeholder">
                <IconSearch />
                <span>输入问题后查看来源、答案和可复制上下文</span>
              </div>
            </a-spin>
          </div>
        </section>

        <section id="knowledge-documents" class="knowledge-card ai-card documents-section">
          <div class="ai-card-h knowledge-card-head">
            <div>
              <div class="t">{{ selectedBase?.name || '部门知识库' }}</div>
              <div class="s">{{ selectedBase?.description || '当前可见部门知识库' }}</div>
            </div>
            <div class="document-tools">
              <a-select
                v-model="documentStatus"
                size="small"
                style="width: 116px"
                @change="loadDocuments"
              >
                <a-option v-for="item in documentStatusOptions" :key="item.value" :value="item.value">
                  {{ item.label }}
                </a-option>
              </a-select>
              <a-input-search
                v-model="documentQuery"
                placeholder="筛选文档"
                allow-clear
                size="small"
                style="width: 220px"
                @search="loadDocuments"
                @press-enter="loadDocuments"
              />
            </div>
          </div>
          <div class="card-body table-wrap">
            <a-spin :loading="docsLoading">
              <table v-if="documentRows.length" class="ai-table knowledge-table">
                <thead>
                  <tr>
                    <th>文档</th>
                    <th>标签</th>
                    <th>来源</th>
                    <th>状态</th>
                    <th>索引</th>
                    <th>更新</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="record in documentRows" :key="record.id">
                    <td>
                      <div class="doc-cell">
                        <img
                          v-if="primaryMedia(record) && mediaUrl(primaryMedia(record))"
                          class="doc-thumb"
                          :src="mediaUrl(primaryMedia(record))"
                          :alt="primaryMedia(record)?.caption || record.title"
                          loading="lazy"
                        />
                        <span v-else class="doc-thumb placeholder"><IconImage /></span>
                        <span class="doc-copy">
                          <span class="doc-title">{{ record.title }}</span>
                          <span class="doc-sub">{{ record.department }} · {{ sourceTypeLabel(record.source_type) }} · {{ modalityLabel(record.modality || 'text') }}</span>
                        </span>
                      </div>
                    </td>
                    <td class="tags-cell">
                      <div class="tag-list">
                        <a-tag v-for="tag in (record.tags || []).slice(0, 3)" :key="`${record.id}-${tag}`" size="small">{{ tag }}</a-tag>
                        <span v-if="!(record.tags || []).length" class="muted">-</span>
                      </div>
                    </td>
                    <td>
                      <div class="source-cell">
                        <a-tag size="small">{{ sourceTypeLabel(record.source_type) }}</a-tag>
                        <span v-if="record.source_ref" class="mono muted source-ref">{{ record.source_ref }}</span>
                      </div>
                    </td>
                    <td>
                      <a-tag size="small" :color="statusTagColor(record.status)">{{ statusLabel(record.status) }}</a-tag>
                    </td>
                    <td>
                      <div class="index-cell">
                        <a-tag size="small" :color="indexTagColor(record.index_status)">{{ indexStatusLabel(record.index_status) }}</a-tag>
                        <span class="mono muted">{{ safeNumber(record.chunk_count) }} chunks</span>
                        <span v-if="record.index_error" class="muted source-ref">{{ record.index_error }}</span>
                      </div>
                    </td>
                    <td>{{ formatTime(record.updated_at) }}</td>
                    <td>
                      <div class="row-actions">
                        <a-tooltip content="查看文档">
                          <button
                            class="row-icon-btn"
                            type="button"
                            aria-label="查看文档"
                            @click="openDetail(record)"
                          >
                            <IconEye />
                          </button>
                        </a-tooltip>
                        <a-tooltip :content="canWriteDocument(record) ? '编辑文档' : '无权编辑该文档'">
                          <button
                            class="row-icon-btn"
                            type="button"
                            :disabled="!canEditDocument(record)"
                            aria-label="编辑文档"
                            @click="openEdit(record)"
                          >
                            <IconEdit />
                          </button>
                        </a-tooltip>
                        <a-tooltip content="重建检索索引">
                          <button
                            class="row-icon-btn"
                            type="button"
                            :disabled="!canEditDocument(record)"
                            aria-label="重建检索索引"
                            @click="reindex(record)"
                          >
                            <IconSync />
                          </button>
                        </a-tooltip>
                        <a-tooltip v-if="record.status === 'deleted'" content="恢复后重新参与检索">
                          <button
                            class="row-icon-btn"
                            type="button"
                            :disabled="!canWriteDocument(record)"
                            aria-label="恢复文档"
                            @click="restoreRow(record)"
                          >
                            <IconUndo />
                          </button>
                        </a-tooltip>
                        <a-tooltip content="删除后不再参与检索，后台保留审计记录">
                          <button
                            v-if="record.status !== 'deleted'"
                            class="row-icon-btn danger"
                            type="button"
                            :disabled="!canWriteDocument(record)"
                            aria-label="删除文档"
                            @click="deleteRow(record)"
                          >
                            <IconDelete />
                          </button>
                        </a-tooltip>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
              <SfEmptyState
                v-else
                icon="book"
                title="暂无文档"
                description="当前部门知识库还没有人工文档"
                hint="先同步平台资产，或新增业务 SOP、排障说明、复盘结论。"
              />
            </a-spin>
          </div>
        </section>

        <section id="knowledge-integration" class="knowledge-card ai-card call-section">
          <div class="ai-card-h knowledge-card-head">
            <div>
              <div class="t">调用方式</div>
              <div class="s">Skill、Agent 和后台任务通过统一上下文接口读取当前部门知识</div>
            </div>
            <div class="call-actions">
              <button class="ai-btn sm" type="button" @click="copyCallPayload">
                <IconCopy />
                复制参数
              </button>
              <button class="ai-btn sm" type="button" :disabled="!query.trim() || contextLoading" @click="runContext">
                <IconCode />
                生成上下文
              </button>
            </div>
          </div>
          <div class="card-body call-grid">
            <div class="call-card">
              <span class="call-label">Endpoint</span>
              <code>POST /api/knowledge/context</code>
              <pre>{{ callPayload }}</pre>
            </div>
            <div class="call-card">
              <span class="call-label">RAG Backend</span>
              <code>{{ storageLabel }} · {{ ragModeLabel }}</code>
              <p>文本原文和图片资产保存在受控存储，图片先经视觉模型生成 caption/OCR/对象标签，再写入 LightRAG 兼容混合索引，可接入 embedding、reranker 与正式 LightRAG Server。</p>
            </div>
          </div>
        </section>
      </main>
    </div>

    <a-modal
      v-model:visible="editorVisible"
      :title="editingId ? '编辑知识文档' : '新增知识文档'"
      :width="'min(92vw, 760px)'"
      :ok-loading="saving"
      @ok="saveDocument"
    >
      <a-form :model="form" layout="vertical">
        <a-form-item label="标题" required>
          <a-input v-model="form.title" placeholder="例如：五星价格力处理 SOP" />
        </a-form-item>
        <a-row :gutter="[12, 0]">
          <a-col :xs="24" :md="10">
            <a-form-item label="来源类型">
              <a-select v-model="form.sourceType">
                <a-option v-for="item in sourceTypeOptions" :key="item.value" :value="item.value">
                  {{ item.label }}
                </a-option>
              </a-select>
            </a-form-item>
          </a-col>
          <a-col :xs="24" :md="14">
            <a-form-item label="来源引用">
              <a-input v-model="form.sourceRef" placeholder="URL、Skill ID、数据源 ID 或外部文档编号" />
            </a-form-item>
          </a-col>
        </a-row>
        <a-form-item label="标签">
          <a-input v-model="form.tagsText" placeholder="用逗号分隔，例如 SOP,价格力" />
        </a-form-item>
        <a-form-item v-if="!editingId">
          <a-checkbox v-model="form.upsertSource">同来源引用存在时覆盖更新</a-checkbox>
        </a-form-item>
        <a-form-item label="内容" required>
          <a-textarea
            v-model="form.content"
            :auto-size="{ minRows: 12, maxRows: 18 }"
            placeholder="粘贴 SOP、复盘结论、业务规则、排障步骤或数据说明"
          />
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal
      v-model:visible="importVisible"
      title="导入网页链接"
      :width="'min(92vw, 620px)'"
      :ok-loading="importing"
      @ok="importUrl"
    >
      <a-form :model="importForm" layout="vertical">
        <a-form-item label="URL" required>
          <a-input v-model="importForm.url" placeholder="https://example.com/sop" />
        </a-form-item>
        <a-form-item label="标题">
          <a-input v-model="importForm.title" placeholder="不填则使用链接路径" />
        </a-form-item>
        <a-form-item label="标签">
          <a-input v-model="importForm.tagsText" placeholder="用逗号分隔，例如 SOP,官网" />
        </a-form-item>
        <a-form-item>
          <a-checkbox v-model="importForm.upsertSource">同链接存在时覆盖更新</a-checkbox>
        </a-form-item>
      </a-form>
    </a-modal>

    <a-drawer
      :visible="detailVisible"
      :width="'min(92vw, 820px)'"
      :title="detailDoc?.title || '知识文档'"
      :footer="false"
      @cancel="detailVisible = false"
    >
      <div v-if="detailDoc" class="detail-drawer">
        <div class="detail-meta">
          <a-tag>{{ sourceTypeLabel(detailDoc.source_type) }}</a-tag>
          <a-tag :color="statusTagColor(detailDoc.status)">{{ statusLabel(detailDoc.status) }}</a-tag>
          <a-tag>{{ modalityLabel(detailDoc.modality || 'text') }}</a-tag>
          <span class="mono">{{ detailDoc.id }}</span>
          <span v-if="detailDoc.source_ref" class="mono">{{ detailDoc.source_ref }}</span>
        </div>
        <div class="detail-tags">
          <a-tag v-for="tag in detailDoc.tags || []" :key="tag" size="small">{{ tag }}</a-tag>
        </div>
        <div v-if="(detailDoc.media_assets || []).length" class="detail-media-grid">
          <article v-for="asset in detailDoc.media_assets" :key="asset.id" class="detail-media-card">
            <img v-if="mediaUrl(asset)" :src="mediaUrl(asset)" :alt="asset.caption || asset.file_name || detailDoc.title" loading="lazy" />
            <div>
              <strong>{{ asset.file_name || '图片' }}</strong>
              <span>{{ mediaStatusLabel(asset.status) }} · {{ formatBytes(asset.byte_size) }}</span>
              <p v-if="asset.caption">{{ asset.caption }}</p>
              <p v-if="asset.index_error" class="warn-text">{{ asset.index_error }}</p>
            </div>
          </article>
        </div>
        <LearningFlowMini
          class="knowledge-detail-flow"
          entity-type="knowledge_document"
          :entity-id="detailDoc.id"
          title="知识数据流"
          subtitle="查看这条知识从哪里产生、被哪些 Agent / Skill 使用，以及是否形成样本或候选。"
          compact
          :limit="8"
        />
        <pre class="detail-content">{{ detailDoc.content }}</pre>
      </div>
    </a-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'
import {
  IconBranch,
  IconCode,
  IconCopy,
  IconDelete,
  IconEdit,
  IconEye,
  IconImage,
  IconLink,
  IconPlus,
  IconRefresh,
  IconRobot,
  IconSearch,
  IconSync,
  IconUndo,
  IconUpload,
} from '@arco-design/web-vue/es/icon'
import { knowledgeApi as rawKnowledgeApi, type KnowledgeBaseRow, type KnowledgeDocumentRow, type KnowledgeMediaAsset, type KnowledgeSearchHit } from '@/api'
import { SfEmptyState } from '@/components/common'
import LearningFlowMini from '@/components/learning/LearningFlowMini.vue'
import { copyText } from '@/utils/clipboard'

defineOptions({ name: 'KnowledgeHome' })

const knowledgeApi: any = rawKnowledgeApi

const loading = ref(false)
const docsLoading = ref(false)
const searching = ref(false)
const asking = ref(false)
const contextLoading = ref(false)
const imageSearching = ref(false)
const syncing = ref(false)
const saving = ref(false)
const uploading = ref(false)
const importing = ref(false)
const bases = ref<KnowledgeBaseRow[]>([])
const summary = ref<Record<string, any>>({})
const ragHealth = ref<Record<string, any>>({})
const documents = ref<KnowledgeDocumentRow[]>([])
const searchHits = ref<KnowledgeSearchHit[]>([])
const contextSources = ref<KnowledgeSearchHit[]>([])
const selectedBaseId = ref('')
const query = ref('')
const mediaMode = ref('all')
const documentQuery = ref('')
const documentStatus = ref('active')
const answer = ref('')
const answerMode = ref('')
const promptContext = ref('')
const contextChars = ref(0)
const hasSearched = ref(false)
const editorVisible = ref(false)
const editingId = ref('')
const detailVisible = ref(false)
const importVisible = ref(false)
const uploadInput = ref<HTMLInputElement | null>(null)
const imageSearchInput = ref<HTMLInputElement | null>(null)
const detailDoc = ref<KnowledgeDocumentRow | null>(null)
const form = reactive({
  title: '',
  content: '',
  tagsText: '',
  sourceType: 'manual',
  sourceRef: '',
  upsertSource: false,
})
const importForm = reactive({
  url: '',
  title: '',
  tagsText: '',
  upsertSource: true,
})

const sourceTypeOptions = [
  { value: 'manual', label: '人工文档' },
  { value: 'url', label: '链接' },
  { value: 'upload', label: '上传内容' },
  { value: 'note', label: '笔记' },
  { value: 'skill', label: 'Skill' },
  { value: 'datasource', label: '数据源' },
]

const documentStatusOptions = [
  { value: 'active', label: '有效' },
  { value: 'deleted', label: '已删除' },
  { value: 'archived', label: '已归档' },
  { value: 'all', label: '全部' },
]

const modalityOptions = [
  { value: 'all', label: '全部' },
  { value: 'text', label: '文本' },
  { value: 'image', label: '图片' },
]

const selectedBase = computed(() => bases.value.find(item => item.id === selectedBaseId.value) || bases.value[0] || null)
const documentRows = computed(() => documents.value)
const chunkCount = computed(() => documentRows.value.reduce((total, doc) => total + safeNumber(doc.chunk_count), 0))
const mediaCount = computed(() => safeNumber(summary.value?.media_count) || documentRows.value.reduce((total, doc) => total + (doc.media_assets || []).length, 0))
const storageLabel = computed(() => {
  const backend = String(summary.value?.storage_backend || selectedBase.value?.storage_backend || 'lightrag')
  if (backend === 'lightrag') return 'LightRAG'
  if (backend === 'postgres_jsonb_vector') return 'Legacy Vector'
  return backend
})
const metricCards = computed(() => [
  { label: '部门知识库', value: summary.value?.department_count || bases.value.length, hint: '按组织部门自动创建', tone: 'brand' },
  { label: '文档', value: summary.value?.document_count || documentRows.value.length, hint: '人工文档 + 平台资产', tone: 'info' },
  { label: '图片', value: mediaCount.value, hint: 'VLM 解析后进入 RAG', tone: 'accent' },
  { label: '切片', value: summary.value?.chunk_count || chunkCount.value, hint: '用于混合检索召回', tone: 'success' },
  { label: 'RAG', value: storageLabel.value, hint: ragModeLabel.value, tone: 'neutral' },
])
const ragModeLabel = computed(() => {
  const mode = String(ragHealth.value?.mode || 'embedded')
  if (mode === 'server') return 'Server hybrid'
  return 'Embedded hybrid'
})
const answerModeText = computed(() => {
  if (answerMode.value === 'llm') return 'AI'
  if (answerMode.value === 'extractive_fallback') return '检索兜底'
  if (answerMode.value === 'insufficient_evidence') return '依据不足'
  return '检索'
})
const callPayload = computed(() => JSON.stringify({
  kb_id: selectedBaseId.value || undefined,
  query: query.value.trim() || '替换成业务问题',
  modality: mediaMode.value,
  top_k: 8,
  max_chars: 6000,
}, null, 2))
const activeSection = ref('overview')
const sectionTabs = computed(() => [
  { key: 'overview', label: '总览', count: bases.value.length },
  { key: 'search', label: '检索问答', count: searchHits.value.length || undefined },
  { key: 'documents', label: '文档管理', count: documentRows.value.length },
  { key: 'integration', label: '调用集成', count: promptContext.value ? contextSources.value.length : undefined },
])

watch(selectedBaseId, () => {
  answer.value = ''
  searchHits.value = []
  promptContext.value = ''
  contextSources.value = []
  contextChars.value = 0
  hasSearched.value = false
  loadDocuments()
})

function normalizeList<T>(value: unknown): T[] {
  if (Array.isArray(value)) return value as T[]
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    if (Array.isArray(record.items)) return record.items as T[]
    if (Array.isArray(record.departments)) return record.departments as T[]
  }
  return []
}

function safeNumber(value: unknown): number {
  const num = Number(value)
  return Number.isFinite(num) ? num : 0
}

function modalityLabel(value?: string): string {
  const mode = String(value || 'all')
  if (mode === 'image') return '图片'
  if (mode === 'text') return '文本'
  return '全部'
}

function primaryMedia(record?: KnowledgeDocumentRow | null): KnowledgeMediaAsset | null {
  const assets = record?.media_assets || []
  return assets.length ? assets[0] : null
}

function mediaUrl(asset?: KnowledgeMediaAsset | null): string {
  if (!asset?.content_url && !asset?.id) return ''
  const url = asset.content_url || `/api/knowledge/media/${encodeURIComponent(asset.id)}/content`
  if (/^https?:\/\//.test(url)) return url
  return url.startsWith('/api/') ? url : `/api${url.startsWith('/') ? url : `/${url}`}`
}

function mediaStatusLabel(status?: string): string {
  const map: Record<string, string> = {
    pending: '待索引',
    indexed: '已索引',
    failed: '失败',
    vision_failed: '视觉解析失败',
    deleted: '已删除',
  }
  return map[status || ''] || status || '-'
}

function formatBytes(value?: number): string {
  const size = safeNumber(value)
  if (!size) return '-'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function selectBase(id: string) {
  selectedBaseId.value = id
}

function jumpToSection(key: string) {
  activeSection.value = key
  const target = typeof document !== 'undefined' ? document.getElementById(`knowledge-${key}`) : null
  target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function sourceTypeLabel(type: string): string {
  const map: Record<string, string> = {
    manual: '人工文档',
    skill: 'Skill',
    datasource: '数据源',
    upload: '上传',
    url: '链接',
    note: '笔记',
  }
  return map[type] || type || '-'
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    active: '有效',
    archived: '归档',
    deleted: '删除',
  }
  return map[status] || status || '-'
}

function statusTagColor(status: string): string {
  if (status === 'active') return 'green'
  if (status === 'deleted') return 'red'
  if (status === 'archived') return 'gray'
  return 'arcoblue'
}

function indexStatusLabel(status?: string): string {
  const map: Record<string, string> = {
    pending: '待索引',
    running: '索引中',
    indexed: '已索引',
    failed: '失败',
  }
  return map[status || ''] || status || '-'
}

function indexTagColor(status?: string): string {
  if (status === 'indexed') return 'green'
  if (status === 'running') return 'arcoblue'
  if (status === 'failed') return 'red'
  return 'gray'
}

function percentScore(value: unknown): string {
  return `${Math.round(Math.max(0, Math.min(1, safeNumber(value))) * 100)}%`
}

function formatTime(value?: string | null): string {
  if (!value) return '-'
  return String(value).replace('T', ' ').slice(0, 16)
}

function canWriteDocument(record: KnowledgeDocumentRow): boolean {
  return record?.can_write !== false
}

function canEditDocument(record: KnowledgeDocumentRow): boolean {
  return canWriteDocument(record) && record.status !== 'deleted'
}

function resetForm() {
  editingId.value = ''
  form.title = ''
  form.content = ''
  form.tagsText = ''
  form.sourceType = 'manual'
  form.sourceRef = ''
  form.upsertSource = false
}

async function refreshAll() {
  loading.value = true
  try {
    const [baseRes, summaryRes, healthRes] = await Promise.all([
      knowledgeApi.bases(),
      knowledgeApi.summary(),
      knowledgeApi.ragHealth?.(),
    ])
    bases.value = normalizeList<KnowledgeBaseRow>(baseRes)
    summary.value = summaryRes || {}
    ragHealth.value = healthRes || {}
    if (!selectedBaseId.value || !bases.value.some(item => item.id === selectedBaseId.value)) {
      const withDocs = bases.value.find(item => safeNumber(item.stats?.document_count) > 0)
      selectedBaseId.value = (withDocs || bases.value[0])?.id || ''
    }
    await loadDocuments()
  } finally {
    loading.value = false
  }
}

async function loadDocuments() {
  if (!selectedBaseId.value) {
    documents.value = []
    return
  }
  docsLoading.value = true
  try {
    const res = await knowledgeApi.documents({
      kb_id: selectedBaseId.value,
      query: documentQuery.value || undefined,
      status: documentStatus.value,
      limit: 100,
    })
    documents.value = normalizeList<KnowledgeDocumentRow>(res)
  } finally {
    docsLoading.value = false
  }
}

async function runSearch() {
  const text = query.value.trim()
  if (!text) return
  searching.value = true
  hasSearched.value = true
  answer.value = ''
  promptContext.value = ''
  try {
    const res = await knowledgeApi.search({
      query: text,
      kb_id: selectedBaseId.value || undefined,
      top_k: 8,
      modality: mediaMode.value,
    })
    searchHits.value = normalizeList<KnowledgeSearchHit>(res)
  } finally {
    searching.value = false
  }
}

async function runAsk() {
  const text = query.value.trim()
  if (!text) return
  asking.value = true
  hasSearched.value = true
  promptContext.value = ''
  try {
    const res = await knowledgeApi.ask({
      query: text,
      kb_id: selectedBaseId.value || undefined,
      top_k: 8,
      use_llm: true,
      modality: mediaMode.value,
    })
    answer.value = String(res?.answer || '')
    answerMode.value = String(res?.answer_mode || '')
    searchHits.value = normalizeList<KnowledgeSearchHit>(res?.sources || [])
  } finally {
    asking.value = false
  }
}

async function runContext() {
  const text = query.value.trim()
  if (!text) return
  contextLoading.value = true
  hasSearched.value = true
  try {
    const res = await knowledgeApi.context({
      query: text,
      kb_id: selectedBaseId.value || undefined,
      top_k: 8,
      max_chars: 6000,
      modality: mediaMode.value,
    })
    promptContext.value = String(res?.prompt_context || '')
    contextChars.value = safeNumber(res?.context_chars)
    contextSources.value = normalizeList<KnowledgeSearchHit>(res?.sources || [])
    searchHits.value = contextSources.value
  } finally {
    contextLoading.value = false
  }
}

async function copyPromptContext() {
  const ok = await copyText(promptContext.value)
  if (ok) Message.success('上下文已复制')
  else Message.warning('复制失败')
}

async function copyCallPayload() {
  const ok = await copyText(callPayload.value)
  if (ok) Message.success('调用参数已复制')
  else Message.warning('复制失败')
}

async function syncAssets() {
  if (!selectedBase.value?.can_write) return
  syncing.value = true
  try {
    const res = await knowledgeApi.sync({ kb_id: selectedBaseId.value })
    Message.success(`已同步 ${safeNumber(res?.total)} 条资产文档`)
    await refreshAll()
  } finally {
    syncing.value = false
  }
}

function triggerUpload() {
  uploadInput.value?.click()
}

function triggerImageSearch() {
  imageSearchInput.value?.click()
}

async function handleUpload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !selectedBaseId.value) return
  uploading.value = true
  try {
    const data = new FormData()
    data.append('file', file)
    data.append('kb_id', selectedBaseId.value)
    await knowledgeApi.uploadDocument(data)
    Message.success('文件已上传并索引')
    await refreshAll()
  } finally {
    uploading.value = false
    input.value = ''
  }
}

async function handleImageSearch(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !selectedBaseId.value) return
  imageSearching.value = true
  hasSearched.value = true
  answer.value = ''
  promptContext.value = ''
  try {
    const data = new FormData()
    data.append('file', file)
    data.append('kb_id', selectedBaseId.value)
    data.append('top_k', '8')
    data.append('modality', mediaMode.value)
    const res = await knowledgeApi.searchByImage(data)
    query.value = String(res?.query || query.value || file.name)
    searchHits.value = normalizeList<KnowledgeSearchHit>(res)
    Message.success(`图片检索完成，命中 ${searchHits.value.length} 条`)
  } finally {
    imageSearching.value = false
    input.value = ''
  }
}

function openImportUrl() {
  importForm.url = ''
  importForm.title = ''
  importForm.tagsText = ''
  importForm.upsertSource = true
  importVisible.value = true
}

async function importUrl() {
  if (!importForm.url.trim()) {
    Message.warning('请填写 URL')
    return
  }
  importing.value = true
  try {
    await knowledgeApi.importUrl({
      kb_id: selectedBaseId.value,
      url: importForm.url.trim(),
      title: importForm.title.trim() || undefined,
      tags: tagsFromText(importForm.tagsText),
      upsert_source: importForm.upsertSource,
    })
    Message.success('链接已导入并索引')
    importVisible.value = false
    await refreshAll()
  } finally {
    importing.value = false
  }
}

function openCreate() {
  resetForm()
  editorVisible.value = true
}

async function openEdit(record: KnowledgeDocumentRow) {
  if (!record?.id || !canEditDocument(record)) return
  const detail = await knowledgeApi.getDocument(record.id)
  editingId.value = detail.id
  form.title = detail.title || ''
  form.content = detail.content || ''
  form.tagsText = (detail.tags || []).join(', ')
  form.sourceType = detail.source_type || 'manual'
  form.sourceRef = detail.source_ref || ''
  form.upsertSource = false
  editorVisible.value = true
}

async function openDetail(record: KnowledgeDocumentRow) {
  if (!record?.id) return
  detailDoc.value = await knowledgeApi.getDocument(record.id)
  detailVisible.value = true
}

function tagsFromText(text: string): string[] {
  return text.split(/[,，\s]+/).map(item => item.trim()).filter(Boolean).slice(0, 30)
}

async function saveDocument() {
  if (!form.title.trim() || !form.content.trim()) {
    Message.warning('请填写标题和内容')
    return
  }
  saving.value = true
  try {
    const payload = {
      kb_id: selectedBaseId.value,
      title: form.title.trim(),
      content: form.content.trim(),
      tags: tagsFromText(form.tagsText),
      source_type: form.sourceType,
      source_ref: form.sourceRef.trim(),
      upsert_source: form.upsertSource,
    }
    if (editingId.value) {
      await knowledgeApi.updateDocument(editingId.value, payload)
    } else {
      await knowledgeApi.createDocument(payload)
    }
    Message.success('知识文档已保存')
    editorVisible.value = false
    await refreshAll()
  } finally {
    saving.value = false
  }
}

async function reindex(record: KnowledgeDocumentRow) {
  if (!canEditDocument(record)) return
  await knowledgeApi.reindexDocument(record.id)
  Message.success('索引已重建')
  await loadDocuments()
}

async function restoreRow(record: KnowledgeDocumentRow) {
  if (!canWriteDocument(record)) return
  await knowledgeApi.updateDocument(record.id, { status: 'active' })
  Message.success('文档已恢复')
  documentStatus.value = 'active'
  await refreshAll()
}

function deleteRow(record: KnowledgeDocumentRow) {
  if (!canWriteDocument(record)) return
  Modal.confirm({
    title: '删除知识文档',
    content: `确认删除「${record.title}」？删除后不会参与检索，后台保留审计记录。`,
    okText: '删除',
    okButtonProps: { status: 'danger' },
    onOk: async () => {
      await knowledgeApi.deleteDocument(record.id)
      Message.success('文档已删除')
      await refreshAll()
    },
  })
}

onMounted(refreshAll)
</script>

<style scoped>
.knowledge-page {
  min-height: 100%;
}

.knowledge-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.knowledge-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.upload-input {
  display: none;
}

.knowledge-actions .ai-btn svg,
.query-actions .ai-btn svg,
.query-filters .ai-btn svg,
.call-actions .ai-btn svg {
  width: 14px;
  height: 14px;
  flex: 0 0 14px;
}

.knowledge-section-tabs {
  flex: 0 0 auto;
}

.knowledge-section-tab {
  border-left: 0;
  border-right: 0;
  border-top: 0;
  background: transparent;
  font-family: var(--ai-font-sans);
}

.knowledge-section-tab-count {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.knowledge-body {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
  min-height: 0;
  overflow: auto;
}

.knowledge-sidebar {
  position: sticky;
  top: 20px;
  padding: 16px 12px;
  align-self: start;
}

.knowledge-side-item {
  width: 100%;
  border: 0;
  background: transparent;
  font-family: var(--ai-font-sans);
  text-align: left;
}

.knowledge-side-item .ic {
  width: 13px;
  height: 13px;
  flex: 0 0 13px;
}

.side-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.knowledge-sidebar-note {
  margin-top: 14px;
  padding: 12px;
  border-radius: 8px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: var(--ai-ink-3);
  font-size: 12px;
}

.knowledge-sidebar-note .note-label {
  color: var(--ai-ink-4);
  font-size: 11px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.knowledge-sidebar-note strong {
  color: var(--ai-ink-1);
  font-size: 13px;
}

.knowledge-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.knowledge-metrics {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
}

.knowledge-metric {
  position: relative;
  padding: 14px 16px;
  min-width: 0;
  overflow: hidden;
}

.knowledge-metric::before {
  content: '';
  position: absolute;
  top: 14px;
  right: 14px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--ai-ink-5);
}

.knowledge-metric[data-tone='brand']::before { background: var(--ai-accent); }
.knowledge-metric[data-tone='info']::before { background: var(--ai-info); }
.knowledge-metric[data-tone='accent']::before { background: var(--ai-warn); }
.knowledge-metric[data-tone='success']::before { background: var(--ai-ok); }

.metric-label {
  display: block;
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.metric-value {
  display: block;
  margin-top: 6px;
  color: var(--ai-ink-1);
  font-size: 28px;
  line-height: 1;
  font-weight: 650;
  letter-spacing: -0.025em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metric-hint {
  display: block;
  margin-top: 8px;
  color: var(--ai-ink-3);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.knowledge-card {
  padding: 0;
  overflow: hidden;
}

.knowledge-card-head {
  justify-content: space-between;
}

.knowledge-card-head > div:first-child {
  min-width: 0;
}

.card-body {
  padding: 14px;
}

.query-grid {
  display: grid;
  grid-template-columns: minmax(260px, 360px) minmax(0, 1fr);
  gap: 14px;
}

.query-compose {
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
  padding: 12px;
  min-width: 0;
}

.query-label {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  margin-bottom: 8px;
}

.query-compose :deep(.arco-input-wrapper) {
  border-color: var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface);
  box-shadow: none;
}

.query-filters {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 10px;
}

.filter-label {
  color: var(--ai-ink-4);
  font-size: 12px;
  margin-right: 2px;
}

.mode-chip {
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-3);
  border-radius: 999px;
  padding: 4px 10px;
  font: inherit;
  font-size: 12px;
  line-height: 1.2;
  cursor: pointer;
}

.mode-chip.active {
  border-color: color-mix(in srgb, var(--ai-accent) 45%, var(--ai-border));
  background: color-mix(in srgb, var(--ai-accent) 12%, transparent);
  color: var(--ai-ink-1);
}

.image-search-btn {
  margin-left: 4px;
}

.query-actions,
.query-help,
.result-meta,
.answer-head,
.document-tools,
.call-actions,
.detail-meta,
.detail-tags,
.tag-list,
.row-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.query-actions {
  margin-top: 10px;
}

.query-help {
  margin-top: 12px;
  color: var(--ai-ink-4);
  font-size: 12px;
  gap: 10px;
}

.query-help span + span::before {
  content: '';
  display: inline-block;
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: var(--ai-ink-5);
  margin-right: 10px;
  vertical-align: middle;
}

.query-output {
  min-width: 0;
  min-height: 164px;
}

.query-output :deep(.arco-spin-children) {
  min-height: 164px;
}

.answer-box,
.context-box,
.result-item,
.query-placeholder {
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
}

.answer-box,
.context-box {
  padding: 12px;
  margin-bottom: 10px;
}

.answer-head {
  justify-content: space-between;
  color: var(--ai-ink-2);
  font-weight: 600;
  margin-bottom: 8px;
}

.answer-box p {
  margin: 0;
  color: var(--ai-ink-2);
  line-height: 1.7;
  white-space: pre-wrap;
}

.context-box {
  background: var(--ai-surface-2);
}

.context-box pre,
.call-card pre,
.detail-content {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--ai-font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: var(--ai-ink-2);
}

.result-list {
  display: grid;
  gap: 10px;
}

.result-item {
  padding: 12px;
}

.result-item header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.result-body.with-media {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
  margin-top: 8px;
}

.result-thumb {
  width: 96px;
  height: 72px;
  object-fit: cover;
  border-radius: 8px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
}

.result-text {
  min-width: 0;
}

.result-title {
  color: var(--ai-ink-1);
  font-weight: 600;
}

.result-item p {
  margin: 8px 0;
  color: var(--ai-ink-2);
  line-height: 1.6;
}

.result-meta {
  color: var(--ai-ink-4);
  font-size: 12px;
}

.media-caption {
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 1.5;
  margin: -2px 0 8px;
}

.query-placeholder {
  height: 164px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 8px;
  color: var(--ai-ink-4);
  background: var(--ai-surface-2);
  font-size: 12px;
}

.query-placeholder svg {
  width: 18px;
  height: 18px;
}

.document-tools,
.call-actions {
  justify-content: flex-end;
}

.table-wrap {
  overflow-x: auto;
  padding: 0;
}

.knowledge-table {
  min-width: 900px;
}

.knowledge-table th:first-child,
.knowledge-table td:first-child {
  padding-left: 14px;
}

.knowledge-table th:last-child,
.knowledge-table td:last-child {
  padding-right: 14px;
}

.knowledge-table th:nth-child(2) { width: 180px; }
.knowledge-table th:nth-child(3) { width: 150px; }
.knowledge-table th:nth-child(4) { width: 92px; }
.knowledge-table th:nth-child(5) { width: 120px; }
.knowledge-table th:nth-child(6) { width: 148px; }
.knowledge-table th:nth-child(7) { width: 150px; }

.doc-cell,
.source-cell,
.index-cell {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.doc-cell {
  flex-direction: row;
  align-items: center;
  gap: 8px;
}

.doc-copy {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.doc-thumb {
  width: 38px;
  height: 38px;
  flex: 0 0 38px;
  border-radius: 8px;
  object-fit: cover;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
}

.doc-thumb.placeholder {
  display: grid;
  place-items: center;
  color: var(--ai-ink-5);
}

.doc-thumb.placeholder svg {
  width: 16px;
  height: 16px;
}

.doc-title {
  color: var(--ai-ink-1);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-sub,
.muted {
  color: var(--ai-ink-4);
  font-size: 12px;
}

.source-ref {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 130px;
}

.mono {
  font-family: var(--ai-font-mono);
  font-size: 12px;
}

.mini-link,
.row-icon-btn {
  border: 0;
  background: transparent;
  color: var(--ai-ink-3);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-family: var(--ai-font-sans);
}

.mini-link {
  gap: 4px;
  font-size: 12px;
  padding: 2px 0;
}

.mini-link:hover {
  color: var(--ai-ink-1);
}

.mini-link svg,
.row-icon-btn svg {
  width: 14px;
  height: 14px;
}

.row-actions {
  gap: 4px;
  flex-wrap: nowrap;
}

.row-icon-btn {
  width: 26px;
  height: 26px;
  border-radius: 6px;
}

.row-icon-btn:hover:not(:disabled) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
}

.row-icon-btn.danger:hover:not(:disabled) {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
}

.row-icon-btn:disabled {
  opacity: 0.42;
  cursor: not-allowed;
}

.call-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 0.75fr);
  gap: 12px;
}

.call-card {
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  padding: 12px;
  background: var(--ai-surface-2);
  min-width: 0;
}

.call-label {
  display: block;
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 650;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  margin-bottom: 6px;
}

.call-card code {
  display: block;
  margin-bottom: 10px;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
}

.call-card p {
  margin: 0;
  color: var(--ai-ink-3);
  font-size: 13px;
  line-height: 1.7;
}

.detail-drawer {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.detail-media-grid {
  display: grid;
  gap: 10px;
}

.knowledge-detail-flow {
  margin: 2px 0;
}

.detail-media-card {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr);
  gap: 12px;
  border: 1px solid var(--ai-border);
  border-radius: 10px;
  padding: 10px;
  background: var(--ai-surface-2);
}

.detail-media-card img {
  width: 180px;
  max-height: 160px;
  object-fit: contain;
  border-radius: 8px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
}

.detail-media-card strong,
.detail-media-card span {
  display: block;
}

.detail-media-card strong {
  color: var(--ai-ink-1);
  margin-bottom: 4px;
}

.detail-media-card span {
  color: var(--ai-ink-4);
  font-size: 12px;
}

.detail-media-card p {
  margin: 8px 0 0;
  color: var(--ai-ink-2);
  line-height: 1.6;
}

.warn-text {
  color: var(--ai-bad) !important;
}

.detail-content {
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
  padding: 12px;
  max-height: 60vh;
  overflow: auto;
}

@media (max-width: 1100px) {
  .knowledge-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .query-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 900px) {
  .knowledge-head {
    flex-direction: column;
    align-items: flex-start;
  }

  .knowledge-actions {
    justify-content: flex-start;
  }

  .knowledge-body {
    grid-template-columns: 1fr;
  }

  .knowledge-sidebar {
    position: static;
    display: flex;
    overflow-x: auto;
    gap: 8px;
    padding: 10px;
  }

  .knowledge-sidebar .ai-side-label,
  .knowledge-sidebar-note {
    display: none;
  }

  .knowledge-side-item {
    flex: 0 0 auto;
    width: auto;
  }

  .knowledge-card-head,
  .document-tools,
  .call-actions {
    align-items: flex-start;
    justify-content: flex-start;
  }

  .knowledge-card-head {
    flex-direction: column;
  }

  .call-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .knowledge-metrics {
    grid-template-columns: 1fr;
  }

  .knowledge-section-tabs {
    overflow-x: auto;
    padding-left: 20px;
    padding-right: 20px;
  }

  .knowledge-body {
    padding: 16px 20px;
  }
}
</style>
