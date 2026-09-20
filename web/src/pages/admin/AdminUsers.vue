<template>
  <div class="admin-users-page ai-main">
    <div class="ai-pagehead admin-users-pagehead">
      <div>
        <div class="ai-crumbs">管理后台 · 账号与组织</div>
        <h1 class="ai-title">用户管理</h1>
        <p class="ai-sub">人员、角色、部门归属 · 钉钉同步 · Codex 插件签名</p>
      </div>
      <div v-if="!isPendingView" class="admin-users-head-actions">
        <button class="ai-btn" type="button" :disabled="syncing" @click="handleSyncDingtalk">
          <SfShellIcon name="refresh" />
          {{ syncing ? '同步中' : '同步钉钉' }}
        </button>
        <button class="ai-btn primary" type="button" @click="openCreateModal">
          <SfShellIcon name="plus" />
          新建用户
        </button>
      </div>
      <div v-else class="admin-users-head-actions">
        <a-select
          v-model="filterDepartmentId"
          allow-clear
          placeholder="筛选部门"
          class="pending-department-select"
          @change="loadPendingUsers"
        >
          <a-option
            v-for="option in visibleDepartmentOptions"
            :key="option.value"
            :value="option.value"
          >
            {{ option.label }}
          </a-option>
        </a-select>
        <button class="ai-btn" type="button" :disabled="pendingLoading" @click="loadPendingUsers">
          <SfShellIcon name="refresh" />
          {{ pendingLoading ? '刷新中' : '刷新' }}
        </button>
      </div>
    </div>

    <AdminUsersTabs
      :active-count="stats.active"
      :pending-count="pendingRows.length"
      :disabled-count="stats.disabled"
    />

    <!-- 激活/停用 tab 下的常规用户表 -->
    <div v-if="!isPendingView" class="admin-users-body ai-pagebody">
      <div class="filter-bar">
        <a-input-search
          v-model="searchKey"
          placeholder="搜索用户 ID / 姓名"
          allow-clear
          class="filter-search"
        />
        <a-select
          v-model="filterRole"
          placeholder="角色"
          allow-clear
          class="filter-select"
        >
          <a-option
            v-for="option in editableRoleOptions"
            :key="option.value"
            :value="option.value"
          >
            {{ option.label }}
          </a-option>
        </a-select>
        <a-select
          v-model="filterDepartmentName"
          placeholder="部门"
          allow-clear
          class="filter-select"
        >
          <a-option
            v-for="dept in deptOptions"
            :key="dept"
            :value="dept"
          >
            {{ dept }}
          </a-option>
        </a-select>
        <a-select
          v-model="filterStatusLocal"
          placeholder="状态"
          allow-clear
          class="filter-select-sm"
        >
          <a-option value="active">启用</a-option>
          <a-option value="disabled">停用</a-option>
        </a-select>
        <div class="filter-spacer" />
        <div class="filter-chips">
          <span
            class="ai-pill is-active"
            :class="{ selected: !filterRole }"
            @click="filterRole = undefined"
          >
            全部 {{ stats.activeAll }}
          </span>
          <span
            v-for="chip in roleChips"
            :key="chip.value"
            class="ai-pill"
            :class="{ selected: filterRole === chip.value }"
            @click="filterRole = (filterRole === chip.value ? undefined : chip.value)"
          >
            {{ chip.label }} {{ chip.count }}
          </span>
        </div>
      </div>

      <a-card class="user-table-card ai-card table-card">
        <a-spin :loading="loading">
          <a-table
            class="page-list-table user-table"
            :data="pagedUsers"
            :pagination="tablePagination"
            row-key="id"
            @row-click="handleUserTableRowClick"
            @page-change="onPageChange"
            @page-size-change="onPageSizeChange"
          >
          <template #columns>
            <a-table-column title="用户">
              <template #cell="{ record }">
                <div class="user-cell-row clickable" @click.stop="openUserDrawer(record)">
                  <div class="row-avatar">
                    <img v-if="getUserAvatar(record)" :src="getUserAvatar(record)" :alt="record.name || record.id" />
                    <span v-else>{{ (record.name || record.id || '?')[0] }}</span>
                  </div>
                  <div class="user-cell-meta">
                    <div class="user-cell-name">{{ record.name || record.id }}</div>
                    <div class="user-cell-id mono-id">{{ record.id }}</div>
                  </div>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="角色" :width="110">
              <template #cell="{ record }">
                <!-- P1-2：角色徽章走 roleTone 映射（utils/tone.ts），解决 V3 角色色失控 -->
                <SfTagChip
                  :label="(roleLabel as Record<string, string>)[record.role] || record.role"
                  :tone="roleTone(record.role)"
                  size="sm"
                />
              </template>
            </a-table-column>
            <a-table-column title="部门" :width="120">
              <template #cell="{ record }">
                <span v-if="record.department" class="ai-pill dept-pill">{{ record.department }}</span>
                <span v-else class="muted-cell">—</span>
              </template>
            </a-table-column>
            <a-table-column title="状态" :width="80">
              <template #cell="{ record }">
                <span
                  class="ai-pill dot"
                  :class="record.is_active ? 'ok' : 'bad'"
                >
                  {{ record.is_active ? '启用' : '停用' }}
                </span>
              </template>
            </a-table-column>
            <a-table-column title="最近活跃" :width="110">
              <template #cell="{ record }">
                <a-tooltip v-if="record.last_activity_at || record.last_login_at" :content="lastActivityTooltip(record)" mini>
                  <span class="muted-cell">{{ relativeTime(record.last_activity_at || record.last_login_at) }}</span>
                </a-tooltip>
                <span v-else class="muted-cell">—</span>
              </template>
            </a-table-column>
            <a-table-column title="Codex 插件" :width="140">
              <template #cell="{ record }">
                <span v-if="record.codex?.is_codex_user" class="codex-mono">
                  {{ codexCellText(record) }}
                </span>
                <span v-else class="muted-cell">—</span>
              </template>
            </a-table-column>
            <a-table-column title="操作" :width="220">
              <template #cell="{ record }">
                <div class="row-actions" @click.stop>
                  <button type="button" class="ai-btn sm" @click="editUser(record)">编辑</button>
                  <button type="button" class="ai-btn sm" @click="resetPwd(record.id)">重置密码</button>
                  <button
                    v-if="isLastActiveSystemAdmin(record)"
                    type="button"
                    class="ai-btn sm"
                    disabled
                    title="最后一个系统管理员不可停用"
                  >
                    硬锁
                  </button>
                  <a-popconfirm v-else-if="record.is_active" content="确定停用？" @ok="disableUser(record.id)">
                    <button type="button" class="ai-btn sm danger">停用</button>
                  </a-popconfirm>
                  <button v-else type="button" class="ai-btn sm" @click="enableUser(record.id)">启用</button>
                </div>
              </template>
            </a-table-column>
          </template>
          </a-table>
        </a-spin>
      </a-card>
    </div>

    <!-- 待激活 tab 下的 pending 用户表（合并自原 PendingUsers.vue） -->
    <div v-else class="admin-users-body admin-users-body--pending ai-pagebody">
      <a-card class="admin-users-pending-card ai-card table-card">
        <div class="pending-summary">
          <SfStatChip label="候选用户" :value="pendingRows.length" />
          <SfStatChip label="可分配部门" :value="visibleDepartmentOptions.length" />
        </div>
        <a-table class="page-list-table" :data="pendingRows" :loading="pendingLoading" :pagination="false" row-key="id">
          <template #empty>
            <SfEmptyState
              title="待激活用户"
              description="暂无待激活用户"
              hint="钉钉首登用户默认进入 pending 状态，需 admin 或 dept_admin 分配角色和部门。"
            />
          </template>
          <template #columns>
            <a-table-column title="用户">
              <template #cell="{ record }">
                <div class="user-cell">
                  <div class="user-name">{{ record.name || record.id }}</div>
                  <div class="user-id">{{ record.id }}</div>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="钉钉部门">
              <template #cell="{ record }">
                <span class="dept-cell">{{ record.dingtalk_department || '未关联' }}</span>
              </template>
            </a-table-column>
            <a-table-column title="进入待激活">
              <template #cell="{ record }">
                <a-tooltip v-if="record.created_at" :content="formatTime(record.created_at)" mini>
                  <span class="muted-cell">{{ relativeTime(record.created_at) }}</span>
                </a-tooltip>
                <span v-else class="muted-cell">-</span>
              </template>
            </a-table-column>
            <a-table-column title="操作" :width="120">
              <template #cell="{ record }">
                <a-button type="primary" size="small" @click="openActivateModal(record)">激活</a-button>
              </template>
            </a-table-column>
          </template>
        </a-table>
      </a-card>
    </div>

    <!-- 新建/编辑弹窗 -->
    <a-modal v-model:visible="showCreate" :title="editingUser ? '编辑用户' : '新建用户'" @ok="handleSave" :ok-loading="saving">
      <a-form :model="userForm" layout="vertical">
        <a-form-item label="用户ID" required><a-input v-model="userForm.user_id" :disabled="!!editingUser" /></a-form-item>
        <a-form-item label="用户名" required><a-input v-model="userForm.username" :disabled="!!editingUser" /></a-form-item>
        <a-form-item label="姓名" required><a-input v-model="userForm.name" /></a-form-item>
        <a-form-item v-if="!editingUser" label="密码"><a-input-password v-model="userForm.password" /></a-form-item>
        <a-form-item label="角色">
          <a-select v-model="userForm.role" :disabled="editingLastSystemAdmin">
            <a-option
              v-for="option in editableRoleOptions"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </a-option>
          </a-select>
          <template v-if="editingLastSystemAdmin" #extra>
            最后一个系统管理员不可改 role。
          </template>
        </a-form-item>
        <a-form-item label="部门"><a-input v-model="userForm.department" /></a-form-item>
      </a-form>
    </a-modal>

    <!-- 激活弹窗（从原 PendingUsers.vue 合并过来） -->
    <a-modal
      v-model:visible="activateVisible"
      title="激活用户"
      ok-text="确认激活"
      cancel-text="取消"
      :ok-loading="activating"
      @ok="handleActivate"
    >
      <a-form :model="activateForm" layout="vertical">
        <a-form-item label="候选用户">
          <div class="modal-static">{{ selectedPendingUser?.name || selectedPendingUser?.id || '-' }}</div>
        </a-form-item>
        <a-form-item label="角色">
          <a-select v-model="activateForm.role">
            <a-option v-for="opt in activateRoleOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</a-option>
          </a-select>
        </a-form-item>
        <a-form-item label="归属部门">
          <a-select v-model="activateForm.department_id" placeholder="选择部门">
            <a-option v-for="opt in visibleDepartmentOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</a-option>
          </a-select>
        </a-form-item>
        <a-form-item label="部门管理员">
          <div class="switch-row">
            <a-switch v-model="activateForm.is_manager" :disabled="activateForm.role !== 'dept_admin'" />
            <span class="switch-copy">
              {{ activateForm.role === 'dept_admin' ? 'dept_admin 必须绑定部门管理员范围' : '仅 system_admin 选择 dept_admin 时可开启' }}
            </span>
          </div>
          <div class="activate-hint">
            <icon-info-circle />
            选 <code>dept_admin</code> 会自动开启；选其他角色会自动关闭。
          </div>
        </a-form-item>
        <a-form-item v-if="userStore.isSystemAdmin" label="跨部门只读">
          <div class="switch-row">
            <a-switch v-model="activateForm.can_view_all" :disabled="activateForm.role === 'system_admin'" />
            <span class="switch-copy">仅 system_admin 可授予全局只读穿透。</span>
          </div>
          <div class="activate-hint">
            <icon-info-circle />
            选 <code>system_admin</code> 时会自动关闭此开关。
          </div>
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- L3-E · 用户详情抽屉（行点击触发） -->
    <a-drawer
      v-model:visible="drawerOpen"
      :width="720"
      :footer="false"
      :header="false"
      unmount-on-close
      @close="onDrawerClose"
    >
      <div v-if="selectedUser" class="user-drawer">
        <!-- 抽屉顶部：头像 + 名称 + pills + 关闭按钮 —— 对齐 L3-E 设计稿 -->
        <header class="ud-header">
          <div class="ud-avatar-wrap">
            <img
              v-if="getUserAvatar(selectedUser)"
              :src="getUserAvatar(selectedUser)"
              :alt="selectedUser.name || selectedUser.id"
              class="ud-avatar-img"
            />
            <span v-else class="ud-avatar-fallback">
              {{ (selectedUser.name || selectedUser.id || '?')[0] }}
            </span>
          </div>
          <div class="ud-header-main">
            <div class="ud-header-name-row">
              <span class="ud-header-name">{{ selectedUser.name || selectedUser.id }}</span>
              <SfTagChip
                :label="(roleLabel as Record<string, string>)[selectedUser.role || ''] || (selectedUser.role || '未分配')"
                :tone="roleTone(selectedUser.role || '')"
                size="sm"
              />
              <span class="ai-pill accent">{{ selectedUser.department || '未分配部门' }}</span>
              <span
                class="ai-pill dot"
                :class="selectedUser.is_active ? 'ok' : 'bad'"
              >
                {{ stateDisplay(selectedUser) }}
              </span>
            </div>
            <div class="ud-header-meta">
              <span class="mono-id">{{ selectedUser.username || selectedUser.id }}</span>
              <span><span class="muted">邮箱</span> {{ selectedUser.email || '—' }}</span>
              <span><span class="muted">手机</span> {{ selectedUser.phone || '—' }}</span>
              <span><span class="muted">钉钉</span> {{ selectedUser.dingtalk_user_id || '未绑定' }}</span>
            </div>
            <div class="ud-kpi-strip">
              <div v-for="item in userDrawerKpis" :key="item.label" class="ud-kpi-cell">
                <span class="ud-kpi-label">{{ item.label }}</span>
                <strong class="mono">{{ item.value }}</strong>
                <em>{{ item.hint }}</em>
              </div>
            </div>
          </div>
          <button class="ud-close" type="button" aria-label="关闭" @click="closeDrawer">
            <SfShellIcon name="x" />
          </button>
        </header>

        <nav class="ud-tabs" aria-label="用户详情视图">
          <button
            v-for="tab in userDrawerTabs"
            :key="tab.key"
            type="button"
            class="ud-tab"
            :class="{ active: userDrawerTab === tab.key }"
            @click="userDrawerTab = tab.key"
          >
            {{ tab.label }}
          </button>
        </nav>

        <div class="ud-body">
          <!-- 基本信息 -->
          <section v-if="userDrawerTab === 'overview'" class="ud-section">
            <div class="ud-section-title">基本信息</div>
            <div class="ud-section-card">
              <div class="ud-meta-row">
                <span class="ud-meta-label">邮箱</span>
                <span class="ud-meta-value mono">{{ selectedUser.email || '未设置' }}</span>
              </div>
              <div class="ud-meta-row">
                <span class="ud-meta-label">手机</span>
                <span class="ud-meta-value mono">{{ selectedUser.phone || '未设置' }}</span>
              </div>
              <div class="ud-meta-row">
                <span class="ud-meta-label">创建时间</span>
                <span class="ud-meta-value mono">{{ selectedUser.created_at ? formatTime(selectedUser.created_at) : '-' }}</span>
              </div>
              <div class="ud-meta-row">
                <span class="ud-meta-label">最近登录</span>
                <span class="ud-meta-value mono">
                  {{ selectedUser.last_login_at ? formatTime(selectedUser.last_login_at) : '从未登录' }}
                </span>
              </div>
              <div class="ud-meta-row">
                <span class="ud-meta-label">最近活跃</span>
                <span class="ud-meta-value mono">
                  {{ selectedUser.last_activity_at ? formatTime(selectedUser.last_activity_at) : '—' }}
                </span>
              </div>
              <div v-if="selectedUser.last_activity_source" class="ud-meta-row">
                <span class="ud-meta-label">活跃来源</span>
                <span class="ud-meta-value">{{ activitySourceLabel(selectedUser.last_activity_source) }}</span>
              </div>
              <div class="ud-meta-row">
                <span class="ud-meta-label">钉钉绑定</span>
                <span class="ud-meta-value mono">{{ selectedUser.dingtalk_user_id || '未绑定' }}</span>
              </div>
              <div class="ud-meta-row">
                <span class="ud-meta-label">用户状态</span>
                <span class="ud-meta-value">{{ stateDisplay(selectedUser) }}</span>
              </div>
            </div>
          </section>

          <!-- 角色与权限 -->
          <section v-if="userDrawerTab === 'permissions'" class="ud-section">
            <div class="ud-section-title">角色与权限</div>
            <div class="ud-section-card">
              <a-spin :loading="detailLoading" class="full-spin">
                <div class="ud-pill-row">
                  <SfTagChip
                    :label="(roleLabel as Record<string, string>)[selectedUser.role || ''] || (selectedUser.role || '-')"
                    :tone="roleTone(selectedUser.role || '')"
                    size="sm"
                  />
                  <span v-if="selectedUser.can_view_all" class="ud-pill ud-pill-soft">跨部门只读</span>
                </div>
                <template v-if="selectedUser.permissions && selectedUser.permissions.length > 0">
                  <div class="ud-permission-table" role="table" aria-label="权限列表">
                    <div class="ud-permission-row ud-permission-head" role="row">
                      <span>资源</span>
                      <span>操作</span>
                      <span>范围</span>
                      <span>来源</span>
                    </div>
                    <div
                      v-for="row in userPermissionRows"
                      :key="row.key"
                      class="ud-permission-row"
                      role="row"
                    >
                      <code>{{ row.resource }}</code>
                      <span>{{ row.action }}</span>
                      <span>{{ row.scope }}</span>
                      <span class="muted">{{ row.source }}</span>
                    </div>
                  </div>
                </template>
                <div v-else-if="!detailLoading" class="ud-empty-note">
                  暂无可见能力 · 该角色未配置默认权限摘要
                </div>
              </a-spin>
            </div>
          </section>

          <!-- Codex 插件状态 -->
          <section v-if="userDrawerTab === 'codex'" class="ud-section">
            <div class="ud-section-title">Codex 插件状态</div>
            <div class="ud-section-card">
              <template v-if="selectedUser.codex?.is_codex_user">
                <div class="ud-meta-row">
                  <span class="ud-meta-label">状态</span>
                  <span class="ud-pill ud-pill-info">已使用</span>
                </div>
                <div class="ud-meta-row">
                  <span class="ud-meta-label">活跃会话</span>
                  <span class="ud-meta-value mono">{{ selectedUser.codex.codex_active_sessions || 0 }}</span>
                </div>
                <div class="ud-meta-row">
                  <span class="ud-meta-label">总会话数</span>
                  <span class="ud-meta-value mono">{{ selectedUser.codex.codex_session_count || 0 }}</span>
                </div>
                <div class="ud-meta-row">
                  <span class="ud-meta-label">MCP 调用</span>
                  <span class="ud-meta-value mono">{{ selectedUser.codex.codex_mcp_call_count || 0 }}</span>
                </div>
                <div class="ud-meta-row">
                  <span class="ud-meta-label">最近活动</span>
                  <span class="ud-meta-value mono">
                    {{ selectedUser.codex.last_codex_activity_at ? formatTime(selectedUser.codex.last_codex_activity_at) : '-' }}
                  </span>
                </div>
              </template>
              <div v-else class="ud-empty-note">该用户未使用 Codex 插件</div>
            </div>
          </section>

          <!-- 最近活动 -->
          <section v-if="userDrawerTab === 'activity'" class="ud-section">
            <div class="ud-section-title">近 30 天活跃热图</div>
            <div class="ud-section-card">
              <div class="ud-heatmap">
                <span
                  v-for="cell in userActivityHeatmap"
                  :key="cell.day"
                  class="ud-heat-cell"
                  :class="`level-${cell.level}`"
                  :title="`${cell.day} · ${cell.count} 次活动`"
                />
              </div>
              <div class="ud-heat-legend">
                <span>少</span>
                <span class="ud-heat-swatch level-0" />
                <span class="ud-heat-swatch level-1" />
                <span class="ud-heat-swatch level-2" />
                <span class="ud-heat-swatch level-3" />
                <span class="ud-heat-swatch level-4" />
                <span>多</span>
              </div>
            </div>
          </section>
          <section v-if="userDrawerTab === 'activity'" class="ud-section">
            <div class="ud-section-title">最近活动</div>
            <div class="ud-section-card">
              <a-spin :loading="activityLoading" class="full-spin">
                <div v-if="recentActivity.length === 0 && !activityLoading" class="ud-empty-note">
                  暂无活动数据
                </div>
                <div v-else class="ud-activity-list">
                  <div
                    v-for="(item, idx) in recentActivity"
                    :key="idx"
                    class="ud-activity-row"
                  >
                    <span class="ud-activity-action mono">{{ item.action }}</span>
                    <span class="ud-activity-time">{{ relativeTime(item.created_at) }}</span>
                  </div>
                </div>
              </a-spin>
            </div>
          </section>

          <!-- 操作 -->
          <section v-if="userDrawerTab === 'actions'" class="ud-section">
            <div class="ud-section-title">操作</div>
            <div class="ud-section-card ud-actions">
              <button
                type="button"
                class="ai-btn primary"
                @click="onResetFromDrawer"
              >
                重置密码
              </button>
              <button
                v-if="selectedUser.is_active"
                type="button"
                class="ai-btn danger"
                :disabled="drawerActionLoading || isLastActiveSystemAdmin(selectedUser)"
                :title="isLastActiveSystemAdmin(selectedUser) ? '最后一个系统管理员不可停用' : '停用该账号'"
                @click="onDisableFromDrawer"
              >
                停用
              </button>
              <button
                v-else
                type="button"
                class="ai-btn"
                :disabled="drawerActionLoading"
                @click="onEnableFromDrawer"
              >
                启用
              </button>
              <button
                type="button"
                class="ai-btn danger"
                :disabled="drawerActionLoading || !selectedUser.dingtalk_user_id"
                :title="selectedUser.dingtalk_user_id ? '解除该账号的钉钉身份绑定' : '当前用户未绑定钉钉'"
                @click="onUnlinkDingtalkFromDrawer"
              >
                解除钉钉绑定
              </button>
            </div>
          </section>
        </div>
      </div>
    </a-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message, Modal } from '@arco-design/web-vue'
