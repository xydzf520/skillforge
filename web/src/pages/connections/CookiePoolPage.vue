<template>
  <div class="page-container page-wide cookie-pool-page">
    <div class="page-header">
      <div>
        <div class="page-kicker">管理后台 · 数据与服务 · Cookie 池</div>
        <h2 class="page-title">Cookie 池 · <span class="ck-platform-id">{{ platform }} / {{ shopId }}</span></h2>
        <p class="page-subtitle">按平台和店铺深链打开授权池详情</p>
      </div>
      <a-space>
        <router-link to="/admin/connections/health">
          <a-button>采集健康</a-button>
        </router-link>
        <router-link to="/admin/connections">
          <a-button>连接管理</a-button>
        </router-link>
      </a-space>
    </div>

    <CookiePoolDetail
      v-if="platform && shopId"
      :platform="platform"
      :shop-id="shopId"
      :summary="summary"
      @changed="loadSummary"
      @close="router.push('/admin/connections')"
    />
    <a-empty v-else description="缺少 platform 或 shop_id" />
  </div>
</template>

<style scoped>
.cookie-pool-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.cookie-pool-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
}
.cookie-pool-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.ck-platform-id {
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-3);
  font-weight: 500;
}
.cookie-pool-page :deep(.arco-btn) {
  height: 30px;
  border-radius: 6px;
  font-weight: 500;
  font-size: 12.5px;
}
</style>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { cookiePoolApi, type CookiePoolSummaryRow } from '@/api'
import CookiePoolDetail from './CookiePoolDetail.vue'

const route = useRoute()
const router = useRouter()
const summary = ref<CookiePoolSummaryRow | null>(null)

const platform = computed(() => String(route.params.platform || ''))
const shopId = computed(() => String(route.params.shop_id || ''))

async function loadSummary() {
  if (!platform.value || !shopId.value) {
    summary.value = null
    return
  }
  try {
    const res = await cookiePoolApi.summary({ platform: platform.value, shop_id: shopId.value, page_size: 1 })
    const items = Array.isArray(res) ? res : (res.items || [])
    summary.value = items[0] || null
  } catch {
    summary.value = null
  }
}

onMounted(loadSummary)
watch([platform, shopId], loadSummary)
</script>
