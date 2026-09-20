<template>
  <div class="page-container imagegen-chat">
    <HallDetailHeader
      :crumbs="[
        { label: '能力大厅', to: '/hall' },
        { label: capability?.display_name || 'GPT ImageGenDialogue' },
      ]"
      :title="capability?.display_name || 'GPT ImageGenDialogue'"
      hide-title
      hide-crumbs
      back-title="返回能力大厅"
    >
      <template #actions>
        <a-button
          type="text"
          size="small"
          :disabled="!conversation.length || running"
          class="head-action"
          @click="resetConversation"
        >
          <template #icon><icon-refresh /></template>
          新对话
        </a-button>
        <a-dropdown trigger="click">
          <a-button type="text" size="small" class="more-btn">
            <template #icon><icon-more /></template>
          </a-button>
          <template #content>
            <a-doption @click="$emit('switch-new')">工作区页面</a-doption>
            <a-doption disabled>对话页面</a-doption>
            <a-doption @click="$emit('switch-legacy')">高级表单</a-doption>
            <a-doption @click="paramsVisible = true">高级参数</a-doption>
            <a-doption @click="uploadOnlyVisible = true">仅上传取 URL</a-doption>
            <a-doption :disabled="!canExecute" @click="queryTaskVisible = true">查询已有任务</a-doption>
            <a-doption :disabled="!latestResult" @click="jsonVisible = true">查看最近结果 JSON</a-doption>
          </template>
        </a-dropdown>
      </template>
    </HallDetailHeader>

    <a-spin :loading="loading" tip="加载能力配置...">
      <div v-if="capability" class="chat-shell">
        <aside v-if="displayImages.length" class="history-rail" aria-label="历史图片">
          <div
            v-for="group in historyDateGroups"
            :key="group.key"
            class="history-date-group"
            :class="{ active: activeDateKey === group.key }"
          >
            <button
              type="button"
              class="history-date-marker"
              :class="{ active: activeDateKey === group.key }"
              :title="group.fullLabel"
              @click="selectDateGroup(group)"
            >
              <span>{{ group.label }}</span>
              <small>{{ group.images.length }}</small>
            </button>
            <div class="history-date-images">
              <button
                v-for="img in group.images"
                :key="img.id"
                type="button"
                class="history-thumb"
                :class="{ active: activeImage?.id === img.id }"
                :title="`${group.fullLabel} · ${img.name}`"
                @click="selectHistoryImage(img)"
              >
                <img :src="img.url" :alt="img.name" />
              </button>
            </div>
          </div>
          <a-button
            v-if="historyHasMore"
            class="history-more"
            size="mini"
            type="text"
            :loading="historyLoading"
            @click="loadMoreHistory"
          >
            更多
          </a-button>
        </aside>

        <main class="dialogue-workspace">
          <section ref="chatStreamEl" class="chat-stream">
            <article v-if="activeImage && !conversation.length" class="msg-wrap assistant history-message">
              <div class="avatar assistant">AI</div>
              <div class="bubble">
                <span class="ref-tag">历史图片</span>
                <div class="img-grid">
                  <article class="gen-image">
                    <img
                      class="clickable"
                      :src="activeImage.url"
                      :alt="activeImage.name"
                      @click="openImagePreview(activeImage)"
                    />
                    <div class="img-tools">
                      <a-button type="primary" size="small" @click="useImageAsReference(activeImage.url, activeImage.name)">
                        <template #icon><icon-loop /></template>
                        基于这张继续改
                      </a-button>
                      <a-button size="small" @click="openMaskEditor(activeImage)">
                        定点编辑
                      </a-button>
                      <a-button size="small" @click="copyUrl(activeImage.url)">
                        <template #icon><icon-copy /></template>
                        复制 URL
                      </a-button>
                      <a-button size="small" @click="downloadImage(activeImage.url, activeImage.name)">
                        <template #icon><icon-download /></template>
                        下载
                      </a-button>
                      <a-button size="small" @click="openUrl(activeImage.url)">
                        <template #icon><icon-launch /></template>
                        打开原图
                      </a-button>
                    </div>
                  </article>
                </div>
              </div>
            </article>

            <div v-if="!conversation.length && !activeImage" class="chat-empty">
              <div class="empty-hero">
                <div class="empty-icon"><icon-image /></div>
                <h2>发一句话生成图片</h2>
                <p>{{ capabilityDescription }}</p>
              </div>
              <div class="example-grid">
                <button
                  v-for="item in PROMPT_EXAMPLES"
                  :key="item.title"
                  type="button"
                  class="example-card"
                  @click="usePromptExample(item.prompt)"
                >
                  <span class="ex-tag">{{ item.tag }}</span>
                  <strong>{{ item.title }}</strong>
                  <span class="ex-sub">{{ item.desc }}</span>
                </button>
              </div>
            </div>

            <article
              v-for="message in conversation"
              :key="message.id"
              class="msg-wrap"
              :class="message.role"
            >
              <div class="avatar" :class="message.role">
                {{ message.role === 'user' ? '你' : 'AI' }}
              </div>
              <div class="bubble" :class="message.role">
                <span v-if="message.refTag" class="ref-tag">{{ message.refTag }}</span>
                <div v-if="message.text" class="msg-text">{{ message.text }}</div>
                <div v-if="message.images?.length" class="user-image-strip">
                  <img
                    v-for="url in message.images"
                    :key="url"
                    :src="url"
                    alt="参考图"
                  />
                </div>
                <div v-if="message.loading" class="msg-loading">
                  <a-spin size="small" />
                  <span>正在生成图片...</span>
                </div>
                <div v-if="message.error" class="msg-error">
                  <icon-exclamation-circle />
                  <span>{{ message.error }}</span>
                </div>
                <div v-if="message.imageGroup?.length" class="img-grid">
                  <article
                    v-for="image in message.imageGroup"
                    :key="image.id"
                    class="gen-image"
                  >
                    <img
                      class="clickable"
                      :src="image.url"
                      :alt="image.name"
                      @click="openImagePreview(image)"
                    />
                    <div class="img-tools">
                      <a-button type="primary" size="small" @click="useImageAsReference(image.url, image.name)">
                        <template #icon><icon-loop /></template>
                        基于这张继续改
                      </a-button>
                      <a-button size="small" @click="openMaskEditor(image)">
                        定点编辑
                      </a-button>
                      <a-button size="small" @click="copyUrl(image.url)">
                        <template #icon><icon-copy /></template>
                        复制 URL
                      </a-button>
                      <a-button size="small" @click="downloadImage(image.url, image.name)">
                        <template #icon><icon-download /></template>
                        下载
                      </a-button>
                      <a-button size="small" @click="openUrl(image.url)">
                        <template #icon><icon-launch /></template>
                        打开原图
                      </a-button>
                    </div>
                  </article>
                </div>
              </div>
            </article>
          </section>

          <footer class="composer">
          <input
            ref="fileInputEl"
            class="hidden-input"
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            multiple
            @change="onPickFiles"
          />

          <div
            class="composer-inner"
            :class="{ dragover: isDragOver }"
            @dragenter.prevent="isDragOver = true"
            @dragover.prevent="isDragOver = true"
            @dragleave.prevent="isDragOver = false"
            @drop.prevent="onDropFiles"
          >
            <div v-if="pendingReferences.length" class="reference-strip">
              <article
                v-for="item in pendingReferences"
                :key="item.path"
                class="ref-chip"
              >
                <img v-if="item.preview_url" :src="item.preview_url" :alt="item.name" />
                <icon-image v-else />
                <span :title="item.name">{{ item.name }}</span>
                <small v-if="item.width && item.height">{{ item.width }}×{{ item.height }}</small>
                <button type="button" class="x" :aria-label="`移除 ${item.name}`" @click="removeReference(item.path)">×</button>
              </article>
              <a-spin v-if="uploadingReferences" size="small" />
            </div>
            <div v-if="referenceSummary" class="reference-summary">
              已按参考图自适应比例：{{ composer.size }} · {{ composer.resolution }}（{{ referenceSummary }}）
            </div>
            <div v-else-if="implicitReferenceSummary" class="reference-summary selected-reference">
              {{ implicitReferenceSummary }}
            </div>
            <a-textarea
              v-model="composer.text"
              :auto-size="{ minRows: 2, maxRows: 6 }"
              placeholder="描述要生成或修改的图片，例：白底商品图，柔光，高级商业摄影"
              class="composer-text"
              @keydown="onComposerKeydown"
            />
            <div class="composer-row">
              <a-tooltip content="添加图片">
                <a-button class="icon-btn" :loading="uploadingReferences" @click="openFilePicker">
                  <template #icon><icon-attachment /></template>
                </a-button>
              </a-tooltip>

              <a-dropdown trigger="click">
                <button type="button" class="size-pill">
                  <icon-original-size />
                  {{ actionLabel }} · {{ composer.size }} · {{ composer.resolution }} · {{ composer.n }}张
                  <icon-down />
                </button>
                <template #content>
                  <div class="size-menu">
                    <div class="size-menu-section">
                      <span class="size-menu-label">动作</span>
                      <div class="size-menu-grid">
                        <button
                          v-for="item in ACTION_OPTIONS"
                          :key="item.value"
                          type="button"
                          class="size-menu-item"
                          :class="{ active: composer.action === item.value }"
                          @click="composer.action = item.value"
                        >{{ item.label }}</button>
                      </div>
                    </div>
                    <div class="size-menu-section">
                      <span class="size-menu-label">比例</span>
                      <div class="size-menu-grid">
                        <button
                          v-for="s in ALL_RATIOS"
                          :key="s"
                          type="button"
                          class="size-menu-item"
                          :class="{ active: composer.size === s }"
                          @click="composer.size = s"
                        >{{ s }}</button>
                      </div>
                    </div>
                    <div class="size-menu-section">
                      <span class="size-menu-label">分辨率</span>
                      <div class="size-menu-grid">
                        <button
                          v-for="r in ['1K', '2K', '4K']"
                          :key="r"
                          type="button"
                          class="size-menu-item"
                          :class="{ active: composer.resolution === r }"
                          @click="composer.resolution = r"
                        >{{ r }}</button>
                      </div>
                    </div>
                    <div class="size-menu-section">
                      <span class="size-menu-label">张数</span>
                      <a-input-number v-model="composer.n" :min="1" :max="1" :precision="0" size="small" disabled />
                    </div>
                    <div class="size-menu-hint">{{ resolutionHint }}</div>
                  </div>
                </template>
              </a-dropdown>

              <button
                type="button"
                class="ai-pill"
                :class="{ active: composer.thinking }"
                @click="composer.thinking = !composer.thinking"
              >
                <icon-thunderbolt />
                <span>Thinking</span>
              </button>

              <a-button
                type="primary"
                size="small"
                class="send-btn"
                :loading="running"
                :disabled="!canSend"
                @click="sendMessage"
              >
                <template #icon><icon-send /></template>
                发送
              </a-button>
            </div>
          </div>
          <div class="composer-help">
            Enter 发送 · Shift+Enter 换行 · 上一次的图自动作为下一次参考
          </div>
          </footer>
        </main>
      </div>
      <SfEmptyState v-else-if="!loading" title="能力不可用" description="未找到该能力" hint="请返回能力大厅确认发布状态和权限。" icon="image" />
    </a-spin>

    <a-modal v-model:visible="uploadOnlyVisible" title="仅上传取 URL" :footer="false" :width="520">
      <p class="modal-hint">选择本地图片，平台会上传到上游图床并返回公开 URL，不创建生成任务。</p>
      <a-upload :custom-request="customUploadOnly" multiple :show-file-list="false" accept="image/*">
        <template #upload-button>
          <a-button type="primary">
            <template #icon><icon-upload /></template>
            选择图片
          </a-button>
        </template>
      </a-upload>
      <ul v-if="uploadOnlyResults.length" class="upload-only-results">
        <li v-for="r in uploadOnlyResults" :key="r.path">
          <strong :title="r.name">{{ r.name }}</strong>
          <code :title="r.path">{{ r.path }}</code>
          <a-button size="mini" @click="copyUrl(r.path)">复制</a-button>
        </li>
      </ul>
    </a-modal>

    <a-modal
      v-model:visible="queryTaskVisible"
      title="查询已有任务"
      ok-text="查询"
      :ok-loading="running"
      @ok="runQueryTask"
      @cancel="queryTaskId = ''"
    >
      <p class="modal-hint">输入已有的图片任务 ID，平台会拉取该任务的当前状态和结果。</p>
      <a-input v-model="queryTaskId" placeholder="task_img_..." allow-clear />
    </a-modal>

    <a-drawer v-model:visible="jsonVisible" title="结果 JSON" placement="right" :width="640" :footer="false">
      <pre class="json-panel">{{ jsonText }}</pre>
    </a-drawer>

    <a-drawer v-model:visible="paramsVisible" title="高级参数" placement="right" :width="520" :footer="false">
      <GptImageParamInspector
        v-model="paramOverrides"
        :title="capability?.display_name || 'GPT ImageGenDialogue'"
        :schema="capability?.input_schema"
        :required-fields="capability?.input_schema?.required || []"
      />
    </a-drawer>

    <a-modal
      v-model:visible="previewVisible"
      :footer="false"
      :title="previewImage?.name || '图片预览'"
      :width="'min(96vw, 1440px)'"
      :body-style="{ padding: '0' }"
      class="image-preview-modal"
    >
      <div class="image-preview-shell">
        <div class="preview-controls">
          <span class="preview-name">{{ previewImage?.name || '图片预览' }}</span>
          <a-button size="mini" :type="previewFit ? 'primary' : 'secondary'" @click="setPreviewFit">适合屏幕</a-button>
          <a-button size="mini" :type="!previewFit && previewScale === 1 ? 'primary' : 'secondary'" @click="setPreviewActual">原始大小</a-button>
          <a-button size="mini" @click="zoomPreview(-0.25)">-</a-button>
          <span class="scale-readout">{{ previewScaleLabel }}</span>
          <a-button size="mini" @click="zoomPreview(0.25)">+</a-button>
        </div>
        <div class="image-preview-body" :class="{ fit: previewFit }">
          <img
            v-if="previewImage"
            :src="previewImage.url"
            :alt="previewImage.name"
            :style="previewImageStyle"
          />
        </div>
      </div>
    </a-modal>

    <a-modal
      v-model:visible="maskEditorVisible"
      title="定点编辑"
      :width="'92vw'"
      :footer="false"
    >
      <div class="mask-editor">
        <div class="mask-toolbar">
          <span class="mask-source" :title="maskSource?.name">{{ maskSource?.name || '当前图片' }}</span>
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
        <a-textarea
          v-model="maskPrompt"
          :auto-size="{ minRows: 2, maxRows: 4 }"
          placeholder="描述定点区域要怎么改，例如：把这块背景换成淡黄色，加 24h 极速回弹"
        />
        <div class="mask-footer">
          <a-button @click="maskEditorVisible = false">取消</a-button>
          <a-button type="primary" :loading="uploadingReferences" :disabled="maskSizing" @click="applyMaskEdit">使用定点区域</a-button>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import {
  IconAttachment,
  IconCopy,
  IconDown,
  IconDownload,
  IconExclamationCircle,
  IconImage,
  IconOriginalSize,
  IconLaunch,
  IconLoop,
  IconMore,
  IconRefresh,
  IconSend,
  IconThunderbolt,
  IconUpload,
} from '@arco-design/web-vue/es/icon'
import { hallApi } from '@/api'
import SfEmptyState from '@/components/common/SfEmptyState.vue'
import GptImageParamInspector from '@/components/hall/GptImageParamInspector.vue'
import HallDetailHeader from '@/components/hall/HallDetailHeader.vue'
import { normalizeGptImageResolution } from '@/pages/hall/gptImageParams'
import { copyText } from '@/utils/clipboard'
import { bjtDateString, bjtParts, toDate } from '@/utils/format'

