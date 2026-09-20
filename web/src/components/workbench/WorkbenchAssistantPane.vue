<template>
  <div class="wb-asst">
    <!-- 头部 -->
    <div class="asst-head">
      <span class="asst-title ai-gradient-text">AI 助手</span>
      <span v-if="activeModule" class="asst-ctx">@{{ moduleLabel }}</span>
      <span v-if="sessionMeta?.model" class="asst-meta">{{ sessionMeta.model }}</span>
      <span v-if="sessionMeta?.prompt_hash" class="asst-meta asst-meta-mono">prompt {{ sessionMeta.prompt_hash }}</span>
      <span v-if="sessionMeta?.tools_count" class="asst-meta">{{ sessionMeta.tools_count }} tools</span>
      <span v-if="sessionMeta?.runtime_profile?.provider" class="asst-meta">{{ sessionMeta.runtime_profile.provider }}</span>
      <span v-if="sessionMeta?.runtime_profile?.permission_strategy" class="asst-meta">{{ sessionMeta.runtime_profile.permission_strategy }}</span>
      <span v-if="sessionMeta?.runtime_profile?.bare_mode" class="asst-meta">bare</span>
      <span v-if="studioMode === 'explain'" class="asst-meta">讲解模式</span>
      <span v-if="studioMode === 'review'" class="asst-meta">审批模式</span>

      <!-- Phase 4: 跟踪模式开关 -->
      <a-tooltip :content="trackingEnabled ? '跟踪模式已开 — AI 改文件时编辑器自动定位' : '点击开启跟踪模式'">
        <button
          class="asst-tracking-btn"
          :class="{ active: trackingEnabled }"
          @click="$emit('toggle-tracking')"
        >
          <icon-eye :size="13" />
          <span>{{ trackingEnabled ? '跟踪中' : '跟踪' }}</span>
        </button>
      </a-tooltip>

      <!-- M3: 会话选择器 -->
      <a-dropdown v-if="conversations.length > 0" trigger="click">
        <button class="asst-conv-btn" title="切换会话">
          <icon-message :size="14" />
          <span v-if="conversations.length > 1">{{ conversations.length }}</span>
        </button>
        <template #content>
          <a-doption v-for="c in conversations" :key="c.id" @click="$emit('switch-conversation', c.id)">
            {{ c.title || c.id?.slice(0, 8) || '对话' }}
          </a-doption>
          <a-divider style="margin:4px 0" />
          <a-doption @click="$emit('new-conversation')">+ 新建对话</a-doption>
        </template>
      </a-dropdown>
      <button v-else class="asst-conv-btn" title="新建对话" @click="$emit('new-conversation')">
        <icon-plus :size="14" />
      </button>
      <button class="asst-close" @click="$emit('close')"><icon-close :size="14" /></button>
    </div>

    <!-- 消息流 -->
    <div class="asst-scroll" ref="scrollEl">
      <div v-if="taskContract" class="asst-v7">
        <div class="asst-v7-head">
          <span class="asst-v7-title">任务合同</span>
          <a-tag v-if="taskContract.risks?.level" size="small" :color="taskContract.risks.level === 'R3' ? 'red' : 'orange'">
            {{ taskContract.risks.level }}
          </a-tag>
        </div>
        <div class="asst-v7-goal">{{ taskContract.goal }}</div>
        <div class="asst-v7-meta">
          <span>{{ taskContract.trigger?.description || taskContract.trigger?.type }}</span>
          <span>{{ taskContract.output?.adapter }} → {{ taskContract.output?.recipient || '待确认' }}</span>
        </div>
        <div v-if="taskGateItems.length" class="asst-v7-gates">
          <div v-for="item in taskGateItems" :key="item.key" class="asst-v7-gate">
            <a-tag size="small" :color="item.passed ? 'green' : 'orange'">{{ item.passed ? '通过' : '待完成' }}</a-tag>
            <span>{{ item.label }}</span>
          </div>
        </div>
        <pre v-if="taskPreviewText" class="asst-v7-preview">{{ taskPreviewText }}</pre>
      </div>

      <!-- 诊断卡（首次显示，无消息时） -->
      <div v-if="!messages?.length && !loading && !isCreate" class="asst-diagnosis">
        <div class="diag-header">
          <icon-robot :size="18" class="ai-gradient-text" />
          <span class="diag-title">已分析 <strong>{{ skillName || '当前 Skill' }}</strong></span>
        </div>
        <div class="diag-stats">
          <span class="diag-stat">目标 {{ goalLen }} 字</span>
          <span class="diag-stat">规则 {{ rulesLen }} 条</span>
          <span class="diag-stat">参数 {{ paramsLen }} 个</span>
          <span class="diag-stat">测试 {{ testsLen }} 个</span>
        </div>
        <div class="diag-actions">
          <button class="diag-btn" @click="sendQuick('帮我优化目标描述，使其更清晰具体')">优化目标</button>
          <button class="diag-btn" @click="sendQuick('检查规则是否有缺失的异常/拒绝分支')">补全异常分支</button>
          <button class="diag-btn" @click="sendQuick('从目标和规则中提取可配置的参数')">提取参数</button>
          <button class="diag-btn" @click="sendQuick('/generate-tests')">生成测试用例</button>
          <button class="diag-btn" @click="sendQuick('用简单的话解释这个 Skill 的执行逻辑')">解释执行逻辑</button>
        </div>
        <div class="diag-divider" />
        <div class="diag-free">或直接输入你想改的内容</div>
      </div>

      <!-- 创建模式空状态 -->
      <div v-else-if="!messages?.length && !loading && isCreate" class="asst-empty">
        <icon-robot :size="24" style="color: var(--ai-ink-4)" />
        <div>描述你想创建的 Skill，AI 帮你生成完整骨架</div>
        <div class="asst-hints">
          <button v-for="h in hints" :key="h" class="hint" @click="sendQuick(h)">{{ h }}</button>
        </div>
      </div>

      <!-- Phase 2 Change Coach 建议卡（事件驱动主动介入） -->
      <div v-if="coachSuggestions.length && !isCreate" class="asst-coach">
        <div class="coach-head">
          <icon-bulb :size="13" class="ai-gradient-text" />
          <span>Change Coach 建议</span>
          <span class="coach-count">{{ coachSuggestions.length }}</span>
        </div>
        <div class="coach-list">
          <template v-for="s in coachSuggestions.slice(0, 3)" :key="s.id">
            <!-- §4.3 Evidence Card：高影响改动专用证据卡 -->
            <div v-if="s.kind === 'evidence_card'" class="evidence-card" :class="`sev-${s.severity}`">
              <div class="ec-head">
                <a-tag size="small" :color="coachSevColor(s.severity)">{{ coachSevLabel(s.severity) }}</a-tag>
                <icon-bar-chart :size="13" />
                <span class="ec-title">{{ s.title }}</span>
              </div>
              <div v-if="s.description" class="ec-desc">{{ s.description }}</div>
              <!-- 证据字段 -->
              <div v-if="s.evidence" class="ec-evidence">
                <div v-if="s.evidence.relative_change !== undefined && s.evidence.relative_change !== null" class="ec-row">
                  <span class="ec-label">变化幅度</span>
                  <span class="ec-val" :class="s.evidence.is_major_change ? 'ec-major' : ''">
                    {{ Math.round(s.evidence.relative_change * 100) }}%
                    <a-tag v-if="s.evidence.is_major_change" size="small" color="red" style="margin-left:4px">超过 ±20%</a-tag>
                  </span>
                </div>
                <div v-if="s.evidence.old !== undefined" class="ec-row">
                  <span class="ec-label">旧值 → 新值</span>
                  <span class="ec-val"><code>{{ s.evidence.old }}</code> → <code>{{ s.evidence.new }}</code></span>
                </div>
                <div v-if="s.evidence.affected_branches?.length" class="ec-row">
                  <span class="ec-label">影响分支</span>
                  <div class="ec-branches">
                    <div v-for="(br, i) in s.evidence.affected_branches.slice(0, 3)" :key="i" class="ec-branch">
                      <a-tag size="small">{{ br.step }}</a-tag>
                      <code>{{ br.condition }}</code>
                    </div>
                    <div v-if="s.evidence.affected_branches.length > 3" class="ec-more">
                      还有 {{ s.evidence.affected_branches.length - 3 }} 条...
                    </div>
                  </div>
                </div>
                <!-- 跨 Skill 下游影响（参数倒排索引） -->
                <div v-if="s.evidence.cross_skill_count" class="ec-row">
                  <span class="ec-label">下游 Skill</span>
                  <div class="ec-branches">
                    <div class="ec-branch ec-cross">
                      <a-tag size="small" color="orange">{{ s.evidence.cross_skill_count }} 个</a-tag>
                      <span style="color: var(--ai-ink-2); font-size: 11px;">
                        全库还有 {{ s.evidence.cross_skill_count }} 个 Skill 也用了此字段
                      </span>
                    </div>
                    <div
                      v-for="(u, i) in (s.evidence.cross_skill_usages || []).slice(0, 3)"
                      :key="`xs-${i}`"
                      class="ec-branch"
                    >
                      <a-tag size="small" color="purple">{{ u.skill_name }}</a-tag>
                      <code style="font-size: 10px;">{{ u.condition }}</code>
                    </div>
                  </div>
                </div>
              </div>
              <div v-if="s.action" class="ec-actions">
                <button class="ec-btn ec-btn-primary" @click="$emit('coach-action', s)">{{ coachActionLabel(s.action) }}</button>
                <button class="ec-btn" @click="$emit('coach-action', { ...s, action: 'simulate_replay' })">查看回放</button>
                <button class="ec-btn ec-btn-ghost" @click="$emit('coach-dismiss', s)">忽略</button>
              </div>
            </div>
            <!-- 普通建议卡（inline / diff） -->
            <div v-else class="coach-card" :class="`sev-${s.severity}`">
              <div class="cc-head">
                <a-tag size="small" :color="coachSevColor(s.severity)">{{ coachSevLabel(s.severity) }}</a-tag>
                <span class="cc-title">{{ s.title }}</span>
              </div>
              <div v-if="s.description" class="cc-desc">{{ s.description }}</div>
              <div v-if="s.action" class="cc-actions">
                <button class="cc-btn" @click="$emit('coach-action', s)">{{ coachActionLabel(s.action) }}</button>
              </div>
            </div>
          </template>
        </div>
      </div>

      <!-- 消息列表 -->
      <template v-for="(msg, i) in (messages || [])" :key="i">
        <!-- F1: 压缩边界 Chip（替代普通气泡） -->
        <CompactBoundaryChip
          v-if="msg._boundary"
          :original-count="msg._original_count || 0"
          :content="msg.content || ''"
        />
        <!-- 普通消息气泡 -->
        <div v-else :class="['msg', msg.role]">
          <div v-if="msg.role !== 'user'" class="msg-avatar"><icon-robot :size="13" /></div>
          <div class="msg-body">
            <div v-if="msg.content || msg.message || msg.text" class="msg-bubble"
                 v-html="renderMd(msg.content || msg.message || msg.text)" />
            <!-- streaming 状态但还没有内容 → 显示动态状态条:
                 dots + "AI 思考中... X 秒" + 当前正在执行的工具名 -->
            <div v-else-if="msg.streaming && msg.role === 'assistant'" class="msg-bubble msg-bubble-streaming">
              <span class="dots" />
              <span class="streaming-text">{{ streamingStatusText(msg) }}</span>
            </div>
            <!-- Phase 4: 工具调用 — 单窗口终端视图 -->
            <div v-if="msg.toolCalls?.length" class="msg-tool-window">
              <div class="tw-head" @click="toolWindowCollapsed[i] = !toolWindowCollapsed[i]" style="cursor:pointer">
                <icon-command :size="12" />
                <span class="tw-title">工具执行</span>
                <span class="tw-count">{{ msg.toolCalls.length }}</span>
                <span class="tw-status" :class="`tw-st-${toolWindowOverallStatus(msg.toolCalls)}`">
                  {{ toolWindowStatusText(msg.toolCalls) }}
                </span>
                <icon-right v-if="toolWindowCollapsed[i]" :size="10" style="margin-left:auto;color:var(--ai-ink-3)" />
                <icon-down v-else :size="10" style="margin-left:auto;color:var(--ai-ink-3)" />
              </div>
              <div v-if="!toolWindowCollapsed[i]" class="tw-body" :ref="el => twBodyRefs[i] = el">
                <div v-for="(tc, ti) in msg.toolCalls" :key="tc.id || ti"
                     class="tw-item" :class="`tw-${tc.status || 'pending'}`"
                     @click="toggleToolDetail(i, ti)">
                  <div class="tw-item-line">
                    <component :is="toolIcon(tc.tool)" :size="11" />
                    <span class="tw-item-tool">{{ tc.tool }}</span>
                    <span class="tw-item-summary">{{ summarizeToolInput(tc.tool, tc.input) }}</span>
                    <span class="tw-item-st">{{ tcStatusIcon(tc.status) }}</span>
                  </div>
                  <div v-if="expandedToolDetail[`${i}_${ti}`] && tc.result" class="tw-item-result"
                       :class="tc.status === 'error' ? 'tw-item-result-err' : ''">
                    {{ String(tc.result).slice(0, 500) }}
                  </div>
                </div>
              </div>
            </div>
            <template v-for="runtime in [messageRuntimePreview(msg)]" :key="`runtime-${i}`">
              <div
                v-if="msg.role !== 'user' && (runtime.todos.length || runtime.reports.length)"
                class="msg-runtime-output"
              >
                <div class="mro-head">
                  <icon-check-circle :size="12" />
                  <span class="mro-title">运行输出</span>
                  <span class="mro-count">待办 {{ runtime.todos.length }}</span>
                  <span class="mro-count">报告 {{ runtime.reports.length }}</span>
                </div>
                <div v-if="runtime.todos.length" class="mro-section">
                  <div class="mro-section-title">会进入收件-待办</div>
                  <div v-for="(todo, ti) in runtime.todos" :key="`todo-${ti}`" class="mro-card">
                    <div class="mro-card-head">
                      <a-tag size="small" :color="todoKindColor(todo.kind)">{{ todoKindLabel(todo.kind) }}</a-tag>
                      <strong>{{ todo.title || `待办 ${ti + 1}` }}</strong>
                    </div>
                    <div v-if="todo.summary" class="mro-muted">{{ todo.summary }}</div>
                    <div class="mro-meta">
                      <span v-if="todo.reviewer_role">角色 {{ todo.reviewer_role }}</span>
                      <span v-if="todo.reviewers?.length">处理人 {{ todo.reviewers.join(', ') }}</span>
                      <span v-if="todo.sla_hours">SLA {{ todo.sla_hours }}h</span>
                      <span v-if="todo.tasks?.length">任务 {{ todo.tasks.length }}</span>
                    </div>
                    <div v-if="todo.tasks?.length" class="mro-task-list">
                      <div v-for="(task, taskIndex) in todo.tasks.slice(0, 3)" :key="taskIndex" class="mro-task">
                        <span class="mro-task-index">{{ taskIndex + 1 }}</span>
                        <span class="mro-task-content">{{ task.content || '-' }}</span>
                        <span v-if="task.executor" class="mro-task-meta">{{ task.executor }}</span>
                      </div>
                    </div>
                  </div>
                </div>
                <div v-else class="mro-muted">本次输出未包含 <code>todos</code>，不会进入收件-待办。</div>
                <div v-if="runtime.reports.length" class="mro-section">
                  <div class="mro-section-title">会进入收件-报告</div>
                  <div v-for="(report, ri) in runtime.reports" :key="`report-${ri}`" class="mro-card">
                    <div class="mro-card-head">
                      <strong>{{ report.title || `报告 ${ri + 1}` }}</strong>
                    </div>
                    <div v-if="report.channel || report.summary" class="mro-muted">
                      {{ [report.channel, report.summary].filter(Boolean).join(' · ') }}
                    </div>
                  </div>
                </div>
                <div v-else class="mro-muted">本次输出未包含 <code>reports</code>，不会进入收件-报告。</div>
              </div>
            </template>
            <div v-if="msg.role !== 'user' && messageUsageItems(msg).length" class="msg-usage-strip">
              <span v-for="item in messageUsageItems(msg)" :key="item.label" class="usage-pill">
                <span class="usage-label">{{ item.label }}</span>
                <span class="usage-value">{{ item.value }}</span>
              </span>
            </div>
            <!-- Phase 4: AI 文件变更摘要 -->
            <div v-if="msg.fileChanges?.length" class="msg-file-changes">
              <div v-for="(fc, fi) in msg.fileChanges" :key="fi"
                   class="file-change-row"
                   :class="{ active: props.activeFile === fc.path }"
                   @click="$emit('jump-to-file', fc)">
                <icon-edit :size="11" />
                <span class="fc-path">{{ fc.path }}</span>
                <span class="fc-op">{{ fcOpLabel(fc.operation) }}</span>
                <span class="fc-action">{{ props.activeFile === fc.path ? '已打开' : '点击查看' }}</span>
              </div>
            </div>
            <div v-if="msg.gitCommit" class="msg-git-commit">
              <div class="gc-main">
                <icon-history :size="12" />
                <span class="gc-label">Git commit</span>
                <code>{{ msg.gitCommit }}</code>
              </div>
              <div v-if="diffSummaryText(msg.diffSummary)" class="gc-summary">
                {{ diffSummaryText(msg.diffSummary) }}
              </div>
              <button class="gc-action" @click="openCommitDiff(msg.gitCommitFull || msg.gitCommit)">
                查看 diff
              </button>
            </div>
            <button v-if="msg.role !== 'user'" class="msg-copy-btn" title="复制" @click="copyMessage(msg)">
              <icon-copy :size="11" />
            </button>
            <div v-if="msg.module_id" class="msg-meta">@{{ msg.module_id }}</div>
          </div>
          <div v-if="msg.role === 'user'" class="msg-avatar user"><icon-user :size="13" /></div>
        </div>
      </template>

      <!-- 打字动画
           只在没有 streaming placeholder 时才渲染独立的"等待气泡"。
           否则会和 sendViaStream 推入 messages 的 streaming=true placeholder
           各自渲染一个 avatar，导致出现两个机器人头像。 -->
      <div v-if="loading && !hasStreamingPlaceholder" class="msg assistant">
        <div class="msg-avatar"><icon-robot :size="13" /></div>
        <div class="msg-bubble"><span class="dots" /></div>
      </div>
    </div>

    <!-- 引用选择器 -->
    <div v-if="showRefPicker" class="asst-refs">
      <WorkbenchReferencePicker
        :references="references"
        :model-value="selectedReferenceIds"
        @update:modelValue="ids => $emit('update-references', ids)"
      />
    </div>

    <!-- Phase 4: 权限审批卡片（AI 想跑命令时弹出） -->
    <div v-if="pendingPermission" class="asst-permission">
      <div class="perm-banner">
        <icon-exclamation-circle-fill :size="18" style="color: #E67700; flex-shrink: 0" />
        <span>AI 助手需要你的确认才能继续</span>
      </div>
      <div class="perm-body">
        <div class="perm-tool-label">操作：<b>{{ pendingPermission.tool }}</b></div>
        <pre class="perm-input">{{ formatPermissionInput(pendingPermission.input) }}</pre>
        <div class="perm-actions">
          <a-button type="primary" size="large" long @click="$emit('respond-permission', { behavior: 'allow', updatedInput: pendingPermission.input })">
            同意执行
          </a-button>
          <div class="perm-secondary-actions">
            <a-button size="small" status="danger" @click="$emit('respond-permission', { behavior: 'deny', message: '用户拒绝' })">
              拒绝
            </a-button>
            <a-button size="small" @click="showPermEdit = !showPermEdit">
              {{ showPermEdit ? '取消' : '修改后执行' }}
            </a-button>
          </div>
        </div>
        <template v-if="showPermEdit">
          <a-textarea
            v-model="permEditDraft"
            :auto-size="{ minRows: 2, maxRows: 6 }"
            placeholder="编辑 input JSON 后点'同意执行'"
            style="margin-top: 8px; font-family: monospace; font-size: 12px"
          />
          <a-button type="primary" size="small" style="margin-top: 6px" @click="submitEditedPermission">
            按编辑后的内容执行
          </a-button>
        </template>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="asst-input ai-input-glow">
      <button class="input-ref-btn" :class="{ active: showRefPicker }" title="引用来源" @click="showRefPicker = !showRefPicker">
        <icon-link :size="14" />
      </button>
      <label class="input-ref-btn" title="上传报告生成 Skill">
        <icon-upload :size="14" />
        <input type="file" accept=".txt,.md,.doc,.docx,.pdf" style="display:none" @change="handleFileUpload" />
      </label>
      <label class="input-ref-btn" :class="{ active: pendingImages.length > 0 }" title="上传图片（截图/图表）">
        <icon-image :size="14" />
        <input type="file" accept="image/*" multiple style="display:none" @change="handleImageUpload" />
      </label>
      <div v-if="pendingImages.length" class="image-preview-bar">
        <div v-for="(img, idx) in pendingImages" :key="idx" class="image-thumb">
          <img :src="'data:' + img.media_type + ';base64,' + img.data" />
          <button class="image-remove" @click="pendingImages.splice(idx, 1)">×</button>
        </div>
      </div>
      <a-textarea
        v-model="draft"
        :auto-size="{ minRows: 1, maxRows: 4 }"
        :placeholder="inputPlaceholder"
        :disabled="studioMode === 'review'"
        class="input-ta"
        @keydown.enter.exact.prevent="handleSend"
      />
      <!-- F2: 生成中显示停止按钮 -->
      <button
        v-if="loading"
        class="input-send ai-send-btn"
        title="停止生成"
        @click="$emit('stop')"
      >
        <icon-pause :size="14" />
      </button>
      <button
        v-else
        class="input-send ai-send-btn"
        :disabled="studioMode === 'review' || !draft.trim()"
        @click="handleSend"
      >
        <icon-arrow-up :size="16" />
      </button>
    </div>

    <!-- F1: 上下文预算进度条（仅有数据时显示） -->
    <BudgetProgress
      v-if="budgetLimit"
      :used="budgetUsed"
      :limit="budgetLimit"
      :status="budgetStatus"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch, nextTick, onBeforeUnmount, type PropType } from 'vue'
