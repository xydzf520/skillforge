<template>
  <a-modal
    v-model:visible="innerVisible"
    title="批量运行 · 多样本投选"
    :width="900"
    :mask-closable="!running"
    @cancel="onClose"
    :footer="false"
  >
    <!-- 配置 -->
    <div v-if="!batchId" class="config-row">
      <div>
        <div class="label">并发次数</div>
        <a-radio-group v-model="n" type="button" size="small">
          <a-radio :value="2">x2</a-radio>
          <a-radio :value="3">x3</a-radio>
          <a-radio :value="5">x5</a-radio>
        </a-radio-group>
      </div>
      <div>
        <div class="label">沙箱模式</div>
        <a-switch v-model="sandbox" />
      </div>
      <a-button
        type="primary"
        :loading="running"
        :disabled="!skillId"
        @click="runBatch"
      >
        <template #icon><icon-thunderbolt /></template>开跑
      </a-button>
    </div>

    <!-- 结果 tab -->
    <div v-else class="result-area">
      <div class="batch-header">
        <span class="batch-id">batch: {{ batchId.slice(0, 14) }}...</span>
        <a-tag size="small" color="arcoblue">{{ runs.length }} runs</a-tag>
        <a-tag v-if="winnerRunId" size="small" color="green">
          已选中 {{ winnerRunId.slice(0, 10) }}
        </a-tag>
        <a-button size="mini" @click="reset">清空重跑</a-button>
      </div>

      <a-tabs v-model:active-key="activeRunIdx" type="card">
        <a-tab-pane
          v-for="(run, idx) in runs"
          :key="idx"
          :title="runTabTitle(run, idx)"
        >
          <a-alert v-if="run.status === 'err'" type="error" show-icon>
            <template #title>运行 #{{ idx + 1 }} 失败</template>
            {{ runErrorText(run.error) }}
          </a-alert>
          <template v-else>
            <div class="run-header">
              <a-tag size="small" :color="run.run_id === winnerRunId ? 'green' : 'gray'">
                {{ run.run_id?.slice(0, 14) || 'pending' }}
              </a-tag>
              <a-button
                v-if="run.run_id && run.run_id !== winnerRunId"
                type="primary"
                size="small"
                @click="markWinner(run.run_id)"
                :loading="markingWinner"
              >
                <template #icon><icon-check /></template>采用此结果
              </a-button>
              <a-tag v-if="run.run_id === winnerRunId" size="small" color="green">
                ✓ 已选为冠军
              </a-tag>
            </div>
            <div class="run-output">
              <pre>{{ formatOutput(run.output) }}</pre>
            </div>
          </template>
        </a-tab-pane>
      </a-tabs>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { IconCheck, IconThunderbolt } from '@arco-design/web-vue/es/icon'
import { Message } from '@arco-design/web-vue'
import { skillApi } from '@/api'
import type {
  SkillExecuteBatchError,
  SkillExecuteBatchRun,
} from '@/api'
import { getErrorMessage } from '@/types/skillstudio'

const props = defineProps<{
  visible: boolean
  skillId: string
  params?: Record<string, unknown>
}>()

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'winner-selected', runId: string, batchId: string): void
}>()

const innerVisible = computed({
  get: () => props.visible,
  set: (v) => emit('update:visible', v),
})

const n = ref<2 | 3 | 5>(3)
const sandbox = ref(true)
const running = ref(false)
const markingWinner = ref(false)
const batchId = ref('')
const runs = ref<SkillExecuteBatchRun[]>([])
const activeRunIdx = ref(0)
const winnerRunId = ref('')

watch(
  () => props.visible,
  (v) => {
    if (!v) reset()
  },
)

function reset() {
  batchId.value = ''
  runs.value = []
  winnerRunId.value = ''
  activeRunIdx.value = 0
}

async function runBatch() {
  if (!props.skillId) return
  running.value = true
  try {
    const response = await skillApi.executeBatch(props.skillId, {
      params: props.params || {},
      sandbox: sandbox.value,
      n: n.value,
    })
    batchId.value = response.batch_id || ''
    runs.value = response.runs || []
    activeRunIdx.value = 0
    if (!runs.value.length) {
      Message.warning('后端返回空结果')
    }
  } catch (error) {
    Message.error(getErrorMessage(error, '批量运行失败'))
  } finally {
    running.value = false
  }
}

async function markWinner(runId: string) {
  markingWinner.value = true
  try {
    await skillApi.markWinner(runId, batchId.value)
    winnerRunId.value = runId
    Message.success('已选中为冠军')
    emit('winner-selected', runId, batchId.value)
  } catch (error) {
    Message.error(getErrorMessage(error, '选中失败'))
  } finally {
    markingWinner.value = false
  }
}

function onClose() {
  if (running.value) {
    Message.warning('正在运行，请稍候')
    return
  }
  emit('update:visible', false)
}

function runTabTitle(run: SkillExecuteBatchRun, idx: number): string {
  if (run.status === 'err') return `失败 #${idx + 1}`
  if (run.run_id === winnerRunId.value) return `已选 #${idx + 1}`
  return `候选 #${idx + 1}`
}

function formatOutput(output: unknown): string {
  if (output == null) return '(空)'
  if (typeof output === 'string') return output
  try {
    return JSON.stringify(output, null, 2)
  } catch {
    return String(output)
  }
}

function runErrorText(error: SkillExecuteBatchError | undefined): string {
  if (!error) return '未知错误'
  if (typeof error === 'string') return error
  return error.message || error.type || '未知错误'
}
</script>

<style scoped>
.config-row {
  display: flex;
  gap: 20px;
  align-items: flex-end;
  padding: 20px 0;
}
.label {
  font-size: 12px;
  color: var(--ai-ink-3);
  margin-bottom: 6px;
}
.result-area {
  padding: 8px 0;
}
.batch-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.batch-id {
  font-family: monospace;
  font-size: 12px;
  color: var(--ai-ink-3);
}
.run-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.run-output {
  background: var(--ai-surface-2);
  border-radius: 4px;
  padding: 12px;
  max-height: 400px;
  overflow: auto;
}
.run-output pre {
  margin: 0;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
