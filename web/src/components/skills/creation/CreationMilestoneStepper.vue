<template>
  <!-- 新建 Skill 向导 · AI 合成阶段顶部进度条 -->
  <!-- 7 个固定 milestone 的实时状态展示 -->
  <div class="cms-wrap">
    <!-- 右上角尝试次数 tag（可选） -->
    <a-tag
      v-if="props.currentAttempt !== undefined && props.currentAttempt !== null"
      class="cms-attempt-tag"
      size="small"
      :color="props.hasError ? 'red' : 'arcoblue'"
    >
      第 {{ props.currentAttempt }}/{{ props.maxAttempts ?? 3 }} 次尝试
    </a-tag>

    <!-- 主体：圆点 + 连线 + label -->
    <div class="cms-track">
      <div
        v-for="(m, i) in items"
        :key="m.key"
        class="cms-node"
        :class="[`cms-node-${m.status}`]"
      >
        <!-- 左侧连线：第一个节点没有 -->
        <span
          v-if="i > 0"
          class="cms-line"
          :class="{ 'cms-line-done': connectorDone(i) }"
        />

        <!-- 圆点 + 图标 -->
        <span class="cms-dot">
          <icon-check v-if="m.status === 'done'" :size="12" class="cms-icon" />
          <icon-close v-else-if="m.status === 'failed'" :size="12" class="cms-icon" />
        </span>

        <!-- label + 耗时 -->
        <div class="cms-text">
          <span class="cms-label" :title="m.label">
            <span class="cms-label-full">{{ m.label }}</span>
            <span class="cms-label-short">{{ shortLabel(m.key, m.label) }}</span>
          </span>
          <span
            v-if="(m.status === 'done' || m.status === 'failed') && m.elapsed_s !== undefined"
            class="cms-elapsed"
          >
            ({{ formatElapsed(m.elapsed_s) }})
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { IconCheck, IconClose } from '@arco-design/web-vue/es/icon'

// ── milestone 单项结构 ──
interface MilestoneItem {
  key: string
  label: string
  status: 'pending' | 'running' | 'done' | 'failed'
  elapsed_s?: number
}

// ── 组件 Props ──
interface Props {
  milestones: MilestoneItem[]
  currentAttempt?: number
  maxAttempts?: number
  hasError?: boolean
}

const props = defineProps<Props>()

// 父组件保证 7 项顺序，这里仅做响应式引用
const items = computed<MilestoneItem[]>(() => props.milestones ?? [])

// 左侧连线染绿条件：仅当前节点已完成（done）时，本节点左侧连线同步染色
// failed 不染绿（红点即可提示故障位置）
function connectorDone(index: number): boolean {
  const cur = items.value[index]
  return !!cur && cur.status === 'done'
}

// 窄屏下的简写映射（中文内部工具）
const SHORT_MAP: Record<string, string> = {
  identify_intent: '识别',
  draft_contract: '契约',
  write_skill_md: 'Skill.md',
  write_main_py: 'main.py',
  write_tests: 'tests',
  verify_import: '验证',
  verify_schema: '自检',
}

function shortLabel(key: string, fallback: string): string {
  return SHORT_MAP[key] ?? fallback
}

// 耗时格式化：<60s → "XXs"，否则 "M分S秒"
function formatElapsed(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return ''
  if (sec < 60) return `${Math.round(sec)}s`
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return s === 0 ? `${m}m` : `${m}m${s}s`
}
</script>

<style scoped>
/* ── 容器 ── */
.cms-wrap {
  position: relative;
  max-width: 880px;
  margin: 0 auto;
  padding: var(--sf-spacing-sm) 0;
}

/* 右上角尝试次数 tag */
.cms-attempt-tag {
  position: absolute;
  top: 0;
  right: 0;
}

/* ── 轨道：横向 7 段 ── */
.cms-track {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  width: 100%;
}

/* ── 单个节点（dot + label 垂直堆叠，连线在左） ── */
.cms-node {
  position: relative;
  flex: 1 1 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 0;
}

/* 左侧连线：绝对定位到节点左边，宽度到上一个节点中心 */
.cms-line {
  position: absolute;
  top: 9px; /* (dot 20 - line 2) / 2 = 9，使连线居于圆点垂直中线 */
  right: 50%;
  left: -50%;
  height: 2px;
  background: var(--ai-border);
  z-index: 0;
}
.cms-line-done {
  background: var(--ai-ok);
}

/* ── 圆点 ── */
.cms-dot {
  position: relative;
  z-index: 1;
  width: 20px;
  height: 20px;
  border-radius: var(--sf-radius-pill);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  color: #fff;
  transition: background-color var(--sf-transition-fast),
    border-color var(--sf-transition-fast);
}
.cms-icon {
  color: #fff;
  line-height: 1;
}

/* ── 状态色：pending 使用默认 dot 样式，此处不额外覆盖 ── */

/* running：蓝色填充 + 脉冲光晕 */
.cms-node-running .cms-dot {
  background: var(--ai-info);
  border-color: var(--ai-info);
}
.cms-node-running .cms-dot::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: var(--sf-radius-pill);
  /* 初始覆盖在 dot 上，逐步向外 8px 扩散并淡出 */
  box-shadow: 0 0 0 0 rgba(var(--arcoblue-6), 0.45);
  animation: cms-pulse 1.2s ease-out infinite;
}

/* done：绿色填充 + 白色 check */
.cms-node-done .cms-dot {
  background: var(--ai-ok);
  border-color: var(--ai-ok);
}

/* failed：红色填充 + 白色 × */
.cms-node-failed .cms-dot {
  background: var(--ai-bad);
  border-color: var(--ai-bad);
}

/* ── label 区域 ── */
.cms-text {
  margin-top: var(--sf-spacing-xs); /* 4px */
  display: flex;
  flex-direction: column;
  align-items: center;
  line-height: 1.2;
  max-width: 100%;
}

.cms-label {
  font-size: var(--sf-font-xs);
  color: var(--ai-ink-4);
  text-align: center;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

/* 默认显示完整 label，窄屏切简写 */
.cms-label-short {
  display: none;
}
.cms-label-full {
  display: inline;
}

.cms-node-running .cms-label {
  color: var(--ai-info);
  font-weight: 600;
}
.cms-node-done .cms-label {
  color: var(--ai-ink-2);
}
.cms-node-pending .cms-label {
  color: var(--ai-ink-4);
}
.cms-node-failed .cms-label {
  color: var(--ai-bad);
  font-weight: 600;
}

/* 耗时：done/failed 才出现 */
.cms-elapsed {
  margin-top: 2px;
  font-size: 10px;
  color: var(--ai-ink-4);
  line-height: 1.2;
}

/* ── running 脉冲动画（0 → 8px 透明光晕，1.2s 循环） ── */
@keyframes cms-pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(var(--arcoblue-6), 0.45);
  }
  70% {
    box-shadow: 0 0 0 8px rgba(var(--arcoblue-6), 0);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(var(--arcoblue-6), 0);
  }
}

/* ── 窄屏紧凑模式 ── */
@media (max-width: 720px) {
  .cms-dot {
    width: 16px;
    height: 16px;
  }
  .cms-line {
    top: 7px; /* (16 - 2) / 2 */
  }
  .cms-label {
    font-size: 11px;
  }
  .cms-label-full {
    display: none;
  }
  .cms-label-short {
    display: inline;
  }
}
</style>