defineProps<{
  runUi?: Record<string, any> | null
}>()

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
type GenImage = {
  id: string
  url: string
  name: string
  created_at?: string
  task_id?: string
  prompt?: string
  source?: string
}
type ImageDateGroup = {
  key: string
  label: string
  fullLabel: string
  images: GenImage[]
}
type ChatMessage = {
  id: number
  role: 'user' | 'assistant'
  text?: string
  images?: string[]
  imageGroup?: GenImage[]
  refTag?: string
  loading?: boolean
  error?: string
  result?: unknown
}

defineEmits(['switch-new', 'switch-legacy'])

const ALL_RATIOS = ['1:1', '4:5', '3:4', '5:4', '3:2', '2:3', '4:3', '16:9', '9:16', '2:1', '1:2', '21:9', '9:21']
const ACTION_OPTIONS = [
  { value: 'auto', label: '自动' },
  { value: 'generate', label: '新图' },
  { value: 'edit', label: '编辑' },
] as const
type DialogueAction = typeof ACTION_OPTIONS[number]['value']
const PROMPT_EXAMPLES = [
  {
    tag: '商品图',
    title: '白底商品主图',
    desc: '产品居中，柔光，高级商业摄影感',
    prompt: '白底商品主图：产品居中，柔光，高级商业摄影感。',
  },
  {
    tag: '营销',
    title: '活动 Banner',
    desc: '左产品右标题，红色活动氛围',
    prompt: '活动 Banner：左产品右标题，红色活动氛围，高级商业视觉。',
  },
  {
    tag: '编辑',
    title: '改背景 + 加卖点',
    desc: '把背景改成淡黄色，加 24h 极速回弹',
    prompt: '把背景改成淡黄色，加上卖点文字：24h 极速回弹；保持产品主体和 Logo 不变。',
  },
  {
    tag: '保留主体',
    title: '换风格不换主体',
    desc: '保留产品 + Logo，换背景换风格',
    prompt: '保留产品主体和 Logo，不改变包装形态，只更换背景和整体视觉风格。',
  },
] as const

