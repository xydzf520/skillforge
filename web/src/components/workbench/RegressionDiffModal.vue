<template>
  <a-modal
    :visible="visible"
    :title="title"
    width="780px"
    :body-style="{ padding: '0', maxHeight: '70vh', overflow: 'auto' }"
    :footer="false"
    :mask-closable="true"
    @update:visible="v => $emit('update:visible', v)"
  >
    <div v-if="!diff" class="rd-empty">加载中...</div>
    <!-- [M10] 后端返回错误时的兜底, 防止 diff.has_changes 等访问崩溃 -->
    <div v-else-if="diff.error || diff._error" class="rd-empty rd-empty-error">
      <icon-exclamation-circle-fill :size="32" style="color: var(--sf-accent-red); margin-bottom: 8px;" />
      <div>加载回归 diff 失败</div>
      <div class="rd-error-detail">{{ diff.error || diff._error || '未知错误' }}</div>
    </div>
    <div v-else-if="!diff.has_changes" class="rd-empty">
      <icon-check-circle :size="32" style="color: var(--sf-accent-green); margin-bottom: 8px;" />
      <div>本次保存无规则层面变化</div>
    </div>
    <div v-else class="rd-body">
      <!-- Summary -->
      <div class="rd-summary">
        <div class="rd-summary-text">{{ diff.summary }}</div>
        <div class="rd-summary-meta">
          base: <code>{{ diff.base_ref }}</code> → head: <code>{{ diff.head_ref }}</code>
        </div>
      </div>

      <!-- [H5/M10] 后端 notes (降级 / 过度估计) 透明展示给用户 -->
      <div v-if="diff.notes?.length" class="rd-notes">
        <div v-for="(note, i) in diff.notes" :key="i" class="rd-note-item">
          <icon-info-circle :size="12" />
          <span>{{ note }}</span>
        </div>
      </div>

      <!-- 受影响的测试用例（最重要：用户最关心的"我改了 Skill 测试还能过吗"） -->
      <div v-if="diff.test_cases_affected?.length" class="rd-section rd-section-affected">
        <div class="rd-section-head">
          <icon-exclamation-circle-fill :size="14" style="color: var(--sf-accent-amber)" />
          受影响的测试用例 ({{ diff.test_cases_affected.length }})
        </div>
        <div class="rd-tests">
          <div v-for="name in diff.test_cases_affected" :key="name" class="rd-test-item">
            <icon-thunderbolt :size="11" />
            <span>{{ name }}</span>
          </div>
        </div>
        <div class="rd-section-hint">
          建议保存后立即跑一遍这些用例，确认行为变化符合预期。
        </div>
      </div>

      <!-- 决策步骤变化 -->
      <div v-if="diff.steps?.length" class="rd-section">
        <div class="rd-section-head">
          <icon-branch :size="14" />
          决策步骤变化 ({{ diff.steps.length }})
        </div>
        <!-- [M10] 大数据量保护: > 50 步只展示前 50, 提示用户翻页 -->
        <div v-if="diff.steps.length > 50" class="rd-truncation-hint">
          ⚠️ 改动涉及 {{ diff.steps.length }} 个步骤, 仅展示前 50 个; 完整详情请用 git diff 查看
        </div>
        <div class="rd-steps">
          <div v-for="step in displayedSteps" :key="step.step_id" class="rd-step">
            <div class="rd-step-head">
              <a-tag size="small" :color="stepTagColor(step.change_type)">{{ stepTagLabel(step.change_type) }}</a-tag>
              <span class="rd-step-name">{{ step.new_name || step.old_name || step.step_id }}</span>
              <span v-if="step.name_changed" class="rd-step-rename">
                （改名 {{ step.old_name }} → {{ step.new_name }}）
              </span>
            </div>
            <!-- 新增分支 -->
            <div v-for="(b, i) in step.branches_added" :key="`a${i}`" class="rd-branch rd-branch-added">
              <span class="rd-branch-marker">+</span>
              <code>{{ b.new?.condition }}</code>
              <span class="rd-arrow">→</span>
              <span>{{ b.new?.conclusion || '(无结论)' }}</span>
              <span v-if="b.new?.action" class="rd-action">[{{ b.new.action }}]</span>
            </div>
            <!-- 删除分支 -->
            <div v-for="(b, i) in step.branches_removed" :key="`r${i}`" class="rd-branch rd-branch-removed">
              <span class="rd-branch-marker">−</span>
              <code>{{ b.old?.condition }}</code>
              <span class="rd-arrow">→</span>
              <span>{{ b.old?.conclusion || '(无结论)' }}</span>
              <span v-if="b.old?.action" class="rd-action">[{{ b.old.action }}]</span>
            </div>
            <!-- 修改分支 -->
            <div v-for="(b, i) in step.branches_modified" :key="`m${i}`" class="rd-branch-mod">
              <div class="rd-mod-fields">
                修改字段:
                <a-tag v-for="f in b.fields_changed" :key="f" size="small" color="orange">{{ f }}</a-tag>
              </div>
              <div class="rd-branch rd-branch-removed">
                <span class="rd-branch-marker">−</span>
                <code>{{ b.old?.condition }}</code>
                <span class="rd-arrow">→</span>
                <span>{{ b.old?.conclusion }}</span>
                <span v-if="b.old?.action" class="rd-action">[{{ b.old.action }}]</span>
              </div>
              <div class="rd-branch rd-branch-added">
                <span class="rd-branch-marker">+</span>
                <code>{{ b.new?.condition }}</code>
                <span class="rd-arrow">→</span>
                <span>{{ b.new?.conclusion }}</span>
                <span v-if="b.new?.action" class="rd-action">[{{ b.new.action }}]</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 测试用例本身的增删 -->
      <div v-if="diff.test_cases_added?.length || diff.test_cases_removed?.length" class="rd-section">
        <div class="rd-section-head">
          <icon-experiment :size="14" />
          测试用例增删
        </div>
        <div v-if="diff.test_cases_added?.length" class="rd-list">
          <div v-for="n in diff.test_cases_added" :key="`tca-${n}`" class="rd-list-item rd-list-added">
            + {{ n }}
          </div>
        </div>
        <div v-if="diff.test_cases_removed?.length" class="rd-list">
          <div v-for="n in diff.test_cases_removed" :key="`tcr-${n}`" class="rd-list-item rd-list-removed">
            − {{ n }}
          </div>
        </div>
      </div>

      <!-- frontmatter 改动 -->
      <div v-if="frontmatterEntries.length" class="rd-section">
        <div class="rd-section-head">
          <icon-settings :size="14" />
          元信息修改
        </div>
        <div class="rd-list">
          <div v-for="entry in frontmatterEntries" :key="entry.key" class="rd-fm-item">
            <span class="rd-fm-key">{{ entry.key }}</span>
            <code class="rd-fm-old">{{ entry.old ?? '∅' }}</code>
            →
            <code class="rd-fm-new">{{ entry.new ?? '∅' }}</code>
          </div>
        </div>
      </div>

      <!-- output_definition flag -->
      <div v-if="diff.output_definition_changed" class="rd-section rd-section-warning">
        <icon-info-circle :size="14" />
        <span>输出定义有改动，下游消费者可能受影响</span>
      </div>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import {
  IconBranch,
  IconExclamationCircleFill,
  IconCheckCircle,
  IconThunderbolt,
  IconExperiment,
  IconSettings,
  IconInfoCircle,
} from '@arco-design/web-vue/es/icon'

