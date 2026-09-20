<template>
  <div class="gpt-image-result-gallery">
    <section class="result-summary">
      <div class="summary-main">
        <a-tag :color="statusMeta.color" size="small">{{ statusMeta.label }}</a-tag>
        <span class="summary-item">
          <span class="summary-label">任务 ID</span>
          <span class="summary-value">{{ taskId || '-' }}</span>
        </span>
        <span class="summary-item">
          <span class="summary-label">图片</span>
          <span class="summary-value">{{ images.length }}</span>
        </span>
        <span class="summary-item">
          <span class="summary-label">文件</span>
          <span class="summary-value">{{ files.length }}</span>
        </span>
      </div>
      <div class="summary-progress">
        <span class="progress-label">进度</span>
        <a-progress
          class="progress-bar"
          size="small"
          :percent="progressRatio"
          :show-text="false"
          :color="progressColor"
        />
        <span class="progress-text">{{ progressText }}</span>
      </div>
    </section>

    <section class="preview-panel">
      <div class="section-head">
        <div class="section-title">
          <icon-eye :size="15" />
          <span>预览</span>
        </div>
        <a-radio-group v-model="previewMode" type="button" size="mini">
          <a-radio value="main">主图</a-radio>
          <a-radio value="banner">Banner</a-radio>
          <a-radio value="detail">详情页</a-radio>
        </a-radio-group>
      </div>

      <div class="preview-stage" :class="previewModeClass">
        <template v-if="previewImage">
          <div v-if="previewMode === 'main'" class="commerce-preview main-preview">
            <img :src="previewImage.url" :alt="previewImage.name" />
            <div class="main-corner">1:1</div>
            <div class="main-caption">
              <strong>{{ previewImage.name }}</strong>
              <span>{{ imageSpec(previewImage) }}</span>
            </div>
          </div>

          <div v-else-if="previewMode === 'banner'" class="commerce-preview banner-preview">
            <img :src="previewImage.url" :alt="previewImage.name" />
            <div class="banner-copy">
              <span>Campaign Visual</span>
              <strong>{{ previewImage.name }}</strong>
            </div>
          </div>

          <div v-else class="commerce-preview detail-preview">
            <div class="detail-top">
              <img :src="previewImage.url" :alt="previewImage.name" />
            </div>
            <div class="detail-blocks">
              <div class="detail-line strong"></div>
              <div class="detail-line"></div>
              <div class="detail-line short"></div>
              <div class="detail-grid">
                <span></span>
                <span></span>
                <span></span>
              </div>
            </div>
          </div>
        </template>
        <a-empty v-else description="暂无图片" />
      </div>
    </section>

    <section class="gallery-panel">
      <div class="section-head">
        <div class="section-title">
          <icon-image :size="15" />
          <span>图片网格</span>
          <a-tag size="small" color="arcoblue">{{ images.length }}</a-tag>
        </div>
        <span v-if="previewImage" class="active-name">{{ previewImage.name }}</span>
      </div>

      <div v-if="images.length" class="image-grid">
        <article
          v-for="image in images"
          :key="image.id"
          class="image-tile"
          :class="{ active: image.id === selectedImageId }"
          @click="selectImage(image)"
        >
          <div class="image-frame">
            <img :src="image.url" :alt="image.name" loading="lazy" />
            <div class="image-index">#{{ image.index + 1 }}</div>
          </div>
          <div class="image-meta">
            <span class="image-name" :title="image.name">{{ image.name }}</span>
            <span class="image-spec">{{ imageSpec(image) }}</span>
          </div>
          <div class="image-actions">
            <a-tooltip content="打开原图">
              <a-button
                size="mini"
                type="text"
                :aria-label="`打开原图 ${image.name}`"
                @click.stop="openUrl(image.url)"
              >
                <icon-launch />
              </a-button>
            </a-tooltip>
            <a-tooltip content="复制 URL">
              <a-button
                size="mini"
                type="text"
                :aria-label="`复制 URL ${image.name}`"
                @click.stop="copyUrl(image.url)"
              >
                <icon-copy />
              </a-button>
            </a-tooltip>
            <a-tooltip content="用作参考图">
              <a-button
                size="mini"
                type="text"
                :aria-label="`用作参考图 ${image.name}`"
                @click.stop="useAsReference(image)"
              >
                <icon-link />
              </a-button>
            </a-tooltip>
            <a-tooltip content="下载">
              <a-button
                size="mini"
                type="text"
                :href="image.url"
                :download="downloadName(image)"
                target="_blank"
                rel="noreferrer"
                :aria-label="`下载 ${image.name}`"
                @click.stop
              >
                <icon-download />
              </a-button>
            </a-tooltip>
          </div>
        </article>
      </div>
      <a-empty v-else description="暂无图片" />
    </section>

    <section class="file-panel">
      <div class="section-head">
        <div class="section-title">
          <icon-file :size="15" />
          <span>文件列表</span>
          <a-tag size="small">{{ files.length }}</a-tag>
        </div>
      </div>

      <div v-if="files.length" class="file-table">
        <div class="file-row file-head">
          <span>文件</span>
          <span>类型</span>
          <span>大小</span>
          <span></span>
        </div>
        <div v-for="file in files" :key="file.id" class="file-row">
          <span class="file-name" :title="file.url || file.name">
            <icon-file-image v-if="file.kind === 'image'" />
            <icon-file v-else />
            {{ file.name }}
          </span>
          <span>{{ file.type || file.kind || '-' }}</span>
          <span>{{ formatBytes(file.size) }}</span>
          <span class="file-actions">
            <a-tooltip v-if="file.url" content="打开">
              <a-button size="mini" type="text" @click.stop="openUrl(file.url)">
                <icon-launch />
              </a-button>
            </a-tooltip>
            <a-tooltip v-if="file.url" content="复制 URL">
              <a-button size="mini" type="text" @click.stop="copyUrl(file.url)">
                <icon-copy />
              </a-button>
            </a-tooltip>
            <a-tooltip v-if="file.url" content="下载">
              <a-button
                size="mini"
                type="text"
                :href="file.url"
                :download="file.name"
                target="_blank"
                rel="noreferrer"
                @click.stop
              >
                <icon-download />
              </a-button>
            </a-tooltip>
          </span>
        </div>
      </div>
      <a-empty v-else description="暂无文件" />
    </section>

  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  IconCopy,
  IconDownload,
  IconEye,
  IconFile,
  IconFileImage,
  IconImage,
  IconLaunch,
  IconLink,
} from '@arco-design/web-vue/es/icon'
import { copyText } from '@/utils/clipboard'

