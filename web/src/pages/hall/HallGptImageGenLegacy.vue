<template>
  <div class="page-container imagegen-form">
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
        <a-tooltip content="基于 ToAPIs GPT-Image-2 的图片生成能力">
          <a-tag color="arcoblue" size="small">GPT-Image-2</a-tag>
        </a-tooltip>
        <a-dropdown trigger="click">
          <a-button type="text" size="small" class="more-btn">
            <template #icon><icon-more /></template>
          </a-button>
          <template #content>
            <a-doption @click="emit('switch-new')">工作区页面</a-doption>
            <a-doption @click="emit('switch-chat')">对话页面</a-doption>
            <a-doption disabled>高级表单</a-doption>
            <a-doption @click="paramsVisible = true">高级参数</a-doption>
            <a-doption @click="uploadOnlyVisible = true">仅上传取 URL</a-doption>
            <a-doption :disabled="!canExecute" @click="queryTaskVisible = true">查询已有任务</a-doption>
            <a-doption :disabled="!result" @click="jsonVisible = true">查看结果 JSON</a-doption>
          </template>
        </a-dropdown>
      </template>
    </HallDetailHeader>

    <a-spin :loading="loading" tip="加载能力配置...">
      <div v-if="capability" class="studio">
        <main class="result-stage">
          <div ref="stageBodyEl" class="stage-body" :class="{ 'is-empty': !primaryImage && !running }">
            <template v-if="primaryImage">
              <img
                class="result-image clickable"
                :src="primaryImage.url"
                :alt="primaryImage.name"
                :style="resultImageStyle"
                @click="openImagePreview(primaryImage)"
              />
              <div class="stage-meta">
                <span>{{ primaryImage.name }}</span>
                <span v-if="headMeta" class="meta">{{ headMeta }}</span>
              </div>
            </template>
            <template v-else-if="running">
              <a-spin tip="正在生成..." size="large" />
            </template>
            <template v-else>
              <div class="result-empty">
                <div class="empty-icon"><icon-image /></div>
                <h3>从一个场景开始，或直接描述你想要的图</h3>
                <p>可以文生图，也可以上传参考图后描述要怎么改</p>
                <div class="template-grid">
                  <button
                    v-for="t in TEMPLATES"
                    :key="t.key"
                    type="button"
                    class="template-card"
                    @click="quickStart(t.key)"
                  >
                    <strong>{{ t.title }}</strong>
                    <span>{{ t.subtitle }}</span>
                  </button>
                </div>
              </div>
            </template>
          </div>

          <div v-if="primaryImage" class="stage-toolbar">
            <a-button type="primary" size="small" @click="useImageAsReference(primaryImage.url, primaryImage.name)">
              <template #icon><icon-loop /></template>
              基于这张继续改
            </a-button>
            <a-button size="small" @click="openMaskEditor(primaryImage)">
              <template #icon><icon-image /></template>
              定点编辑
            </a-button>
            <a-button size="small" @click="copyUrl(primaryImage.url)">
              <template #icon><icon-copy /></template>
              复制 URL
            </a-button>
            <a-button
              size="small"
              @click="downloadImage(primaryImage.url, primaryImage.name)"
            >
              <template #icon><icon-download /></template>
              下载
            </a-button>
            <a-button size="small" @click="openUrl(primaryImage.url)">
              <template #icon><icon-launch /></template>
              打开原图
            </a-button>
            <a-button size="small" type="text" class="json-shortcut" @click="jsonVisible = true">
              JSON
              <template #icon><icon-right /></template>
            </a-button>
          </div>

          <div v-if="historyImages.length" class="history-strip">
            <div class="history-head">
              <span class="hint">历史图片 · {{ historyTotal || historyImages.length }}</span>
              <div class="history-date-switch">
                <a-button
                  size="mini"
                  type="text"
                  :disabled="!canSwitchHistoryDay(-1)"
                  @click="switchHistoryDay(-1)"
                >‹</a-button>
                <span class="history-date-label">{{ activeHistoryGroup?.label || '全部日期' }}</span>
                <a-button
                  size="mini"
                  type="text"
                  :disabled="!canSwitchHistoryDay(1) && !historyHasMore"
                  :loading="historyLoading"
                  @click="switchHistoryDay(1)"
                >›</a-button>
              </div>
              <span class="history-note">默认载入 20 张</span>
            </div>
            <div class="history-timeline">
              <div v-if="activeHistoryGroup" class="history-group">
                <span class="time-marker">{{ activeHistoryGroup.label }}</span>
                <button
                  v-for="item in activeHistoryGroup.items"
                  :key="item.id"
                  class="thumb"
                  :class="{ active: selectedImageId === item.id }"
                  type="button"
                  :title="`${item.name} · ${formatHistoryTime(item.created_at)}`"
                  @click="selectHistoryImage(item)"
                >
                  <img :src="item.url" :alt="item.name" />
                  <small>{{ formatHistoryTime(item.created_at) }}</small>
                </button>
              </div>
              <a-button
                v-if="historyHasMore"
                size="mini"
                :loading="historyLoading"
                class="history-more"
                @click="loadMoreHistory"
              >
                更多
              </a-button>
            </div>
          </div>
        </main>

        <aside class="control-panel">
          <input
            ref="fileInputEl"
            class="hidden-input"
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            multiple
            @change="onPickFiles"
          />

          <div class="control-scroll">
          <section class="control-section">
            <h4 class="section-title">
              <span class="num">1</span>
              参考图<span class="optional">（可选）</span>
              <span class="section-hint">{{ uploadedReferences.length ? '有图 → 编辑/参考生成' : '不传 → 文生图' }}</span>
            </h4>
            <div
              class="upload-zone"
              :class="{ dragover: isDragOver, has: uploadedReferences.length }"
              @click="openFilePicker"
              @dragenter.prevent="isDragOver = true"
              @dragover.prevent="isDragOver = true"
              @dragleave.prevent="isDragOver = false"
              @drop.prevent="onDropFiles"
            >
              <template v-if="!uploadedReferences.length">
                <icon-upload class="zone-icon" />
                <span class="zone-text">拖拽图片到这里，或点击上传</span>
                <span class="zone-hint">PNG / JPG / WEBP / GIF</span>
              </template>
              <template v-else>
                <article
                  v-for="item in uploadedReferences"
                  :key="item.path"
                  class="ref-thumb"
                >
                  <img v-if="item.preview_url" :src="item.preview_url" :alt="item.name" />
                  <icon-image v-else />
                  <span v-if="item.width && item.height" class="ref-size">{{ item.width }}×{{ item.height }}</span>
                  <button class="ref-remove" type="button" :aria-label="`删除 ${item.name}`" @click.stop="removeReference(item.path)">×</button>
                </article>
                <button class="ref-add" type="button" aria-label="添加参考图" @click.stop="openFilePicker">
                  <icon-plus />
                </button>
              </template>
              <a-spin v-if="uploadingReferences" class="upload-spin" />
            </div>
            <div v-if="referenceSummary" class="reference-summary">
              已按参考图自适应比例：{{ form.size }} · {{ form.resolution }}（{{ referenceSummary }}）
            </div>

            <div v-if="uploadedReferences.length" class="op-row">
              <a-radio-group v-model="form.operation" type="button" size="small">
                <a-radio value="generate">参考生成</a-radio>
                <a-radio value="edit">描述编辑</a-radio>
              </a-radio-group>
              <a-tooltip :content="opHint" position="top">
                <icon-info-circle class="op-info" />
              </a-tooltip>
            </div>
          </section>

          <div class="section-divider"></div>

          <section class="control-section">
            <h4 class="section-title">
              <span class="num">2</span>
              描述
              <span class="section-hint">中文即可</span>
            </h4>
            <div class="chips">
              <button
                v-for="t in TEMPLATES"
                :key="t.key"
                type="button"
                class="chip"
                :class="{ active: appliedTemplate === t.key }"
                @click="toggleTemplate(t.key)"
              >{{ t.title }}</button>
            </div>
            <a-textarea
              v-model="form.prompt"
              :auto-size="{ minRows: 4, maxRows: 9 }"
              allow-clear
              :placeholder="promptPlaceholder"
              class="prompt-input"
            />
          </section>

          <div class="section-divider"></div>

          <section class="control-section">
            <h4 class="section-title">
              <span class="num">3</span>
              出图设置
            </h4>
            <div class="field-row">
              <label>比例</label>
              <a-radio-group v-model="form.size" type="button" size="small">
                <a-radio v-for="s in PRIMARY_RATIOS" :key="s" :value="s">{{ s }}</a-radio>
              </a-radio-group>
              <a-dropdown trigger="click">
                <a-button size="small" class="extra-btn">
                  {{ EXTRA_RATIOS.includes(form.size) ? form.size : '更多' }}
                  <template #icon><icon-down /></template>
                </a-button>
                <template #content>
                  <a-doption v-for="s in EXTRA_RATIOS" :key="s" @click="form.size = s">{{ s }}</a-doption>
                </template>
              </a-dropdown>
            </div>

            <div class="field-row">
              <label>分辨率</label>
              <a-radio-group v-model="form.resolution" type="button" size="small">
                <a-radio value="1K">1K</a-radio>
                <a-radio value="2K">2K</a-radio>
                <a-radio value="4K">4K</a-radio>
              </a-radio-group>
              <span class="field-hint">{{ resolutionHint }}</span>
            </div>

            <div class="field-row">
              <label>张数</label>
              <a-input-number v-model="form.n" :min="1" :max="4" :precision="0" :style="{ width: '88px' }" />
            </div>

            <div class="advanced-row" :class="{ on: form.thinking_mode }" @click="form.thinking_mode = !form.thinking_mode">
              <icon-thunderbolt class="ad-icon" />
              <div class="ad-text">
                <strong>Thinking</strong>
                <span>先做产品校验，再调用 GPT-Image-2</span>
              </div>
              <a-switch :model-value="form.thinking_mode" size="small" @click.stop="form.thinking_mode = !form.thinking_mode" />
            </div>
          </section>
          </div>

          <div class="cta-bar">
            <a-button
              class="cta"
              type="primary"
              long
              size="large"
              :loading="running"
              :disabled="!canRun"
              @click="runGenerate"
            >
              <template #icon><icon-send /></template>
              {{ ctaLabel }}
            </a-button>
            <a-button class="reset" size="large" :disabled="running" @click="resetAll">清空</a-button>
          </div>
        </aside>
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
        :title="capability?.display_name || 'GPT ImageGen'"
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
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import {
  IconCopy,
  IconDown,
  IconDownload,
  IconImage,
  IconInfoCircle,
  IconLaunch,
  IconLoop,
  IconMore,
  IconPlus,
  IconRight,
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
import { bjtDateString, formatTimeOnly, toDate } from '@/utils/format'

defineProps<{
  runUi?: Record<string, any> | null
}>()

const emit = defineEmits(['switch-new', 'switch-chat'])

type TemplateKey = 'main' | 'banner' | 'detail' | 'white' | 'logo'
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
  created_at?: string
  task_id?: string
  prompt?: string
  source?: string
}