const props = defineProps({
  visible: { type: Boolean, default: false },
  diff: { type: Object as PropType<any>, default: null },
})

defineEmits(['update:visible'])

const title = computed(() => {
  if (!props.diff) return '回归 diff'
  if (!props.diff.has_changes) return '回归 diff · 无变化'
  return '回归 diff · 改动影响一览'
})

const frontmatterEntries = computed(() => {
  if (!props.diff?.frontmatter_changes) return []
  return Object.entries(props.diff.frontmatter_changes).map(([key, val]) => ({
    key,
    old: (val as any)?.old,
    new: (val as any)?.new,
  }))
})

// [M10] 大数据量保护: 只展示前 50 步, 防止 DOM 节点爆炸
const displayedSteps = computed(() => {
  const steps = Array.isArray(props.diff?.steps) ? props.diff.steps : []
  return steps.slice(0, 50)
})

function stepTagColor(t: string) {
  return { added: 'green', removed: 'red', modified: 'orange', unchanged: 'gray' }[t] || 'gray'
}
function stepTagLabel(t: string) {
  return { added: '新增', removed: '删除', modified: '修改', unchanged: '无变化' }[t] || t
}
</script>

<style scoped>
.rd-empty {
  padding: 60px 24px;
  text-align: center;
  color: var(--ai-ink-3);
  font-size: 13px;
}
.rd-empty-error {
  color: var(--ai-bad);
}
.rd-error-detail {
  margin-top: 8px;
  font-size: 12px;
  color: var(--ai-ink-3);
  font-family: var(--ai-font-mono);
  word-break: break-word;
}
.rd-notes {
  margin: 0 0 12px;
  padding: 8px 12px;
  background: var(--ai-warn-soft);
  border-left: 3px solid var(--ai-warn);
  border-radius: 6px;
  font-size: 11px;
  color: var(--ai-ink-2);
  line-height: 1.6;
}
.rd-note-item {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 2px 0;
}
.rd-truncation-hint {
  margin: 0 0 12px;
  padding: 8px 12px;
  background: var(--ai-warn-soft);
  border: 1px dashed var(--ai-warn);
  border-radius: 6px;
  font-size: 11px;
  color: var(--ai-warn);
}
.rd-body { padding: 16px 20px 24px; }

