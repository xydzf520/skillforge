<template>
  <div v-if="store.hasTasks" class="task-dock" :class="{ open: store.dockOpen }">
    <!-- 折叠态：小圆徽章 -->
    <button
      v-if="!store.dockOpen"
      class="dock-pill"
      :class="pillStatusClass"
      @click="store.toggleDock()"
      :title="pillTooltip"
    >
      <a-spin v-if="store.runningCount > 0" :size="14" />
      <icon-check-circle-fill v-else-if="allSuccess" class="pill-icon-success" />
      <icon-close-circle-fill v-else-if="hasError" class="pill-icon-error" />
      <icon-check-circle-fill v-else class="pill-icon-neutral" />
      <span class="pill-count">{{ pillCount }}</span>
    </button>

    <!-- 展开态：完整面板 -->
    <div v-else class="dock-panel">
      <div class="dock-header">
        <div class="dock-title">
          <a-spin v-if="store.runningCount > 0" :size="14" />
          <icon-check-circle-fill v-else class="dock-icon-done" />
          <span v-if="store.runningCount > 0">运行中 · {{ store.runningCount }}</span>
          <span v-else>已完成 · {{ store.tasks.length }}</span>
        </div>
        <div class="dock-actions">
          <a-tooltip content="清除已完成" position="top" v-if="finishedCount > 0">
            <button class="dock-icon-btn" @click.stop="store.clearFinished()">
              <icon-delete />
            </button>
          </a-tooltip>
          <button class="dock-icon-btn" @click.stop="store.toggleDock()" title="折叠">
            <icon-down />
          </button>
        </div>
      </div>

      <div class="dock-body">
        <div
          v-for="task in store.tasks"
          :key="task.id"
          class="dock-task"
          :class="[`status-${task.status}`]"
          @click="jumpTo(task)"
        >
          <div class="task-icon">
            <a-spin v-if="task.status === 'running'" :size="16" />
            <icon-check-circle-fill v-else-if="task.status === 'success'" class="icon-success" />
            <icon-close-circle-fill v-else-if="task.status === 'error'" class="icon-error" />
            <icon-exclamation-circle-fill v-else class="icon-blocked" />
          </div>
          <div class="task-body">
            <div class="task-title">
              <span class="task-kind">{{ kindLabel[task.kind] }}</span>
              <span class="task-name">{{ task.skillName || task.title }}</span>
            </div>
            <div class="task-meta">
              <span v-if="task.status === 'running'">{{ elapsed(task) }}{{ task.message ? ' · ' + task.message : '' }}</span>
              <span v-else-if="task.message" class="task-message">{{ task.message }}</span>
              <span v-else-if="task.status === 'success'">已完成 · {{ elapsed(task) }}</span>
            </div>
          </div>
          <button class="task-dismiss" @click.stop="store.dismiss(task.id)" v-if="task.status !== 'running'">
            <icon-close />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useRunningTasksStore, type RunningTask } from '@/stores/runningTasks'
import {
  IconCheckCircleFill, IconCloseCircleFill, IconExclamationCircleFill,
  IconClose, IconDown, IconDelete,
} from '@arco-design/web-vue/es/icon'

const store = useRunningTasksStore()
const router = useRouter()

const kindLabel: Record<string, string> = {
  sandbox: '沙箱执行',
  test: '测试',
  execute: '执行',
  creation: '新建 Skill',
  validate: '验证',
  chat: 'AI 对话',
}

const finishedCount = computed(() => store.tasks.filter(t => t.status !== 'running').length)
const allSuccess = computed(() => store.tasks.every(t => t.status === 'success'))
const hasError = computed(() => store.tasks.some(t => t.status === 'error' || t.status === 'blocked'))

const pillCount = computed(() => store.runningCount > 0 ? store.runningCount : store.tasks.length)
const pillStatusClass = computed(() => {
  if (store.runningCount > 0) return 'pill-running'
  if (hasError.value) return 'pill-error'
  return 'pill-success'
})
const pillTooltip = computed(() => {
  if (store.runningCount > 0) return `运行中 ${store.runningCount} 个任务`
  if (hasError.value) return `已完成，存在错误`
  return `已完成 ${store.tasks.length} 个任务`
})

const now = ref(Date.now())
let timer: number | null = null

