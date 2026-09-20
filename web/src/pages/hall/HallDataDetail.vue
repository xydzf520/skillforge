<template>
  <div class="page-container">
    <HallDetailHeader
      :crumbs="[
        { label: '能力大厅', to: '/hall' },
        { label: '数据', to: '/hall?view=type&tab=data' },
        { label: source?.name || source?.id || '数据能力详情' },
      ]"
      :title="source?.name || source?.id || '数据能力详情'"
    />

    <a-spin :loading="loading" tip="加载中...">
      <div v-if="source" class="detail-body">
        <a-card class="detail-section" title="基础信息">
          <a-descriptions :data="basicInfo" :column="2" size="medium" layout="inline-horizontal" />
          <div v-if="source.usage_hint" class="usage-hint">
            <icon-bulb />{{ source.usage_hint }}
          </div>
        </a-card>

        <a-card class="detail-section" title="字段结构（schema）">
          <template #extra>
            <a-button
              v-if="source.has_sensitive_fields && !source.schema_unmasked && canUnmask"
              size="small"
              type="outline"
              status="warning"
              @click="toggleUnmask"
            >
              <template #icon><icon-eye /></template>显示敏感字段
            </a-button>
            <a-tag v-else-if="source.schema_unmasked" color="orange" size="medium">
              已解除脱敏 · 已审计
            </a-tag>
          </template>
          <a-empty
            v-if="!(source.schema_preview && source.schema_preview.length)"
            description="未登记字段 schema"
          />
          <a-table
            v-else
            :data="source.schema_preview"
            :pagination="false"
            row-key="field"
            size="small"
          >
            <template #columns>
              <a-table-column title="字段" data-index="field" />
              <a-table-column title="类型" data-index="type" />
              <a-table-column title="说明" data-index="desc">
                <template #cell="{ record }">
                  {{ record.desc || record.description || '-' }}
                  <a-tag v-if="record.sensitive" color="red" size="small" class="schema-sensitive-tag"
                    >敏感</a-tag
                  >
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-card>

        <a-card class="detail-section" title="被哪些 Skill 使用">
          <a-empty v-if="!consumers.length" description="暂无 Skill 使用此数据源" />
          <div v-else class="consumers-list">
            <div
              v-for="c in consumers"
              :key="c.id"
              class="consumer-item"
              @click="goSkill(router, c.id, c)"
            >
              <icon-tool />
              <span class="consumer-name">{{ c.name || c.id }}</span>
              <a-tag size="small" color="arcoblue">{{ c.department || '-' }}</a-tag>
              <a-tag size="small" :color="statusColor(c.status)">{{ c.status || '-' }}</a-tag>
            </div>
          </div>
        </a-card>

        <a-card class="detail-section" title="我的访问权限">
          <div class="access-summary" :class="accessClass">
            <template v-if="accessStatus === 'granted'">
              <icon-check-circle-fill class="access-icon access-icon-granted" />
              已授权
              <span v-if="expiresAt"> · 有效期至 {{ expiresAt }}</span>
              <a-button
                class="access-action-button"
                size="small"
                type="outline"
                @click="goPreview"
              >
                查看数据
              </a-button>
            </template>
            <template v-else-if="accessStatus === 'pending'">
              <icon-clock-circle class="access-icon access-icon-pending" />
              申请审批中
            </template>
            <template v-else>
              <icon-info-circle class="access-icon access-icon-none" />
              未授权
              <a-button
                class="access-action-button"
                size="small"
                type="primary"
                @click="openRequest"
              >
                申请访问
              </a-button>
            </template>
          </div>
        </a-card>
      </div>
    </a-spin>

    <DataAccessRequestModal
      v-model:visible="requestModalVisible"
      :source-id="sourceId"
      :source-name="source?.name || sourceId"
      :owner-contact="source?.owner_contact"
      @submitted="onRequestSubmitted"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  IconBulb,
  IconCheckCircleFill,
  IconClockCircle,
  IconEye,
  IconInfoCircle,
  IconTool,
} from '@arco-design/web-vue/es/icon'
import { hallApi } from '@/api'
import { goSkill } from '@/utils/goSkill'
import { confirmSensitiveReveal } from '@/utils/confirmDelete'
import { formatDate } from '@/utils/format'
import DataAccessRequestModal from '@/components/hall/DataAccessRequestModal.vue'
import HallDetailHeader from '@/components/hall/HallDetailHeader.vue'

