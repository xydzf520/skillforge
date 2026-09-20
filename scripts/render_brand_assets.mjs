/** Rebuild platform, extension and standalone Bridge icons from one SVG. */
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
import path from 'node:path'

const require = createRequire(import.meta.url)
const sharp = require('sharp')
const root = fileURLToPath(new URL('../', import.meta.url))
const source = await readFile(path.join(root, 'web/public/brand/mark.svg'))
await writeFile(path.join(root, 'web/public/favicon.svg'), source)
const icons = [
  ['web/public/favicon-32.png', 32], ['web/public/apple-touch-icon.png', 180],
  ...[32, 64, 128, 256].map(n => [`bridge/icon_${n}.png`, n]),
  ...[16, 48, 128].map(n => [`chrome-extension/icon${n}.png`, n]),
]
for (const [file, size] of icons) {
  await sharp(source, { density: 384 }).resize(size, size).png().toFile(path.join(root, file))
}

// The downloadable Bridge must remain a self-contained file.
const bridgePath = path.join(root, 'bridge/skillforgebridge.py')
const bridge = await readFile(bridgePath, 'utf8')
const encoded = (await readFile(path.join(root, 'bridge/icon_128.png'))).toString('base64')
const declaration = `ICON_PNG_B64 = (\n${encoded.match(/.{1,76}/g).map(line => `    "${line}"`).join('\n')}\n)`
const pattern = /ICON_PNG_B64 = \([\s\S]*?\n\)/g
if ([...bridge.matchAll(pattern)].length !== 1) throw new Error('Expected one embedded Bridge icon')
await writeFile(bridgePath, bridge.replace(pattern, declaration))

// README banners remain editable SVG. They contain no remotely loaded assets.
await mkdir(path.join(root, 'docs/brand'), { recursive: true })
const mark = source.toString().match(/<rect[\s\S]*<\/svg>/)[0].replace('</svg>', '')
for (const [locale, line1, line2] of [
  ['zh', '企业 AI 工作台与技能协作平台', '让业务经验成为可复用的 AI 能力'],
  ['en', 'Enterprise AI workspace & skill collaboration', 'Turn business expertise into reusable AI capabilities'],
]) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="360" viewBox="0 0 1200 360" role="img" aria-labelledby="title desc">
<title id="title">SkillForge</title><desc id="desc">${line1.replaceAll('&', '&amp;')}. ${line2}</desc>
<rect width="1200" height="360" rx="24" fill="#F3F7F4"/>
<path d="M810 0V360M930 0V360M1050 0V360M780 60H1200M780 180H1200M780 300H1200" stroke="#DFE9E2"/>
<g transform="translate(58 58) scale(1.25)">${mark}</g>
<text x="162" y="116" font-family="system-ui, -apple-system, Segoe UI, sans-serif" font-size="52" font-weight="650" letter-spacing="-2.5" fill="#163D35">SkillForge</text>
<text x="60" y="206" font-family="system-ui, -apple-system, Segoe UI, sans-serif" font-size="${locale === 'zh' ? 29 : 26}" font-weight="550" fill="#203E36">${line1.replaceAll('&', '&amp;')}</text>
<text x="60" y="250" font-family="system-ui, -apple-system, Segoe UI, sans-serif" font-size="20" fill="#587168">${line2}</text>
<path d="M846 92H922L976 146V236H1118" fill="none" stroke="#C0D8CA" stroke-width="3"/>
<rect x="815" y="61" width="62" height="62" rx="16" fill="#DFECE3"/>
<path d="M835 80H858M835 90H850M835 100H858" stroke="#638B78" stroke-width="4" stroke-linecap="round"/>
<g transform="translate(927 131) scale(1.55)">${mark}</g>
<rect x="1087" y="205" width="62" height="62" rx="16" fill="#DFECE3"/>
<path d="M1105 236L1114 245L1132 226" fill="none" stroke="#638B78" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
</svg>\n`
  await writeFile(path.join(root, `docs/brand/readme-${locale}.svg`), svg)
}
console.log('Rebuilt 9 PNG icons, favicon, Bridge embedded icon and 2 README banners.')
