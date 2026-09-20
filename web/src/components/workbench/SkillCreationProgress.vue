<template>
  <div class="scp">
    <!-- 顶部：6 个 milestone 步骤条 -->
    <div class="scp-header">
      <div class="scp-title-col">
        <div class="scp-title">
          <icon-robot :size="22" style="color:var(--ai-info)" />
          <span>正在为你创建 Skill</span>
          <span class="scp-attempt" v-if="attempt > 1">（重试 {{ attempt }}/2）</span>
        </div>
        <div v-if="waitingSubtitle" class="scp-subtitle">
          <a-spin :size="12" style="margin-right:6px" />
          <span>{{ waitingSubtitle }}</span>
        </div>
      </div>
      <a-button
        size="small"
        type="outline"
        status="danger"
        :disabled="!canInterrupt"
        @click="onInterrupt"
      >
        <icon-close-circle :size="13" /> 中断
      </a-button>
    </div>

    <div class="scp-steps">
      <div
        v-for="(m, i) in milestoneList"
        :key="m.key"
        class="scp-step"
        :class="{
          'scp-step-done': milestoneState[m.key] === 'done',
          'scp-step-active': milestoneState[m.key] === 'active',
          'scp-step-pending': !milestoneState[m.key],
        }"
      >
        <div class="scp-step-icon">
          <icon-check v-if="milestoneState[m.key] === 'done'" :size="14" />
          <a-spin v-else-if="milestoneState[m.key] === 'active'" :size="14" />
          <span v-else class="scp-step-num">{{ i + 1 }}</span>
        </div>
        <div class="scp-step-body">
          <div class="scp-step-label">{{ m.label }}</div>
          <div v-if="milestoneTimings[m.key]" class="scp-step-time">{{ milestoneTimings[m.key] }}s</div>
        </div>
      </div>
    </div>

    <!-- 中部双栏：左 contract 预览（read-only / 末态可确认） / 右工具调用日志 -->
    <div class="scp-body">
      <div class="scp-left">
        <div class="scp-pane-head">
          <icon-file :size="14" /> 任务合同
          <span v-if="!contract" class="scp-pane-hint">等待 contract.json 写完...</span>
          <span v-else-if="!skillReady" class="scp-pane-hint">📖 只读预览（生成完成后才能确认）</span>
          <span v-else class="scp-pane-hint" style="color:var(--ai-ok)">✓ 可确认</span>
        </div>
        <div class="scp-contract-cards" v-if="contract">
          <div class="scp-card">
            <div class="scp-card-h"><icon-bulb :size="14" style="color:var(--ai-warn)" /> 目标</div>
            <div class="scp-card-body">{{ contract.goal || '—' }}</div>
            <div class="scp-card-actions" v-if="skillReady">
              <a-button
                v-if="canApprove('target')"
                size="mini" type="primary"
                :loading="reviewingCheckpoint === 'target'"
                @click="submitReview('target')"
              >确认目标</a-button>
              <a-tag v-else size="small" :color="cpColor('target')">{{ cpLabel('target') }}</a-tag>
            </div>
          </div>

          <div class="scp-card">
            <div class="scp-card-h"><icon-thunderbolt :size="14" style="color:rgb(var(--purple-6))" /> 触发</div>
            <div class="scp-card-body">
              <div v-if="contract.trigger?.type === 'cron'" class="scp-trigger-cron">
                <a-tag size="small" color="purple">cron</a-tag>
                <code>{{ contract.trigger?.expression }}</code>
              </div>
              <div v-else><a-tag size="small">{{ contract.trigger?.type || 'manual' }}</a-tag></div>
              <div class="scp-trigger-desc">{{ contract.trigger?.description || '' }}</div>
            </div>
          </div>

          <div class="scp-card">
            <div class="scp-card-h"><icon-import :size="14" style="color:var(--ai-info)" /> 输入数据源 ({{ (contract.input || []).length }})</div>
            <div class="scp-card-body">
              <div v-for="(inp, i) in (contract.input || [])" :key="i" class="scp-input-row">
                <strong>{{ inp.name }}</strong>
                <span>{{ inp.source }} / {{ inp.type }}{{ inp.required ? '' : ' (可选)' }}</span>
              </div>
            </div>
          </div>

          <div class="scp-card">
            <div class="scp-card-h"><icon-export :size="14" style="color:var(--ai-ok)" /> 权限 ({{ (contract.permissions || []).length }})</div>
            <div class="scp-card-body">
              <div v-for="(perm, i) in (contract.permissions || [])" :key="i" class="scp-perm-row">
                <a-tag size="small" :color="perm.action === 'send_message' ? 'orange' : 'blue'">{{ perm.action }}</a-tag>
                <span>{{ perm.target }}</span>
                <span v-if="!perm.reversible" class="scp-perm-warn">不可逆</span>
              </div>
            </div>
            <div class="scp-card-actions" v-if="skillReady">
              <a-button
                v-if="canApprove('permission')"
                size="mini" type="primary"
                :loading="reviewingCheckpoint === 'permission'"
                @click="submitReview('permission')"
              >授予权限</a-button>
              <a-tag v-else size="small" :color="cpColor('permission')">{{ cpLabel('permission') }}</a-tag>
            </div>
          </div>

          <div class="scp-card scp-card-wide">
            <div class="scp-card-h"><icon-message :size="14" style="color:rgb(var(--cyan-6))" /> 真实预演</div>
            <div class="scp-card-body">
              <pre class="scp-preview-pre">{{ preview?.rendered_output || '（生成中...）' }}</pre>
              <PreviewCardActions v-if="preview" :preview="preview" />
            </div>
            <div class="scp-card-actions" v-if="skillReady">
              <a-button
                v-if="canApprove('preview')"
                size="mini" type="primary"
                :loading="reviewingCheckpoint === 'preview'"
                @click="submitReview('preview')"
              >预演符合预期</a-button>
              <a-tag v-else size="small" :color="cpColor('preview')">{{ cpLabel('preview') }}</a-tag>
            </div>
          </div>

          <div class="scp-card">
            <div class="scp-card-h"><icon-settings :size="14" style="color:rgb(var(--gray-6))" /> 责任说明</div>
            <div class="scp-card-body" style="font-size:12px;color:var(--ai-ink-2)">
              失败会通知责任人，连续 3 次失败自动停用，可回滚到最近发布版本。
            </div>
            <div class="scp-card-actions" v-if="skillReady">
              <a-button
                v-if="canApprove('responsibility')"
                size="mini" type="primary"
                :loading="reviewingCheckpoint === 'responsibility'"
                @click="submitReview('responsibility')"
              >我已知晓</a-button>
              <a-tag v-else size="small" :color="cpColor('responsibility')">{{ cpLabel('responsibility') }}</a-tag>
            </div>
          </div>
        </div>
        <div v-else class="scp-empty">
          <icon-loading :size="20" />
          <p>等待 AI 起草任务合同...</p>
        </div>
      </div>

      <div class="scp-right">
        <div class="scp-pane-head">
          <icon-code :size="14" /> 实时工具调用
          <span class="scp-pane-hint">{{ toolEvents.length }} 个事件</span>
        </div>
        <div class="scp-tool-log" ref="toolLogEl">
          <div v-if="!toolEvents.length && !errorPayload" class="scp-empty">
            <icon-loading :size="16" /> <span>等待 AI 启动...</span>
          </div>
          <div
            v-for="(ev, i) in toolEvents"
            :key="i"
            class="scp-tool-row"
            :class="`scp-tool-${ev.kind}`"
          >
            <span class="scp-tool-tag">{{ ev.tag }}</span>
            <span class="scp-tool-text">{{ ev.text }}</span>
          </div>
          <div v-if="errorPayload" class="scp-error-banner">
            <icon-exclamation-circle :size="14" />
            <strong>{{ errorPayload.code }}</strong>
            <span>{{ errorPayload.detail }}</span>
            <div v-if="errorPayload.scratch_dir" class="scp-error-scratch">
              失败现场已保留在: <code>{{ errorPayload.scratch_dir }}</code>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 底部：finalize 按钮（必须 4 必感知点全部确认才能点）-->
    <div class="scp-footer" v-if="skillReady">
      <div class="scp-gate">
        <span v-if="gate?.can_publish" class="scp-gate-ok">
          <icon-check-circle :size="14" /> 4 必感知点全部确认，可创建
        </span>
        <span v-else class="scp-gate-warn">
          <icon-info-circle :size="14" /> 还需确认 {{ pendingCount }} 项
        </span>
      </div>
      <a-space>
        <a-button @click="onCancel">放弃</a-button>
        <a-button
          type="primary"
          :disabled="!gate?.can_publish"
          :loading="finalizing"
          @click="onFinalize"
        >
          创建 Skill 并打开编辑器
        </a-button>
      </a-space>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onBeforeUnmount, computed, nextTick, onMounted } from 'vue'