const TEMPLATES: { key: TemplateKey; title: string; subtitle: string; size: string; resolution: string; prompt: string }[] = [
  {
    key: 'main',
    title: '电商主图',
    subtitle: '1:1 高转化商品主图',
    size: '1:1',
    resolution: '1K',
    prompt:
      '请生成一张高转化电商商品主图。产品主体清晰居中，占画面 65%-75%，干净高级的商业摄影质感。背景简洁，光影柔和，预留清晰卖点文字区域，主标题突出核心卖点。不出现真实品牌、错误 Logo、乱码文字、水印、二维码或价格误导信息。',
  },
  {
    key: 'banner',
    title: 'Banner 横图',
    subtitle: '16:9 活动页与店铺首页',
    size: '16:9',
    resolution: '2K',
    prompt:
      '请生成一张电商活动 Banner 横图，适合首页焦点图或店铺活动页。产品位于视觉黄金区，左/右侧保留清晰文案区域，背景有活动氛围但不抢主体。高级商业海报质感，主标题突出促销/卖点，副标题补充场景；避免乱码、水印、二维码。',
  },
  {
    key: 'detail',
    title: '详情页卖点',
    subtitle: '9:16 竖版信息层级',
    size: '9:16',
    resolution: '2K',
    prompt:
      '请生成一张电商详情页卖点图，适合竖版详情页模块。围绕产品展示核心功能、材质工艺、适用场景和购买理由，信息层级清楚。上方产品视觉，下方或侧边卖点说明区域，留白充足；可信赖、专业、有品质感。',
  },
  {
    key: 'white',
    title: '白底主图',
    subtitle: '平台合规纯净图',
    size: '1:1',
    resolution: '1K',
    prompt:
      '请生成一张电商白底主图。产品居中，背景纯白或接近纯白，边缘干净，阴影轻柔自然。产品占画面 70% 左右，材质真实，细节清楚，不增加多余道具、文字、水印或二维码。',
  },
  {
    key: 'logo',
    title: '保留 Logo',
    subtitle: '参考图一致性优先',
    size: '1:1',
    resolution: '1K',
    prompt:
      '请基于参考图生成电商宣传图，严格保留产品外观、Logo、包装形态和核心识别元素。只更换背景、配色、光影和营销文案区域；不改变产品结构，不生成错误 Logo 或虚假品牌。',
  },
]