import { userApi as rawUserApi, orgApi, auditApi as rawAuditApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { roleLabel } from '@/utils/constants'
import { roleTone } from '@/utils/tone'
import { formatTime, relativeTime } from '@/utils/format'
import { userAvatarUrl } from '@/utils/avatar'
import { SfEmptyState } from '@/components/common'
import { SfTagChip, SfStatChip } from '@/components/sf'
import AdminUsersTabs from '@/layouts/AdminUsersTabs.vue'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'
import { IconInfoCircle } from '@arco-design/web-vue/es/icon'

type UserRecord = {
  id: string
  username?: string
  name?: string
  role?: string
  department?: string
  state?: string
  is_active?: boolean
  can_view_all?: boolean
  dingtalk_user_id?: string | null
  email?: string | null
  phone?: string | null
  created_at?: string | null
  last_login_at?: string
  last_activity_at?: string
  last_activity_source?: string
  permissions?: string[]
  codex?: {
    is_codex_user?: boolean
    codex_session_count?: number
    codex_active_sessions?: number
    codex_mcp_call_count?: number
    last_codex_activity_at?: string | null
  }
  avatar_url?: string
}

type ActivityEntry = { action: string; created_at: string }
type UserDrawerTab = 'overview' | 'permissions' | 'activity' | 'codex' | 'actions'

type PendingUserRow = {
  id: string
  name?: string
  dingtalk_department?: string | null
  dingtalk_department_id?: string | null
  created_at?: string | null
}

type SelectOption = { label: string; value: string }

const userApi: any = rawUserApi
const auditApi: any = rawAuditApi
const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

// 用户详情抽屉（L3-E）
const drawerOpen = ref(false)
const selectedUser = ref<UserRecord | null>(null)
const recentActivity = ref<ActivityEntry[]>([])
const activityLoading = ref(false)
const detailLoading = ref(false)
const drawerActionLoading = ref(false)
const userDrawerTab = ref<UserDrawerTab>('overview')
const userDrawerTabs: Array<{ key: UserDrawerTab; label: string }> = [
  { key: 'overview', label: '概览' },
  { key: 'permissions', label: '权限' },
  { key: 'activity', label: '活动' },
  { key: 'codex', label: 'Codex' },
  { key: 'actions', label: '操作' },
]

const userDrawerKpis = computed(() => {
  const user = selectedUser.value
  const codex = user?.codex || {}
  const permissionCount = Array.isArray(user?.permissions) ? user.permissions.length : 0
  return [
    { label: '近 30 天活动', value: String(recentActivity.value.length || 0), hint: activityLoading.value ? '加载中' : '审计记录' },
    { label: '权限键', value: String(permissionCount), hint: user?.can_view_all ? '跨部门可见' : (user?.department || '未分配') },
    { label: 'Codex 会话', value: String(codex.codex_session_count || 0), hint: `${codex.codex_active_sessions || 0} 活跃` },
    { label: 'MCP 调用', value: String(codex.codex_mcp_call_count || 0), hint: codex.last_codex_activity_at ? relativeTime(codex.last_codex_activity_at) : '无记录' },
  ]
})

const userPermissionRows = computed(() => {
  const permissions = Array.isArray(selectedUser.value?.permissions) ? selectedUser.value.permissions : []
  return permissions.map((key) => {
    const parts = String(key).split('.').filter(Boolean)
    const action = parts.length > 1 ? parts.slice(1).join('.') : 'read'
    return {
      key,
      resource: parts[0] || key,
      action,
      scope: selectedUser.value?.can_view_all ? '全局' : (selectedUser.value?.department || '当前部门'),
      source: selectedUser.value?.role || 'role',
    }
  })
})

const userActivityHeatmap = computed(() => {
  const today = new Date()
  const counts = new Map<string, number>()
  recentActivity.value.forEach((item) => {
    const d = new Date(item.created_at)
    if (Number.isNaN(d.getTime())) return
    const key = d.toISOString().slice(0, 10)
    counts.set(key, (counts.get(key) || 0) + 1)
  })
  return Array.from({ length: 30 }, (_, index) => {
    const d = new Date(today)
    d.setDate(today.getDate() - (29 - index))
    const day = d.toISOString().slice(0, 10)
    const count = counts.get(day) || 0
    return {
      day,
      count,
      level: Math.min(4, count),
    }
  })
})

function openUserDrawer(record: UserRecord, syncRoute = true) {
  // 先用列表行的字段渲染骨架，避免抽屉打开瞬间一片空白
  selectedUser.value = { ...record }
  userDrawerTab.value = 'overview'
  drawerOpen.value = true
  recentActivity.value = []
  loadUserDetail(record.id)
  loadRecentActivity(record.id)
  if (syncRoute && route.params.userId !== record.id) {
    router.push({ path: `/admin/users/${encodeURIComponent(record.id)}`, query: route.query })
  }
}

function handleUserTableRowClick(record: UserRecord) {
  if (record?.id) openUserDrawer(record)
}

function closeDrawer() {
  if (route.params.userId) {
    router.push({ path: '/admin/users', query: route.query })
    return
  }
  drawerOpen.value = false
}

function onDrawerClose() {
  if (route.params.userId) {
    router.replace({ path: '/admin/users', query: route.query })
  }
  selectedUser.value = null
  recentActivity.value = []
}

function openUserByRoute(userId: string) {
  if (!userId) return
  if (drawerOpen.value && selectedUser.value?.id === userId) return
  const record = users.value.find((item) => item.id === userId)
  openUserDrawer(record || {
    id: userId,
    username: userId,
    name: userId,
    role: '',
    state: 'active',
    is_active: true,
  }, false)
}

async function loadUserDetail(userId: string) {
  detailLoading.value = true
  try {
    const detail = await userApi.getDetail(userId)
    if (!detail) return
    // 仅当抽屉里还展示同一用户时才覆盖（避免快速点切换的 race）
    if (!selectedUser.value || selectedUser.value.id !== userId) return
    selectedUser.value = { ...selectedUser.value, ...detail }
  } catch {
    // 详情拿不到时保留列表行的兜底字段，避免抽屉信息消失
  } finally {
    detailLoading.value = false
  }
}

async function loadRecentActivity(userId: string) {
  activityLoading.value = true
  try {
    const res = await auditApi.query({ user_id: userId, page: 1, page_size: 30 })
    const items = Array.isArray(res?.items) ? res.items : []
    recentActivity.value = items.map((x: any) => ({
      action: x.action || '-',
      created_at: x.created_at || x.timestamp || '',
    }))
  } catch {
    // 接口不可用时静默降级为"暂无活动数据"
    recentActivity.value = []
  } finally {
    activityLoading.value = false
  }
}

function onResetFromDrawer() {
  if (!selectedUser.value) return
  resetPwd(selectedUser.value.id)
}

async function onDisableFromDrawer() {
  if (!selectedUser.value) return
  const id = selectedUser.value.id
  Modal.confirm({
    title: '停用用户',
    content: `确定停用用户 ${id}？`,
    okText: '确认停用',
    cancelText: '取消',
    onOk: async () => {
      await disableUser(id)
      drawerOpen.value = false
    },
  })
}

async function refreshSelectedUser(id: string) {
  await loadUsers()
  await loadUserDetail(id)
  await loadRecentActivity(id)
}

async function onEnableFromDrawer() {
  if (!selectedUser.value) return
  const id = selectedUser.value.id
  drawerActionLoading.value = true
  try {
    await userApi.updateState(id, 'active')
    Message.success('已启用')
    if (selectedUser.value?.id === id) {
      selectedUser.value = { ...selectedUser.value, is_active: true, state: 'active' }
    }
    await refreshSelectedUser(id)
  } catch (e: any) {
    Message.error(e?._message || '启用失败')
  } finally {
    drawerActionLoading.value = false
  }
}

function onUnlinkDingtalkFromDrawer() {
  if (!selectedUser.value?.dingtalk_user_id) return
  const id = selectedUser.value.id
  Modal.confirm({
    title: '解除钉钉绑定',
    content: `确定解除用户 ${id} 的钉钉绑定？解除后该用户需要重新通过钉钉登录完成绑定。`,
    okText: '确认解除',
    cancelText: '取消',
    onOk: async () => {
      drawerActionLoading.value = true
      try {
        await userApi.unlinkDingtalk(id)
        Message.success('已解除钉钉绑定')
        if (selectedUser.value?.id === id) {
          selectedUser.value = { ...selectedUser.value, dingtalk_user_id: null }
        }
        await refreshSelectedUser(id)
      } catch (e: any) {
        Message.error(e?._message || '解除失败')
      } finally {
        drawerActionLoading.value = false
      }
    },
  })
}

// 常规用户列表
const loading = ref(false)
const saving = ref(false)
const syncing = ref(false)
const users = ref<UserRecord[]>([])
const showCreate = ref(false)
const editingUser = ref<UserRecord | null>(null)
const userForm = reactive({ user_id: '', username: '', name: '', password: '', role: 'operator', department: '' })
const searchKey = ref('')
const filterRole = ref<string | undefined>(undefined)
const editableRoleOptions: SelectOption[] = [
  { label: '系统管理员', value: 'system_admin' },
  { label: '部门管理员', value: 'dept_admin' },
  { label: 'AIBP', value: 'aibp' },
  { label: '观察员', value: 'observer' },
  { label: '管理员 (legacy)', value: 'admin' },
  { label: 'AI 工程师 (legacy)', value: 'ai_engineer' },
  { label: '业务负责人 (legacy)', value: 'biz_owner' },
  { label: '运营 (legacy)', value: 'operator' },
  { label: '总监 (legacy)', value: 'director' },
]
// 设计稿: 角色 + 部门 + 状态 三个 select；状态用 filterStatusLocal 区分 active/disabled，
// 与上层 tab (active/pending/disabled) 解耦——tab 切到"激活"后仍可在"启用"内细分
const filterDepartmentName = ref<string | undefined>(undefined)
const filterStatusLocal = ref<'active' | 'disabled' | undefined>(undefined)
const currentPage = ref(1)
const pageSize = ref(50)

// URL 查询参数 ?status=active|pending|disabled 控制 tab；也兼容 /admin/users/pending 路径
const filterStatus = computed<'active' | 'pending' | 'disabled'>(() => {
  if (route.path.endsWith('/pending')) return 'pending'
  const s = route.query?.status
  if (s === 'disabled') return 'disabled'
  if (s === 'pending') return 'pending'
  return 'active'
})
const isPendingView = computed(() => filterStatus.value === 'pending')

const stats = computed(() => {
  const all = users.value
  const isAdminRole = (r?: string) => r === 'admin' || r === 'system_admin'
  return {
    total: all.length,
    active: all.filter(u => u.is_active).length,
    disabled: all.filter(u => !u.is_active).length,
    admins: all.filter(u => isAdminRole(u.role)).length,
    // 设计稿右侧 chip 行用：tab 内已激活账号总数
    activeAll: all.filter(u => u.is_active).length,
  }
})

// 设计稿右侧 chip 行 —— 系统管理员 / 部门管理员 / 员工（其余角色合并入"员工"）
const roleChips = computed(() => {
  const all = users.value.filter(u => u.is_active)
  const sysAdmin = all.filter(u => u.role === 'system_admin' || u.role === 'admin').length
  const deptAdmin = all.filter(u => u.role === 'dept_admin').length
  const employees = all.length - sysAdmin - deptAdmin
  return [
    { value: 'system_admin', label: '系统管理员', count: sysAdmin },
    { value: 'dept_admin',   label: '部门管理员', count: deptAdmin },
    { value: 'employee',     label: '员工',     count: employees },
  ]
})

const activeSystemAdminCount = computed(() =>
  users.value.filter((u) => u.is_active && isSystemAdminRole(u.role)).length,
)

const editingLastSystemAdmin = computed(() =>
  !!editingUser.value && isLastActiveSystemAdmin(editingUser.value),
)

function isSystemAdminRole(role?: string | null): boolean {
  return role === 'system_admin' || role === 'admin'
}

function isLastActiveSystemAdmin(record?: Pick<UserRecord, 'role' | 'is_active'> | null): boolean {
  return !!record?.is_active && isSystemAdminRole(record.role) && activeSystemAdminCount.value <= 1
}

// 部门 select 选项：从已加载的用户里去重得到
const deptOptions = computed(() => {
  const set = new Set<string>()
  for (const u of users.value) {
    if (u.department) set.add(u.department)
  }
  return Array.from(set).sort()
})

const filteredUsers = computed(() => {
  let list = users.value
  // 上层 tab：active / pending / disabled
  if (filterStatus.value === 'disabled') list = list.filter(u => !u.is_active)
  else list = list.filter(u => u.is_active)
  // 工具栏 状态 select：在当前 tab 内再细分（与 tab 同义时基本无副作用）
  if (filterStatusLocal.value === 'active') list = list.filter(u => u.is_active)
  else if (filterStatusLocal.value === 'disabled') list = list.filter(u => !u.is_active)
  // 搜索：用户 ID / 姓名 / username 都查
  if (searchKey.value) {
    const k = searchKey.value.toLowerCase()
    list = list.filter(u =>
      u.id?.toLowerCase().includes(k) ||
      u.name?.toLowerCase().includes(k) ||
      u.username?.toLowerCase().includes(k)
    )
  }
  // 角色 select：special chip 值 employee = 非管理员
  if (filterRole.value === 'employee') {
    list = list.filter(u => u.role !== 'system_admin' && u.role !== 'admin' && u.role !== 'dept_admin')
  } else if (filterRole.value) {
    list = list.filter(u => u.role === filterRole.value)
  }
  if (filterDepartmentName.value) {
    list = list.filter(u => u.department === filterDepartmentName.value)
  }
  return list
})

const pagedUsers = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredUsers.value.slice(start, start + pageSize.value)
})

