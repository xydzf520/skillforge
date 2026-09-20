<template>
  <div class="event-timeline">
    <!-- 折叠/展开 header -->
    <div
      class="timeline-header"
      :class="{ 'is-expanded': !collapsed }"
      @click="toggleCollapsed"
    >
      <div class="header-left">
        <component :is="collapsed ? IconMenuUnfold : IconMenuFold" class="header-icon" />
        <span class="header-label">实时日志 {{ events.length }} 条</span>
      </div>
      <component :is="collapsed ? IconDown : IconUp" class="header-chevron" />
    </div>

    <!-- 展开态事件列表 -->
    <div v-if="!collapsed" ref="scrollerRef" class="timeline-body">
      <!-- 空态 -->
      <div v-if="events.length === 0" class="timeline-empty">等待第一个事件…</div>

      <!-- 事件行 -->
      <div
        v-for="ev in events"
        :key="ev.id"
        class="timeline-event"
        :class="{
          'is-error': ev.kind === 'tool_error',
          'is-milestone': ev.kind === 'milestone',
        }"
      >
        <!-- 左侧彩色竖条 -->
        <span class="event-bar" :style="{ background: barColor(ev.kind) }" />

        <!-- 正文 + 可选详情 -->
        <div class="event-content">
          <span class="event-text">
            <!-- milestone 前缀 -->
            <span v-if="ev.kind === 'milestone'">● </span>
            <!-- tool_error 前缀 -->
            <span v-else-if="ev.kind === 'tool_error'">⚠ </span>
            {{ ev.text }}
            <!-- detail 切换按钮 -->
            <button
              v-if="ev.detail"
              type="button"
              class="event-detail-toggle"
              @click.stop="toggleDetail(ev.id)"
            >
              {{ expanded[ev.id] ? '-详情' : '+详情' }}
            </button>
          </span>
          <!-- 详情内容 -->
          <pre v-if="ev.detail && expanded[ev.id]" class="event-detail">{{ ev.detail }}</pre>
        </div>

        <!-- 右侧时间 -->
        <span class="event-time">{{ fmt(ev.time) }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
// Skill 创建向导 AI 合成阶段的实时事件时间线
// 默认折叠，展开后显示彩色竖条 + 事件文本 + 时间戳
import { nextTick, reactive, ref, watch } from 'vue'
import {
  IconDown,
  IconMenuFold,
  IconMenuUnfold,
  IconUp,
} from '@arco-design/web-vue/es/icon'
import { formatTimeFull } from '@/utils/format'

// 事件类型枚举
export type EventKind =
  | 'milestone'
  | 'tool_call'
  | 'tool_error'
  | 'thinking'
  | 'retry'
  | 'info'
  | 'contract_ready'
  | 'skill_ready'

export interface TimelineEvent {
  id: string | number
  kind: EventKind
  text: string
  detail?: string // 二级可展开详情（比如错误栈）
  time: number // ms 时间戳
}

const props = withDefaults(
  defineProps<{
    events: TimelineEvent[]
    collapsed?: boolean
  }>(),
  {
    collapsed: true,
  },
)

const emit = defineEmits<{
  (e: 'update:collapsed', v: boolean): void
}>()

// 单条事件的 detail 展开状态（内部管理，不暴露）
const expanded = reactive<Record<string | number, boolean>>({})

// 滚动容器 ref
const scrollerRef = ref<HTMLElement | null>(null)

const fmt = (ms: number) => formatTimeFull(ms).split(' ')[1] || '--:--:--'

// 事件 kind → 左侧竖条颜色
const barColor = (kind: EventKind): string => {
  switch (kind) {
    case 'milestone':
      return 'var(--ai-info)'
    case 'tool_call':
      return 'var(--ai-border)'
    case 'tool_error':
      return 'var(--ai-bad)'
    case 'thinking':
      // --purple-6 若项目中未定义则 fallback 到 arcoblue-4
      return 'rgb(var(--purple-6, var(--arcoblue-4)))'
    case 'retry':
      return 'var(--ai-warn)'
    case 'contract_ready':
    case 'skill_ready':
      return 'var(--ai-ok)'
    case 'info':
    default:
      return 'var(--ai-ink-4)'
  }
}

// 切换 header 折叠状态
const toggleCollapsed = () => {
  emit('update:collapsed', !props.collapsed)
}

// 切换单条事件的 detail 展开状态
const toggleDetail = (id: string | number) => {
  expanded[id] = !expanded[id]
}

// 自动滚动：仅当用户已接近底部时，新事件到达后自动滚到底
// 判定 nearBottom：scrollTop + clientHeight >= scrollHeight - 4
// 若用户手动上滚离底部 > 60px，视为阅读态，不自动跟随
const NEAR_BOTTOM_THRESHOLD = 4
const USER_SCROLL_UP_THRESHOLD = 60

const isNearBottom = (el: HTMLElement): boolean => {
  // 距离底部（已滚动到底 = 0）
  const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
  // 在阈值内视作贴底；同时若用户显著上滚（超过 60px）则直接判定为不跟随
  if (distanceFromBottom > USER_SCROLL_UP_THRESHOLD) return false
  return distanceFromBottom <= NEAR_BOTTOM_THRESHOLD
}

// 监听事件数量变化：新事件到达 → 若此前在底部，下一帧滚到底
watch(
  () => props.events.length,
  async () => {
    if (props.collapsed) return
    const el = scrollerRef.value
    if (!el) return
    // 先检测 nearBottom（基于变化前的位置）
    const shouldFollow = isNearBottom(el)
    await nextTick()
    if (shouldFollow && scrollerRef.value) {
      scrollerRef.value.scrollTop = scrollerRef.value.scrollHeight
    }
  },
)

// 折叠态切换到展开态时，默认滚到底
watch(
  () => props.collapsed,
  async (isCollapsed) => {
    if (isCollapsed) return
    await nextTick()
    if (scrollerRef.value) {
      scrollerRef.value.scrollTop = scrollerRef.value.scrollHeight
    }
  },
)
</script>

<style scoped>
.event-timeline {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
}

/* 折叠/展开 header */
.timeline-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 40px;
  padding: 8px 14px;
  background: var(--ai-surface-2);
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s ease;
  user-select: none;
}