import { IconClose, IconRobot, IconUser, IconArrowUp, IconLink, IconMessage, IconPlus, IconUpload, IconBulb, IconBarChart, IconCopy, IconPause, IconEye, IconLock, IconEdit, IconCode, IconFile, IconSearch, IconFolder, IconCommand, IconLoading, IconCheckCircle, IconCloseCircle, IconExclamationCircleFill, IconDown, IconRight, IconImage, IconHistory } from '@arco-design/web-vue/es/icon'
import { Message } from '@arco-design/web-vue'
import WorkbenchReferencePicker from './WorkbenchReferencePicker.vue'
import CompactBoundaryChip from '@/components/CompactBoundaryChip.vue'
import BudgetProgress from '@/components/BudgetProgress.vue'
import { renderMd } from '@/utils/renderMd'
import {
  normalizeRuntimeOutputPreview,
  type RuntimeOutputPreview,
} from '@/utils/runtimeOutputPreview'

const props = defineProps({
  messages: { type: Array as PropType<any[]>, default: () => [] },
  loading: { type: Boolean, default: false },
  activeModule: { type: String, default: '' },
  activeFile: { type: String, default: '' },
  selection: { type: Object as PropType<any>, default: null },
  references: { type: Array as PropType<any[]>, default: () => [] },
  selectedReferenceIds: { type: Array as PropType<Array<string | number>>, default: () => [] },
  isCreate: { type: Boolean, default: false },
  conversations: { type: Array as PropType<any[]>, default: () => [] },
  doc: { type: Object as PropType<any>, default: () => ({}) },
  // Phase 2 Change Coach 建议列表
  coachSuggestions: { type: Array as PropType<any[]>, default: () => [] },
  // F1: 上下文预算（来自 testApi.getConversation 的响应）
  budgetUsed: { type: Number, default: 0 },
  budgetLimit: { type: Number, default: 0 },
  budgetStatus: { type: String, default: 'ok' },
  // Phase 4: 跟踪模式 + 权限审批
  trackingEnabled: { type: Boolean, default: false },
  pendingPermission: { type: Object as PropType<any>, default: null },
  sessionMeta: { type: Object as PropType<any>, default: null },
  studioMode: { type: String, default: 'edit' },
})

