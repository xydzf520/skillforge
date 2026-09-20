<template>
  <div class="wb-shell" :class="{ fullscreen }">
    <!-- 顶栏 -->
    <header class="wb-topbar">
      <slot name="topbar" />
    </header>

    <!-- 主体 -->
    <div class="wb-body">
      <!-- 左侧导航 -->
      <aside
        v-if="navigatorOpen"
        class="wb-navigator"
        :style="{ width: navigatorWidth + 'px' }"
      >
        <slot name="navigator" />
        <!-- 右边缘拖拽手柄 -->
        <div
          class="wb-col-drag wb-col-drag-right"
          title="拖拽调整宽度"
          @mousedown="startNavigatorResize"
        />
      </aside>

      <!-- 中央编辑区 -->
      <main class="wb-editor">
        <slot name="editor" />
      </main>

      <!-- 右侧 AI 面板 -->
      <aside
        v-if="assistantOpen"
        class="wb-assistant"
        :style="{ width: assistantWidth + 'px' }"
      >
        <!-- 左边缘拖拽手柄 -->
        <div
          class="wb-col-drag wb-col-drag-left"
          title="拖拽调整宽度"
          @mousedown="startAssistantResize"
        />
        <slot name="assistant" />
      </aside>
    </div>

    <!-- 底部面板 -->
    <div
      v-if="bottomOpen"
      class="wb-bottom"
      :class="{ maximized: bottomMaximized }"
      :style="{ height: effectiveBottomHeight + 'px' }"
    >
      <div class="wb-bottom-drag" @mousedown="startBottomResize" />
      <slot name="bottom" />
    </div>

    <!-- 浮层插槽（命令面板、内联提示等） -->
    <slot name="overlay" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'

const props = defineProps({
  navigatorOpen: { type: Boolean, default: true },
  navigatorWidth: { type: Number, default: 220 },
  assistantOpen: { type: Boolean, default: true },
  assistantWidth: { type: Number, default: 380 },
  bottomOpen: { type: Boolean, default: false },
  bottomHeight: { type: Number, default: 250 },
  bottomMaximized: { type: Boolean, default: false },
  fullscreen: { type: Boolean, default: false },
})

const emit = defineEmits([
  'update:bottomHeight',
  'update:navigatorWidth',
  'update:assistantWidth',
])

// 视口高度（响应窗口变化，用于最大化计算）
const vh = ref(typeof window !== 'undefined' ? window.innerHeight : 800)
function onResize() {
  vh.value = window.innerHeight
}
onMounted(() => window.addEventListener('resize', onResize))
onBeforeUnmount(() => window.removeEventListener('resize', onResize))

// 实际底部面板高度：最大化 → 80vh，否则 → 传入的 bottomHeight
const effectiveBottomHeight = computed(() =>
  props.bottomMaximized ? Math.floor(vh.value * 0.8) : props.bottomHeight
)

// 底部面板拖拽调整高度
function startBottomResize(e: MouseEvent) {
  if (props.bottomMaximized) return // 最大化时不允许拖
  e.preventDefault()
  const startY = e.clientY
  const startH = props.bottomHeight
  const ctrl = _registerDragController()
  function onMove(ev: MouseEvent) {
    const delta = startY - ev.clientY
    const newH = Math.max(120, Math.min(700, startH + delta))
    emit('update:bottomHeight', newH)
  }
  function onUp() {
    ctrl.abort()
  }
  document.body.style.cursor = 'ns-resize'
  document.body.style.userSelect = 'none'
  document.addEventListener('mousemove', onMove, { signal: ctrl.signal })
  document.addEventListener('mouseup', onUp, { signal: ctrl.signal })
}

// 左侧导航宽度拖拽（180-400px）
function startNavigatorResize(e: MouseEvent) {
  e.preventDefault()
  e.stopPropagation()
  const startX = e.clientX
  const startW = props.navigatorWidth
  const ctrl = _registerDragController()
  function onMove(ev: MouseEvent) {
    const delta = ev.clientX - startX
    const newW = Math.max(180, Math.min(400, startW + delta))
    emit('update:navigatorWidth', newW)
  }
  function onUp() {
    ctrl.abort()
  }
  document.body.style.cursor = 'ew-resize'
  document.body.style.userSelect = 'none'
  document.addEventListener('mousemove', onMove, { signal: ctrl.signal })
  document.addEventListener('mouseup', onUp, { signal: ctrl.signal })
}

// 右侧 AI 面板宽度拖拽（300-1200px，注意方向：向左拖增大）
function startAssistantResize(e: MouseEvent) {
  e.preventDefault()
  e.stopPropagation()
  const startX = e.clientX
  const startW = props.assistantWidth
  const ctrl = _registerDragController()
  function onMove(ev: MouseEvent) {
    const delta = startX - ev.clientX
    const newW = Math.max(300, Math.min(1200, startW + delta))
    emit('update:assistantWidth', newW)
  }
  function onUp() {
    ctrl.abort()
  }
  document.body.style.cursor = 'ew-resize'
  document.body.style.userSelect = 'none'
  document.addEventListener('mousemove', onMove, { signal: ctrl.signal })
  document.addEventListener('mouseup', onUp, { signal: ctrl.signal })
}

