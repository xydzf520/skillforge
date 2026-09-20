<template>
  <div class="tt-runchain-wrap" data-testid="tasktree-detail-runchain">
    <section class="tt-runs-card ai-card">
      <div class="ai-card-h tt-card-title">
        <span class="t">执行链路</span>
        <span class="s">活跃 + 最近完成</span>
      </div>

      <div class="tt-runs-body">
      <template v-if="instance">
        <div class="tt-section">
          <div class="tt-section-head">
            <h4>活跃执行</h4>
            <span>{{ instance.active_runs?.length || 0 }}</span>
          </div>
          <div v-if="instance.active_runs?.length" class="tt-run-detail-list">
            <TaskTreeRunDetail
              v-for="run in instance.active_runs"
              :key="run.run_id"
              :run="run"
              compact
              show-actions
              @select="$emit('select-run', instance, run)"
              @show-chain="$emit('show-chain', $event)"
              @edit-skill="$emit('edit-skill', $event)"
              @open-history="$emit('open-history', $event)"
            />
          </div>
          <SfEmptyState v-else description="没有活跃执行" />
        </div>

        <div class="tt-section">
          <div class="tt-section-head">
            <h4>最近完成</h4>
            <span>{{ instance.recent_completed?.length || 0 }}</span>
          </div>
          <div v-if="instance.recent_completed?.length" class="tt-run-detail-list">
            <TaskTreeRunDetail
              v-for="run in instance.recent_completed"
              :key="run.run_id"
              :run="run"
              compact
              :selected="selectedRunId === run.run_id"
              :show-actions="false"
              @select="$emit('select-run', instance, run)"
              @show-chain="$emit('show-chain', $event)"
              @edit-skill="$emit('edit-skill', $event)"
              @open-history="$emit('open-history', $event)"
            />
          </div>
          <SfEmptyState v-else description="暂无最近完成" />
        </div>
      </template>

      <SfEmptyState v-else description="选择节点后展示执行链路" />
      </div>
    </section>

    <!-- 选中 run 的详情卡（耗时 / 输入输出 / 回写 / 重试） -->
    <TaskTreeRunDetail
      v-if="selectedRun"
      :run="selectedRun"
      :show-actions="false"
      :selected="true"
      @show-chain="$emit('show-chain', $event)"
      @edit-skill="$emit('edit-skill', $event)"
      @open-history="$emit('open-history', $event)"
      @retry-skill="$emit('retry-skill', $event)"
    />

    <!-- 决策→审批→待办→派发的回写链路时间轴 -->
    <TaskTreeChainView :chain="selectedChain" :loading="chainLoading" />
  </div>
</template>

<script setup lang="ts">
import { SfEmptyState } from '@/components/common'
import TaskTreeChainView from '../TaskTreeChainView.vue'
import TaskTreeRunDetail from '../TaskTreeRunDetail.vue'
import type {
  TaskTreeInstanceNode,
  TaskTreeRunChainResponse,
  TaskTreeSkillRunItem,
} from '../types'

defineProps<{
  instance: TaskTreeInstanceNode | null
  selectedRun: TaskTreeSkillRunItem | null
  selectedChain: TaskTreeRunChainResponse | null
  chainLoading: boolean
  selectedRunId: string
}>()

defineEmits<{
  'select-run': [instance: TaskTreeInstanceNode, run: TaskTreeSkillRunItem]
  'show-chain': [runId: string]
  'edit-skill': [skillId: string]
  'open-history': [skillId: string]
  'retry-skill': [skillId: string]
}>()
</script>

<style scoped>
.tt-runchain-wrap {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

.tt-runs-card {
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

.tt-runs-body {
  padding: 16px;
}

.tt-section {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--ai-border);
}

.tt-section:first-of-type {
  margin-top: 0;
  padding-top: 0;
  border-top: none;
}

.tt-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.tt-section-head h4 {
  margin: 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
}

.tt-section-head span {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--ai-ink-4);
  font-size: 12px;
}

.tt-run-detail-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
</style>