const tablePagination = computed(() => ({
  current: currentPage.value,
  pageSize: pageSize.value,
  total: filteredUsers.value.length,
  showTotal: true,
  showPageSize: true,
}))

function onPageChange(page: number) { currentPage.value = page }
function onPageSizeChange(size: number) { currentPage.value = 1; pageSize.value = size }

const getUserAvatar = userAvatarUrl

function lastActivityTooltip(record: UserRecord): string {
  const at = record.last_activity_at || record.last_login_at
  const source = record.last_activity_source ? activitySourceLabel(record.last_activity_source) : ''
  return [formatTime(at), source].filter(Boolean).join(' · ')
}

function activitySourceLabel(source: string): string {
  const labels: Record<string, string> = {
    image_generation: '图片生成',
    llm_usage: 'AI 调用',
    audit: '操作记录',
    codex: 'Codex 插件',
    login: '登录',
  }
  return labels[source] || source
}

// 搜索/筛选变化时回到第一页
watch([searchKey, filterRole, filterStatus, filterDepartmentName, filterStatusLocal], () => { currentPage.value = 1 })
watch(() => route.params.userId, (value) => {
  const userId = typeof value === 'string' ? value : ''
  if (userId) {
    openUserByRoute(userId)
  } else if (drawerOpen.value) {
    drawerOpen.value = false
  }
}, { immediate: true })

