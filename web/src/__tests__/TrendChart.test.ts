import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

vi.mock('vue-echarts', () => ({
  default: {
    name: 'VueEChartsStub',
    props: ['option'],
    template: '<div class="chart-stub" />',
  },
}))

import TrendChart from '@/components/charts/TrendChart.vue'

function renderChart(props?: { color?: string; unit?: string }) {
  const wrapper = mount(TrendChart, {
    props: {
      labels: ['04-18', '04-19'],
      values: [12, 18],
      ...props,
    },
  })

  return wrapper.getComponent({ name: 'VueEChartsStub' }).props('option') as any
}

describe('TrendChart', () => {
  it('uses a valid fallback rgba gradient when color is omitted', () => {
    const option = renderChart()
    const [startStop, endStop] = option.series[0].areaStyle.color.colorStops

    expect(option.series[0].itemStyle.color).toBe('rgb(22,93,255)')
    expect(startStop.color).toBe('rgba(22,93,255,0.18)')
    expect(endStop.color).toBe('rgba(22,93,255,0)')
    expect(startStop.color).not.toContain('rgbaa')
  })

  it('normalizes rgba input colors without generating invalid rgbaa output', () => {
    const option = renderChart({ color: 'rgba(12,34,56,1)' })
    const [startStop, endStop] = option.series[0].areaStyle.color.colorStops

    expect(option.series[0].itemStyle.color).toBe('rgba(12,34,56,1)')
    expect(startStop.color).toBe('rgba(12,34,56,0.18)')
    expect(endStop.color).toBe('rgba(12,34,56,0)')
  })
})
