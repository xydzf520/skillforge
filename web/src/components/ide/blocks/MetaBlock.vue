<template>
  <div class="block">
    <div class="block-title">基础信息</div>
    <a-form :model="form" layout="vertical" size="small">
      <a-row :gutter="12">
        <a-col :span="12">
          <a-form-item label="Skill ID" :disabled="!isCreate">
            <a-input v-model="form.skill_id" :disabled="!isCreate" :placeholder="skillIdPlaceholder" @input="emitUpdate" />
          </a-form-item>
        </a-col>
        <a-col :span="12">
          <a-form-item label="名称">
            <a-input v-model="form.name" placeholder="Skill 名称" @input="emitUpdate" />
          </a-form-item>
        </a-col>
        <a-col :span="12">
          <a-form-item label="部门">
            <a-input v-model="form.department" placeholder="所属部门" @input="emitUpdate" />
          </a-form-item>
        </a-col>
        <a-col :span="12">
          <a-form-item label="角色">
            <a-input v-model="form.role" placeholder="目标角色" @input="emitUpdate" />
          </a-form-item>
        </a-col>
        <a-col :span="8">
          <a-form-item label="触发方式">
            <a-select v-model="form.trigger_type" @change="emitUpdate">
              <a-option value="manual">手动</a-option>
              <a-option value="scheduler">定时</a-option>
              <a-option value="webhook">Webhook</a-option>
              <a-option value="event">事件</a-option>
            </a-select>
          </a-form-item>
        </a-col>
        <a-col :span="24">
          <a-form-item
            label="风险等级"
            :help="riskHelpText"
            :validate-status="form.risk_level ? '' : 'error'"
          >
            <!-- W4-B: radio 带人话说明，强制用户主动选择而非默认 R2 -->
            <a-radio-group v-model="form.risk_level" type="button" size="small" @change="emitUpdate">
              <a-radio value="R1">R1 低</a-radio>
              <a-radio value="R2">R2 中</a-radio>
              <a-radio value="R3">R3 高</a-radio>
              <a-radio value="R4">R4 极高</a-radio>
            </a-radio-group>
          </a-form-item>
        </a-col>
        <a-col :span="8">
          <a-form-item label="审批层级">
            <a-input-number v-model="form.approval_level" :min="0" :max="3" @change="emitUpdate" />
          </a-form-item>
        </a-col>
      </a-row>
    </a-form>
  </div>
</template>

<script setup lang="ts">
import { reactive, watch, computed } from 'vue'
import type { PropType } from 'vue'
import { getRiskTooltip } from '@/utils/skillStatus'
import { useUserStore } from '@/stores/user'

const props = defineProps({
  value: { type: Object as PropType<any>, default: () => ({}) },
  isCreate: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update'])

const form = reactive({
  // W4-B: risk_level default 空，强制用户主动选；后端也拒绝空
  skill_id: '', name: '', department: '', role: '',
  trigger_type: 'manual', risk_level: '', approval_level: 1,
})

// W4-B: 动态 help 文案，随 radio 切换显示"R2（中）：可编辑，发布需部门管理员审核"
const riskHelpText = computed(() => {
  if (!form.risk_level) return '请选择风险等级（必填），不同等级决定发布需要的审批权限'
  return getRiskTooltip(form.risk_level)
})

// v2.6.3: skill_id placeholder 跟当前用户部门走，而不是硬编码 "EC-xxx"
const userStore = useUserStore()
const skillIdPlaceholder = computed(() => {
  const dept = (userStore.department || '').trim()
  return dept ? `如 ${dept}-关键动作-01` : '如 部门-关键动作-01（建议 <部门>-<场景>-<序号>）'
})

watch(() => props.value, (v) => {
  if (v) Object.assign(form, v)
}, { immediate: true, deep: true })

function emitUpdate() {
  emit('update', { ...form })
}
</script>

<style scoped>
.block { padding: 4px 0; }
.block-title { font-size: 15px; font-weight: 700; color: var(--ai-ink-1); margin-bottom: 14px; }
</style>