// Codex 单元格 —— 设计稿展示 `v0.2 · 在用` mono 文本；后端目前没下发版本号，
// 用「会话/MCP」概要字符串代替，保持一行 mono 风格
function codexCellText(record: UserRecord): string {
  const c = record.codex
  if (!c?.is_codex_user) return '—'
  const sessions = c.codex_active_sessions ?? 0
  const mcp = c.codex_mcp_call_count ?? 0
  return `已使用 · ${sessions} 会话 · MCP ${mcp}`
}

// 用户状态显示 —— 优先用后端返回的 state（pending/active/disabled），
// 否则按 is_active 兜底，保持与抽屉头部 pill 一致
function stateDisplay(record: UserRecord): string {
  const map: Record<string, string> = { pending: '待激活', active: '启用', disabled: '停用' }
  if (record.state && map[record.state]) return map[record.state]
  return record.is_active ? '启用' : '停用'
}

function openCreateModal() {
  editingUser.value = null
  Object.assign(userForm, { user_id: '', username: '', name: '', password: '', role: 'observer', department: '' })
  showCreate.value = true
}

async function loadUsers() {
  loading.value = true
  try {
    const res = await userApi.list({ page_size: 1000 })
    users.value = Array.isArray(res) ? res : (res.items || [])
  } finally { loading.value = false }
}

