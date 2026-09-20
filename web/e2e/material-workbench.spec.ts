import { expect, test } from '@playwright/test'

const workbenchUrl = process.env.MATERIAL_WORKBENCH_URL || 'http://127.0.0.1:8765/'

test.describe('FDE 素材供给系统 4.0 静态项目包', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 960 })
    await page.goto(workbenchUrl)
    await expect(page.getByRole('button', { name: '生产', exact: true })).toBeVisible()
  })

  test('真实需求、原文生成和可用业务指标是生产页主路径', async ({ page }) => {
    await expect(page.getByRole('heading', { name: '生产', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: '素材需求单' })).toBeVisible()
    await expect(page.getByText('本周已选用', { exact: true })).toBeVisible()
    await expect(page.getByText('本周真实消耗', { exact: true })).toHaveCount(0)
    await expect(page.locator('.north-star-strip article')).toHaveCount(4)
    await expect(page.locator('#requirement')).toBeVisible()
    await expect(page.locator('#script')).toBeVisible()

    const directSubmit = page.locator('#submitButton')
    await expect(directSubmit).toBeDisabled()
    await page.locator('#requirement').fill('中国年轻成年情侣在真实客厅自然对话，动作松弛，不要字幕。')
    await page.locator('#script').fill('女：开了吗？\n男：开了。')
    await expect(directSubmit).toBeEnabled()
    await expect(page.locator('#compiledPrompt')).toHaveValue(/女：开了吗？/)
    await expect(page.locator('label:has(#productParticipation)')).toContainText('产品信息用途')
  })

  test('AI 策略和完整复刻保持为可选生产方式', async ({ page }) => {
    await page.getByRole('button', { name: /AI 策略/ }).click()
    await expect(page.getByRole('button', { name: '生成三个方向', exact: true })).toBeVisible()
    await expect(page.getByText(/A、B 台词逐字不变/)).toBeVisible()

    await page.getByRole('button', { name: /视频复刻/ }).click()
    await expect(page.getByText('H3 全时长结构复刻', { exact: true })).toBeVisible()
    await expect(page.getByText('精准局部替换', { exact: true })).toBeVisible()
    await expect(page.getByText(/复刻源视频支持 2–60 秒/)).toBeVisible()
    await expect(page.locator('#generateReplay')).toBeVisible()
    await expect(page.locator('#contentSection')).not.toBeVisible()
  })

  test('编导选片默认进入素材墙且不批量创建视频', async ({ page }) => {
    await page.getByRole('button', { name: /审片/ }).click()
    await expect(page.getByRole('heading', { name: '审片', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: /待我选片/ })).toBeVisible()
    await expect(page.getByRole('button', { name: /已产生消耗/ })).toBeVisible()
    await expect(page.locator('#reviewWallView')).toBeVisible()
    await expect(page.locator('#materialWall video')).toHaveCount(0)
    await expect(page.getByRole('button', { name: '批量选用' })).toHaveCount(0)
    await expect(page.locator('#reviewImmersive')).not.toBeVisible()
  })

  for (const width of [1600, 1280, 1024, 768]) {
    test(`素材墙在 ${width}px 视口不产生整体横向滚动`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await page.getByRole('button', { name: /审片/ }).click()
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
      expect(overflow).toBeLessThanOrEqual(1)
    })
  }
})