import type { PropType } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  IconRobot, IconCheck, IconClose, IconCloseCircle, IconBulb,
  IconImport, IconExport, IconMessage, IconSettings, IconCode,
  IconCheckCircle, IconExclamationCircle, IconInfoCircle, IconLoading,
  IconFile, IconThunderbolt,
} from '@arco-design/web-vue/es/icon'
import { workbenchApi as rawWorkbenchApi } from '@/api'
import PreviewCardActions from '@/components/workbench/PreviewCardActions.vue'
import { useRunningTasksStore } from '@/stores/runningTasks'

const workbenchApi: any = rawWorkbenchApi
const runningTasks = useRunningTasksStore()
const dockTaskId = ref('')
const props = defineProps({
  initialMessage: { type: String, default: '' },  // 用户的 SOP（新建模式）
  resumeDraftId: { type: String, default: '' },   // 刷新恢复模式：从这个 draft_id 重连
  resumeData: { type: Object as PropType<any>, default: null },    // 从 my-active-draft 拿到的 snapshot
})
const emit = defineEmits(['cancel', 'created'])
// created(payload: {skill_id, draft_id, git_commit, quality_score})

// ─── 状态 ───
const ws = ref<any>(null)
const draftId = ref('')
const attempt = ref(1)
const milestoneList = ref<any[]>([])    // [{key, label}, ...]
const milestoneState = reactive<Record<string, string>>({})  // key → 'active' | 'done'
const milestoneTimings = reactive<Record<string, number>>({})  // key → seconds
const toolEvents = ref<any[]>([])      // [{kind, tag, text}]
const contract = ref<any>(null)
const checkpoints = ref<any[]>([])     // 4 必感知点 review_state
const gate = ref<any>(null)
const preview = ref<any>(null)
const skillReady = ref(false)
const errorPayload = ref<any>(null)
const finalizing = ref(false)
const reconnectAttempts = ref(0)
const reconnectTimer = ref<ReturnType<typeof setTimeout> | null>(null)
const reviewingCheckpoint = ref('')
const toolLogEl = ref<HTMLElement | null>(null)

