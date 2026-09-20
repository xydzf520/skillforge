<template>
  <section class="tt-diag-card ai-card" data-testid="tasktree-detail-diagnosis">
    <div class="ai-card-h tt-card-title">
      <span class="t">失败诊断</span>
        <a-tag
          v-if="showAiTag"
          size="small"
          :color="aiAvailable ? 'arcoblue' : 'gray'"
          data-testid="tasktree-diagnosis-source-tag"
        >
          {{ aiAvailable ? 'AI 诊断' : '规则引擎' }}
        </a-tag>
    </div>

    <div class="tt-diag-body">
    <template v-if="!run">
      <SfEmptyState description="选择一条运行记录查看诊断" />
    </template>
    <template v-else-if="run.status !== SkillRunStatus.FAILED">
      <SfEmptyState description="当前运行未失败，无需诊断" />
    </template>
    <template v-else>
      <div class="tt-diag-actions">
        <a-button
          size="mini"
          type="outline"
          :loading="loading"
          data-testid="tasktree-diagnose-btn"
          @click="$emit('diagnose-run', run.run_id)"
        >
          {{ diagnosis ? '重新诊断' : '智能诊断' }}
        </a-button>
      </div>

      <div v-if="loading" class="tt-diag-loading">
        <a-spin />
      </div>

      <template v-else-if="diagnosis">
        <div
          v-if="!aiAvailable"
          class="tt-diag-fallback-tip"
          data-testid="tasktree-diagnosis-fallback-tip"
        >
          AI 服务不可用，当前展示规则引擎结果。
        </div>
        <div class="tt-diag-text">{{ diagnosis }}</div>
      </template>

      <SfEmptyState v-else description="点击「智能诊断」生成分析结论" />
    </template>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { SfEmptyState } from '@/components/common'
import { SkillRunStatus, type TaskTreeSkillRunItem } from '../types'

const props = withDefaults(
  defineProps<{
    run: TaskTreeSkillRunItem | null
    diagnosis: string
    /**
     * 后端（Group B）填充；未声明 / true 都视为 AI 诊断；
     * 仅当显式 false 时显示规则引擎兜底提示。
     */
    aiAvailable?: boolean
    loading?: boolean
  }>(),
  {
    aiAvailable: true,
    loading: false,
  },
)

defineEmits<{
  'diagnose-run': [runId: string]
}>()

// 只在失败且已有 diagnosis 文本时显示 tag，避免空状态下误导
const showAiTag = computed(() => {
  return props.run?.status === SkillRunStatus.FAILED && Boolean(props.diagnosis)
})
</script>

<style scoped>
.tt-diag-card {
  min-width: 0;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  box-shadow: none;
  overflow: hidden;
}

.tt-card-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 500;
  font-size: 13px;
  color: var(--ai-ink-1);
}

.tt-diag-body {
  padding: 16px;
}

.tt-diag-actions {
  margin-bottom: 12px;
}

.tt-diag-loading {
  display: flex;
  justify-content: center;
  padding: 16px 0;
}

.tt-diag-fallback-tip {
  margin-bottom: 10px;
  padding: 8px 10px;
  border-radius: var(--ai-radius-s);
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: 12px;
}

.tt-diag-text {
  padding: 12px;
  border-radius: var(--ai-radius-s);
  background: var(--ai-warn-soft);
  border: 1px solid var(--ai-warn-soft);
  color: var(--ai-ink-2);
  line-height: 1.65;
  white-space: pre-wrap;
  font-size: 12.5px;
}
</style>