const emit = defineEmits([
  'send', 'close', 'update-references', 'switch-conversation', 'new-conversation',
  'upload-report', 'coach-action', 'coach-dismiss', 'stop',
  // Phase 4
  'toggle-tracking', 'respond-permission', 'jump-to-file',
])

// Phase 4: 权限审批编辑态
const showPermEdit = ref(false)
const permEditDraft = ref('')
watch(() => props.pendingPermission, (req) => {
  if (req) {
    permEditDraft.value = JSON.stringify(req.input || {}, null, 2)
    showPermEdit.value = false
  } else {
    permEditDraft.value = ''
    showPermEdit.value = false
  }
})

function formatPermissionInput(input: any) {
  try { return JSON.stringify(input || {}, null, 2) }
  catch { return String(input) }
}

function submitEditedPermission() {
  let parsed
  try {
    parsed = JSON.parse(permEditDraft.value)
  } catch (e) {
    Message.error('JSON 解析失败：' + (e instanceof Error ? e.message : String(e)))
    return
  }
  emit('respond-permission', { behavior: 'allow', updatedInput: parsed })
}

// 是否已有 streaming 状态的 assistant placeholder（避免和"等待气泡"重复渲染头像）
const hasStreamingPlaceholder = computed(() => {
  const list = props.messages || []
  for (let i = list.length - 1; i >= 0; i--) {
    const m = list[i]
    if (!m) continue
    if (m.role === 'assistant') return !!m.streaming
    // 遇到 user 消息提前停（最近一条 assistant 已遍历过）
    if (m.role === 'user') return false
  }
  return false
})

