<template>
  <a-modal
    :visible="visible"
    title="Skill 权限"
    :width="'min(92vw, 820px)'"
    :footer="false"
    :unmount-on-close="true"
    @cancel="close"
  >
    <a-spin :loading="loading" style="width: 100%">
      <div class="perm-modal">
        <section class="perm-section">
          <div class="section-head">
            <div>
              <div class="section-title">可见范围</div>
              <div class="section-desc">{{ visibilityDesc }}</div>
            </div>
            <a-tag :color="canManage ? 'green' : 'gray'">{{ canManage ? '可管理' : '只读' }}</a-tag>
          </div>
          <div class="visibility-row">
            <a-select v-model="visibilityDraft" :disabled="!canManage" size="small" style="width: 220px">
              <a-option v-for="item in visibilityOptions" :key="item.value" :value="item.value">
                {{ item.label }}
              </a-option>
            </a-select>
            <a-button
              size="small"
              type="primary"
              :disabled="!canManage || visibilityDraft === visibility"
              :loading="savingVisibility"
              @click="saveVisibility"
            >
              保存范围
            </a-button>
          </div>
        </section>

        <section class="perm-section">
          <div class="section-head">
            <div>
              <div class="section-title">成员权限</div>
              <div class="section-desc">owner 可管理权限；editor 可编辑；reviewer 可审核；viewer 只读运行。</div>
            </div>
            <a-button size="small" :loading="loading" @click="loadAll">刷新</a-button>
          </div>

          <div v-if="canManage" class="add-row">
            <a-select
              v-model="memberForm.user_id"
              allow-search
              :filter-option="false"
              placeholder="搜索用户"
              size="small"
              style="min-width: 260px"
              @search="loadCandidates"
              @dropdown-visible-change="handleCandidateDropdown"
            >
              <a-option v-for="user in candidateOptions" :key="user.id" :value="user.id">
                {{ user.name || user.id }} · {{ user.department || '未指定部门' }}
              </a-option>
            </a-select>
            <a-select v-model="memberForm.role" size="small" style="width: 140px">
              <a-option v-for="item in roleOptions" :key="item.value" :value="item.value">
                {{ item.label }}
              </a-option>
            </a-select>
            <a-button type="primary" size="small" :loading="savingMember" @click="saveMember()">
              添加成员
            </a-button>
          </div>

          <a-table
            :data="members"
            :pagination="false"
            row-key="user_id"
            size="small"
            :bordered="false"
          >
            <a-table-column title="成员" :width="240">
              <template #cell="{ record }">
                <div class="member-cell">
                  <div class="member-name">{{ record.name || record.user_id }}</div>
                  <div class="member-id">{{ record.user_id }}</div>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="角色" :width="170">
              <template #cell="{ record }">
                <a-select
                  v-if="canManage"
                  :model-value="record.role"
                  size="mini"
                  @change="handleRoleChange(record, $event)"
                >
                  <a-option v-for="item in roleOptions" :key="item.value" :value="item.value">
                    {{ item.label }}
                  </a-option>
                </a-select>
                <a-tag v-else size="small" color="arcoblue">{{ roleLabel(record.role) }}</a-tag>
              </template>
            </a-table-column>
            <a-table-column title="授权人" data-index="granted_by" :width="120" />
            <a-table-column title="授权时间" :width="170">
              <template #cell="{ record }">{{ formatTime(record.granted_at) }}</template>
            </a-table-column>
            <a-table-column v-if="canManage" title="操作" :width="90" align="center">
              <template #cell="{ record }">
                <a-popconfirm content="移除该成员？" @ok="removeMember(record.user_id)">
                  <a-button type="text" status="danger" size="mini">移除</a-button>
                </a-popconfirm>
              </template>
            </a-table-column>
          </a-table>

          <a-empty v-if="!members.length && !loading" description="暂无成员" />
        </section>
      </div>
    </a-spin>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { skillApi } from '@/api'
import { formatTime as formatBjtTime } from '@/utils/format'
import { getErrorMessage } from '@/types/skillstudio'

type SkillMemberRow = {
  user_id: string
  name?: string
  role: string
  granted_by?: string
  granted_at?: string
}

type CandidateUser = {
  id: string
  name?: string
  username?: string
  role?: string
  department?: string
}

const props = defineProps<{
  visible: boolean
  skillId: string
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'changed'): void
}>()

const loading = ref(false)
const savingVisibility = ref(false)
const savingMember = ref(false)
const members = ref<SkillMemberRow[]>([])
const candidates = ref<CandidateUser[]>([])
const visibility = ref('department')
const visibilityDraft = ref('department')
const permissions = ref<Record<string, boolean>>({})
const memberForm = reactive({ user_id: '', role: 'viewer' })

