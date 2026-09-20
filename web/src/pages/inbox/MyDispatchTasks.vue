<template>
  <div class="page-container">
    <div class="page-header page-detail-toolbar">
      <a-button size="small" @click="router.push('/inbox?tab=pending')">
        <template #icon><icon-left /></template>返回收件中心
      </a-button>
      <h2 class="page-title" style="margin-top: 6px">我收到的派发任务</h2>
      <a-button @click="loadAll">刷新</a-button>
    </div>

    <div class="dispatch-body">
      <div class="filter-bar">
        <a-radio-group v-model="statusFilter" type="button" @change="onFilterChange">
          <a-radio value="">全部</a-radio>
          <a-radio value="sent">待完成</a-radio>
          <a-tooltip content="推送未到达你的 IM（钉钉离线 / 关闭通知 / 网络异常等）">
            <a-radio value="pushed_no_dingtalk">通知未触达</a-radio>
          </a-tooltip>
          <a-radio value="done">已完成</a-radio>
          <a-radio value="cancelled">已取消</a-radio>
        </a-radio-group>
      </div>

      <div v-if="focusTaskId" class="focus-note">
        已定位到旧链接中的任务 #{{ focusTaskId }}。
      </div>

      <a-spin :loading="loading" style="width: 100%">
        <div class="dispatch-grid">
          <DispatchCard
            v-for="record in items"
            :key="record.id"
            :item="record"
            :is-focus="record.id === focusTaskId"
            @ack="ack"
          />
        </div>
        <SfEmptyState
          v-if="!loading && items.length === 0"
          icon="send"
          title="没有派发给你的任务"
          description="当前没有需要你执行或反馈的派发任务"
          hint="派发任务 = 有人把一条自动执行的任务分给你复核/决策。等上级派发或系统触发时，这里会自动出现通知。"
        />
      </a-spin>

      <div class="pagination-wrap">
        <a-pagination
          :current="pagination.current"
          :page-size="pagination.pageSize"
          :total="pagination.total"
          @change="onPageChange"
        />
      </div>
    </div>

    <a-modal
      v-model:visible="ackModalVisible"
      title="填写完成说明"
      :footer="false"
      :width="520"
      :mask-closable="!ackSubmitting"
      @cancel="resetAckModal"
    >
      <div v-if="ackTarget" class="ack-modal-body">
        <div class="ack-task-title">{{ ackTarget.title || `任务 #${ackTarget.id}` }}</div>
        <div v-if="ackTarget.content" class="ack-task-content">{{ ackTarget.content }}</div>
        <a-textarea
          v-model="ackNote"
          :max-length="1000"
          show-word-limit
          :auto-size="{ minRows: 4, maxRows: 8 }"
          placeholder="填写实际处理结果，最多 1000 字"
        />
        <div v-if="ackNoteError" class="ack-note-error">{{ ackNoteError }}</div>
        <div class="ack-modal-actions">
          <a-button @click="resetAckModal">取消</a-button>
          <a-button type="primary" :loading="ackSubmitting" @click="submitAck">标记完成</a-button>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconLeft } from '@arco-design/web-vue/es/icon'
import { useRoute, useRouter } from 'vue-router'
import { todoApi } from '@/api'
import { readQueryText } from './presentation'
import DispatchCard from './DispatchCard.vue'
import { SfEmptyState } from '@/components/common'

const route: any = useRoute()
const router: any = useRouter()

const loading = ref(false)
const items = ref<any[]>([])
const statusFilter = ref('sent')
const pagination = ref({ current: 1, pageSize: 20, total: 0 })
const focusTaskId = computed(() => Number(readQueryText(route.query.focus)) || 0)
const ackModalVisible = ref(false)
const ackTarget = ref<any | null>(null)
const ackNote = ref('')
const ackSubmitting = ref(false)
const ackNoteError = computed(() => {
  if (ackNote.value.length > 1000) return '完成说明最多 1000 字'
  return ''
})

async function loadAll() {
  loading.value = true
  try {
    const data: any = await todoApi.listMyDispatchTasks({
      status: statusFilter.value || undefined,
      page: pagination.value.current,
      page_size: pagination.value.pageSize,
    })
    items.value = data.items || []
    pagination.value.total = data.total || 0
  } catch (error: any) {
    Message.error(error._message || '加载派发任务失败')
  } finally {
    loading.value = false
  }
}

function ack(record: any) {
  ackTarget.value = record
  ackNote.value = ''
  ackModalVisible.value = true
}

function resetAckModal() {
  if (ackSubmitting.value) return
  ackModalVisible.value = false
  ackTarget.value = null
  ackNote.value = ''
}

async function submitAck() {
  const target = ackTarget.value
  if (!target) return
  const note = ackNote.value.trim()
  if (!note) {
    Message.warning('请填写完成说明')
    return
  }
  if (note.length > 1000) {
    Message.warning('完成说明最多 1000 字')
    return
  }
  ackSubmitting.value = true
  try {
    await todoApi.ackDispatchTask(target.id, { note })
    Message.success('已标记完成')
    ackModalVisible.value = false
    ackTarget.value = null
    ackNote.value = ''
    await loadAll()
  } catch (error: any) {
    Message.error(error._message || '操作失败')
  } finally {
    ackSubmitting.value = false
  }
}

function onFilterChange() {
  pagination.value.current = 1
  loadAll()
}

function onPageChange(page: number) {
  pagination.value.current = page
  loadAll()
}

onMounted(loadAll)
</script>

<style scoped>
.dispatch-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.filter-bar {
  padding: 12px 16px;
  border-radius: 12px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
}

.focus-note {
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(22, 93, 255, 0.08);
  color: #124ec7;
  font-size: 13px;
  font-weight: 700;
}

.dispatch-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 420px));
  gap: 14px;
  justify-content: start;
}

.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 4px;
}

.ack-modal-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.ack-task-title {
  color: var(--ai-ink-1);
  font-size: 15px;
  font-weight: 800;
  line-height: 1.45;
}

.ack-task-content {
  max-height: 96px;
  overflow: auto;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-size: 13px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.ack-note-error {
  color: var(--ai-bad);
  font-size: 12px;
  font-weight: 700;
}

.ack-modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}
</style>
