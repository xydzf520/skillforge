<template>
  <div class="skill-dep-page ai-main">
    <div class="skill-dep-head ai-pagehead">
      <div class="page-heading">
        <button type="button" class="ai-btn back-btn" @click="$router.back()">
          <SfShellIcon name="arrowl" />
          <span>返回</span>
        </button>
        <div class="ai-crumbs">Skills · 依赖图</div>
        <h2 class="ai-title"><span class="dep-skill-id">{{ skillId }}</span> · 依赖图</h2>
        <div class="ai-sub">Fork 血缘、依赖数据源、下游 Playbook 调用</div>
      </div>
    </div>

    <div class="skill-dep-body ai-pagebody">
      <section class="skill-dep-card ai-card">
        <SkillDependencyGraph
          :skill-id="skillId"
          @open-node="onOpenNode"
        />
      </section>
    </div>
  </div>
</template>

<style scoped>
.skill-dep-page {
  min-height: 100%;
}
.skill-dep-head {
  align-items: flex-start;
}
.page-heading {
  display: grid;
  gap: 0;
}
.skill-dep-body {
  min-width: 0;
}
.skill-dep-card {
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  box-shadow: none;
  overflow: hidden;
}
.dep-skill-id {
  font-family: var(--ai-font-mono);
}
.back-btn {
  margin-bottom: 4px;
}
.back-btn svg {
  width: 14px;
  height: 14px;
}
</style>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SkillDependencyGraph from '@/components/studio/SkillDependencyGraph.vue'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

defineOptions({ name: 'SkillDependencyView' })

const route = useRoute()
const router = useRouter()
const skillId = computed(() => String(route.params.id || ''))

function onOpenNode(node: { id: string; type: string }) {
  if (node.type === 'skill') {
    router.push(`/skills/${node.id}`)
  } else if (node.type === 'datasource') {
    router.push(`/hall/data/${node.id}`)
  } else if (node.type === 'playbook') {
    router.push(`/playbook/${node.id}`)
  }
}
</script>
