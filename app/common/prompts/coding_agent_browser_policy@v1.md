9. **浏览器数据采集 (你有 browser_* MCP 工具)**:
   SkillForge 有一个 Docker Chrome, 已注入各平台 cookies。可用 MCP 原生工具:
   - `browser_status` — 检查浏览器状态
   - `browser_list_platforms` — 列出已发现的平台/页面（看缓存表里有什么）
   - `browser_get_cached_apis` — 按 id / domain / page_path 读已缓存的 API 列表
   - `browser_fetch_json` — 直接在浏览器上下文 fetch API（带登录态）
   - `browser_capture_apis` — 现场抓包发现页面内部 API（慢，~12s）
   - `browser_extract` — 在页面执行 JS / 提取 DOM
   - `browser_explore` — 页面结构概览
   - `browser_screenshot` — 截图调试

   如果浏览器没运行, 提示用户去 数据源 > 浏览器连接 启动。

   ## 标准取数链路（**严格按顺序**）

   1. **第一步：查缓存** — 调 `browser_list_platforms(domain="<目标域名>")`
      - 如果有命中 → 调 `browser_get_cached_apis(id=<命中的 id>)` 拿完整 API 列表 → 跳到第 3 步
      - 如果为空 → 跳到第 2 步

   2. **第二步：缓存为空时不要自己 capture_apis！**
      - 优先告诉用户："此平台未发现 API，请在管理后台 数据源 > 平台 API 表 点击「刷新 API 表」一次"
      - 也可直接调 `POST /api/browser/discover` 触发（如果你能跑）
      - **不要**在用户没要求的情况下主动 `browser_capture_apis`，那需要 12 秒并且会污染 Chrome target
      - 实在缓存又拿不到，再用 `browser_capture_apis` 兜底，但要在响应里说清楚

   3. **第三步：从 cached_apis 里挑合适的 API**
      - 优先选 method=GET 且响应 keys 含 `data` / `result` / `metrics` 的
      - 用 `browser_fetch_json(url=...)` 验证一次，看真实 JSON 形状
      - 把 API URL 和响应 schema 写进 contract.json + main.py

   4. **第四步：写 main.py 时**
      - 调用 SkillForge 的 `/api/browser/fetch-json-local` HTTP 端点（cookie 自动带）
      - **不要**直接 `requests.get(目标平台 URL)` — 会因为没 cookie 401
      - 拿到 JSON 后做业务判断，不要用 DOM 抽取

   5. **登态检查（强烈建议在 main.py 加上）**：
      - 先调 `POST /api/browser/verify-login {"source_id": "platform-<平台>"}` 看 verify_status
      - 如果 `expired` 或 `unknown` → 直接告警退出，提示"cookie 失效，请重新推送"
      - **不要**硬试取数失败再说，会浪费 timeout 时间

   ## 核心原则

   - **缓存 > 抓包 > DOM**：永远优先 `browser_get_cached_apis`，其次 `browser_fetch_json`，最后才 `browser_capture_apis`，DOM 抽取只在结构化 API 全部不可用时用
   - **抓 API, 不抓 DOM**：DOM 一改版就挂；API 路径和响应 schema 稳定得多
   - **fetch 之前先看登态**：`verify_login` 比直接试 fetch 快 3-5 倍

   ## 反例（已被生产事故验证过的坑）

   - ❌ 不查缓存就 `browser_capture_apis` → 浪费 12 秒
   - ❌ 已经知道 API 路径还去 `browser_explore` 抓 DOM → 平台改版必挂
   - ❌ `browser_extract` 里硬猜 selector，不先 `browser_fetch_json` 验证 → 字段对不上
   - ❌ main.py 里直连 `https://sycm.taobao.com/...` 不走浏览器 → 没 cookie 401
   - ❌ 跳过 `verify_login`，直接试 fetch，失败后再排查 → 90% 的时间浪费在登态问题上
