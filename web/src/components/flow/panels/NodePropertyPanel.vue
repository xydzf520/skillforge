<template>
  <div class="node-prop-panel" v-if="node">
    <div class="prop-header">
      <span class="prop-icon">{{ iconMap[node.type] || '📋' }}</span>
      <span class="prop-title">{{ titleMap[node.type] || '节点属性' }}</span>
      <button class="prop-close" @click="$emit('close')">×</button>
    </div>

    <div class="prop-body">
      <!-- 数据源 -->
      <template v-if="node.type === 'dataSource'">
        <div class="field"><label>名称</label><input v-model="form.name" @input="emitChange" /></div>
        <div class="field"><label>来源</label><input v-model="form.source" @input="emitChange" /></div>
        <div class="field"><label>频率</label><input v-model="form.frequency" @input="emitChange" /></div>
        <div v-if="node.data.is_active !== null" class="field-info">
          状态: {{ node.data.is_active ? '🟢 在线' : '🔴 离线' }}
          <span v-if="node.data.last_updated"> · {{ node.data.last_updated }}</span>
        </div>
      </template>

      <!-- 决策步骤 -->
      <template v-if="node.type === 'decisionStep'">
        <div class="field"><label>步骤名称</label><input v-model="form.name" @input="emitChange" /></div>
        <div class="field"><label>描述</label><textarea v-model="form.description" rows="2" @input="emitChange"></textarea></div>
        <div class="branch-list">
          <div v-for="(b, i) in form.branches" :key="i" class="branch-item">
            <div class="branch-head">
              <span class="branch-idx">分支 {{ Number(i) + 1 }}</span>
              <button v-if="form.branches.length > 1" class="branch-del" @click="removeBranch(Number(i))">删除</button>
            </div>
            <div class="field"><label>条件</label><input v-model="b.condition" @input="emitChange" /></div>
            <div class="field"><label>结论</label><input v-model="b.conclusion" @input="emitChange" /></div>
            <div class="field"><label>动作</label><input v-model="b.action" @input="emitChange" /></div>
            <div class="field">
              <label>下一步</label>
              <select v-model="b.next_step" @change="emitChange">
                <option :value="null">无（终止）</option>
                <option v-for="s in availableSteps" :key="s" :value="s">Step {{ s }}</option>
              </select>
            </div>
          </div>
          <button class="add-branch" @click="addBranch">+ 添加分支</button>
        </div>
      </template>

      <!-- 结论 -->
      <template v-if="node.type === 'conclusion'">
        <div class="field"><label>结论</label><input v-model="form.conclusion" @input="emitChange" /></div>
        <div class="field"><label>动作</label><input v-model="form.action" @input="emitChange" /></div>
        <div class="field">
          <label>下一步</label>
          <select v-model="form.next_step" @change="emitChange">
            <option :value="null">无</option>
            <option v-for="s in availableSteps" :key="s" :value="s">Step {{ s }}</option>
          </select>
        </div>
      </template>

      <!-- 参数 -->
      <template v-if="node.type === 'param'">
        <div v-for="(val, key) in form.params" :key="key" class="field param-field">
          <label>{{ key }}</label>
          <input v-model="form.params[key]" @input="emitChange" />
        </div>
      </template>

      <!-- 脚本 -->
      <template v-if="node.type === 'script'">
        <div class="field-info">📄 {{ node.data.path }}</div>
        <div class="field-info">大小: {{ formatSize(node.data.size) }}</div>
        <button class="view-btn" @click="$emit('view-script', node.data)">查看源码</button>
      </template>

      <!-- 输出 -->
      <template v-if="node.type === 'output'">
        <div class="field"><label>名称</label><input v-model="form.name" @input="emitChange" /></div>
        <div class="field"><label>格式</label><input v-model="form.format" @input="emitChange" /></div>
        <div class="field"><label>收件人</label><input v-model="form.recipient" @input="emitChange" /></div>
        <div class="field">
          <label>审批级别</label>
          <select v-model="form.approval_level" @change="emitChange">
            <option value="自动">自动 (L0)</option>
            <option value="L1">L1 组长</option>
            <option value="L2">L2 总监</option>
            <option value="L3">L3 VP</option>
          </select>
        </div>
      </template>

      <!-- 审批 -->
      <template v-if="node.type === 'approval'">
        <div class="field-info">审批级别: {{ node.data.level }}</div>
        <div class="field-info">触发方式: {{ node.data.trigger_type }}</div>
        <div v-if="node.data.targets?.length" class="field-info">
          目标用户: {{ node.data.targets.join(', ') }}
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, reactive } from 'vue'
import type { PropType } from 'vue'