// 所有 drag 用 AbortController 统一管理；组件卸载时 abort 所有仍在监听的，
// 防止 mouseup 未触发（鼠标拖到浏览器外等边缘情况）导致 mousemove 监听永驻 document。
const _dragControllers: AbortController[] = []
function _registerDragController(): AbortController {
  const ctrl = new AbortController()
  ctrl.signal.addEventListener('abort', () => {
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    const idx = _dragControllers.indexOf(ctrl)
    if (idx >= 0) _dragControllers.splice(idx, 1)
  })
  _dragControllers.push(ctrl)
  return ctrl
}
onBeforeUnmount(() => {
  _dragControllers.splice(0).forEach((c) => c.abort())
})
</script>

<style scoped>
.wb-shell {
  display: flex; flex-direction: column;
  height: 100%; overflow: hidden;
  background: var(--ai-bg);
  font-family: var(--ai-font-sans);
}
.wb-shell.fullscreen { position: fixed; inset: 0; z-index: 1000; }

.wb-topbar { flex-shrink: 0; }

.wb-body { flex: 1; display: flex; min-height: 0; }

.wb-navigator {
  flex-shrink: 0; display: flex; flex-direction: column;
  background: var(--ai-surface);
  border-right: 1px solid var(--ai-border);
  overflow: hidden; position: relative;
}

.wb-editor { flex: 1; min-width: 0; overflow: hidden; display: flex; flex-direction: column; background: var(--ai-bg); }

.wb-assistant {
  flex-shrink: 0; display: flex; flex-direction: column;
  background: var(--ai-surface);
  border-left: 1px solid var(--ai-border);
  overflow: hidden; position: relative;
}

/* 左右两侧面板的拖拽手柄：6px 宽隐形条，hover 高亮 */
.wb-col-drag {
  position: absolute; top: 0; bottom: 0; width: 6px;
  cursor: ew-resize; z-index: 15;
  background: transparent; transition: background .15s;
}
.wb-col-drag:hover,
.wb-col-drag:active { background: var(--ai-border-2); }
.wb-col-drag-right { right: -3px; }
.wb-col-drag-left  { left:  -3px; }

.wb-bottom {
  flex-shrink: 0; display: flex; flex-direction: column;
  background: var(--ai-surface);
  border-top: 1px solid var(--ai-border);
  position: relative;
  transition: height .15s;
}
.wb-bottom.maximized { box-shadow: 0 -6px 18px -8px rgba(20, 19, 15, 0.18); }

.wb-bottom-drag {
  position: absolute; top: -3px; left: 0; right: 0; height: 6px;
  cursor: ns-resize; z-index: 10;
}
.wb-bottom.maximized .wb-bottom-drag { cursor: default; }
.wb-bottom-drag:hover { background: var(--ai-border-2); }

/* ── 响应式布局 ── */
@media (max-width: 1439px) {
  .wb-assistant { position: absolute; right: 0; top: 0; bottom: 0; z-index: 20; box-shadow: -4px 0 16px -6px rgba(20, 19, 15, 0.18); }
}
@media (max-width: 1023px) {
  .wb-navigator { width: 50px !important; }
  .wb-navigator .wb-col-drag { display: none; }
  .wb-assistant { position: absolute; right: 0; top: 0; bottom: 0; z-index: 20; box-shadow: -4px 0 16px -6px rgba(20, 19, 15, 0.18); }
}
@media (max-width: 767px) {
  /* v2.9.1 M3：移动端不再全屏拦截，改为折叠布局 —— 三面板变 drawer / 只显示 editor */
  .wb-navigator { position: absolute; left: 0; top: 0; bottom: 0; z-index: 21; box-shadow: 4px 0 16px -6px rgba(20, 19, 15, 0.18); }
  .wb-assistant { position: absolute; right: 0; top: 0; bottom: 0; z-index: 20; box-shadow: -4px 0 16px -6px rgba(20, 19, 15, 0.18); }
  .wb-bottom { max-height: 40vh; }
  .wb-editor { padding: 0 !important; }
  /* 窄屏下额外加一条提示条，告知编辑器体验更适合桌面 */
  .wb-shell::before {
    content: '建议桌面使用获得完整编辑体验';
    position: sticky; top: 0; left: 0; right: 0; z-index: 30;
    display: block; padding: 6px 12px;
    background: var(--ai-warn-soft);
    color: var(--ai-warn);
    font-size: 12px; text-align: center;
  }
}
</style>
