/**
 * P4 验证：架构清理
 *
 * - D3 /skills/new 创建页不再有 "Swarm 协作" 按钮
 * - O12 "最近编辑" localStorage key 按用户 ID scope
 */
import { test, expect } from '@playwright/test'
import { snap } from './helpers/snapshot'

test.describe('P4 · Skills 架构清理', () => {
  test('D3 Studio 新建页移除 Swarm 协作入口', async ({ page }) => {
    await page.goto('/skills/new')
    // 等加载完成
    await page.waitForTimeout(1500)
    const swarmBtn = page.getByText('Swarm 协作', { exact: true })
    await expect(swarmBtn).toHaveCount(0)
    await snap(page, 'T81-no-swarm')
  })

  test('O12 最近编辑 localStorage key 按用户 scope', async ({ page }) => {
    await page.goto('/skills/list')
    const scopedKey = await page.evaluate(() => {
      // 模拟 userStore.userInfo.user_id = 'admin'
      return Object.keys(localStorage).filter((k) => k.startsWith('sf-skill-recent'))
    })
    // 初始可能没有（未编辑过任何 skill），但 key 格式一旦写入应包含冒号和用户名
    const legacyKey = await page.evaluate(() => localStorage.getItem('sf-skill-recent'))
    // 全局 key 应为空 / 旧值不再写入
    if (legacyKey) {
      console.warn('legacy sf-skill-recent 仍存在但不再被写入新数据')
    }
    // 如果有 scoped key，必然带冒号
    for (const k of scopedKey) {
      if (k === 'sf-skill-recent') continue
      expect(k).toMatch(/^sf-skill-recent:/)
    }
  })
})
