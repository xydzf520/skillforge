// 注入到每个页面，清除自动化检测痕迹。
// 通过 Chrome DevTools Protocol 的 Page.addScriptToEvaluateOnNewDocument 注入。

Object.defineProperty(navigator, 'webdriver', { get: () => undefined })

// 隐藏 CDP 相关属性。
Reflect.deleteProperty(window, 'cdc_adoQpoasnfa76pfcZLmcfl_Array')
Reflect.deleteProperty(window, 'cdc_adoQpoasnfa76pfcZLmcfl_Promise')
Reflect.deleteProperty(window, 'cdc_adoQpoasnfa76pfcZLmcfl_Symbol')

Object.defineProperty(navigator, 'plugins', {
  get: () => [
    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
    { name: 'Native Client', filename: 'internal-nacl-plugin' },
  ],
})

Object.defineProperty(navigator, 'languages', {
  get: () => ['zh-CN', 'zh', 'en'],
})

Object.defineProperty(navigator, 'hardwareConcurrency', {
  get: () => 8,
})

const originalGetParameter = WebGLRenderingContext.prototype.getParameter
WebGLRenderingContext.prototype.getParameter = function (parameter) {
  if (parameter === 37445) return 'Intel Inc.'
  if (parameter === 37446) return 'Intel Iris OpenGL Engine'
  return originalGetParameter.apply(this, arguments)
}
