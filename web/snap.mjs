import { chromium } from 'playwright'

const url = process.argv[2] || 'http://localhost:3000/'
const out = process.argv[3] || '/tmp/snap.png'
const width = Number(process.argv[4] || 1440)
const height = Number(process.argv[5] || 900)

const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({ viewport: { width, height } })
const page = await ctx.newPage()
try {
  await page.goto(url, { waitUntil: 'networkidle', timeout: 15000 })
} catch (e) {
  console.error('goto failed:', e.message)
}
await page.waitForTimeout(800)
await page.screenshot({ path: out, fullPage: false })
console.log('saved', out, 'title=', await page.title())
await browser.close()