.timeline-header:hover {
  background: var(--ai-surface-2);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--ai-ink-2);
}

.header-icon {
  font-size: 14px;
}

.header-label {
  font-size: 12px;
  color: var(--ai-ink-2);
}

.header-chevron {
  font-size: 14px;
  color: var(--ai-ink-3);
}

/* 展开态 timeline 容器 */
.timeline-body {
  max-height: 300px;
  overflow-y: auto;
  padding: 8px 0;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: 8px;
}

/* 空态 */
.timeline-empty {
  font-size: 13px;
  color: var(--ai-ink-4);
  text-align: center;
  padding: 16px 0;
}

/* 单条事件行 */
.timeline-event {
  display: grid;
  grid-template-columns: 3px 1fr auto;
  gap: 10px;
  align-items: start;
  padding: 4px 14px;
  font-size: 12px;
}

/* 左侧竖条 */
.event-bar {
  width: 3px;
  align-self: stretch;
  border-radius: 1px;
}

/* 正文区域 */
.event-content {
  min-width: 0; /* 允许 word-break 生效 */
}

.event-text {
  font-family: var(--ai-font-mono);
  word-break: break-word;
  color: var(--ai-ink-2);
  line-height: 1.5;
}

/* milestone 加粗 */
.timeline-event.is-milestone .event-text {
  font-weight: 600;
}

/* tool_error 整行红色文字 */
.timeline-event.is-error .event-text {
  color: var(--ai-bad);
}

/* +详情 / -详情 按钮 */
.event-detail-toggle {
  margin-left: 6px;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--ai-info);
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
}

.event-detail-toggle:hover {
  text-decoration: underline;
}

/* 详情展开块 */
.event-detail {
  margin-top: 4px;
  margin-bottom: 0;
  padding: 6px 10px;
  background: var(--ai-surface-2);
  border-radius: 4px;
  font-family: var(--ai-font-mono);
  font-size: 11px;
  color: var(--ai-ink-3);
  white-space: pre-wrap;
  word-break: break-word;
}

/* 右侧时间戳 */
.event-time {
  font-size: 11px;
  color: var(--ai-ink-4);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
</style>
