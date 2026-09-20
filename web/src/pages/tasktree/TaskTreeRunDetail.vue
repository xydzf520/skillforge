<template>
  <article
    class="tt-run-detail"
    :class="[{ compact, selected }]"
    :data-testid="compact ? 'tasktree-run-compact' : 'tasktree-run-detail'"
    @click="compact ? $emit('select') : undefined"
  >
    <template v-if="compact">
      <div class="tt-run-detail-top">
        <div class="tt-run-detail-title">
          <strong>{{ run.skill_name }}</strong>
          <a-tag size="small" :color="runStatusColor(run.status)">{{ runStatusLabel(run.status) }}</a-tag>
        </div>
        <span v-if="run.started_at">{{ formatTime(run.started_at) }}</span>
      </div>
      <div class="tt-run-detail-foot">
        <span v-if="run.duration_seconds != null">{{ formatDuration(run.duration_seconds) }}</span>
        <span v-else>-</span>
        <span v-if="writebackText(run.writeback)" class="tt-writeback-link" @click.stop="$emit('show-chain', run.run_id)">
          {{ writebackText(run.writeback) }}
        </span>
      </div>
      <div v-if="showActions" class="tt-run-detail-actions">
        <a-button size="mini" type="outline" @click.stop="$emit('edit-skill', run.skill_id)">编辑 Skill</a-button>
        <a-button size="mini" type="outline" @click.stop="$emit('open-history', run.skill_id)">查看历史</a-button>
        <a-button size="mini" type="outline" @click.stop="$emit('show-chain', run.run_id)">回写链路</a-button>
      </div>
    </template>

    <template v-else>
      <div class="tt-run-detail-top">
        <div class="tt-run-detail-title">
          <strong>{{ run.skill_name }}</strong>
          <a-tag size="small" :color="runStatusColor(run.status)">{{ runStatusLabel(run.status) }}</a-tag>
        </div>
        <span v-if="run.started_at">{{ formatTime(run.started_at) }}</span>
      </div>

      <a-descriptions :column="1" size="small" class="tt-desc">
        <a-descriptions-item label="Skill">{{ run.skill_name }}</a-descriptions-item>
        <a-descriptions-item label="状态">{{ runStatusLabel(run.status) }}</a-descriptions-item>
        <a-descriptions-item label="触发方式">{{ run.trigger_type || '-' }}</a-descriptions-item>
        <a-descriptions-item label="开始时间">{{ formatTime(run.started_at) }}</a-descriptions-item>
        <a-descriptions-item label="耗时">{{ formatDuration(run.duration_seconds) }}</a-descriptions-item>
        <a-descriptions-item label="错误">{{ run.error_message || '-' }}</a-descriptions-item>
      </a-descriptions>

      <div class="tt-json-grid">
        <div>
          <div class="tt-mini-label">输入摘要</div>
          <JsonPreview :data="run.input_summary" :max-height="220" />
        </div>
        <div>
          <div class="tt-mini-label">输出摘要</div>
          <JsonPreview :data="run.output_summary" :max-height="220" />
        </div>
      </div>

      <div class="tt-expanded-footer">
        <div>
          <div class="tt-mini-label">回写状态</div>
          <a-tag :color="writebackColor(run.writeback)">{{ writebackText(run.writeback) || '无回写' }}</a-tag>
        </div>
        <a-space wrap>
          <a-button
            v-if="run.status === 'failed'"
            size="mini"
            type="outline"
            :loading="diagnosisLoading"
            @click.stop="$emit('diagnose-run', run.run_id)"
          >
            智能诊断
          </a-button>
          <a-button
            v-if="run.status === 'failed' || run.status === 'completed'"
            size="mini"
            type="outline"
            @click.stop="$emit('retry-skill', run.skill_id)"
          >
            一键重试
          </a-button>
          <a-button size="mini" type="outline" @click.stop="$emit('edit-skill', run.skill_id)">编辑 Skill</a-button>
          <a-button size="mini" type="outline" @click.stop="$emit('open-history', run.skill_id)">查看历史</a-button>
          <a-button size="mini" type="outline" @click.stop="$emit('show-chain', run.run_id)">回写链路</a-button>
        </a-space>
      </div>

      <div v-if="run.status === 'failed' && diagnosis" class="tt-diagnosis">
        <div class="tt-mini-label">智能诊断</div>
        <div class="tt-diagnosis-text">{{ diagnosis }}</div>
      </div>

      <div v-if="skillValue || skillValueLoading" class="tt-value-panel">
        <div class="tt-mini-label">价值归因</div>
        <a-spin :loading="skillValueLoading">
          <template v-if="skillValue">
            <div class="tt-value-grid">
              <span>节省工时 {{ skillValue.saved_hours ?? 0 }}h</span>
              <span>估算收益 {{ skillValue.estimated_cost_saving ?? 0 }}</span>
              <span>风险规避 {{ skillValue.risk_events_prevented ?? 0 }}</span>
            </div>
            <div v-if="skillValue.recommendation" class="tt-value-recommendation">
              {{ skillValue.recommendation }}
            </div>
          </template>
        </a-spin>
      </div>
    </template>
  </article>
