<template>
  <Teleport to="body">
    <div class="ip-overlay" @click.self="$emit('cancel')">
      <div class="ip-bar" :style="barStyle">
        <div class="ip-context" v-if="selectedText">
          <icon-edit :size="13" />
          <span class="ip-selected">{{ truncatedSelection }}</span>
        </div>
        <div class="ip-input-row">
          <icon-robot :size="15" class="ip-icon" />
          <input
            ref="inputEl"
            v-model="prompt"
            class="ip-input"
            placeholder="告诉 AI 你想怎么改..."
            @keydown.enter="handleSubmit"
            @keydown.escape="$emit('cancel')"
          />
          <button class="ip-submit" :class="{ ready: prompt.trim() }" :disabled="!prompt.trim()" @click="handleSubmit">
            <icon-arrow-up :size="14" />
          </button>
        </div>
        <div class="ip-hints">
          <button v-for="h in hints" :key="h" class="ip-hint" @click="prompt = h; handleSubmit()">{{ h }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, nextTick } from 'vue'
import type { PropType } from 'vue'
import { IconEdit, IconRobot, IconArrowUp } from '@arco-design/web-vue/es/icon'

const props = defineProps({
  selectedText: { type: String, default: '' },
  position: { type: Object as PropType<{ top: number; left: number } | null>, default: null }, // { top, left } 相对于视口
  moduleId: { type: String, default: '' },
})

const emit = defineEmits(['submit', 'cancel'])

const prompt = ref('')
const inputEl = ref<HTMLInputElement | null>(null)

const truncatedSelection = computed(() => {
  const t = props.selectedText || ''
  return t.length > 60 ? t.slice(0, 57) + '...' : t
})

const barStyle = computed<any>(() => {
  if (props.position) {
    return {
      position: 'fixed',
      top: `${Math.min(props.position.top, window.innerHeight - 180)}px`,
      left: `${Math.max(props.position.left, 20)}px`,
    }
  }
  // 默认居中偏上
  return { position: 'fixed', top: '25vh', left: '50%', transform: 'translateX(-50%)' }
})

const hints = ['改成更严格', '简化逻辑', '补充边界条件', '用中文重写']

function handleSubmit() {
  const text = prompt.value.trim()
  if (!text) return
  emit('submit', text)
  prompt.value = ''
}

onMounted(() => {
  nextTick(() => inputEl.value?.focus())
})
</script>

<style scoped>
.ip-overlay {
  position: fixed; inset: 0; z-index: 2000;
  background: rgba(0, 0, 0, .2);
}

.ip-bar {
  width: 440px; background: var(--ai-surface);
  border-radius: 12px; box-shadow: 0 12px 40px rgba(0,0,0,.2);
  overflow: hidden; animation: ipSlideIn .12s ease;
}
@keyframes ipSlideIn { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: translateY(0); } }

.ip-context {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 14px; background: var(--ai-surface-2);
  border-bottom: 1px solid var(--ai-border);
  font-size: 12px; color: var(--ai-ink-3);
}
.ip-selected {
  font-family: var(--ai-font-mono); font-size: 11px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

.ip-input-row {
  display: flex; align-items: center; gap: 8px;
  padding: 12px 14px;
}
.ip-icon { background: var(--sf-gradient-ai); -webkit-background-clip: text; -webkit-text-fill-color: transparent; flex-shrink: 0; }
.ip-input {
  flex: 1; border: none; outline: none; background: transparent;
  font-size: 13px; color: var(--ai-ink-1);
}
.ip-input::placeholder { color: var(--ai-ink-4); }
.ip-submit {
  width: 28px; height: 28px; border-radius: 6px; border: none; flex-shrink: 0;
  background: var(--ai-surface-2); color: var(--ai-ink-4);
  display: flex; align-items: center; justify-content: center; cursor: pointer;
  transition: all .12s;
}
.ip-submit.ready { background: var(--sf-brand-action); color: var(--sf-on-action); }
.ip-submit:disabled { opacity: .4; cursor: not-allowed; }

.ip-hints {
  display: flex; gap: 4px; padding: 0 14px 10px; flex-wrap: wrap;
}
.ip-hint {
  padding: 3px 8px; border-radius: 4px; font-size: 11px;
  border: 1px solid var(--ai-border); background: var(--ai-surface-2);
  color: var(--ai-ink-3); cursor: pointer; transition: all .1s;
}
.ip-hint:hover { border-color: var(--ai-accent-ink); color: var(--ai-accent-ink); }
</style>
