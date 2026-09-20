<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <a-button type="text" size="small" @click="$router.push('/skills/templates')">← 返回模板库</a-button>
        <h2 class="page-title">{{ detail?.display_name || detail?.name || route.params.id }}</h2>
        <div class="page-subtitle">{{ detail?.description || '从这个骨架 Fork 一个新 Skill，再补充部门数据和规则' }}</div>
      </div>
      <a-space>
        <a-input v-model="forkForm.skill_id" placeholder="新 Skill ID" class="fork-input-id" />
        <a-input v-model="forkForm.department" placeholder="部门" class="fork-input-dept" />
        <a-button type="primary" :loading="forking" @click="handleFork">Fork 创建</a-button>
      </a-space>
    </div>

    <a-row :gutter="16">
      <a-col :xs="24" :lg="8">
        <a-card class="page-list-card" title="骨架信息">
          <a-descriptions :column="1" size="small">
            <a-descriptions-item label="分类">{{ detail?.category || '通用' }}</a-descriptions-item>
            <a-descriptions-item label="部门">{{ detail?.department || '-' }}</a-descriptions-item>
            <a-descriptions-item label="步骤">{{ detail?.steps_count || 0 }}</a-descriptions-item>
            <a-descriptions-item label="测试">{{ detail?.test_cases_count || 0 }}</a-descriptions-item>
          </a-descriptions>
          <div class="file-list">
            <div v-for="file in detail?.files || []" :key="file" class="file-item">{{ file }}</div>
          </div>
        </a-card>
      </a-col>
      <a-col :xs="24" :lg="16">
        <a-card class="page-list-card" title="SKILL.md">
          <pre class="skill-md">{{ detail?.skill_md || '' }}</pre>
        </a-card>
      </a-col>
    </a-row>
  </div>
</template>

<script setup lang="ts">
import { Message } from '@arco-design/web-vue'
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { skillApi as rawSkillApi } from '@/api'

defineOptions({ name: 'SkillTemplateDetail' })

const route = useRoute()
const router = useRouter()
const skillApi: any = rawSkillApi
const detail = ref<any>(null)
const forking = ref(false)
const forkForm = reactive({ skill_id: '', department: '' })

async function loadDetail() {
  detail.value = await skillApi.getTemplate(String(route.params.id || ''))
  if (!forkForm.department) forkForm.department = detail.value?.department || ''
  if (!forkForm.skill_id) forkForm.skill_id = `fork-${String(route.params.id || '')}`
}

async function handleFork() {
  if (!forkForm.skill_id.trim()) return
  forking.value = true
  try {
    const res = await skillApi.forkTemplate(String(route.params.id || ''), {
      skill_id: forkForm.skill_id.trim(),
      department: forkForm.department.trim() || '通用',
    })
    Message.success('Fork 成功')
    router.push(`/skills/${res.skill_id}`)
  } catch (error: any) {
    Message.error(error?._message || 'Fork 失败')
  } finally {
    forking.value = false
  }
}

onMounted(loadDetail)
</script>

<style scoped>
.fork-input-id { width: 180px; }
.fork-input-dept { width: 120px; }
.skill-md { white-space: pre-wrap; word-break: break-word; font-size: var(--sf-text-caption); line-height: 1.6; overflow-x: auto; max-width: 100%; }
.file-list { margin-top: 16px; display: flex; flex-direction: column; gap: 6px; color: var(--ai-ink-3); font-size: var(--sf-text-caption); }
.file-item { padding: 6px 8px; background: var(--ai-surface-2); border-radius: 4px; }

@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
    align-items: flex-start;
  }
  .page-header :deep(.arco-space) {
    width: 100%;
    flex-wrap: wrap;
  }
  .fork-input-id,
  .fork-input-dept {
    width: 100%;
  }
}
</style>