const route = useRoute()
const capabilityId = computed(() => String(route.params.id || ''))

const loading = ref(false)
const running = ref(false)
const capability = ref<any>(null)

const fileInputEl = ref<HTMLInputElement | null>(null)
const chatStreamEl = ref<HTMLElement | null>(null)
const isDragOver = ref(false)
const uploadingReferences = ref(false)
const pendingReferences = ref<UploadedReference[]>([])

const conversation = ref<ChatMessage[]>([])
const sessionImages = ref<GenImage[]>([])
const historyImages = ref<GenImage[]>([])
const historyTotal = ref(0)
const historyPage = ref(1)
const historyLoading = ref(false)
const selectedImageId = ref('')
const latestResult = ref<unknown>(null)

const uploadOnlyVisible = ref(false)
const uploadOnlyResults = ref<UploadedReference[]>([])
const queryTaskVisible = ref(false)
const queryTaskId = ref('')
const jsonVisible = ref(false)
const paramsVisible = ref(false)
const previewVisible = ref(false)
const previewImage = ref<GenImage | null>(null)
const previewFit = ref(true)
const previewScale = ref(1)
const previewNatural = ref({ width: 0, height: 0 })
const maskEditorVisible = ref(false)
const maskSource = ref<GenImage | null>(null)
const maskCanvasEl = ref<HTMLCanvasElement | null>(null)
const maskNatural = ref({ width: 1, height: 1 })
const maskDisplay = ref({ width: 640, height: 640 })
const maskSizing = ref(false)
const maskBrushSize = ref(36)
const maskEraser = ref(false)
const maskHasPaint = ref(false)
const maskPrompt = ref('')
const maskReferencePath = ref('')
const maskGuideReferencePath = ref('')
const maskSourceReferencePath = ref('')
const defaultParamOverrides = () => ({ wait: true, download: false })
const paramOverrides = ref<Record<string, unknown>>(defaultParamOverrides())

const composer = reactive({
  text: '',
  action: 'auto' as DialogueAction,
  size: '1:1',
  resolution: '1K',
  n: 1,
  thinking: false,
})

let nextMessageId = 1
const lastAssistantImageUrl = ref('')
let maskPainting = false
let maskLastPoint: { x: number; y: number } | null = null
const historyPageSize = 20

const canExecute = computed(() => capability.value?.permissions?.execute !== false)

const canSend = computed(() => {
  if (!canExecute.value) return false
  if (running.value || uploadingReferences.value) return false
  if (!composer.text.trim()) return false
  return true
})

const capabilityDescription = computed(() => {
  return String(
    capability.value?.description
    || '基于 ToAPIs GPT-Image-2 的对话式图片生成能力，按 OpenAI Responses API 的图片对话交互组织输入，支持多轮提示、图片输入、描述编辑、上传、轮询和下载。',
  )
})

const actionLabel = computed(() => ACTION_OPTIONS.find(item => item.value === composer.action)?.label || '自动')

const resolutionHint = computed(() => {
  if (composer.resolution === '4K') return '4K 仅支持 16:9/9:16/2:1/1:2/21:9/9:21'
  if (composer.resolution === '1K') return '1K 仅支持 1:1/3:2/2:3'
  return '2K 支持全部比例'
})

const referenceSummary = computed(() => {
  const first = pendingReferences.value[0]
  if (!first) return ''
  const count = pendingReferences.value.length > 1 ? `等 ${pendingReferences.value.length} 张` : '1 张'
  const size = first.width && first.height ? `${first.width}x${first.height}` : ''
  return [count, size, first.ratio].filter(Boolean).join(' · ')
})
const implicitReferenceSummary = computed(() => {
  if (pendingReferences.value.length || !lastAssistantImageUrl.value) return ''
  const item = displayImages.value.find(image => image.url === lastAssistantImageUrl.value) || activeImage.value
  if (!item || item.url !== lastAssistantImageUrl.value) return ''
  return `当前选中图片将作为下一轮参考：${item.name}`
})

const displayImages = computed(() => mergeImages(sessionImages.value, historyImages.value))
const activeImage = computed<GenImage | null>(() => {
  if (!displayImages.value.length) return null
  return displayImages.value.find(item => item.id === selectedImageId.value) || displayImages.value[0] || null
})
const historyDateGroups = computed<ImageDateGroup[]>(() => groupImagesByDate(displayImages.value))
const activeDateKey = computed(() => activeImage.value ? imageDateKey(activeImage.value) : '')
const historyHasMore = computed(() => historyImages.value.length < historyTotal.value)
const jsonText = computed(() => latestResult.value ? JSON.stringify(latestResult.value, null, 2) : '{}')
const previewScaleLabel = computed(() => previewFit.value ? '适合屏幕' : `${Math.round(previewScale.value * 100)}%`)
const previewImageStyle = computed(() => {
  if (previewFit.value) {
    return {
      width: 'auto',
      height: 'auto',
      maxWidth: '100%',
      maxHeight: '100%',
    }
  }
  const width = previewNatural.value.width
    ? `${Math.round(previewNatural.value.width * previewScale.value)}px`
    : `${Math.round(100 * previewScale.value)}%`
  return {
    width,
    height: 'auto',
    maxWidth: 'none',
    maxHeight: 'none',
  }
})

watch(
  () => composer.size,
  (newSize) => {
    composer.resolution = normalizeGptImageResolution(newSize, composer.resolution)
  },
)
watch(
  () => composer.resolution,
  (resolution) => {
    composer.resolution = normalizeGptImageResolution(composer.size, resolution)
  },
)
watch(conversation, () => nextTick(scrollChatToBottom), { deep: true })

onMounted(load)
onBeforeUnmount(revokeAllPreviewUrls)
watch(() => route.params.id, load)

async function load() {
  if (!capabilityId.value) return
  loading.value = true
  historyImages.value = []
  historyTotal.value = 0
  historyPage.value = 1
  selectedImageId.value = ''
  lastAssistantImageUrl.value = ''
  try {
    capability.value = await hallApi.directCapability(capabilityId.value)
    await loadImageHistory({ page: 1, append: false })
  } catch {
    capability.value = null
  } finally {
    loading.value = false
  }
}

