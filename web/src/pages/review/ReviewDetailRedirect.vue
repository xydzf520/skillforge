<template>
  <div class="page-container">
    <a-card class="page-section-card">
      <a-spin :loading="loading" style="width: 100%">
        <div class="redirect-box">
          <div class="redirect-title">正在跳转到 SkillStudio 审批视角…</div>
          <div v-if="error" class="redirect-error">{{ error }}</div>
          <a-space v-if="error">
            <a-button type="primary" size="small" @click="retryRedirect">重试</a-button>
            <a-button type="text" size="small" @click="$router.push(fallbackPath)">返回审核列表</a-button>
          </a-space>
          <a-button v-else-if="!loading" type="text" @click="$router.push(fallbackPath)">返回审核列表</a-button>
        </div>
      </a-spin>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { reviewApi as rawReviewApi } from '@/api'

const route: any = useRoute()
const router: any = useRouter()
const reviewApi: any = rawReviewApi
const loading = ref(true)
const error = ref('')
const fallbackPath = '/reviews'
let timeoutTimer: ReturnType<typeof setTimeout> | null = null

async function doRedirect() {
  loading.value = true
  error.value = ''
  timeoutTimer = setTimeout(() => {
    if (loading.value) {
      loading.value = false
      error.value = '跳转超时，请重试或返回审核列表'
    }
  }, 5000)
  try {
    const reviewId = String(route.params.id || '')
    const detail = await reviewApi.get(reviewId)
    if (!detail?.skill_id) {
      error.value = '未找到对应 Skill，无法跳转'
      return
    }
    if (String(detail.skill_id).startsWith('playbook:')) {
      const playbookName = String(detail.skill_id).slice('playbook:'.length)
      router.replace(`/playbook/${encodeURIComponent(playbookName)}?review_id=${reviewId}`)
      return
    }
    router.replace(`/skills/${encodeURIComponent(String(detail.skill_id))}?perspective=review&review_id=${reviewId}`)
  } catch (e: any) {
    error.value = e?._message || '跳转失败'
  } finally {
    loading.value = false
    if (timeoutTimer) { clearTimeout(timeoutTimer); timeoutTimer = null }
  }
}

function retryRedirect() {
  doRedirect()
}

onMounted(doRedirect)
onBeforeUnmount(() => {
  if (timeoutTimer) { clearTimeout(timeoutTimer); timeoutTimer = null }
})
</script>

<style scoped>
.redirect-box {
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: center;
  justify-content: center;
  min-height: 180px;
}
.redirect-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--ai-ink-1);
}
.redirect-error {
  font-size: 13px;
  color: var(--ai-bad);
}
</style>