const route = useRoute()
const router = useRouter()

const sourceId = String(route.params.id)
const loading = ref(false)
const source = ref<any>(null)
const requestModalVisible = ref(false)

const unmasked = ref(false)

async function load() {
  loading.value = true
  try {
    const params = unmasked.value ? { unmask: true } : undefined
    source.value = await hallApi.dataDetail(sourceId, params)
  } catch (e: any) {
    source.value = null
  } finally {
    loading.value = false
  }
}

const canUnmask = computed(() => {
  const status = source.value?.my_access?.status
  return status === 'granted'
})

async function toggleUnmask() {
  if (!canUnmask.value) return
  const sourceName = source.value?.name || source.value?.id
  confirmSensitiveReveal(async () => {
    unmasked.value = true
    await load()
  }, {
    content: sourceName
      ? `将按已授权身份读取「${sourceName}」未脱敏 schema，并写入审计日志。仅在明确需要核对敏感字段时继续。`
      : undefined,
  })
}

const basicInfo = computed(() => {
  if (!source.value) return []
  const visMap: Record<string, string> = {
    company: '全公司',
    department: '部门内',
    private: '私有',
  }
  return [
    { label: 'ID', value: source.value.id },
    { label: '负责部门', value: source.value.department || '-' },
    { label: '负责人', value: source.value.owner_contact || '-' },
    { label: '可见性', value: visMap[source.value.visibility] || source.value.visibility },
    { label: '类型', value: source.value.source_type || '-' },
    {
      label: '说明',
      value: source.value.description || '暂无说明',
    },
  ]
})

const consumers = computed(() => (source.value && Array.isArray(source.value.consumers)) ? source.value.consumers : [])

const accessStatus = computed(() => source.value?.my_access?.status || 'none')
const accessClass = computed(() => ({
  'status-granted': accessStatus.value === 'granted',
  'status-pending': accessStatus.value === 'pending',
  'status-none': accessStatus.value === 'none',
}))
const expiresAt = computed(() => {
  const ex = source.value?.my_access?.expires_at
  if (!ex) return ''
  const formatted = formatDate(ex)
  return formatted === '-' ? String(ex) : formatted
})

function statusColor(s: string) {
  const m: Record<string, string> = {
    active: 'green',
    shadow: 'arcoblue',
    draft: 'gray',
    deprecated: 'red',
  }
  return m[s] || 'gray'
}

function openRequest() {
  requestModalVisible.value = true
}

function goPreview() {
  // 现有数据源预览页
  router.push(`/datasources/${sourceId}`)
}

function onRequestSubmitted(_id: number) {
  load()
}

onMounted(load)
</script>

<style scoped>
.detail-body {
  display: flex;
  flex-direction: column;
  gap: var(--sf-spacing-lg);
}
.detail-section {
  border-radius: var(--ai-radius);
}
.usage-hint {
  margin-top: var(--sf-spacing-md);
  padding: var(--sf-spacing-sm) var(--sf-spacing-md);
  background: var(--ai-surface-2);
  border-radius: var(--ai-radius);
  display: flex;
  gap: var(--sf-spacing-sm);
  align-items: start;
  color: var(--ai-ink-2);
  font-size: var(--sf-font-sm);
}
.schema-sensitive-tag {
  margin-left: var(--sf-spacing-xs);
}
.consumers-list {
  display: flex;
  flex-direction: column;
  gap: var(--sf-spacing-sm);
}
.consumer-item {
  display: flex;
  align-items: center;
  gap: var(--sf-spacing-sm);
  padding: var(--sf-spacing-sm) var(--sf-spacing-md);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  cursor: pointer;
  transition: background var(--sf-transition-fast);
}
.consumer-item:hover {
  background: var(--ai-surface-2);
}
.consumer-name {
  font-weight: 600;
  color: var(--ai-ink-1);
}
.access-summary {
  display: flex;
  align-items: center;
  gap: var(--sf-spacing-sm);
  font-size: var(--sf-font-md);
  padding: var(--sf-spacing-sm) 0;
}
.access-icon-granted {
  color: var(--ai-ok);
}
.access-icon-pending {
  color: var(--ai-warn);
}
.access-icon-none {
  color: var(--ai-ink-4);
}
.access-action-button {
  margin-left: var(--sf-spacing-lg);
}
</style>