// streaming 期间的状态文字 — 让用户看到 AI 在做什么, 不只是 dots
// 优先级: 当前未完成工具 > 等待时长
// 触发刷新: streamTick 每秒 +1, 让 X 秒计数实时更新
const streamTick = ref(0)
let streamTickTimer: ReturnType<typeof setInterval> | null = null
function startStreamTicker() {
  if (streamTickTimer) return
  streamTickTimer = setInterval(() => {
    streamTick.value++
    if (!hasStreamingPlaceholder.value) {
      clearInterval(streamTickTimer)
      streamTickTimer = null
      streamTick.value = 0
    }
  }, 1000)
}
watch(hasStreamingPlaceholder, (now) => {
  if (now) startStreamTicker()
}, { immediate: true })

onBeforeUnmount(() => {
  if (streamTickTimer) {
    clearInterval(streamTickTimer)
    streamTickTimer = null
  }
})

function streamingStatusText(msg: any) {
  // 强制依赖 streamTick 触发响应式更新
  void streamTick.value
  // 1. 优先：找最后一个 pending 工具调用，显示"正在 Read scripts/x.py"
  const calls = msg?.toolCalls || []
  for (let i = calls.length - 1; i >= 0; i--) {
    const tc = calls[i]
    if (tc?.status === 'pending') {
      const summary = summarizeToolInput(tc.tool, tc.input) || ''
      const short = summary.length > 50 ? summary.slice(0, 47) + '...' : summary
      return `正在 ${tc.tool}${short ? ' ' + short : ''}`
    }
  }
  // 2. 否则：等待时长（让用户知道流是活的，不是卡死）
  const startTs = msg?._streamStartTs
  if (startTs) {
    const elapsed = Math.max(0, Math.floor((Date.now() - startTs) / 1000))
    return `AI 思考中... ${elapsed}s`
  }
  return 'AI 思考中...'
}

function messageUsageItems(msg: any) {
  const items: Array<{ label: string; value: string }> = []
  const duration = Number(msg?.durationMs || msg?.duration_ms || 0)
  if (duration > 0) {
    const seconds = duration / 1000
    items.push({ label: '耗时', value: seconds >= 60 ? `${(seconds / 60).toFixed(1)}m` : `${seconds.toFixed(1)}s` })
  }
  const usage = msg?.usage || {}
  const input = Number(usage.input_tokens || 0)
  const output = Number(usage.output_tokens || 0)
  const cacheCreate = Number(usage.cache_creation_input_tokens || 0)
  const cacheRead = Number(usage.cache_read_input_tokens || 0)
  const total = Number(usage.total_tokens || 0) || input + output + cacheCreate + cacheRead
  if (total > 0) items.push({ label: 'Token', value: total.toLocaleString('en-US') })
  const toolCalls = Number(msg?.toolCallCount ?? msg?.toolCalls?.length ?? 0)
  if (toolCalls > 0) items.push({ label: '调用', value: `${toolCalls} 次` })
  const cost = Number(msg?.totalCostUsd || msg?.total_cost_usd || 0)
  if (cost > 0) items.push({ label: '费用', value: `$${cost.toFixed(4)}` })
  return items
}

// Phase 4: 工具图标 / 状态映射
const TOOL_ICON_MAP: Record<string, any> = {
  Bash: IconCommand,
  Read: IconFile,
  Edit: IconEdit,
  Write: IconFile,
  MultiEdit: IconEdit,
  Grep: IconSearch,
  Glob: IconFolder,
  WebFetch: IconLink,
  WebSearch: IconSearch,
}
// 单窗口终端视��� — 折叠/��开状态
const toolWindowCollapsed = reactive<Record<string, boolean>>({})
const expandedToolDetail = reactive<Record<string, boolean>>({})
const twBodyRefs: Record<string, any> = {} // 纯 DOM 引用，不用 reactive（避免渲染期间触发响应式副作用）

function toggleToolDetail(msgIdx: string | number, tcIdx: string | number) {
  const key = `${msgIdx}_${tcIdx}`
  expandedToolDetail[key] = !expandedToolDetail[key]
}

function toolWindowOverallStatus(toolCalls: any[]) {
  if (toolCalls.some(tc => tc.status === 'error')) return 'error'
  if (toolCalls.some(tc => !tc.status || tc.status === 'pending')) return 'pending'
  return 'success'
}

function toolWindowStatusText(toolCalls: any[]) {
  const done = toolCalls.filter(tc => tc.status === 'success').length
  const errors = toolCalls.filter(tc => tc.status === 'error').length
  const pending = toolCalls.length - done - errors
  if (pending > 0) return `${done}/${toolCalls.length} 执行中...`
  if (errors > 0) return `完成 ${done}，失败 ${errors}`
  return '全部完成'
}

function tcStatusIcon(s: unknown) {
  if (s === 'success') return '✓'
  if (s === 'error') return '✗'
  return '⋯'
}

// 自动滚动到终端窗口底部
watch(() => props.messages?.map((m: any) => m.toolCalls?.length || 0).join(','), () => {
  nextTick(() => {
    for (const [key, el] of Object.entries(twBodyRefs)) {
      if (el && !toolWindowCollapsed[key]) {
        el.scrollTop = el.scrollHeight
      }
    }
  })
})

// 保留旧 expandedToolGroups 引用以防外部使用
const expandedToolGroups = reactive({})

function _toolCallFileKey(tc: any) {
  // 提取 tool call 对应的文件路径作为分组 key
  const inp = tc.input || {}
  if (inp.file_path) return inp.file_path
  if (tc.tool === 'Bash') return '_bash_' // Bash 命令不按文件分组，各自独立
  if (tc.tool === 'WebFetch' || tc.tool === 'WebSearch') return '_web_'
  return '_other_' + (tc.id || '')
}

function groupToolCalls(toolCalls: any[]) {
  // 按文件路径分组，保留顺序，同一文件的多次操作合并成一条
  const groups = []
  const groupMap = new Map() // key → group index
  for (const tc of toolCalls) {
    let key = _toolCallFileKey(tc)
    // Bash 命令每个都独立一组（不合并）
    if (key.startsWith('_bash_') || key.startsWith('_other_')) {
      key = key + '_' + groups.length
    }
    if (groupMap.has(key)) {
      const g = groups[groupMap.get(key)]
      g.items.push(tc)
      g.count++
      g.lastTool = tc.tool
      g.lastStatus = tc.status || 'pending'
      g.lastSummary = summarizeToolInput(tc.tool, tc.input)
      g.lastResult = tc.result
      // 更新 label（显示最新的操作类型）
      g.label = tc.tool === g.items[0].tool
        ? `${tc.tool} → ${(tc.input?.file_path || '').split('/').pop() || '?'}`
        : `${(tc.input?.file_path || '').split('/').pop() || tc.tool}`
    } else {
      const fp = tc.input?.file_path || ''
      groupMap.set(key, groups.length)
      groups.push({
        key,
        label: fp ? `${tc.tool} → ${fp.split('/').pop()}` : tc.tool,
        items: [tc],
        count: 1,
        lastTool: tc.tool,
        lastStatus: tc.status || 'pending',
        lastSummary: summarizeToolInput(tc.tool, tc.input),
        lastResult: tc.result,
      })
    }
  }
  return groups
}

