<template>
  <div class="dep-view">
    <p class="dep-subtitle">
      显示当前 Skill 的 fork 血缘、依赖数据源与 Playbook 调用关系。
    </p>
    <SkillDependencyGraph
      v-if="shouldRenderGraph"
      :skill-id="skillId"
      @open-node="onOpenNode"
    />
    <div v-else class="dep-idle">
      切到“依赖图”标签后再加载。
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import SkillDependencyGraph from '@/components/studio/SkillDependencyGraph.vue'
import type { SkillDependencyNodeType } from '@/types/skillstudio'

const props = defineProps<{
  skillId: string
  active?: boolean
}>()

const router = useRouter()
const shouldRenderGraph = ref(Boolean(props.active))

watch(
  () => props.active,
  (active) => {
    if (active) shouldRenderGraph.value = true
  },
  { immediate: true },
)

function onOpenNode(node: { id: string; type: SkillDependencyNodeType }) {
  if (node.type === 'skill') {
    void router.push(`/skills/${encodeURIComponent(node.id)}`)
    return
  }
  if (node.type === 'datasource') {
    void router.push(`/hall/data/${encodeURIComponent(node.id)}`)
    return
  }
  if (node.type === 'playbook') {
    void router.push(`/playbook/${encodeURIComponent(node.id)}`)
  }
}
</script>

<style scoped>
.dep-view {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  min-height: 0;
  padding: 16px;
}

.dep-subtitle {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ai-ink-3);
}

.dep-idle {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 260px;
  font-size: 12px;
  color: var(--ai-ink-4);
  /* webkit 4 边 1px dashed → solid，用 4 个 background gradient 拼出虚线框 */
  background-color: var(--ai-surface-2);
  background-image:
    linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to bottom, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to bottom, var(--ai-border-2) 50%, transparent 0%);
  background-position: top, bottom, left, right;
  background-size: 6px 1px, 6px 1px, 1px 6px, 1px 6px;
  background-repeat: repeat-x, repeat-x, repeat-y, repeat-y;
  border-radius: 8px;
}
</style>
