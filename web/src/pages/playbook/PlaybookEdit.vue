<template>
  <div class="page-container">
    <a-spin :loading="loading" style="width: 100%">
    <div class="page-header page-detail-toolbar">
        <a-button @click="$router.push(`/playbook/${pbName}`)"><icon-left /> 返回</a-button>
        <h2 class="page-title">编辑 {{ pbName }}</h2>
      <a-space>
        <a-button @click="handleValidate">验证</a-button>
        <a-button type="primary" @click="handleSave" :loading="saving">保存</a-button>
      </a-space>
    </div>

    <div class="editor-layout">
      <!-- 画布（主区域） -->
      <div class="canvas-area">
        <iframe
          ref="editorFrame"
          :src="`/playbook-editor/index.html?name=${pbName}`"
          class="editor-iframe"
          @load="onFrameLoad"
        />
      </div>

      <!-- 右侧 YAML 预览（可折叠） -->
      <div class="yaml-panel" :class="{ collapsed: yamlCollapsed }">
        <div class="yaml-panel-header" @click="yamlCollapsed = !yamlCollapsed">
          <span>{{ yamlCollapsed ? 'YAML' : 'YAML 预览' }}</span>
          <icon-right v-if="yamlCollapsed" />
          <icon-left v-else />
        </div>
        <a-textarea
          v-if="!yamlCollapsed"
          v-model="yamlPreview"
          :auto-size="{ minRows: 20, maxRows: 50 }"
          readonly
          class="yaml-textarea"
        />
      </div>
    </div>
    <!-- 条件编辑器 -->
    <ConditionEditor
      ref="conditionEditorRef"
      :step-ids="availableStepIds"
      :step-outputs="availableStepOutputs"
      @save="onConditionSave"
    />

    </a-spin>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { playbookApi as rawPlaybookApi, skillApi as rawSkillApi } from '@/api'
import { IconLeft, IconRight } from '@arco-design/web-vue/es/icon'
import ConditionEditor from '@/components/ConditionEditor.vue'

const playbookApi: any = rawPlaybookApi
const skillApi: any = rawSkillApi
const route: any = useRoute()
const pbName: string = route.params.name

const loading = ref(false)
const saving = ref(false)
const yamlCollapsed = ref(false)
const yamlPreview = ref('')
const editorFrame = ref<any>(null)
const playbook = ref<any>({})
const pendingSteps = ref<any[] | null>(null)
const pendingLayout = ref<any>(null)

// 条件编辑器
const conditionEditorRef = ref<any>(null)
let _editingEdgeId: string | null = null

const availableStepIds = computed(() => {
  const steps = pendingSteps.value || playbook.value?.steps || []
  return steps.map((s: Record<string, unknown>) => s.skill_id || s.id || s.name).filter(Boolean)
})

const availableStepOutputs = computed(() => {
  const outputs = {}
  const steps = pendingSteps.value || playbook.value?.steps || []
  for (const s of steps) {
    const id = String(s.skill_id || s.id || s.name || '')
    ;(outputs as Record<string, string[]>)[id] = (s.output_fields as string[]) || ['result']
  }
  return outputs
})

// 监听画布变更
function onMessage(e: MessageEvent) {
  if (e.origin !== window.location.origin) return
  const data = e.data
  if (!data || data.source !== 'playbook-editor') return

  if (data.type === 'playbook-changed') {
    pendingSteps.value = data.payload.steps
    pendingLayout.value = data.payload._canvas_layout
    // 更新 YAML 预览
    const preview = {
      name: playbook.value.name || pbName,
      description: playbook.value.description || '',
      department: playbook.value.department || '',
      steps: data.payload.steps,
      _canvas_layout: data.payload._canvas_layout,
    }
    yamlPreview.value = JSON.stringify(preview, null, 2)
  }

  if (data.type === 'editor-ready') {
    // 画布就绪，推送数据
    pushDataToEditor()
  }

  // 画布请求打开条件编辑器
  if (data.type === 'edit-condition') {
    _editingEdgeId = data.payload?.edgeId
    conditionEditorRef.value?.open(data.payload?.condition)
  }
}

function onConditionSave(condition: any) {
  // 将结构化条件发回画布
  const frame = editorFrame.value
  if (frame && frame.contentWindow) {
    frame.contentWindow.postMessage({
      type: 'update-condition',
      payload: { edgeId: _editingEdgeId, condition },
    }, window.location.origin)
  }
  _editingEdgeId = null
}

function pushDataToEditor() {
  const frame = editorFrame.value
  if (!frame || !frame.contentWindow) return
  // 推送 Playbook 数据
  frame.contentWindow.postMessage({
    type: 'load-playbook',
    payload: playbook.value,
  }, window.location.origin)
}

function onFrameLoad() {
  // 也在 iframe load 时推送
  pushDataToEditor()
}

async function loadPlaybook() {
  loading.value = true
  try {
    const [data, skills] = await Promise.all([
      playbookApi.get(pbName),
      skillApi.list().catch(() => []),
    ])
    playbook.value = data
    yamlPreview.value = JSON.stringify(data, null, 2)

    // 推送 Skill 列表给画布
    const frame = editorFrame.value
    if (frame && frame.contentWindow) {
      frame.contentWindow.postMessage({
        type: 'set-skills',
        payload: Array.isArray(skills) ? skills : [],
      }, window.location.origin)
    }
  } catch (e: any) {
    Message.error(e._message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  saving.value = true
  try {
    const saveData = {
      ...playbook.value,
      steps: pendingSteps.value || playbook.value.steps || [],
    }
    if (pendingLayout.value) {
      saveData._canvas_layout = pendingLayout.value
    }
    await playbookApi.save(pbName, saveData)
    Message.success('保存成功')
  } catch (e: any) {
    Message.error(e._message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function handleValidate() {
  try {
    const result = await playbookApi.validate(pbName)
    if (result.valid) {
      Message.success('验证通过')
    } else {
      Message.warning(`验证发现 ${result.errors?.length || 0} 个问题`)
    }
  } catch (e: any) {
    Message.error(e._message || '验证失败')
  }
}

onMounted(() => {
  window.addEventListener('message', onMessage)
  loadPlaybook()
})

onUnmounted(() => {
  window.removeEventListener('message', onMessage)
})
</script>

<style scoped>
.editor-layout {
  display: flex;
  gap: 0;
  height: calc(100vh - 120px);
}
.canvas-area {
  flex: 1;
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  overflow: hidden;
}
.editor-iframe {
  width: 100%;
  height: 100%;
  border: none;
}
.yaml-panel {
  width: 320px;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--ai-border);
  border-left: none;
  border-radius: 0 4px 4px 0;
  background: var(--ai-surface-2);
  transition: width 0.2s;
}
.yaml-panel.collapsed {
  width: 36px;
  cursor: pointer;
}
.yaml-panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  user-select: none;
  border-bottom: 1px solid var(--ai-border);
}
.yaml-textarea :deep(textarea) {
  font-family: var(--ai-font-mono);
  font-size: 12px;
  flex: 1;
}
@media (max-width: 768px) {
  .editor-layout {
    flex-direction: column;
  }
  .yaml-panel {
    width: 100%;
    border-left: 1px solid var(--ai-border);
    border-radius: 0 0 4px 4px;
  }
  .yaml-panel.collapsed {
    width: 100%;
  }
}
</style>
