<template>
  <div class="block">
    <div class="block-header">
      <div class="block-title">决策规则</div>
      <a-button size="mini" type="outline" :disabled="disabled" @click="addStep">+ 添加步骤</a-button>
    </div>

    <div v-if="!localSteps.length" class="block-empty">暂无决策规则</div>

    <a-collapse v-else :default-active-key="localSteps.map((_, i) => String(i))">
      <a-collapse-item v-for="(step, si) in localSteps" :key="String(si)">
        <template #header>
          <span class="step-title">{{ step.id || `step_${si + 1}` }} - {{ step.name || '未命名步骤' }}</span>
        </template>
        <template #extra>
          <a-button size="mini" type="text" status="danger" :disabled="disabled" @click.stop="removeStep(si)">删除</a-button>
        </template>

        <div class="step-body">
          <div class="step-row">
            <div class="step-field">
              <div class="step-label">步骤 ID</div>
              <a-input v-model="step.id" size="mini" :disabled="disabled" placeholder="step_1" @input="emitUpdate" />
            </div>
            <div class="step-field">
              <div class="step-label">步骤名称</div>
              <a-input v-model="step.name" size="mini" :disabled="disabled" placeholder="步骤名称" @input="emitUpdate" />
            </div>
          </div>

          <div class="step-field">
            <div class="step-label">描述</div>
            <a-textarea
              v-model="step.description"
              :auto-size="{ minRows: 2, maxRows: 6 }"
              :disabled="disabled"
              placeholder="步骤描述"
              @input="emitUpdate"
            />
          </div>

          <div class="branches-header">
            <span class="step-label" style="margin-bottom:0">分支</span>
            <a-button size="mini" type="text" :disabled="disabled" @click="addBranch(si)">+ 添加分支</a-button>
          </div>

          <div v-if="!step.branches.length" class="branch-empty">暂无分支</div>

          <div v-for="(br, bi) in step.branches" :key="bi" class="branch-card">
            <div class="branch-head">
              <span class="branch-index">分支 {{ bi + 1 }}</span>
              <a-button size="mini" type="text" status="danger" :disabled="disabled" @click="removeBranch(si, bi)">删除</a-button>
            </div>
            <div class="branch-grid">
              <div class="branch-field">
                <div class="branch-label">条件 (condition)</div>
                <a-input v-model="br.condition" size="mini" :disabled="disabled" placeholder="判断条件" @input="emitUpdate" />
              </div>
              <div class="branch-field">
                <div class="branch-label">结论 (conclusion)</div>
                <a-input v-model="br.conclusion" size="mini" :disabled="disabled" placeholder="结论" @input="emitUpdate" />
              </div>
              <div class="branch-field">
                <div class="branch-label">动作 (action)</div>
                <a-input v-model="br.action" size="mini" :disabled="disabled" placeholder="执行动作" @input="emitUpdate" />
              </div>
              <div class="branch-field">
                <div class="branch-label">下一步 (next_step)</div>
                <a-input v-model="br.next_step" size="mini" :disabled="disabled" placeholder="step_2 或留空" @input="emitUpdate" />
              </div>
            </div>
          </div>
        </div>
      </a-collapse-item>
    </a-collapse>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import type { PropType } from 'vue'
import type { SkillRuleStep } from '@/types/skill'

interface LocalBranch {
  condition: string
  conclusion: string
  action: string
  next_step: string
}

interface LocalStep {
  id: string
  name: string
  description: string
  branches: LocalBranch[]
}

const props = defineProps({
  value: { type: Array as PropType<SkillRuleStep[]>, default: () => [] },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update'])

const localSteps = ref<LocalStep[]>([])

watch(() => props.value, (v) => {
  localSteps.value = (v || []).map(s => ({
    id: s.id || '',
    name: s.name || '',
    description: s.description || '',
    branches: (s.branches || []).map(b => ({
      condition: b.condition || '',
      conclusion: b.conclusion || '',
      action: b.action || '',
      next_step: b.next_step || '',
    })),
  }))
}, { immediate: true, deep: true })

function emitUpdate() {
  emit('update', localSteps.value.map(s => ({
    id: s.id,
    name: s.name,
    description: s.description,
    branches: s.branches.map(b => ({
      condition: b.condition,
      conclusion: b.conclusion,
      action: b.action,
      next_step: b.next_step || null,
    })),
  })))
}

function addStep() {
  const idx = localSteps.value.length + 1
  localSteps.value.push({ id: `step_${idx}`, name: '', description: '', branches: [] })
  emitUpdate()
}

function removeStep(i: number) {
  localSteps.value.splice(i, 1)
  emitUpdate()
}

function addBranch(si: number) {
  localSteps.value[si].branches.push({ condition: '', conclusion: '', action: '', next_step: '' })
  emitUpdate()
}

function removeBranch(si: number, bi: number) {
  localSteps.value[si].branches.splice(bi, 1)
  emitUpdate()
}
</script>

<style scoped>
.block { padding: 4px 0; }
.block-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.block-title { font-size: 15px; font-weight: 700; color: var(--ai-ink-1); }
.block-empty { color: var(--ai-ink-4); font-size: 13px; padding: 20px 0; text-align: center; }

.step-title { font-size: 13px; font-weight: 600; color: var(--ai-ink-1); }
.step-body { padding: 4px 0; }
.step-row { display: flex; gap: 10px; margin-bottom: 8px; }
.step-row .step-field { flex: 1; }
.step-field { margin-bottom: 8px; }
.step-label { font-size: 11px; font-weight: 600; color: var(--ai-ink-4); margin-bottom: 4px; }

.branches-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.branch-empty { color: var(--ai-ink-4); font-size: 12px; padding: 8px 0; text-align: center; }

.branch-card {
  border: 1px solid var(--ai-border); border-radius: 8px;
  padding: 10px 12px; margin-bottom: 8px; background: var(--ai-surface-2);
}
.branch-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.branch-index { font-size: 12px; font-weight: 600; color: var(--ai-ink-3); }
.branch-label { font-size: 11px; font-weight: 600; color: var(--ai-ink-4); margin-bottom: 4px; }
.branch-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.branch-field { min-width: 0; }
</style>
