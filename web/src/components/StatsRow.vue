<template>
  <a-row :gutter="16" style="margin-bottom: 16px">
    <a-col v-for="(item, i) in items" :key="i" :span="Math.floor(24 / items.length)">
      <a-card class="stat-card">
        <a-statistic
          :title="item.title"
          :value="typeof item.value === 'number' ? item.value : Number(item.value)"
          :suffix="item.suffix"
          :value-style="item.color ? { color: item.color } : undefined"
        />
      </a-card>
    </a-col>
  </a-row>
</template>

<script setup lang="ts">
import type { PropType } from 'vue'
/**
 * 统计卡片行组件。
 * 用法：<StatsRow :items="[{ title: '总数', value: 100 }, { title: '成功', value: 80, color: 'green' }]" />
 */
defineProps({
  items: { type: Array as PropType<Array<{ title: string; value: number | string; suffix?: string; color?: string }>>, required: true },
})
</script>

<style scoped>
.stat-card { text-align: center; }
</style>
