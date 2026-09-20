<template>
  <div class="imagegen-route-shell ai-main">
    <HallGptImageGenLegacy v-if="viewMode === 'legacy'" :run-ui="runUi" @switch-new="setMode('new')" />
    <HallGptImageGenChat
      v-else-if="viewMode === 'chat'"
      :run-ui="runUi"
      @switch-new="setMode('new')"
      @switch-legacy="setMode('legacy')"
    />
    <HallGptImageGenWorkspace
      v-else
      :run-ui="runUi"
      @switch-legacy="setMode('legacy')"
      @switch-chat="setMode('chat')"
    />
    <DirectCapabilityUiDesigner :capability-id="capabilityId" v-model="runUi" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import HallGptImageGenLegacy from './HallGptImageGenLegacy.vue'
import HallGptImageGenWorkspace from './HallGptImageGenWorkspace.vue'
import HallGptImageGenChat from './HallGptImageGenChat.vue'
import DirectCapabilityUiDesigner from '@/components/hall/DirectCapabilityUiDesigner.vue'

const route = useRoute()
const router = useRouter()
const storageKey = 'skillforge:gpt-imagegen:view-mode'
const runUi = ref<Record<string, any> | null>(null)
const capabilityId = computed(() => String(route.params.id || ''))
const routeMode = computed(() => String(route.query.mode || ''))
const viewMode = computed(() => {
  if (routeMode.value === 'legacy') return 'legacy'
  if (routeMode.value === 'chat') return 'chat'
  return 'new'
})

function setMode(mode: 'new' | 'legacy' | 'chat') {
  localStorage.setItem(storageKey, mode)
  router.replace({
    query: {
      ...route.query,
      mode,
    },
  })
}

watch(
  () => route.query.mode,
  (mode) => {
    if (mode) return
    const stored = localStorage.getItem(storageKey)
    if (stored === 'legacy') setMode('legacy')
  },
  { immediate: true },
)
</script>

<style scoped>
.imagegen-route-shell {
  min-height: calc(100vh - 52px);
  color: var(--ai-ink-1);
}
</style>
