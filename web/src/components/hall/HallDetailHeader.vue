<template>
  <div class="page-header hall-detail-header">
    <div class="page-heading">
      <a-breadcrumb v-if="!hideCrumbs && crumbs.length" class="detail-crumb">
        <a-breadcrumb-item v-for="(c, idx) in crumbs" :key="idx">
          <router-link v-if="c.to" :to="c.to">{{ c.label }}</router-link>
          <template v-else>{{ c.label }}</template>
        </a-breadcrumb-item>
      </a-breadcrumb>
      <div v-if="!hideTitle" class="title-row">
        <a-button
          class="back-icon-btn"
          type="text"
          size="small"
          :title="backTitle"
          aria-label="返回大厅"
          @click="goBack"
        >
          <template #icon><icon-left /></template>
        </a-button>
        <a-tooltip
          v-if="tooltip"
          :content="tooltip"
          position="right"
          trigger="hover focus"
          :popup-visible-delay="200"
        >
          <h2 class="page-title" tabindex="0">{{ title }}</h2>
        </a-tooltip>
        <h2 v-else class="page-title">{{ title }}</h2>
      </div>
    </div>
    <div v-if="$slots.actions" class="header-actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { IconLeft } from '@arco-design/web-vue/es/icon'

type Crumb = { label: string; to?: string }

const props = withDefaults(
  defineProps<{
    crumbs: Crumb[]
    title: string
    hideTitle?: boolean
    hideCrumbs?: boolean
    tooltip?: string
    backTitle?: string
    fallbackTo?: string
  }>(),
  { backTitle: '返回大厅', fallbackTo: '/hall' },
)

const router = useRouter()

function goBack() {
  if (window.history.state?.back) router.back()
  else router.push(props.fallbackTo)
}
</script>

<style scoped>
.hall-detail-header {
  /* global .page-header 已是 flex + space-between + align-center；
     这里只补充 heading 内部布局 */
}
.detail-crumb {
  margin-bottom: 6px;
}
.title-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.back-icon-btn {
  padding: 0 4px;
  color: var(--ai-ink-2);
}
.back-icon-btn:hover {
  color: var(--ai-ink-1);
}
.header-actions {
  display: flex;
  gap: 8px;
}
</style>