function editUser(u: UserRecord) {
  editingUser.value = u
  Object.assign(userForm, { user_id: u.id, username: u.username, name: u.name, role: u.role, department: u.department, password: '' })
  showCreate.value = true
}

async function handleSave() {
  saving.value = true
  try {
    if (editingUser.value) {
      await userApi.update(userForm.user_id, { name: userForm.name, role: userForm.role, department: userForm.department })
    } else {
      await userApi.create(userForm)
    }
    showCreate.value = false
    editingUser.value = null
    Message.success('保存成功')
    await loadUsers()
  } catch (e: any) { Message.error(e._message || '操作失败') }
  finally { saving.value = false }
}

function resetPwd(id: string) {
  Modal.confirm({
    title: '重置密码',
    content: `确定将用户 ${id} 的密码重置为默认密码？`,
    okText: '确认重置',
    cancelText: '取消',
    onOk: async () => {
      try {
        await userApi.resetPassword(id, { new_password: 'SkillForge@2026' })
        Message.success('密码已重置为默认密码')
      } catch (e: any) { Message.error(e._message || '重置失败') }
    },
  })
}

async function disableUser(id: string) {
  try {
    if (typeof userApi.updateState === 'function') {
      await userApi.updateState(id, 'disabled')
    } else {
      await userApi.disable(id)
    }
    Message.success('已停用')
    await loadUsers()
  }
  catch (e: any) { Message.error(e._message || '操作失败') }
}

