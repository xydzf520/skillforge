<template>
  <div class="page-container page-wide imagegen-workspace">
    <HallDetailHeader
      :crumbs="[
        { label: '能力大厅', to: '/hall' },
        { label: capability?.display_name || 'GPT ImageGen' },
      ]"
      :title="capability?.display_name || 'GPT ImageGen'"
      hide-title
      hide-crumbs
      back-title="返回能力大厅"
    >
      <template #actions>
        <a-dropdown trigger="click">
          <a-button size="small">更多功能</a-button>
          <template #content>
            <a-doption disabled>工作区页面</a-doption>
            <a-doption @click="$emit('switch-chat')">对话页面</a-doption>
            <a-doption @click="$emit('switch-legacy')">高级表单</a-doption>
            <a-doption @click="paramsVisible = true">高级参数</a-doption>
            <a-doption :disabled="capability?.permissions?.execute === false" @click="queryTaskVisible = true">查询已有任务</a-doption>
            <a-doption :disabled="capability?.permissions?.execute === false" @click="uploadOnlyVisible = true">仅上传取 URL</a-doption>
          </template>
        </a-dropdown>
        <a-tag color="green" size="small">新版默认</a-tag>
        <a-tooltip content="任务异步提交，默认每用户最多 3 个活跃任务">
          <a-tag color="arcoblue" size="small">并发队列</a-tag>
        </a-tooltip>
      </template>
    </HallDetailHeader>

    <div v-if="pageWarning" class="page-warning" role="status">
      {{ pageWarning }}
      <button type="button" @click="retrySecondaryData">重试加载</button>
    </div>

    <a-spin :loading="loading" tip="加载能力配置...">
      <div v-if="capability" class="workspace-shell" :style="activeWorkspaceToneStyle">
        <section class="workspace-toolbar">
          <div class="workspace-toolbar-main">
            <span class="toolbar-kicker">工作区</span>
            <div class="workspace-tabs">
              <button
                v-for="workspace in workspaces"
                :key="workspace.id"
                type="button"
                class="workspace-tab"
                :class="{ active: activeWorkspaceId === workspace.id }"
                :style="workspaceToneStyle(workspace.id)"
                @click="activeWorkspaceId = workspace.id"
              >
                <span class="status-dot" :class="workspaceStatus(workspace.id)" />
                <input
                  v-if="renamingWorkspaceId === workspace.id"
                  v-model="renameDraft"
                  class="tab-rename-input"
                  maxlength="40"
                  @blur="finishRename"
                  @keydown.enter.prevent="finishRename"
                  @keydown.esc.prevent="cancelRename"
                  @click.stop
                />
                <span v-else class="tab-name" @dblclick.stop="startRename(workspace)">{{ workspace.name }}</span>
                <a-dropdown trigger="click">
                  <button class="tab-more" type="button" @click.stop>...</button>
                  <template #content>
                    <a-doption @click="startRename(workspace)">重命名</a-doption>
                    <a-doption @click="duplicateWorkspace(workspace)">复制配置</a-doption>
                    <a-doption :disabled="!canDeleteWorkspace(workspace)" @click="deleteWorkspace(workspace.id)">删除空工作区</a-doption>
                  </template>
                </a-dropdown>
              </button>
            </div>
          </div>
          <div class="workspace-toolbar-actions">
            <div class="task-capacity">
              <strong>{{ activeTaskCount }}</strong>
              <span>/ {{ activeLimit }} 活跃</span>
            </div>
            <button class="workspace-add" type="button" :disabled="workspaces.length >= 12" @click="addWorkspace">+ 新建</button>
          </div>
        </section>

        <div class="workspace-grid">
          <main class="result-column">
            <section class="result-card">
              <div class="card-head">
                <div>
                  <h3>当前工作区结果</h3>
                  <p>{{ activeWorkspace?.name || '-' }}</p>
                </div>
                <a-space>
                  <a-button v-if="activeWorkspaceImages.length" size="small" @click="copyImageUrls(activeWorkspaceImages)">复制 URL</a-button>
                </a-space>
              </div>
              <div v-if="activeWorkspaceImages.length" class="image-grid">
                <article v-for="image in activeWorkspaceImages" :key="image.id" class="image-card">
                  <img :src="image.url" :alt="image.name" @click="previewImage = image" />
                  <div class="image-tools">
                    <span :title="image.name">{{ image.name }}</span>
                    <div class="image-tool-buttons">
                      <a-button size="mini" @click="useImageAsReference(image.url, image.name)">继续修改</a-button>
                      <a-button size="mini" @click="openMaskEditor(image)">定点编辑</a-button>
                      <a-button size="mini" @click="downloadImage(image.url, image.name)">下载</a-button>
                    </div>
                  </div>
                </article>
              </div>
              <div v-else class="result-empty">
                <icon-image />
                <strong>还没有结果</strong>
                <span>提交任务后，完成图片会回填到当前工作区。</span>
              </div>
            </section>

            <section class="history-card">
              <div class="card-head">
                <div>
                  <h3>结果与历史</h3>
                  <p>已生成图片 {{ historyGeneratedCount }} 张 · 总大小 {{ historyTotalSizeLabel }}</p>
                </div>
                <a-radio-group v-model="historyFilter" type="button" size="small">
                  <a-radio value="workspace">当前工作区</a-radio>
                  <a-radio value="all">全部</a-radio>
                  <a-radio value="today">今天</a-radio>
                </a-radio-group>
              </div>
              <div v-if="filteredHistoryImages.length" class="history-grid">
                <button
                  v-for="image in filteredHistoryImages"
                  :key="image.id"
                  class="history-thumb"
                  type="button"
                  :title="image.name"
                  @click="previewImage = image"
                >
                  <img :src="image.url" :alt="image.name" />
                  <small>{{ formatHistoryLabel(image.created_at) }}</small>
                </button>
              </div>
              <div v-else class="history-empty">
                <span>{{ historyFilter === 'workspace' && historyImages.length ? '当前工作区暂无历史' : '暂无匹配历史' }}</span>
                <a-button
                  v-if="historyFilter === 'workspace' && historyImages.length"
                  size="mini"
                  type="text"
                  @click="historyFilter = 'all'"
                >
                  查看全部历史
                </a-button>
              </div>
              <a-button v-if="historyHasMore" size="small" :loading="historyLoading" @click="loadMoreHistory">更多历史</a-button>
            </section>
          </main>

          <aside class="config-column">
            <section class="config-card">
              <div class="card-head compact">
                <div>
                  <h3>当前工作区配置</h3>
                  <p>{{ activeWorkspace?.name || '-' }}</p>
                </div>
              </div>
              <input
                ref="fileInputEl"
                class="hidden-input"
                type="file"
                accept="image/png,image/jpeg,image/webp,image/gif"
                multiple
                @change="onPickFiles"
              />
              <template v-if="isUiFieldVisible('reference_images')">
              <label class="field-label">{{ uiFieldTitle('reference_images', '参考图') }}</label>
              <div
                class="reference-box"
                :class="{ dragging: referenceDragActive }"
                @click="openFilePicker"
                @dragenter.prevent="onReferenceDragEnter"
                @dragover.prevent="onReferenceDragOver"
                @dragleave.prevent="onReferenceDragLeave"
                @drop.prevent="onReferenceDrop"
              >
                <template v-if="activeForm.references.length">
                  <article v-for="item in activeForm.references" :key="item.path" class="ref-chip">
                    <button
                      class="ref-preview"
                      type="button"
                      :aria-label="`放大参考图 ${item.name}`"
                      @click.stop="openReferencePreview(item)"
                    >
                      <img v-if="item.preview_url" :src="item.preview_url" :alt="item.name" />
                      <icon-image v-else />
                    </button>
                    <span :title="item.name">{{ item.name }}</span>
                    <button class="ref-remove" type="button" aria-label="清理参考图" @click.stop="removeReference(item.path)">×</button>
                  </article>
                </template>
                <template v-else>
                  <icon-upload />
                  <span>点击或拖拽图片到这里</span>
                </template>
                <a-spin v-if="uploadingReferences" :size="16" />
              </div>
              </template>

              <template v-if="isUiFieldVisible('prompt')">
              <label class="field-label">{{ uiFieldTitle('prompt', '描述') }}</label>
              <a-textarea
                v-model="activeForm.prompt"
                :auto-size="{ minRows: 4, maxRows: 7 }"
                placeholder="描述要生成或修改的图片，例：白底商品图，柔光，高级商业摄影"
              />

              <div class="preset-row">
                <button v-for="item in presets" :key="item.key" type="button" @click="applyPreset(item.key)">
                  {{ item.title }}
                </button>
              </div>
              </template>

              <div v-if="visibleSettingFields.length" class="setting-grid">
                <template v-for="field in visibleSettingFields" :key="field.key">
                <div v-if="field.key === 'operation'" class="setting-field">
                  <span>{{ uiFieldTitle(field.key, field.label) }}</span>
                  <a-select v-model="activeForm.operation">
                    <a-option value="generate">生成</a-option>
                    <a-option value="edit">编辑</a-option>
                  </a-select>
                </div>
                <div v-else-if="field.key === 'size'" class="setting-field">
                  <span>{{ uiFieldTitle(field.key, field.label) }}</span>
                  <a-select v-model="activeForm.size">
                    <a-option v-for="ratio in ratios" :key="ratio" :value="ratio">{{ ratio }}</a-option>
                  </a-select>
                </div>
                <div v-else-if="field.key === 'resolution'" class="setting-field">
                  <span>{{ uiFieldTitle(field.key, field.label) }}</span>
                  <a-select v-model="activeForm.resolution">
                    <a-option value="1K">1K</a-option>
                    <a-option value="2K">2K</a-option>
                    <a-option value="4K">4K</a-option>
                  </a-select>
                </div>
                <div v-else-if="field.key === 'n'" class="setting-field">
                  <span>{{ uiFieldTitle(field.key, field.label) }}</span>
                  <a-input-number v-model="activeForm.n" :min="1" :max="4" :precision="0" />
                </div>
                </template>
              </div>

              <div v-if="isUiFieldVisible('thinking_mode')" class="thinking-toggle">
                <span>{{ uiFieldTitle('thinking_mode', 'Thinking') }}</span>
                <a-switch v-model="activeForm.thinking_mode" />
              </div>

              <a-button
                type="primary"
                long
                size="large"
                :disabled="!canSubmit"
                :loading="submitting"
                @click="submitTask"
              >
                <template #icon><icon-send /></template>
                提交到队列
              </a-button>
            </section>

            <section class="queue-card">
              <div class="card-head compact">
                <div>
                  <h3>生成队列</h3>
                  <p>{{ activeTaskCount }} / {{ activeLimit }} 活跃</p>
                </div>
                <a-button size="mini" :loading="tasksLoading" @click="loadTasks">刷新</a-button>
              </div>
              <div v-if="tasks.length" class="task-list">
                <article
                  v-for="task in tasks"
                  :key="task.id"
                  class="task-row"
                  :class="task.status"
                  :style="workspaceToneStyle(task.workspace_id)"
                  @click="selectTaskWorkspace(task)"
                >
                  <div class="task-thumb">
                    <img v-if="task.image_urls?.[0]" :src="task.image_urls[0]" alt="生成结果" />
                    <span v-else>{{ statusLabel(task.status).slice(0, 1) }}</span>
                  </div>
                  <div class="task-main">
                    <div class="task-title">
                      <strong>{{ task.workspace_name || workspaceName(task.workspace_id) }}</strong>
                      <a-tag size="small" :color="statusColor(task.status)">{{ statusLabel(task.status) }}</a-tag>
                    </div>
                    <div class="task-prompt">{{ task.prompt || task.params?.prompt || '无描述' }}</div>
                    <div class="task-meta">
                      <span>{{ task.upstream_task_id || task.id.slice(0, 8) }}</span>
                      <span>{{ task.params?.size || '-' }} · {{ task.params?.resolution || '-' }} · {{ task.params?.n || 1 }}张</span>
                    </div>
                    <a-progress v-if="task.status === 'in_progress'" :percent="taskProgress(task)" size="mini" />
                    <div v-if="task.error" class="task-error">{{ task.error }}</div>
                  </div>
                  <div class="task-actions" @click.stop>
                    <a-button size="mini" @click="copyTaskConfig(task)">复制配置</a-button>
                    <a-button size="mini" :disabled="!canRetry(task)" @click="retryTask(task)">重试</a-button>
                    <a-button size="mini" @click="openTaskJson(task)">JSON</a-button>
                  </div>
                </article>
              </div>
              <div v-else class="queue-empty">暂无任务</div>
            </section>
          </aside>
        </div>
      </div>
      <SfEmptyState v-else-if="!loading" title="能力不可用" description="未找到该能力" hint="请返回能力大厅确认发布状态和权限。" icon="image" />
    </a-spin>

    <a-modal v-model:visible="jsonVisible" title="任务 JSON" :footer="false" :width="760">
      <pre class="json-block">{{ selectedTask ? JSON.stringify(selectedTask, null, 2) : '{}' }}</pre>
    </a-modal>

    <a-modal v-model:visible="uploadOnlyVisible" title="仅上传取 URL" :footer="false" :width="560">
      <p class="modal-hint">选择本地图片，平台会保存为参考图 URL，不创建生成任务。</p>
      <a-upload :custom-request="customUploadOnly" multiple :show-file-list="false" accept="image/*">
        <template #upload-button>
          <a-button type="primary">
            <template #icon><icon-upload /></template>
            选择图片
          </a-button>
        </template>
      </a-upload>
      <ul v-if="uploadOnlyResults.length" class="upload-only-results">
        <li v-for="item in uploadOnlyResults" :key="item.path">
          <strong :title="item.name">{{ item.name }}</strong>
          <code :title="item.path">{{ item.path }}</code>
          <a-button size="mini" @click="copyUrl(item.path)">复制</a-button>
          <a-button size="mini" @click="addUploadOnlyToWorkspace(item)">加入当前工作区</a-button>
        </li>
      </ul>
    </a-modal>

    <a-modal
      v-model:visible="queryTaskVisible"
      title="查询已有任务"
      ok-text="提交查询"
      :ok-loading="submitting"
      @ok="queryExistingTask"
      @cancel="queryTaskId = ''"
    >
      <p class="modal-hint">输入已有图片任务 ID，会作为当前工作区的队列任务持续轮询。</p>
      <a-input v-model="queryTaskId" placeholder="tsk_img_..." allow-clear />
    </a-modal>

    <a-drawer v-model:visible="paramsVisible" title="高级参数" placement="right" :width="520" :footer="false">
      <GptImageParamInspector
        v-model="paramOverrides"
        :title="capability?.display_name || 'GPT ImageGen'"
        :schema="capability?.input_schema"
        :required-fields="capability?.input_schema?.required || []"
      />
    </a-drawer>

    <a-modal
      v-model:visible="maskEditorVisible"
      title="定点编辑"
      :width="'92vw'"
      :footer="false"
    >
      <div class="mask-editor">
        <div class="mask-toolbar">
          <span class="mask-source" :title="maskSource?.name">{{ maskSource?.name || '当前图片' }}</span>
          <span class="mask-region-indicator" :style="{ '--region-color': activeMaskRegion.color }">
            区域 {{ activeMaskRegion.index }}
          </span>
          <label>画笔</label>
          <a-slider v-model="maskBrushSize" :min="8" :max="96" :step="2" :style="{ width: '160px' }" />
          <span class="mask-size">{{ maskBrushSize }}px</span>
          <a-button size="small" :type="maskEraser ? 'primary' : 'secondary'" @click="maskEraser = !maskEraser">
            {{ maskEraser ? '橡皮中' : '橡皮' }}
          </a-button>
          <a-button size="small" :disabled="maskSizing" @click="clearMaskCanvas">清空</a-button>
        </div>
        <div class="mask-canvas-stage">
          <a-spin v-if="maskSizing" tip="加载图片尺寸..." />
          <div
            v-else-if="maskSource"
            class="mask-canvas-wrap"
            :style="{ width: `${maskDisplay.width}px`, height: `${maskDisplay.height}px` }"
          >
            <img :src="maskSource.url" :alt="maskSource.name" />
            <canvas
              ref="maskCanvasEl"
              class="mask-canvas"
              :width="maskNatural.width"
              :height="maskNatural.height"
              @pointerdown="startMaskPaint"
              @pointermove="moveMaskPaint"
              @pointerup="stopMaskPaint"
              @pointercancel="stopMaskPaint"
              @pointerleave="stopMaskPaint"
            />
          </div>
        </div>
        <div class="mask-region-panel">
          <div class="mask-region-list">
            <button
              v-for="region in maskRegions"
              :key="region.id"
              type="button"
              class="mask-region-tab"
              :class="{ active: activeMaskRegionId === region.id }"
              :style="{ '--region-color': region.color }"
              @click="activeMaskRegionId = region.id"
            >
              <span class="region-swatch" />
              <span>区域 {{ region.index }}</span>
            </button>
            <a-button size="small" :disabled="maskRegions.length >= maxMaskRegions" @click="addMaskRegion">添加区域</a-button>
          </div>
          <div class="mask-region-editor">
            <a-textarea
              v-model="activeMaskRegion.prompt"
              :auto-size="{ minRows: 2, maxRows: 4 }"
              :placeholder="`描述区域 ${activeMaskRegion.index} 要怎么改，例如：把这块背景换成淡黄色`"
            />
            <a-button
              v-if="maskRegions.length > 1"
              size="small"
              status="danger"
              @click="removeMaskRegion(activeMaskRegion.id)"
            >
              删除当前区域
            </a-button>
          </div>
        </div>
        <div class="mask-footer">
          <a-button @click="maskEditorVisible = false">取消</a-button>
          <a-button type="primary" :loading="uploadingReferences" :disabled="maskSizing" @click="applyMaskEdit">使用定点区域</a-button>
        </div>
      </div>
    </a-modal>

    <a-modal :visible="!!previewImage" :footer="false" :width="960" @cancel="previewImage = null">
      <div v-if="previewImage" class="preview-box">
        <img :src="previewImage.url" :alt="previewImage.name" />
        <div class="preview-actions">
          <strong>{{ previewImage.name }}</strong>
          <div class="preview-action-buttons">
            <a-button size="small" @click="useImageAsReference(previewImage.url, previewImage.name)">继续修改</a-button>
            <a-button size="small" @click="openMaskEditor(previewImage)">定点编辑</a-button>
            <a-button size="small" @click="copyImageUrls([previewImage])">复制 URL</a-button>
            <a-button size="small" @click="downloadImage(previewImage.url, previewImage.name)">下载</a-button>
          </div>
        </div>
      </div>
    </a-modal>

    <a-modal :visible="!!previewReference" :footer="false" :width="860" @cancel="previewReference = null">
      <div v-if="previewReference" class="preview-box">
        <img v-if="referenceDisplayUrl(previewReference)" :src="referenceDisplayUrl(previewReference)" :alt="previewReference.name" />
        <div v-else class="reference-preview-empty">
          <icon-image />
          <span>{{ previewReference.path }}</span>
        </div>
        <div class="preview-actions">
          <strong>{{ previewReference.name }}</strong>
          <div class="preview-action-buttons">
            <a-button size="small" :disabled="!canMaskEditReference(previewReference)" @click="openReferenceMaskEditor(previewReference)">定点编辑</a-button>
            <a-button size="small" @click="copyUrl(previewReference.path)">复制 URL</a-button>
            <a-button size="small" status="danger" @click="removeReferenceFromPreview">清理</a-button>
          </div>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { IconImage, IconSend, IconUpload } from '@arco-design/web-vue/es/icon'