const PRIMARY_RATIOS = ['1:1', '4:5', '3:4', '16:9', '9:16']
const EXTRA_RATIOS = ['3:2', '2:3', '4:3', '5:4', '2:1', '1:2', '21:9', '9:21']
const ALL_RATIOS = [...PRIMARY_RATIOS, ...EXTRA_RATIOS]

const route = useRoute()
const capabilityId = computed(() => String(route.params.id || ''))

const loading = ref(false)
const running = ref(false)
const capability = ref<any>(null)
const result = ref<any>(null)

const fileInputEl = ref<HTMLInputElement | null>(null)
const isDragOver = ref(false)
const uploadingReferences = ref(false)
const uploadedReferences = ref<UploadedReference[]>([])
const appliedTemplate = ref<TemplateKey | ''>('')
const selectedImageId = ref('')
const historyImages = ref<GalleryImage[]>([])
const historyTotal = ref(0)
const historyPage = ref(1)
const historyLoading = ref(false)
const historyPageSize = 20
const selectedHistoryDayKey = ref('')

const uploadOnlyVisible = ref(false)
const uploadOnlyResults = ref<UploadedReference[]>([])
const queryTaskVisible = ref(false)
const queryTaskId = ref('')
const jsonVisible = ref(false)
const paramsVisible = ref(false)
const stageBodyEl = ref<HTMLElement | null>(null)
const stageSize = ref({ width: 0, height: 0 })
const primaryNatural = ref({ width: 0, height: 0 })
const previewVisible = ref(false)
const previewImage = ref<GalleryImage | null>(null)
const previewFit = ref(true)
const previewScale = ref(1)
const previewNatural = ref({ width: 0, height: 0 })
const maskEditorVisible = ref(false)
const maskSource = ref<GalleryImage | null>(null)
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

const form = reactive({
  prompt: '',
  reference_images: [] as string[],
  operation: 'generate' as 'generate' | 'edit',
  size: '1:1',
  resolution: '1K',
  n: 1,
  thinking_mode: false,
})

let maskPainting = false
let maskLastPoint: { x: number; y: number } | null = null
let stageResizeObserver: ResizeObserver | null = null

const promptPlaceholder = computed(() => {
  if (uploadedReferences.value.length && form.operation === 'edit') {
    return '描述要怎么修改这张图，例：保留主体，背景换成浅黄色，左上方加一个红色星星'
  }
  if (uploadedReferences.value.length) {
    return '描述参考生成要求，例：保持产品造型，换成高级红色活动背景'
  }
  return '描述要生成的图片，例：白底商品图，产品居中，柔光，高级商业摄影'
})

const opHint = computed(() => {
  if (form.operation === 'edit') return '描述编辑：通过参考图参数保留主体并按描述修改'
  return '参考生成：以参考图为主体或风格基础重新生成，构图自由度更高'
})

const resolutionHint = computed(() => {
  if (form.resolution === '4K') return '4K 仅支持 16:9/9:16/2:1/1:2/21:9/9:21'
  if (form.resolution === '1K') return '1K 仅支持 1:1/3:2/2:3'
  return '2K 支持全部比例'
})

const referenceSummary = computed(() => {
  const first = uploadedReferences.value[0]
  if (!first) return ''
  const count = uploadedReferences.value.length > 1 ? `等 ${uploadedReferences.value.length} 张` : '1 张'
  const size = first.width && first.height ? `${first.width}x${first.height}` : ''
  return [count, size, first.ratio].filter(Boolean).join(' · ')
})