async function enableUser(id: string) {
  try {
    await userApi.updateState(id, 'active')
    Message.success('已启用')
    await loadUsers()
    if (selectedUser.value?.id === id) {
      await refreshSelectedUser(id)
    }
  } catch (e: any) {
    Message.error(e?._message || '启用失败')
  }
}

async function handleSyncDingtalk() {
  syncing.value = true
  try { await userApi.syncDingtalk(); Message.success('同步完成'); await loadUsers() }
  catch (e: any) { Message.error(e._message || '同步失败') }
  finally { syncing.value = false }
}

// ─── 待激活用户（从 PendingUsers.vue 合并过来） ───
const pendingLoading = ref(false)
const pendingRows = ref<PendingUserRow[]>([])
const filterDepartmentId = ref('')
const allDepartmentOptions = ref<SelectOption[]>([])
const activateVisible = ref(false)
const activating = ref(false)
const selectedPendingUser = ref<PendingUserRow | null>(null)
const activateForm = reactive({
  role: 'observer',
  department_id: '',
  is_manager: false,
  can_view_all: false,
})

const activateRoleOptions = computed(() => {
  if (userStore.isSystemAdmin) {
    return [
      { label: '系统管理员', value: 'system_admin' },
      { label: '部门管理员', value: 'dept_admin' },
      { label: '业务伙伴', value: 'aibp' },
      { label: '观察员', value: 'observer' },
    ]
  }
  return [
    { label: '业务伙伴', value: 'aibp' },
    { label: '观察员', value: 'observer' },
  ]
})

const visibleDepartmentOptions = computed(() => {
  if (userStore.isSystemAdmin) return allDepartmentOptions.value
  return allDepartmentOptions.value.filter((opt) => userStore.canManageDepartment(opt.value))
})

function flattenOrgTree(nodes: any[], prefix = ''): SelectOption[] {
  const result: SelectOption[] = []
  for (const node of nodes) {
    const label = prefix ? `${prefix} / ${node.name}` : node.name
    result.push({ label, value: node.id })
    result.push(...flattenOrgTree(node.children || [], label))
  }
  return result
}

async function loadDepartments() {
  try {
    const tree = await orgApi.getTree()
    allDepartmentOptions.value = flattenOrgTree(Array.isArray(tree) ? tree : [])
  } catch {
    allDepartmentOptions.value = []
  }
}

async function loadPendingUsers() {
  pendingLoading.value = true
  try {
    const params = filterDepartmentId.value ? { department_id: filterDepartmentId.value } : undefined
    const res: any = await userApi.listPending(params)
    pendingRows.value = Array.isArray(res) ? res : (res.items || [])
  } catch (e: any) {
    pendingRows.value = []
    Message.error(e?._message || '加载待激活用户失败')
  } finally {
    pendingLoading.value = false
  }
}

function resetActivateForm() {
  activateForm.role = userStore.isSystemAdmin ? 'observer' : 'aibp'
  activateForm.department_id = ''
  activateForm.is_manager = false
  activateForm.can_view_all = false
}

function openActivateModal(record: PendingUserRow) {
  selectedPendingUser.value = record
  resetActivateForm()
  const defaultDept = record.dingtalk_department_id || visibleDepartmentOptions.value[0]?.value || ''
  if (defaultDept && visibleDepartmentOptions.value.some(o => o.value === defaultDept)) {
    activateForm.department_id = defaultDept
  } else {
    activateForm.department_id = visibleDepartmentOptions.value[0]?.value || ''
  }
  activateVisible.value = true
}

async function handleActivate() {
  if (!selectedPendingUser.value) return
  if (!activateForm.department_id) {
    Message.warning('请选择归属部门')
    return
  }
  activating.value = true
  try {
    await userApi.activate(selectedPendingUser.value.id, {
      role: activateForm.role,
      department_id: activateForm.department_id,
      is_manager: activateForm.is_manager,
      can_view_all: activateForm.can_view_all,
    })
    Message.success('用户已激活')
    activateVisible.value = false
    selectedPendingUser.value = null
    await Promise.all([loadPendingUsers(), loadUsers()])
  } catch (e: any) {
    Message.error(e?._message || '激活失败')
  } finally {
    activating.value = false
  }
}

// 选 dept_admin 自动开 is_manager；选 system_admin 自动关 can_view_all
watch(() => activateForm.role, (role) => {
  activateForm.is_manager = role === 'dept_admin'
  if (role === 'system_admin') activateForm.can_view_all = false
})

// Tab 切换 → 按需加载对应数据
watch(filterStatus, async (next) => {
  if (next === 'pending') {
    if (!allDepartmentOptions.value.length) await loadDepartments()
    await loadPendingUsers()
  }
}, { immediate: true })

onMounted(() => {
  loadUsers()
  if (filterStatus.value !== 'pending') {
    loadPendingUsers()
  }
})
</script>

<style scoped>
.admin-users-page {
  gap: 0;
  padding: 0;
  max-width: none;
  margin: 0;
  min-height: calc(100vh - 92px);
  overflow-x: auto;
}
.admin-users-pagehead {
  flex: 0 0 auto;
}
.admin-users-head-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
  flex-shrink: 0;
}
.admin-users-head-actions .ai-btn {
  cursor: pointer;
  font-family: var(--ai-font-sans);
}
.admin-users-head-actions .ai-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.admin-users-head-actions svg {
  width: 12px;
  height: 12px;
  flex: 0 0 12px;
}
.admin-users-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.user-table-card,
.admin-users-pending-card {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}
.admin-users-body :deep(.table-card .arco-card-body) {
  padding: 0 !important;
}
@media (max-width: 640px) {
  .admin-users-pagehead {
    flex-direction: column;
    align-items: flex-start;
    padding: 16px;
  }
  .admin-users-page :deep(.admin-users-tabs) {
    padding: 0 16px;
    overflow-x: auto;
  }
  .admin-users-body {
    padding: 16px;
  }
  .admin-users-head-actions {
    justify-content: flex-start;
    width: 100%;
  }
}