import { hallApi } from '@/api'
import SfEmptyState from '@/components/common/SfEmptyState.vue'
import HallDetailHeader from '@/components/hall/HallDetailHeader.vue'
import GptImageParamInspector from '@/components/hall/GptImageParamInspector.vue'
import { normalizeGptImageResolution } from '@/pages/hall/gptImageParams'
import { copyText } from '@/utils/clipboard'
import { formatTimeOnly, toDate } from '@/utils/format'

const props = defineProps<{
  runUi?: DirectCapabilityUi | null
}>()

defineEmits(['switch-legacy', 'switch-chat'])

type DirectCapabilityUi = {
  merged_schema?: {
    components?: UiComponent[]
    personal_defaults?: Record<string, unknown>
  }
  merged_ui_schema_hash?: string
}

type UiComponent = {
  id?: string
  type?: string
  title?: string
  field?: string
  binding?: string
  visible?: boolean
  children?: UiComponent[]
}

type WorkspaceForm = {
  prompt: string
  operation: 'generate' | 'edit'
  size: string
  resolution: '1K' | '2K' | '4K'
  n: number
  thinking_mode: boolean
  references: UploadedReference[]
  mask_path?: string
  mask_guide_path?: string
  mask_source_path?: string
}

type Workspace = {
  id: string
  name: string
  form: WorkspaceForm
}

type UploadedReference = {
  id?: string
  name: string
  path: string
  size?: number
  width?: number
  height?: number
  ratio?: string
  preview_url?: string
}

type GalleryImage = {
  id: string
  url: string
  name: string
  byte_size?: number | null
  created_at?: string
  task_id?: string
  prompt?: string
  extra?: Record<string, any>
}

type HistoryStats = {
  total_count: number
  known_count: number
  unknown_count: number
  known_bytes: number
}

type MaskRegion = {
  id: string
  index: number
  color: string
  prompt: string
}

type DirectTask = {
  id: string
  workspace_id?: string
  workspace_name?: string
  upstream_task_id?: string
  status: string
  prompt?: string
  params?: Record<string, any>
  result?: Record<string, any>
  image_urls?: string[]
  error?: string
  created_at?: string
  updated_at?: string
  completed_at?: string
}

const route = useRoute()
const capabilityId = computed(() => String(route.params.id || ''))
const loading = ref(false)
const capability = ref<any>(null)
const pageWarning = ref('')
const workspaces = ref<Workspace[]>([])
const activeWorkspaceId = ref('')
const tasks = ref<DirectTask[]>([])
const tasksLoading = ref(false)
const activeLimit = ref(3)
const submitting = ref(false)
const historyImages = ref<GalleryImage[]>([])
const historyTotal = ref(0)
const historyStats = ref<HistoryStats>({ total_count: 0, known_count: 0, unknown_count: 0, known_bytes: 0 })
const historyPage = ref(1)
const historyLoading = ref(false)
const historyPageSize = 40
// 历史是服务端按用户永久保存的数据，工作区只是当前浏览器的本地组织方式。
// 新浏览器、清理 localStorage 或工作区重建后，旧图片通常没有当前 workspace_id；
// 默认按工作区过滤会让用户误以为历史丢失，因此默认直接展示全部历史。
const historyFilter = ref<'workspace' | 'all' | 'today'>('all')
const fileInputEl = ref<HTMLInputElement | null>(null)
const uploadingReferences = ref(false)
const jsonVisible = ref(false)
const selectedTask = ref<DirectTask | null>(null)
const previewImage = ref<GalleryImage | null>(null)
const previewReference = ref<UploadedReference | null>(null)
const referenceDragActive = ref(false)
const paramsVisible = ref(false)
const queryTaskVisible = ref(false)
const queryTaskId = ref('')
const uploadOnlyVisible = ref(false)
const uploadOnlyResults = ref<UploadedReference[]>([])
const paramOverrides = ref<Record<string, unknown>>({ wait: false, download: false })
const maskEditorVisible = ref(false)
const maskSource = ref<GalleryImage | null>(null)
const maskCanvasEl = ref<HTMLCanvasElement | null>(null)
const maskNatural = ref({ width: 1, height: 1 })
const maskDisplay = ref({ width: 640, height: 640 })
const maskSizing = ref(false)
const maskBrushSize = ref(36)
const maskEraser = ref(false)
const maskHasPaint = ref(false)
const maskRegions = ref<MaskRegion[]>([])
const activeMaskRegionId = ref('')
const renamingWorkspaceId = ref('')
const renameDraft = ref('')
const completedTaskIds = ref<Set<string>>(new Set())
let taskTimer: number | undefined
let fastPollUntil = 0
const pendingHistoryTaskDeadlines = new Map<string, number>()
const watchedTaskTimers = new Map<string, number>()
const watchedTaskAttempts = new Map<string, number>()
const watchedCompatTasks = new Map<string, DirectTask>()
let taskRequestCapabilityId = ''
let maskPainting = false
let maskLastPoint: { x: number; y: number } | null = null
let referenceDragDepth = 0