function toolIcon(tool: string) {
  return TOOL_ICON_MAP[tool] || IconCode
}
function tcStatusLabel(s: string) {
  if (s === 'success') return '✓ 完成'
  if (s === 'error') return '✗ 失败'
  return '⏳ 进行中'
}
function summarizeToolInput(tool: string, input: any) {
  if (!input) return ''
  if (tool === 'Bash') return input.command || ''
  if (tool === 'Read') return `${input.file_path || ''}${input.offset ? `  L${input.offset}-${(input.offset || 0) + (input.limit || 0)}` : ''}`
  if (tool === 'Edit') return input.file_path || ''
  if (tool === 'Write') return `${input.file_path || ''} (${(input.content || '').length} 字)`
  if (tool === 'MultiEdit') return `${input.file_path || ''} (${(input.edits || []).length} 处)`
  if (tool === 'Grep') return `${input.pattern || ''}${input.path ? ` in ${input.path}` : ''}`
  if (tool === 'Glob') return input.pattern || ''
  if (tool === 'WebFetch' || tool === 'WebSearch') return input.url || input.query || ''
  return JSON.stringify(input).slice(0, 100)
}

function messageRuntimePreview(msg: any): RuntimeOutputPreview {
  const result: RuntimeOutputPreview = { todos: [], reports: [] }
  const seenTodos = new Set<string>()
  const seenReports = new Set<string>()

  const append = (source: unknown) => {
    if (!source) return
    const preview = normalizeRuntimeOutputPreview(source)
    for (const todo of preview.todos) {
      const key = `${todo.kind || ''}|${todo.title || ''}|${todo.summary || ''}|${todo.tasks?.length || 0}`
      if (seenTodos.has(key)) continue
      seenTodos.add(key)
      result.todos.push(todo)
    }
    for (const report of preview.reports) {
      const key = `${report.channel || ''}|${report.title || ''}|${report.summary || ''}`
      if (seenReports.has(key)) continue
      seenReports.add(key)
      result.reports.push(report)
    }
  }

  append(msg?.content || msg?.message || msg?.text)
  for (const call of msg?.toolCalls || []) {
    append(call?.result)
  }
  return result
}

function todoKindLabel(kind?: string) {
  return kind === 'dispatch' ? '派发' : '审核'
}

function todoKindColor(kind?: string) {
  return kind === 'dispatch' ? 'orange' : 'blue'
}
function fcOpLabel(op: string) {
  return { edit: '修改', write: '写入', multi_edit: '多处修改' }[op] || op
}

function diffSummaryText(summary: any) {
  if (!summary || typeof summary !== 'object') return ''
  const files = Number(summary.total_files || summary.files?.length || 0)
  const ins = Number(summary.total_insertions || 0)
  const del = Number(summary.total_deletions || 0)
  if (!files && !ins && !del) return ''
  return `${files} 个文件，+${ins} / -${del}`
}