</template>

<script setup lang="ts">
import { formatTime } from '@/utils/format'
import JsonPreview from '@/components/JsonPreview.vue'
import { formatDuration, runStatusColor, runStatusLabel, writebackColor, writebackText } from './helpers'
import type { TaskTreeSkillRunItem, TaskTreeSkillValueResponse } from './types'

withDefaults(defineProps<{
  run: TaskTreeSkillRunItem
  compact?: boolean
  selected?: boolean
  showActions?: boolean
  diagnosis?: string
  diagnosisLoading?: boolean
  skillValue?: TaskTreeSkillValueResponse | null
  skillValueLoading?: boolean
}>(), {
  compact: false,
  selected: false,
  showActions: true,
  diagnosis: '',
  diagnosisLoading: false,
  skillValue: null,
  skillValueLoading: false,
})

defineEmits<{
  select: []
  'show-chain': [runId: string]
  'edit-skill': [skillId: string]
  'open-history': [skillId: string]
  'diagnose-run': [runId: string]
  'retry-skill': [skillId: string]
}>()
</script>

<style scoped>
/* 设计稿 .ai-card：surface + border + radius 8px，无 gradient/大阴影 */
.tt-run-detail {
  padding: 12px 14px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  box-shadow: none;
  transition: background 0.15s ease, border-color 0.15s ease;
  font-family: var(--ai-font-sans);
}

.tt-run-detail.compact {
  cursor: pointer;
}

.tt-run-detail:hover,
.tt-run-detail.selected {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}

.tt-run-detail.selected {
  border-color: var(--ai-ink-1);
}

.tt-run-detail-top,
.tt-run-detail-foot,
.tt-expanded-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.tt-run-detail-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.tt-run-detail-title strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ai-ink-1);
  font-weight: 500;
  font-size: 13px;
  letter-spacing: -0.005em;
}

.tt-run-detail-top > span {
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

.tt-run-detail-foot {
  margin-top: 6px;
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

.tt-run-detail-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 10px;
}

.tt-desc {
  margin-top: 10px;
}

.tt-json-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 10px;
}

.tt-mini-label {
  margin-bottom: 6px;
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.tt-expanded-footer {
  margin-top: 12px;
  align-items: flex-end;
}

.tt-diagnosis {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: var(--ai-radius-s);
  background: var(--ai-warn-soft);
  border: 1px solid var(--ai-warn-soft);
}

.tt-diagnosis-text {
  color: var(--ai-ink-2);
  line-height: 1.65;
  white-space: pre-wrap;
  font-size: 12.5px;
}

.tt-value-panel {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: var(--ai-radius-s);
  background: var(--ai-info-soft);
  border: 1px solid var(--ai-info-soft);
}

.tt-value-grid {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  color: var(--ai-ink-2);
  font-size: 12.5px;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

.tt-value-recommendation {
  margin-top: 8px;
  color: var(--ai-ink-2);
  line-height: 1.65;
  white-space: pre-wrap;
  font-size: 12.5px;
}

.tt-writeback-link {
  color: var(--ai-accent);
  cursor: pointer;
}
.tt-writeback-link:hover {
  color: var(--ai-accent-ink);
}
</style>