defineOptions({ name: 'GptImageResultGallery' })

type AnyRecord = Record<string, unknown>
type PreviewMode = 'main' | 'banner' | 'detail'

interface GalleryImage {
  id: string
  index: number
  url: string
  name: string
  type?: string
  width?: number
  height?: number
  source?: unknown
}

interface GalleryFile {
  id: string
  name: string
  kind?: string
  type?: string
  size?: number
  url?: string
  source?: unknown
}

const props = defineProps<{
  result?: AnyRecord | null
}>()

const emit = defineEmits<{
  (e: 'use-reference', url: string, image: GalleryImage): void
}>()

const previewMode = ref<PreviewMode>('main')
const selectedImageId = ref('')

const rawResult = computed(() => props.result || {})
const resultObject = computed<AnyRecord>(() => {
  if (isPlainObject(rawResult.value)) return rawResult.value
  return { value: rawResult.value }
})

const status = computed(() => {
  return normalizeText(
    pickValue(resultObject.value, [
      'status',
      'state',
      'task_status',
      'taskStatus',
      'phase',
      'data.status',
      'output.status',
      'result.status',
    ]),
  ) || 'unknown'
})

const statusMeta = computed(() => statusInfo(status.value))

const taskId = computed(() => {
  return normalizeText(
    pickValue(resultObject.value, [
      'task_id',
      'taskId',
      'job_id',
      'jobId',
      'run_id',
      'runId',
      'execution_id',
      'executionId',
      'id',
      'data.task_id',
      'output.task_id',
      'result.task_id',
    ]),
  )
})

const progressPercent = computed(() => {
  const raw = pickValue(resultObject.value, [
    'progress',
    'percent',
    'percentage',
    'data.progress',
    'output.progress',
    'result.progress',
  ])
  const parsed = normalizeProgress(raw)
  if (parsed !== null) return parsed
  if (['success', 'succeeded', 'completed', 'complete', 'done'].includes(status.value.toLowerCase())) return 100
  return 0
})

