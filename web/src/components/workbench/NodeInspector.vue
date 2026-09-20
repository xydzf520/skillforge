<template>
  <div class="ni" v-if="node">
    <div class="ni-head">
      <span class="ni-title">{{ node.name || node.id }}</span>
      <a-tag size="small" color="arcoblue">Step {{ node.id }}</a-tag>
    </div>

    <!-- 分支编辑 -->
    <div class="ni-section">
      <div class="ni-label">分支条件</div>
      <div v-for="(br, i) in node.branches" :key="i" class="ni-branch">
        <a-input v-model="br.condition" size="mini" placeholder="条件" @input="emitUpdate" />
        <a-input v-model="br.conclusion" size="mini" placeholder="结论" @input="emitUpdate" />
        <a-input v-model="br.action" size="mini" placeholder="动作" @input="emitUpdate" />
        <a-tooltip content="抽屉编辑" position="top">
          <a-button size="mini" type="text" @click="openRuleDrawer(Number(i))"><icon-edit :size="12" /></a-button>
        </a-tooltip>
        <a-button size="mini" type="text" status="danger" @click="removeBranch(Number(i))"><icon-delete :size="12" /></a-button>
      </div>
      <div class="ni-branch-actions">
        <a-button size="mini" type="text" @click="addBranch">+ 添加分支</a-button>
        <a-button v-if="node.branches?.length" size="mini" type="outline" @click="openRuleDrawer(0)">
          <icon-edit :size="12" style="margin-right: 4px" />编辑规则
        </a-button>
      </div>
    </div>

    <!-- 引用参数 -->
    <div class="ni-section" v-if="referencedParams.length">
      <div class="ni-label">引用参数</div>
      <div v-for="p in referencedParams" :key="p.name" class="ni-param" @click="$emit('edit-param', p)">
        <span class="ni-param-name">{{ p.name }}</span>
        <span class="ni-param-val">{{ p.default_value ?? p.value }}</span>
      </div>
    </div>

    <!-- AI 动作 -->
    <div class="ni-section">
      <div class="ni-label ai-gradient-text">AI 动作</div>
      <div class="ni-actions">
        <button class="ni-action" @click="$emit('ai-action', 'complete-branches')">
          <icon-branch :size="12" class="ni-action-icon" />补全相反分支
        </button>
        <button class="ni-action" @click="$emit('ai-action', 'extract-param')">
          <icon-settings :size="12" class="ni-action-icon" />提取为参数
        </button>
        <button class="ni-action" @click="$emit('ai-action', 'generate-boundary-test')">
          <icon-experiment :size="12" class="ni-action-icon" />生成边界测试
        </button>
        <button class="ni-action" @click="$emit('ai-action', 'generate-counter-example')">
          <icon-exclamation-circle :size="12" class="ni-action-icon" />生成反例
        </button>
        <button class="ni-action" @click="$emit('ai-action', 'explain-to-business')">
          <icon-message :size="12" class="ni-action-icon" />解释给业务
        </button>
        <button class="ni-action" @click="$emit('ai-action', 'fix-failed-test')">
          <icon-tool :size="12" class="ni-action-icon" />修复失败用例
        </button>
      </div>
    </div>

    <!-- 删除节点 -->
    <div class="ni-footer">
      <a-popconfirm content="确定删除此步骤？" @ok="$emit('delete-node', node.id)">
        <a-button size="small" status="danger" type="text">删除步骤</a-button>
      </a-popconfirm>
    </div>
  </div>
  <div v-else class="ni-empty">
    <icon-desktop :size="20" style="color:var(--ai-ink-4)" />
    <span>点击画布中的节点查看详情</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import {
  IconDelete,
  IconDesktop,
  IconBranch,
  IconSettings,
  IconExperiment,
  IconExclamationCircle,
  IconMessage,
  IconTool,
  IconEdit,
} from '@arco-design/web-vue/es/icon'

const props = defineProps({
  node: { type: Object as PropType<any>, default: null },
  allParams: { type: Array as PropType<any[]>, default: () => [] },
})

const emit = defineEmits(['update', 'edit-param', 'ai-action', 'delete-node', 'edit-rule'])

const referencedParams = computed(() => {
  if (!props.node?.branches) return []
  const allConditions = props.node.branches.map((b: Record<string, unknown>) => b.condition || '').join(' ')
  return props.allParams.filter(p => allConditions.includes(p.name) || allConditions.includes(String(p.default_value ?? p.value ?? '')))
})

function emitUpdate() { emit('update', props.node) }

function addBranch() {
  if (!props.node) return
  props.node.branches.push({ condition: '', conclusion: '', action: '', next_step: null })
  emitUpdate()
}

function removeBranch(i: number) {
  if (!props.node) return
  props.node.branches.splice(i, 1)
  emitUpdate()
}

function openRuleDrawer(branchIndex: number) {
  if (!props.node) return
  emit('edit-rule', { nodeId: props.node.id, branchIndex })
}
</script>

<style scoped>
.ni { padding: 16px; height: 100%; overflow-y: auto; }
.ni-empty {
  height: 100%; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 8px; font-size: 12px; color: var(--ai-ink-4);
}

.ni-head { display: flex; align-items: center; gap: 8px; margin-bottom: 16px; }
.ni-title { font-size: 15px; font-weight: 700; color: var(--ai-ink-1); }

.ni-section { margin-bottom: 16px; }
.ni-label { font-size: 11px; font-weight: 600; color: var(--ai-ink-3); text-transform: uppercase; letter-spacing: .5px; margin-bottom: 8px; }

.ni-branch {
  display: flex; gap: 4px; align-items: center; margin-bottom: 4px;
  padding: 6px 8px; background: var(--ai-surface-2); border-radius: 6px;
}
.ni-branch-actions {
  display: flex; align-items: center; gap: 8px; margin-top: 8px;
}

.ni-param {
  display: flex; align-items: center; justify-content: space-between;
  padding: 6px 10px; border-radius: 6px; margin-bottom: 4px;
  background: var(--ai-accent-soft); cursor: pointer; transition: all .1s;
}
.ni-param:hover { background: var(--ai-accent-soft); }
.ni-param-name { font-size: 12px; font-weight: 500; color: var(--ai-accent-ink); font-family: var(--ai-font-mono); }
.ni-param-val { font-size: 12px; color: var(--ai-ink-2); font-family: var(--ai-font-mono); }

.ni-actions { display: flex; flex-direction: column; gap: 4px; }
.ni-action {
  display: flex; align-items: center;
  padding: 8px 12px; border: 1px solid var(--ai-border); border-radius: 6px;
  background: var(--ai-surface-2); color: var(--ai-ink-2); font-size: 12px;
  text-align: left; cursor: pointer; transition: all .12s;
}
.ni-action:hover { border-color: var(--ai-accent-ink); color: var(--ai-accent-ink); background: var(--ai-accent-soft); }
.ni-action-icon { margin-right: 6px; flex-shrink: 0; }

.ni-footer { margin-top: 24px; padding-top: 12px; border-top: 1px solid var(--ai-border); }
</style>