function stopElapsedTimer() {
  if (timer) {
    window.clearInterval(timer)
    timer = null
  }
}

watch(() => store.runningCount, (count) => {
  if (count <= 0) {
    stopElapsedTimer()
    return
  }
  now.value = Date.now()
  if (timer) return
  timer = window.setInterval(() => { now.value = Date.now() }, 1000)
}, { immediate: true })

onUnmounted(() => {
  stopElapsedTimer()
})

function elapsed(task: RunningTask) {
  const end = task.finishedAt ?? now.value
  const secs = Math.max(0, Math.floor((end - task.startedAt) / 1000))
  if (secs < 60) return `${secs}s`
  const m = Math.floor(secs / 60)
  const s = secs % 60
  return `${m}m${s}s`
}

function jumpTo(task: RunningTask) {
  if (task.status !== 'running' && task.runId) {
    router.push(`/execution/${task.runId}`)
    return
  }
  if (task.returnPath) router.push(task.returnPath)
}
</script>

<style scoped>
.task-dock {
  position: fixed;
  left: 20px;
  bottom: 20px;
  z-index: 100;
}

/* ─── 折叠态：紧凑圆形徽章 ─── */
.dock-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 36px;
  min-width: 36px;
  padding: 0 12px;
  border: 1px solid var(--ai-border);
  border-radius: 18px;
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  box-shadow: var(--ai-shadow-2);
  transition: transform 0.15s, box-shadow 0.15s;
}
.dock-pill:hover {
  transform: translateY(-1px);
  box-shadow: var(--ai-shadow-2);
}
.dock-pill.pill-running {
  border-color: var(--ai-info);
  background: var(--ai-surface-2);
}
.dock-pill.pill-error {
  border-color: var(--ai-bad);
}
.dock-pill.pill-success {
  border-color: var(--ai-ok);
}
.pill-icon-success { color: var(--ai-ok); font-size: 15px; }
.pill-icon-error { color: var(--ai-bad); font-size: 15px; }
.pill-icon-neutral { color: var(--ai-ink-3); font-size: 15px; }
.pill-count {
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.02em;
}

/* ─── 展开态：完整面板 ─── */
.dock-panel {
  width: 320px;
  max-width: calc(100vw - 40px);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: 12px;
  box-shadow: var(--ai-shadow-2);
  overflow: hidden;
}

.dock-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  user-select: none;
  background: var(--ai-surface);
  border-bottom: 1px solid var(--ai-border);
}
.dock-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 700;
  color: var(--ai-ink-1);
}
.dock-icon-done {
  color: var(--ai-ok);
  font-size: 14px;
}
.dock-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
.dock-icon-btn {
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  color: var(--ai-ink-3);
  cursor: pointer;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
}
.dock-icon-btn:hover {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
}

.dock-body {
  max-height: 60vh;
  overflow-y: auto;
  padding: 4px 0;
}
.dock-task {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 14px;
  cursor: pointer;
  transition: background 0.12s;
  border-bottom: 1px solid var(--ai-border);
}
.dock-task:last-child {
  border-bottom: none;
}
.dock-task:hover {
  background: var(--ai-surface-2);
}
.task-icon {
  flex-shrink: 0;
  width: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 2px;
}
.icon-success { color: var(--ai-ok); font-size: 16px; }
.icon-error { color: var(--ai-bad); font-size: 16px; }
.icon-blocked { color: var(--ai-warn); font-size: 16px; }

.task-body {
  flex: 1;
  min-width: 0;
}
.task-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--ai-ink-1);
}
.task-kind {
  font-size: 11px;
  font-weight: 600;
  color: var(--ai-ink-3);
  background: var(--ai-surface-2);
  padding: 1px 6px;
  border-radius: 4px;
  flex-shrink: 0;
}
.task-name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-meta {
  font-size: 11px;
  color: var(--ai-ink-3);
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dock-task.status-error .task-message {
  color: var(--ai-bad);
}
.task-dismiss {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border: none;
  background: transparent;
  color: var(--ai-ink-3);
  cursor: pointer;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  opacity: 0;
  transition: opacity 0.12s, background 0.12s;
}
.dock-task:hover .task-dismiss {
  opacity: 1;
}
.task-dismiss:hover {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
}
</style>