const progressRatio = computed(() => progressPercent.value / 100)
const progressText = computed(() => {
  const raw = pickValue(resultObject.value, [
    'progress',
    'percent',
    'percentage',
    'data.progress',
    'output.progress',
    'result.progress',
  ])
  const parsed = normalizeProgress(raw)
  if (parsed === null && progressPercent.value === 0) return '-'
  return `${Math.round(progressPercent.value)}%`
})
const progressColor = computed(() => statusMeta.value.color === 'red' ? 'var(--ai-bad)' : 'var(--ai-accent-ink)')

const images = computed<GalleryImage[]>(() => normalizeImages(resultObject.value))
const files = computed<GalleryFile[]>(() => normalizeFiles(resultObject.value, images.value))

const previewImage = computed(() => {
  return images.value.find(image => image.id === selectedImageId.value) || images.value[0] || null
})
const previewModeClass = computed(() => `preview-${previewMode.value}`)
watch(
  images,
  (list) => {
    if (!list.length) {
      selectedImageId.value = ''
      return
    }
    if (!list.some(image => image.id === selectedImageId.value)) {
      selectedImageId.value = list[0].id
    }
  },
  { immediate: true },
)

function selectImage(image: GalleryImage) {
  selectedImageId.value = image.id
}

function openUrl(url?: string) {
  if (!url) return
  window.open(url, '_blank', 'noopener,noreferrer')
}

async function copyUrl(url?: string) {
  if (!url) return
  const ok = await copyText(url)
  if (ok) Message.success('已复制 URL')
  else Message.warning('复制失败，请手动复制')
}

function useAsReference(image: GalleryImage) {
  emit('use-reference', image.url, image)
  Message.success('已发送为参考图')
}

function statusInfo(value: string): { label: string; color: string } {
  const key = value.toLowerCase()
  const map: Record<string, { label: string; color: string }> = {
    success: { label: '成功', color: 'green' },
    succeeded: { label: '成功', color: 'green' },
    completed: { label: '完成', color: 'green' },
    complete: { label: '完成', color: 'green' },
    done: { label: '完成', color: 'green' },
    running: { label: '运行中', color: 'arcoblue' },
    processing: { label: '处理中', color: 'arcoblue' },
    in_progress: { label: '处理中', color: 'arcoblue' },
    pending: { label: '等待中', color: 'orange' },
    queued: { label: '排队中', color: 'orange' },
    failed: { label: '失败', color: 'red' },
    error: { label: '错误', color: 'red' },
    canceled: { label: '已取消', color: 'gray' },
    cancelled: { label: '已取消', color: 'gray' },
    unknown: { label: 'unknown', color: 'gray' },
  }
  return map[key] || { label: value, color: 'gray' }
}

function normalizeImages(root: AnyRecord): GalleryImage[] {
  const candidates: GalleryImage[] = []
  const seen = new WeakSet<object>()
  collectImages(root, 'result', seen, candidates)

  const unique = new Map<string, GalleryImage>()
  candidates.forEach((image) => {
    if (!image.url || unique.has(image.url)) return
    unique.set(image.url, {
      ...image,
      id: `image-${unique.size}`,
      index: unique.size,
      name: image.name || `生成图 ${unique.size + 1}`,
    })
  })
  return Array.from(unique.values())
}

function collectImages(value: unknown, path: string, seen: WeakSet<object>, out: GalleryImage[]) {
  if (typeof value === 'string') {
    if (isOutputImagePath(path) && isUsableUrl(value)) {
      out.push(createImage(value, path, out.length))
    }
    return
  }

  if (Array.isArray(value)) {
    value.forEach((item, index) => collectImages(item, `${path}.${index}`, seen, out))
    return
  }

  if (!isPlainObject(value)) return
  if (seen.has(value)) return
  seen.add(value)

  const url = extractImageUrl(value, path)
  if (url) {
    out.push(createImage(url, path, out.length, value))
  }

  Object.entries(value).forEach(([key, child]) => {
    const nextPath = `${path}.${key}`
    if (shouldSkipImagePath(nextPath)) return
    if (isImageContainerKey(key) || Array.isArray(child) || isPlainObject(child)) {
      collectImages(child, nextPath, seen, out)
    }
  })
}

