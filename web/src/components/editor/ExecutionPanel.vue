<template>
  <div class="exec-panel" :style="{ height: panelHeight + 'px' }">
    <!-- 拖拽调整高度 -->
    <div class="exec-resize-bar" @mousedown="startResize"></div>

    <div class="exec-toolbar">
      <span class="panel-title">执行面板</span>
      <a-radio-group v-model="inputMode" size="mini" type="button">
        <a-radio value="json">JSON</a-radio>
        <a-radio value="form">表单</a-radio>
      </a-radio-group>
      <div style="flex: 1"></div>
      <a-button type="primary" size="mini" :loading="running" @click="runExecution" :disabled="!skillId">
        <template #icon><icon-play-arrow /></template>执行
      </a-button>
      <a-button size="mini" @click="$emit('close')"><icon-close /></a-button>
    </div>

    <div class="exec-body">
      <!-- 左侧：参数输入 -->
      <div class="exec-input">
        <div class="section-label">输入参数</div>
        <textarea
          v-if="inputMode === 'json'"
          v-model="paramsJson"
          class="param-textarea"
          placeholder='{"key": "value"}'
          spellcheck="false"
        ></textarea>
        <div v-else class="param-form">
          <div v-for="(val, key) in formParams" :key="key" class="form-row">
            <label>{{ key }}</label>
            <a-input v-model="formParams[key]" size="small" />
          </div>
          <div v-if="Object.keys(formParams).length === 0" class="form-empty">
            从 JSON 中提取参数或手动输入
          </div>
        </div>
      </div>

      <!-- 右侧：执行结果 -->
      <div class="exec-output">
        <div class="section-label">执行结果</div>
        <div v-if="!result && !running && !error" class="output-empty">
          点击执行按钮或按 <kbd>Ctrl+Shift+R</kbd> 运行沙箱
        </div>

        <div v-if="running" class="output-running">
          <a-spin />
          <span>{{ statusMessage }}</span>
        </div>

        <div v-if="error" class="output-error">
          <a-alert type="error" :title="error" />
        </div>

        <!-- 数据质量门禁阻止 -->
        <div v-if="result && result.status === 'blocked'" class="output-blocked">
          <a-alert type="error" title="执行被数据质量门禁阻止">
            <template #content>
              <ul style="margin: 4px 0 0; padding-left: 18px;">
                <li v-for="reason in result.reasons" :key="reason">{{ reason }}</li>
              </ul>
            </template>
          </a-alert>
        </div>

        <div v-if="result && result.status !== 'blocked'" class="output-result">
          <!-- 决策路径 -->
          <div v-if="result.output?.decision_path" class="decision-path">
            <div class="section-sublabel">决策路径</div>
            <div
              v-for="(path, i) in result.output.decision_path"
              :key="i"
              class="path-step"
              @click="onPathClick(path)"
            >
              <span class="path-index">{{ Number(i) + 1 }}</span>
              <span>{{ path }}</span>
            </div>
          </div>

          <!-- 结论 -->
          <div v-if="result.output?.conclusion" class="conclusion-tag">
            <a-tag :color="conclusionColor(result.output.conclusion)" size="large">
              {{ result.output.conclusion }}
            </a-tag>
          </div>

          <!-- 原始输出 -->
          <div class="section-sublabel">原始输出</div>
          <pre class="output-json">{{ JSON.stringify(result.output || result, null, 2) }}</pre>

          <div v-if="result.run_id" class="run-id">Run ID: {{ result.run_id }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue'
import type { PropType } from 'vue'
import { Message } from '@arco-design/web-vue'
import { executionApi as rawExecutionApi } from '@/api'
import { IconPlayArrow, IconClose } from '@arco-design/web-vue/es/icon'
import { useRunningTasksStore } from '@/stores/runningTasks'

const props = defineProps({
  skillId: { type: String, required: true },
  parsed: { type: Object as PropType<any>, default: () => ({}) },
})

const emit = defineEmits(['jump-to-line', 'jump-to-step', 'close'])
const executionApi: any = rawExecutionApi
const runningTasks = useRunningTasksStore()

const inputMode = ref('json')
const paramsJson = ref('{}')
const formParams = ref<Record<string, any>>({})
const running = ref(false)
const statusMessage = ref('正在执行...')
const result = ref<any>(null)
const error = ref('')
const panelHeight = ref(300)

// JSON ↔ 表单同步
watch(inputMode, (mode) => {
  if (mode === 'form') {
    try {
      formParams.value = JSON.parse(paramsJson.value)
    } catch {
      formParams.value = {}
    }
  } else {
    paramsJson.value = JSON.stringify(formParams.value, null, 2)
  }
})

// 从 parsed.frontmatter.params 预填参数
watch(() => props.parsed, (p) => {
  if (p?.frontmatter?.params && paramsJson.value === '{}') {
    const defaults: Record<string, any> = {}
    for (const [key, val] of Object.entries(p.frontmatter.params)) {
      defaults[key] = (val as any)?.default ?? ''
    }
    if (Object.keys(defaults).length) {
      paramsJson.value = JSON.stringify(defaults, null, 2)
    }
  }
}, { immediate: true })

async function runExecution() {
  result.value = null
  error.value = ''
  running.value = true
  statusMessage.value = '正在执行...'

  let params = {}
  try {
    params = inputMode.value === 'json'
      ? JSON.parse(paramsJson.value)
      : { ...formParams.value }
  } catch {
    error.value = 'JSON 格式错误'
    running.value = false
    return
  }

  const skillName = props.parsed?.frontmatter?.name || props.skillId
  const taskId = runningTasks.start({
    kind: 'sandbox',
    title: '沙箱执行',
    skillId: props.skillId,
    skillName,
    returnPath: `/skills/${props.skillId}`,
  })
  try {
    // 用 HTTP API 执行沙箱（简单可靠，WebSocket 作为后续增强）
    const data = await executionApi.run({
      skill_id: props.skillId,
      params,
      sandbox: true,
    })
    result.value = data
    const conclusion = data?.output?.conclusion
    runningTasks.finish(taskId, {
      status: data?.status === 'blocked' ? 'blocked' : 'success',
      message: data?.status === 'blocked' ? '数据质量门禁阻止' : (conclusion || '执行完成'),
      runId: data?.run_id,
    })
  } catch (e: any) {
    const msg = e._message || (e instanceof Error ? e.message : String(e)) || '执行失败'
    error.value = msg
    runningTasks.fail(taskId, msg)
  } finally {
    running.value = false
  }
}

function conclusionColor(c: string) {
  if (c?.includes('绿')) return 'green'
  if (c?.includes('黄')) return 'orange'
  if (c?.includes('红')) return 'red'
  return 'blue'
}

function onPathClick(path: string) {
  // 从路径描述中提取 step_id，由父组件通过 line_map 解析为实际行号
  const match = path.match(/step_?(\d+)/i)
  if (match) {
    emit('jump-to-step', `step_${match[1]}`)
  }
}

// 拖拽调整高度 — 用 AbortController 保证组件卸载 / mouseup 丢失时监听一定清理
const _dragCtrls: AbortController[] = []
function startResize(e: MouseEvent) {
  const startY = e.clientY
  const startH = panelHeight.value
  const ctrl = new AbortController()
  _dragCtrls.push(ctrl)
  ctrl.signal.addEventListener('abort', () => {
    const idx = _dragCtrls.indexOf(ctrl)
    if (idx >= 0) _dragCtrls.splice(idx, 1)
  })
  function onMove(ev: MouseEvent) {
    panelHeight.value = Math.max(150, Math.min(600, startH - (ev.clientY - startY)))
  }
  function onUp() { ctrl.abort() }
  document.addEventListener('mousemove', onMove, { signal: ctrl.signal })
  document.addEventListener('mouseup', onUp, { signal: ctrl.signal })
}
onBeforeUnmount(() => _dragCtrls.splice(0).forEach((c) => c.abort()))

// 暴露给父组件
defineExpose({ runExecution })
</script>

<style scoped>
.exec-panel {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  flex-shrink: 0;
}
.exec-resize-bar {
  height: 4px;
  cursor: ns-resize;
  background: transparent;
  transition: background 0.15s;
}
.exec-resize-bar:hover { background: var(--ai-accent-ink); }

.exec-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-bottom: 1px solid var(--ai-border);
  flex-shrink: 0;
}
.panel-title { font-size: 12px; font-weight: 600; }

