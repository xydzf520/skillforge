<template>
  <div class="module-sidebar" :class="{ collapsed }">
    <!-- 折叠按钮 -->
    <button class="sidebar-toggle" @click="collapsed = !collapsed" :title="collapsed ? '展开模块' : '收起模块'">
      <icon-menu-fold v-if="!collapsed" :size="16" />
      <icon-menu-unfold v-else :size="16" />
    </button>

    <!-- 模块列表 -->
    <div class="module-list">
      <button
        v-for="m in modulesWithMeta"
        :key="m.key"
        class="module-item"
        :class="{ active: m.key === activeModule }"
        :title="collapsed ? `${m.label} · ${m.statusText}` : ''"
        @click="$emit('change', m.key)"
      >
        <component :is="m.icon" :size="18" class="module-icon" />
        <template v-if="!collapsed">
          <div class="module-info">
            <span class="module-label">{{ m.label }}</span>
            <span class="module-status-text">{{ m.statusText }}</span>
          </div>
          <span class="module-dot" :class="m.dotClass" />
        </template>
      </button>
    </div>

    <!-- 底部 -->
    <div v-if="!collapsed" class="sidebar-footer">
      <div class="module-hint">点击模块聚焦对话范围</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { PropType } from 'vue'
import {
  IconBulb, IconList, IconSettings, IconFile, IconExperiment, IconShareAlt,
  IconMenuFold, IconMenuUnfold,
} from '@arco-design/web-vue/es/icon'

const props = defineProps({
  modules: { type: Array as PropType<any[]>, default: () => [] },
  activeModule: { type: String, default: '' },
})
defineEmits(['change'])

const collapsed = ref(false)

const ICON_MAP = {
  goal: IconBulb,
  rules: IconList,
  params: IconSettings,
  output_table: IconFile,
  test_cases: IconExperiment,
  workflow: IconShareAlt,
}

const LABEL_MAP = {
  goal: '目标',
  rules: '规则',
  params: '参数',
  output_table: '输出',
  test_cases: '测试',
  workflow: '工作流',
}

const modulesWithMeta = computed(() =>
  props.modules.map((m) => {
    const key = m.key || m.module
    const rawStatus = m.status || ''
    const isReady = rawStatus === 'ready' || rawStatus === '已验证'
    const isDraft = rawStatus === '草稿' || rawStatus === 'draft'
    return {
      key,
      label: (LABEL_MAP as Record<string, unknown>)[key] || m.label || key,
      icon: (ICON_MAP as Record<string, unknown>)[key] || IconBulb,
      statusText: isReady ? `${m.item_count || m.itemCount || 0} 项` : isDraft ? '草稿' : '空',
      dotClass: isReady ? 'dot-ready' : isDraft ? 'dot-draft' : 'dot-empty',
    }
  })
)
</script>

<style scoped>
.module-sidebar {
  display: flex;
  flex-direction: column;
  height: 100%;
  width: 200px;
  background: var(--ai-surface);
  border-right: 1px solid var(--ai-border);
  transition: width var(--sf-transition);
  overflow: hidden;
}
.module-sidebar.collapsed {
  width: 52px;
}

/* 折叠按钮 */
.sidebar-toggle {
  display: flex; align-items: center; justify-content: center;
  height: 44px; width: 100%; border: none; background: none;
  color: var(--ai-ink-3); cursor: pointer; flex-shrink: 0;
  border-bottom: 1px solid var(--ai-border);
  transition: color var(--sf-transition);
}
.sidebar-toggle:hover { color: var(--ai-warn); }

/* 模块列表 */
.module-list {
  flex: 1; overflow-y: auto; padding: 8px 6px;
  display: flex; flex-direction: column; gap: 2px;
}
.module-item {
  display: flex; align-items: center; gap: 10px;
  width: 100%; border: none; border-radius: 8px;
  padding: 10px;
  background: transparent; cursor: pointer;
  transition: all var(--sf-transition); color: var(--ai-ink-2);
  text-align: left;
}
.collapsed .module-item {
  justify-content: center; padding: 10px 0;
}
.module-item:hover {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
}
.module-item.active {
  background: var(--ai-warn-soft);
  color: var(--ai-ink-1);
  box-shadow: inset 3px 0 0 var(--ai-warn);
}
.module-item.active .module-icon {
  color: var(--ai-warn);
}
.module-icon {
  flex-shrink: 0;
  color: var(--ai-ink-3);
  transition: color var(--sf-transition);
}
.module-info {
  flex: 1; min-width: 0;
  display: flex; flex-direction: column;
}
.module-label {
  font-size: 13px; font-weight: 800; line-height: 1.3;
}
.module-status-text {
  font-size: 11px; color: var(--ai-ink-3); line-height: 1.3; font-weight: 600;
}
.module-dot {
  width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0;
}
.dot-ready { background: var(--ai-ok); }
.dot-draft { background: var(--ai-warn); }
.dot-empty { background: var(--ai-border); }

/* 底部 */
.sidebar-footer {
  padding: 10px 12px; border-top: 1px solid var(--ai-border);
}
.module-hint {
  font-size: 11px; color: var(--ai-ink-3); text-align: center; font-weight: 600;
}
</style>
