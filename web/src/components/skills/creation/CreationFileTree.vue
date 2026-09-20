<template>
  <!-- Skill 创建向导 · AI 合成阶段左栏文件树 -->
  <div class="creation-file-tree">
    <!-- 顶部计数 header -->
    <div class="creation-file-tree-header">
      <span class="creation-file-tree-count">
        已生成 {{ fileCount }} 个文件
      </span>
      <!-- 右侧留给以后扩展 -->
      <span class="creation-file-tree-header-slot" />
    </div>

    <!-- 空态：AI 还没写出任何文件 -->
    <div v-if="fileCount === 0" class="creation-file-empty">
      等 AI 开始写文件…
    </div>

    <!-- 分组渲染，按固定顺序 -->
    <template v-else>
      <div
        v-for="group in groups"
        v-show="group.items.length > 0"
        :key="group.key"
        class="creation-file-group"
      >
        <div class="creation-file-group-header">
          <span class="creation-file-group-icon">{{ group.icon }}</span>
          <span>{{ group.label }}</span>
        </div>
        <div
          v-for="path in group.items"
          :key="path"
          class="creation-file-item"
          :class="{
            active: path === props.selected,
            'recently-added': recentlySet.has(path),
          }"
          @click="onSelect(path)"
        >
          <!-- 左侧 icon:根据扩展名决定表情符号 -->
          <span class="creation-file-item-icon">{{ extIcon(path) }}</span>
          <!-- 中间文件名 -->
          <span class="creation-file-item-name">{{ path }}</span>
          <!-- 右侧大小 -->
          <span class="creation-file-item-size">{{ formatSize(path) }}</span>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

// ─── Props / Emits 契约 ───
const props = withDefaults(
  defineProps<{
    files: Record<string, string>
    selected?: string
    recentlyAdded?: string[]
  }>(),
  {
    selected: '',
    recentlyAdded: () => [],
  },
)

const emit = defineEmits<{
  (e: 'update:selected', path: string): void
}>()

// ─── 已生成文件总数 ───
const fileCount = computed(() => Object.keys(props.files).length)

// ─── 最近新增的路径集合,用于 O(1) 命中判断 ───
const recentlySet = computed(() => new Set(props.recentlyAdded || []))

// ─── 分组定义:顺序固定,每组按字符串排序,空组由 v-show 隐藏 ───
interface FileGroup {
  key: string
  icon: string
  label: string
  items: string[]
}

const groups = computed<FileGroup[]>(() => {
  const paths = Object.keys(props.files).sort()
  const contract: string[] = []
  const docs: string[] = []
  const policy: string[] = []
  const scripts: string[] = []
  const tests: string[] = []
  const fixtures: string[] = []
  const others: string[] = []

  for (const p of paths) {
    if (p === 'contract.json') {
      contract.push(p)
    } else if (p === 'SKILL.md' || p === 'intent.md') {
      docs.push(p)
    } else if (p === 'policy.yaml') {
      policy.push(p)
    } else if (p.startsWith('scripts/')) {
      scripts.push(p)
    } else if (p.startsWith('tests/')) {
      tests.push(p)
    } else if (p.startsWith('fixtures/')) {
      fixtures.push(p)
    } else {
      others.push(p)
    }
  }

  return [
    { key: 'contract', icon: '📜', label: '契约', items: contract },
    { key: 'docs', icon: '📋', label: '说明', items: docs },
    { key: 'policy', icon: '🔐', label: '策略', items: policy },
    { key: 'scripts', icon: '🛠', label: '脚本', items: scripts },
    { key: 'tests', icon: '🧪', label: '测试', items: tests },
    { key: 'fixtures', icon: '🎯', label: '样例', items: fixtures },
    { key: 'others', icon: '🗂', label: '其他', items: others },
  ]
})

// ─── 根据扩展名给文件项前面的小 icon ───
function extIcon(path: string): string {
  const lower = path.toLowerCase()
  if (lower.endsWith('.md')) return '📝'
  if (lower.endsWith('.json')) return '🗂'
  if (lower.endsWith('.yaml') || lower.endsWith('.yml')) return '⚙️'
  if (lower.endsWith('.py')) return '🐍'
  return '📄'
}

// ─── 文件大小格式化:< 1KB 显示 B,>= 1KB 显示 KB(1 位小数) ───
function formatSize(path: string): string {
  const content = props.files[path] ?? ''
  const n = content.length
  if (n < 1024) return `${n}B`
  return `${(n / 1024).toFixed(1)}KB`
}

// ─── 点击文件项:emit update:selected 走 v-model 回路 ───
function onSelect(path: string) {
  emit('update:selected', path)
}
</script>

<style scoped>
/* 外层容器:父组件 grid 给 260px 宽,这里占满并纵向滚动 */
.creation-file-tree {
  width: 100%;
  height: 100%;
  overflow-y: auto;
  background: var(--ai-surface);
  font-size: 12px;
  color: var(--ai-ink-1);
}

/* 顶部 header:32px 高,左侧显示文件总数 */
.creation-file-tree-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 32px;
  padding: 0 12px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
}
.creation-file-tree-count {
  font-size: 12px;
  font-weight: 600;
  color: var(--ai-ink-3);
}
.creation-file-tree-header-slot {
  /* 占位,留给以后扩展(例如搜索/过滤按钮) */
  min-width: 0;
}

/* 空态:居中提示,至少 120px 高 */
.creation-file-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 120px;
  padding: 16px;
  font-size: 13px;
  color: var(--ai-ink-4);
}

/* 分组容器 */
.creation-file-group {
  display: flex;
  flex-direction: column;
}

/* 分组 header:12px 粗体 */
.creation-file-group-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  font-size: 12px;
  font-weight: 600;
  color: var(--ai-ink-3);
}
.creation-file-group-icon {
  font-size: 12px;
  line-height: 1;
}

/* 文件项:等宽字体,左 icon + 中间 filename + 右 size */
.creation-file-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  font-size: 12px;
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-1);
  cursor: pointer;
  border-radius: 4px;
  transition: background 0.12s ease, color 0.12s ease;
}
.creation-file-item:hover {
  background: var(--ai-surface-2);
}

/* 左侧 icon 空白占位:14px 保证对齐 */
.creation-file-item-icon {
  flex-shrink: 0;
  width: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
}

/* 中间文件名:flex-1 + ellipsis */
.creation-file-item-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 右侧大小:text-4 小字 */
.creation-file-item-size {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--ai-ink-4);
  font-variant-numeric: tabular-nums;
}

/* 选中态:蓝底 + 蓝字 + 左侧 3px 蓝色竖条(用 inset box-shadow 实现) */
.creation-file-item.active {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  font-weight: 600;
  box-shadow: inset 3px 0 0 var(--ai-info);
}
.creation-file-item.active .creation-file-item-size {
  color: var(--ai-info);
}

/*
 * 新文件脉冲动画:1.2s 一次,不循环
 * 注意:pulse 和 active 可以共存——active 的背景位于后面,
 * 会覆盖脉冲结束后的静态态;脉冲期间 background 由 animation 接管。
 */
.creation-file-item.recently-added {
  animation: pulseBg 1.2s ease-out 1;
}
@keyframes pulseBg {
  0% {
    background: rgba(var(--arcoblue-1), 0.7);
  }
  100% {
    background: transparent;
  }
}
</style>
