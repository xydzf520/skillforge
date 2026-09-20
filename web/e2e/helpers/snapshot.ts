/**
 * Wave 5 e2e 截图 helper
 *
 * 所有截图统一写到 /home/skillforge/skillforge/screenshots/wave5/
 * 命名：T<NN>-<场景>.png
 */
import type { Page } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SCREENSHOT_DIR = path.resolve(HERE, '../../../screenshots/wave5')

/** 视口截图 */
export async function snap(page: Page, name: string): Promise<string> {
  const file = path.join(SCREENSHOT_DIR, `${name}.png`)
  await page.screenshot({ path: file })
  return file
}

/** 全页长截图（用于表单/列表） */
export async function snapFull(page: Page, name: string): Promise<string> {
  const file = path.join(SCREENSHOT_DIR, `${name}.png`)
  await page.screenshot({ path: file, fullPage: true })
  return file
}
