<template>
  <div class="page-container admin-org-page">
    <div class="page-header">
      <div>
        <div class="page-kicker">管理后台 · 账号与组织</div>
        <h2 class="page-title">组织管理</h2>
        <p class="page-subtitle">部门 / 项目组 / 虚拟组层级 · 钉钉同步 · 成员归属</p>
      </div>
      <a-space>
        <a-button class="ai-btn-like" :loading="syncing" @click="handleSyncDingtalk">
          <template #icon><icon-sync /></template>
          从钉钉导入
        </a-button>
        <a-button class="ai-btn-like primary" type="primary" @click="openCreateModal">
          <template #icon><icon-plus /></template>
          新建组织
        </a-button>
      </a-space>
    </div>

    <div class="org-kpis">
      <article class="org-kpi ai-card">
        <span class="org-kpi-label">组织节点</span>
        <strong>{{ totalOrgCount }}</strong>
        <span>部门 / 项目组 / 虚拟组</span>
      </article>
      <article class="org-kpi ai-card">
        <span class="org-kpi-label">总成员归属</span>
        <strong>{{ totalMemberCount }}</strong>
        <span>含子组织成员统计</span>
      </article>
      <article class="org-kpi ai-card">
        <span class="org-kpi-label">当前组织</span>
        <strong>{{ selectedOrg ? orgTypeLabel(selectedOrg.type) : '-' }}</strong>
        <span>{{ selectedOrg?.name || '未选择' }}</span>
      </article>
    </div>

    <div class="org-workspace">
      <section class="org-tree-card ai-card">
        <header class="ai-card-h">
          <div>
            <div class="t">组织架构</div>
            <div class="s">点击节点查看成员与上下级</div>
          </div>
        </header>
        <div class="org-card-body">
          <a-spin :loading="treeLoading">
            <a-tree
              :data="treeData"
              :selected-keys="selectedKeys"
              show-line
              block-node
              @select="handleSelectOrg"
            />
          </a-spin>
        </div>
      </section>

      <section class="org-members-card ai-card">
        <header class="ai-card-h member-head">
          <div>
            <div class="t">{{ memberCardTitle }}</div>
            <div class="s">成员归属、管理者和加入时间</div>
          </div>
          <div class="member-actions">
            <a-space>
              <a-button v-if="selectedOrg" class="ai-btn-like" @click="openAddMemberModal">添加成员</a-button>
              <a-button v-if="selectedOrg" class="ai-btn-like" @click="openMoveModal">移动</a-button>
              <a-popconfirm
                v-if="selectedOrg"
                content="确定删除该组织？含子组织时勾选'强制删除'可把子组织上移到父级。"
                @ok="handleDeleteUnit(false)"
              >
                <a-button class="ai-btn-like danger" status="danger">删除</a-button>
              </a-popconfirm>
            </a-space>
          </div>
        </header>
        <div class="org-card-body">

          <!-- P2/G4：完整版空状态，替代原极简 SfEmptyState；
               图标 + 标题 + 说明 + CTA，对齐 Playbook 空态规范 -->
          <SfEmptyStateV2
            v-if="!selectedOrg"
            :icon="IconBranch"
            title="请选择左侧组织"
            description="点击左侧组织树里的任一节点，可查看成员、调整上下级或移动；没有合适的？点右上「新建组织」。"
            action-text="新建组织"
            @action="openCreateModal"
          />

          <template v-else>
            <a-descriptions :column="2" bordered size="small" class="org-desc">
              <a-descriptions-item label="组织名称">{{ selectedOrg.name }}</a-descriptions-item>
              <a-descriptions-item label="类型">{{ orgTypeLabel(selectedOrg.type) }}</a-descriptions-item>
              <a-descriptions-item label="父级">
                <span class="mono-id">{{ selectedOrg.parent_id || '-' }}</span>
              </a-descriptions-item>
              <a-descriptions-item label="成员数">
                <span class="mono-num">{{ selectedOrg.total_member_count ?? selectedOrg.member_count ?? 0 }}</span>
                （直接 <span class="mono-num">{{ selectedOrg.member_count ?? 0 }}</span>）
              </a-descriptions-item>
            </a-descriptions>

            <a-table class="page-list-table" :data="members" :loading="membersLoading" :pagination="false" row-key="user_id">
              <template #columns>
                <a-table-column title="姓名">
                  <template #cell="{ record }">
                    <span class="member-name">{{ record.name || record.user_id }}</span>
                  </template>
                </a-table-column>
                <a-table-column title="用户名">
                  <template #cell="{ record }">
                    <span class="mono-id">{{ record.username || '-' }}</span>
                  </template>
                </a-table-column>
                <a-table-column title="类型">
                  <template #cell="{ record }">
                    <span class="muted-cell">{{ record.membership_type || '-' }}</span>
                  </template>
                </a-table-column>
                <a-table-column title="管理者">
                  <template #cell="{ record }">
                    <a-tag size="small" :color="record.is_manager ? 'green' : 'gray'">{{ record.is_manager ? '是' : '否' }}</a-tag>
                  </template>
                </a-table-column>
                <a-table-column title="加入时间">
                  <template #cell="{ record }"><span class="muted-cell">{{ formatTime(record.joined_at) }}</span></template>
                </a-table-column>
                <a-table-column title="操作" :width="100">
                  <template #cell="{ record }">
                    <a-popconfirm content="确定移除该成员？" @ok="removeMember(record)">
                      <a-button type="text" status="danger" size="small">移除</a-button>
                    </a-popconfirm>
                  </template>
                </a-table-column>
              </template>
            </a-table>
          </template>
        </div>
      </section>
    </div>

    <a-modal v-model:visible="showCreateModal" title="新建组织" :ok-loading="savingUnit" @ok="handleCreateUnit">
      <a-form :model="unitForm" layout="vertical">
        <a-form-item label="组织名称" required>
          <a-input v-model="unitForm.name" placeholder="例如 618 大促项目组" />
        </a-form-item>
        <a-form-item label="组织类型" required>
          <a-select v-model="unitForm.type">
            <a-option value="department">部门</a-option>
            <a-option value="project">项目组</a-option>
            <a-option value="virtual">虚拟组</a-option>
          </a-select>
        </a-form-item>
        <a-form-item label="父级组织">
          <a-select v-model="unitForm.parent_id" allow-clear placeholder="可选">
            <a-option v-for="option in orgOptions" :key="option.value || 'root'" :value="option.value">
              {{ option.label }}
            </a-option>
          </a-select>
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="showMemberModal" title="添加成员" :ok-loading="savingMember" @ok="handleAddMember">
      <a-form :model="memberForm" layout="vertical">
        <a-form-item label="用户 ID" required>
          <a-input v-model="memberForm.user_id" placeholder="例如 zhangsan" />
        </a-form-item>
        <a-form-item label="成员类型">
          <a-select v-model="memberForm.membership_type">
            <a-option value="primary">主组织</a-option>
            <a-option value="secondary">副组织</a-option>
          </a-select>
        </a-form-item>
        <a-form-item label="是否管理者">
          <a-switch v-model="memberForm.is_manager" />
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="showMoveModal" title="移动组织到新父级" :ok-loading="savingMove" @ok="handleMoveUnit">
      <a-form :model="moveForm" layout="vertical">
        <a-form-item label="目标父级">
          <a-select v-model="moveForm.new_parent_id" allow-clear placeholder="留空 = 移到顶级">
            <a-option
              v-for="option in movableParentOptions"
              :key="option.value || 'root'"
              :value="option.value"
            >
              {{ option.label }}
            </a-option>
          </a-select>
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="showSyncResultModal" title="钉钉导入结果" :footer="false">
      <a-descriptions :column="1" bordered size="small" class="org-desc">
        <a-descriptions-item label="导入部门数">
          <a-tag size="small" color="arcoblue">{{ syncResult?.synced_units ?? 0 }}</a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="导入成员数">
          <a-tag size="small" color="arcoblue">{{ syncResult?.synced_users ?? 0 }}</a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="错误数">
          <a-tag size="small" :color="(syncResult?.errors?.length || 0) > 0 ? 'red' : 'green'">
            {{ syncResult?.errors?.length ?? 0 }}
          </a-tag>
        </a-descriptions-item>
      </a-descriptions>
      <div v-if="syncResult?.errors?.length" class="sync-errors">
        <div class="sync-errors-title">错误明细（{{ syncResult.errors.length }} 条）</div>
        <a-list :data="syncResult.errors" :bordered="false" size="small">
          <template #item="{ item }">
            <a-list-item class="sync-error-item">{{ item }}</a-list-item>
          </template>
        </a-list>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconPlus, IconSync, IconBranch } from '@arco-design/web-vue/es/icon'
