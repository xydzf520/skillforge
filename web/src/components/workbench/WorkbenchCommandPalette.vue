<template>
  <Teleport to="body">
    <div class="cp-overlay" @click.self="$emit('close')">
      <div class="cp-dialog">
        <div class="cp-input-row">
          <icon-search :size="16" class="cp-icon" />
          <input
            ref="inputEl"
            v-model="query"
            class="cp-input"
            placeholder="输入命令..."
            @keydown.escape="$emit('close')"
            @keydown.enter="executeSelected"
            @keydown.up.prevent="moveSelection(-1)"
            @keydown.down.prevent="moveSelection(1)"
          />
        </div>
        <div class="cp-list" v-if="filtered.length">
          <button
            v-for="(cmd, i) in filtered" :key="cmd.id"
            class="cp-item" :class="{ selected: selectedIndex === i, 'ai-item': cmd.group === 'AI' }"
            @click="execute(cmd)"
            @mouseenter="selectedIndex = i"
          >
            <component :is="cmd.icon" :size="14" class="cp-item-icon" />
            <div class="cp-item-text">
              <span class="cp-item-label">{{ cmd.label }}</span>
              <span v-if="cmd.shortcut" class="cp-item-key">{{ cmd.shortcut }}</span>
            </div>
            <span class="cp-item-group">{{ cmd.group }}</span>
          </button>
        </div>
        <div class="cp-empty" v-else-if="query">
          无匹配命令
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, nextTick } from 'vue'
import {
  IconSearch, IconExperiment, IconBug, IconBranch, IconSync, IconThunderbolt,
  IconCheckCircle, IconPlayArrow, IconSun, IconFullscreen,
} from '@arco-design/web-vue/es/icon'

const emit = defineEmits(['close', 'execute'])

const COMMANDS = [
  { id: 'validate', label: '运行验证', icon: IconCheckCircle, group: '工作流', shortcut: '' },
  { id: 'test', label: '运行测试', icon: IconPlayArrow, group: '工作流', shortcut: '' },
  { id: 'sandbox', label: '打开沙箱', icon: IconThunderbolt, group: '工作流', shortcut: '' },
  { id: 'multi-run', label: '批量运行投选', icon: IconThunderbolt, group: '工作流', shortcut: '' },
  { id: 'generate-tests', label: '生成测试用例', icon: IconExperiment, group: 'AI', shortcut: '/generate-tests' },
  { id: 'discover-antipatterns', label: '发现反例', icon: IconBug, group: 'AI', shortcut: '/discover-antipatterns' },
  { id: 'suggest-branches', label: '建议分支', icon: IconBranch, group: 'AI', shortcut: '/suggest-branches' },
  { id: 'drift-check', label: '参数漂移检测', icon: IconSync, group: 'AI', shortcut: '/drift-check' },
  { id: 'run-aiclaw', label: 'AIClaw 执行', icon: IconThunderbolt, group: 'AI', shortcut: '/run-aiclaw' },
  { id: 'derive-thresholds', label: 'AI 推导阈值', icon: IconExperiment, group: 'AI', shortcut: '' },
  { id: 'batch-create', label: '批量创建 Skill', icon: IconThunderbolt, group: '工作流', shortcut: '' },
  { id: 'toggle-mermaid', label: '决策树可视化', icon: IconBranch, group: '可视化', shortcut: '' },
  { id: 'toggle-flow', label: '流程图', icon: IconBranch, group: '可视化', shortcut: '' },
  { id: 'toggle-theme', label: '切换编辑器主题', icon: IconSun, group: '外观', shortcut: '' },
  { id: 'fullscreen', label: '全屏模式', icon: IconFullscreen, group: '外观', shortcut: '' },
]

const query = ref('')
const selectedIndex = ref(0)
const inputEl = ref<HTMLInputElement | null>(null)

const filtered = computed(() => {
  const q = query.value.toLowerCase().trim()
  if (!q) return COMMANDS
  return COMMANDS.filter(cmd =>
    cmd.label.toLowerCase().includes(q) ||
    cmd.id.includes(q) ||
    cmd.group.toLowerCase().includes(q) ||
    (cmd.shortcut && cmd.shortcut.includes(q))
  )
})

function moveSelection(delta: number) {
  const len = filtered.value.length
  if (!len) return
  selectedIndex.value = (selectedIndex.value + delta + len) % len
}

function executeSelected() {
  const cmd = filtered.value[selectedIndex.value]
  if (cmd) execute(cmd)
}

function execute(cmd: any) {
  emit('execute', cmd.id)
  emit('close')
}

onMounted(() => {
  nextTick(() => inputEl.value?.focus())
})
</script>

<style scoped>
.cp-overlay {
  position: fixed; inset: 0; z-index: 2000;
  background: rgba(0, 0, 0, .35);
  display: flex; justify-content: center; padding-top: 15vh;
  animation: cpFadeIn .1s ease;
}
@keyframes cpFadeIn { from { opacity: 0; } to { opacity: 1; } }

.cp-dialog {
  width: 480px; max-height: 400px; background: var(--ai-surface);
  border-radius: 12px; box-shadow: 0 16px 48px rgba(0,0,0,.2);
  display: flex; flex-direction: column; overflow: hidden;
  animation: cpSlideIn .12s ease;
}
@keyframes cpSlideIn { from { opacity: 0; transform: translateY(-8px); } to { opacity: 1; transform: none; } }

.cp-input-row {
  display: flex; align-items: center; gap: 10px;
  padding: 14px 16px; border-bottom: 1px solid var(--ai-border);
}
.cp-icon { color: var(--ai-ink-4); flex-shrink: 0; }
.cp-input {
  flex: 1; border: none; outline: none; background: transparent;
  font-size: 14px; color: var(--ai-ink-1);
}
.cp-input::placeholder { color: var(--ai-ink-4); }

.cp-list { flex: 1; overflow-y: auto; padding: 4px; }
.cp-item {
  display: flex; align-items: center; gap: 10px; width: 100%;
  padding: 8px 12px; border: none; border-radius: 8px;
  background: transparent; color: var(--ai-ink-2); cursor: pointer;
  font-size: 13px; text-align: left; transition: background .08s;
}
.cp-item:hover, .cp-item.selected { background: var(--ai-surface-2); }
.cp-item-icon { color: var(--ai-ink-4); flex-shrink: 0; }
.cp-item[data-group="AI"] .cp-item-icon,
.cp-item.ai-item .cp-item-icon { background: var(--sf-gradient-ai); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.cp-item-text { flex: 1; display: flex; align-items: center; gap: 8px; }
.cp-item-label { font-weight: 500; }
.cp-item-key { font-size: 11px; color: var(--ai-ink-4); font-family: var(--ai-font-mono); }
.cp-item-group { font-size: 11px; color: var(--ai-ink-4); }
.cp-item.ai-item .cp-item-group { background: var(--sf-gradient-ai); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 600; }

.cp-empty { padding: 24px; text-align: center; font-size: 13px; color: var(--ai-ink-4); }
</style>