const canExecute = computed(() => capability.value?.permissions?.execute !== false)

const ctaLabel = computed(() => {
  if (!canExecute.value) return '无运行权限'
  if (uploadedReferences.value.length && form.operation === 'edit') return '编辑图片'
  if (uploadedReferences.value.length) return '参考生成'
  return '生成图片'
})

const canRun = computed(() => {
  if (!canExecute.value) return false
  if (running.value || uploadingReferences.value) return false
  if (!String(form.prompt || '').trim()) return false
  return true
})

const primaryImage = computed<GalleryImage | null>(() => {
  if (!historyImages.value.length) return null
  return (
    historyImages.value.find(img => img.id === selectedImageId.value)
    || historyImages.value[0]
    || null
  )
})

const headMeta = computed(() => {
  const r = result.value?.result || result.value
  if (!r || !primaryImage.value) return ''
  const taskId = (r.task_id || '').toString().slice(0, 24)
  const status = r.status || ''
  return [taskId, status].filter(Boolean).join(' · ')
})

const jsonText = computed(() => result.value ? JSON.stringify(result.value, null, 2) : '{}')
const historyHasMore = computed(() => historyImages.value.length < historyTotal.value)
const previewScaleLabel = computed(() => previewFit.value ? '适合屏幕' : `${Math.round(previewScale.value * 100)}%`)
const resultImageStyle = computed(() => {
  const naturalWidth = primaryNatural.value.width
  const naturalHeight = primaryNatural.value.height
  const availableWidth = stageSize.value.width
  const availableHeight = stageSize.value.height
  if (!naturalWidth || !naturalHeight || !availableWidth || !availableHeight) {
    return {
      width: 'auto',
      height: 'auto',
      maxWidth: '100%',
      maxHeight: '100%',
    }
  }
  const scale = Math.min(availableWidth / naturalWidth, availableHeight / naturalHeight, 1)
  return {
    width: `${Math.max(1, Math.floor(naturalWidth * scale))}px`,
    height: `${Math.max(1, Math.floor(naturalHeight * scale))}px`,
    maxWidth: '100%',
    maxHeight: '100%',
  }
})
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
const historyGroups = computed(() => {
  const groups: { key: string; label: string; items: GalleryImage[] }[] = []
  const byKey = new Map<string, { key: string; label: string; items: GalleryImage[] }>()
  historyImages.value.forEach((item) => {
    const key = historyDayKey(item.created_at)
    let group = byKey.get(key)
    if (!group) {
      group = { key, label: historyDayLabel(item.created_at), items: [] }
      byKey.set(key, group)
      groups.push(group)
    }
    group.items.push(item)
  })
  return groups
})
const activeHistoryGroup = computed(() => {
  if (!historyGroups.value.length) return null
  return (
    historyGroups.value.find(group => group.key === selectedHistoryDayKey.value)
    || historyGroups.value[0]
  )
})
const activeHistoryGroupIndex = computed(() => {
  if (!activeHistoryGroup.value) return -1
  return historyGroups.value.findIndex(group => group.key === activeHistoryGroup.value?.key)
})

watch(
  () => form.size,
  (newSize) => {
    form.resolution = normalizeGptImageResolution(newSize, form.resolution)
  },
)
watch(
  () => form.resolution,
  (resolution) => {
    form.resolution = normalizeGptImageResolution(form.size, resolution)
  },
)

watch(uploadedReferences, (list) => {
  form.reference_images = list.map(item => item.path)
  if (!list.length) form.operation = 'generate'
  const missingMaskGuide = maskGuideReferencePath.value && !list.some(item => item.path === maskGuideReferencePath.value)
  const missingMaskSource = maskSourceReferencePath.value && !list.some(item => item.path === maskSourceReferencePath.value)
  if (maskReferencePath.value && (missingMaskGuide || missingMaskSource)) {
    maskGuideReferencePath.value = ''
    maskSourceReferencePath.value = ''
    maskReferencePath.value = ''
    const { mask: _mask, ...rest } = paramOverrides.value
    paramOverrides.value = rest
  }
})

onMounted(load)
onBeforeUnmount(() => {
  stageResizeObserver?.disconnect()
  revokeAllPreviewUrls()
})
watch(() => route.params.id, load)
watch(stageBodyEl, observeStageBody, { immediate: true })
watch(
  () => primaryImage.value?.url,
  (url) => loadPrimaryNatural(url || ''),
  { immediate: true },
)
watch(
  () => selectedImageId.value,
  (id) => {
    const item = historyImages.value.find(image => image.id === id)
    if (item) selectedHistoryDayKey.value = historyDayKey(item.created_at)
  },
)
watch(historyGroups, (groups) => {
  if (!groups.length) {
    selectedHistoryDayKey.value = ''
    return
  }
  if (!groups.some(group => group.key === selectedHistoryDayKey.value)) {
    selectedHistoryDayKey.value = groups[0].key
  }
})

function observeStageBody(el: HTMLElement | null) {
  stageResizeObserver?.disconnect()
  stageResizeObserver = null
  if (!el) return
  updateStageSize(el)
  stageResizeObserver = new ResizeObserver(() => updateStageSize(el))
  stageResizeObserver.observe(el)
}

function updateStageSize(el: HTMLElement) {
  const style = window.getComputedStyle(el)
  const width = el.clientWidth - parseFloat(style.paddingLeft || '0') - parseFloat(style.paddingRight || '0')
  const height = el.clientHeight - parseFloat(style.paddingTop || '0') - parseFloat(style.paddingBottom || '0')
  stageSize.value = {
    width: Math.max(1, Math.floor(width)),
    height: Math.max(1, Math.floor(height)),
  }
}

