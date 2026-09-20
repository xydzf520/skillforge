<template>
  <component :is="resolved" />
</template>

<script setup lang="ts">
import { computed, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import HallGptImageGenForm from './HallGptImageGenForm.vue'
import HallGptImageGenChat from './HallGptImageGenChat.vue'
import HallFinetunedModelChat from './HallFinetunedModelChat.vue'
import HallAiChat from './HallAiChat.vue'

const route = useRoute()
const router = useRouter()

const specialAbilityRoutes: Record<string, { path: string; component: any }> = {
  'skillforge-finetuned-model-chat': {
    path: '/hall/finetuned-model-chat',
    component: HallFinetunedModelChat,
  },
}

const resolved = computed(() => {
  const id = String(route.params.id || '')
  if (id === 'ai-chat') return HallAiChat
  const special = specialAbilityRoutes[id]
  if (special) return special.component
  if (id === 'gpt-imagegen-dialogue') return HallGptImageGenChat
  return HallGptImageGenForm
})

watchEffect(() => {
  const id = String(route.params.id || '')
  const special = specialAbilityRoutes[id]
  if (special && route.path !== special.path) {
    router.replace(special.path)
  }
})
</script>