import { orgApi as rawOrgApi } from '@/api'
import { formatTime } from '@/utils/format'
import { normalizeListResponse } from '../portal/shared'
import { SfEmptyStateV2 } from '@/components/sf'

defineOptions({ name: 'AdminOrg' })

type OrgUnit = {
  id: string
  name: string
  type?: string
  parent_id?: string | null
  member_count?: number
  total_member_count?: number
  children?: OrgUnit[]
}

type OrgTreeNode = {
  key: string
  title: string
  children?: OrgTreeNode[]
  raw: OrgUnit
}

type OrgMember = {
  user_id: string
  username?: string
  name?: string
  membership_type?: string
  is_manager?: boolean
  joined_at?: string
}

const orgApi: any = rawOrgApi
const treeLoading = ref(false)
const syncing = ref(false)
const membersLoading = ref(false)
const savingUnit = ref(false)
const savingMember = ref(false)
const savingMove = ref(false)
const orgTree = ref<OrgUnit[]>([])
const members = ref<OrgMember[]>([])
const selectedOrgId = ref('')
const showCreateModal = ref(false)
const showMemberModal = ref(false)
const showMoveModal = ref(false)
const showSyncResultModal = ref(false)
const syncResult = ref<{
  synced_units?: number
  synced_users?: number
  errors?: string[]
} | null>(null)
const unitForm = ref({ name: '', type: 'department', parent_id: '' })
const memberForm = ref({ user_id: '', membership_type: 'primary', is_manager: false })
const moveForm = ref<{ new_parent_id: string | null }>({ new_parent_id: null })