const ratios = ['1:1', '3:2', '2:3', '4:3', '3:4', '5:4', '4:5', '16:9', '9:16', '2:1', '1:2', '21:9', '9:21']
const maxMaskRegions = 6
const pendingHistoryTaskTimeoutMs = 120000
const watchedTaskMaxAttempts = 180
const maskRegionColors = [
  'rgba(255, 64, 64, 0.68)',
  'rgba(37, 99, 235, 0.68)',
  'rgba(5, 150, 105, 0.68)',
  'rgba(217, 119, 6, 0.72)',
  'rgba(124, 58, 237, 0.68)',
  'rgba(8, 145, 178, 0.68)',
]
const presets = [
  { key: 'main', title: '主图', size: '1:1', resolution: '1K', prompt: '请生成一张高转化电商商品主图。产品主体清晰居中，背景干净，柔光，高级商业摄影质感，不出现水印、二维码或乱码文字。' },
  { key: 'white', title: '白底', size: '1:1', resolution: '1K', prompt: '请生成一张电商白底主图。产品居中，背景纯白，边缘干净，阴影自然，不增加多余道具、文字或水印。' },
  { key: 'banner', title: 'Banner', size: '16:9', resolution: '2K', prompt: '请生成一张电商活动 Banner 横图。产品位于视觉黄金区，侧边保留文案区域，背景有活动氛围但不抢主体。' },
  { key: 'detail', title: '详情', size: '9:16', resolution: '2K', prompt: '请生成一张电商详情页卖点图。信息层级清楚，展示产品功能、材质、适用场景和购买理由。' },
]
const settingFieldDefs = [
  { key: 'operation', label: '动作' },
  { key: 'size', label: '比例' },
  { key: 'resolution', label: '分辨率' },
  { key: 'n', label: '张数' },
]
const uiFieldAliases: Record<string, string[]> = {
  reference_images: ['reference_images', 'image_urls'],
  prompt: ['prompt'],
  operation: ['operation'],
  size: ['size'],
  resolution: ['resolution'],
  n: ['n'],
  thinking_mode: ['thinking_mode'],
}

function formatFileSize(bytes: number | undefined | null) {
  const value = Number(bytes || 0)
  if (!Number.isFinite(value) || value <= 0) return '-'
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)}KB`
  return `${(value / 1024 / 1024).toFixed(2)}MB`
}

function uploadErrorMessage(error: any, file?: File) {
  const backend = error?._backendMessage || error?.response?.data?.detail || error?.response?.data?.message
  const message = backend || error?._message || error?.message || '上传失败'
  const fileInfo = file ? `${file.name}（${formatFileSize(file.size)}）` : '图片'
  return `${fileInfo} 上传失败：${message}`
}

const activeWorkspace = computed(() => workspaces.value.find(item => item.id === activeWorkspaceId.value) || workspaces.value[0] || null)
const activeForm = computed(() => activeWorkspace.value?.form || defaultForm())
const activeWorkspaceToneStyle = computed(() => workspaceToneStyle(activeWorkspaceId.value))
const activeTaskCount = computed(() => tasks.value.filter(task => ['submitting', 'in_progress', 'rate_limited'].includes(task.status)).length)
const canSubmit = computed(() => {
  if (!capability.value || capability.value?.permissions?.execute === false) return false
  if (submitting.value || uploadingReferences.value) return false
  return Boolean(String(activeForm.value.prompt || '').trim())
})
const activeWorkspaceImages = computed<GalleryImage[]>(() => {
  const workspace = activeWorkspace.value
  const taskImages = tasks.value
    .filter(task => taskMatchesWorkspace(task, workspace) && task.status === 'completed' && task.image_urls?.length)
    .slice(0, 3)
    .flatMap(task => (task.image_urls || []).map((url, idx) => ({
      id: `${task.id}-${idx}`,
      url,
      name: filenameFromUrl(url, `生成图 ${idx + 1}`),
      created_at: task.completed_at || task.created_at,
      task_id: task.upstream_task_id,
      prompt: task.prompt,
    })))
  const workspaceHistory = historyImages.value.filter(item => imageMatchesWorkspace(item, workspace))
  const mergedWorkspaceImages = mergeImages(taskImages, workspaceHistory)
  if (mergedWorkspaceImages.length) return mergedWorkspaceImages.slice(0, 6)
  const workspaceHasTasks = tasks.value.some(task => taskMatchesWorkspace(task, workspace))
  return workspaceHasTasks ? [] : historyImages.value.slice(0, 6)
})
const hasLiveTasks = computed(() => tasks.value.some(task => !['completed', 'failed'].includes(task.status)))
const filteredHistoryImages = computed(() => {
  const today = new Date().toDateString()
  if (historyFilter.value === 'workspace') {
    return historyImages.value.filter(item => imageMatchesWorkspace(item, activeWorkspace.value))
  }
  if (historyFilter.value === 'today') {
    return historyImages.value.filter(item => {
      const date = toDate(item.created_at)
      return date ? date.toDateString() === today : false
    })
  }
  return historyImages.value
})
const historyHasMore = computed(() => historyImages.value.length < historyTotal.value)
const historyGeneratedCount = computed(() => historyStats.value.total_count || historyTotal.value || historyImages.value.length)
const historyTotalSizeLabel = computed(() => {
  const bytes = Number(historyStats.value.known_bytes || 0)
  const unknown = Number(historyStats.value.unknown_count || 0)
  if (bytes > 0 && unknown > 0) return `${formatFileSize(bytes)} 已知，${unknown} 张未知`
  if (bytes > 0) return formatFileSize(bytes)
  if (unknown > 0) return `${unknown} 张待同步`
  return '-'
})
const activeMaskRegion = computed(() => {
  return maskRegions.value.find(region => region.id === activeMaskRegionId.value)
    || maskRegions.value[0]
    || createMaskRegion(0)
})
const flatUiComponents = computed(() => flattenUiComponents(props.runUi?.merged_schema?.components || []))
const visibleSettingFields = computed(() => settingFieldDefs
  .filter(field => isUiFieldVisible(field.key))
  .sort((a, b) => uiFieldOrder(a.key) - uiFieldOrder(b.key)))
const workspaceTones = [
  { color: 'var(--ai-accent)', bg: 'var(--ai-accent-soft)', border: 'var(--ai-border-2)', ring: 'var(--ai-accent-soft)' },
  { color: 'var(--ai-ok)', bg: 'var(--ai-ok-soft)', border: 'var(--ai-border-2)', ring: 'var(--ai-ok-soft)' },
  { color: 'var(--ai-warn)', bg: 'var(--ai-warn-soft)', border: 'var(--ai-border-2)', ring: 'var(--ai-warn-soft)' },
  { color: 'var(--ai-info)', bg: 'var(--ai-info-soft)', border: 'var(--ai-border-2)', ring: 'var(--ai-info-soft)' },
  { color: 'var(--ai-bad)', bg: 'var(--ai-bad-soft)', border: 'var(--ai-border-2)', ring: 'var(--ai-bad-soft)' },
  { color: 'var(--ai-accent-ink)', bg: 'var(--ai-accent-soft)', border: 'var(--ai-border-2)', ring: 'var(--ai-accent-soft)' },
]

watch(workspaces, persistWorkspaces, { deep: true })
watch(
  () => props.runUi?.merged_ui_schema_hash,
  () => {
    for (const workspace of workspaces.value) {
      applyUiDefaults(workspace.form, { preservePrompt: true })
    }
  },
)
watch(() => activeForm.value.size, (size) => {
  activeForm.value.resolution = normalizeGptImageResolution(size, activeForm.value.resolution)
})
watch(() => activeForm.value.resolution, (resolution) => {
  activeForm.value.resolution = normalizeGptImageResolution(activeForm.value.size, resolution)
})
watch(() => route.params.id, load)

onMounted(load)
onBeforeUnmount(() => {
  stopTaskPolling()
  stopWatchedTasks()
  revokePreviewUrls()
})

function defaultForm(): WorkspaceForm {
  const form: WorkspaceForm = {
    prompt: '',
    operation: 'generate',
    size: '1:1',
    resolution: '1K',
    n: 1,
    thinking_mode: false,
    references: [],
    mask_path: '',
    mask_guide_path: '',
    mask_source_path: '',
  }
  applyUiDefaults(form, { preservePrompt: false })
  return form
}

function defaultWorkspaces(): Workspace[] {
  return Array.from({ length: 4 }, (_, idx) => ({
    id: `ws-${Date.now()}-${idx}-${Math.random().toString(16).slice(2, 8)}`,
    name: defaultWorkspaceName(idx),
    form: defaultForm(),
  }))
}

function defaultWorkspaceName(idx: number) {
  return `工作区${idx + 1}`
}

function flattenUiComponents(components: UiComponent[]): UiComponent[] {
  const result: UiComponent[] = []
  for (const component of components || []) {
    if (!component || typeof component !== 'object') continue
    result.push(component)
    if (Array.isArray(component.children)) {
      result.push(...flattenUiComponents(component.children))
    }
  }
  return result
}

function uiAliases(field: string): string[] {
  return uiFieldAliases[field] || [field]
}

function componentsForField(field: string): UiComponent[] {
  const aliases = new Set(uiAliases(field))
  return flatUiComponents.value.filter((component) => {
    if (component.type !== 'field') return false
    if (component.field && aliases.has(component.field)) return true
    const binding = String(component.binding || '')
    return [...aliases].some(alias => binding === `params.${alias}`)
  })
}

function isUiFieldVisible(field: string): boolean {
  const components = componentsForField(field)
  if (!components.length) return true
  return components.some(component => component.visible !== false)
}

function uiFieldTitle(field: string, fallback: string): string {
  const component = componentsForField(field).find(item => item.visible !== false) || componentsForField(field)[0]
  return String(component?.title || fallback)
}

function uiFieldOrder(field: string): number {
  const components = componentsForField(field)
  if (!components.length) return 1000 + settingFieldDefs.findIndex(item => item.key === field)
  const ids = components.map(item => item.id).filter(Boolean)
  const order = flatUiComponents.value.findIndex(item => item.id && ids.includes(item.id))
  return order >= 0 ? order : 1000
}

function applyUiDefaults(form: WorkspaceForm, options: { preservePrompt: boolean }) {
  const defaults = props.runUi?.merged_schema?.personal_defaults || {}
  const prompt = defaults.prompt
  if (typeof prompt === 'string' && (!options.preservePrompt || !form.prompt.trim())) {
    form.prompt = prompt
  }
  const size = defaults.size
  if (typeof size === 'string' && ratios.includes(size)) {
    form.size = size
  }
  const resolution = defaults.resolution
  if (resolution === '1K' || resolution === '2K' || resolution === '4K') {
    form.resolution = resolution
  }
  const n = Number(defaults.n)
  if (Number.isFinite(n) && n >= 1) {
    form.n = Math.min(4, Math.max(1, Math.round(n)))
  }
}

function isLegacyAutoWorkspaceName(name: string) {
  const normalized = name.trim()
  return ['主图 A', '白底图', 'Banner', '详情图'].includes(normalized)
    || /^方案\s*\d+$/.test(normalized)
    || /^工作区\s+\d+$/.test(normalized)
}

function normalizeLoadedWorkspaceName(name: unknown, idx: number) {
  const sanitized = sanitizeWorkspaceName(String(name || ''))
  return !sanitized || isLegacyAutoWorkspaceName(sanitized) ? defaultWorkspaceName(idx) : sanitized
}

function nextWorkspaceName() {
  const used = new Set(workspaces.value.map(item => item.name))
  for (let idx = 0; idx < 12; idx += 1) {
    const name = defaultWorkspaceName(idx)
    if (!used.has(name)) return name
  }
  return defaultWorkspaceName(workspaces.value.length)
}

function storageKey() {
  return `skillforge:gpt-imagegen:workspaces:${capabilityId.value}`
}

function loadWorkspaceState() {
  revokePreviewUrls()
  try {
    const raw = localStorage.getItem(storageKey())
    const parsed = raw ? JSON.parse(raw) : null
    if (Array.isArray(parsed?.items) && parsed.items.length) {
      workspaces.value = parsed.items.slice(0, 12).map((item: any, idx: number) => ({
        id: String(item.id || `ws-${Math.random().toString(16).slice(2)}`),
        name: normalizeLoadedWorkspaceName(item.name, idx),
        form: {
          ...defaultForm(),
          ...(item.form || {}),
          references: Array.isArray(item.form?.references) ? item.form.references : [],
        },
      }))
      activeWorkspaceId.value = parsed.activeWorkspaceId && workspaces.value.some(item => item.id === parsed.activeWorkspaceId)
        ? parsed.activeWorkspaceId
        : workspaces.value[0].id
      return
    }
  } catch {
    // ignore broken local state
  }
  workspaces.value = defaultWorkspaces()
  activeWorkspaceId.value = workspaces.value[0]?.id || ''
}

function persistWorkspaces() {
  if (!capabilityId.value || !workspaces.value.length) return
  const items = workspaces.value.map(workspace => ({
    ...workspace,
    form: {
      ...workspace.form,
      references: workspace.form.references.map(({ preview_url: _preview, ...rest }) => rest),
    },
  }))
  localStorage.setItem(storageKey(), JSON.stringify({ activeWorkspaceId: activeWorkspaceId.value, items }))
}

async function load() {
  if (!capabilityId.value) return
  loading.value = true
  tasks.value = []
  historyImages.value = []
  historyTotal.value = 0
  historyStats.value = { total_count: 0, known_count: 0, unknown_count: 0, known_bytes: 0 }
  historyPage.value = 1
  completedTaskIds.value = new Set()
  pendingHistoryTaskDeadlines.clear()
  stopTaskPolling()
  loadWorkspaceState()
  pageWarning.value = ''
  try {
    capability.value = await hallApi.directCapability(capabilityId.value)
  } catch {
    capability.value = null
    loading.value = false
    return
  }
  try {
    await Promise.all([loadTasks(), loadImageHistory({ page: 1, append: false })])
    startTaskPolling(1000)
  } finally {
    loading.value = false
  }
}

async function retrySecondaryData() {
  pageWarning.value = ''
  await Promise.all([loadTasks(), loadImageHistory({ page: 1, append: false })])
}

function stopTaskPolling() {
  if (taskTimer) {
    window.clearInterval(taskTimer)
    taskTimer = undefined
  }
}

function stopWatchedTasks() {
  watchedTaskTimers.forEach(timer => window.clearTimeout(timer))
  watchedTaskTimers.clear()
  watchedTaskAttempts.clear()
  watchedCompatTasks.clear()
}

function clearWatchedTaskTimer(taskId: string) {
  const timer = watchedTaskTimers.get(taskId)
  if (timer) window.clearTimeout(timer)
  watchedTaskTimers.delete(taskId)
}

function stopWatchingTask(taskId: string) {
  clearWatchedTaskTimer(taskId)
  watchedTaskAttempts.delete(taskId)
  watchedCompatTasks.delete(taskId)
}

function startTaskPolling(interval = 3000) {
  stopTaskPolling()
  taskTimer = window.setInterval(() => {
    void loadTasks()
  }, interval)
}

function tuneTaskPolling() {
  const interval = hasLiveTasks.value || hasPendingHistoryRefresh(tasks.value) || Date.now() < fastPollUntil ? 1000 : 3000
  if (taskTimer) {
    stopTaskPolling()
    startTaskPolling(interval)
  }
}

function addWorkspace() {
  if (workspaces.value.length >= 12) return
  const workspace = {
    id: `ws-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
    name: nextWorkspaceName(),
    form: defaultForm(),
  }
  workspaces.value.push(workspace)
  activeWorkspaceId.value = workspace.id
}

