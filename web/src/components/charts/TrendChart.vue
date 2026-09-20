<template>
  <div class="trend-chart-wrap" data-testid="trend-chart">
    <v-chart :option="option" autoresize class="trend-chart" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { use } from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent, TitleComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'

use([LineChart, BarChart, PieChart, GridComponent, LegendComponent, TooltipComponent, TitleComponent, CanvasRenderer])

const props = defineProps<{
  labels: string[]
  values: number[]
  color?: string
  unit?: string
  kind?: 'line' | 'bar' | 'pie'
}>()

const DEFAULT_COLOR = 'rgb(22,93,255)'

function toRgba(color: string, alpha: number) {
  const normalized = color.trim()
  const normalizedAlpha = Math.max(0, Math.min(1, alpha))
  const rgbMatch = normalized.match(
    /^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,\s*(?:0|1|0?\.\d+))?\s*\)$/i,
  )
  if (rgbMatch) {
    const [, r, g, b] = rgbMatch
    return `rgba(${r},${g},${b},${normalizedAlpha})`
  }

  const hexMatch = normalized.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i)
  if (hexMatch) {
    const rawHex = hexMatch[1]
    const fullHex = rawHex.length === 3
      ? rawHex.split('').map((digit) => `${digit}${digit}`).join('')
      : rawHex
    const r = Number.parseInt(fullHex.slice(0, 2), 16)
    const g = Number.parseInt(fullHex.slice(2, 4), 16)
    const b = Number.parseInt(fullHex.slice(4, 6), 16)
    return `rgba(${r},${g},${b},${normalizedAlpha})`
  }

  return toRgba(DEFAULT_COLOR, normalizedAlpha)
}

const seriesColor = computed(() => props.color?.trim() || DEFAULT_COLOR)

const option = computed(() => {
  const kind = props.kind || 'line'
  const tooltip = {
    backgroundColor: 'rgba(22,32,51,0.96)',
    borderColor: 'transparent',
    textStyle: { color: '#fff', fontSize: 12 },
    valueFormatter: (v: number) => `${v}${props.unit || ''}`,
  }
  if (kind === 'pie') {
    return {
      tooltip: { ...tooltip, trigger: 'item' },
      legend: { bottom: 0, type: 'scroll' },
      series: [{
        type: 'pie',
        radius: ['42%', '68%'],
        center: ['50%', '44%'],
        data: props.labels.map((label, index) => ({ name: label, value: props.values[index] ?? 0 })),
      }],
    }
  }
  return {
    grid: { top: 16, bottom: 28, left: 40, right: 12 },
    xAxis: {
      type: 'category',
      boundaryGap: kind === 'bar',
      data: props.labels,
      axisLabel: { color: '#5b6476', fontSize: 11 },
      axisLine: { lineStyle: { color: 'rgba(22,32,51,0.12)' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#5b6476', fontSize: 11 },
      splitLine: { lineStyle: { color: 'rgba(22,32,51,0.06)' } },
    },
    tooltip: { ...tooltip, trigger: 'axis' },
    series: [
      {
        type: kind,
        data: props.values,
        smooth: kind === 'line',
        symbol: kind === 'line' ? 'circle' : undefined,
        symbolSize: kind === 'line' ? 6 : undefined,
        itemStyle: { color: seriesColor.value },
        lineStyle: kind === 'line' ? { width: 2, color: seriesColor.value } : undefined,
        areaStyle: kind === 'line'
          ? {
              color: {
                type: 'linear',
                x: 0, y: 0, x2: 0, y2: 1,
                colorStops: [
                  { offset: 0, color: toRgba(seriesColor.value, 0.18) },
                  { offset: 1, color: toRgba(seriesColor.value, 0) },
                ],
              },
            }
          : undefined,
      },
    ],
  }
})
</script>

<style scoped>
.trend-chart-wrap {
  width: 100%;
  height: 240px;
}
.trend-chart {
  width: 100%;
  height: 100%;
}
</style>