function currentSkillIdFromLocation() {
  const match = window.location.pathname.match(/\/skills\/([^/?#]+)/)
  return match ? decodeURIComponent(match[1]) : ''
}

function openCommitDiff(commit: string) {
  const skillId = currentSkillIdFromLocation()
  if (!skillId || !commit) {
    Message.warning('缺少 Skill 或 commit，无法打开 diff')
    return
  }
  const url = `/api/skills/${encodeURIComponent(skillId)}/diff-summary?target=${encodeURIComponent(commit)}`
  window.open(url, '_blank', 'noopener,noreferrer')
}

function coachSevColor(sev: string) {
  const m: Record<string, string> = { critical: 'red', high: 'red', medium: 'orange', low: 'blue', info: 'gray' }
  return m[sev] || 'gray'
}
function coachSevLabel(sev: string) {
  const m: Record<string, string> = { critical: '严重', high: '重要', medium: '建议', low: '提示', info: '信息' }
  return m[sev] || sev
}
function coachActionLabel(action: string) {
  const m: Record<string, string> = {
    complete_branches: '补全兜底分支',
    extract_param: '提取参数',
    generate_tests: '生成测试',
    generate_counter_examples: '生成反例',
    fix_lint: '修复问题',
    simulate_param: '模拟影响',
    analyze_failure: '分析失败',
    suggest_alternatives: '给我替代方案',
    simulate_replay: '查看回放',
  }
  return m[action] || action
}

const MODULE_LABELS: Record<string, string> = { meta: '基础信息', goal: '目标', rules: '规则', params: '参数', output_table: '输出', test_cases: '测试', workflow: '工作流' }
const taskContract = computed(() => props.doc?.custom_sections?.__task_contract || null)
const taskGateItems = computed(() => props.doc?.custom_sections?.__task_gate?.items || [])
const taskPreviewText = computed(() => {
  const artifacts = props.doc?.custom_sections?.__artifacts || {}
  const intent = artifacts['intent.md']
  if (!intent) return ''
  return intent.split('\n').slice(0, 10).join('\n')
})

const draft = ref('')
const scrollEl = ref<HTMLElement | null>(null)
const showRefPicker = ref(false)

const moduleLabel = computed(() => MODULE_LABELS[props.activeModule] || props.activeModule)

// 诊断卡统计（从 doc prop 读取）
const skillName = computed(() => props.doc?.meta?.name || '')
const goalLen = computed(() => (props.doc?.goal || '').length)
const rulesLen = computed(() => props.doc?.rules?.length || 0)
const paramsLen = computed(() => props.doc?.params?.length || 0)
const testsLen = computed(() => props.doc?.test_cases?.length || 0)

const hints = computed(() =>
  props.isCreate
    ? ['帮我生成一个客户流失预警 Skill', '创建投放预算自动分配 Skill']
    : [`优化 ${moduleLabel.value} 模块`, '把参数阈值调低', '补充测试样例', '/generate-tests', '/drift-check']
)

const inputPlaceholder = computed(() => {
  if (props.studioMode === 'review') return `审批模式：查看变化、风险与影响摘要`
  if (props.studioMode === 'explain') return `讲解模式：解释逻辑、参数与业务影响`
  if (props.selection?.text) return `对选中内容提问或修改...`
  return `输入指令或 /command...`
})

function scrollToBottom() {
  nextTick(() => { if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight })
}
watch(() => props.messages?.length, scrollToBottom)
watch(() => props.loading, scrollToBottom)

const pendingImages = ref<Array<{ data: string, media_type: string }>>([])

function handleImageUpload(e: Event) {
  const target = e.target as HTMLInputElement | null
  const files = Array.from(target?.files || []) as File[]
  for (const file of files) {
    if (!file.type.startsWith('image/')) continue
    const reader = new FileReader()
    reader.onload = () => {
      const base64 = String(reader.result || '').split(',')[1] || ''
      pendingImages.value.push({ data: base64, media_type: file.type })
    }
    reader.readAsDataURL(file)
  }
  if (target) target.value = ''
}

function handleSend() {
  if (props.studioMode === 'review') return
  const text = draft.value.trim()
  if (!text && !pendingImages.value.length) return
  if (props.loading) return
  draft.value = ''

  const images = pendingImages.value.length ? [...pendingImages.value] : undefined
  pendingImages.value = []

  // 检查是否是 slash command
  const isCommand = text.startsWith('/')
  emit('send', text || '分析这张图片', {
    targetModule: props.activeModule,
    selectionRange: props.selection,
    isCommand,
    images,
  })
}

function handleFileUpload(e: Event) {
  const target = e.target as HTMLInputElement | null
  const file = target?.files?.[0]
  if (!file) return
  emit('upload-report', file)
  if (target) target.value = '' // 清空以允许重复上传同一文件
}

function sendQuick(text: string) {
  if (props.studioMode === 'review') return
  emit('send', text, {
    targetModule: props.activeModule,
    selectionRange: null,
    isCommand: text.startsWith('/'),
  })
}

async function copyMessage(msg: any) {
  const text = msg.content || msg.message || msg.text || ''
  const { copyText } = await import('@/utils/clipboard')
  const ok = await copyText(text)
  if (ok) Message.success('已复制')
  else Message.warning('复制失败')
}
</script>

<style scoped>
.wb-asst { display: flex; flex-direction: column; height: 100%; }

/* 头部 */
.asst-head {
  display: flex; align-items: center; gap: 8px;
  height: 42px; padding: 0 14px; flex-shrink: 0;
  border-bottom: 1px solid var(--ai-border);
}
.asst-title { font-size: 13px; font-weight: 800; color: var(--ai-ink-1); }
.asst-ctx {
  font-size: 11px; padding: 2px 6px; border-radius: var(--sf-radius-xs);
  background: var(--ai-warn-soft); color: var(--ai-warn); font-weight: 800;
}
.asst-meta {
  font-size: 10px;
  color: var(--ai-ink-3);
  background: var(--ai-surface-2);
  border-radius: 999px;
  padding: 2px 6px;
}
.asst-meta-mono { font-family: var(--ai-font-mono); }
.asst-conv-btn {
  width: 28px; height: 28px; border-radius: 4px; border: none;
  background: transparent; color: var(--ai-ink-3); cursor: pointer;
  display: flex; align-items: center; justify-content: center; gap: 2px; font-size: 11px;
  transition: all var(--sf-transition); font-weight: 700;
}
.asst-conv-btn:hover { background: var(--ai-surface-2); color: var(--ai-ink-1); }
.asst-close {
  margin-left: auto; width: 28px; height: 28px; border-radius: 4px; border: none;
  background: transparent; color: var(--ai-ink-3); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: all var(--sf-transition);
}
.asst-close:hover { background: var(--ai-surface-2); color: var(--ai-ink-1); }

/* 消息 */
.asst-scroll { flex: 1; overflow-y: auto; padding: 16px 12px; scroll-behavior: smooth; }

/* 诊断卡 */
.asst-diagnosis { padding: 20px 12px; }
.diag-header { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.diag-title { font-size: 13px; color: var(--ai-ink-2); font-weight: 700; }
.diag-title strong { color: var(--ai-ink-1); font-weight: 800; }
.diag-stats {
  display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px;
}
.diag-stat {
  padding: 4px 10px; border-radius: 4px; font-size: 12px;
  background: var(--ai-surface-2); color: var(--ai-ink-2); font-weight: 700;
}
.diag-actions { display: flex; flex-direction: column; gap: 6px; }
.diag-btn {
  padding: 10px 14px; border-radius: 8px; font-size: 12.5px; text-align: left;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-2); cursor: pointer; transition: all var(--sf-transition);
  font-weight: 700;
}
.diag-btn:hover {
  border-color: var(--ai-warn);
  color: var(--ai-warn);
  background: var(--ai-warn-soft);
}
.diag-divider { height: 1px; background: var(--ai-border); margin: 16px 0 10px; }
.diag-free { font-size: 12px; color: var(--ai-ink-3); text-align: center; font-weight: 600; }

.asst-empty {
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  padding: 40px 12px; text-align: center; font-size: 12px; color: var(--ai-ink-3);
  font-weight: 700;
}
.asst-hints { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; width: 100%; }
.hint {
  padding: 8px 12px; border-radius: 8px; font-size: 12px; text-align: left;
  background: var(--ai-surface); border: 1px solid var(--ai-border);
  color: var(--ai-ink-2); cursor: pointer; transition: all var(--sf-transition);
  font-weight: 700;
}
.hint:hover { border-color: var(--ai-warn); color: var(--ai-warn); }

/* Phase 2 Change Coach 建议卡 */
.asst-coach {
  margin: 4px 0 16px; padding: 10px 12px;
  border: 1px solid var(--ai-border); border-radius: 8px;
  background: var(--sf-gradient-ai-soft);
}
.coach-head { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.coach-head span { font-size: 12px; font-weight: 800; color: var(--ai-ink-1); }
.coach-count {
  padding: 1px 6px; border-radius: 999px; font-size: 10px;
  background: var(--sf-warning-solid); color: #fff; font-weight: 800;
}
.asst-v7 {
  margin: 4px 0 12px;
  padding: 12px 14px;
  border-radius: 8px;
  border: 1px solid rgba(198, 106, 20, 0.18);
  background: var(--ai-surface);
}
.asst-v7-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 6px;
}
.asst-v7-title {
  font-size: 12px;
  font-weight: 800;
  color: var(--ai-ink-1);
}
.asst-v7-goal {
  font-size: 12px;
  line-height: 1.6;
  color: var(--ai-ink-1);
  font-weight: 700;
}
.asst-v7-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 6px;
  font-size: 11px;
  color: var(--ai-ink-3);
  font-weight: 600;
}
.asst-v7-gates {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.asst-v7-gate {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  border-radius: 999px;
  background: var(--ai-surface);
  font-size: 11px;
  color: var(--ai-ink-2);
  font-weight: 700;
}
.asst-v7-preview {
  margin: 10px 0 0;
  padding: 10px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  white-space: pre-wrap;
  font-size: 11px;
  line-height: 1.6;
  color: var(--ai-ink-2);
}
.coach-list { display: flex; flex-direction: column; gap: 6px; }
.coach-card {
  padding: 8px 10px; border-radius: 8px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
}
.coach-card.sev-critical, .coach-card.sev-high { border-left: 3px solid var(--ai-bad); }
.coach-card.sev-medium { border-left: 3px solid var(--ai-warn); }
.cc-head { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
.cc-title { font-size: 12px; font-weight: 800; color: var(--ai-ink-1); line-height: 1.3; }
.cc-desc { font-size: 11px; color: var(--ai-ink-2); line-height: 1.4; font-weight: 600; }
.cc-actions { margin-top: 6px; }
.cc-btn {
  padding: 4px 10px; border-radius: 4px; font-size: 11px;
  border: 1px solid var(--ai-warn); color: var(--ai-warn);
  background: transparent; cursor: pointer; transition: all var(--sf-transition);
  font-weight: 800;
}
.cc-btn:hover { background: var(--sf-warning-solid); color: #fff; }

/* §4.3 Evidence Card — 高影响改动专用 */
.evidence-card {
  padding: 10px 12px; border-radius: 8px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-warn);
  box-shadow: 0 2px 6px rgba(198, 106, 20, 0.10);
}
.evidence-card.sev-critical, .evidence-card.sev-high {
  border-color: var(--ai-bad);
  box-shadow: 0 2px 8px rgba(191, 63, 63, 0.14);
}
.ec-head { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
.ec-title { font-size: 12px; font-weight: 800; color: var(--ai-ink-1); line-height: 1.3; }
.ec-desc { font-size: 11px; color: var(--ai-ink-2); line-height: 1.5; margin-bottom: 8px; font-weight: 600; }
.ec-evidence {
  padding: 8px 10px; background: var(--ai-surface-2); border-radius: 4px;
  margin-bottom: 8px; font-size: 11px;
}
.ec-row { display: flex; gap: 8px; padding: 3px 0; align-items: flex-start; }
.ec-label { color: var(--ai-ink-3); min-width: 60px; flex-shrink: 0; font-weight: 800; text-transform: uppercase; letter-spacing: 0.3px; font-size: 10px; }
.ec-val { color: var(--ai-ink-1); flex: 1; min-width: 0; word-break: break-all; font-weight: 700; }
.ec-val code { background: var(--ai-border); padding: 1px 5px; border-radius: var(--sf-radius-xs); font-family: var(--ai-font-mono); font-size: 11px; }
.ec-val.ec-major { color: var(--ai-bad); font-weight: 800; }
.ec-branches { display: flex; flex-direction: column; gap: 3px; flex: 1; min-width: 0; }
.ec-branch { display: flex; align-items: center; gap: 4px; }
.ec-branch code { font-size: 10px; color: var(--ai-ink-2); }
.ec-more { font-size: 10px; color: var(--ai-ink-3); font-style: italic; }
.ec-actions { display: flex; gap: 6px; }
.ec-btn {
  padding: 5px 12px; border-radius: 4px; font-size: 11px;
  border: 1px solid var(--ai-border); background: var(--ai-surface);
  color: var(--ai-ink-2); cursor: pointer; transition: all var(--sf-transition);
  font-weight: 800;
}
.ec-btn:hover { border-color: var(--ai-warn); color: var(--ai-warn); }
.ec-btn.ec-btn-primary {
  background: var(--sf-warning-solid); color: #fff; border-color: var(--ai-warn);
}
.ec-btn.ec-btn-primary:hover { background: var(--sf-warning-hover); }
.ec-btn.ec-btn-ghost {
  color: var(--ai-ink-3); border-color: transparent;
}

.msg { display: flex; gap: 8px; margin-bottom: 14px; animation: fadeIn .15s ease; }
.msg.user { flex-direction: row-reverse; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
.msg-avatar {
  width: 26px; height: 26px; border-radius: 8px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  background: var(--sf-brand-action); color: var(--sf-on-action);
  box-shadow: var(--ai-shadow-1);
}
.msg-avatar.user { background: var(--ai-border); color: var(--ai-ink-1); box-shadow: none; }
.msg-body { max-width: 85%; position: relative; }
.msg-copy-btn {
  position: absolute; top: 4px; right: 4px;
  width: 22px; height: 22px; border-radius: var(--sf-radius-xs); border: none;
  background: var(--ai-surface); color: var(--ai-ink-3);
  cursor: pointer; opacity: 0; transition: opacity var(--sf-transition);
  display: flex; align-items: center; justify-content: center;
}
.msg-body:hover .msg-copy-btn { opacity: 1; }
.msg-copy-btn:hover { background: var(--ai-border); color: var(--ai-ink-1); }
.msg-bubble {
  padding: 8px 12px; border-radius: 8px; font-size: 12.5px; line-height: 1.6;
  word-break: break-word;
  font-weight: 600;
  font-family: var(--sf-font-sans);
}
.msg.assistant .msg-bubble {
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-top-left-radius: 3px;
  color: var(--ai-ink-1);
}
.msg.user .msg-bubble { background: var(--sf-brand-action); color: var(--sf-on-action); border-top-right-radius: 3px; }
.msg-bubble :deep(*) { font-family: var(--ai-font-sans); }
.msg-bubble :deep(code) { padding: 1px 4px; border-radius: var(--sf-radius-xs); font-size: 11px; background: var(--ai-border); font-family: var(--ai-font-mono); }
.msg.user .msg-bubble :deep(code) { background: rgba(255,255,255,.15); }

/* ───── Markdown 完整渲染样式（GFM + 表格 + 代码块 + 标题） ───── */
.msg-bubble :deep(p) { margin: 0 0 6px; }
.msg-bubble :deep(p:last-child) { margin-bottom: 0; }
.msg-bubble :deep(h1),
.msg-bubble :deep(h2),
.msg-bubble :deep(h3),
.msg-bubble :deep(h4) {
  margin: 12px 0 6px; font-weight: 700; line-height: 1.3;
}
.msg-bubble :deep(h1) { font-size: 15px; }
.msg-bubble :deep(h2) { font-size: 14px; }
.msg-bubble :deep(h3) { font-size: 13px; }
.msg-bubble :deep(h4) { font-size: 12.5px; }
.msg-bubble :deep(ul),
.msg-bubble :deep(ol) {
  margin: 4px 0 6px; padding-left: 20px;
}
.msg-bubble :deep(li) { margin: 2px 0; }
.msg-bubble :deep(blockquote) {
  margin: 6px 0; padding: 4px 10px;
  border-left: 3px solid var(--ai-border-2);
  color: var(--ai-ink-2);
  background: var(--ai-surface-2);
}
.msg-bubble :deep(pre) {
  margin: 6px 0; padding: 8px 10px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  overflow-x: auto;
  font-size: 11.5px; line-height: 1.5;
  font-family: var(--ai-font-mono);
}
.msg-bubble :deep(pre code) {
  padding: 0; background: transparent; font-size: inherit;
}
.msg-bubble :deep(table) {
  border-collapse: collapse;
  margin: 6px 0;
  font-size: 11.5px;
  width: 100%;
}
.msg-bubble :deep(th),
.msg-bubble :deep(td) {
  border: 1px solid var(--ai-border-2);
  padding: 4px 8px;
  text-align: left;
  vertical-align: top;
}
.msg-bubble :deep(th) {
  background: var(--ai-surface-2);
  font-weight: 700;
}
.msg-bubble :deep(a) {
  color: var(--ai-accent-ink);
  text-decoration: none;
}
.msg-bubble :deep(a:hover) { text-decoration: underline; }
.msg-bubble :deep(hr) {
  border: 0;
  border-top: 1px solid var(--ai-border);
  margin: 8px 0;
}
/* User bubbles follow their foreground in both themes. */
.msg.user .msg-bubble :deep(blockquote) {
  border-left-color: currentColor;
  background: color-mix(in srgb, currentColor 8%, transparent);
  color: inherit;
}
.msg.user .msg-bubble :deep(pre) {
  background: color-mix(in srgb, currentColor 10%, transparent);
  border-color: color-mix(in srgb, currentColor 25%, transparent);
}
.msg.user .msg-bubble :deep(th),
.msg.user .msg-bubble :deep(td) {
  border-color: color-mix(in srgb, currentColor 25%, transparent);
}
.msg.user .msg-bubble :deep(th) { background: color-mix(in srgb, currentColor 15%, transparent); }
.msg.user .msg-bubble :deep(a) { color: inherit; text-decoration: underline; }
.msg-meta { font-size: 10px; color: var(--ai-ink-3); margin-top: 2px; padding-left: 4px; font-weight: 600; }

/* streaming 状态条：dots + 当前活动文字 */
.msg-bubble-streaming {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 500;
}
.streaming-text {
  font-size: 11.5px;
  color: var(--ai-ink-3);
  font-family: var(--sf-font-sans);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 320px;
}

.dots { display: inline-flex; gap: 3px; }
.dots::before, .dots::after, .dots { content: ''; width: 5px; height: 5px; border-radius: 50%; background: var(--ai-ink-3); animation: dot 1.2s infinite ease-in-out; }
.dots::before { animation-delay: -.3s; }
.dots::after { animation-delay: .3s; }
@keyframes dot { 0%,80%,100%{opacity:.3;transform:scale(.8)} 40%{opacity:1;transform:scale(1)} }

/* 引用区 */
.asst-refs {
  max-height: 200px; overflow-y: auto; flex-shrink: 0;
  border-top: 1px solid var(--ai-border); padding: 8px;
}

/* 输入 */
.asst-input {
  display: flex; align-items: flex-end; gap: 6px;
  padding: 10px 12px; border-top: 1px solid var(--ai-border); flex-shrink: 0;
}
.input-ta { flex: 1; }
.input-ta :deep(.arco-textarea-wrapper) { border-radius: 8px !important; }
.input-ta :deep(.arco-textarea) { font-size: 12.5px !important; padding: 8px 10px !important; }
.input-send {
  width: 32px; height: 32px; border-radius: 8px; border: none; flex-shrink: 0;
  background: var(--ai-border); color: var(--ai-ink-3);
  display: flex; align-items: center; justify-content: center; cursor: pointer; transition: all var(--sf-transition);
}
.input-send.ready { background: var(--sf-brand-action); color: var(--sf-on-action); box-shadow: var(--ai-shadow-1); }
.input-send:disabled { opacity: .4; cursor: not-allowed; }
.input-ref-btn {
  width: 32px; height: 32px; border-radius: 8px; border: 1px solid transparent;
  background: transparent; color: var(--ai-ink-3); cursor: pointer; transition: all var(--sf-transition);
  display: flex; align-items: center; justify-content: center; flex-shrink: 0;
}
.input-ref-btn:hover { background: var(--ai-surface-2); color: var(--ai-ink-1); }
.input-ref-btn.active { background: var(--ai-warn-soft); color: var(--ai-warn); border-color: var(--ai-warn); }

.tc-result-preview { font-size: 11px; color: var(--ai-ink-3); background: var(--ai-surface-2); padding: 6px 8px; border-radius: 4px; margin-top: 4px; max-height: 120px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; font-family: var(--ai-font-mono); }

.image-preview-bar { display: flex; gap: 6px; padding: 6px 0; flex-wrap: wrap; }
.image-thumb { position: relative; width: 48px; height: 48px; border-radius: 6px; overflow: hidden; border: 1px solid var(--ai-border); }
.image-thumb img { width: 100%; height: 100%; object-fit: cover; }
.image-remove { position: absolute; top: -2px; right: -2px; width: 16px; height: 16px; border-radius: 50%; background: var(--color-danger-6); color: white; border: none; font-size: 10px; line-height: 16px; cursor: pointer; padding: 0; }

/* ═══════ Phase 4: 跟踪模式 + 工具调用 + 权限审批 ═══════ */

.asst-tracking-btn {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 8px;
  border: 1px solid var(--ai-border);
  border-radius: 12px;
  background: transparent;
  color: var(--ai-ink-3);
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s;
}
.asst-tracking-btn:hover { background: var(--ai-surface-2); color: var(--ai-ink-1); }
.asst-tracking-btn.active {
  background: rgba(0, 180, 42, 0.1);
  color: rgb(0, 140, 30);
  border-color: rgba(0, 180, 42, 0.4);
}

/* 工具执行 — 单窗口终端视图 */
.msg-tool-window {
  margin-top: 6px;
  border-radius: 8px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  overflow: hidden;
  font-size: 12px;
}
.tw-head {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 10px;
  font-weight: 500; color: var(--ai-ink-1);
  background: var(--ai-surface-2);
  border-bottom: 1px solid var(--ai-border);
  user-select: none;
}
.tw-title { font-size: 11px; }
.tw-count {
  font-size: 10px;
  color: var(--ai-info);
  background: var(--ai-info-soft);
  padding: 0 5px;
  border-radius: 8px;
  font-weight: 600;
}
.tw-status { margin-left: auto; font-size: 10px; color: var(--ai-ink-3); }
.tw-st-success { color: rgb(0, 150, 40); }
.tw-st-error { color: rgb(200, 50, 50); }
.tw-st-pending { color: rgb(200, 140, 0); }
.tw-body {
  max-height: 240px;
  overflow-y: auto;
  padding: 2px 0;
}
.tw-item {
  padding: 3px 10px;
  cursor: pointer;
  transition: background 0.1s;
}
.tw-item:hover { background: var(--ai-surface-2); }
.tw-item + .tw-item { border-top: 1px solid var(--ai-border); }
.tw-item-line {
  display: flex; align-items: center; gap: 6px;
  min-height: 22px;
}
.tw-item-tool {
  font-family: var(--ai-font-mono);
  font-size: 11px; font-weight: 600;
  color: var(--ai-ink-2);
  flex-shrink: 0;
}
.tw-item-summary {
  font-family: var(--ai-font-mono);
  font-size: 11px;
  color: var(--ai-ink-3);
  flex: 1;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.tw-item-st { flex-shrink: 0; font-size: 11px; width: 14px; text-align: center; }
.tw-pending .tw-item-st { color: rgb(200, 140, 0); }
.tw-success .tw-item-st { color: rgb(0, 160, 40); }
.tw-error .tw-item-st { color: rgb(200, 50, 50); }
.tw-item-result {
  font-family: var(--ai-font-mono);
  font-size: 10px; color: var(--ai-ink-3);
  background: var(--ai-surface-2);
  padding: 4px 8px; margin: 3px 0 2px;
  border-radius: 4px;
  max-height: 120px; overflow-y: auto;
  white-space: pre-wrap; word-break: break-all;
}
.tw-item-result-err {
  color: rgb(180, 50, 50);
  background: rgba(220, 60, 60, 0.06);
}

.msg-runtime-output {
  display: flex;
  flex-direction: column;
  gap: 7px;
  margin-top: 6px;
  padding: 8px;
  border-radius: 8px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  font-size: 11px;
}
.mro-head {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--ai-ink-2);
  font-weight: 800;
}
.mro-title {
  color: var(--ai-ink-1);
}
.mro-count {
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: 10px;
}
.mro-section {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.mro-section-title {
  font-weight: 800;
  color: var(--ai-ink-1);
}
.mro-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 7px 8px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
}
.mro-card-head {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.mro-card-head strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ai-ink-1);
}
.mro-muted {
  color: var(--ai-ink-3);
  line-height: 1.45;
  font-weight: 600;
}
.mro-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 9px;
  color: var(--ai-ink-3);
  font-weight: 700;
}
.mro-task-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.mro-task {
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr) auto;
  gap: 5px;
  align-items: start;
  color: var(--ai-ink-2);
}
.mro-task-index {
  width: 15px;
  height: 15px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--ai-border);
  color: var(--ai-ink-3);
  font-size: 10px;
  font-weight: 800;
}
.mro-task-content {
  min-width: 0;
  line-height: 1.45;
  word-break: break-word;
}
.mro-task-meta {
  color: var(--ai-ink-3);
  font-family: var(--ai-font-mono);
  white-space: nowrap;
}

.msg-usage-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 6px;
}
.usage-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  font-size: 10.5px;
  line-height: 1.5;
}
.usage-label {
  color: var(--ai-ink-3);
  font-weight: 800;
}
.usage-value {
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-weight: 800;
}