async function loadPrimaryNatural(url: string) {
  primaryNatural.value = { width: 0, height: 0 }
  if (!url) return
  const meta = await readImageMeta(url)
  if (primaryImage.value?.url !== url) return
  primaryNatural.value = {
    width: meta.width || 0,
    height: meta.height || 0,
  }
}

async function load() {
  if (!capabilityId.value) return
  loading.value = true
  historyImages.value = []
  historyTotal.value = 0
  historyPage.value = 1
  selectedImageId.value = ''
  selectedHistoryDayKey.value = ''
  try {
    capability.value = await hallApi.directCapability(capabilityId.value)
    await loadImageHistory({ page: 1, append: false })
  } catch {
    capability.value = null
  } finally {
    loading.value = false
  }
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
      uploadedReferences.value = [...uploadedReferences.value, ...ok]
      Message.success(`已上传 ${ok.length} 张参考图`)
    }
    if (failed.length) Message.error(`上传失败：${failed.join('、')}`)
  } finally {
    uploadingReferences.value = false
  }
}

function applyReferenceSize(item: UploadedReference) {
  if (!item.width || !item.height) return
  const ratio = closestRatio(item.width / item.height)
  form.size = ratio
  form.resolution = normalizeGptImageResolution(ratio, form.resolution)
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
  const target = uploadedReferences.value.find(item => item.path === path)
  if (target?.preview_url && target.preview_url.startsWith('blob:')) URL.revokeObjectURL(target.preview_url)
  uploadedReferences.value = uploadedReferences.value.filter(item => item.path !== path)
}

function revokeAllPreviewUrls() {
  uploadedReferences.value.forEach((item) => {
    if (item.preview_url && item.preview_url.startsWith('blob:')) URL.revokeObjectURL(item.preview_url)
  })
}

function quickStart(key: TemplateKey) {
  toggleTemplate(key)
}

function toggleTemplate(key: TemplateKey) {
  if (appliedTemplate.value === key) {
    appliedTemplate.value = ''
    return
  }
  const t = TEMPLATES.find(item => item.key === key)
  if (!t) return
  appliedTemplate.value = key
  form.prompt = t.prompt
  form.size = t.size
  form.resolution = t.resolution
}

