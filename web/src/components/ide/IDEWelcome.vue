<template>
  <div class="welcome">
    <div class="welcome-glow" />
    <div class="welcome-icon">
      <icon-bulb :size="28" />
    </div>
    <h2 class="welcome-h">创建新 Skill</h2>
    <p class="welcome-p">选择一种方式开始</p>
    <div class="welcome-actions">
      <button class="action-card" @click="emit('startManual')">
        <icon-edit :size="22" />
        <div class="ac-title">手动创建</div>
        <div class="ac-desc">从基础信息开始，逐步填写各模块</div>
      </button>
      <button class="action-card primary" @click="emit('startChat')">
        <icon-robot :size="22" />
        <div class="ac-title">AI 对话生成</div>
        <div class="ac-desc">描述你的需求，AI 帮你生成完整骨架</div>
      </button>
      <button class="action-card" @click="showTemplates = !showTemplates">
        <icon-copy :size="22" />
        <div class="ac-title">从模板开始</div>
        <div class="ac-desc">基于现有 Skill 快速创建类似的新 Skill</div>
      </button>
    </div>

    <!-- 模板推荐区域 -->
    <div v-if="showTemplates" class="template-section">
      <div class="template-search">
        <a-input v-model="templateQuery" placeholder="输入关键词搜索模板..." size="small" @input="searchTemplates">
          <template #prefix><icon-search :size="14" /></template>
        </a-input>
      </div>
      <div v-if="loadingTemplates" class="template-loading">
        <a-spin :size="16" /> 搜索中...
      </div>
      <div v-else-if="templates.length" class="template-list">
        <div v-for="t in templates" :key="t.skill_id || t.id" class="template-item" @click="emit('applyTemplate', t)">
          <div class="tpl-top">
            <span class="tpl-name">{{ t.name || t.skill_id }}</span>
            <a-tag v-if="t.similarity" color="arcoblue" size="small">{{ Math.round(t.similarity * 100) }}% 相似</a-tag>
          </div>
          <div class="tpl-desc">{{ t.reason || t.description || '' }}</div>
          <div class="tpl-meta">
            <span>{{ t.department || '' }}</span>
            <span>{{ t.trigger_type || '' }}</span>
          </div>
        </div>
      </div>
      <div v-else class="template-empty">
        {{ templateQuery ? '未找到匹配模板' : '输入关键词开始搜索' }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { IconBulb, IconEdit, IconRobot, IconCopy, IconSearch } from '@arco-design/web-vue/es/icon'
import { skillApi as rawSkillApi } from '@/api'

const emit = defineEmits(['startManual', 'startChat', 'applyTemplate'])
const skillApi: any = rawSkillApi

const showTemplates = ref(false)
const templateQuery = ref('')
const templates = ref<any[]>([])
const loadingTemplates = ref(false)
let searchTimer: ReturnType<typeof setTimeout> | null = null

function searchTemplates() {
  clearTimeout(searchTimer)
  const q = templateQuery.value.trim()
  if (!q) { templates.value = []; return }
  searchTimer = setTimeout(async () => {
    loadingTemplates.value = true
    try {
      const r = await skillApi.recommendTemplate({ name: q, department: '', purpose: q })
      templates.value = r.recommendations || r.templates || r || []
    } catch { templates.value = [] }
    finally { loadingTemplates.value = false }
  }, 500)
}
</script>

<style scoped>
.welcome {
  display: flex; flex-direction: column; align-items: center;
  padding: 60px 24px; text-align: center; position: relative; height: 100%; overflow-y: auto;
}
.welcome-glow {
  position: absolute; top: 20px; left: 50%; transform: translateX(-50%);
  width: 240px; height: 240px; border-radius: 50%;
  background: radial-gradient(circle, rgba(var(--primary-6), 0.06), transparent 70%);
  pointer-events: none;
}
.welcome-icon {
  width: 56px; height: 56px; border-radius: 16px; margin-bottom: 20px;
  background: var(--ai-accent-ink);
  color: #fff; display: flex; align-items: center; justify-content: center;
  box-shadow: 0 8px 24px rgba(var(--primary-6), .15); position: relative;
}
.welcome-h { font-size: 20px; font-weight: 700; color: var(--ai-ink-1); margin: 0 0 6px; }
.welcome-p { font-size: 14px; color: var(--ai-ink-3); margin: 0 0 32px; }

.welcome-actions { display: flex; gap: 16px; flex-wrap: wrap; justify-content: center; }
.action-card {
  width: 200px; padding: 24px 20px; border-radius: 12px;
  border: 1px solid var(--ai-border); background: var(--ai-surface-2);
  cursor: pointer; text-align: center; transition: all .15s;
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  color: var(--ai-ink-2);
}
.action-card:hover { border-color: var(--ai-accent-ink); transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,.06); }
.action-card.primary {
  border: 1px solid transparent;
  background: var(--sf-gradient-ai-soft);
  color: #8b5cf6;
  position: relative;
}
.action-card.primary::before {
  content: '';
  position: absolute; inset: -1px; border-radius: 13px;
  background: var(--sf-gradient-ai-border);
  z-index: -1; mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0); mask-composite: exclude; padding: 1px;
}
.action-card.primary:hover { box-shadow: 0 8px 24px rgba(139, 92, 246, .15); transform: translateY(-2px); }
.action-card.primary .ac-title { background: var(--sf-gradient-ai); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.ac-title { font-size: 14px; font-weight: 600; }
.ac-desc { font-size: 12px; color: var(--ai-ink-3); line-height: 1.5; }

/* 模板推荐 */
.template-section { width: 100%; max-width: 640px; margin-top: 24px; text-align: left; }
.template-search { margin-bottom: 12px; }
.template-loading { display: flex; align-items: center; gap: 8px; color: var(--ai-ink-4); font-size: 12px; padding: 16px 0; justify-content: center; }
.template-empty { text-align: center; color: var(--ai-ink-4); font-size: 12px; padding: 20px 0; }
.template-list { display: flex; flex-direction: column; gap: 8px; }
.template-item {
  padding: 12px 14px; border: 1px solid var(--ai-border); border-radius: 8px;
  cursor: pointer; transition: all .12s; background: var(--ai-surface-2);
}
.template-item:hover { border-color: var(--ai-accent-ink); box-shadow: 0 2px 8px rgba(0,0,0,.04); }
.tpl-top { display: flex; align-items: center; justify-content: space-between; }
.tpl-name { font-size: 13px; font-weight: 600; color: var(--ai-ink-1); }
.tpl-desc { font-size: 12px; color: var(--ai-ink-3); margin-top: 4px; line-height: 1.4; }
.tpl-meta { display: flex; gap: 10px; font-size: 11px; color: var(--ai-ink-4); margin-top: 6px; }
</style>