async function loadImageHistory(options: { page?: number; append?: boolean; preferUrl?: string } = {}) {
  if (!capabilityId.value || historyLoading.value) return
  const page = options.page || 1
  historyLoading.value = true
  try {
    const response: any = await hallApi.directCapabilityImageHistory(capabilityId.value, {
      page,
      page_size: historyPageSize,
    })
    const incoming = (Array.isArray(response?.items) ? response.items : [])
      .map(normalizeHistoryImage)
      .filter((item: GenImage) => item.url && isGeneratedImageUrl(item.url))
    historyTotal.value = Number(response?.total || incoming.length || 0)
    historyPage.value = page
    historyImages.value = options.append ? mergeImages(historyImages.value, incoming) : incoming
    const preferred = options.preferUrl
      ? displayImages.value.find(item => item.url === options.preferUrl)
      : null
    if (preferred) {
      selectHistoryImage(preferred)
    } else if (!selectedImageId.value && displayImages.value[0]) {
      selectHistoryImage(displayImages.value[0], { scroll: false })
    }
  } catch {
    if (!options.append) historyImages.value = []
  } finally {
    historyLoading.value = false
  }
}

async function loadMoreHistory() {
  if (!historyHasMore.value || historyLoading.value) return
  await loadImageHistory({ page: historyPage.value + 1, append: true })
}

function normalizeHistoryImage(item: any): GenImage {
  const url = String(item?.url || item?.image_url || '')
  return {
    id: `history-${item?.id || url}`,
    url,
    name: String(item?.name || filenameFromUrl(url, 'image.png')),
    created_at: item?.created_at ? String(item.created_at) : undefined,
    task_id: item?.task_id ? String(item.task_id) : undefined,
    prompt: item?.prompt ? String(item.prompt) : undefined,
    source: item?.source ? String(item.source) : undefined,
  }
}

function mergeImages(...lists: GenImage[][]): GenImage[] {
  const map = new Map<string, GenImage>()
  lists.flat().forEach((item) => {
    if (!item.url) return
    const existing = map.get(item.url)
    if (!existing || item.id.startsWith('session-')) map.set(item.url, item)
  })
  return Array.from(map.values()).sort((a, b) => imageTimeValue(b.created_at) - imageTimeValue(a.created_at))
}

function imageTimeValue(value?: string): number {
  if (!value) return 0
  const time = toDate(value)?.getTime() ?? Number.NaN
  return Number.isFinite(time) ? time : 0
}

function groupImagesByDate(images: GenImage[]): ImageDateGroup[] {
  const groups = new Map<string, ImageDateGroup>()
  images.forEach((image) => {
    const key = imageDateKey(image)
    let group = groups.get(key)
    if (!group) {
      group = {
        key,
        label: formatHistoryDateLabel(key),
        fullLabel: formatHistoryDateFullLabel(key),
        images: [],
      }
      groups.set(key, group)
    }
    group.images.push(image)
  })
  return Array.from(groups.values())
}

function imageDateKey(image: GenImage): string {
  return image.created_at ? bjtDateString(image.created_at) || 'unknown' : 'unknown'
}

function formatHistoryDateLabel(key: string): string {
  if (key === 'unknown') return '未知'
  if (isDateOffset(key, 0)) return '今天'
  if (isDateOffset(key, 1)) return '昨天'
  const [year, month, day] = key.split('-')
  const currentYear = bjtParts(Date.now())?.year
  return Number(year) === currentYear ? `${month}-${day}` : key
}

function formatHistoryDateFullLabel(key: string): string {
  if (key === 'unknown') return '未知日期'
  if (isDateOffset(key, 0)) return `今天 ${key}`
  if (isDateOffset(key, 1)) return `昨天 ${key}`
  return key
}

function isDateOffset(key: string, offsetDays: number): boolean {
  return key === bjtDateString(Date.now(), -offsetDays)
}

function selectDateGroup(group: ImageDateGroup) {
  if (group.images[0]) selectHistoryImage(group.images[0])
}

function selectHistoryImage(item: GenImage, options: { scroll?: boolean } = {}) {
  selectedImageId.value = item.id
  lastAssistantImageUrl.value = item.url
  if (options.scroll === false || conversation.value.length) return
  nextTick(() => {
    const element = chatStreamEl.value
    if (element) element.scrollTop = 0
  })
}

function isGeneratedImageUrl(url: string): boolean {
  try {
    const parsed = new URL(url, window.location.origin)
    return !/\/uploads\//.test(parsed.pathname)
  } catch {
    return true
  }
}

function openImagePreview(item: GenImage) {
  previewImage.value = item
  previewFit.value = true
  previewScale.value = 1
  previewNatural.value = { width: 0, height: 0 }
  previewVisible.value = true
  loadPreviewNatural(item.url)
}

function loadPreviewNatural(url: string) {
  const image = new Image()
  image.onload = () => {
    if (previewImage.value?.url !== url) return
    previewNatural.value = {
      width: image.naturalWidth || 0,
      height: image.naturalHeight || 0,
    }
  }
  image.onerror = () => {
    if (previewImage.value?.url === url) previewNatural.value = { width: 0, height: 0 }
  }
  image.src = url
}

function setPreviewFit() {
  previewFit.value = true
  previewScale.value = 1
}

function setPreviewActual() {
  previewFit.value = false
  previewScale.value = 1
}

function zoomPreview(delta: number) {
  previewFit.value = false
  previewScale.value = Math.min(4, Math.max(0.25, Number((previewScale.value + delta).toFixed(2))))
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

async function onDropFiles(event: DragEvent) {
  isDragOver.value = false
  const files = Array.from(event.dataTransfer?.files || []).filter(f => f.type.startsWith('image/'))
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
        if (!response?.path) throw new Error('missing path')
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
      } catch {
        if (previewUrl) URL.revokeObjectURL(previewUrl)
        failed.push(file.name)
      }
    }
    if (ok.length) {
      applyReferenceSize(ok[0])
      pendingReferences.value = [...pendingReferences.value, ...ok]
      Message.success(`已添加 ${ok.length} 张参考图`)
    }
    if (failed.length) Message.error(`上传失败：${failed.join('、')}`)
  } finally {
    uploadingReferences.value = false
  }
}

function applyReferenceSize(item: UploadedReference) {
  if (!item.width || !item.height) return
  const ratio = closestRatio(item.width / item.height)
  composer.size = ratio
  composer.resolution = normalizeGptImageResolution(ratio, composer.resolution)
}

async function readImageMeta(url: string): Promise<{ width?: number; height?: number; ratio?: string }> {
  return new Promise((resolve) => {
    const image = new Image()
    image.onload = () => {
      const width = image.naturalWidth || undefined
      const height = image.naturalHeight || undefined
      resolve({
        width,
        height,
        ratio: width && height ? closestRatio(width / height) : undefined,
      })
    }
    image.onerror = () => resolve({})
    image.src = url
  })
}

function closestRatio(aspect: number): string {
  if (!Number.isFinite(aspect) || aspect <= 0) return '1:1'
  return ALL_RATIOS.reduce((best, ratio) => {
    const score = Math.abs(ratioValue(ratio) - aspect)
    return score < Math.abs(ratioValue(best) - aspect) ? ratio : best
  }, '1:1')
}

function ratioValue(ratio: string): number {
  const [w, h] = ratio.split(':').map(Number)
  return w && h ? w / h : 1
}

function removeReference(path: string) {
  const target = pendingReferences.value.find(item => item.path === path)
  if (target?.preview_url && target.preview_url.startsWith('blob:')) URL.revokeObjectURL(target.preview_url)
  pendingReferences.value = pendingReferences.value.filter(item => item.path !== path)
  if ([maskReferencePath.value, maskGuideReferencePath.value, maskSourceReferencePath.value].includes(path)) {
    clearMaskSelection()
  }
}

function revokeAllPreviewUrls() {
  pendingReferences.value.forEach((item) => {
    if (item.preview_url && item.preview_url.startsWith('blob:')) URL.revokeObjectURL(item.preview_url)
  })
}