const selectedOrg = computed(() => findOrgById(orgTree.value, selectedOrgId.value))
const memberCardTitle = computed(() => `${selectedOrg.value?.name || '请选择组织'} — 成员`)
const selectedKeys = computed(() => (selectedOrgId.value ? [selectedOrgId.value] : []))
const treeData = computed<OrgTreeNode[]>(() => orgTree.value.map(node => mapTreeNode(node)))
const orgOptions = computed(() => flattenOrgOptions(orgTree.value))
const totalOrgCount = computed(() => countOrgUnits(orgTree.value))
const totalMemberCount = computed(() => orgTree.value.reduce((sum, node) => sum + Number(node.total_member_count ?? node.member_count ?? 0), 0))
// 移动时的可选父级：排除自身及自身后代（避免循环）
const movableParentOptions = computed(() => {
  if (!selectedOrg.value) return orgOptions.value
  const excluded = new Set<string>()
  const collect = (node: OrgUnit) => {
    excluded.add(node.id)
    for (const child of node.children || []) collect(child)
  }
  collect(selectedOrg.value)
  return orgOptions.value.filter(opt => !excluded.has(opt.value))
})

function mapTreeNode(node: OrgUnit): OrgTreeNode {
  return {
    key: node.id,
    title: `${node.name}${typeof node.total_member_count === 'number' ? ` (${node.total_member_count})` : typeof node.member_count === 'number' ? ` (${node.member_count})` : ''}`,
    raw: node,
    children: (node.children || []).map(child => mapTreeNode(child)),
  }
}

function countOrgUnits(nodes: OrgUnit[]): number {
  return nodes.reduce((sum, node) => sum + 1 + countOrgUnits(node.children || []), 0)
}

function flattenOrgOptions(nodes: OrgUnit[], depth = 0): Array<{ label: string; value: string }> {
  const options: Array<{ label: string; value: string }> = []
  for (const node of nodes) {
    options.push({ label: `${'　'.repeat(depth)}${node.name}`, value: node.id })
    if (node.children?.length) {
      options.push(...flattenOrgOptions(node.children, depth + 1))
    }
  }
  return options
}

function findOrgById(nodes: OrgUnit[], id: string): OrgUnit | null {
  for (const node of nodes) {
    if (node.id === id) return node
    if (node.children?.length) {
      const found = findOrgById(node.children, id)
      if (found) return found
    }
  }
  return null
}

function orgTypeLabel(type?: string): string {
  const labels: Record<string, string> = {
    department: '部门',
    project: '项目组',
    virtual: '虚拟组',
  }
  return labels[type || ''] || type || '-'
}

