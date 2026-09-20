#!/usr/bin/env node
/** Local, read-only screenshot preview. Uses public E2E fixtures, never a backend. */
import http from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { dirname, resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import { enterpriseFixtures } from './screenshot_enterprise_fixtures.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(resolve(root, 'web/package.json'));
const ts = require('typescript');
const port = Number(process.env.SCREENSHOT_PORT || 54179);
const handlers = [];

// Reuse fixture definitions without importing Playwright or executing E2E tests.
async function fixture(file, entry, prefix = '/api/') {
  const input = await readFile(resolve(root, 'web/e2e', file), 'utf8');
  const source = input.split('test.describe(')[0];
  const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
  const printer = ts.createPrinter();
  const definitions = ast.statements.filter(s => !ts.isImportDeclaration(s));
  const code = definitions.map(s => printer.printNode(ts.EmitHint.Unspecified, s, ast)).join('\n');
  const compiled = ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const context = vm.createContext({ exports: {}, URL, console, setTimeout, clearTimeout, structuredClone });
  vm.runInContext(compiled + `\nexports.previewEntry = ${entry};`, context, { filename: file });
  return context.exports.previewEntry({
    addInitScript: async () => {},
    routeWebSocket: async () => {},
    route: async (pattern, handle) => {
      const escaped = String(pattern).split('**').map(s => s.split('*').map(p => p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('[^/]*')).join('.*');
      handlers.push({ regex: typeof pattern.test === 'function' ? pattern : new RegExp('^' + escaped + '$'), handle, prefix });
    },
  });
}
const state = await fixture('mock-api.ts', 'installMockApi');
state.user.name = '公开演示';
state.user.avatar_url = '';
// Current editor expects the positions keyed directly by step ID.
state.playbooks[0]._canvas_layout = { collect: { x: 80, y: 20 }, summarize: { x: 460, y: 260 } };
await fixture('hall-v3-screenshots.spec.ts', 'mockAll', '/api/hall/');
await fixture('hall-v3-capability.spec.ts', 'mockHallApi');
await fixture('learning-flow.spec.ts', 'installLearningFlowMocks', '/api/learning/');
await fixture('task-tree.spec.ts', 'installTaskTreeRoutes', '/api/task-tree');

async function fixtureResponse(pathname) {
  const url = new URL(pathname, `http://127.0.0.1:${port}`);
  const selected = handlers.filter(h => pathname.startsWith(h.prefix) && h.regex.test(url.href)).at(-1);
  let output;
  await selected.handle({ request: () => ({ url: () => url.href, method: () => 'GET', postData: () => null }),
    fulfill: async ({ body }) => { output = JSON.parse(body); } });
  return output;
}
const learningHome = {
  summary: await fixtureResponse('/api/learning/summary'),
  topology: await fixtureResponse('/api/learning/flow-topology'),
  automation: await fixtureResponse('/api/learning/automation-status'),
};
// The current pulse view has a richer public unit-test fixture than the older E2E suite.
const pulseSource = await readFile(resolve(root, 'web/src/__tests__/LearningFlow.test.ts'), 'utf8');
const pulseAst = ts.createSourceFile('pulse.ts', pulseSource, ts.ScriptTarget.Latest, true);
let pulseLiteral;
function findPulse(node) {
  if (!pulseLiteral && ts.isCallExpression(node) && node.expression.getText(pulseAst) === 'mocks.learningApi.pulse.mockResolvedValue') pulseLiteral = node.arguments[0];
  ts.forEachChild(node, findPulse);
}
findPulse(pulseAst);
const pulseCode = ts.transpileModule('exports.data = ' + pulseLiteral.getText(pulseAst), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const pulseContext = vm.createContext({ exports: {} });
vm.runInContext(pulseCode, pulseContext);
for (const [path, data] of Object.entries({ '/api/learning/home': learningHome, '/api/learning/pulse': pulseContext.exports.data, ...enterpriseFixtures(state) })) {
  handlers.push({ regex: new RegExp('^http://127\\.0\\.0\\.1:' + port + path + '(\\?.*)?$'), prefix: path,
    handle: route => route.fulfill({ body: JSON.stringify(typeof data === 'function' ? data(new URL(route.request().url())) : data) }) });
}

const banner = `<script>localStorage.setItem('sf-theme','light');</script><style>*,*::before,*::after{animation-duration:0s!important;transition-duration:0s!important}#screenshot-notice{position:fixed;bottom:0;left:0;right:0;height:30px;z-index:2147483647;display:flex;align-items:center;justify-content:center;gap:12px;background:#123d37;color:#fff;font:12px system-ui;letter-spacing:.3px;pointer-events:none}</style><div id="screenshot-notice">界面演示 · 合成示例数据 <span style="opacity:.72">Synthetic preview · No enterprise systems or models connected</span></div>`;
const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.json': 'application/json' };
const server = http.createServer(async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Content-Security-Policy', "connect-src 'self'; img-src 'self' data: blob:; frame-src 'self'");
  const url = new URL(req.url, `http://127.0.0.1:${port}`);
  try {
    // A static assessment response illustrates the authoring form; it never calls AI or writes data.
    if (req.method === 'POST' && url.pathname === '/api/skills/architect/assess') {
      req.resume();
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ tier: 'partial', skill_complexity: 'medium', confidence: 0.7,
        recommended_path: 'interview', covered_dimensions: ['目标', '触发时机', '输出', '人工确认'],
        missing_dimensions: ['质检标准', '数据权限'],
        user_hint: '合成示例：建议在采访中补齐质检评分标准与数据授权范围。',
        rationale: 'Static synthetic fixture; no model was called.' }));
      return;
    }
    if (!['GET', 'HEAD'].includes(req.method)) {
      res.writeHead(405, { 'Content-Type': 'application/json', Allow: 'GET, HEAD' });
      res.end(JSON.stringify({ detail: 'Synthetic screenshot preview is read-only.' }));
      return;
    }
    if (url.pathname.startsWith('/api/')) {
      const candidates = handlers.filter(h => url.pathname.startsWith(h.prefix) && h.regex.test(url.href)).reverse();
      let fulfilled = false;
      for (const h of candidates) {
        await h.handle({
          request: () => ({ url: () => url.href, method: () => req.method, postData: () => null }),
          fulfill: async ({ status = 200, contentType = 'application/json', body = '' }) => {
            res.writeHead(status, { 'Content-Type': contentType });
            res.end(body); fulfilled = true;
          },
          continue: async () => {}, fallback: async () => {},
        });
        if (fulfilled) return;
      }
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ detail: 'No synthetic fixture for this endpoint.' }));
      return;
    }
    const base = url.pathname.startsWith('/playbook-editor/') ? resolve(root, 'web/public') : resolve(root, 'web/dist');
    let path = resolve(base, '.' + decodeURIComponent(url.pathname));
    if (!path.startsWith(base + sep) && path !== base) { res.writeHead(403); res.end(); return; }
    try { if ((await stat(path)).isDirectory()) path = resolve(path, 'index.html'); }
    catch { path = resolve(base, 'index.html'); }
    let body = await readFile(path);
    if (path.endsWith('index.html')) body = Buffer.from(body.toString().replace('</body>', banner + '</body>'));
    res.writeHead(200, { 'Content-Type': mime[extname(path)] || 'application/octet-stream' });
    res.end(req.method === 'HEAD' ? undefined : body);
  } catch (error) {
    console.error('Preview fixture error:', url.pathname, error.message);
    if (!res.headersSent) res.writeHead(500, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ detail: 'Screenshot fixture unavailable; see local console.' }));
  }
});
server.listen(port, '127.0.0.1', () => console.log(`Read-only synthetic preview: http://127.0.0.1:${port}/hall?view=capability`));
