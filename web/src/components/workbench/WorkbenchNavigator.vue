<template>
  <div class="wb-nav">
    <div class="nav-head">
      <span class="nav-head-title">{{ activeSectionLabel }}</span>
      <button
        v-for="tab in sections" :key="tab.key"
        class="nav-head-btn" :class="{ active: section === tab.key }"
        :title="tab.label"
        type="button"
        @click="$emit('change-section', tab.key)"
      >
        <component :is="tab.icon" :size="12" />
      </button>
    </div>

    <!-- 模块大纲（主区域） -->
    <div v-show="section === 'modules'" class="nav-section">
      <nav class="nav-list">
        <button
          v-for="m in modules" :key="m.key"
          class="nav-item" :class="{ active: activeModule === m.key, dirty: m.dirty }"
          @click="$emit('select-module', m.key)"
        >
          <component :is="iconMap[m.key]" :size="17" class="nav-icon" />
          <div class="nav-text">
            <span class="nav-label">{{ m.label }}</span>
            <span class="nav-summary">{{ m.summary }}</span>
          </div>
          <span v-if="m.hasContent" class="nav-count">{{ m.count }}</span>
          <span v-if="m.dirty" class="nav-dot" />
          <span v-if="m.validation?.status === 'error'" class="nav-badge error">!</span>
          <span v-else-if="m.validation?.status === 'success'" class="nav-badge ok" />
        </button>
      </nav>
    </div>

    <!-- 版本历史 -->
    <div v-show="section === 'versions'" class="nav-section">
      <!-- 说明条：当前编辑版本 / 选中版本 -->
      <div v-if="commits.length" class="version-status-bar">
        <div class="vs-row">
          <span class="vs-dot vs-dot-head" />
          <span class="vs-label">当前编辑</span>
          <code class="vs-hash">{{ headCommit?.hash?.slice(0, 7) || 'HEAD' }}</code>
        </div>
        <div v-if="selectedCommit && selectedCommit !== headCommit?.hash" class="vs-row">
          <span class="vs-dot vs-dot-sel" />
          <span class="vs-label">已选版本</span>
          <code class="vs-hash">{{ selectedCommit?.slice(0, 7) }}</code>
          <button class="vs-diff-btn" @click="$emit('view-diff', selectedCommit)">查看差异</button>
        </div>
      </div>

      <div v-if="commits.length" class="nav-commits">
        <div
          v-for="(c, ci) in commits" :key="c.hash"
          class="commit-item"
          :class="{
            active: selectedCommit === c.hash,
            'is-head': ci === 0
          }"
          @click="$emit('select-commit', c.hash)"
        >
          <!-- 时间线竖线 -->
          <div class="commit-timeline">
            <div class="tl-dot" :class="ci === 0 ? 'tl-dot-head' : 'tl-dot-normal'" />
            <div v-if="ci < commits.length - 1" class="tl-line" />
          </div>

          <div class="commit-content">
            <div class="commit-msg-row">
              <span class="commit-msg">{{ c.message }}</span>
              <a-tag v-if="ci === 0" size="small" color="green" class="commit-badge">当前</a-tag>
            </div>
            <div class="commit-meta">
              <code class="commit-hash">{{ c.hash?.slice(0, 7) }}</code>
              <span class="commit-time">{{ formatTime(c.date || c.timestamp) }}</span>
              <span v-if="c.author" class="commit-author">{{ c.author }}</span>
            </div>

            <!-- 操作按钮（选中且非 HEAD 时显示） -->
            <div v-if="selectedCommit === c.hash && ci !== 0" class="commit-actions">
              <button class="ca-btn ca-btn-diff" @click.stop="$emit('view-diff', c.hash)">
                <icon-swap :size="11" /> 查看差异
              </button>
              <a-popconfirm
                content="回滚后将覆盖当前编辑内容，确认吗？"
                ok-text="确认回滚"
                cancel-text="取消"
                @ok="$emit('rollback', c.hash)"
              >
                <button class="ca-btn ca-btn-rollback">
                  <icon-undo :size="11" /> 回滚到此版本
                </button>
              </a-popconfirm>
            </div>
          </div>
        </div>
      </div>
      <div v-else-if="loadingHistory" class="nav-placeholder">
        <a-spin :size="16" />
        <span>加载中...</span>
      </div>
      <div v-else class="nav-placeholder">
        <icon-history :size="20" style="color:var(--ai-ink-4)" />
        <span>{{ skillId ? '暂无提交记录' : '创建后可查看历史' }}</span>
      </div>
    </div>

    <!-- 文件树 -->
    <div v-show="section === 'files'" class="nav-section">
      <div v-if="fileList.length" class="nav-files">
        <template v-for="f in fileList" :key="f.path">
          <div
            v-if="!isDirCollapsed(f)"
            class="file-item"
            :class="{ active: activeFile === f.path, dir: f.isDir }"
            :style="{ paddingLeft: ((f.depth || 0) * 16 + 10) + 'px' }"
            @click="f.isDir ? toggleDir(f.path) : $emit('select-file', f.path)"
            @contextmenu.prevent="showFileMenu($event, f)"
          >
            <icon-down v-if="f.isDir && !collapsedDirs.has(f.path)" :size="10" class="file-chevron" />
            <icon-right v-else-if="f.isDir" :size="10" class="file-chevron" />
            <icon-folder v-if="f.isDir" :size="14" class="file-icon" style="color:var(--ai-warn)" />
            <component v-else :is="fileIcon(f.path)" :size="14" class="file-icon" />
            <span class="file-name">{{ f.name }}</span>
          </div>
        </template>
        <button class="file-add-btn" @click="$emit('file-create')">
          <icon-plus :size="12" /> 新建文件
        </button>
      </div>
      <div v-else-if="!skillId" class="nav-placeholder">
        <icon-folder :size="20" style="color:var(--ai-ink-4)" />
        <span>创建 Skill 后可查看文件</span>
      </div>
      <div v-else class="nav-placeholder">
        <a-spin :size="16" v-if="loadingFiles" />
        <icon-folder v-else :size="20" style="color:var(--ai-ink-4)" />
        <span>{{ loadingFiles ? '加载中...' : '暂无文件' }}</span>
      </div>
      <!-- 文件右键菜单（在 v-if/else 链外面） -->
      <Teleport to="body">
        <div v-if="fileCtx.visible" class="file-ctx-menu" :style="{ top: fileCtx.y + 'px', left: fileCtx.x + 'px' }" @click="fileCtx.visible = false">
          <button class="ctx-item" @click="$emit('file-create')">新建文件</button>
          <button class="ctx-item" @click="$emit('file-create-dir')">新建目录</button>
          <button v-if="fileCtx.file && !fileCtx.file.isDir && fileCtx.file.name !== 'SKILL.md'" class="ctx-item" @click="$emit('file-rename', fileCtx.file.path)">重命名</button>
          <button v-if="fileCtx.file && fileCtx.file.name !== 'SKILL.md'" class="ctx-item danger" @click="$emit('file-delete', fileCtx.file.path)">删除</button>
        </div>
      </Teleport>
    </div>

    <!-- 引用 -->
    <div v-show="section === 'references'" class="nav-section">
      <slot name="references" />
    </div>
  </div>