function createImage(url: string, path: string, index: number, source?: unknown): GalleryImage {
  const record = isPlainObject(source) ? source : {}
  const width = normalizeNumber(record.width || record.w)
  const height = normalizeNumber(record.height || record.h)
  const type = normalizeText(record.type || record.mime_type || record.mimeType || record.content_type)
  return {
    id: `image-${index}`,
    index,
    url,
    name: normalizeText(record.name || record.filename || record.file_name || record.title) || fileNameFromUrl(url, `生成图 ${index + 1}`),
    type,
    width,
    height,
    source,
  }
}

function extractImageUrl(record: AnyRecord, path: string): string {
  const b64 = normalizeText(record.b64_json || record.base64 || record.image_base64)
  if (b64) return b64.startsWith('data:image/') ? b64 : `data:image/png;base64,${b64}`

  const url = normalizeText(
    record.url
    || record.image_url
    || record.imageUrl
    || record.src
    || record.href
    || record.download_url
    || record.downloadUrl
    || record.original_url
    || record.originalUrl,
  )
  if (!url) return ''
  if (isLikelyImageUrl(url) || isOutputImagePath(path) || hasImageMeta(record)) return url
  return ''
}

function normalizeFiles(root: AnyRecord, imageList: GalleryImage[]): GalleryFile[] {
  const explicit: GalleryFile[] = []
  collectFileValues(root).forEach((value) => {
    explicit.push(...normalizeFileValue(value, explicit.length))
  })

  imageList.forEach((image) => {
    explicit.push({
      id: `file-image-${image.index}`,
      name: downloadName(image),
      kind: 'image',
      type: image.type || 'image',
      url: image.url,
      source: image.source,
    })
  })

  const unique = new Map<string, GalleryFile>()
  explicit.forEach((file) => {
    const key = file.url || file.name
    if (!key || unique.has(key)) return
    unique.set(key, { ...file, id: `file-${unique.size}` })
  })
  return Array.from(unique.values())
}

function collectFileValues(root: AnyRecord): unknown[] {
  const values: unknown[] = []
  const keys = new Set(['files', 'file_list', 'fileList', 'artifacts', 'attachments'])
  const seen = new WeakSet<object>()

  function visit(value: unknown) {
    if (!isPlainObject(value) && !Array.isArray(value)) return
    if (seen.has(value)) return
    seen.add(value)

    if (Array.isArray(value)) {
      value.forEach(visit)
      return
    }

    Object.entries(value).forEach(([key, child]) => {
      if (keys.has(key)) values.push(child)
      if (['data', 'output', 'outputs', 'result', 'response'].includes(key) || isPlainObject(child)) {
        visit(child)
      }
    })
  }

  visit(root)
  return values
}

function normalizeFileValue(value: unknown, startIndex: number): GalleryFile[] {
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => normalizeFileValue(item, startIndex + index))
  }

  if (typeof value === 'string') {
    return [{
      id: `file-${startIndex}`,
      name: fileNameFromUrl(value, `文件 ${startIndex + 1}`),
      kind: isLikelyImageUrl(value) ? 'image' : 'file',
      url: isUsableUrl(value) ? value : undefined,
    }]
  }

  if (!isPlainObject(value)) return []

  const url = normalizeText(value.url || value.download_url || value.downloadUrl || value.href || value.path)
  const name = normalizeText(value.name || value.filename || value.file_name || value.title)
    || (url ? fileNameFromUrl(url, `文件 ${startIndex + 1}`) : `文件 ${startIndex + 1}`)
  return [{
    id: `file-${startIndex}`,
    name,
    kind: isLikelyImageUrl(url) ? 'image' : normalizeText(value.kind) || 'file',
    type: normalizeText(value.type || value.mime_type || value.mimeType || value.content_type),
    size: normalizeNumber(value.size || value.bytes || value.file_size),
    url: isUsableUrl(url) ? url : undefined,
    source: value,
  }]
}

function pickValue(source: AnyRecord, paths: string[]): unknown {
  for (const path of paths) {
    const value = path.split('.').reduce<unknown>((current, key) => {
      if (!isPlainObject(current)) return undefined
      return current[key]
    }, source)
    if (value !== undefined && value !== null && value !== '') return value
  }
  return undefined
}