// 等待占位 & 心跳可视化：
// - elapsedSec:组件 mounted 起每秒 tick 的墙钟,驱动"已等待 Ns"
// - lastHeartbeatSec:收到后端 heartbeat 时记录当前 elapsedSec,用于展示"上次心跳 Ns 前"
// - started:是否已收到 started 事件(首响应抵达的分水岭)
const elapsedSec = ref(0)
const lastHeartbeatSec = ref<number | null>(null)
const started = ref(false)
let elapsedTimer: ReturnType<typeof setInterval> | null = null

const waitingSubtitle = computed(() => {
  if (errorPayload.value || skillReady.value) return ''
  if (!started.value) {
    return `LLM 思考中 · 已等 ${elapsedSec.value}s · 首响应通常 1–5 min`
  }
  const since = lastHeartbeatSec.value == null ? null : elapsedSec.value - lastHeartbeatSec.value
  if (since != null && since <= 60) {
    return `活跃 · 已运行 ${elapsedSec.value}s · 上次心跳 ${since}s 前`
  }
  return `已运行 ${elapsedSec.value}s`
})

const canInterrupt = computed(() => !!ws.value && !skillReady.value && !errorPayload.value)
const pendingCount = computed(() => (gate.value?.items || []).filter((i: Record<string, unknown>) => !i.passed).length)