/* 顶部按钮统一外形 —— 30px / 6px 圆角 / 12.5px 字号，对应 .ai-btn */
.admin-users-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary)) {
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
.admin-users-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary):hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
.admin-users-page :deep(.arco-btn-primary.ai-btn-like) {
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
.admin-users-page :deep(.arco-btn-primary.ai-btn-like:hover) {
  background: var(--ai-ink-2);
  border-color: var(--ai-ink-2);
}

/* 表头 —— 11.5px / uppercase / ink-4 */
.admin-users-page :deep(.arco-table-th) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 8px 12px !important;
}
.admin-users-page :deep(.arco-table-th .arco-table-th-title) {
  color: var(--ai-ink-4);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* 单元格 —— 12.5px / ink-1 / ai-border 底分隔线 */
.admin-users-page :deep(.arco-table-td) {
  font-size: 12.5px !important;
  color: var(--ai-ink-1) !important;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 10px 12px !important;
  background: var(--ai-surface) !important;
}

/* 行 hover：surface-2 */
.admin-users-page :deep(.arco-table-tr:hover .arco-table-td),
.admin-users-page :deep(.arco-table-tr-hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

/* tag pills —— 20px 高 / 4px 方角 / 11px/500 —— 按 arcoblue / green / red / orange / gray 五档 */
.admin-users-page :deep(.arco-tag.arco-tag-size-small),
.admin-users-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
}
.admin-users-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft) !important;
  color: var(--ai-info) !important;
  border-color: transparent !important;
}
.admin-users-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft) !important;
  color: var(--ai-ok) !important;
  border-color: transparent !important;
}
.admin-users-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft) !important;
  color: var(--ai-bad) !important;
  border-color: transparent !important;
}
.admin-users-page :deep(.arco-tag-color-orange),
.admin-users-page :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft) !important;
  color: var(--ai-warn) !important;
  border-color: transparent !important;
}
.admin-users-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-2) !important;
  border-color: transparent !important;
}

/* 筛选区域 —— 设计稿：搜索 / 角色 / 部门 / 状态 + 右侧 chip 统计组 */
.filter-bar {
  display: flex;
  align-items: center;
  margin-bottom: 0;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  flex-wrap: wrap;
  gap: 8px;
}
.filter-search {
  width: 280px !important;
  flex: 0 0 280px;
}
.filter-select {
  width: 140px !important;
  flex: 0 0 140px;
}
.admin-users-page :deep(.filter-select) {
  width: 140px !important;
  flex: 0 0 140px;
}
.filter-select-sm {
  width: 110px !important;
  flex: 0 0 110px;
}
.admin-users-page :deep(.filter-select-sm) {
  width: 110px !important;
  flex: 0 0 110px;
}
.filter-spacer {
  flex: 1;
  min-width: 12px;
}
.filter-chips {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 6px;
}
/* 右侧 chip 可点击：选中态使用反色（对应设计稿 line 94：background ink-1 / color white） */
.filter-chips .ai-pill {
  cursor: pointer;
  transition: background .12s, color .12s, border-color .12s;
  user-select: none;
}
.filter-chips .ai-pill:hover {
  border-color: var(--ai-border-2);
}
.filter-chips .ai-pill.selected {
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  border-color: var(--ai-ink-1);
}

/* 用户单元格 —— 头像 28px 渐变 + name + mono-id */
.user-cell-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.user-table :deep(.arco-table-tr) {
  cursor: pointer;
}
.row-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 600;
  color: var(--ai-ink-1);
  overflow: hidden;
  flex: 0 0 auto;
}
.row-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.user-cell-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.user-cell-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
  line-height: 1.2;
}
.user-cell-id {
  font-size: 11.5px;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
  line-height: 1.2;
}
/* mono-id 工具类（与设计稿 tokens.css line 304 同名）—— 抽屉头部 username 也用 */
.mono-id {
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-3);
}

/* 部门列 —— 单独样式的 ai-pill（柔和背景） */
.dept-pill {
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  border-color: transparent;
}

.dept-cell {
  font-size: 12.5px;
  color: var(--ai-ink-2);
}

.muted-cell {
  font-size: 12px;
  color: var(--ai-ink-4);
}

/* Codex 单元格 —— mono 单行，与设计稿一致 */
.codex-mono {
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-1);
}
.codex-cell {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 11.5px;
  color: var(--ai-ink-3);
}
.codex-meta {
  font-family: var(--ai-font-mono);
  font-size: 11px;
  color: var(--ai-ink-3);
}

/* 操作列 —— 设计稿用 .ai-btn sm 按钮一排，而不是 a-button text */
.row-actions {
  display: inline-flex;
  gap: 4px;
  justify-content: flex-end;
  flex-wrap: nowrap;
}
.row-actions .ai-btn.sm {
  height: 24px;
  padding: 0 8px;
  font-size: 11.5px;
  border-radius: 4px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-weight: 500;
  cursor: pointer;
  font-family: inherit;
  display: inline-flex;
  align-items: center;
  white-space: nowrap;
  transition: background .12s, border-color .12s;
}
.row-actions .ai-btn.sm:hover:not(:disabled) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
.row-actions .ai-btn.sm.danger {
  color: var(--ai-bad);
}
.row-actions .ai-btn.sm.danger:hover:not(:disabled) {
  background: var(--ai-bad-soft);
  border-color: var(--ai-bad);
}
.row-actions .ai-btn.sm:disabled {
  opacity: 0.55;
  cursor: not-allowed;
  color: var(--ai-ink-4);
}

/* 表格内 .ai-pill 在 scoped 下需重新声明（直接拷贝 ai-tokens.css 同名规则，保证 scoped 命中） */
.admin-users-page :deep(.arco-table-td) .ai-pill,
.user-drawer .ai-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  color: var(--ai-ink-2);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  white-space: nowrap;
}
.admin-users-page :deep(.arco-table-td) .ai-pill.ok,
.user-drawer .ai-pill.ok {
  color: var(--ai-ok);
  background: var(--ai-ok-soft);
  border-color: transparent;
}
.admin-users-page :deep(.arco-table-td) .ai-pill.bad,
.user-drawer .ai-pill.bad {
  color: var(--ai-bad);
  background: var(--ai-bad-soft);
  border-color: transparent;
}
.admin-users-page :deep(.arco-table-td) .ai-pill.accent,
.user-drawer .ai-pill.accent {
  color: var(--ai-accent-ink, var(--ai-info));
  background: var(--ai-accent-soft, var(--ai-info-soft));
  border-color: transparent;
}
.admin-users-page :deep(.arco-table-td) .ai-pill.dot::before,
.user-drawer .ai-pill.dot::before {
  content: '';
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}
/* filter-bar 的 ai-pill 也需要兜底（scoped 隔离）*/
.filter-chips .ai-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  color: var(--ai-ink-2);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  white-space: nowrap;
}

/* 底部统计条 */
.bottom-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--ai-border);
}
.stats-inline {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

/* pending tab 顶部统计 */
.pending-summary {
  display: flex;
  gap: 6px;
  margin-bottom: 12px;
}

/* pending 表格内的用户单元格（旧结构保留兼容） */
.user-cell { display: flex; flex-direction: column; gap: 2px; }
.user-name { font-weight: 500; color: var(--ai-ink-1); font-size: 13px; }
.user-id { font-size: 11.5px; color: var(--ai-ink-4); font-family: var(--ai-font-mono); }

/* Modal 内辅助元素 */
.modal-static {
  padding: 6px 10px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  font-size: 12.5px;
  color: var(--ai-ink-1);
}
.switch-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.switch-copy {
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 1.5;
}
.activate-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  padding: 6px 10px;
  background: var(--ai-info-soft);
  border-left: 2px solid var(--ai-info);
  border-radius: 4px;
  font-size: 12px;
  color: var(--ai-ink-2);
}
.activate-hint code {
  font-family: var(--ai-font-mono);
  background: var(--ai-surface);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 11px;
  color: var(--ai-info);
  border: 1px solid var(--ai-border);
}