function normalizeText(value: unknown): string {
  if (typeof value === 'string') return value.trim()
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return ''
}

function normalizeNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return undefined
}

function normalizeProgress(value: unknown): number | null {
  const raw = normalizeNumber(typeof value === 'string' ? value.replace('%', '') : value)
  if (raw === undefined) return null
  const percent = raw <= 1 ? raw * 100 : raw
  return Math.max(0, Math.min(100, percent))
}

function imageSpec(image: GalleryImage): string {
  if (image.width && image.height) return `${image.width} x ${image.height}`
  return image.type || 'image'
}

function downloadName(image: GalleryImage): string {
  const name = fileNameFromUrl(image.url, image.name || `gpt-image-${image.index + 1}.png`)
  if (/\.[a-z0-9]{2,5}$/i.test(name)) return name
  return `${name}.png`
}

function fileNameFromUrl(url: string, fallback: string): string {
  if (!url) return fallback
  if (url.startsWith('data:image/')) return fallback
  try {
    const parsed = new URL(url, window.location.origin)
    const name = decodeURIComponent(parsed.pathname.split('/').filter(Boolean).pop() || '')
    return name || fallback
  } catch {
    const name = url.split('?')[0].split('#')[0].split('/').filter(Boolean).pop()
    return name || fallback
  }
}

function formatBytes(size?: number): string {
  if (!size || size <= 0) return '-'
  if (size < 1024) return `${size} B`
  const kb = size / 1024
  if (kb < 1024) return `${kb.toFixed(kb >= 100 ? 0 : 1)} KB`
  const mb = kb / 1024
  return `${mb.toFixed(mb >= 100 ? 0 : 1)} MB`
}

function isPlainObject(value: unknown): value is AnyRecord {
  return Object.prototype.toString.call(value) === '[object Object]'
}

function isUsableUrl(value: string): boolean {
  return /^(https?:\/\/|blob:|data:image\/|\/)/i.test(value)
}

function isLikelyImageUrl(value: string): boolean {
  return /^data:image\//i.test(value) || /\.(png|jpe?g|webp|gif|avif|bmp|svg)(\?|#|$)/i.test(value)
}

function isImageContainerKey(key: string): boolean {
  return /(image|images|img|thumbnail|thumb|gallery|asset|artifact|output|url)/i.test(key)
}

function shouldSkipImagePath(path: string): boolean {
  return /(reference|input|request|param|prompt|mask|source_image)/i.test(path)
}

function isOutputImagePath(path: string): boolean {
  return isImageContainerKey(path) && !shouldSkipImagePath(path)
}

function hasImageMeta(record: AnyRecord): boolean {
  const type = normalizeText(record.type || record.mime_type || record.mimeType || record.content_type)
  return /^image\//i.test(type) || type.toLowerCase() === 'image'
}

</script>

<style scoped>
.gpt-image-result-gallery {
  display: flex;
  flex-direction: column;
  gap: 12px;
  color: var(--ai-ink-1);
}

.result-summary,
.preview-panel,
.gallery-panel,
.file-panel {
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
}

.result-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 12px;
}

.summary-main,
.summary-progress,
.summary-item,
.section-head,
.section-title,
.image-actions,
.file-actions {
  display: flex;
  align-items: center;
}

.summary-main {
  flex-wrap: wrap;
  gap: 8px 12px;
  min-width: 0;
}

.summary-item {
  min-width: 0;
  gap: 6px;
  font-size: 12px;
}

.summary-label,
.progress-label,
.file-head {
  color: var(--ai-ink-3);
}

.summary-value {
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  color: var(--ai-ink-1);
}

.summary-progress {
  flex: 0 0 240px;
  gap: 8px;
  font-size: 12px;
}

.progress-bar {
  flex: 1;
}

.progress-text {
  width: 40px;
  text-align: right;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  color: var(--ai-ink-2);
}

.preview-panel,
.gallery-panel,
.file-panel {
  padding: 12px;
}

.section-head {
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}

.section-title {
  gap: 6px;
  min-width: 0;
  font-size: 13px;
  font-weight: 700;
}

