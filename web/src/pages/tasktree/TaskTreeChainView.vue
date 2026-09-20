<template>
  <section v-if="chain" class="tt-chain-card ai-card" data-testid="tasktree-chain">
    <div class="ai-card-h tt-card-title">
      <span class="t">回写链路</span>
      <span class="s">{{ chain.run_id }}</span>
    </div>

    <div v-if="loading" class="tt-chain-loading">
      <a-spin />
    </div>

    <div v-else class="tt-chain-body">
    <TaskTreeTrainingSummary :jobs="chain.training_jobs || []" />
    <a-timeline class="tt-timeline">
      <a-timeline-item
        v-for="step in chain.chain || []"
        :key="`${step.type}-${step.id}`"
        :dot-color="chainStepColor(step.status)"
      >
        <div class="tt-chain-step">
          <div class="tt-chain-head">
            <strong>{{ step.type }}</strong>
            <a-tag size="small" :color="chainStepColor(step.status)">{{ step.status }}</a-tag>
          </div>
          <div class="tt-chain-title">{{ step.title || '-' }}</div>
          <div class="tt-chain-meta">
            <span v-if="step.assignee">负责人 {{ step.assignee }}</span>
            <span v-if="step.created_at">创建于 {{ formatTime(step.created_at) }}</span>
            <span v-if="step.decided_at">决策于 {{ formatTime(step.decided_at) }}</span>
          </div>
        </div>
      </a-timeline-item>
    </a-timeline>
    <a-tag v-if="chain.chain_complete" color="green">链路闭环</a-tag>
    </div>
  </section>
</template>

<script setup lang="ts">
import { formatTime } from '@/utils/format'
import TaskTreeTrainingSummary from './TaskTreeTrainingSummary.vue'
import type { TaskTreeRunChainResponse } from './types'

defineProps<{
  chain: TaskTreeRunChainResponse | null
  loading?: boolean
}>()

function chainStepColor(status?: string): string {
  if (status === 'pending') return 'orange'
  if (status === 'approved' || status === 'dispatched' || status === 'completed' || status === 'done') return 'green'
  if (status === 'rejected' || status === 'failed') return 'red'
  return 'gray'
}
</script>

<style scoped>
.tt-chain-card {
  min-width: 0;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  box-shadow: none;
  overflow: hidden;
}

.tt-card-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  font-weight: 500;
  font-size: 13px;
  color: var(--ai-ink-1);
}
.tt-card-title .s {
  font-family: var(--ai-font-mono);
}

.tt-chain-body {
  padding: 16px;
}

.tt-chain-loading {
  display: grid;
  place-items: center;
  min-height: 120px;
}

.tt-chain-meta {
  color: var(--ai-ink-4);
  font-size: 11.5px;
}

.tt-timeline {
  margin-top: 4px;
}

.tt-chain-step {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tt-chain-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.tt-chain-head strong {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
}

.tt-chain-title {
  color: var(--ai-ink-1);
  font-size: 12.5px;
}

.tt-chain-meta {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
</style>
