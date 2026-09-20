const path = require('path');
const { mkdirSync } = require('fs');

const puppeteer = require(path.join(__dirname, '..', '..', 'web', 'node_modules', 'puppeteer'));

const BASE = process.env.SMOKE_BASE_URL || 'http://127.0.0.1:18000';
const USERNAME = process.env.SMOKE_ADMIN_USERNAME || 'smoke_admin';
const PASSWORD = process.env.SMOKE_ADMIN_PASSWORD || 'TestPwd2026!';
const CARD_ID = process.env.SMOKE_INBOX_CARD_ID || '';
const TODO_ID = process.env.SMOKE_INBOX_TODO_ID || '';
const REPORT_TITLE = process.env.SMOKE_INBOX_REPORT_TITLE || 'Smoke Inbox Report';
const TODO_TITLE = process.env.SMOKE_INBOX_TODO_TITLE || 'Smoke Inbox Todo';
const EXECUTABLE = process.env.SMOKE_BROWSER_EXECUTABLE || '/usr/bin/chromium-browser';
const DIR = path.join(__dirname, '..', '..', 'screenshots', 'smoke-inbox-browser');

mkdirSync(DIR, { recursive: true });

let stepNo = 0;

async function shot(page, label) {
  stepNo += 1;
  await page.screenshot({
    path: path.join(DIR, `${String(stepNo).padStart(2, '0')}-${label}.png`),
    fullPage: true,
  });
}

async function waitForText(page, text, timeout = 15000) {
  await page.waitForFunction(
    (expected) => document.body?.innerText?.includes(expected),
    { timeout },
    text,
  );
}

async function waitForUrl(page, expected, timeout = 15000) {
  await page.waitForFunction(
    (fragment) => window.location.href.includes(fragment),
    { timeout },
    expected,
  );
}

async function main() {
  const browser = await puppeteer.launch({
    headless: true,
    executablePath: EXECUTABLE,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
    defaultViewport: { width: 1440, height: 960, deviceScaleFactor: 1.5 },
  });

  const page = await browser.newPage();
  page.setDefaultTimeout(15000);

  try {
    await page.goto(`${BASE}/login`, { waitUntil: 'networkidle2' });
    await waitForText(page, '登录 SkillForge');
    await shot(page, 'login');

    await page.type('input[type="text"]', USERNAME);
    await page.type('input[type="password"]', PASSWORD);
    await Promise.all([
      page.click('button.arco-btn-primary'),
      page.waitForFunction(() => !window.location.pathname.startsWith('/login'), { timeout: 15000 }),
    ]);
    await shot(page, 'after-login');

    await page.goto(`${BASE}/inbox`, { waitUntil: 'networkidle2' });
    await waitForText(page, '收件中心');
    await waitForText(page, '待办');
    await shot(page, 'inbox-home');

    await page.goto(`${BASE}/inbox?tab=reports`, { waitUntil: 'networkidle2' });
    await waitForText(page, REPORT_TITLE);
    await shot(page, 'inbox-reports');

    await page.goto(`${BASE}/inbox/reports/${encodeURIComponent(CARD_ID)}`, { waitUntil: 'networkidle2' });
    await waitForText(page, REPORT_TITLE);
    await waitForText(page, '调试上下文');
    await shot(page, 'report-detail');

    await page.goto(`${BASE}/todos/${encodeURIComponent(TODO_ID)}`, { waitUntil: 'networkidle2' });
    await waitForUrl(page, `/inbox/todos/${TODO_ID}`);
    await waitForText(page, TODO_TITLE);
    await shot(page, 'todo-detail-redirect');

    await page.goto(`${BASE}/todos/dispatch/42`, { waitUntil: 'networkidle2' });
    await waitForUrl(page, '/inbox/dispatch');
    await waitForText(page, '已定位到旧链接中的任务 #42');
    await shot(page, 'dispatch-redirect');

    console.log('browser smoke passed');
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