function clearMaskSelection() {
  maskReferencePath.value = ''
  maskGuideReferencePath.value = ''
  maskSourceReferencePath.value = ''
  const { mask: _mask, ...rest } = paramOverrides.value
  paramOverrides.value = rest
}

function onComposerKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    if (canSend.value) sendMessage()
  }
}

function usePromptExample(prompt: string) {
  composer.text = prompt
}

function scrollChatToBottom() {
  const element = chatStreamEl.value
  if (!element) return
  element.scrollTop = element.scrollHeight
}

async function sendMessage() {
  if (!canSend.value) return
  const text = composer.text.trim()
  const referencePaths = pendingReferences.value.map(item => item.path)
  const requestedAction = normalizeAction(paramOverrides.value.action || composer.action)
  const willUseLastImage = !pendingReferences.value.length && Boolean(lastAssistantImageUrl.value) && requestedAction !== 'generate'
  const userImages = (pendingReferences.value.length
    ? pendingReferences.value.map(item => item.preview_url || item.path)
    : (willUseLastImage ? [lastAssistantImageUrl.value] : [])
  ).filter(Boolean) as string[]
  const inferredAction: DialogueAction = referencePaths.length || willUseLastImage ? 'edit' : 'generate'
  const action = requestedAction === 'auto' ? inferredAction : requestedAction

  conversation.value.push({
    id: nextMessageId++,
    role: 'user',
    text,
    images: userImages,
  })
  const aiMessageId = nextMessageId++
  conversation.value.push({
    id: aiMessageId,
    role: 'assistant',
    loading: true,
    refTag: willUseLastImage ? '基于当前选中图' : (referencePaths.length ? '基于你上传的图' : ''),
  })

  composer.text = ''
  const sendingReferences = [...pendingReferences.value]
  pendingReferences.value = []

  running.value = true
  try {
    const payload: Record<string, unknown> = {
      ...paramOverrides.value,
      message: text,
      prompt: text,
      action: requestedAction,
      operation: normalizeOperation(paramOverrides.value.operation) || action,
      size: composer.size,
      resolution: composer.resolution,
      n: 1,
      thinking_mode: composer.thinking,
    }
    const refList: string[] = referencePaths.slice()
    if (willUseLastImage) refList.push(lastAssistantImageUrl.value)
    if (refList.length) payload.reference_images = refList

    const response = await hallApi.runDirectCapability(capabilityId.value, { params: payload })
    latestResult.value = response

    const status = String(((response as any)?.result?.status ?? (response as any)?.status ?? '')).toLowerCase()
    const failed = status === 'failed' || (response as any)?.exit_code

    const target = conversation.value.find(m => m.id === aiMessageId)
    if (!target) return

    target.loading = false
    target.result = response
    if (failed) {
      target.error = '这次没有生成成功'
      target.text = ''
    } else {
      const urls = extractGeneratedImageUrls(response)
      if (urls.length) {
        target.imageGroup = urls.map((url, idx) => ({
          id: `${aiMessageId}-${idx}`,
          url,
          name: filenameFromUrl(url, `生成图 ${idx + 1}`),
        }))
        const createdAt = new Date().toISOString()
        const newImages = urls.map((url, idx) => ({
          id: `session-${aiMessageId}-${idx}`,
          url,
          name: filenameFromUrl(url, `生成图 ${idx + 1}`),
          created_at: createdAt,
        }))
        sessionImages.value = mergeImages(newImages, sessionImages.value)
        selectedImageId.value = newImages[0].id
        lastAssistantImageUrl.value = urls[0]
        target.text = ''
        await loadImageHistory({ page: 1, append: false, preferUrl: urls[0] })
      } else {
        target.text = '已收到任务，但没有解析到图片 URL，可在右上"⋯"查看 JSON。'
      }
    }
    sendingReferences.forEach((item) => {
      if (item.preview_url && item.preview_url.startsWith('blob:')) URL.revokeObjectURL(item.preview_url)
    })
  } catch (error: any) {
    const target = conversation.value.find(m => m.id === aiMessageId)
    if (target) {
      target.loading = false
      target.error = error?._message || '请求失败'
    }
    Message.error(error?._message || '请求失败')
  } finally {
    running.value = false
    clearMaskSelection()
  }
}

function normalizeAction(value: unknown): DialogueAction {
  const raw = String(value || '').trim().toLowerCase()
  if (raw === 'generate' || raw === 'edit' || raw === 'auto') return raw
  return 'auto'
}

function normalizeOperation(value: unknown): 'generate' | 'edit' | '' {
  const raw = String(value || '').trim().toLowerCase()
  if (raw === 'generate' || raw === 'edit') return raw
  return ''
}

async function runQueryTask() {
  if (!canExecute.value) {
    Message.warning('无运行权限')
    return
  }
  const id = queryTaskId.value.trim()
  if (!id) {
    Message.warning('请输入任务 ID')
    return
  }
  conversation.value.push({
    id: nextMessageId++,
    role: 'user',
    text: `查询任务 ${id}`,
  })
  const aiMessageId = nextMessageId++
  conversation.value.push({
    id: aiMessageId,
    role: 'assistant',
    loading: true,
    refTag: '查询任务',
  })
  running.value = true
  try {
    const response = await hallApi.runDirectCapability(capabilityId.value, {
      params: { task_id: id, wait: true, download: false },
    })
    latestResult.value = response
    const target = conversation.value.find(m => m.id === aiMessageId)
    if (!target) return
    target.loading = false
    target.result = response
    const urls = extractGeneratedImageUrls(response)
    if (urls.length) {
      target.imageGroup = urls.map((url, idx) => ({
        id: `${aiMessageId}-${idx}`,
        url,
        name: filenameFromUrl(url, `任务图 ${idx + 1}`),
      }))
      const createdAt = new Date().toISOString()
      const newImages = urls.map((url, idx) => ({
        id: `session-${aiMessageId}-${idx}`,
        url,
        name: filenameFromUrl(url, `任务图 ${idx + 1}`),
        created_at: createdAt,
      }))
      sessionImages.value = mergeImages(newImages, sessionImages.value)
      selectedImageId.value = newImages[0].id
      lastAssistantImageUrl.value = urls[0]
    } else {
      target.text = '任务返回，但没有图片 URL。'
    }
    queryTaskVisible.value = false
    queryTaskId.value = ''
  } catch (error: any) {
    const target = conversation.value.find(m => m.id === aiMessageId)
    if (target) {
      target.loading = false
      target.error = error?._message || '查询失败'
    }
  } finally {
    running.value = false
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
    uploadOnlyResults.value = [
      ...uploadOnlyResults.value,
      { id: response.id, name: response.original_name || file.name, path: response.path },
    ]
    option.onSuccess?.(response)
    Message.success('上传完成')
  } catch (error: any) {
    option.onError?.(error)
    Message.error(error?._message || '上传失败')
  }
}

function extractGeneratedImageUrls(response: unknown): string[] {
  const urls: string[] = []
  const add = (value: unknown) => {
    if (isHttpImageUrl(value) && isGeneratedImageUrl(value) && !urls.includes(value)) urls.push(value)
  }
  const root = response && typeof response === 'object' ? response as Record<string, unknown> : {}
  const result = root.result && typeof root.result === 'object' ? root.result as Record<string, unknown> : root
  const imageUrls = result.image_urls
  if (Array.isArray(imageUrls)) imageUrls.forEach(add)
  else add(imageUrls)
  add(result.image_url)
  const raw = result.raw_response && typeof result.raw_response === 'object'
    ? result.raw_response as Record<string, unknown>
    : {}
  const rawResult = raw.result && typeof raw.result === 'object' ? raw.result as Record<string, unknown> : {}
  const data = rawResult.data
  if (Array.isArray(data)) {
    data.forEach((item) => {
      if (item && typeof item === 'object') add((item as Record<string, unknown>).url)
    })
  }
  return urls
}