async function loadTree(selectFirst = false) {
  treeLoading.value = true
  try {
    const res = await orgApi.getTree()
    orgTree.value = normalizeListResponse<OrgUnit>(res)
    if ((selectFirst || !selectedOrgId.value) && orgTree.value[0]) {
      selectedOrgId.value = orgTree.value[0].id
    }
  } catch (error: any) {
    Message.error(error?._message || '加载组织树失败')
    orgTree.value = []
  } finally {
    treeLoading.value = false
  }
}

async function loadMembers(orgId: string) {
  if (!orgId) {
    members.value = []
    return
  }
  membersLoading.value = true
  try {
    const res = await orgApi.getMembers(orgId)
    members.value = normalizeListResponse<OrgMember>(res)
  } catch (error: any) {
    Message.error(error?._message || '加载成员失败')
    members.value = []
  } finally {
    membersLoading.value = false
  }
}

function handleSelectOrg(keys: (string | number)[]) {
  const nextId = String(keys[0] || '')
  if (nextId) selectedOrgId.value = nextId
}

function openCreateModal() {
  unitForm.value = { name: '', type: 'department', parent_id: selectedOrgId.value || '' }
  showCreateModal.value = true
}

function openAddMemberModal() {
  memberForm.value = { user_id: '', membership_type: 'secondary', is_manager: false }
  showMemberModal.value = true
}

async function handleCreateUnit() {
  if (!unitForm.value.name.trim()) {
    Message.warning('请输入组织名称')
    return
  }
  savingUnit.value = true
  try {
    const res = await orgApi.createUnit({
      name: unitForm.value.name.trim(),
      type: unitForm.value.type,
      parent_id: unitForm.value.parent_id || undefined,
    })
    showCreateModal.value = false
    Message.success('组织已创建')
    await loadTree(true)
    if (res?.id) selectedOrgId.value = res.id
  } catch (error: any) {
    Message.error(error?._message || '创建组织失败')
  } finally {
    savingUnit.value = false
  }
}

async function handleAddMember() {
  if (!selectedOrg.value) return
  if (!memberForm.value.user_id.trim()) {
    Message.warning('请输入用户 ID')
    return
  }
  savingMember.value = true
  try {
    await orgApi.addMembership({
      user_id: memberForm.value.user_id.trim(),
      org_unit_id: selectedOrg.value.id,
      membership_type: memberForm.value.membership_type,
      is_manager: memberForm.value.is_manager,
    })
    showMemberModal.value = false
    Message.success('成员已添加')
    await Promise.all([loadMembers(selectedOrg.value.id), loadTree()])
  } catch (error: any) {
    Message.error(error?._message || '添加成员失败')
  } finally {
    savingMember.value = false
  }
}

async function removeMember(record: OrgMember) {
  if (!selectedOrg.value) return
  try {
    await orgApi.removeMembership(record.user_id, selectedOrg.value.id)
    Message.success('成员已移除')
    await Promise.all([loadMembers(selectedOrg.value.id), loadTree()])
  } catch (error: any) {
    Message.error(error?._message || '移除成员失败')
  }
}

async function handleSyncDingtalk() {
  syncing.value = true
  try {
    const res = await orgApi.syncDingtalk()
    syncResult.value = {
      synced_units: res?.synced_units ?? 0,
      synced_users: res?.synced_users ?? 0,
      errors: Array.isArray(res?.errors) ? res.errors : [],
    }
    showSyncResultModal.value = true
    const errCount = syncResult.value.errors?.length || 0
    const noData = (syncResult.value.synced_units || 0) === 0 && (syncResult.value.synced_users || 0) === 0
    if (errCount === 0) {
      Message.success(`导入完成：部门 ${syncResult.value.synced_units} / 成员 ${syncResult.value.synced_users}`)
    } else if (noData) {
      Message.error(`导入失败（${errCount} 条错误），详见弹窗 — 常见原因是钉钉应用未开权限`)
    } else {
      Message.warning(`部分导入：部门 ${syncResult.value.synced_units} / 成员 ${syncResult.value.synced_users}，另有 ${errCount} 条错误`)
    }
    await loadTree()
  } catch (error: any) {
    Message.error(error?._message || '导入失败')
  } finally {
    syncing.value = false
  }
}

function openMoveModal() {
  if (!selectedOrg.value) return
  moveForm.value = { new_parent_id: selectedOrg.value.parent_id ?? null }
  showMoveModal.value = true
}