async function runGenerate() {
  if (!canRun.value) return
  running.value = true
  result.value = null
  try {
    const payload: Record<string, unknown> = {
      ...paramOverrides.value,
      prompt: form.prompt,
      operation: form.operation,
      size: form.size,
      resolution: form.resolution,
      n: form.n,
      thinking_mode: form.thinking_mode,
    }
    if (uploadedReferences.value.length) {
      payload.reference_images = uploadedReferences.value.map(item => item.path)
    }
    if (maskReferencePath.value) {
      payload.mask = maskReferencePath.value
    }
    const response = await hallApi.runDirectCapability(capabilityId.value, { params: payload })
    result.value = response
    const status = String(((response as any)?.result?.status ?? (response as any)?.status ?? '')).toLowerCase()
    if (status === 'failed' || (response as any)?.exit_code) {
      Message.error('生成失败，可在右上"⋯"菜单查看 JSON 详情')
      return
    }
    const generatedImages = appendHistoryFromResult(response)
    await loadImageHistory({
      page: 1,
      append: false,
      preferLatest: true,
      preferUrl: generatedImages[0]?.url,
      preserve: generatedImages,
    })
    Message.success('生成完成')
  } catch (error: any) {
    Message.error(error?._message || '生成失败')
  } finally {
    running.value = false
  }
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
  running.value = true
  result.value = null
  try {
    const response = await hallApi.runDirectCapability(capabilityId.value, {
      params: { task_id: id, wait: true, download: false },
    })
    result.value = response
    const generatedImages = appendHistoryFromResult(response)
    await loadImageHistory({
      page: 1,
      append: false,
      preferLatest: true,
      preferUrl: generatedImages[0]?.url,
      preserve: generatedImages,
    })
    queryTaskVisible.value = false
    queryTaskId.value = ''
    Message.success('查询完成')
  } catch (error: any) {
    Message.error(error?._message || '查询失败')
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

function appendHistoryFromResult(response: unknown): GalleryImage[] {
  const urls = collectImageUrls(response)
  if (!urls.length) return []
  const now = new Date().toISOString()
  const newcomers: GalleryImage[] = urls.map((url, idx) => ({
    id: `${Date.now()}-${idx}`,
    url,
    name: filenameFromUrl(url, `生成图 ${historyImages.value.length + idx + 1}`),
    created_at: now,
  }))
  historyImages.value = mergeHistoryImages(newcomers, historyImages.value)
  historyTotal.value = Math.max(historyTotal.value, historyImages.value.length)
  selectedImageId.value = newcomers[0].id
  selectedHistoryDayKey.value = historyDayKey(newcomers[0].created_at)
  return newcomers
}

async function loadImageHistory(
  options: {
    page?: number
    append?: boolean
    preferLatest?: boolean
    preferUrl?: string
    preserve?: GalleryImage[]
  } = {},
) {
  if (!capabilityId.value || historyLoading.value) return
  const page = options.page || 1
  const previousSelected = selectedImageId.value
  const previousUrl = historyImages.value.find(item => item.id === previousSelected)?.url || ''
  historyLoading.value = true
  try {
    const response: any = await hallApi.directCapabilityImageHistory(capabilityId.value, {
      page,
      page_size: historyPageSize,
    })
    const incoming = Array.isArray(response?.items) ? response.items.map(normalizeHistoryImage) : []
    historyTotal.value = Number(response?.total || incoming.length || 0)
    historyPage.value = page
    const preserved = options.preserve || []
    historyImages.value = options.append
      ? mergeHistoryImages(historyImages.value, incoming, preserved)
      : mergeHistoryImages(incoming, preserved)
    const preferredByUrl = options.preferUrl
      ? historyImages.value.find(item => item.url === options.preferUrl)
      : null
    const previousByUrl = previousUrl
      ? historyImages.value.find(item => item.url === previousUrl)
      : null
    if (preferredByUrl) {
      selectedImageId.value = preferredByUrl.id
      selectedHistoryDayKey.value = historyDayKey(preferredByUrl.created_at)
    } else if (options.preferLatest && historyImages.value[0]) {
      selectedImageId.value = historyImages.value[0].id
      selectedHistoryDayKey.value = historyDayKey(historyImages.value[0].created_at)
    } else if (previousSelected && historyImages.value.some(item => item.id === previousSelected)) {
      selectedImageId.value = previousSelected
      const item = historyImages.value.find(image => image.id === previousSelected)
      if (item) selectedHistoryDayKey.value = historyDayKey(item.created_at)
    } else if (previousByUrl) {
      selectedImageId.value = previousByUrl.id
      selectedHistoryDayKey.value = historyDayKey(previousByUrl.created_at)
    } else {
      selectedImageId.value = historyImages.value[0]?.id || ''
      selectedHistoryDayKey.value = historyImages.value[0] ? historyDayKey(historyImages.value[0].created_at) : ''
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

function canSwitchHistoryDay(direction: number) {
  const index = activeHistoryGroupIndex.value
  if (index < 0) return false
  const targetIndex = index + direction
  return targetIndex >= 0 && targetIndex < historyGroups.value.length
}

async function switchHistoryDay(direction: number) {
  const index = activeHistoryGroupIndex.value
  if (index < 0) return
  const targetIndex = index + direction
  if (targetIndex >= 0 && targetIndex < historyGroups.value.length) {
    selectedHistoryDayKey.value = historyGroups.value[targetIndex].key
    const first = historyGroups.value[targetIndex].items[0]
    if (first) selectedImageId.value = first.id
    return
  }
  if (direction > 0 && historyHasMore.value && !historyLoading.value) {
    const previousCount = historyGroups.value.length
    await loadMoreHistory()
    const nextGroup = historyGroups.value[previousCount] || historyGroups.value[historyGroups.value.length - 1]
    if (nextGroup) {
      selectedHistoryDayKey.value = nextGroup.key
      if (nextGroup.items[0]) selectedImageId.value = nextGroup.items[0].id
    }
  }
}

function normalizeHistoryImage(item: any): GalleryImage {
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

function mergeHistoryImages(...lists: GalleryImage[][]): GalleryImage[] {
  const map = new Map<string, GalleryImage>()
  lists.flat().forEach((item) => {
    if (!item.url) return
    const existing = map.get(item.url)
    if (!existing || (!existing.created_at && item.created_at) || item.id.startsWith('history-')) {
      map.set(item.url, item)
    }
  })
  return Array.from(map.values()).sort((a, b) => historyTimeValue(b.created_at) - historyTimeValue(a.created_at))
}

function historyTimeValue(value?: string): number {
  if (!value) return 0
  const time = toDate(value)?.getTime() ?? Number.NaN
  return Number.isFinite(time) ? time : 0
}

function historyDayKey(value?: string): string {
  return value ? bjtDateString(value) || 'unknown' : 'unknown'
}

function historyDayLabel(value?: string): string {
  const key = value ? bjtDateString(value) : ''
  if (!key) return '未知时间'
  if (key === bjtDateString(Date.now())) return '今天'
  if (key === bjtDateString(Date.now(), -1)) return '昨天'
  const [, month, day] = key.split('-')
  return `${Number(month)}月${Number(day)}日`
}

function formatHistoryTime(value?: string): string {
  return formatTimeOnly(value).replace('-', '--:--')
}

function selectHistoryImage(item: GalleryImage, showPreview = false) {
  selectedImageId.value = item.id
  selectedHistoryDayKey.value = historyDayKey(item.created_at)
  if (showPreview) openImagePreview(item)
}

function openImagePreview(item: GalleryImage) {
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

function openMaskEditor(item: GalleryImage) {
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
    uploadedReferences.value = [sourceRef, maskRef]
    form.operation = 'edit'
    applyReferenceSize(sourceRef)
    const guard = '第二张参考图是定点编辑区域参考图，尺寸与原图一致；红色区域表示要修改的位置。只修改红色区域；未标记区域必须保持原图主体、Logo、包装形态和背景细节不变。'
    form.prompt = prompt ? `${guard}\n${prompt}` : guard
    paramOverrides.value = { ...paramOverrides.value, mask: maskResponse.path }
    maskEditorVisible.value = false
    Message.success('已生成定点区域参考，可直接编辑图片')
  } catch (error: any) {
    Message.error(error?._message || '定点区域上传失败')
  } finally {
    uploadingReferences.value = false
  }
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

async function useImageAsReference(url: string, name: string) {
  if (!url) return
  const meta = await readImageMeta(url)
  uploadedReferences.value = [
    ...uploadedReferences.value,
    { name, path: url, preview_url: url, width: meta.width, height: meta.height, ratio: meta.ratio },
  ]
  applyReferenceSize({ name, path: url, ...meta })
  form.operation = 'edit'
  Message.success('已加入参考图，可继续修改')
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

function resetAll() {
  form.prompt = ''
  form.reference_images = []
  form.operation = 'generate'
  form.size = '1:1'
  form.resolution = '1K'
  form.n = 1
  form.thinking_mode = false
  maskReferencePath.value = ''
  maskGuideReferencePath.value = ''
  maskSourceReferencePath.value = ''
  paramOverrides.value = defaultParamOverrides()
  appliedTemplate.value = ''
  revokeAllPreviewUrls()
  uploadedReferences.value = []
  selectedImageId.value = historyImages.value[0]?.id || ''
  selectedHistoryDayKey.value = historyImages.value[0] ? historyDayKey(historyImages.value[0].created_at) : ''
  result.value = null
}
</script>

<style scoped>
.imagegen-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: calc(100dvh - 12rem);
}

.more-btn {
  width: 32px;
  padding: 0;
}

.studio {
  flex: 1;
  min-height: 0;
  height: clamp(560px, calc(100dvh - 170px), 920px);
  display: grid;
  grid-template-columns: minmax(0, 1fr) clamp(340px, 28vw, 460px);
  gap: 16px;
  align-items: stretch;
}

/* RESULT (left) */
.result-stage {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  overflow: hidden;
}

.stage-body {
  flex: 1;
  min-height: 320px;
  position: relative;
  display: grid;
  place-items: center;
  padding: clamp(12px, 2vw, 24px);
  background: var(--ai-surface-2);
  overflow: hidden;
}

.result-image {
  display: block;
  max-width: 100%;
  max-height: 100%;
  width: auto;
  height: auto;
  object-fit: contain;
  border-radius: 6px;
  background: var(--ai-surface);
  box-shadow: var(--ai-shadow-2);
}
.result-image.clickable {
  cursor: zoom-in;
}
.stage-meta {
  position: absolute;
  bottom: 16px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  background: var(--ai-ink-2);
  color: var(--ai-surface);
  font-size: 12px;
  border-radius: 999px;
  max-width: 80%;
}
.stage-meta span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.stage-meta .meta {
  font-family: var(--ai-font-mono);
  opacity: 0.8;
  font-size: 11px;
}

.result-empty {
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  width: 100%;
  max-width: 760px;
  margin: 0 auto;
}
.empty-icon {
  width: 56px;
  height: 56px;
  border-radius: 12px;
  background: var(--ai-accent-soft);
  color: var(--ai-accent-ink);
  display: grid;
  place-items: center;
  font-size: 26px;
}
.result-empty h3 {
  margin: 4px 0 0;
  font-size: 16px;
  font-weight: 600;
}
.result-empty p {
  margin: 0;
  color: var(--ai-ink-3);
  font-size: 13px;
}
.template-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
  width: 100%;
  margin-top: 16px;
}
.template-card {
  text-align: left;
  padding: 12px 14px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 4px;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.template-card:hover {
  border-color: var(--ai-accent);
  box-shadow: var(--ai-shadow-1);
}
.template-card strong {
  font-size: 13px;
  color: var(--ai-ink-1);
  font-weight: 600;
}
.template-card span {
  font-size: 12px;
  color: var(--ai-ink-3);
}

.stage-toolbar {
  flex: 0 0 auto;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  border-top: 1px solid var(--ai-border);
  background: var(--ai-surface);
}
.stage-toolbar .json-shortcut {
  margin-left: auto;
  color: var(--ai-ink-3);
}

.history-strip {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
  padding: 8px 14px 10px;
  border-top: 1px solid var(--ai-border);
  background: var(--ai-surface);
}
.history-head,
.history-timeline,
.history-group {
  display: flex;
  align-items: center;
}
.history-head {
  justify-content: space-between;
  gap: 10px;
}
.history-strip .hint {
  color: var(--ai-ink-3);
  font-size: 12px;
}
.history-date-switch {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  color: var(--ai-ink-2);
}
.history-date-switch :deep(.arco-btn-size-mini) {
  width: 22px;
  height: 22px;
  padding: 0;
  font-size: 16px;
  line-height: 1;
}
.history-date-label {
  min-width: 72px;
  max-width: 128px;
  text-align: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 600;
}
.history-note {
  color: var(--ai-ink-4);
  font-size: 11px;
}
.history-timeline {
  gap: 10px;
  overflow-x: auto;
  scrollbar-width: thin;
  padding-bottom: 2px;
}
.history-group {
  flex: 0 0 auto;
  gap: 6px;
}
.time-marker {
  flex: 0 0 auto;
  min-width: 42px;
  color: var(--ai-ink-3);
  font-size: 11px;
  line-height: 1.2;
  text-align: right;
  padding-right: 2px;
  border-right: 1px solid var(--ai-border);
}
.thumb {
  flex: 0 0 auto;
  width: 54px;
  height: 66px;
  padding: 0;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface-2);
  cursor: pointer;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.thumb img {
  width: 100%;
  height: 48px;
  object-fit: cover;
  display: block;
}
.thumb small {
  height: 18px;
  line-height: 18px;
  font-size: 10px;
  color: var(--ai-ink-3);
  background: var(--ai-surface);
}
.thumb.active {
  border-color: var(--ai-accent);
  outline: 2px solid var(--ai-accent-soft);
}
.history-more {
  flex: 0 0 auto;
}

/* CONTROL PANEL (right) — single panel with sections */
.control-panel {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  overflow: hidden;
}
.control-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.control-section {
  padding: 14px 16px;
}
.section-divider {
  height: 1px;
  background: var(--ai-border);
  margin: 0 16px;
}
.section-title {
  margin: 0 0 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
}
.section-title .num {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  display: inline-grid;
  place-items: center;
  font-size: 11px;
  font-weight: 700;
}
.section-title .optional {
  margin-left: -2px;
  color: var(--ai-ink-3);
  font-size: 12px;
  font-weight: 400;
}
.section-title .section-hint {
  margin-left: auto;
  color: var(--ai-ink-3);
  font-size: 12px;
  font-weight: 400;
}

/* upload zone */
.hidden-input {
  display: none;
}
.upload-zone {
  position: relative;
  min-height: 96px;
  border: 1.5px dashed var(--ai-border-2);
  border-radius: 8px;
  padding: 14px;
  background: var(--ai-surface-2);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 6px;
}
.upload-zone:hover,
.upload-zone.dragover {
  border-color: var(--ai-accent);
  background: var(--ai-accent-soft);
}
.upload-zone.has {
  cursor: default;
  align-content: flex-start;
  justify-content: flex-start;
  align-items: stretch;
  gap: 8px;
  padding: 10px;
}
.zone-icon {
  font-size: 22px;
  color: var(--ai-accent-ink);
}
.zone-text {
  font-size: 13px;
  color: var(--ai-ink-2);
}
.zone-hint {
  flex-basis: 100%;
  text-align: center;
  font-size: 11px;
  color: var(--ai-ink-3);
}
.ref-thumb {
  position: relative;
  width: clamp(64px, 7vw, 92px);
  aspect-ratio: 1;
  border-radius: 6px;
  background: var(--ai-surface-2);
  overflow: hidden;
  border: 1px solid var(--ai-border);
}
.ref-thumb img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}
.ref-size {
  position: absolute;
  left: 4px;
  bottom: 4px;
  max-width: calc(100% - 8px);
  padding: 1px 4px;
  border-radius: 4px;
  background: var(--ai-ink-2);
  color: var(--ai-surface);
  font-size: 10px;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ref-remove {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 18px;
  height: 18px;
  border: 0;
  border-radius: 50%;
  background: var(--ai-ink-2);
  color: var(--ai-surface);
  font-size: 12px;
  cursor: pointer;
  line-height: 16px;
}
.ref-add {
  width: 64px;
  height: 64px;
  border: 1.5px dashed var(--ai-border-2);
  border-radius: 6px;
  background: transparent;
  color: var(--ai-ink-3);
  cursor: pointer;
  display: grid;
  place-items: center;
}
.ref-add:hover {
  color: var(--ai-accent-ink);
  border-color: var(--ai-accent);
}
.upload-spin {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  background: var(--ai-surface);
  border-radius: 8px;
}
.reference-summary {
  margin-top: 8px;
  color: var(--ai-ink-3);
  font-size: 11px;
  line-height: 1.5;
}

.op-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}
.op-info {
  color: var(--ai-ink-3);
  cursor: help;
}

/* description chips */
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}
.chip {
  padding: 4px 10px;
  font-size: 12px;
  border: 1px solid var(--ai-border);
  border-radius: 999px;
  background: var(--ai-surface);
  color: var(--ai-ink-2);
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}
.chip:hover {
  color: var(--ai-accent-ink);
  border-color: var(--ai-accent);
}
.chip.active {
  color: var(--ai-accent-ink);
  border-color: var(--ai-accent);
  background: var(--ai-accent-soft);
}

.prompt-input :deep(textarea) {
  line-height: 1.7;
  font-size: 13px;
}

/* settings */
.field-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.field-row label {
  width: 40px;
  flex: 0 0 auto;
  font-size: 12px;
  color: var(--ai-ink-2);
}
.field-row .extra-btn {
  white-space: nowrap;
}
.field-hint {
  flex-basis: 100%;
  margin-left: 48px;
  margin-top: -2px;
  color: var(--ai-ink-3);
  font-size: 11px;
}

.advanced-row {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.advanced-row:hover {
  border-color: var(--ai-accent);
}
.advanced-row.on {
  border-color: var(--ai-accent);
  background: var(--ai-accent-soft);
}
.ad-icon {
  color: var(--ai-accent-ink);
  font-size: 18px;
}
.advanced-row.on .ad-icon {
  color: var(--ai-accent-ink);
}
.ad-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.ad-text strong {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
}
.ad-text span {
  font-size: 11px;
  color: var(--ai-ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.advanced-detail {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  background: var(--ai-accent-soft);
}

.cta-bar {
  margin-top: auto;
  padding: 12px 16px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 80px;
  gap: 8px;
  border-top: 1px solid var(--ai-border);
  background: var(--ai-surface);
}
.cta {
  height: 42px;
  font-weight: 500;
}
.reset {
  height: 42px;
}

/* modals */
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

@media (max-width: 1100px) {
  .imagegen-form {
    min-height: 0;
  }
  .studio {
    flex: none;
    min-height: 0;
    height: auto;
    grid-template-columns: 1fr;
  }
  .result-stage,
  .control-panel {
    overflow: visible;
  }
  .control-scroll {
    overflow-y: visible;
    flex: none;
  }
  .stage-body {
    min-height: clamp(320px, 60vw, 540px);
  }
  .stage-body img.result-image {
    max-height: 70vh;
  }
}

@media (max-width: 720px) {
  .result-stage {
    min-height: 0;
  }
  .stage-body {
    min-height: min(420px, 58dvh);
    padding: 10px;
  }
  .template-grid {
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  }
  .field-row label {
    width: 100%;
    margin-bottom: 4px;
  }
  .field-hint {
    margin-left: 0;
  }
  .stage-toolbar {
    padding: 8px 10px;
  }
  .stage-toolbar .json-shortcut {
    margin-left: 0;
  }
  .history-strip {
    padding: 8px 10px 10px;
  }
  .upload-zone.has {
    max-height: 42dvh;
    overflow-y: auto;
  }
}
</style>