.msg-file-changes {
  display: flex; flex-direction: column; gap: 2px;
  margin-top: 4px;
}
.file-change-row {
  display: flex; align-items: center; gap: 6px;
  padding: 3px 8px;
  border-radius: 4px;
  background: rgba(0, 130, 220, 0.06);
  font-size: 11px;
  cursor: pointer;
  transition: background 0.15s;
}
.file-change-row:hover { background: rgba(0, 130, 220, 0.12); }
.file-change-row.active {
  background: rgba(22, 93, 255, 0.14);
  box-shadow: inset 0 0 0 1px rgba(22, 93, 255, 0.28);
}
.fc-path { font-family: var(--ai-font-mono); color: var(--ai-ink-1); }
.fc-op { color: var(--ai-ink-3); margin-left: auto; font-size: 10px; }
.fc-action {
  flex-shrink: 0;
  color: var(--ai-accent-ink);
  font-size: 10px;
  font-weight: 700;
}
.msg-git-commit {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 4px 8px;
  align-items: center;
  margin-top: 6px;
  padding: 7px 8px;
  border-radius: 4px;
  border: 1px solid rgba(15, 143, 111, 0.18);
  background: rgba(15, 143, 111, 0.06);
  font-size: 11px;
}
.gc-main {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  color: var(--ai-ink-2);
  font-weight: 800;
}
.gc-main code {
  font-family: var(--ai-font-mono);
  color: var(--ai-ok);
}
.gc-label { color: var(--ai-ink-3); }
.gc-summary {
  grid-column: 1 / -1;
  color: var(--ai-ink-3);
  font-weight: 700;
}
.gc-action {
  border: none;
  background: transparent;
  color: var(--ai-accent-ink);
  cursor: pointer;
  font-size: 11px;
  font-weight: 800;
  padding: 2px 4px;
}
.gc-action:hover { text-decoration: underline; }

