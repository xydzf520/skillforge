<template>
  <transition name="slide-up">
    <div v-if="visible" class="undo-toast">
      <span class="toast-msg">{{ message }}</span>
      <a-button v-if="canUndo" type="text" size="small" class="undo-btn" @click="handleUndo">撤销</a-button>
      <a-button type="text" size="small" class="close-btn" @click="dismiss">
        <icon-close />
      </a-button>
      <div class="countdown-bar" :style="{ width: progress + '%' }"></div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { IconClose } from '@arco-design/web-vue/es/icon'

const visible = ref(false)
const message = ref('')
const canUndo = ref(false)
const progress = ref(100)
let _timer: ReturnType<typeof setTimeout> | null = null
let _countTimer: ReturnType<typeof setInterval> | null = null
let _undoFn: (() => void) | null = null

function show(msg: string, options: { onUndo?: () => void; onConfirm?: () => void; duration?: number } = {}) {
  dismiss()
  message.value = msg
  canUndo.value = !!options.onUndo
  _undoFn = options.onUndo || null
  progress.value = 100
  visible.value = true

  const duration = options.duration || 5000
  const step = 50
  let elapsed = 0
  _countTimer = setInterval(() => {
    elapsed += step
    progress.value = Math.max(0, 100 - (elapsed / duration) * 100)
  }, step)

  _timer = setTimeout(() => {
    dismiss()
    if (options.onConfirm) options.onConfirm()
  }, duration)
}

function handleUndo() {
  if (_undoFn) _undoFn()
  dismiss()
}

function dismiss() {
  visible.value = false
  if (_timer) clearTimeout(_timer)
  if (_countTimer) clearInterval(_countTimer)
  _timer = null
  _countTimer = null
  _undoFn = null
}

defineExpose({ show, dismiss })
</script>

<style scoped>
.undo-toast {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  padding: 10px 16px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 12px;
  box-shadow: var(--ai-shadow-2);
  z-index: 9999;
  min-width: 280px;
  overflow: hidden;
}
.toast-msg { flex: 1; font-size: 14px; }
.undo-btn { color: var(--ai-info) !important; font-weight: 600; }
.close-btn { color: var(--ai-ink-4) !important; }
.countdown-bar {
  position: absolute;
  bottom: 0;
  left: 0;
  height: 2px;
  background: var(--ai-info);
  transition: width 50ms linear;
}
.slide-up-enter-active, .slide-up-leave-active { transition: all 0.3s ease; }
.slide-up-enter-from, .slide-up-leave-to { opacity: 0; transform: translateX(-50%) translateY(20px); }
</style>
