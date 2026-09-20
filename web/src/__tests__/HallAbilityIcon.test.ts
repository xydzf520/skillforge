import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import CapabilityCard from '@/components/hall/CapabilityCard.vue'

describe('Hall ability card icons', () => {
  it('Skill 资产卡片标题旁图标使用语义色，不再是灰色方块', () => {
    const wrapper = mount(CapabilityCard, {
      props: {
        favorite: false,
        cap: {
          category: '数据分析',
          skill_count_active: 1,
          skill_count_total: 2,
          top_departments: [],
          data_sources: [],
          sample_skills: [],
        },
      },
    })

    const icon = wrapper.find('.cap-icon')
    expect(icon.exists()).toBe(true)
    expect(icon.attributes('aria-hidden')).toBe('true')
    expect(icon.classes()).toContain('cap-icon--info')
  })

  it('直接能力卡片标题旁图标绑定语义 tone 且不使用中性灰底', () => {
    const hallHomePath = resolve(process.cwd(), 'src/pages/hall/HallHome.vue')
    const source = readFileSync(hallHomePath, 'utf8')
    const titleOccurrences = source.match(/<h3 class="direct-card-name"/g) || []
    const directIconBlock = source.match(/\.direct-icon \{[\s\S]*?\n\}/)?.[0] || ''

    expect(titleOccurrences).toHaveLength(1)
    expect(source).toContain(':class="`direct-icon--${hallCapabilityTone(cap)}`"')
    expect(directIconBlock).toContain('border-radius: 999px')
    expect(directIconBlock).not.toContain('var(--ai-surface-2)')
  })
})
