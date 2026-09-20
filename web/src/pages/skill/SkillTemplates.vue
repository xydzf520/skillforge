<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h2 class="page-title">从模板创建</h2>
        <div class="page-subtitle">选择标准 Skill 骨架，Fork 后补本部门数据源、阈值和业务规则。模板不代表已上线能力，只是创建起点。</div>
      </div>
    </div>

    <a-card class="page-list-card">
      <a-spin :loading="loading">
        <a-row v-if="templates.length" :gutter="[16, 16]">
          <a-col v-for="item in templates" :key="item.id" :xs="24" :sm="12" :lg="8">
            <a-card hoverable class="template-card" @click="$router.push(`/skills/templates/${item.id}`)">
              <div class="template-top">
                <a-tag size="small">{{ item.category || '通用' }}</a-tag>
                <span class="template-trigger">{{ item.trigger_type || 'manual' }}</span>
              </div>
              <div class="template-title">{{ item.display_name || item.name || item.id }}</div>
              <div class="template-desc">{{ item.description || '暂无说明' }}</div>
              <div class="template-meta">
                <span>{{ item.steps_count || 0 }} steps</span>
                <span>{{ item.test_cases_count || 0 }} tests</span>
              </div>
            </a-card>
          </a-col>
        </a-row>
        <SfEmptyState
          v-else
          title="模板中心"
          description="还没有模板"
          hint="模板是可复用的 Skill 骨架。后续可以从成熟 Skill 抽象出审批、预警、日报等标准模板。"
          action-label="去创建 Skill"
          @action="$router.push('/skills/new')"
        />
      </a-spin>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { skillApi as rawSkillApi } from '@/api'
import { SfEmptyState } from '@/components/common'

defineOptions({ name: 'SkillTemplates' })

const skillApi: any = rawSkillApi
const loading = ref(false)
const templates = ref<any[]>([])

async function loadTemplates() {
  loading.value = true
  try {
    const res = await skillApi.listTemplates()
    templates.value = Array.isArray(res?.items) ? res.items : []
  } finally {
    loading.value = false
  }
}

onMounted(loadTemplates)
</script>

<style scoped>
.template-card { border-radius: var(--sf-radius-md); }
.template-top { display: flex; justify-content: space-between; margin-bottom: 12px; color: var(--ai-ink-3); }
.template-title { font-size: var(--sf-text-h2); font-weight: 700; margin-bottom: 8px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.template-desc { color: var(--ai-ink-2); min-height: 42px; }
.template-meta { display: flex; gap: 12px; margin-top: 12px; color: var(--ai-ink-3); font-size: var(--sf-text-caption); }

@media (max-width: 480px) {
  .template-card {
    min-height: auto;
  }
  .template-top {
    margin-bottom: 8px;
  }
}
</style>