.active-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--ai-ink-3);
}

.preview-stage {
  min-height: 260px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: linear-gradient(135deg, var(--ai-surface-2), var(--ai-surface-2));
  display: grid;
  place-items: center;
  padding: 16px;
  overflow: hidden;
}

.commerce-preview {
  position: relative;
  overflow: hidden;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  box-shadow: 0 6px 18px rgba(29, 33, 41, 0.08);
}

.commerce-preview img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.main-preview {
  width: min(360px, 100%);
  aspect-ratio: 1 / 1;
  padding: 18px;
}

.main-corner {
  position: absolute;
  top: 10px;
  right: 10px;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(29, 33, 41, 0.72);
  color: #fff;
  font-size: 11px;
}

.main-caption {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 8px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.92);
  font-size: 12px;
}

.main-caption strong,
.banner-copy strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.main-caption span {
  flex-shrink: 0;
  color: var(--ai-ink-3);
}

.banner-preview {
  width: min(760px, 100%);
  aspect-ratio: 16 / 5;
}

.banner-preview img {
  object-fit: cover;
}

.banner-copy {
  position: absolute;
  left: 24px;
  top: 50%;
  max-width: min(360px, 55%);
  transform: translateY(-50%);
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
  border-radius: 8px;
  background: rgba(29, 33, 41, 0.68);
  color: #fff;
}

.banner-copy span {
  font-size: 11px;
  opacity: 0.78;
}

.banner-copy strong {
  font-size: 18px;
}

.detail-preview {
  width: min(340px, 100%);
  max-height: 520px;
  display: flex;
  flex-direction: column;
}

.detail-top {
  aspect-ratio: 4 / 3;
  padding: 12px;
  background: var(--ai-surface-2);
}

.detail-top img {
  object-fit: contain;
}

.detail-blocks {
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.detail-line {
  height: 8px;
  border-radius: 4px;
  background: var(--ai-surface-2);
}

.detail-line.strong {
  width: 72%;
  height: 14px;
  background: var(--ai-accent-ink);
}

.detail-line.short {
  width: 48%;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin-top: 4px;
}

.detail-grid span {
  aspect-ratio: 1;
  border-radius: 6px;
  background: var(--ai-surface-2);
}

.image-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(168px, 1fr));
  gap: 10px;
}

.image-tile {
  min-width: 0;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  overflow: hidden;
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.image-tile:hover,
.image-tile.active {
  border-color: var(--ai-accent);
  box-shadow: 0 4px 14px rgba(22, 93, 255, 0.12);
}

.image-frame {
  position: relative;
  aspect-ratio: 1 / 1;
  background: var(--ai-surface-2);
}

.image-frame img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}

.image-index {
  position: absolute;
  top: 6px;
  left: 6px;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(29, 33, 41, 0.68);
  color: #fff;
  font-size: 11px;
}

.image-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 8px 4px;
}

.image-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 600;
}

.image-spec {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--ai-ink-3);
}

.image-actions {
  justify-content: flex-end;
  gap: 2px;
  padding: 0 6px 6px;
}

.image-actions :deep(.arco-btn),
.file-actions :deep(.arco-btn) {
  width: 24px;
  height: 24px;
  padding: 0;
  color: var(--ai-ink-2);
}

.file-table {
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  overflow: hidden;
}

.file-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 96px 82px 92px;
  align-items: center;
  gap: 8px;
  min-height: 34px;
  padding: 6px 10px;
  border-top: 1px solid var(--ai-border);
  font-size: 12px;
}

.file-row:first-child {
  border-top: 0;
}

.file-head {
  min-height: 30px;
  background: var(--ai-surface-2);
  font-weight: 600;
}

.file-name {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-actions {
  justify-content: flex-end;
  gap: 2px;
}

@media (max-width: 720px) {
  .result-summary {
    align-items: stretch;
    flex-direction: column;
  }

  .summary-progress {
    flex: none;
    width: 100%;
  }

  .section-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .banner-preview {
    aspect-ratio: 4 / 3;
  }

  .banner-copy {
    left: 12px;
    right: 12px;
    max-width: none;
  }

  .file-row {
    grid-template-columns: minmax(0, 1fr) 72px 64px 84px;
  }
}
</style>