.rd-summary {
  padding: 12px 14px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  margin-bottom: 16px;
}
.rd-summary-text {
  font-size: 13px;
  font-weight: 700;
  color: var(--ai-ink-1);
  line-height: 1.5;
}
.rd-summary-meta {
  margin-top: 4px;
  font-size: 11px;
  color: var(--ai-ink-3);
}
.rd-summary-meta code {
  background: rgba(0,0,0,0.04);
  padding: 0 4px;
  border-radius: 3px;
  font-size: 10px;
}

.rd-section {
  margin-bottom: 18px;
  padding: 12px 14px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: 8px;
}
.rd-section-affected {
  border-color: var(--ai-warn);
  background: var(--ai-warn-soft);
}
.rd-section-warning {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--ai-warn);
  font-size: 12px;
  font-weight: 700;
}
.rd-section-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 800;
  color: var(--ai-ink-1);
  margin-bottom: 10px;
}
.rd-section-hint {
  margin-top: 8px;
  font-size: 11px;
  color: var(--ai-ink-3);
  font-style: italic;
}

.rd-tests {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.rd-test-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  background: var(--ai-warn-soft);
  border-radius: 4px;
  font-size: 12px;
  color: var(--ai-ink-1);
  font-weight: 600;
}

.rd-step {
  padding: 11px 0;
  /* webkit 1px dashed 渲染成 solid，用 background gradient pattern 代替 */
  background-image: linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%);
  background-position: bottom;
  background-size: 6px 1px;
  background-repeat: repeat-x;
}
.rd-step:last-child { background-image: none; }
.rd-step-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 700;
  margin-bottom: 6px;
}
.rd-step-name { color: var(--ai-ink-1); }
.rd-step-rename {
  font-weight: 500;
  color: var(--ai-ink-3);
  font-size: 11px;
}

.rd-branch {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  margin: 2px 0;
  font-size: 11px;
  border-radius: 3px;
  font-family: var(--ai-font-mono);
}
.rd-branch code {
  background: transparent;
  padding: 0;
  font-size: 11px;
}
.rd-branch-added {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
}
.rd-branch-removed {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
}
.rd-branch-marker {
  font-weight: 800;
  width: 12px;
  text-align: center;
}
.rd-arrow { color: var(--ai-ink-3); }
.rd-action {
  background: var(--ai-surface);
  padding: 0 6px;
  border-radius: 3px;
  font-size: 10px;
  color: var(--ai-ink-2);
}

.rd-branch-mod {
  margin: 6px 0;
  padding: 6px 8px;
  background: var(--ai-warn-soft);
  border-left: 2px solid var(--ai-warn);
  border-radius: 0 4px 4px 0;
}
.rd-mod-fields {
  font-size: 11px;
  color: var(--ai-ink-3);
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 4px;
}

.rd-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.rd-list-item {
  font-size: 11px;
  font-family: var(--ai-font-mono);
  padding: 3px 8px;
  border-radius: 3px;
}
.rd-list-added { background: var(--ai-ok-soft); color: var(--ai-ok); }
.rd-list-removed { background: var(--ai-bad-soft); color: var(--ai-bad); }

.rd-fm-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
  font-size: 11px;
}
.rd-fm-key {
  font-weight: 700;
  color: var(--ai-ink-2);
  min-width: 80px;
}
.rd-fm-old {
  color: var(--ai-bad);
  background: var(--ai-bad-soft);
  padding: 1px 5px;
  border-radius: 3px;
}
.rd-fm-new {
  color: var(--ai-ok);
  background: var(--ai-ok-soft);
  padding: 1px 5px;
  border-radius: 3px;
}
</style>