// ─── 工具调用日志：根据事件类型给标签和颜色 ───
function pushToolEvent(kind: string, tag: string, text: string) {
  toolEvents.value.push({ kind, tag, text })
  // 自动滚动到底部
  nextTick(() => {
    if (toolLogEl.value) toolLogEl.value.scrollTop = toolLogEl.value.scrollHeight
  })
}

// ─── WS 断线重连 ───
function onWsClose() {
  ws.value = null
  if (skillReady.value || errorPayload.value) return
  if (draftId.value && reconnectAttempts.value < 3) {
    reconnectAttempts.value++
    pushToolEvent('warn', 'WS', `连接断开，2s 后重连 (${reconnectAttempts.value}/3)...`)
    reconnectTimer.value = setTimeout(() => {
      const sock = workbenchApi.openCreateSkillStream()
      ws.value = sock
      sock.onopen = () => {
        sock.send(JSON.stringify({ type: 'resume', draft_id: draftId.value }))
        pushToolEvent('info', 'WS', '已重连')
        reconnectAttempts.value = 0
      }
      sock.onmessage = (e: Record<string, unknown>) => { try { handleFrame(JSON.parse(String(e.data))) } catch {} }
      sock.onerror = () => {}
      sock.onclose = onWsClose
    }, 2000)
  } else if (!skillReady.value && !errorPayload.value) {
    errorPayload.value = { code: 'WS_DISCONNECTED', detail: '连接断开且重连失败', scratch_dir: '' }
  }
}

// 任务坞：按 draftId 注册/复用任务
function registerDockTask(draft: string, initialMessage: string) {
  if (!draft) return
  const id = runningTasks.startOrUpdate(`creation:${draft}`, {
    kind: 'creation',
    title: '新建 Skill',
    skillName: '新 Skill 生成中',
    message: initialMessage,
    returnPath: '/skills/new',
  })
  dockTaskId.value = id
}

// ─── WS 连接和事件分发 ───
function start() {
  // resume 模式：用已有 draft_id 重连
  if (props.resumeDraftId) {
    draftId.value = props.resumeDraftId
    registerDockTask(props.resumeDraftId, skillReady.value ? '等待确认' : '后台继续生成中')
    // 先从 snapshot 恢复已完成的 milestones
    if (props.resumeData) {
      const rd = props.resumeData
      if (rd.contract) contract.value = rd.contract
      if (rd.checkpoints) checkpoints.value = rd.checkpoints
      if (rd.gate) gate.value = rd.gate
      if (rd.preview) preview.value = rd.preview
      if (rd.generation_status === 'ready') {
        skillReady.value = true
        pushToolEvent('done', '✅', '生成已完成（从历史恢复）')
        if (dockTaskId.value) {
          runningTasks.finish(dockTaskId.value, { status: 'success', message: '已生成，待确认' })
        }
        return  // 不需要 WS，直接显示 4 必感知点
      }
      // 恢复已完成的 milestones
      for (const m of (rd.milestones || [])) {
        if (m.key) {
          milestoneState[m.key] = 'done'
          if (m.elapsed_s) milestoneTimings[m.key] = m.elapsed_s
        }
      }
      pushToolEvent('start', 'RESUME', `从历史恢复，继续接收实时事件`)
    }
    // 建 WS 重连
    ws.value = workbenchApi.openCreateSkillStream()
    ws.value.onopen = () => {
      ws.value.send(JSON.stringify({ type: 'resume', draft_id: props.resumeDraftId }))
    }
  } else {
    // 新建模式
    if (!props.initialMessage) {
      Message.error('缺少 SOP 输入')
      return
    }
    ws.value = workbenchApi.openCreateSkillStream()
    ws.value.onopen = () => {
      ws.value.send(JSON.stringify({ type: 'start', message: props.initialMessage }))
    }
  }
  ws.value.onmessage = (e: Record<string, unknown>) => {
    try {
      const data = JSON.parse(String(e.data))
      handleFrame(data)
    } catch (err) {
      console.error('SkillCreationProgress parse error', err, e.data)
    }
  }
  ws.value.onerror = () => {}
  ws.value.onclose = onWsClose
}