</template>

<script setup lang="ts">
import {
  IconIdcard, IconBulb, IconList, IconSettings, IconFile, IconExperiment, IconShareAlt,
  IconHistory, IconFolder, IconLink, IconPlus, IconHome,
  IconBranch, IconExport, IconMindMapping, IconSwap, IconUndo, IconCheckCircle,
  IconCodeSandbox,
} from '@arco-design/web-vue/es/icon'
import { computed, reactive } from 'vue'
import type { PropType } from 'vue'
import { IconDown, IconRight, IconCode } from '@arco-design/web-vue/es/icon'
import { formatTimeShort } from '@/utils/format'

type NavigatorModule = {
  key: string
  label?: string
  summary?: string
  count?: number
  hasContent?: boolean
  dirty?: boolean
  validation?: { status?: string }
}

type CommitItem = {
  hash?: string
  message?: string
  date?: string
  timestamp?: string
  author?: string
}

type FileItem = {
  path: string
  name: string
  isDir?: boolean
  depth?: number
}

const props = defineProps({
  modules: { type: Array as PropType<NavigatorModule[]>, default: () => [] },
  activeModule: { type: String, default: 'meta' },
  section: { type: String, default: 'modules' },
  commits: { type: Array as PropType<CommitItem[]>, default: () => [] },
  selectedCommit: { type: String, default: '' },
  loadingHistory: { type: Boolean, default: false },
  skillId: { type: String, default: '' },
  fileList: { type: Array as PropType<FileItem[]>, default: () => [] },
  activeFile: { type: String, default: '' },
  loadingFiles: { type: Boolean, default: false },
})

defineEmits([
  'select-module', 'change-section', 'open-bottom', 'switch-to-code',
  'select-commit', 'select-file',
  'file-create', 'file-create-dir', 'file-rename', 'file-delete',
  'rollback', 'view-diff',
])

