# 产品截图 / Product screenshots

2026-09-20 从公开版实际 Vue / React 前端截取。原图为 1280 × 720 JPEG；没有用设计稿、图片生成或图片编辑替代产品界面。每张图片底部均标记“合成示例数据”。

Captured from the actual public-edition Vue / React frontend on 2026-09-20. The original JPEGs are 1280 × 720; no generated mockups or post-capture image edits were used. Every image carries a synthetic-data notice.

| 图片 / Image | 页面 / Page | 展示范围 / Scope |
|---|---|---|
| [能力大厅](capability-hall.jpg) | `/hall` | 部门、业务场景与能力入口 / Discovery and launch points |
| [技能建设](skill-authoring.jpg) | `/skills/new` | 业务需求、输出约定与采访路径 / Requirements and authoring paths |
| [流程画布](workflow-canvas.jpg) | `/playbook-editor/index.html?name=replenishment-check&readonly=1` | 依赖、超时、失败策略 / Dependencies, timeouts and failure policies |
| [数据与训练流转](learning-flow.jpg) | `/learning-flow` | 原始记录、清洗、训练、评估、部署 / Records through deployment |
| [节点管理](node-operations.jpg) | `/task-tree?anomaly=0&inst=inst-rd-1&tab=schedules&run=run-rd-active` | 节点、调度、版本、执行结果 / Nodes, schedules, versions and results |

## 数据来源与边界 / Data and scope

- 页面由生产构建资源渲染；数据来自仓库中已有的公开 E2E / 单元测试夹具。技能需求文案为人工编写的客服质检示例。
- `serve_screenshot_preview.mjs` 只监听本机回环地址，提供合成 API 响应，不连接真实后端、企业系统、模型或运行节点。
- 技能需求评估请求仅返回固定的合成示例；其他写入请求均返回 405。没有执行技能生成、训练、审核发布或定时任务。
- 数字、日期、成功率、模型状态及建议置信度只用于展示界面。不同页面的测试夹具是独立示例，不是同一批任务的端到端证据。
- 图片不包含账号凭据、公司数据或私有部署地址。公开版限制仍以 README 的版本范围和发布复核记录为准。

The production frontend renders public test fixtures. The preview binds to loopback only and does not connect to a real backend, enterprise system, model or execution node. Requirement assessment returns a fixed synthetic response; other write requests return 405. Dates, metrics, states and confidence values are illustrative. Fixtures on different pages are independent examples, not end-to-end evidence. No credentials, company records or private deployment addresses are included.

## 本地复现 / Reproduce locally

在仓库根目录安装依赖并构建（Node 22）：

```bash
npm ci --prefix web
npm ci --prefix playbook-editor
npm run build --prefix playbook-editor
npm run build --prefix web
node scripts/serve_screenshot_preview.mjs
```

打开终端显示的本机地址，再进入上表页面。技能建设页填写合成业务需求即可；不点击生成、运行、发布等操作。节点管理页选择示例节点的“定时任务”。演示仅覆盖上表页面，不是完整产品后端。

Run the commands from the repository root, then open the loopback URL printed by the server and visit the listed routes. Enter synthetic business requirements on the authoring page. Do not invoke generation, execution or publication. This preview covers the listed screens only; it is not a replacement backend. Stop it with Ctrl+C.