function handleFrame(f: any) {
  switch (f.type) {
    case 'heartbeat':
      // 后端 router_contract 每 25s 推一次,用于维持 nginx 连接 + 给用户活跃信号
      lastHeartbeatSec.value = elapsedSec.value
      break
    case 'draft_created':
      draftId.value = f.draft_id
      registerDockTask(f.draft_id, '准备生成...')
      break
    case 'started':
      started.value = true
      attempt.value = f.attempt || 1
      milestoneList.value = (f.milestones || []).map((k: string) => ({
        key: k, label: f.milestone_labels?.[k] || k,
      }))
      pushToolEvent('start', `START`, `子进程已启动 (attempt ${f.attempt})`)
      if (dockTaskId.value) {
        runningTasks.updateTask(dockTaskId.value, {
          message: attempt.value > 1 ? `生成中（重试 ${attempt.value}）` : '生成中',
        })
      }
      break
    case 'milestone':
      // 标记当前 milestone 为 active，已完成的为 done
      // 实际推进：把这个 key 之前的所有 key 都设为 done，这个 key 设为 done，下一个设为 active
      markMilestoneDone(f.milestone, f.elapsed_s)
      if (dockTaskId.value) {
        const m = milestoneList.value.find(x => x.key === f.milestone)
        const idx = milestoneList.value.findIndex(x => x.key === f.milestone)
        runningTasks.updateTask(dockTaskId.value, {
          message: m ? `${idx + 1}/${milestoneList.value.length} · ${m.label}` : undefined,
        })
      }
      break
    case 'thinking':
      pushToolEvent('think', '思考', f.text.length > 200 ? f.text.slice(0, 200) + '...' : f.text)
      break
    case 'tool_call':
      formatToolCall(f.name, f.input)
      break
    case 'tool_result':
      if (f.is_error) {
        pushToolEvent('error', '✗', f.content_preview || '工具返回错误')
      } else {
        pushToolEvent('result', '✓', f.content_preview || '')
      }
      break
    case 'contract_ready':
      contract.value = f.contract
      pushToolEvent('contract', '⭐', '任务合同就绪，已加载到左侧预览')
      break
    case 'skill_ready':
      // runner 的原始事件，含 files dict（前端不需要直接用，等 enriched 那帧）
      pushToolEvent('done', '✅', `生成完成，耗时 ${f.elapsed_s}s`)
      break
    case 'skill_ready_enriched':
      // 服务端组合后的 review_state + gate
      contract.value = f.contract
      checkpoints.value = f.checkpoints || []
      gate.value = f.gate
      preview.value = f.preview
      skillReady.value = true
      // 把所有未完成的 milestone 都标 done（防止 verify_import 没被检测到）
      for (const m of milestoneList.value) {
        if (milestoneState[m.key] !== 'done') milestoneState[m.key] = 'done'
      }
      if (dockTaskId.value) {
        runningTasks.finish(dockTaskId.value, {
          status: 'success',
          message: '已生成，待确认',
        })
      }
      break
    case 'retry':
      pushToolEvent('retry', '⚠ RETRY', `${f.code}: ${f.reason}`)
      // 重置 milestone 显示
      for (const k of Object.keys(milestoneState)) delete milestoneState[k]
      for (const k of Object.keys(milestoneTimings)) delete milestoneTimings[k]
      break
    case 'error':
      errorPayload.value = f
      pushToolEvent('error', '✗ ERROR', `${f.code}: ${f.detail}`)
      if (dockTaskId.value) {
        runningTasks.fail(dockTaskId.value, `${f.code || 'ERROR'}: ${(f.detail || '').slice(0, 80)}`)
      }
      break
    case 'done':
      // 流终止
      try { ws.value?.close() } catch {}
      break
    default:
      console.warn('SkillCreationProgress 未识别事件:', f)
  }
}