/* ── L3-E · 用户详情抽屉 ── */
.user-cell-row.clickable {
  cursor: pointer;
}
.user-cell-row.clickable:hover .user-cell-name {
  color: var(--ai-info);
}

/* 抽屉 chrome：去掉默认 padding，自接管 */
.admin-users-page :deep(.arco-drawer-body) {
  padding: 0 !important;
  background: var(--ai-bg);
}
.admin-users-page :deep(.arco-drawer) {
  background: var(--ai-bg);
}

.user-drawer {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--ai-bg);
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}

.ud-header {
  display: flex;
  align-items: flex-start;
  gap: 18px;
  padding: 20px 24px;
  background: var(--ai-surface);
  border-bottom: 1px solid var(--ai-border);
}
/* 64px 头像 + 渐变背景（对齐 L3-E 设计稿 line 620） */
.ud-avatar-wrap {
  flex: 0 0 auto;
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  display: grid;
  place-items: center;
  overflow: hidden;
  color: var(--ai-ink-1);
}
.ud-avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.ud-avatar-fallback {
  font-size: 24px;
  font-weight: 600;
  color: var(--ai-ink-1);
}
.ud-header-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
/* name + pills 一行（对齐 L3-E 设计稿 line 622） */
.ud-header-name-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.ud-header-name {
  font-size: 22px;
  font-weight: 600;
  color: var(--ai-ink-1);
  letter-spacing: -0.02em;
  line-height: 1.2;
}
/* 用户名 / 邮箱 / 手机 / 钉钉 一行 (对齐 L3-E 设计稿 line 627) */
.ud-header-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px;
  font-size: 12px;
  color: var(--ai-ink-3);
}
.ud-header-meta .muted {
  color: var(--ai-ink-4);
}
.ud-header-meta .mono-id {
  font-family: var(--ai-font-mono);
  font-size: 12px;
  color: var(--ai-ink-3);
}
.ud-kpi-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0;
  margin-top: 4px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  overflow: hidden;
  background: var(--ai-bg);
}
.ud-kpi-cell {
  min-width: 0;
  padding: 9px 10px;
  border-right: 1px solid var(--ai-border);
}
.ud-kpi-cell:last-child {
  border-right: 0;
}
.ud-kpi-label {
  display: block;
  color: var(--ai-ink-4);
  font-size: 11px;
  line-height: 1.2;
}
.ud-kpi-cell strong {
  display: block;
  margin-top: 2px;
  color: var(--ai-ink-1);
  font-size: 18px;
  line-height: 1.15;
  font-weight: 600;
}
.ud-kpi-cell em {
  display: block;
  margin-top: 2px;
  color: var(--ai-ink-4);
  font-size: 11px;
  line-height: 1.2;
  font-style: normal;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ud-close {
  flex: 0 0 auto;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-3);
  display: grid;
  place-items: center;
  cursor: pointer;
}
.ud-close:hover {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
}

.ud-tabs {
  display: flex;
  gap: 18px;
  padding: 0 24px;
  background: var(--ai-surface);
  border-bottom: 1px solid var(--ai-border);
}
.ud-tab {
  height: 42px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--ai-ink-3);
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
}
.ud-tab:hover {
  color: var(--ai-ink-1);
}
.ud-tab.active {
  color: var(--ai-ink-1);
  border-bottom-color: var(--ai-ink-1);
}

/* 通用 pill —— 与设计系统一致 */
.ud-pill {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  border: 1px solid transparent;
  white-space: nowrap;
}
.ud-pill-soft {
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.ud-pill-ok {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
}
.ud-pill-muted {
  background: var(--ai-surface-2);
  color: var(--ai-ink-4);
}
.ud-pill-info {
  background: var(--ai-info-soft);
  color: var(--ai-info);
}

.ud-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 18px 24px 28px;
  background: var(--ai-bg);
}

.ud-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ud-section-title {
  font-size: 11px;
  font-weight: 500;
  color: var(--ai-ink-4);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.ud-section-card {
  padding: 14px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ud-meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
}
.ud-meta-label {
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-weight: 500;
}
.ud-meta-value {
  color: var(--ai-ink-1);
  text-align: right;
  max-width: 60%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ud-meta-value.mono {
  font-family: var(--ai-font-mono);
  font-size: 12px;
}

.ud-pill-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

/* 权限键 pill —— 用 code 元素 + mono 字体 + soft 背景 */
.ud-permission-table {
  margin-top: 10px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  overflow: hidden;
}
.ud-permission-row {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr) minmax(96px, .7fr) minmax(80px, .6fr);
  gap: 10px;
  align-items: center;
  min-height: 34px;
  padding: 7px 10px;
  border-bottom: 1px solid var(--ai-border);
  font-size: 12px;
}
.ud-permission-row:last-child {
  border-bottom: 0;
}
.ud-permission-head {
  min-height: 30px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
}
.ud-permission-row code {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-1);
}
.ud-permission-row .muted {
  color: var(--ai-ink-4);
}

.ud-empty-note {
  padding: 6px 0;
  color: var(--ai-ink-4);
  font-size: 12px;
}

.ud-heatmap {
  display: grid;
  grid-template-columns: repeat(15, 1fr);
  gap: 3px;
}
.ud-heat-cell {
  aspect-ratio: 1;
  border-radius: 3px;
  background: var(--ai-surface-3);
}
.ud-heat-cell.level-1,
.ud-heat-swatch.level-1 {
  background: var(--ai-accent-soft);
}
.ud-heat-cell.level-2,
.ud-heat-swatch.level-2 {
  background: var(--ai-info-soft);
}
.ud-heat-cell.level-3,
.ud-heat-swatch.level-3 {
  background: var(--ai-accent);
}
.ud-heat-cell.level-4,
.ud-heat-swatch.level-4 {
  background: var(--ai-info);
}
.ud-heat-legend {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  margin-top: 8px;
  color: var(--ai-ink-4);
  font-size: 10.5px;
}
.ud-heat-swatch {
  width: 9px;
  height: 9px;
  border-radius: 2px;
  background: var(--ai-surface-3);
}

.ud-activity-list {
  display: flex;
  flex-direction: column;
  gap: 0;
}
.ud-activity-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--ai-border);
  font-size: 12.5px;
}
.ud-activity-row:last-child {
  border-bottom: 0;
}
.ud-activity-action {
  color: var(--ai-ink-2);
  font-family: var(--ai-font-mono);
  font-size: 12px;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ud-activity-time {
  color: var(--ai-ink-4);
  font-size: 11.5px;
  flex: 0 0 auto;
}

.ud-actions {
  flex-direction: row;
  flex-wrap: wrap;
  gap: 8px;
}

/* 抽屉里的按钮 —— .ai-btn 复刻 */
.user-drawer .ai-btn {
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  font-family: inherit;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.user-drawer .ai-btn:hover:not(:disabled) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
.user-drawer .ai-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.user-drawer .ai-btn.primary {
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
  color: var(--ai-surface);
}
.user-drawer .ai-btn.primary:hover:not(:disabled) {
  background: var(--ai-ink-2);
  border-color: var(--ai-ink-2);
}

/* Admin sweep utilities: remove inline style / hard-coded color drift */
.pending-department-select {
  width: 220px;
}
.full-spin {
  width: 100%;
}
.user-drawer .ai-btn.danger {
  color: var(--ai-bad);
}
.user-drawer .ai-btn.danger:hover:not(:disabled) {
  border-color: var(--ai-bad);
  background: var(--ai-bad-soft);
}
@media (max-width: 900px) {
  .admin-users-pagehead,
  .filter-bar,
  .ud-header,
  .ud-tabs,
  .ud-body {
    padding-inline: 16px;
  }
  .filter-search,
  .pending-department-select {
    width: 100% !important;
    flex: 1 1 100%;
  }
  .ud-kpi-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

</style>