// HEAD = 最新 commit（列表第一个）
const headCommit = computed(() => props.commits[0] || null)

const fileCtx = reactive<{ visible: boolean; x: number; y: number; file: FileItem | null }>({ visible: false, x: 0, y: 0, file: null })

function showFileMenu(e: MouseEvent, file: FileItem) {
  fileCtx.visible = true
  fileCtx.x = e.clientX
  fileCtx.y = e.clientY
  fileCtx.file = file
  // 点击其他地方关闭
  setTimeout(() => document.addEventListener('click', () => { fileCtx.visible = false }, { once: true }), 10)
}

// 文件夹折叠/展开状态（路径 → 是否收起）
const collapsedDirs = reactive(new Set<string>())

function toggleDir(dirPath: string) {
  if (collapsedDirs.has(dirPath)) {
    collapsedDirs.delete(dirPath)
  } else {
    collapsedDirs.add(dirPath)
  }
}

function isDirCollapsed(f: FileItem) {
  // 检查此项是否在某个已收起的祖先目录下（如果是则隐藏）
  const itemPath = f.path
  for (const dir of collapsedDirs) {
    const prefix = dir.endsWith('/') ? dir : dir + '/'
    if (itemPath.startsWith(prefix) && itemPath !== prefix) {
      return true
    }
  }
  return false
}

function fileIcon(path: string) {
  if (path.endsWith('.md')) return IconFile
  if (path.endsWith('.yaml') || path.endsWith('.yml')) return IconSettings
  if (path.endsWith('.py')) return IconCode
  if (path.endsWith('.json')) return IconList
  return IconFile
}

function formatTime(ts: string | undefined) {
  if (!ts) return ''
  const formatted = formatTimeShort(ts)
  return formatted === '-' ? ts : formatted
}

const sections = [
  { key: 'modules', label: '模块', icon: IconCodeSandbox },
  { key: 'versions', label: '版本', icon: IconHistory },
  { key: 'files', label: '文件', icon: IconFolder },
  { key: 'references', label: '引用', icon: IconLink },
]

const activeSectionLabel = computed(() => {
  return sections.find((item) => item.key === props.section)?.label || '模块'
})

const iconMap: Record<string, any> = {
  overview: IconHome,
  meta: IconIdcard,
  goal: IconBulb,
  rules: IconBranch,          // 决策分支图标，语义匹配"规则/决策树"
  params: IconSettings,
  output_table: IconExport,   // 输出/导出图标
  todos: IconCheckCircle,
  test_cases: IconExperiment,
  workflow: IconMindMapping,  // 工作流/编排图标
}
</script>

<style scoped>
.wb-nav { display: flex; flex-direction: column; height: 100%; font-family: var(--ai-font-sans); }

.nav-head {
  display: flex; align-items: center; gap: 6px; flex-shrink: 0;
  height: 41px; padding: 0 12px; border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface);
}
.nav-head-title {
  flex: 1; min-width: 0; color: var(--ai-ink-3);
  font-size: 11.5px; font-weight: 600; line-height: 1;
  text-transform: uppercase; letter-spacing: 0.04em;
}
.nav-head-btn {
  width: 22px; height: 22px; border: 0; border-radius: 5px;
  background: transparent; color: var(--ai-ink-4);
  display: inline-flex; align-items: center; justify-content: center;
  cursor: pointer; transition: background .15s, color .15s;
}
.nav-head-btn:hover,
.nav-head-btn.active {
  background: var(--ai-surface-2); color: var(--ai-ink-1);
}

/* 区域 */
.nav-section { flex: 1; display: flex; flex-direction: column; overflow: hidden; }