function markMilestoneDone(key: string, elapsedS: number) {
  // 找到这个 key 在列表的位置，把它和之前所有都设 done
  const idx = milestoneList.value.findIndex(m => m.key === key)
  if (idx < 0) return
  for (let i = 0; i <= idx; i++) {
    const k = milestoneList.value[i].key
    if (milestoneState[k] !== 'done') {
      milestoneState[k] = 'done'
      // 只在第一次记录耗时
      if (elapsedS !== undefined && !milestoneTimings[k]) {
        milestoneTimings[k] = elapsedS
      }
    }
  }
  // 下一个设为 active
  if (idx + 1 < milestoneList.value.length) {
    const nextKey = milestoneList.value[idx + 1].key
    if (milestoneState[nextKey] !== 'done') milestoneState[nextKey] = 'active'
  }
}

function formatToolCall(name: string, input: any) {
  if (name === 'Write' || name === 'MultiEdit' || name === 'Edit') {
    pushToolEvent('write', `[${name}]`, `${input.file_path || '?'} (${input.size || '?'} chars)`)
  } else if (name === 'Read') {
    pushToolEvent('read', '[Read]', input.file_path || '?')
  } else if (name === 'Bash') {
    pushToolEvent('bash', '[Bash]', input.command || '?')
  } else if (name === 'Glob' || name === 'Grep') {
    pushToolEvent('search', `[${name}]`, input.pattern || '?')
  } else {
    pushToolEvent('tool', `[${name}]`, JSON.stringify(input).slice(0, 120))
  }
}

// ─── 4 必感知点：调旧的 review 接口 ───
function checkpointState(key: string) {
  return (checkpoints.value || []).find(c => c.key === key) || null
}
function canApprove(key: string) {
  const item = checkpointState(key)
  return !!(item && item.required && !item.approved)
}
function cpColor(key: string) {
  const item = checkpointState(key)
  if (!item) return ''
  if (item.decision === 'not_required') return ''
  return item.approved ? 'green' : 'orange'
}
function cpLabel(key: string) {
  const item = checkpointState(key)
  if (!item) return ''
  if (item.decision === 'not_required') return '无需确认'
  return item.approved ? '已确认' : '待确认'
}

async function submitReview(checkpoint: string) {
  if (!draftId.value) return
  reviewingCheckpoint.value = checkpoint
  try {
    const resp = await workbenchApi.reviewTaskContract(draftId.value, {
      checkpoint, decision: 'approved', detail: { source: 'skill_creation_progress' },
    })
    checkpoints.value = resp.checkpoints || checkpoints.value
    gate.value = resp.gate || gate.value
  } catch (e) {
    Message.error(String((e as Record<string, unknown>)?._message || '确认失败'))
  } finally {
    reviewingCheckpoint.value = ''
  }
}

// ─── finalize ───
async function onFinalize() {
  if (!gate.value?.can_publish) {
    Message.warning('请先确认 4 必感知点')
    return
  }
  finalizing.value = true
  try {
    const resp = await workbenchApi.finalizeCreateSkill(draftId.value)
    Message.success(`Skill ${resp.skill_id} 已创建`)
    if (dockTaskId.value) {
      runningTasks.dismiss(dockTaskId.value)
    }
    emit('created', resp)
  } catch (e) {
    Message.error(String((e as Record<string, unknown>)?._message || '创建失败'))
  } finally {
    finalizing.value = false
  }
}