function isHttpImageUrl(value: unknown): value is string {
  return (
    typeof value === 'string'
    && /^https?:\/\//.test(value)
    && /\.(png|jpe?g|webp|gif)(\?|#|$)/i.test(value)
  )
}

function collectExplicitImageUrls(value: unknown): string[] {
  const urls: string[] = []
  const add = (item: unknown) => {
    if (isHttpImageUrl(item) && !urls.includes(item)) urls.push(item)
  }
  const roots: Record<string, unknown>[] = []
  if (value && typeof value === 'object') {
    const root = value as Record<string, unknown>
    roots.push(root)
    if (root.result && typeof root.result === 'object') {
      roots.push(root.result as Record<string, unknown>)
    }
  }
  roots.forEach((root) => {
    const imageUrls = root.image_urls
    if (Array.isArray(imageUrls)) imageUrls.forEach(add)
    else add(imageUrls)
    add(root.image_url)
    add(root.url)
  })
  return urls
}

function collectImageUrls(value: unknown, out: string[] = [], seen = new Set<unknown>(), keyPath = ''): string[] {
  if (!keyPath) {
    const explicitUrls = collectExplicitImageUrls(value)
    if (explicitUrls.length) return explicitUrls
  }
  if (out.length >= 12) return out
  if (typeof value === 'string') {
    if (
      /(image_urls?|files?|file|url|image_url)/i.test(keyPath)
      && isHttpImageUrl(value)
      && !out.includes(value)
    ) out.push(value)
    return out
  }
  if (!value || typeof value !== 'object' || seen.has(value)) return out
  seen.add(value)
  if (/api_key|prompt|reference|input|message|request|uploaded|upload/i.test(keyPath)) return out
  if (Array.isArray(value)) {
    value.forEach(item => collectImageUrls(item, out, seen, keyPath))
    return out
  }
  Object.entries(value as Record<string, unknown>).forEach(([k, v]) => {
    if (/api_key|prompt|reference|input|message|request|uploaded|upload/i.test(k)) return
    collectImageUrls(v, out, seen, k)
  })
  return out
}

function filenameFromUrl(url: string, fallback: string): string {
  try {
    const parsed = new URL(url, window.location.origin)
    const last = decodeURIComponent(parsed.pathname.split('/').filter(Boolean).pop() || '')
    return last || fallback
  } catch {
    return url.split('?')[0].split('/').filter(Boolean).pop() || fallback
  }
}

function openMaskEditor(item: GenImage) {
  maskSource.value = item
  maskPrompt.value = ''
  maskEraser.value = false
  maskHasPaint.value = false
  maskSizing.value = true
  maskNatural.value = { width: 1, height: 1 }
  maskDisplay.value = fitWithin(1, 1, Math.min(window.innerWidth * 0.84, 1120), Math.min(window.innerHeight * 0.58, 620))
  maskEditorVisible.value = true
  loadMaskSourceSize(item.url)
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
    ctx.strokeStyle = 'rgba(255, 64, 64, 0.62)'
    maskHasPaint.value = true
  }
  ctx.beginPath()
  ctx.moveTo(from.x, from.y)
  ctx.lineTo(to.x, to.y)
  ctx.stroke()
  ctx.restore()
}

function canvasToPngBlob(canvas: HTMLCanvasElement): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/png'))
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

async function drawMaskSourceToCanvas(ctx: CanvasRenderingContext2D, width: number, height: number): Promise<boolean> {
  if (!maskSource.value?.url || !capabilityId.value) return false
  let objectUrl = ''
  try {
    const response = await fetch(
      hallApi.directCapabilityImageDownloadUrl(
        capabilityId.value,
        maskSource.value.url,
        filenameFromUrl(maskSource.value.url, 'source.png'),
      ),
      { credentials: 'include' },
    )
    if (!response.ok) return false
    const blob = await response.blob()
    objectUrl = URL.createObjectURL(blob)
    const image = await new Promise<HTMLImageElement | null>((resolve) => {
      const img = new Image()
      img.onload = () => resolve(img)
      img.onerror = () => resolve(null)
      img.src = objectUrl
    })
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
    const prompt = maskPrompt.value.trim()
    const sourceRef: UploadedReference = {
      name: maskSource.value.name,
      path: maskSource.value.url,
      preview_url: maskSource.value.url,
      width: sourceMeta.width || maskNatural.value.width,
      height: sourceMeta.height || maskNatural.value.height,
      ratio: sourceMeta.ratio,
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
    maskReferencePath.value = maskResponse.path
    maskGuideReferencePath.value = guideResponse.path
    maskSourceReferencePath.value = sourceRef.path
    pendingReferences.value.forEach((item) => {
      if (item.preview_url && item.preview_url.startsWith('blob:')) URL.revokeObjectURL(item.preview_url)
    })
    pendingReferences.value = [sourceRef, maskRef]
    composer.action = 'edit'
    applyReferenceSize(sourceRef)
    const guard = '第二张参考图是定点编辑区域参考图，尺寸与原图一致；红色区域表示要修改的位置。只修改红色区域；未标记区域必须保持原图主体、Logo、包装形态和背景细节不变。'
    composer.text = prompt ? `${guard}\n${prompt}` : guard
    paramOverrides.value = { ...paramOverrides.value, mask: maskResponse.path }
    maskEditorVisible.value = false
    Message.success('已生成定点区域参考，可直接发送编辑')
  } catch (error: any) {
    Message.error(error?._message || '定点区域上传失败')
  } finally {
    uploadingReferences.value = false
  }
}

function useImageAsReference(url: string, name: string) {
  if (!url) return
  readImageMeta(url).then((meta) => {
    const item = { name, path: url, preview_url: url, width: meta.width, height: meta.height, ratio: meta.ratio }
    pendingReferences.value = [...pendingReferences.value, item]
    applyReferenceSize(item)
    Message.success('已加入参考图')
  })
}

async function copyUrl(text: string) {
  if (!text) return
  if (await copyText(text)) Message.success('已复制 URL')
  else Message.warning('复制失败，请手动复制')
}

async function downloadImage(url: string, name: string) {
  if (!url || !capabilityId.value) return
  const filename = filenameFromUrl(url, name || 'image.png')
  try {
    const response = await fetch(
      hallApi.directCapabilityImageDownloadUrl(capabilityId.value, url, filename),
      { credentials: 'include' },
    )
    if (!response.ok) throw new Error(`download failed: ${response.status}`)
    const blob = await response.blob()
    const objectUrl = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = filename
    anchor.style.display = 'none'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
  } catch (error) {
    console.error(error)
    Message.error('下载失败，请稍后重试')
  }
}

function openUrl(url: string) {
  if (!url) return
  window.open(url, '_blank', 'noopener,noreferrer')
}

function resetConversation() {
  if (running.value) return
  revokeAllPreviewUrls()
  pendingReferences.value = []
  conversation.value = []
  sessionImages.value = []
  latestResult.value = null
  composer.text = ''
  composer.action = 'auto'
  composer.n = 1
  composer.thinking = false
  clearMaskSelection()
  paramOverrides.value = defaultParamOverrides()
  lastAssistantImageUrl.value = ''
  if (historyImages.value[0]) selectHistoryImage(historyImages.value[0], { scroll: false })
  else selectedImageId.value = ''
  nextMessageId = 1
}
</script>

<style scoped>
.imagegen-chat {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: calc(100dvh - 96px);
}

.head-action {
  margin-right: 4px;
}
.more-btn {
  width: 32px;
  padding: 0;
}

.chat-shell {
  position: relative;
  box-sizing: border-box;
  flex: 0 0 auto;
  min-height: 0;
  height: clamp(560px, calc(100dvh - 170px), 920px);
  display: flex;
  flex-direction: row;
  gap: 0;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-bg);
  overflow: hidden;
}

.history-rail {
  box-sizing: border-box;
  flex: 0 0 clamp(150px, 12vw, 184px);
  padding: clamp(10px, 1.6dvh, 16px) 12px 16px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: clamp(10px, 1.3dvh, 14px);
  overflow: hidden;
  border-right: 1px solid var(--ai-border);
  background: var(--ai-surface);
}

.history-date-group {
  position: relative;
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}

