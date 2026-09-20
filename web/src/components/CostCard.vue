<template>
  <div class="sf-cost-card">
    <a-row :gutter="16">
      <a-col :span="6">
        <a-card class="stat-card">
          <a-statistic
            title="今日 AI 调用"
            :value="todayCalls"
            :value-style="{ color: 'rgb(var(--blue-6))' }"
          />
          <template #extra>
            <icon-thunderbolt :size="14" />
          </template>
        </a-card>
      </a-col>
      <a-col :span="6">
        <a-card class="stat-card">
          <a-statistic
            title="今日成本"
            :value="todayCostUSD"
            :precision="4"
            prefix="$"
            :value-style="{ color: 'var(--ai-warn)' }"
          />
        </a-card>
      </a-col>
      <a-col :span="6">
        <a-card class="stat-card">
          <a-statistic
            :title="`近 ${rangeDays} 天总成本`"
            :value="rangeCostUSD"
            :precision="4"
            prefix="$"
          />
        </a-card>
      </a-col>
      <a-col :span="6">
        <a-card class="stat-card">
          <a-statistic
            :title="`近 ${rangeDays} 天 token`"
            :value="rangeTokensK"
            suffix="k"
            :precision="1"
          />
        </a-card>
      </a-col>
    </a-row>

    <a-row :gutter="16" style="margin-top: 16px">
      <!-- Top Skills -->
      <a-col :span="12">
        <a-card title="近 7 天成本 Top Skill" :bordered="true">
          <a-empty v-if="!topSkills.length" description="暂无成本记录" />
          <a-table
            v-else
            :data="topSkills"
            :pagination="false"
            size="small"
          >
            <template #columns>
              <a-table-column title="Skill ID" data-index="skill_id" />
              <a-table-column title="调用数" data-index="calls" :width="80" />
              <a-table-column title="成本 (USD)" :width="120">
                <template #cell="{ record }">
                  ${{ Number(record.cost_usd).toFixed(4) }}
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-card>
      </a-col>

      <!-- 按模型分布 -->
      <a-col :span="12">
        <a-card :title="`近 ${rangeDays} 天按模型成本`" :bordered="true">
          <a-empty v-if="!byModel.length" description="暂无成本记录" />
          <a-table
            v-else
            :data="byModel"
            :pagination="false"
            size="small"
          >
            <template #columns>
              <a-table-column title="模型" data-index="model" />
              <a-table-column title="调用数" data-index="calls" :width="80" />
              <a-table-column title="成本 (USD)" :width="120">
                <template #cell="{ record }">
                  ${{ Number(record.cost_usd).toFixed(4) }}
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-card>
      </a-col>
    </a-row>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconThunderbolt } from '@arco-design/web-vue/es/icon'
import { dashboardApi as rawDashboardApi } from '@/api'

const dashboardApi: any = rawDashboardApi
const props = defineProps({
  rangeDays: { type: Number, default: 30 },
})

const today = ref<any>({ total_calls: 0, total_cost_usd: 0 })
const summary = ref<any>({
  total_calls: 0, total_cost_usd: 0,
  total_input_tokens: 0, total_output_tokens: 0,
  by_model: [], by_source: [],
})
const topSkills = ref<any[]>([])

const todayCalls = computed(() => today.value.total_calls || 0)
const todayCostUSD = computed(() => Number(today.value.total_cost_usd || 0))
const rangeCostUSD = computed(() => Number(summary.value.total_cost_usd || 0))
const rangeTokensK = computed(() => {
  const total = (summary.value.total_input_tokens || 0) + (summary.value.total_output_tokens || 0)
  return total / 1000
})
const byModel = computed(() => summary.value.by_model || [])

async function load() {
  try {
    const [t, s, ts] = await Promise.all([
      dashboardApi.costsToday(),
      dashboardApi.costsSummary(props.rangeDays),
      dashboardApi.costsTopSkills(10, 7),
    ])
    today.value = t || {}
    summary.value = s || {}
    topSkills.value = ts?.items || []
  } catch (e: any) {
    // 非 admin 用户会被 403 拦截，静默不显示
    if (e?.status !== 403) {
      console.warn('[CostCard] 加载失败:', e)
    }
  }
}

defineExpose({ refresh: load })
onMounted(load)
</script>

<style scoped>
.sf-cost-card {
  margin-top: 16px;
}
.stat-card {
  height: 100%;
}
</style>