const props = defineProps({
  node: Object as PropType<any>,
  allSteps: { type: Array as PropType<any[]>, default: () => [] },
})
const emit = defineEmits(['change', 'close', 'view-script'])

const form = reactive<Record<string, any>>({})
const availableSteps = ref<any[]>([])

watch(() => props.node, (n) => {
  if (!n) return
  Object.assign(form, JSON.parse(JSON.stringify(n.data || {})))
  availableSteps.value = props.allSteps.filter(s => s !== n.data?.id)
}, { immediate: true, deep: true })

function emitChange() {
  emit('change', { nodeId: props.node.id, type: props.node.type, data: JSON.parse(JSON.stringify(form)) })
}

function addBranch() {
  if (!form.branches) form.branches = []
  form.branches.push({ condition: '', conclusion: '', action: '', next_step: null })
  emitChange()
}

function removeBranch(i: number) {
  form.branches.splice(i, 1)
  emitChange()
}

function formatSize(b: number) { return b > 1024 ? `${(b/1024).toFixed(1)} KB` : `${b || 0} B` }

const iconMap: Record<string, string> = { dataSource: '📊', decisionStep: '🔀', conclusion: '✅', script: '🔧', param: '⚙', output: '📤', approval: '👤' }
const titleMap: Record<string, string> = { dataSource: '数据源', decisionStep: '决策步骤', conclusion: '结论', script: '脚本', param: '参数配置', output: '输出定义', approval: '审批路由' }
</script>

<style scoped>
.node-prop-panel {
  width: 300px; background: #fff; border-left: 1px solid #e5e6eb;
  display: flex; flex-direction: column; height: 100%; overflow: hidden;
}
.prop-header {
  display: flex; align-items: center; gap: 8px; padding: 12px 14px;
  border-bottom: 1px solid #e5e6eb; flex-shrink: 0;
}
.prop-icon { font-size: 16px; }
.prop-title { font-size: 14px; font-weight: 600; flex: 1; }
.prop-close { border: none; background: none; font-size: 18px; cursor: pointer; color: #86909c; }
.prop-body { flex: 1; overflow-y: auto; padding: 14px; }

.field { margin-bottom: 10px; }
.field label { display: block; font-size: 11px; font-weight: 500; color: #86909c; margin-bottom: 3px; }
.field input, .field textarea, .field select {
  width: 100%; padding: 6px 8px; border: 1px solid #e5e6eb; border-radius: 6px;
  font-size: 13px; outline: none; font-family: inherit;
}
.field input:focus, .field textarea:focus, .field select:focus { border-color: var(--ai-ink-1); }
.field-info { font-size: 12px; color: #667085; margin-bottom: 8px; }

.branch-list { margin-top: 8px; }
.branch-item { background: #f7f8fa; border-radius: 8px; padding: 10px; margin-bottom: 8px; }
.branch-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.branch-idx { font-size: 12px; font-weight: 600; color: #344054; }
.branch-del { border: none; background: none; color: #f04438; cursor: pointer; font-size: 12px; }
/* 4 边 dashed "+ 添加分支" 按钮：webkit 1px dashed 4 边渲染成 solid，用 4 个 gradient 拼出虚线框 */
.add-branch {
  width: 100%; padding: 9px 8px; border-radius: 6px;
  background-color: transparent;
  background-image:
    linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to bottom, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to bottom, var(--ai-border-2) 50%, transparent 0%);
  background-position: top, bottom, left, right;
  background-size: 6px 1px, 6px 1px, 1px 6px, 1px 6px;
  background-repeat: repeat-x, repeat-x, repeat-y, repeat-y;
  color: var(--ai-ink-3); cursor: pointer; font-size: 12px;
}
.add-branch:hover { color: var(--ai-ink-1); }
.view-btn {
  width: 100%; padding: 8px; border: 1px solid #fdba74; border-radius: 6px;
  background: #fff7ed; color: #c2410c; cursor: pointer; font-size: 13px; font-weight: 500;
}
.param-field input { font-family: monospace; font-size: 12px; }
</style>