async function handleMoveUnit() {
  if (!selectedOrg.value) return
  // 禁止移到自身或后代（computed 已过滤，这里再兜一层）
  const targetId = moveForm.value.new_parent_id
  if (targetId === selectedOrg.value.id) {
    Message.warning('不能移动到自身')
    return
  }
  savingMove.value = true
  try {
    await orgApi.moveUnit(selectedOrg.value.id, targetId || null)
    showMoveModal.value = false
    Message.success('已移动')
    await loadTree()
  } catch (error: any) {
    Message.error(error?._message || '移动失败')
  } finally {
    savingMove.value = false
  }
}

async function handleDeleteUnit(force: boolean) {
  if (!selectedOrg.value) return
  const orgId = selectedOrg.value.id
  try {
    await orgApi.deleteUnit(orgId, force)
    Message.success('已删除')
    selectedOrgId.value = ''
    await loadTree()
  } catch (error: any) {
    // 有子组织时后端会报错；给用户二次确认 force 删除的机会
    const msg = error?._message || '删除失败'
    if (msg.includes('子') || msg.includes('children') || /HAS_CHILDREN/i.test(msg)) {
      Message.info('该组织下有子组织，请勾选"强制删除"再试')
      // 触发强制删除二次确认
      const { Modal } = await import('@arco-design/web-vue')
      Modal.warning({
        title: '强制删除',
        content: `组织 ${selectedOrg.value?.name} 下的子组织将上移到父级，继续吗？`,
        hideCancel: false,
        onOk: () => handleDeleteUnit(true),
      })
    } else {
      Message.error(msg)
    }
  }
}

watch(selectedOrgId, (nextId) => {
  loadMembers(nextId)
}, { immediate: true })

onMounted(() => {
  loadTree(true)
})
</script>

<style scoped>
/* 设计稿 page chrome 覆盖 —— 与 AdminUsers 同模式 */
.admin-org-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.admin-org-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  margin: 0;
}
.admin-org-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.admin-org-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}

/* 顶部按钮统一外形 —— 30px / 6px 圆角 / 12.5px 字号 */
.admin-org-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary)) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 500;
  box-shadow: none;
}
.admin-org-page :deep(.arco-btn.ai-btn-like .arco-btn-content) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.admin-org-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary):hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
.admin-org-page :deep(.arco-btn-primary.ai-btn-like) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
  color: var(--ai-surface);
  font-size: 12.5px;
  font-weight: 500;
  box-shadow: none;
}
.admin-org-page :deep(.arco-btn-primary.ai-btn-like:hover) {
  background: var(--ai-ink-2);
  border-color: var(--ai-ink-2);
}

.admin-org-page :deep(.arco-btn.ai-btn-like.danger) {
  color: var(--ai-bad);
  border-color: var(--ai-bad-soft);
  background: var(--ai-bad-soft);
}

.admin-org-page :deep(.arco-btn.ai-btn-like.danger:hover) {
  border-color: var(--ai-bad);
  background: var(--ai-bad-soft);
}