const roleOptions = [
  { value: 'owner', label: 'Owner' },
  { value: 'editor', label: 'Editor' },
  { value: 'reviewer', label: 'Reviewer' },
  { value: 'viewer', label: 'Viewer' },
]

const visibilityOptions = [
  { value: 'company', label: '全公司可见' },
  { value: 'department', label: '本部门可见' },
  { value: 'private', label: '仅成员可见' },
]

const canManage = computed(() => Boolean(permissions.value.manage_members))
const candidateOptions = computed(() =>
  candidates.value.filter(user => !members.value.some(member => member.user_id === user.id)),
)
const visibilityDesc = computed(() => {
  if (visibilityDraft.value === 'company') return '所有 active 用户都能看到这个 Skill。'
  if (visibilityDraft.value === 'private') return '只有成员和系统管理员能看到这个 Skill。'
  return '组织范围内用户可见，历史数据按部门名称兜底匹配。'
})

watch(() => props.visible, (value) => {
  if (value) loadAll()
})

function close() {
  emit('update:visible', false)
}

function roleLabel(role: string) {
  return roleOptions.find(item => item.value === role)?.label || role
}

function formatTime(value?: string) {
  if (!value) return '-'
  const formatted = formatBjtTime(value)
  return formatted === '-' ? value : formatted
}

async function loadMembers() {
  if (!props.skillId) return
  const res: any = await skillApi.listMembers(props.skillId)
  members.value = Array.isArray(res?.items) ? res.items : []
  visibility.value = String(res?.visibility || 'department')
  visibilityDraft.value = visibility.value
  permissions.value = res?.permissions && typeof res.permissions === 'object' ? res.permissions : {}
}

async function loadCandidates(q = '') {
  if (!props.skillId) return
  try {
    const res: any = await skillApi.memberCandidates(props.skillId, { q, limit: 60 })
    candidates.value = Array.isArray(res?.items) ? res.items : []
  } catch (e) {
    Message.error(getErrorMessage(e, '加载候选用户失败'))
  }
}

function handleCandidateDropdown(open: boolean) {
  if (open) loadCandidates('')
}

function handleRoleChange(record: SkillMemberRow, role: string | number | boolean | Record<string, unknown>) {
  saveMember(record.user_id, String(role))
}

async function loadAll() {
  loading.value = true
  try {
    await loadMembers()
    if (canManage.value) await loadCandidates('')
  } catch (e) {
    Message.error(getErrorMessage(e, '加载权限失败'))
  } finally {
    loading.value = false
  }
}

async function saveVisibility() {
  if (!props.skillId || !canManage.value) return
  savingVisibility.value = true
  try {
    const res: any = await skillApi.updateVisibility(props.skillId, visibilityDraft.value)
    visibility.value = String(res?.visibility || visibilityDraft.value)
    visibilityDraft.value = visibility.value
    permissions.value = res?.permissions || permissions.value
    Message.success('可见范围已更新')
    emit('changed')
    await loadMembers()
  } catch (e) {
    Message.error(getErrorMessage(e, '更新可见范围失败'))
  } finally {
    savingVisibility.value = false
  }
}

async function saveMember(userId = memberForm.user_id, role = memberForm.role) {
  if (!props.skillId || !canManage.value || !userId) return
  savingMember.value = true
  try {
    await skillApi.upsertMember(props.skillId, { user_id: userId, role })
    Message.success('成员权限已保存')
    memberForm.user_id = ''
    memberForm.role = 'viewer'
    emit('changed')
    await loadMembers()
    await loadCandidates('')
  } catch (e) {
    Message.error(getErrorMessage(e, '保存成员权限失败'))
  } finally {
    savingMember.value = false
  }
}

async function removeMember(userId: string) {
  if (!props.skillId || !canManage.value || !userId) return
  try {
    await skillApi.deleteMember(props.skillId, userId)
    Message.success('成员已移除')
    emit('changed')
    await loadMembers()
    await loadCandidates('')
  } catch (e) {
    Message.error(getErrorMessage(e, '移除成员失败'))
  }
}
</script>

<style scoped>
.perm-modal {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.perm-section {
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  padding: 14px;
  background: var(--ai-surface);
}
.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}
.section-title {
  font-size: 14px;
  font-weight: 800;
  color: var(--ai-ink-1);
}
.section-desc {
  margin-top: 3px;
  font-size: 12px;
  color: var(--ai-ink-3);
}
.visibility-row,
.add-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.add-row {
  margin-bottom: 12px;
}
.member-cell {
  min-width: 0;
}
.member-name {
  font-weight: 700;
  color: var(--ai-ink-1);
}
.member-id {
  margin-top: 2px;
  font-size: 12px;
  color: var(--ai-ink-3);
  word-break: break-all;
}
</style>