.asst-permission {
  margin: 8px 12px;
  border-radius: 10px;
  overflow: hidden;
  border: 2px solid #E67700;
  box-shadow: 0 4px 20px rgba(230, 119, 0, 0.15);
  animation: perm-pulse 2s ease-in-out infinite;
}
@keyframes perm-pulse {
  0%, 100% { box-shadow: 0 4px 20px rgba(230, 119, 0, 0.15); }
  50% { box-shadow: 0 4px 24px rgba(230, 119, 0, 0.3); }
}
.perm-banner {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 14px;
  background: var(--ai-warn-soft);
  font-size: 14px; font-weight: 700; color: #B45A00;
}
.perm-body {
  padding: 12px 14px;
  background: var(--ai-surface);
}
.perm-tool-label {
  font-size: 12px; color: var(--ai-ink-2); margin-bottom: 6px;
}
.perm-tool-label b { color: var(--ai-ink-1); font-family: var(--ai-font-mono); }
.perm-input {
  font-family: var(--ai-font-mono); font-size: 11px;
  color: var(--ai-ink-2);
  background: var(--ai-surface);
  padding: 8px 10px;
  border-radius: 6px;
  border: 1px solid rgba(0, 0, 0, 0.06);
  max-height: 100px; overflow: auto;
  margin: 0 0 12px;
  white-space: pre-wrap; word-break: break-all;
}
.perm-actions {
  display: flex; flex-direction: column; gap: 8px;
}
.perm-secondary-actions {
  display: flex; gap: 8px; justify-content: center;
}
</style>