.exec-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}
.exec-input, .exec-output {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: auto;
  padding: 8px 12px;
}
.exec-input { border-right: 1px solid var(--ai-border); max-width: 40%; }

.section-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--ai-ink-3);
  text-transform: uppercase;
  margin-bottom: 6px;
}
.section-sublabel {
  font-size: 11px;
  font-weight: 600;
  color: var(--ai-ink-3);
  margin: 8px 0 4px;
}

.param-textarea {
  flex: 1;
  resize: none;
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  padding: 8px;
  font-family: var(--ai-font-mono);
  font-size: 12px;
  background: var(--ai-surface);
  color: var(--ai-ink-1);
}

.form-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.form-row label { font-size: 12px; min-width: 100px; color: var(--ai-ink-2); }
.form-empty { color: var(--ai-ink-3); font-size: 12px; }

.output-empty { color: var(--ai-ink-3); font-size: 12px; padding: 20px 0; text-align: center; }
.output-empty kbd {
  background: var(--ai-surface-2); padding: 2px 6px; border-radius: 4px;
  font-size: 11px; font-family: var(--ai-font-mono);
}
.output-running { display: flex; align-items: center; gap: 8px; padding: 20px 0; font-size: 13px; }

.decision-path { margin-bottom: 8px; }
.path-step {
  display: flex; align-items: center; gap: 8px;
  padding: 4px 8px; font-size: 12px;
  cursor: pointer; border-radius: 4px;
  transition: background 0.15s;
}
.path-step:hover { background: var(--ai-surface-2); }
.path-index {
  width: 20px; height: 20px;
  display: flex; align-items: center; justify-content: center;
  border-radius: 50%; background: var(--ai-accent-ink);
  color: var(--ai-surface); font-size: 11px; font-weight: 600; flex-shrink: 0;
}
.conclusion-tag { margin: 8px 0; }

.output-json {
  font-family: var(--ai-font-mono);
  font-size: 11px;
  background: var(--ai-surface);
  padding: 8px;
  border-radius: 4px;
  overflow: auto;
  max-height: 200px;
  white-space: pre-wrap;
  word-break: break-all;
}
.run-id { font-size: 11px; color: var(--ai-ink-3); margin-top: 8px; }
</style>