function duplicateWorkspace(workspace: Workspace) {
  if (workspaces.value.length >= 12) return
  const copy = {
    id: `ws-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
    name: nextWorkspaceName(),
    form: {
      ...workspace.form,
      references: workspace.form.references.map(item => ({ ...item })),
    },
  }
  workspaces.value.push(copy)
  activeWorkspaceId.value = copy.id
}

function canDeleteWorkspace(workspace: Workspace) {
  if (workspaces.value.length <= 1) return false
  return !tasks.value.some(task => task.workspace_id === workspace.id)
}

function deleteWorkspace(id: string) {
  const workspace = workspaces.value.find(item => item.id === id)
  if (!workspace || !canDeleteWorkspace(workspace)) return
  workspaces.value = workspaces.value.filter(item => item.id !== id)
  if (activeWorkspaceId.value === id) activeWorkspaceId.value = workspaces.value[0]?.id || ''
}

function startRename(workspace: Workspace) {
  renamingWorkspaceId.value = workspace.id
  renameDraft.value = workspace.name
}

function finishRename() {
  const workspace = workspaces.value.find(item => item.id === renamingWorkspaceId.value)
  if (workspace) workspace.name = sanitizeWorkspaceName(renameDraft.value)
  renamingWorkspaceId.value = ''
  renameDraft.value = ''
}

function cancelRename() {
  renamingWorkspaceId.value = ''
  renameDraft.value = ''
}

function sanitizeWorkspaceName(value: string) {
  const text = String(value || '').trim().replace(/\s+/g, ' ')
  return (text || '工作区').slice(0, 40)
}

function workspaceName(id?: string) {
  return workspaces.value.find(item => item.id === id)?.name || '未归类'
}

function taskMatchesWorkspace(task: DirectTask, workspace: Workspace | null) {
  if (!workspace) return false
  if (task.workspace_id && task.workspace_id === workspace.id) return true
  if (!task.workspace_id && task.workspace_name && task.workspace_name === workspace.name) return true
  return Boolean(task.workspace_id && task.workspace_id !== workspace.id && task.workspace_name && task.workspace_name === workspace.name)
}

function imageMatchesWorkspace(image: GalleryImage, workspace: Workspace | null) {
  if (!workspace) return false
  const extra = image.extra || {}
  if (extra.workspace_id && extra.workspace_id === workspace.id) return true
  if (!extra.workspace_id && extra.workspace_name && extra.workspace_name === workspace.name) return true
  return Boolean(extra.workspace_id && extra.workspace_id !== workspace.id && extra.workspace_name && extra.workspace_name === workspace.name)
}

function workspaceToneStyle(id?: string) {
  const idx = Math.max(0, workspaces.value.findIndex(item => item.id === id))
  const tone = workspaceTones[idx % workspaceTones.length] || workspaceTones[0]
  return {
    '--ws-color': tone.color,
    '--ws-bg': tone.bg,
    '--ws-border': tone.border,
    '--ws-ring': tone.ring,
  }
}

function workspaceStatus(id: string) {
  const related = tasks.value.filter(task => task.workspace_id === id)
  if (related.some(task => task.status === 'failed')) return 'failed'
  if (related.some(task => task.status === 'rate_limited')) return 'rate_limited'
  if (related.some(task => ['submitting', 'in_progress'].includes(task.status))) return 'running'
  if (related.some(task => task.status === 'queued')) return 'queued'
  if (related.some(task => task.status === 'completed')) return 'completed'
  return 'idle'
}

function applyPreset(key: string) {
  const preset = presets.find(item => item.key === key)
  if (!preset) return
  activeForm.value.prompt = preset.prompt
  activeForm.value.size = preset.size
  activeForm.value.resolution = preset.resolution as '1K' | '2K' | '4K'
}

function openFilePicker() {
  fileInputEl.value?.click()
}

async function onPickFiles(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  await uploadReferenceFiles(files)
}

function imageFilesFromTransfer(dataTransfer: DataTransfer | null): File[] {
  return Array.from(dataTransfer?.files || []).filter(file => /^image\//i.test(file.type))
}

function onReferenceDragEnter(event: DragEvent) {
  referenceDragDepth += 1
  if (imageFilesFromTransfer(event.dataTransfer).length || event.dataTransfer?.items?.length) {
    referenceDragActive.value = true
  }
}

function onReferenceDragOver(event: DragEvent) {
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
  referenceDragActive.value = true
}

function onReferenceDragLeave() {
  referenceDragDepth = Math.max(0, referenceDragDepth - 1)
  if (referenceDragDepth === 0) referenceDragActive.value = false
}

async function onReferenceDrop(event: DragEvent) {
  referenceDragDepth = 0
  referenceDragActive.value = false
  const files = imageFilesFromTransfer(event.dataTransfer)
  if (!files.length) {
    Message.warning('请拖入图片文件')
    return
  }
  await uploadReferenceFiles(files)
}

async function uploadReferenceFiles(files: File[]) {
  if (!files.length || !capabilityId.value) return
  uploadingReferences.value = true
  const ok: UploadedReference[] = []
  const failed: string[] = []
  try {
    for (const file of files) {
      let previewUrl = ''
      try {
        previewUrl = URL.createObjectURL(file)
        const meta = await readImageMeta(previewUrl)
        const response: any = await hallApi.uploadDirectCapabilityReference(capabilityId.value, file)
        ok.push({
          id: response.id,
          name: response.original_name || file.name,
          path: response.path,
          size: response.size ?? file.size,
          width: meta.width,
          height: meta.height,
          ratio: meta.ratio,
          preview_url: previewUrl,
        })
      } catch (error: any) {
        if (previewUrl) URL.revokeObjectURL(previewUrl)
        failed.push(uploadErrorMessage(error, file))
      }
    }
    if (ok.length) {
      activeForm.value.references = [...activeForm.value.references, ...ok]
      Message.success(`已上传 ${ok.length} 张参考图`)
    }
    if (failed.length) {
      Message.error(failed.slice(0, 3).join('；'))
    }
  } finally {
    uploadingReferences.value = false
  }
}

function removeReference(path: string) {
  const target = activeForm.value.references.find(item => item.path === path)
  if (target?.preview_url?.startsWith('blob:')) URL.revokeObjectURL(target.preview_url)
  activeForm.value.references = activeForm.value.references.filter(item => item.path !== path)
  if (previewReference.value?.path === path) previewReference.value = null
  reconcileMaskReferences(activeForm.value)
}

function openReferencePreview(item: UploadedReference) {
  previewReference.value = item
}

function referenceDisplayUrl(item?: UploadedReference | null) {
  if (!item) return ''
  if (item.preview_url) return item.preview_url
  return renderableImageUrl(item.path) ? item.path : ''
}

function canMaskEditReference(item?: UploadedReference | null) {
  return Boolean(referenceDisplayUrl(item))
}

function referenceToGalleryImage(item: UploadedReference): GalleryImage | null {
  const url = referenceDisplayUrl(item)
  if (!url) return null
  return {
    id: `reference-${item.id || item.path}`,
    url,
    name: item.name || filenameFromUrl(item.path, '参考图.png'),
    extra: {
      reference_path: item.path,
    },
  }
}

function openReferenceMaskEditor(item: UploadedReference) {
  const image = referenceToGalleryImage(item)
  if (!image) {
    Message.warning('参考图预览不可用，请重新上传后再定点编辑')
    return
  }
  previewReference.value = null
  openMaskEditor(image)
}

function removeReferenceFromPreview() {
  const path = previewReference.value?.path
  if (!path) return
  removeReference(path)
}

function reconcileMaskReferences(form: WorkspaceForm) {
  if (!form.mask_path) return
  const paths = new Set(form.references.map(item => item.path))
  if ((form.mask_guide_path && !paths.has(form.mask_guide_path)) || (form.mask_source_path && !paths.has(form.mask_source_path))) {
    form.mask_path = ''
    form.mask_guide_path = ''
    form.mask_source_path = ''
  }
}

function revokePreviewUrls() {
  workspaces.value.forEach(workspace => {
    revokeReferencePreviewUrls(workspace.form.references)
  })
}

function revokeReferencePreviewUrls(references: UploadedReference[], keepUrls = new Set<string>()) {
  references.forEach(item => {
    if (item.preview_url?.startsWith('blob:') && !keepUrls.has(item.preview_url)) URL.revokeObjectURL(item.preview_url)
  })
}

function readImageMeta(url: string): Promise<{ width?: number; height?: number; ratio?: string }> {
  return new Promise((resolve) => {
    const image = new Image()
    image.onload = () => {
      const width = image.naturalWidth || undefined
      const height = image.naturalHeight || undefined
      resolve({ width, height, ratio: width && height ? closestRatio(width / height) : undefined })
    }
    image.onerror = () => resolve({})
    image.src = url
  })
}

function closestRatio(aspect: number): string {
  if (!Number.isFinite(aspect) || aspect <= 0) return '1:1'
  return ratios.reduce((best, ratio) => {
    const score = Math.abs(ratioValue(ratio) - aspect)
    return score < Math.abs(ratioValue(best) - aspect) ? ratio : best
  }, '1:1')
}

function ratioValue(ratio: string): number {
  const [w, h] = ratio.split(':').map(Number)
  return w && h ? w / h : 1
}

async function submitTask() {
  if (!canSubmit.value || !activeWorkspace.value) return
  submitting.value = true
  try {
    const form = activeForm.value
    const payload: Record<string, unknown> = {
      ...paramOverrides.value,
      workspace_id: activeWorkspace.value.id,
      workspace_name: activeWorkspace.value.name,
      prompt: form.prompt,
      operation: form.operation,
      size: form.size,
      resolution: form.resolution,
      n: form.n,
      thinking_mode: form.thinking_mode,
      reference_images: form.references.map(item => item.path),
      wait: false,
      download: false,
    }
    if (form.mask_path) payload.mask = form.mask_path
    const task = await createTaskWithCompatibilityFallback(payload)
    await applyIncomingTasks([task])
    watchTaskUntilDone(task)
    fastPollUntil = Date.now() + 120000
    tuneTaskPolling()
    void loadTasks()
    Message.success(task.status === 'queued' ? '已进入队列' : '已提交任务')
  } catch (error: any) {
    Message.error(error?._message || '提交失败')
  } finally {
    submitting.value = false
  }
}

async function createTaskWithCompatibilityFallback(payload: Record<string, unknown>): Promise<DirectTask> {
  try {
    return await hallApi.createDirectCapabilityTask(capabilityId.value, { params: payload }) as DirectTask
  } catch (error: any) {
    if (!isMethodNotAllowed(error)) throw error
    const response: any = await hallApi.runDirectCapability(capabilityId.value, { params: { ...payload, wait: false, download: false } })
    return directRunResponseToTask(response, payload)
  }
}

function isMethodNotAllowed(error: any) {
  return error?.response?.status === 405 || /method not allowed/i.test(String(error?._message || error?.message || ''))
}

function directRunResponseToTask(response: Record<string, any>, payload: Record<string, unknown>): DirectTask {
  const root = taskResultRoot(response)
  const taskId = pickNestedText(response, ['task_id', 'taskId', 'job_id', 'jobId', 'id'])
  return {
    id: `compat-${taskId || Date.now()}`,
    workspace_id: String(payload.workspace_id || ''),
    workspace_name: String(payload.workspace_name || ''),
    upstream_task_id: taskId,
    status: normalizeTaskStatus(String(root.status || response.status || 'in_progress')),
    prompt: String(payload.prompt || ''),
    params: payload,
    result: response,
    image_urls: collectTaskImageUrls(response),
    error: String(root.error || response.error || ''),
    created_at: new Date().toISOString(),
  }
}

function taskResultRoot(response: Record<string, any> | null | undefined) {
  const nested = response?.result
  return nested && typeof nested === 'object' ? nested : (response || {})
}

function normalizeTaskStatus(status: string) {
  const value = status.toLowerCase()
  if (['queued', 'submitting', 'in_progress', 'completed', 'failed', 'rate_limited'].includes(value)) return value
  if (value === 'running' || value === 'processing') return 'in_progress'
  if (value === 'succeeded' || value === 'success') return 'completed'
  return 'in_progress'
}

function taskIdentityValues(task: DirectTask) {
  return [
    task.id,
    task.upstream_task_id,
    task.result?.task_id,
    task.result?.result?.task_id,
    task.result?.id,
    task.result?.result?.id,
  ].map(value => String(value || '').trim()).filter(Boolean)
}

function taskIdentityKey(task: DirectTask) {
  const upstream = String(task.upstream_task_id || '').trim()
  if (upstream) return `upstream:${upstream}`
  const nested = pickNestedText(task.result, ['task_id', 'taskId', 'job_id', 'jobId'])
  if (nested) return `upstream:${nested}`
  return `id:${task.id}`
}

function completedHistoryImageForTask(task: DirectTask) {
  const ids = new Set(taskIdentityValues(task))
  if (!ids.size) return null
  return historyImages.value.find(image => {
    if (image.task_id && ids.has(String(image.task_id))) return true
    const extraTaskId = image.extra?.task_id
    return Boolean(extraTaskId && ids.has(String(extraTaskId)))
  }) || null
}

function historyHasTaskResult(task: DirectTask) {
  return Boolean(completedHistoryImageForTask(task))
}

function taskHasImageResult(task: DirectTask) {
  return Boolean((task.image_urls?.length || collectTaskImageUrls(task.result).length) || historyHasTaskResult(task))
}

function taskStatusRank(status: string) {
  return ({
    completed: 60,
    failed: 50,
    in_progress: 30,
    submitting: 20,
    rate_limited: 15,
    queued: 10,
  } as Record<string, number>)[status] || 0
}

function preferTask(next: DirectTask, current?: DirectTask) {
  if (!current) return next
  if (current.status === 'completed' && next.status !== 'completed') return current
  if (next.status === 'completed' && current.status !== 'completed') return next
  if (taskHasImageResult(current) && !taskHasImageResult(next)) return current
  if (taskHasImageResult(next) && !taskHasImageResult(current)) return next
  const nextRank = taskStatusRank(next.status)
  const currentRank = taskStatusRank(current.status)
  if (nextRank !== currentRank) return nextRank > currentRank ? next : current
  return timeValue(next.updated_at || next.completed_at || next.created_at) >= timeValue(current.updated_at || current.completed_at || current.created_at)
    ? { ...current, ...next, params: next.params || current.params, result: next.result || current.result }
    : current
}

function noteCompletedTasksMissingImages(taskList: DirectTask[], previous: Set<string>) {
  const now = Date.now()
  taskList.forEach(task => {
    if (task.status !== 'completed') return
    if (taskHasImageResult(task)) {
      pendingHistoryTaskDeadlines.delete(task.id)
      return
    }
    if (!previous.has(task.id) || !pendingHistoryTaskDeadlines.has(task.id)) {
      pendingHistoryTaskDeadlines.set(task.id, now + pendingHistoryTaskTimeoutMs)
    }
  })
}

function hasPendingHistoryRefresh(taskList: DirectTask[]) {
  if (!pendingHistoryTaskDeadlines.size) return false
  const now = Date.now()
  const taskMap = new Map(taskList.map(task => [task.id, task]))
  let pending = false
  pendingHistoryTaskDeadlines.forEach((deadline, taskId) => {
    const task = taskMap.get(taskId)
    if (!task || task.status !== 'completed' || taskHasImageResult(task) || deadline <= now) {
      pendingHistoryTaskDeadlines.delete(taskId)
      return
    }
    pending = true
  })
  return pending
}

function normalizeIncomingTask(task: DirectTask): DirectTask {
  const imageUrls = task.image_urls?.length ? task.image_urls : collectTaskImageUrls(task.result)
  const status = normalizeTaskStatus(String(task.status || task.result?.result?.status || task.result?.status || ''))
  const historyImage = completedHistoryImageForTask(task)
  if ((imageUrls.length || historyHasTaskResult(task)) && status !== 'failed') {
    return {
      ...task,
      status: 'completed',
      image_urls: imageUrls.length ? imageUrls : (historyImage ? [historyImage.url] : []),
      error: '',
      completed_at: task.completed_at || historyImage?.created_at,
    }
  }
  return {
    ...task,
    status,
    image_urls: imageUrls,
  }
}

async function applyIncomingTasks(taskList: DirectTask[], options: { refreshHistory?: boolean } = {}) {
  const incoming = taskList.map(normalizeIncomingTask)
  if (!incoming.length) return
  tasks.value = mergeTasks(incoming, tasks.value)
  const previous = completedTaskIds.value
  const next = new Set(previous)
  const completed = incoming.filter(task => task.status === 'completed')
  let hasNewCompleted = false
  completed.forEach(task => {
    next.add(task.id)
    if (!previous.has(task.id)) hasNewCompleted = true
    stopWatchingTask(task.id)
  })
  incoming
    .filter(task => ['failed'].includes(task.status))
    .forEach(task => stopWatchingTask(task.id))
  noteCompletedTasksMissingImages(incoming, previous)
  completedTaskIds.value = next
  if (completed.length) {
    mergeCompletedTaskImages(completed)
  }
  if (options.refreshHistory || hasNewCompleted || hasPendingHistoryRefresh(incoming)) {
    await loadImageHistory({ page: 1, append: false })
    const latestCompleted = tasks.value.filter(task => task.status === 'completed')
    mergeCompletedTaskImages(latestCompleted)
    hasPendingHistoryRefresh(tasks.value)
  }
}

function watchTaskUntilDone(task: DirectTask) {
  if (!task?.id) return
  if (['completed', 'failed'].includes(task.status)) {
    stopWatchingTask(task.id)
    return
  }
  if (task.id.startsWith('compat-')) {
    if (!task.upstream_task_id) return
    watchedCompatTasks.set(task.id, task)
  }
  if (watchedTaskTimers.has(task.id)) return
  watchedTaskAttempts.set(task.id, 0)
  scheduleTaskWatch(task.id, 1000)
}

function scheduleTaskWatch(taskId: string, delay: number) {
  clearWatchedTaskTimer(taskId)
  const timer = window.setTimeout(() => {
    void pollWatchedTask(taskId)
  }, delay)
  watchedTaskTimers.set(taskId, timer)
}

async function pollWatchedTask(taskId: string) {
  const requestCapabilityId = capabilityId.value
  if (!requestCapabilityId) {
    stopWatchingTask(taskId)
    return
  }
  watchedTaskTimers.delete(taskId)
  const attempts = (watchedTaskAttempts.get(taskId) || 0) + 1
  watchedTaskAttempts.set(taskId, attempts)
  try {
    const compatTask = watchedCompatTasks.get(taskId)
    const task = compatTask
      ? await pollCompatTask(requestCapabilityId, compatTask)
      : normalizeIncomingTask(await hallApi.directCapabilityTask(requestCapabilityId, taskId, { advance: true }) as DirectTask)
    await applyIncomingTasks([task], { refreshHistory: task.status === 'completed' })
    if (!['completed', 'failed'].includes(task.status) && attempts < watchedTaskMaxAttempts) {
      if (compatTask) watchedCompatTasks.set(task.id, { ...compatTask, ...task, params: { ...(compatTask.params || {}), ...(task.params || {}) } })
      scheduleTaskWatch(taskId, task.status === 'queued' ? 1500 : 1000)
    }
  } catch {
    if (attempts < watchedTaskMaxAttempts) {
      scheduleTaskWatch(taskId, Math.min(5000, 1000 + attempts * 500))
    } else {
      stopWatchingTask(taskId)
    }
  }
}

async function pollCompatTask(requestCapabilityId: string, task: DirectTask) {
  const payload = {
    ...(task.params || {}),
    task_id: task.upstream_task_id,
    wait: false,
    download: false,
  }
  const response: any = await hallApi.runDirectCapability(requestCapabilityId, { params: payload })
  const incoming = directRunResponseToTask(response, payload)
  return normalizeIncomingTask({
    ...incoming,
    id: task.id,
    created_at: task.created_at || incoming.created_at,
  })
}

async function loadTasks() {
  const requestCapabilityId = capabilityId.value
  if (!requestCapabilityId) return
  if (taskRequestCapabilityId === requestCapabilityId) return
  taskRequestCapabilityId = requestCapabilityId
  tasksLoading.value = true
  try {
    const response: any = await hallApi.directCapabilityTasks(requestCapabilityId, { page: 1, page_size: 50, advance: false })
    if (requestCapabilityId !== capabilityId.value) return
    const incoming = (Array.isArray(response?.items) ? response.items : []).map(normalizeIncomingTask)
    tasks.value = mergeTasks(incoming, tasks.value)
    activeLimit.value = Number(response?.active_limit || 3)
    const previous = completedTaskIds.value
    const next = new Set<string>()
    let hasNewCompleted = false
    tasks.value.forEach((task: DirectTask) => {
      if (task.status === 'completed') {
        next.add(task.id)
        if (!previous.has(task.id)) hasNewCompleted = true
      }
    })
    noteCompletedTasksMissingImages(tasks.value, previous)
    completedTaskIds.value = next
    if (hasNewCompleted || hasPendingHistoryRefresh(tasks.value)) {
      await loadImageHistory({ page: 1, append: false })
      const latestTasks = tasks.value.length ? tasks.value : incoming
      mergeCompletedTaskImages(latestTasks.filter((task: DirectTask) => task.status === 'completed'))
      hasPendingHistoryRefresh(latestTasks)
    }
    tasks.value.forEach(watchTaskUntilDone)
    tuneTaskPolling()
  } catch {
    // polling should stay quiet
  } finally {
    if (taskRequestCapabilityId === requestCapabilityId) {
      taskRequestCapabilityId = ''
      tasksLoading.value = false
    }
  }
}

function taskToImages(task: DirectTask): GalleryImage[] {
  const urls = task.image_urls?.length ? task.image_urls : collectTaskImageUrls(task.result)
  return (urls || []).map((url, idx) => ({
    id: `task-${task.id}-${idx}`,
    url,
    name: filenameFromUrl(url, `生成图 ${idx + 1}`),
    created_at: task.completed_at || task.created_at,
    task_id: task.upstream_task_id || task.id,
    prompt: task.prompt || String(task.params?.prompt || ''),
    extra: {
      workspace_id: task.workspace_id,
      workspace_name: task.workspace_name,
      task_id: task.id,
    },
  }))
}

function mergeCompletedTaskImages(completedTasks: DirectTask[]) {
  const images = completedTasks.flatMap(taskToImages)
  if (!images.length) return
  historyImages.value = mergeImages(images, historyImages.value)
}

function mergeTasks(incoming: DirectTask[], existing: DirectTask[]) {
  const merged: DirectTask[] = []
  ;[...existing, ...incoming].forEach(task => {
    const ids = new Set(taskIdentityValues(task))
    const idx = merged.findIndex(item => {
      if (taskIdentityKey(item) === taskIdentityKey(task)) return true
      return taskIdentityValues(item).some(id => ids.has(id))
    })
    if (idx >= 0) {
      merged[idx] = preferTask(task, merged[idx])
    } else {
      merged.push(task)
    }
  })
  return merged.sort((a, b) => timeValue(b.created_at) - timeValue(a.created_at))
}

async function retryTask(task: DirectTask) {
  try {
    let retry: DirectTask
    if (task.id.startsWith('compat-')) {
      retry = await createTaskWithCompatibilityFallback({ ...(task.params || {}), wait: false, download: false })
    } else {
      retry = await hallApi.retryDirectCapabilityTask(capabilityId.value, task.id) as DirectTask
    }
    await applyIncomingTasks([retry])
    watchTaskUntilDone(retry)
    Message.success('已重试')
  } catch (error: any) {
    if (isMethodNotAllowed(error) && task.params) {
      try {
        const retry = await createTaskWithCompatibilityFallback({ ...(task.params || {}), wait: false, download: false })
        await applyIncomingTasks([retry])
        watchTaskUntilDone(retry)
        Message.success('已用兼容方式重试')
        return
      } catch (fallbackError: any) {
        Message.error(fallbackError?._message || '重试失败')
        return
      }
    }
    Message.error(error?._message || '重试失败')
  }
}

async function queryExistingTask() {
  const id = queryTaskId.value.trim()
  if (!id || !activeWorkspace.value) {
    Message.warning('请输入任务 ID')
    return
  }
  submitting.value = true
  const payload: Record<string, unknown> = {
    ...paramOverrides.value,
    workspace_id: activeWorkspace.value.id,
    workspace_name: activeWorkspace.value.name,
    task_id: id,
    wait: false,
    download: false,
  }
  try {
    const task = await createTaskWithCompatibilityFallback(payload)
    await applyIncomingTasks([task])
    watchTaskUntilDone(task)
    queryTaskVisible.value = false
    queryTaskId.value = ''
    Message.success('已加入查询队列')
  } catch (error: any) {
    Message.error(error?._message || '查询失败')
  } finally {
    submitting.value = false
  }
}

async function customUploadOnly(option: any) {
  const file = option.fileItem?.file as File | undefined
  if (!file || !capabilityId.value) {
    option.onError?.(new Error('文件不存在'))
    return
  }
  try {
    const response: any = await hallApi.uploadDirectCapabilityReference(capabilityId.value, file)
    if (!response?.path) throw new Error('missing path')
    const item: UploadedReference = {
      id: response.id,
      name: response.original_name || file.name,
      path: response.path,
      size: response.size ?? file.size,
    }
    uploadOnlyResults.value = [...uploadOnlyResults.value, item]
    option.onSuccess?.(response)
    Message.success('上传完成')
  } catch (error: any) {
    option.onError?.(error)
    Message.error(uploadErrorMessage(error, file))
  }
}

async function addUploadOnlyToWorkspace(item: UploadedReference) {
  if (!item?.path) return
  const previewUrl = renderableImageUrl(item.path) ? item.path : ''
  const meta = previewUrl ? await readImageMeta(previewUrl) : {}
  activeForm.value.references = [
    ...activeForm.value.references,
    { ...item, preview_url: previewUrl || undefined, width: meta.width, height: meta.height, ratio: meta.ratio },
  ]
  activeForm.value.operation = 'edit'
  if (meta.ratio) {
    activeForm.value.size = meta.ratio
    activeForm.value.resolution = normalizeGptImageResolution(meta.ratio, activeForm.value.resolution)
  }
  Message.success('已加入当前工作区')
}

function renderableImageUrl(value: string) {
  return /^(https?:|blob:|data:|\/api\/)/i.test(value)
}

async function useImageAsReference(url: string, name: string) {
  if (!url) return
  const meta = await readImageMeta(url)
  activeForm.value.references = [
    ...activeForm.value.references,
    { name, path: url, preview_url: url, width: meta.width, height: meta.height, ratio: meta.ratio },
  ]
  activeForm.value.operation = 'edit'
  if (meta.ratio) {
    activeForm.value.size = meta.ratio
    activeForm.value.resolution = normalizeGptImageResolution(meta.ratio, activeForm.value.resolution)
  }
  previewImage.value = null
  Message.success('已加入参考图，可继续修改')
}

function openMaskEditor(item: GalleryImage) {
  maskSource.value = item
  resetMaskRegions()
  maskEraser.value = false
  maskHasPaint.value = false
  maskSizing.value = true
  maskNatural.value = { width: 1, height: 1 }
  maskDisplay.value = fitWithin(1, 1, Math.min(window.innerWidth * 0.84, 1120), Math.min(window.innerHeight * 0.58, 620))
  maskEditorVisible.value = true
  loadMaskSourceSize(item.url)
}

function createMaskRegion(idx: number): MaskRegion {
  return {
    id: `region-${Date.now()}-${idx}-${Math.random().toString(16).slice(2, 8)}`,
    index: idx + 1,
    color: maskRegionColors[idx % maskRegionColors.length],
    prompt: '',
  }
}

function normalizeMaskRegionIndexes() {
  maskRegions.value = maskRegions.value.map((region, idx) => ({
    ...region,
    index: idx + 1,
    color: maskRegionColors[idx % maskRegionColors.length],
  }))
}

function resetMaskRegions() {
  const first = createMaskRegion(0)
  maskRegions.value = [first]
  activeMaskRegionId.value = first.id
}

function addMaskRegion() {
  if (maskRegions.value.length >= maxMaskRegions) return
  const region = createMaskRegion(maskRegions.value.length)
  maskRegions.value = [...maskRegions.value, region]
  activeMaskRegionId.value = region.id
  maskEraser.value = false
}

function removeMaskRegion(id: string) {
  if (maskRegions.value.length <= 1) return
  maskRegions.value = maskRegions.value.filter(region => region.id !== id)
  normalizeMaskRegionIndexes()
  if (activeMaskRegionId.value === id) {
    activeMaskRegionId.value = maskRegions.value[0]?.id || ''
  }
  Message.info('已删除区域；如画布上已有对应颜色，请用橡皮或清空后重画')
}

function loadMaskSourceSize(url: string) {
  const image = new Image()
  image.onload = () => {
    if (maskSource.value?.url !== url) return
    const width = image.naturalWidth || 1024
    const height = image.naturalHeight || 1024
    maskNatural.value = { width, height }
    maskDisplay.value = fitWithin(width, height, Math.min(window.innerWidth * 0.84, 1120), Math.min(window.innerHeight * 0.58, 620))
    maskSizing.value = false
    requestAnimationFrame(clearMaskCanvas)
  }
  image.onerror = () => {
    if (maskSource.value?.url !== url) return
    maskNatural.value = { width: 1024, height: 1024 }
    maskDisplay.value = fitWithin(1024, 1024, Math.min(window.innerWidth * 0.84, 1120), Math.min(window.innerHeight * 0.58, 620))
    maskSizing.value = false
    requestAnimationFrame(clearMaskCanvas)
  }
  image.src = url
}

function fitWithin(width: number, height: number, maxWidth: number, maxHeight: number) {
  const scale = Math.min(maxWidth / width, maxHeight / height, 1)
  return {
    width: Math.max(160, Math.round(width * scale)),
    height: Math.max(160, Math.round(height * scale)),
  }
}

function clearMaskCanvas() {
  const canvas = maskCanvasEl.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.clearRect(0, 0, canvas.width, canvas.height)
  maskHasPaint.value = false
}

function updateMaskHasPaint() {
  const canvas = maskCanvasEl.value
  const ctx = canvas?.getContext('2d')
  if (!canvas || !ctx) {
    maskHasPaint.value = false
    return
  }
  const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data
  let hasPaint = false
  for (let i = 3; i < data.length; i += 4) {
    if (data[i] > 0) {
      hasPaint = true
      break
    }
  }
  maskHasPaint.value = hasPaint
}

function maskPointerPoint(event: PointerEvent) {
  const canvas = maskCanvasEl.value
  if (!canvas) return null
  const rect = canvas.getBoundingClientRect()
  return {
    x: ((event.clientX - rect.left) / rect.width) * canvas.width,
    y: ((event.clientY - rect.top) / rect.height) * canvas.height,
  }
}

function startMaskPaint(event: PointerEvent) {
  const canvas = maskCanvasEl.value
  const point = maskPointerPoint(event)
  if (!canvas || !point) return
  canvas.setPointerCapture?.(event.pointerId)
  maskPainting = true
  maskLastPoint = point
  drawMaskStroke(point, point)
}

function moveMaskPaint(event: PointerEvent) {
  if (!maskPainting) return
  const point = maskPointerPoint(event)
  if (!point || !maskLastPoint) return
  drawMaskStroke(maskLastPoint, point)
  maskLastPoint = point
}

function stopMaskPaint(event: PointerEvent) {
  maskCanvasEl.value?.releasePointerCapture?.(event.pointerId)
  maskPainting = false
  maskLastPoint = null
}

function drawMaskStroke(from: { x: number; y: number }, to: { x: number; y: number }) {
  const canvas = maskCanvasEl.value
  const ctx = canvas?.getContext('2d')
  if (!canvas || !ctx) return
  ctx.save()
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.lineWidth = maskBrushSize.value
  if (maskEraser.value) {
    ctx.globalCompositeOperation = 'destination-out'
    ctx.strokeStyle = 'rgba(0,0,0,1)'
  } else {
    ctx.globalCompositeOperation = 'source-over'
    ctx.strokeStyle = activeMaskRegion.value.color
    maskHasPaint.value = true
  }
  ctx.beginPath()
  ctx.moveTo(from.x, from.y)
  ctx.lineTo(to.x, to.y)
  ctx.stroke()
  ctx.restore()
  if (maskEraser.value) updateMaskHasPaint()
}

function canvasToPngBlob(canvas: HTMLCanvasElement): Promise<Blob | null> {
  return new Promise(resolve => canvas.toBlob(resolve, 'image/png'))
}

async function exportMaskGuideBlob(): Promise<Blob | null> {
  const canvas = maskCanvasEl.value
  if (!canvas) return null
  const output = document.createElement('canvas')
  output.width = canvas.width
  output.height = canvas.height
  const ctx = output.getContext('2d')
  if (!ctx) return null
  const drewSource = await drawMaskSourceToCanvas(ctx, output.width, output.height)
  if (!drewSource) {
    ctx.fillStyle = '#f2f3f5'
    ctx.fillRect(0, 0, output.width, output.height)
  }
  ctx.drawImage(canvas, 0, 0)
  return canvasToPngBlob(output)
}

function maskRegionInstructionLines() {
  const lines = maskRegions.value
    .map(region => ({ ...region, prompt: region.prompt.trim() }))
    .filter(region => region.prompt)
    .map(region => `区域 ${region.index}（${regionColorName(region.index)}）：${region.prompt}`)
  return lines
}

function regionColorName(index: number) {
  return ['红色', '蓝色', '绿色', '橙色', '紫色', '青色'][index - 1] || `颜色${index}`
}

function loadImageElement(url: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => resolve(null)
    image.src = url
  })
}

function isBrowserLocalImageUrl(url: string) {
  return /^(blob:|data:)/i.test(url)
}

function isSameOriginImageUrl(url: string) {
  return url.startsWith('/') || url.startsWith(window.location.origin)
}

async function drawImageUrlToCanvas(ctx: CanvasRenderingContext2D, width: number, height: number, url: string): Promise<boolean> {
  const image = await loadImageElement(url)
  if (!image) return false
  ctx.drawImage(image, 0, 0, width, height)
  return true
}

async function drawFetchedImageToCanvas(ctx: CanvasRenderingContext2D, width: number, height: number, url: string): Promise<boolean> {
  let objectUrl = ''
  try {
    const response = await fetch(url, { credentials: 'include' })
    if (!response.ok) return false
    objectUrl = URL.createObjectURL(await response.blob())
    return await drawImageUrlToCanvas(ctx, width, height, objectUrl)
  } catch {
    return false
  } finally {
    if (objectUrl) URL.revokeObjectURL(objectUrl)
  }
}

async function drawMaskSourceToCanvas(ctx: CanvasRenderingContext2D, width: number, height: number): Promise<boolean> {
  const sourceUrl = maskSource.value?.url || ''
  if (!sourceUrl) return false
  if (isBrowserLocalImageUrl(sourceUrl)) {
    return drawImageUrlToCanvas(ctx, width, height, sourceUrl)
  }
  if (isSameOriginImageUrl(sourceUrl)) {
    const drewSameOrigin = await drawFetchedImageToCanvas(ctx, width, height, sourceUrl)
    if (drewSameOrigin) return true
  }
  if (!capabilityId.value) return false
  let objectUrl = ''
  try {
    const response = await fetch(
      hallApi.directCapabilityImageDownloadUrl(
        capabilityId.value,
        sourceUrl,
        filenameFromUrl(sourceUrl, 'source.png'),
      ),
      { credentials: 'include' },
    )
    if (!response.ok) return false
    const blob = await response.blob()
    objectUrl = URL.createObjectURL(blob)
    const image = await loadImageElement(objectUrl)
    if (!image) return false
    ctx.drawImage(image, 0, 0, width, height)
    return true
  } catch {
    return false
  } finally {
    if (objectUrl) URL.revokeObjectURL(objectUrl)
  }
}

async function exportOpenAiMaskBlob(): Promise<Blob | null> {
  const canvas = maskCanvasEl.value
  const sourceCtx = canvas?.getContext('2d')
  if (!canvas || !sourceCtx) return null
  const source = sourceCtx.getImageData(0, 0, canvas.width, canvas.height)
  const output = document.createElement('canvas')
  output.width = canvas.width
  output.height = canvas.height
  const ctx = output.getContext('2d')
  if (!ctx) return null
  const mask = ctx.createImageData(canvas.width, canvas.height)
  for (let i = 0; i < source.data.length; i += 4) {
    const painted = source.data[i + 3] > 0
    mask.data[i] = 255
    mask.data[i + 1] = 255
    mask.data[i + 2] = 255
    mask.data[i + 3] = painted ? 0 : 255
  }
  ctx.putImageData(mask, 0, 0)
  return canvasToPngBlob(output)
}

async function applyMaskEdit() {
  if (!maskSource.value || !maskCanvasEl.value || !capabilityId.value) return
  if (maskSizing.value) {
    Message.warning('图片尺寸加载中，请稍后再标记')
    return
  }
  if (!maskHasPaint.value) {
    Message.warning('请先在图片上标出要修改的区域')
    return
  }
  const filledRegionPrompts = maskRegionInstructionLines()
  if (maskRegions.value.length > 1 && filledRegionPrompts.length !== maskRegions.value.length) {
    Message.warning('请填写每个区域的修改说明')
    return
  }
  const [maskBlob, guideBlob] = await Promise.all([exportOpenAiMaskBlob(), exportMaskGuideBlob()])
  if (!maskBlob || !guideBlob) {
    Message.error('生成定点区域失败')
    return
  }
  const now = Date.now()
  const maskFile = new File([maskBlob], `mask-${now}.png`, { type: 'image/png' })
  const guideFile = new File([guideBlob], `mask-guide-${now}.png`, { type: 'image/png' })
  try {
    uploadingReferences.value = true
    const [maskResponse, guideResponse]: any[] = await Promise.all([
      hallApi.uploadDirectCapabilityReference(capabilityId.value, maskFile),
      hallApi.uploadDirectCapabilityReference(capabilityId.value, guideFile),
    ])
    if (!maskResponse?.path || !guideResponse?.path) throw new Error('missing mask path')
    const maskPreviewUrl = URL.createObjectURL(guideBlob)
    const sourceMeta = await readImageMeta(maskSource.value.url)
    const sourcePath = String(maskSource.value.extra?.reference_path || maskSource.value.url)
    const sourceRef: UploadedReference = {
      name: maskSource.value.name,
      path: sourcePath,
      preview_url: maskSource.value.url,
      width: sourceMeta.width || maskNatural.value.width,
      height: sourceMeta.height || maskNatural.value.height,
      ratio: sourceMeta.ratio || closestRatio(maskNatural.value.width / maskNatural.value.height),
    }
    const maskRef: UploadedReference = {
      id: guideResponse.id,
      name: '定点区域参考.png',
      path: guideResponse.path,
      preview_url: maskPreviewUrl,
      size: guideResponse.size ?? guideBlob.size,
      width: maskNatural.value.width,
      height: maskNatural.value.height,
      ratio: closestRatio(maskNatural.value.width / maskNatural.value.height),
    }
    const form = activeForm.value
    const keepPreviewUrls = new Set<string>()
    if (sourceRef.preview_url?.startsWith('blob:')) keepPreviewUrls.add(sourceRef.preview_url)
    revokeReferencePreviewUrls(form.references, keepPreviewUrls)
    form.references = [sourceRef, maskRef]
    form.operation = 'edit'
    form.mask_path = maskResponse.path
    form.mask_guide_path = guideResponse.path
    form.mask_source_path = sourceRef.path
    if (sourceRef.ratio) {
      form.size = sourceRef.ratio
      form.resolution = normalizeGptImageResolution(sourceRef.ratio, form.resolution)
    }
    const hasMultipleRegions = maskRegions.value.length > 1
    const regionLines = filledRegionPrompts
    const regionSummary = hasMultipleRegions
      ? '第二张参考图是多区域定点编辑参考图，尺寸与原图一致；不同颜色表示不同修改区域。严格按颜色区域分别修改：'
      : '第二张参考图是定点编辑区域参考图，尺寸与原图一致；红色区域表示要修改的位置。'
    const guard = `${regionSummary}\n${regionLines.join('\n')}\n只修改已标记区域；未标记区域必须保持原图主体、Logo、包装形态和背景细节不变。`
    form.prompt = guard
    maskEditorVisible.value = false
    previewImage.value = null
    previewReference.value = null
    Message.success('已生成定点区域参考，可直接提交编辑任务')
  } catch (error: any) {
    Message.error(error?._message || '定点区域上传失败')
  } finally {
    uploadingReferences.value = false
  }
}

function canRetry(task: DirectTask) {
  return ['failed', 'completed'].includes(task.status)
}

function copyTaskConfig(task: DirectTask) {
  const workspace = workspaces.value.find(item => item.id === task.workspace_id) || activeWorkspace.value
  if (!workspace) return
  const params = task.params || {}
  workspace.form.prompt = String(params.prompt || '')
  workspace.form.operation = params.operation === 'edit' ? 'edit' : 'generate'
  workspace.form.size = String(params.size || '1:1')
  workspace.form.resolution = normalizeGptImageResolution(workspace.form.size, String(params.resolution || '1K'))
  workspace.form.n = Number(params.n || 1)
  workspace.form.thinking_mode = Boolean(params.thinking_mode)
  workspace.form.mask_path = ''
  workspace.form.mask_guide_path = ''
  workspace.form.mask_source_path = ''
  activeWorkspaceId.value = workspace.id
  Message.success('配置已复制到工作区')
}

function selectTaskWorkspace(task: DirectTask) {
  if (task.workspace_id && workspaces.value.some(item => item.id === task.workspace_id)) {
    activeWorkspaceId.value = task.workspace_id
  }
}

function openTaskJson(task: DirectTask) {
  selectedTask.value = task
  jsonVisible.value = true
}

async function loadImageHistory(options: { page: number; append: boolean }) {
  if (!capabilityId.value || historyLoading.value) return
  historyLoading.value = true
  try {
    const response: any = await hallApi.directCapabilityImageHistory(capabilityId.value, {
      page: options.page,
      page_size: historyPageSize,
    })
    const incoming = (Array.isArray(response?.items) ? response.items : []).map(historyItemToImage)
    historyTotal.value = Number(response?.total || incoming.length || 0)
    historyStats.value = normalizeHistoryStats(response?.stats, historyTotal.value)
    historyPage.value = options.page
    historyImages.value = options.append ? mergeImages(historyImages.value, incoming) : incoming
    if (tasks.value.length) {
      tasks.value = tasks.value
        .map(normalizeIncomingTask)
        .sort((a, b) => timeValue(b.created_at) - timeValue(a.created_at))
      completedTaskIds.value = new Set(tasks.value.filter(task => task.status === 'completed').map(task => task.id))
    }
  } catch {
    pageWarning.value = '历史图片暂时加载失败，不影响新任务生成。'
  } finally {
    historyLoading.value = false
  }
}

async function loadMoreHistory() {
  if (!historyHasMore.value) return
  await loadImageHistory({ page: historyPage.value + 1, append: true })
}

function historyItemToImage(item: any): GalleryImage {
  const url = String(item?.url || item?.image_url || '')
  return {
    id: `history-${item?.id || url}`,
    url,
    name: item?.name || filenameFromUrl(url, '生成图'),
    byte_size: Number.isFinite(Number(item?.byte_size)) ? Number(item.byte_size) : null,
    created_at: item?.created_at,
    task_id: item?.task_id,
    prompt: item?.prompt,
    extra: item?.extra || {},
  }
}

function normalizeHistoryStats(value: any, fallbackTotal: number): HistoryStats {
  const total = Number(value?.total_count ?? fallbackTotal ?? 0)
  const known = Number(value?.known_count ?? 0)
  const unknown = Number(value?.unknown_count ?? Math.max(total - known, 0))
  const bytes = Number(value?.known_bytes ?? 0)
  return {
    total_count: Number.isFinite(total) ? Math.max(total, 0) : 0,
    known_count: Number.isFinite(known) ? Math.max(known, 0) : 0,
    unknown_count: Number.isFinite(unknown) ? Math.max(unknown, 0) : 0,
    known_bytes: Number.isFinite(bytes) ? Math.max(bytes, 0) : 0,
  }
}

function mergeImages(existing: GalleryImage[], incoming: GalleryImage[]) {
  const map = new Map<string, GalleryImage>()
  ;[...existing, ...incoming].forEach(image => map.set(image.url, image))
  return Array.from(map.values()).sort((a, b) => timeValue(b.created_at) - timeValue(a.created_at))
}

function statusLabel(status: string) {
  return ({
    queued: '排队',
    submitting: '提交中',
    in_progress: '生成中',
    completed: '完成',
    failed: '失败',
    rate_limited: '排队重试',
  } as Record<string, string>)[status] || status
}

function statusColor(status: string) {
  return ({
    queued: 'gray',
    submitting: 'arcoblue',
    in_progress: 'blue',
    completed: 'green',
    failed: 'red',
    rate_limited: 'orange',
  } as Record<string, string>)[status] || 'gray'
}

function taskProgress(task: DirectTask) {
  const progress = Number(task.result?.result?.progress ?? task.result?.progress ?? 20)
  return Math.max(0.05, Math.min(0.98, progress / 100))
}

function formatHistoryLabel(value?: string) {
  const date = toDate(value)
  return date ? formatTimeOnly(date) : ''
}

function timeValue(value?: string) {
  const date = toDate(value)
  return date ? date.getTime() : 0
}

function filenameFromUrl(url: string, fallback: string) {
  try {
    const name = decodeURIComponent(new URL(url).pathname.split('/').filter(Boolean).pop() || '')
    return name || fallback
  } catch {
    return fallback
  }
}

function pickNestedText(value: unknown, keys: string[]): string {
  if (!value || typeof value !== 'object') return ''
  const record = value as Record<string, any>
  for (const key of keys) {
    const raw = record[key]
    if (raw !== undefined && raw !== null && raw !== '') return String(raw)
  }
  for (const nested of Object.values(record)) {
    const found = pickNestedText(nested, keys)
    if (found) return found
  }
  return ''
}

function collectTaskImageUrls(value: unknown, out: string[] = [], seen: Set<unknown> = new Set(), keyPath = ''): string[] {
  if (out.length >= 24) return out
  if (typeof value === 'string') {
    if (/(image_urls?|files?|file|url|image_url)/i.test(keyPath) && isHttpImageUrl(value) && !out.includes(value)) out.push(value)
    return out
  }
  if (!value || typeof value !== 'object' || seen.has(value)) return out
  seen.add(value)
  if (Array.isArray(value)) {
    value.forEach(item => collectTaskImageUrls(item, out, seen, keyPath))
    return out
  }
  Object.entries(value as Record<string, unknown>).forEach(([key, nested]) => collectTaskImageUrls(nested, out, seen, key))
  return out
}

function isHttpImageUrl(value: string) {
  return /^https?:\/\//i.test(value) && /\.(png|jpe?g|webp|gif)(\?|#|$)/i.test(value)
}

async function copyImageUrls(images: GalleryImage[]) {
  const text = images.map(item => item.url).filter(Boolean).join('\n')
  if (await copyText(text)) Message.success('已复制 URL')
}

async function copyUrl(text: string) {
  if (!text) return
  if (await copyText(text)) Message.success('已复制 URL')
  else Message.warning('复制失败，请手动复制')
}

function downloadImage(url: string, name: string) {
  if (!url || !capabilityId.value) return
  const anchor = document.createElement('a')
  anchor.href = hallApi.directCapabilityImageDownloadUrl(capabilityId.value, url, name)
  anchor.download = filenameFromUrl(url, name || 'image.png')
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}
</script>

<style scoped>
.page-warning {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 0 0 12px;
  padding: 10px 12px;
  border: 1px solid var(--ai-warn);
  border-radius: 8px;
  color: var(--ai-ink-1);
  background: var(--ai-warn-soft);
  font-size: 13px;
}
.page-warning button {
  flex: 0 0 auto;
  border: 0;
  color: var(--ai-accent-ink);
  background: transparent;
  cursor: pointer;
  font-weight: 600;
}
.imagegen-workspace {
  min-height: 100%;
  max-width: 1760px;
}
.imagegen-workspace :deep(.arco-spin),
.imagegen-workspace :deep(.arco-spin-children) {
  width: 100%;
}
.workspace-shell {
  display: flex;
  flex-direction: column;
  gap: 14px;
  width: 100%;
  --ws-color: var(--ai-accent);
  --ws-bg: var(--ai-accent-soft);
  --ws-border: var(--ai-border);
  --ws-ring: var(--ai-accent-soft);
}
.workspace-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  border-radius: 8px;
  box-shadow: var(--ai-shadow-1);
}
.workspace-toolbar-main {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1 1 auto;
  min-width: 0;
}
.toolbar-kicker {
  color: var(--ai-ink-3);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0;
  white-space: nowrap;
}
.workspace-tabs {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1 1 auto;
  min-width: 0;
  overflow-x: auto;
  padding: 2px;
}
.workspace-toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
}
.task-capacity {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  height: 34px;
  padding: 0 10px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  white-space: nowrap;
}
.task-capacity strong {
  color: var(--ai-ink-1);
  font-size: 15px;
}
.workspace-tab,
.workspace-add {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  color: var(--ai-ink-2);
  padding: 0 10px;
  cursor: pointer;
  white-space: nowrap;
}
.workspace-tab {
  min-width: 126px;
  justify-content: space-between;
  border-color: var(--ws-border);
  background: linear-gradient(90deg, var(--ws-bg), var(--ai-surface) 82%);
  box-shadow: inset 3px 0 0 var(--ws-color);
  transition: border-color 0.16s ease, box-shadow 0.16s ease, transform 0.16s ease;
}
.workspace-tab.active {
  border-color: var(--ws-color);
  color: var(--ws-color);
  background: linear-gradient(90deg, var(--ws-bg), var(--ai-surface) 74%);
  box-shadow: inset 4px 0 0 var(--ws-color), 0 0 0 3px var(--ws-ring);
}
.workspace-tab:hover {
  transform: translateY(-1px);
  border-color: var(--ws-color);
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ai-surface-2);
}
.status-dot.running,
.status-dot.submitting {
  background: var(--ai-accent);
}
.status-dot.queued,
.status-dot.rate_limited {
  background: var(--ai-warn);
}
.status-dot.completed {
  background: var(--ai-ok);
}
.status-dot.failed {
  background: var(--ai-bad);
}
.tab-name {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 700;
}
.tab-more {
  border: none;
  background: transparent;
  cursor: pointer;
  color: inherit;
}
.tab-rename-input {
  width: 120px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  padding: 2px 6px;
}
.workspace-rule {
  color: var(--ai-ink-3);
  font-size: 12px;
}
.workspace-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 428px;
  align-items: start;
  gap: 16px;
  min-height: calc(100vh - 180px);
}
.result-column,
.config-column {
  gap: 14px;
  min-width: 0;
}
.result-column {
  display: flex;
  flex-direction: column;
}
.config-column {
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 12px;
}
.result-card,
.history-card,
.config-card,
.queue-card {
  position: relative;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  padding: 14px;
  box-shadow: var(--ai-shadow-1);
}
.result-card,
.config-card {
  border-top-color: var(--ws-color);
  border-top-width: 3px;
}
.result-card::before,
.config-card::before {
  content: "";
  position: absolute;
  left: 14px;
  right: 14px;
  top: -3px;
  height: 3px;
  border-radius: 8px 8px 0 0;
  background: linear-gradient(90deg, var(--ws-color), transparent 72%);
  pointer-events: none;
}
.config-card,
.queue-card {
  padding: 12px;
}
.result-card {
  min-height: 520px;
}
.card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  min-height: 38px;
  margin-bottom: 12px;
}
.card-head.compact {
  margin-bottom: 10px;
}
.card-head h3 {
  margin: 0;
  font-size: 16px;
  color: var(--ai-ink-1);
}
.card-head p {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 18px;
}
.image-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
}
.image-card {
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  overflow: hidden;
  background: var(--ai-surface-2);
  transition: border-color 0.16s ease, box-shadow 0.16s ease;
}
.image-card:hover {
  border-color: var(--ws-border);
  box-shadow: var(--ai-shadow-2);
}
.image-card img {
  width: 100%;
  aspect-ratio: 1 / 1;
  object-fit: contain;
  display: block;
  background: var(--ai-surface-2);
  cursor: zoom-in;
}
.image-tools {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  padding: 8px;
}
.image-tools span {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--ai-ink-2);
}
.image-tool-buttons {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  flex-wrap: wrap;
}
.result-empty,
.history-empty,
.queue-empty {
  min-height: 220px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--ai-ink-3);
  background: var(--ai-surface-2);
  border-radius: 8px;
}
.result-empty {
  min-height: 360px;
}
.history-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
  gap: 8px;
  margin-bottom: 10px;
}
.history-thumb {
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  padding: 4px;
  cursor: pointer;
}
.history-thumb img {
  width: 100%;
  aspect-ratio: 1 / 1;
  object-fit: cover;
  border-radius: 6px;
  display: block;
}
.history-thumb small {
  display: block;
  margin-top: 4px;
  color: var(--ai-ink-3);
  font-size: 11px;
}
.hidden-input {
  display: none;
}
.field-label {
  display: block;
  margin: 10px 0 6px;
  font-size: 12px;
  font-weight: 700;
  color: var(--ai-ink-2);
}
.reference-box {
  min-height: 112px;
  border: 1px dashed var(--ai-border-2);
  border-radius: 8px;
  background: var(--ai-surface-2);
  padding: 10px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  color: var(--ai-ink-3);
  cursor: pointer;
  transition: border-color 0.16s ease, background 0.16s ease;
}
.reference-box:hover {
  border-color: var(--ws-color);
  background: var(--ws-bg);
}
.reference-box.dragging {
  border-color: var(--ws-color);
  background: var(--ws-bg);
  box-shadow: inset 0 0 0 2px var(--ws-ring);
}
.ref-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 210px;
  min-height: 44px;
  padding: 4px 6px 4px 4px;
  border-radius: 8px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
}
.ref-preview {
  width: 34px;
  height: 34px;
  flex: 0 0 auto;
  border: none;
  border-radius: 6px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  padding: 0;
  overflow: hidden;
  cursor: zoom-in;
}
.ref-preview img {
  width: 34px;
  height: 34px;
  object-fit: cover;
  display: block;
}
.ref-preview svg {
  width: 18px;
  height: 18px;
  margin: 8px;
}
.ref-chip span {
  min-width: 0;
  max-width: 128px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}
.ref-remove {
  width: 22px;
  height: 22px;
  flex: 0 0 auto;
  border: none;
  border-radius: 50%;
  background: transparent;
  cursor: pointer;
  color: var(--ai-ink-3);
  line-height: 20px;
}
.ref-remove:hover {
  background: var(--ai-surface-2);
  color: var(--ai-bad);
}
.preset-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 10px 0;
}
.preset-row button {
  border: 1px solid var(--ws-border);
  background: var(--ai-surface);
  border-radius: 8px;
  padding: 5px 11px;
  cursor: pointer;
  color: var(--ai-ink-2);
  font-weight: 700;
}
.preset-row button:hover {
  color: var(--ws-color);
  background: var(--ws-bg);
}
.setting-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin: 10px 0;
}
.setting-field {
  display: grid;
  gap: 6px;
  min-width: 0;
  font-size: 12px;
  font-weight: 700;
  color: var(--ai-ink-2);
}
.setting-field span {
  line-height: 18px;
}
.setting-field :deep(.arco-select-view),
.setting-field :deep(.arco-input-wrapper),
.setting-field :deep(.arco-input-number),
.setting-field :deep(.arco-input-number .arco-input-wrapper) {
  width: 100%;
  height: 34px;
  box-sizing: border-box;
}
.thinking-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 0;
  color: var(--ai-ink-2);
  font-weight: 700;
}
.task-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 390px;
  overflow-y: auto;
}
.task-row {
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr);
  gap: 10px;
  border: 1px solid var(--ai-border);
  border-left: 3px solid var(--ws-color);
  border-radius: 8px;
  padding: 8px;
  background: var(--ai-surface);
  cursor: pointer;
  transition: border-color 0.16s ease, box-shadow 0.16s ease;
}
.task-row:hover {
  border-color: var(--ws-border);
  border-left-color: var(--ws-color);
  box-shadow: var(--ai-shadow-1);
}
.task-row.completed {
  border-color: var(--ai-ok);
  border-left-color: var(--ws-color);
}
.task-row.failed {
  border-color: var(--ai-bad);
  border-left-color: var(--ws-color);
}
.task-thumb {
  width: 54px;
  height: 54px;
  border-radius: 8px;
  background: var(--ai-surface-2);
  display: grid;
  place-items: center;
  overflow: hidden;
  color: var(--ai-ink-3);
  font-weight: 800;
}
.task-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.task-main {
  min-width: 0;
}
.task-title,
.task-meta,
.task-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.task-title {
  justify-content: space-between;
}
.task-title strong,
.task-prompt {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-prompt {
  margin: 4px 0;
  color: var(--ai-ink-2);
  font-size: 12px;
}
.task-meta {
  color: var(--ai-ink-3);
  font-size: 11px;
}
.task-actions {
  grid-column: 2;
  margin-top: 6px;
}
.task-error {
  margin-top: 5px;
  color: var(--ai-bad);
  font-size: 12px;
}
.modal-hint {
  margin: 0 0 12px;
  color: var(--ai-ink-3);
  font-size: 13px;
}
.upload-only-results {
  display: grid;
  gap: 8px;
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
}
.upload-only-results li {
  display: grid;
  grid-template-columns: minmax(90px, 0.8fr) minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 8px;
  padding: 8px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
}
.upload-only-results strong,
.upload-only-results code {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.upload-only-results code {
  color: var(--ai-ink-3);
  font-size: 12px;
}
.mask-editor {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.mask-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.mask-source {
  flex: 1 1 180px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ai-ink-2);
  font-size: 12px;
}
.mask-region-indicator {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 26px;
  padding: 0 10px;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--region-color) 70%, var(--ai-surface));
  background: color-mix(in srgb, var(--region-color) 14%, var(--ai-surface));
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}
.mask-region-indicator::before {
  content: "";
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--region-color);
}
.mask-toolbar label,
.mask-size {
  color: var(--ai-ink-3);
  font-size: 12px;
}
.mask-canvas-stage {
  height: min(62dvh, 660px);
  display: grid;
  place-items: center;
  overflow: auto;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
}
.mask-canvas-wrap {
  position: relative;
  flex: 0 0 auto;
  background: var(--ai-surface);
  box-shadow: var(--ai-shadow-2);
}
.mask-canvas-wrap img,
.mask-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.mask-canvas-wrap img {
  object-fit: contain;
  user-select: none;
  pointer-events: none;
}
.mask-canvas {
  touch-action: none;
  cursor: crosshair;
}
.mask-region-panel {
  display: grid;
  grid-template-columns: minmax(150px, 220px) minmax(0, 1fr);
  gap: 12px;
}
.mask-region-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.mask-region-tab {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  color: var(--ai-ink-2);
  padding: 0 10px;
  cursor: pointer;
  text-align: left;
}
.mask-region-tab.active {
  border-color: color-mix(in srgb, var(--region-color) 76%, var(--ai-surface));
  background: color-mix(in srgb, var(--region-color) 12%, var(--ai-surface));
  color: var(--ai-ink-1);
  box-shadow: inset 3px 0 0 var(--region-color);
}
.region-swatch {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--region-color);
  flex: 0 0 auto;
}
.mask-region-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.mask-footer,
.preview-action-buttons {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}
.json-block {
  margin: 0;
  max-height: 620px;
  overflow: auto;
  background: var(--ai-ink-1);
  color: var(--ai-accent-soft);
  border-radius: 8px;
  padding: 12px;
  font-size: 12px;
}
.preview-box {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.preview-box img {
  max-width: 100%;
  max-height: 70vh;
  object-fit: contain;
  background: var(--ai-surface-2);
  border-radius: 8px;
}
.reference-preview-empty {
  min-height: 260px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--ai-ink-3);
  background: var(--ai-surface-2);
  border-radius: 8px;
  word-break: break-all;
  padding: 18px;
}
.preview-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
@media (max-width: 1080px) {
  .workspace-toolbar {
    align-items: stretch;
    flex-direction: column;
  }
  .workspace-toolbar-main,
  .workspace-toolbar-actions {
    width: 100%;
  }
  .workspace-tabs {
    flex: 1;
  }
  .workspace-grid {
    grid-template-columns: 1fr;
  }
  .upload-only-results li {
    grid-template-columns: 1fr;
  }
  .config-column {
    position: static;
  }
}
@media (min-width: 1081px) and (max-width: 1320px) {
  .workspace-grid {
    grid-template-columns: minmax(0, 1fr) 400px;
  }
}
</style>