async function onCancel() {
  // 告诉后端放弃草稿（标记 cancelled），刷新后不再弹出恢复
  if (draftId.value) {
    try {
      await workbenchApi.dismissDraft(draftId.value)
    } catch { /* 静默 */ }
  }
  if (dockTaskId.value) {
    runningTasks.dismiss(dockTaskId.value)
  }
  cleanup()
  emit('cancel')
}

function onInterrupt() {
  // 直接关 WS。后端 SubprocessSession 会检测到 stdin EOF 自动退出
  cleanup()
  errorPayload.value = { code: 'INTERRUPTED', detail: '用户中断了创建', scratch_dir: '' }
  if (dockTaskId.value) {
    runningTasks.fail(dockTaskId.value, '用户中断')
  }
}

function cleanup() {
  if (reconnectTimer.value) {
    clearTimeout(reconnectTimer.value)
    reconnectTimer.value = null
  }
  try { ws.value?.close() } catch {}
  ws.value = null
  if (elapsedTimer) {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}

onMounted(() => {
  // 每秒 tick 一次,驱动"已等待 Ns"和心跳新鲜度计算
  elapsedTimer = setInterval(() => { elapsedSec.value += 1 }, 1000)
})

onBeforeUnmount(cleanup)

// ─── 暴露给父组件 ───
defineExpose({ start, draftId })
</script>

<style scoped>
.scp {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 600px;
  background: var(--ai-surface);
  font-size: 13px;
}

.scp-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  border-bottom: 1px solid var(--ai-border);
}
.scp-title-col {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.scp-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
}
.scp-subtitle {
  display: flex;
  align-items: center;
  font-size: 12px;
  color: var(--ai-ink-3);
  font-variant-numeric: tabular-nums;
}
.scp-attempt {
  font-size: 12px;
  color: var(--ai-warn);
  font-weight: 500;
}

/* 顶部步骤条 */
.scp-steps {
  display: flex;
  gap: 8px;
  padding: 16px 20px;
  background: var(--ai-surface-2);
  border-bottom: 1px solid var(--ai-border);
  overflow-x: auto;
}
.scp-step {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  flex-shrink: 0;
  transition: all 0.2s;
}
.scp-step-icon {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: 11px;
  font-weight: 600;
}
.scp-step-num { font-size: 11px; }
.scp-step-label { font-size: 12px; color: var(--ai-ink-1); }
.scp-step-time { font-size: 10px; color: var(--ai-ink-3); margin-top: 2px; }

.scp-step-done {
  border-color: var(--ai-ok);
  background: rgba(var(--green-1), 0.4);
}
.scp-step-done .scp-step-icon {
  background: var(--sf-success-solid);
  color: white;
}
.scp-step-active {
  border-color: var(--ai-info);
  background: rgba(var(--arcoblue-1), 0.4);
}
.scp-step-active .scp-step-icon {
  background: var(--ai-info);
  color: white;
}

/* 中部双栏 */
.scp-body {
  flex: 1;
  display: flex;
  gap: 12px;
  padding: 12px 20px;
  overflow: hidden;
}
.scp-left, .scp-right {
  flex: 1;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  overflow: hidden;
  min-width: 0;
}
.scp-pane-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  font-size: 12px;
  font-weight: 600;
  color: var(--ai-ink-2);
  background: var(--ai-surface-2);
  border-bottom: 1px solid var(--ai-border);
}
.scp-pane-hint {
  margin-left: auto;
  font-size: 11px;
  color: var(--ai-ink-3);
  font-weight: 400;
}