/* 模块列表 */
.nav-list { flex: 1; padding: 8px 8px 12px; display: flex; flex-direction: column; gap: 1px; overflow-y: auto; }
.nav-item {
  display: flex; align-items: center; gap: 8px; width: 100%;
  padding: 8px 10px; border: 0; border-radius: 5px;
  background: transparent; color: var(--ai-ink-2); cursor: pointer;
  transition: background .15s, color .15s; text-align: left; font-size: 13px;
  font-family: var(--ai-font-sans);
}
.nav-item:hover { background: var(--ai-surface-2); color: var(--ai-ink-1); }
.nav-item.active { background: var(--ai-surface-2); color: var(--ai-ink-1); }
.nav-icon { flex-shrink: 0; color: var(--ai-ink-4); }
.nav-item:hover .nav-icon,
.nav-item.active .nav-icon { color: var(--ai-ink-1); }
.nav-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.nav-label { font-weight: 450; font-size: 12.5px; line-height: 1.3; color: inherit; }
.nav-item.active .nav-label { font-weight: 500; }
.nav-summary {
  font-size: 10.5px; color: var(--ai-ink-4);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: 450;
}
.nav-count {
  font-size: 11px; font-weight: 500; min-width: 20px; text-align: center;
  padding: 0 6px; border-radius: 4px;
  background: var(--ai-surface-3); color: var(--ai-ink-3);
  font-variant-numeric: tabular-nums;
  height: 18px; line-height: 18px;
}
.nav-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--ai-warn); flex-shrink: 0; }
.nav-badge {
  width: 16px; height: 16px; border-radius: 4px; font-size: 10px; font-weight: 500;
  display: flex; align-items: center; justify-content: center; flex-shrink: 0;
}
.nav-badge.error { background: var(--ai-bad-soft); color: var(--ai-bad); }
.nav-badge.ok { background: var(--ai-ok-soft); color: var(--ai-ok); }
.nav-badge.ok::after { content: '✓'; font-size: 9px; }

/* 新建文件按钮 — webkit 1px dashed 4 边渲染成 solid，用 4 个 background gradient 拼出虚线框 */
.file-add-btn {
  display: flex; align-items: center; gap: 4px;
  width: 100%; padding: 7px 10px; border-radius: 5px;
  background-color: transparent;
  background-image:
    linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to bottom, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to bottom, var(--ai-border-2) 50%, transparent 0%);
  background-position: top, bottom, left, right;
  background-size: 6px 1px, 6px 1px, 1px 6px, 1px 6px;
  background-repeat: repeat-x, repeat-x, repeat-y, repeat-y;
  color: var(--ai-ink-4); font-size: 12px;
  cursor: pointer; margin-top: 4px; font-weight: 450;
  font-family: var(--ai-font-sans);
}
.file-add-btn:hover { color: var(--ai-ink-2); }

/* 文件右键菜单 */
.file-ctx-menu {
  position: fixed; z-index: 3000; min-width: 140px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: 6px; box-shadow: 0 6px 18px -8px rgba(20, 19, 15, 0.18); padding: 4px;
  font-family: var(--ai-font-sans);
}
.ctx-item {
  display: block; width: 100%; padding: 6px 12px; border: 0; border-radius: 4px;
  background: transparent; color: var(--ai-ink-2); font-size: 12px;
  text-align: left; cursor: pointer; font-weight: 450; transition: background .15s, color .15s;
}
.ctx-item:hover { background: var(--ai-surface-2); color: var(--ai-ink-1); }
.ctx-item.danger { color: var(--ai-bad); }
.ctx-item.danger:hover { background: var(--ai-bad-soft); }

/* 文件列表 */
.nav-files { flex: 1; overflow-y: auto; padding: 0 8px 8px; }
.file-item {
  display: flex; align-items: center; gap: 8px; padding: 6px 10px;
  border-radius: 5px; cursor: pointer; font-size: 12.5px; color: var(--ai-ink-2);
  transition: background .15s, color .15s;
  font-weight: 450; font-family: var(--ai-font-sans);
}
.file-item:not(.dir):hover { background: var(--ai-surface-2); color: var(--ai-ink-1); }
.file-item.active { background: var(--ai-surface-2); color: var(--ai-ink-1); font-weight: 500; }
.file-item.dir { cursor: default; font-weight: 500; color: var(--ai-ink-3); padding-left: 8px; }
.file-icon { flex-shrink: 0; color: var(--ai-ink-4); }
.file-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* ─── 版本状态条 ─── */
.version-status-bar {
  padding: 8px 10px; margin: 0 8px 6px;
  background: var(--ai-surface-2); border-radius: 6px;
  border: 1px solid var(--ai-border);
}
.vs-row {
  display: flex; align-items: center; gap: 6px;
  font-size: 11px; color: var(--ai-ink-2); line-height: 1.8;
  font-weight: 450;
}
.vs-dot {
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
}
.vs-dot-head { background: var(--ai-ok); }
.vs-dot-sel  { background: var(--ai-ink-1); }
.vs-label { color: var(--ai-ink-4); font-weight: 450; }
.vs-hash {
  font-family: var(--ai-font-mono); font-size: 11px; color: var(--ai-ink-1);
  background: var(--ai-surface-3); padding: 0 4px; border-radius: 4px; font-weight: 450;
}
.vs-diff-btn {
  margin-left: auto; padding: 2px 7px; border-radius: 4px; font-size: 10px;
  border: 1px solid var(--ai-border); color: var(--ai-ink-2);
  background: var(--ai-surface); cursor: pointer; transition: background .15s, color .15s;
  font-weight: 500; font-family: var(--ai-font-sans);
}
.vs-diff-btn:hover { background: var(--ai-surface-2); color: var(--ai-ink-1); }