.history-date-group::before {
  content: '';
  position: absolute;
  top: 26px;
  bottom: -14px;
  left: 50%;
  width: 1px;
  background: var(--ai-border);
  transform: translateX(-50%);
  pointer-events: none;
}

.history-date-group:last-of-type::before {
  bottom: 0;
}

.history-date-marker {
  position: relative;
  z-index: 1;
  min-width: 62px;
  height: 24px;
  padding: 0 7px;
  border: 1px solid var(--ai-border);
  border-radius: 999px;
  background: var(--ai-surface);
  color: var(--ai-ink-2);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  font-size: 11px;
  cursor: pointer;
  box-shadow: var(--ai-shadow-1);
  transition: color 0.15s, border-color 0.15s, background 0.15s, box-shadow 0.15s;
}

.history-date-marker small {
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 999px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: 10px;
  line-height: 16px;
}

.history-date-marker:hover,
.history-date-marker.active {
  color: var(--ai-accent-ink);
  border-color: var(--ai-accent);
  background: var(--ai-accent-soft);
  box-shadow: var(--ai-shadow-1);
}

.history-date-marker.active small {
  color: var(--ai-surface);
  background: var(--ai-accent);
}

.history-date-images {
  position: relative;
  z-index: 1;
  width: 100%;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  justify-items: center;
  gap: clamp(6px, 0.9dvh, 8px);
}

.history-thumb {
  width: clamp(38px, 5.4dvh, 54px);
  height: clamp(38px, 5.4dvh, 54px);
  padding: 0;
  border: 1px solid transparent;
  border-radius: 8px;
  background: var(--ai-surface);
  cursor: pointer;
  opacity: 0.58;
  box-shadow: var(--ai-shadow-1);
  transition: opacity 0.15s, border-color 0.15s, transform 0.15s, box-shadow 0.15s;
}

.history-thumb:hover,
.history-thumb.active {
  opacity: 1;
  border-color: var(--ai-accent);
  transform: translateY(-1px);
  box-shadow: var(--ai-shadow-2);
}

.history-thumb img {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: cover;
  border-radius: 7px;
}

.history-more {
  flex: 0 0 auto;
}

.dialogue-workspace {
  min-width: 0;
  max-width: 100%;
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  background: var(--ai-bg);
}

.image-stage {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 24px 12px;
  overflow: hidden;
}

.image-stage.empty {
  color: var(--ai-ink-3);
}

.stage-image {
  display: block;
  width: auto;
  height: auto;
  max-width: min(100%, 1024px);
  max-height: min(100%, calc(100dvh - 315px));
  object-fit: contain;
  box-shadow: var(--ai-shadow-2);
  background: var(--ai-surface);
}

.stage-image.clickable {
  cursor: zoom-in;
}

.stage-tools {
  position: absolute;
  left: 50%;
  bottom: 14px;
  transform: translateX(-50%);
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 6px;
  padding: 8px;
  border-radius: 12px;
  background: var(--ai-surface);
  box-shadow: var(--ai-shadow-2);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s;
}

.image-stage:hover .stage-tools {
  opacity: 1;
  pointer-events: auto;
}

.stage-empty {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--ai-ink-3);
  font-size: 14px;
}

.stage-empty .arco-icon {
  font-size: 28px;
}

.stage-running {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  background: var(--ai-surface-2);
  backdrop-filter: blur(2px);
}

.chat-stream {
  box-sizing: border-box;
  flex: 1;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  padding: clamp(10px, 2dvh, 20px) clamp(18px, 4vw, 44px) clamp(8px, 1.3dvh, 12px);
  overscroll-behavior: contain;
  scrollbar-width: thin;
}

.chat-empty {
  width: 100%;
  max-width: 940px;
  margin: 0 auto 20px;
  padding: 0;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 18px;
}
.empty-hero {
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  margin-top: 12px;
}
.empty-icon {
  width: 64px;
  height: 64px;
  border: 1px solid var(--ai-border);
  border-radius: 16px;
  background: var(--ai-surface);
  color: var(--ai-accent-ink);
  display: grid;
  place-items: center;
  font-size: 28px;
}
.empty-orb {
  width: 76px;
  height: 76px;
  border-radius: 22px;
  background: linear-gradient(135deg, var(--ai-accent) 0%, var(--ai-accent-ink) 100%);
  color: var(--ai-surface);
  display: grid;
  place-items: center;
  font-size: 30px;
  box-shadow: var(--ai-shadow-2);
  position: relative;
}
.empty-orb::before {
  content: '';
  position: absolute;
  inset: -10px;
  border-radius: 30px;
  background: radial-gradient(circle at 50% 30%, var(--ai-accent-soft), transparent 70%);
  z-index: -1;
}
.chat-empty h2 {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  color: var(--ai-ink-1);
  letter-spacing: -0.2px;
}
.chat-empty p {
  margin: 0;
  color: var(--ai-ink-2);
  font-size: 14px;
  line-height: 1.7;
  max-width: 440px;
}

.example-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  width: 100%;
}
.example-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 5px;
  text-align: left;
  padding: 12px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  cursor: pointer;
  transition: border-color 0.15s, transform 0.15s, box-shadow 0.15s;
}
.example-card:hover {
  border-color: var(--ai-accent);
  transform: translateY(-1px);
  box-shadow: var(--ai-shadow-1);
}
.ex-tag {
  font-size: 11px;
  color: var(--ai-ink-3);
  background: var(--ai-surface-2);
  padding: 2px 8px;
  border-radius: 999px;
}
.example-card strong {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
}
.ex-sub {
  font-size: 12px;
  color: var(--ai-ink-3);
  line-height: 1.5;
}

.history-focus {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}
.history-focus-image {
  display: block;
  width: auto;
  height: auto;
  max-width: min(100%, 820px);
  max-height: min(58dvh, 680px);
  object-fit: contain;
  border-radius: 8px;
  background: var(--ai-surface-2);
  box-shadow: var(--ai-shadow-2);
}
.history-focus-image.clickable {
  cursor: zoom-in;
}

.msg-wrap {
  width: 100%;
  max-width: min(960px, 100%);
  margin: 0 auto;
  padding: 0 0 clamp(8px, 1.3dvh, 12px);
  display: flex;
  gap: 12px;
}
.msg-wrap.user {
  flex-direction: row-reverse;
}
.msg-wrap.history-message {
  max-width: min(980px, 100%);
}
.avatar {
  flex: 0 0 auto;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 10px;
  color: var(--ai-surface);
  font-weight: 600;
  margin-top: 2px;
}
.avatar.assistant {
  background: var(--ai-accent);
}
.avatar.user {
  background: var(--ai-ink-1);
}

.bubble {
  max-width: min(100% - 44px, 820px);
  padding: 12px 14px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  font-size: 13px;
  line-height: 1.7;
  color: var(--ai-ink-1);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.history-message .bubble {
  width: fit-content;
  max-width: min(100% - 44px, 900px);
  padding: 12px;
}
.bubble.user {
  max-width: min(70%, 640px);
  background: var(--ai-accent-ink);
  color: var(--ai-surface);
  border-color: var(--ai-accent-ink);
}
.user-image-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.user-image-strip img {
  width: 56px;
  height: 56px;
  border-radius: 6px;
  object-fit: contain;
  background: var(--ai-surface-2);
}
.ref-tag {
  display: inline-flex;
  align-self: flex-start;
  gap: 4px;
  align-items: center;
  padding: 2px 8px;
  background: var(--ai-accent-soft);
  color: var(--ai-accent-ink);
  font-size: 11px;
  border-radius: 999px;
}
.msg-text {
  white-space: pre-wrap;
  word-break: break-word;
}
.msg-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--ai-ink-3);
  font-size: 12px;
}
.img-grid {
  display: grid;
  gap: 10px;
  width: 100%;
  max-width: min(100%, 860px);
}
.gen-image {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  width: 100%;
}
.gen-image img {
  display: block;
  width: auto;
  max-width: min(100%, clamp(300px, 48vw, 760px));
  max-height: clamp(210px, calc(100dvh - 500px), 500px);
  object-fit: contain;
  border-radius: 8px;
  background: var(--ai-surface-2);
  box-shadow: var(--ai-shadow-1);
}
.gen-image img.clickable {
  cursor: zoom-in;
}
.img-tools {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 4px;
  width: 100%;
}
.msg-error {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--ai-bad);
  font-size: 12px;
}