/* contract 卡片 */
.scp-contract-cards {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.scp-card {
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.scp-card-wide {
  grid-column: 1 / -1;
}
.scp-card-h {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 600;
  color: var(--ai-ink-1);
}
.scp-card-body {
  font-size: 12px;
  color: var(--ai-ink-2);
  max-height: 200px;
  overflow-y: auto;
}
.scp-card-actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  margin-top: 4px;
}

.scp-input-row {
  display: flex;
  justify-content: space-between;
  padding: 5px 0;
  /* webkit 1px dashed 渲染成 solid，用 background gradient pattern 代替 */
  background-image: linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%);
  background-position: bottom;
  background-size: 6px 1px;
  background-repeat: repeat-x;
  font-size: 11px;
  gap: 8px;
}
.scp-input-row:last-child { background-image: none; }
.scp-input-row strong { color: var(--ai-ink-1); font-weight: 600; }
.scp-input-row span { color: var(--ai-ink-3); font-size: 10px; }

.scp-perm-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
  font-size: 11px;
}
.scp-perm-warn {
  margin-left: auto;
  color: var(--ai-bad);
  font-size: 10px;
}
.scp-trigger-cron { display: flex; align-items: center; gap: 8px; }
.scp-trigger-cron code { font-family: var(--ai-font-mono); font-size: 11px; color: var(--ai-ink-1); }
.scp-trigger-desc { margin-top: 4px; font-size: 11px; color: var(--ai-ink-2); line-height: 1.5; }
.scp-preview-pre {
  font-family: var(--ai-font-mono);
  font-size: 11px;
  background: var(--ai-surface-2);
  padding: 8px;
  border-radius: 4px;
  margin: 0;
  max-height: 150px;
  overflow: auto;
  white-space: pre-wrap;
}

/* 工具调用日志 */
.scp-tool-log {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
  font-family: var(--ai-font-mono);
  font-size: 11px;
  background: var(--ai-surface-2);
}
.scp-tool-row {
  display: flex;
  gap: 8px;
  padding: 4px 6px;
  border-radius: 3px;
  align-items: flex-start;
  line-height: 1.5;
}
.scp-tool-row:hover { background: var(--ai-surface-2); }
.scp-tool-tag {
  flex-shrink: 0;
  font-weight: 600;
  color: var(--ai-ink-3);
  min-width: 60px;
}
.scp-tool-text {
  word-break: break-all;
  color: var(--ai-ink-1);
}

.scp-tool-write .scp-tool-tag { color: var(--ai-ok); }
.scp-tool-bash .scp-tool-tag { color: rgb(var(--purple-6)); }
.scp-tool-read .scp-tool-tag { color: var(--ai-info); }
.scp-tool-think .scp-tool-tag { color: rgb(var(--cyan-6)); }
.scp-tool-think .scp-tool-text { color: var(--ai-ink-2); font-style: italic; }
.scp-tool-result .scp-tool-tag { color: var(--ai-ok); }
.scp-tool-error .scp-tool-tag { color: var(--ai-bad); }
.scp-tool-error .scp-tool-text { color: var(--ai-bad); }
.scp-tool-contract .scp-tool-tag { color: var(--ai-warn); }
.scp-tool-done .scp-tool-tag { color: var(--ai-ok); }
.scp-tool-retry .scp-tool-tag { color: var(--ai-warn); }

.scp-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 32px;
  color: var(--ai-ink-3);
  font-size: 12px;
}

.scp-error-banner {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 12px;
  background: rgba(var(--red-1), 0.5);
  border: 1px solid var(--ai-bad);
  border-radius: 4px;
  margin: 8px;
  flex-wrap: wrap;
  font-size: 11px;
}
.scp-error-banner strong { color: var(--ai-bad); }
.scp-error-scratch {
  width: 100%;
  margin-top: 4px;
  font-size: 10px;
  color: var(--ai-ink-3);
}
.scp-error-scratch code {
  background: var(--ai-surface-2);
  padding: 1px 4px;
  border-radius: 3px;
}

/* 底部 finalize */
.scp-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  border-top: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
}
.scp-gate-ok {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--ai-ok);
  font-size: 13px;
  font-weight: 600;
}
.scp-gate-warn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--ai-warn);
  font-size: 13px;
}
</style>