/* ─── 提交历史列表 ─── */
.nav-commits { flex: 1; overflow-y: auto; padding: 0 8px 8px; }
.commit-item {
  display: flex; gap: 8px; padding: 6px 8px; border-radius: 5px;
  cursor: pointer; margin-bottom: 1px; transition: background .15s;
}
.commit-item:hover { background: var(--ai-surface-2); }
.commit-item.active { background: var(--ai-surface-2); }
.commit-item.is-head { }

/* 时间线 */
.commit-timeline {
  display: flex; flex-direction: column; align-items: center;
  padding-top: 4px; flex-shrink: 0; width: 12px;
}
.tl-dot {
  width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0;
}
.tl-dot-head   { background: var(--ai-ok); box-shadow: 0 0 0 2px var(--ai-ok-soft); }
.tl-dot-normal { background: var(--ai-surface); border: 2px solid var(--ai-border); }
.commit-item.active .tl-dot-normal { background: var(--ai-ink-1); border-color: var(--ai-ink-1); }
.tl-line {
  width: 2px; flex: 1; min-height: 8px; margin-top: 3px;
  background: var(--ai-border);
}

/* 提交内容 */
.commit-content { flex: 1; min-width: 0; }
.commit-msg-row {
  display: flex; align-items: flex-start; gap: 5px; margin-bottom: 3px;
}
.commit-msg {
  font-size: 12px; font-weight: 450; color: var(--ai-ink-2); line-height: 1.4;
  flex: 1; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.commit-item.active .commit-msg { color: var(--ai-ink-1); font-weight: 500; }
.commit-badge { flex-shrink: 0; }
.commit-meta { display: flex; gap: 6px; align-items: center; }
.commit-hash {
  font-size: 10px; font-family: var(--ai-font-mono); color: var(--ai-ink-3);
  background: var(--ai-surface-3); padding: 0 3px; border-radius: 4px; font-weight: 450;
}
.commit-time  { font-size: 10px; color: var(--ai-ink-4); font-weight: 450; }
.commit-author{ font-size: 10px; color: var(--ai-ink-4); font-weight: 450; }

/* 操作按钮（选中非 HEAD 时） */
.commit-actions {
  display: flex; gap: 5px; margin-top: 6px; flex-wrap: wrap;
}
.ca-btn {
  display: inline-flex; align-items: center; gap: 3px;
  padding: 4px 9px; border-radius: 5px; font-size: 11px; cursor: pointer;
  border: 1px solid var(--ai-border); background: var(--ai-surface);
  color: var(--ai-ink-2); transition: background .15s, color .15s, border-color .15s;
  font-weight: 500; font-family: var(--ai-font-sans);
}
.ca-btn:hover { background: var(--ai-surface-2); color: var(--ai-ink-1); border-color: var(--ai-border-2); }
.ca-btn-rollback {
  border-color: var(--ai-border); color: var(--ai-ink-2);
}
.ca-btn-rollback:hover { background: var(--ai-surface-2); color: var(--ai-ink-1); }

/* 占位 */
.nav-placeholder {
  flex: 1; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 8px; padding: 20px;
  font-size: 12px; color: var(--ai-ink-4); text-align: center; font-weight: 450;
}

/* a-tag → ai-pill 映射（commit badge 等） */
.wb-nav :deep(.arco-tag) {
  height: 20px; padding: 0 7px; border-radius: 4px; font-size: 11px;
  font-weight: 500; line-height: 18px;
}
.wb-nav :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft); color: var(--ai-ok); border-color: transparent;
}
.wb-nav :deep(.arco-tag-color-arcoblue),
.wb-nav :deep(.arco-tag-color-blue) {
  background: var(--ai-info-soft); color: var(--ai-info); border-color: transparent;
}
.wb-nav :deep(.arco-tag-color-orange),
.wb-nav :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft); color: var(--ai-warn); border-color: transparent;
}
.wb-nav :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft); color: var(--ai-bad); border-color: transparent;
}
.wb-nav :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2); color: var(--ai-ink-3); border-color: transparent;
}
</style>