.org-kpis {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.org-kpi {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 16px;
}

.org-kpi-label {
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.org-kpi strong {
  overflow: hidden;
  font-size: 26px;
  line-height: 1.1;
  font-weight: 650;
  letter-spacing: -0.03em;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.org-kpi span:last-child {
  overflow: hidden;
  color: var(--ai-ink-4);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.org-workspace {
  display: grid;
  grid-template-columns: minmax(300px, 0.85fr) minmax(0, 1.8fr);
  gap: 14px;
  align-items: start;
}

.org-tree-card,
.org-members-card {
  overflow: hidden;
  min-height: 320px;
}

.org-card-body {
  padding: 14px;
}

.member-head {
  justify-content: space-between;
  align-items: center;
}

.member-actions {
  flex: 0 0 auto;
}

/* 卡片标题 —— 13px / ink-1 / 与 AdminUsers 一致 */
.admin-org-page :deep(.page-list-card .arco-card-header) {
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 12px 14px !important;
}
.admin-org-page :deep(.page-list-card .arco-card-header-title) {
  font-size: 13px !important;
  font-weight: 500 !important;
  color: var(--ai-ink-1) !important;
}
.admin-org-page :deep(.page-list-card .arco-card-body) {
  padding: 14px !important;
}

/* a-tree 节点：13px / ink-1 / hover surface-2 */
.admin-org-page :deep(.arco-tree-node) {
  padding: 0;
}
.admin-org-page :deep(.arco-tree-node-title) {
  font-size: 13px;
  color: var(--ai-ink-1);
  white-space: normal;
  border-radius: 5px;
  padding: 4px 8px;
  font-weight: 450;
  transition: background 0.15s ease, color 0.15s ease;
}
.admin-org-page :deep(.arco-tree-node-title:hover) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
}
.admin-org-page :deep(.arco-tree-node-selected .arco-tree-node-title),
.admin-org-page :deep(.arco-tree-node-title-highlight),
.admin-org-page :deep(.arco-tree-node-title-block.arco-tree-node-selected) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-1) !important;
  font-weight: 500;
}
.admin-org-page :deep(.arco-tree-node-switcher) {
  color: var(--ai-ink-4);
}
.admin-org-page :deep(.arco-tree-node-indent-block) {
  border-color: var(--ai-border) !important;
}

/* a-descriptions（组织信息 + 同步结果）扁平化 */
.admin-org-page :deep(.org-desc.arco-descriptions-border .arco-descriptions-item-label-block),
.admin-org-page :deep(.org-desc.arco-descriptions-border .arco-descriptions-item-value-block) {
  border-color: var(--ai-border) !important;
}
.admin-org-page :deep(.org-desc .arco-descriptions-item-label-block) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 8px 10px !important;
}
.admin-org-page :deep(.org-desc .arco-descriptions-item-value-block) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-1) !important;
  font-size: 12.5px !important;
  padding: 8px 10px !important;
}

.org-desc {
  margin-bottom: 16px;
}

/* 表头 —— 11.5px / uppercase / ink-4 */
.admin-org-page :deep(.arco-table-th) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 8px 12px !important;
}
.admin-org-page :deep(.arco-table-th .arco-table-th-title) {
  color: var(--ai-ink-4);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* 单元格 —— 12.5px / ink-1 / ai-border 底分隔线 */
.admin-org-page :deep(.arco-table-td) {
  font-size: 12.5px !important;
  color: var(--ai-ink-1) !important;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 10px 12px !important;
  background: var(--ai-surface) !important;
}

/* 行 hover：surface-2 */
.admin-org-page :deep(.arco-table-tr:hover .arco-table-td),
.admin-org-page :deep(.arco-table-tr-hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

/* tag pills —— 20px 高 / 4px 方角 / 11px/500 —— 按 arcoblue / green / red / orange / gray 五档 */
.admin-org-page :deep(.arco-tag.arco-tag-size-small),
.admin-org-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
}
.admin-org-page :deep(.arco-tag-color-arcoblue),
.admin-org-page :deep(.arco-tag-color-blue) {
  background: var(--ai-info-soft) !important;
  color: var(--ai-info) !important;
  border-color: transparent !important;
}
.admin-org-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft) !important;
  color: var(--ai-ok) !important;
  border-color: transparent !important;
}
.admin-org-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft) !important;
  color: var(--ai-bad) !important;
  border-color: transparent !important;
}
.admin-org-page :deep(.arco-tag-color-orange),
.admin-org-page :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft) !important;
  color: var(--ai-warn) !important;
  border-color: transparent !important;
}
.admin-org-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-2) !important;
  border-color: transparent !important;
}

/* mono 字体 —— 组织 ID / 用户名 / 成员数 */
.mono-id {
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-3);
  font-variant-numeric: tabular-nums;
}
.mono-num {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--ai-ink-1);
  font-weight: 500;
}
.member-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
}
.muted-cell {
  font-size: 12px;
  color: var(--ai-ink-4);
}

/* 同步结果弹窗内错误列表 */
.sync-errors {
  margin-top: 16px;
  max-height: 260px;
  overflow: auto;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
  padding: 8px;
}
.sync-errors-title {
  font-size: 12px;
  font-weight: 500;
  margin-bottom: 8px;
  color: var(--ai-ink-3);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.sync-error-item {
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-bad);
}
.admin-org-page :deep(.sync-errors .arco-list-item) {
  padding: 4px 6px !important;
  border: 0 !important;
}

@media (max-width: 1180px) {
  .org-workspace {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .org-kpis {
    grid-template-columns: 1fr;
  }

  .member-head {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