/* composer */
.composer {
  box-sizing: border-box;
  width: 100%;
  flex: 0 0 auto;
  background: var(--ai-bg);
  padding: clamp(6px, 1.2dvh, 10px) clamp(18px, 4vw, 44px) clamp(8px, 1.5dvh, 14px);
}
.hidden-input {
  display: none;
}
.composer-inner {
  box-sizing: border-box;
  width: 100%;
  max-width: min(860px, 100%);
  margin: 0 auto;
  border: 1px solid var(--ai-border);
  border-radius: 14px;
  padding: 10px 12px;
  background: var(--ai-surface);
  display: flex;
  flex-direction: column;
  gap: 7px;
  max-height: none;
  overflow: visible;
  box-shadow: var(--ai-shadow-2);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.composer-inner:focus-within {
  border-color: var(--ai-accent);
  box-shadow: 0 0 0 4px var(--ai-accent-soft), var(--ai-shadow-2);
}
.composer-inner.dragover {
  border-color: var(--ai-accent-ink);
  background: var(--ai-accent-soft);
}
.reference-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.ref-chip {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  padding: 3px 6px 3px 4px;
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  font-size: 11px;
  color: var(--ai-ink-2);
  background: var(--ai-surface-2);
  max-width: min(320px, 100%);
}
.ref-chip img {
  width: 36px;
  height: 36px;
  border-radius: 3px;
  object-fit: contain;
  background: var(--ai-surface);
}
.ref-chip span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 150px;
}
.ref-chip small {
  color: var(--ai-ink-3);
  font-size: 10px;
  white-space: nowrap;
}
.ref-chip .x {
  color: var(--ai-ink-3);
  border: 0;
  background: transparent;
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  padding: 0 2px;
}
.reference-summary {
  color: var(--ai-ink-3);
  font-size: 11px;
  line-height: 1.5;
}

.composer-text :deep(.arco-textarea-wrapper) {
  border: 0;
  background: transparent;
  padding: 0;
  box-shadow: none !important;
}
.composer-text :deep(textarea) {
  border: 0;
  outline: none;
  background: transparent;
  font-size: 14px;
  line-height: 1.6;
  padding: 4px 0;
  color: var(--ai-ink-1);
  overflow-y: auto !important;
  overscroll-behavior: contain;
  scrollbar-width: thin;
}
.composer-text :deep(textarea::placeholder) {
  color: var(--ai-ink-3);
}

.composer-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.icon-btn {
  width: 30px;
  height: 30px;
  padding: 0;
  border-radius: 999px;
}

.size-pill,
.ai-pill {
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--ai-border);
  border-radius: 999px;
  background: var(--ai-surface);
  color: var(--ai-ink-2);
  font-size: 12px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}
.size-pill:hover,
.ai-pill:hover {
  color: var(--ai-accent-ink);
  border-color: var(--ai-accent);
  background: var(--ai-accent-soft);
}
.ai-pill.active {
  color: var(--ai-accent-ink);
  border-color: var(--ai-accent);
  background: var(--ai-accent-soft);
}

.send-btn {
  margin-left: auto;
  height: 32px;
  padding: 0 16px;
  border-radius: 999px;
  font-weight: 500;
}

.composer-help {
  width: 100%;
  max-width: 860px;
  margin: 6px auto 0;
  padding: 0 8px;
  color: var(--ai-ink-3);
  font-size: 11px;
  text-align: center;
}

.image-preview-shell {
  height: min(86dvh, 980px);
  max-height: calc(100dvh - 96px);
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.preview-controls {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface);
}
.preview-name {
  margin-right: auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ai-ink-2);
  font-size: 12px;
}
.scale-readout {
  min-width: 64px;
  text-align: center;
  color: var(--ai-ink-2);
  font-size: 12px;
}
.image-preview-body {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--ai-surface-2);
  overflow: auto;
}
.image-preview-body.fit {
  overflow: hidden;
}
.image-preview-body img {
  display: block;
  object-fit: contain;
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
.mask-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.composer-row {
  flex-wrap: wrap;
}

@media (max-width: 720px) {
  .imagegen-chat {
    min-height: 0;
  }
  .chat-shell {
    height: clamp(620px, calc(100dvh - 170px), 760px);
    min-height: 0;
    flex-direction: column;
    gap: 0;
  }
  .history-rail {
    flex: 0 0 auto;
    width: auto;
    max-width: 100%;
    padding: 8px 12px;
    flex-direction: row;
    justify-content: flex-start;
    align-items: center;
    gap: 12px;
    overflow-x: auto;
    overflow-y: hidden;
    border-right: 0;
    border-bottom: 1px solid var(--ai-border);
  }
  .history-date-group {
    width: auto;
    flex: 0 0 auto;
    flex-direction: row;
    align-items: center;
    gap: 8px;
  }
  .history-date-group::before {
    display: none;
  }
  .history-date-marker {
    min-width: 50px;
    height: 24px;
  }
  .history-date-images {
    display: flex;
    flex-direction: row;
    gap: 8px;
    width: auto;
  }
  .history-thumb {
    width: 48px;
    height: 48px;
  }
  .dialogue-workspace {
    min-height: 0;
  }
  .chat-stream {
    padding: 8px 12px 6px;
  }
  .chat-empty {
    gap: 14px;
  }
  .msg-wrap {
    padding-bottom: 8px;
  }
  .bubble,
  .history-message .bubble {
    padding: 8px;
    max-width: calc(100% - 40px);
  }
  .bubble.user {
    max-width: min(84%, 520px);
  }
  .image-stage {
    padding: 12px 12px 6px;
    flex-direction: column;
  }
  .stage-image {
    max-height: min(100%, calc(100dvh - 300px));
  }
  .stage-tools {
    position: static;
    transform: none;
    opacity: 1;
    pointer-events: auto;
    margin-top: 8px;
    box-shadow: none;
  }
  .example-grid {
    grid-template-columns: 1fr;
  }
  .composer {
    padding: 6px 12px 10px;
  }
  .composer-inner {
    max-height: none;
  }
  .composer-row {
    gap: 4px;
  }
  .size-pill {
    max-width: calc(100vw - 160px);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .send-btn {
    margin-left: auto;
  }
  .reference-strip {
    max-height: 28dvh;
    overflow-y: auto;
  }
  .img-grid {
    max-width: 100%;
  }
  .gen-image img {
    max-width: 100%;
    max-height: min(23dvh, 190px);
  }
  .size-menu {
    width: min(320px, calc(100vw - 32px));
  }
}

/* size dropdown content */
.size-menu {
  padding: 12px;
  width: 320px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.size-menu-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.size-menu-label {
  font-size: 11px;
  color: var(--ai-ink-3);
}
.size-menu-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.size-menu-item {
  padding: 4px 10px;
  font-size: 12px;
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  background: var(--ai-surface);
  color: var(--ai-ink-2);
  cursor: pointer;
}
.size-menu-item.active {
  color: var(--ai-accent-ink);
  border-color: var(--ai-accent);
  background: var(--ai-accent-soft);
}
.size-menu-hint {
  font-size: 11px;
  color: var(--ai-ink-3);
}

.modal-hint {
  margin: 0 0 12px;
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 1.6;
}
.upload-only-results {
  margin-top: 16px;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.upload-only-results li {
  display: grid;
  grid-template-columns: minmax(0, 140px) minmax(0, 1fr) 56px;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface-2);
}
.upload-only-results li strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}
.upload-only-results li code {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: var(--ai-ink-2);
  font-family: var(--ai-font-mono);
}

.json-panel {
  margin: 0;
  padding: 14px;
  border-radius: 6px;
  background: var(--ai-ink-1);
  color: var(--ai-accent-soft);
  font-size: 12px;
  line-height: 1.6;
  overflow: auto;
  height: calc(100vh - 120px);
}
</style>
