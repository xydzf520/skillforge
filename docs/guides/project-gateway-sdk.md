# 平台项目 Project Gateway SDK

> 用于网页项目接入平台后记录运行与能力调用。本文包含可配置能力及历史兼容接口；使用前核对项目授权、模型、数据记录范围和节点配置。

## 网关职责与分布式协作

产品定位是**统一 AI 能力入口与分布式执行协作**。Project Gateway 负责业务应用的授权调用、输入输出和执行关联；任务队列、调度器与 Bridge 负责领取、下发及节点运行；模型封装与 MCP 通道接入具体能力。这些模块共同实现统筹调用，不是由单个 HTTP 转发接口承担全部职责。

### 复用企业闲置算力

网关与节点体系的重要用途，是把公司已授权接入的服务器、GPU 工作站等资源用于模型推理、小模型微调和生图等任务。企业可在兼容环境中复用现有设备与服务，减少为每个应用单独部署以及等待单台机器的情况。加速和成本节省是待测量的目标，需要把排队、模型加载、数据传输、执行、人工复核与运维一起计算。

| 工作负载 | 已核对的实现 | 需要分别验证的部分 |
|---|---|---|
| 资源观测与训练选择 | [训练服务](../../app/training/service.py)的 `_training_resource_from_instance` 汇总在线、GPU 利用率、闲置 GPU 数与可用显存；部分训练流程按角色、固定目标优先级和资源余量选节点 | 闲置指标来自节点报告和规则判断，不能据此自动抢占设备；不同任务路径未共用一个通用资源调度器 |
| 模型推理 | [项目服务](../../app/projects/service.py)的模型目录与推理通道；训练服务中的推理部署目标选择 | 运行器、模型、显存、节点授权；部分兼容路径保留固定目标约束 |
| 小模型微调 | 训练资源检查 LoRA / QLoRA 等能力；[Bridge](../../bridge/skillforgebridge.py)含训练操作及本地运行器，回收日志、指标和产物 | 合格数据、训练环境、与原模型对照、审批部署及回滚；支持某种任务不等于任意模型均可运行 |
| 生图与素材任务 | [直接能力服务](../../app/hall/direct_capability_service.py)含生图调用和产物记录；[媒体服务](../../app/media/service.py)的部分派发路径检查节点能力、可用显存、忙碌状态和队列 | 生图服务和媒体节点是不同路径；自有 GPU 生图需对应运行器与接入验收。不能把媒体视频任务调度或外部生图 API 结果当作自有 GPU 生图已打通 |

建议的接入顺序：确认资源负责人和可用范围 → 注册兼容节点并配置运行器 → 核对能力与资源报告 → 执行一项低风险任务 → 检查产物、总耗时与对原有工作的影响。可用时段、抢占和优先级应按目标任务明确配置或补充实现，不能假定平台已统一支持。

### 授权、任务与运行记录

| 机制 | 代码依据 | 能说明什么 |
|---|---|---|
| 项目身份、权限范围和模型配置 | [项目服务](../../app/projects/service.py)、[AI 调用封装](../../app/common/ai.py) | 不同业务应用共用能力入口，上游凭据留在服务端；模型选择以配置和已接入通道为准 |
| 多 Worker 任务领取 | [任务队列](../../app/execution/task_queue.py)、[Worker 注册](../../app/execution/worker_service.py) | 通过 PostgreSQL `FOR UPDATE SKIP LOCKED` 原子领取，支持优先级、失败重试、超时回收及领取者校验 |
| 节点定时和平台补偿 | [调度器](../../app/execution/scheduler.py)、[Bridge 注册](../../app/aiclaw/bridge_registry.py) | 优先由节点执行定时，平台检测漏跑并补偿；分布式锁约束重复调度，连接 epoch 区分新旧会话 |
| 跨进程调用限额 | [Redis 限流器](../../app/common/rate_limiter.py) | Redis Lua 原子更新滑动窗口，在共享 Redis 的进程间协调配额 |
| 请求去重和过程追溯 | [项目服务](../../app/projects/service.py)、[项目模型](../../app/projects/models.py) | 请求 ID、事务锁与运行记录关联输入输出、能力调用、报告及待办；用量记录支持归因 |
| 执行数据进入改进流程 | 本文“数据记录与训练出口”及训练资产 API | 部分路径形成样本候选、数据集版本与同步任务；业务审核、训练和效果评估仍需单独完成 |

当前边界需要与产品介绍一起阅读：

- Bridge 的连接注册、待返回请求和部分状态保存在进程内，尚不能据此宣称任意 HTTP Worker 都能调到任意节点，或已完成跨实例自动故障切换。
- Redis 未初始化或异常时限流会放行，当前不是故障时仍严格守住额度的实现。
- 常驻模型通道保留固定目标兼容，模型配置切换不等于依据实时负载自动选择最优模型；训练出口也仍有旧目标回退。
- 幂等机制约束已接入路径，不等于所有外部操作都能做到恰好一次执行；超时重试仍需核对外部结果。
- 历史手工项目未声明能力时仍允许部分 AI 调用，数据／MCP 则要求显式声明；载荷脱敏主要依赖字段名，不能保证自由文本中的业务秘密均被识别。

企业价值应表述为“复用集成能力、统筹节点执行、管理调用边界、积累可追溯数据”。高可用、吞吐量、成本下降和效果改善需要独立部署与业务测量证据，不从上述机制直接推导。

## 引入

项目静态包可加载平台根路径 SDK；外部 URL 项目建议使用平台运行上下文或 URL 参数中的绝对地址 `sf_gateway_sdk`：

```html
<script src="/project-gateway-sdk.js"></script>
<!-- 外部 URL 示例：<script src="https://skillforge.example.com/project-gateway-sdk.js"></script> -->
```

SDK 暴露 `window.PlatformProjectGateway`，兼容别名 `window.SkillForgeProject` / `window.SFProjectGateway`。

平台上传 Codex 生成的静态网页时，如果入口 HTML 没有接入 SDK，会自动注入 `/project-gateway-sdk.js` 和 `/project-autowire.js`。Autowire 会启动心跳，记录页面加载/表单/普通按钮输入，并在页面加载、表单提交或按钮动作后自动回传输出快照进入 `auto_analyze` AI 循环；同时会自动包装项目页内 `fetch` / `XMLHttpRequest`，把非静态 API request/response 作为脱敏后的 input/output trace 送入 Project Gateway（API response 默认 `auto_analyze:false`，避免高频接口重复模型调用）。同时提供 `window.SkillForgeProjectBridge.output/report/todo/ai/autoOutput/installApiCapture` 便捷方法。上传后还可通过 `/api/projects/{project_id}/service` 查看平台自动生成的服务契约，并通过 `POST /api/projects/{project_id}/service/invoke` 把该项目作为 API 服务调用：平台会创建 run、记录 input、调用声明的 AI/数据能力、ingest 输出并进入报告/待办/学习闭环。后台 `ai.cheap.*` 可配置低成本模型，开启 `project.service_conversion.ai_enabled` 后用于优化这个契约；需要完全手写协议时再按下面的最小用法直接调用 SDK。

## 最小用法

```html
<script src="/project-gateway-sdk.js"></script>
<script>
async function main() {
  const gateway = window.PlatformProjectGateway
  const ctx = await gateway.ready()
  gateway.startHeartbeat({ intervalMs: 25000 })
  console.log('可用能力', gateway.capabilities())
  console.log('网关限额', gateway.limits())

  await gateway.input({
    input: { keyword: '今日运营', filters: { department: ctx.project.department } }
  })

  const ai = await gateway.capability({
    capability: 'ai.generate',
    prompt: '请总结页面数据',
    input: { project: ctx.project, rows: [] }
  })

  await gateway.ingest({
    output: { summary: ai?.result?.output || ai.output || ai.text || ai.summary || '完成' },
    reports: [{ title: '项目报告', summary: '关键结论' }],
    todos: []
  })
}
main().catch(console.error)
</script>
```

## API

- `ready({ timeoutMs?, force? })`：获取平台下发的 `project/run/capabilities/gateway` 上下文；SDK 会从上下文保存本次 iframe 运行的 `gateway_token`，后续调用自动携带。
- `gatewayInfo()`：读取平台 origin、SDK 绝对地址、postMessage 协议、会话要求和网关限额等元数据。
- `capabilities()`：读取 manifest 声明且平台下发给当前项目的能力列表。
- `limits()`：读取 Project Gateway 当前载荷/调用上限，用于网页内提前分片或提示。
- `gatewayTokenAvailable()`：返回 SDK 是否已收到本次运行的网关会话 token。业务代码通常不需要读取 token。
- `startHeartbeat({ intervalMs?, payload?, timeoutMs? })`：定时刷新运行存活态，返回 `stop()`。
- `heartbeat(payload?, options?)`：单次心跳。
- `input(payload, options?)` / `recordInput(payload, options?)`：记录用户在项目内的查询、筛选、表单参数等输入，进入 Run Trace / Learning Loop，但不会把 run 标记为完成。
- `ingest(payload, options?)`：回传输出、报告、待办，进入 Run Trace / DecisionLog / Learning Loop；平台项目页默认补 `auto_analyze=true`，让输出立即进入 AI 分析循环，确需只记录时在 payload 中传 `auto_analyze:false` 或 `autoAnalyze:false`。
- `capability(payload, options?)`：调用平台 AI、数据、MCP 能力，必须由项目 manifest 声明并由平台授权。
- `ai(promptOrPayload, input?, options?)`：`ai.generate` 的简写。
- `analyze(payload?, options?)`：触发平台对当前 run 的 AI 分析。
- `onContext(listener)`：监听上下文刷新。

`options.requestId` 可自定义幂等键；同一个 `requestId` 重试不会重复扣费或重复建待办。

## 上传项目转 API 服务

静态项目上传成功后，契约中的 `service.api_service.invoke_url` 可直接用于后端/部门工具调用：

```bash
curl -X POST "$SKILLFORGE/api/projects/<project_id>/service/invoke" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"order-123","input":{"order_id":"123"},"prompt":"查询订单并生成报告"}'
```

返回会包含 `project_run_id`、`input_event_id`、`capability.call_id`、`run.report_count/todo_count` 和 trace 链接。`request_id` 是幂等键，重复调用不会重复扣费或重复建待办。

在 Codex/sf CLI 中应使用 CLI token 路由：

```bash
sf project invoke <project_id> --request-id order-123 --input-json '{"order_id":"123"}' --prompt '查询订单并生成报告'
```

CLI 实际调用 `/api/codex/projects/<project_id>/service/invoke`，不依赖浏览器 Cookie。

## 公司后端 SDK

需要集成到公司多个后端项目时，先登录 SkillForge Web，在项目列表 `/projects` 打开对应项目 `/projects/<project_id>`，在“Project Gateway SDK / 公司后端 SDK token”区域创建 Project SDK token，然后用服务端 SDK 调 `/api/projects/sdk/*`。该 token 只授权当前 Project Gateway，不是外网大模型 key；外网模型调用由 SkillForge 服务端读取后台 `ai.*` 配置，并按平台模型路由代发。

SDK 首期提供：

- `sdk/node/index.ts`
- `sdk/python/skillforge_project_sdk.py`

核心链路：

```text
company service -> SkillForge Project SDK -> SkillForge Project Gateway
  -> configured resident model / external LLM proxy
  -> ProjectRun / ProjectCapabilityCall / UsageLog
  -> LearningEvent / sample candidates (depending on the call path)
  -> configured async dataset sink (when enabled)
```

这是逻辑路径，不表示所有调用都会到达全部阶段。常驻模型仍有历史 macOS 节点约束，训练出口仍有旧目标回退；见下方“常驻模型兼容接口”和“数据记录与训练出口”。

token 原文只在创建响应中返回一次，数据库保存 HMAC hash；列表接口只返回 `token_prefix`、状态和时间字段。公司后端调用时使用：

```http
Authorization: Bearer sfproj_...
```

默认 token scope 仅包含 `runs:create`、`runs:capability`、`runs:trace`。`runs:ingest`、`runs:input`、`runs:assets`、`training:sync`、`training:assets`、`training:samples`、`training:datasets:create`、`training:datasets:sync` 等写入/同步权限需要创建 token 时显式授权。公网部署必须使用 HTTPS；Node/Python SDK 只允许公网 `https://` base URL，`http://localhost` / `http://127.0.0.1` 仅用于本地调试。

外网稳定性保护：

- 服务端按 Project SDK token 做 QPS 和每日请求限流，默认配置为 `PROJECT_SDK_QPS_PER_TOKEN=5`、`PROJECT_SDK_DAILY_LIMIT_PER_TOKEN=10000`。
- 236 常驻模型按 `token + model` 做并发保护，默认 `PROJECT_SDK_CONCURRENT_236_CALLS_PER_TOKEN_MODEL=4`。
- 429 响应包含 `error.detail.error_category=quota_error` 和 `error.detail.retry_after_seconds`，客户端应按该值退避，不要立即重试。
- SDK 会自动重试网络超时和 `502/503/504`，默认最多 2 次；`400/401/403/404/413/422/429` 不自动重试。
- 平台限流不是 WAF 替代品。公网 SkillForge 前面仍应放 TLS 终止、API Gateway/WAF、DDoS 防护、IP/Origin allowlist 和集中访问日志。

Node 最小用法：

```ts
import { SkillForgeProjectSdkError, createSkillForgeProjectSdk } from './sdk/node'

const sf = createSkillForgeProjectSdk({
  baseUrl: process.env.SKILLFORGE_BASE_URL!,
  projectId: 'order-report',
  token: process.env.SKILLFORGE_PROJECT_TOKEN!,
  timeoutMs: 180_000,
  maxRetries: 2,
})

try {
  const models = await sf.list236Models()
  const modelId = process.env.SKILLFORGE_MODEL_ID
  if (!modelId || !models.data?.some((item: { id: string; callable?: boolean }) => item.id === modelId && item.callable)) {
    throw new Error('Configure a callable model ID returned by this deployment')
  }
  const run = await sf.startRun({ request_id: 'order-123', input: { order_id: '123' } })
  const ai = await sf.chat236(run.projectRunId, modelId, [
    { role: 'user', content: '查询订单并生成报告' },
  ], { order_id: '123' })
  await sf.ingest(run.projectRunId, { output: ai.result || ai.output, reports: [{ title: '订单报告' }] })
  console.log(models.data.length, run.traceUrl, ai.trainingSink)
} catch (error) {
  if (error instanceof SkillForgeProjectSdkError && error.category === 'quota_error') {
    console.warn('retry later', error.retryAfterSeconds)
  }
  throw error
}
```

Python 最小用法：

```python
import os
from skillforge_project_sdk import SkillForgeProjectSdk, SkillForgeProjectSdkError

sf = SkillForgeProjectSdk(
    base_url=os.environ["SKILLFORGE_BASE_URL"],
    project_id="order-report",
    token=os.environ["SKILLFORGE_PROJECT_TOKEN"],
    timeout=180.0,
    max_retries=2,
)

try:
    models = sf.list_236_models()
    model_id = os.environ.get("SKILLFORGE_MODEL_ID")
    if not model_id or not any(item.get("id") == model_id and item.get("callable") for item in models.get("data", [])):
        raise ValueError("Configure a callable model ID returned by this deployment")
    run = sf.start_run({"request_id": "order-123", "input": {"order_id": "123"}})
    ai = sf.chat_236(run["projectRunId"], model_id, [
        {"role": "user", "content": "查询订单并生成报告"},
    ], {"order_id": "123"})
    sf.ingest(run["projectRunId"], {"output": ai.get("result") or ai.get("output")})
except SkillForgeProjectSdkError as exc:
    if exc.category == "quota_error":
        print("retry later", exc.retry_after_seconds)
    raise
```

SDK 的不同调用返回运行、调用或出口状态，如 `projectRunId`、`traceUrl`、`callId` 和 `trainingSink`，以该接口实际响应为准。`trainingSink.status=pending` 只表示出口等待处理，不代表已同步、已训练或已部署。常驻模型调用还要核对受支持节点、授权和目录中的 `callable` 状态；不要仅根据 Bridge 在线、模型名称或部署标签判断能否调用。

### 训练资产与图文微调数据

Project SDK 可把公司后端产生的文本、图片、图文样本登记为统一训练资产目录。资产目录只保存 metadata、hash、权限、lineage 和 storage ref；大文件仍保存在项目运行资产、知识库媒体或配置的存储中。数据集版本使用 manifest 引用媒体，不把图片 base64 写入训练任务。下面使用合成内容与演示质量分值，不能作为真实样本审核结果；`training-primary` 也要替换为已配置的目标节点。

Python 示例：

```python
run = sf.start_run({"request_id": "vision-001", "input": {"task": "ad-review"}})
asset = sf.upload_asset(
    run["projectRunId"],
    "frame.png",
    mime_type="image/png",
    metadata={"source": "ad-review-service"},
)
sample = sf.record_training_sample(
    run["projectRunId"],
    {
        "messages": [
            {"role": "user", "content": "判断图片是否适合投放"},
            {"role": "assistant", "content": "适合投放，主图清晰"},
        ]
    },
    dataset_profile="vision_instruction_v1",
    asset_id=asset["asset"]["id"],
    labels=["vision", "ad_review"],
    quality_score=0.9,
)
dataset = sf.create_dataset_version({
    "name": "ad-vision",
    "dataset_profile": "vision_instruction_v1",
    "limit": 200,
})
sf.sync_training_dataset(dataset["datasetVersion"]["id"], {"target_gateway_id": "training-primary"})
```

Node 示例：

```ts
const run = await sf.startRun({ request_id: 'vision-001', input: { task: 'ad-review' } })
const asset = await sf.uploadAsset(run.projectRunId, imageBlob, {
  fileName: 'frame.png',
  metadata: { source: 'ad-review-service' },
})
await sf.recordTrainingSample(run.projectRunId, {
  messages: [
    { role: 'user', content: '判断图片是否适合投放' },
    { role: 'assistant', content: '适合投放，主图清晰' },
  ],
}, {
  datasetProfile: 'vision_instruction_v1',
  assetId: asset.asset.id,
  labels: ['vision', 'ad_review'],
  qualityScore: 0.9,
})
const dataset = await sf.createDatasetVersion({
  name: 'ad-vision',
  dataset_profile: 'vision_instruction_v1',
  limit: 200,
})
await sf.syncTrainingDataset(dataset.datasetVersion.id, { target_gateway_id: 'training-primary' })
```

平台训练页 `/training/datasets` 可展示资产、数据集版本与同步任务。`dataset_version_id` 关联数据集，图文 profile 可转为 `multimodal_sft` 任务；需要实际训练节点和运行器。创建数据集、排队、同步成功和模型训练完成是不同状态。

### 常驻模型兼容接口（可选）

`/api/projects/models/236`、`/api/projects/openai/236/v1/models` 和 `/api/projects/openai/236/v1/chat/completions` 是代码保留的历史兼容路由，`236` 是路由标识，不是可访问地址或公开版附带的机器。当前实现仍约束 `inference-primary` 及相关旧别名，不能当作已实现任意节点选择的通用代理。

部署者需要先配置并授权自己的模型节点，从模型目录选择真实可调用的模型 ID。公开版不附带 macOS 机器、OpenWebUI 实例或任何模型权重；没有完成配置时不执行下面的示例。

```bash
# 先设置自己的平台地址、Project SDK token 和目录返回的模型 ID
MODEL_ID='replace-with-catalog-model-id'
# 模型 ID 应来自可信目录；用 JSON 编码避免手工拼接请求
python3 - "$MODEL_ID" <<'PYCODE' > /tmp/skillforge-model-request.json
import json, sys
print(json.dumps({
    "model": sys.argv[1],
    "messages": [{"role": "user", "content": "请分析这份合成示例"}],
    "max_tokens": 512,
    "truncation": "disabled"
}, ensure_ascii=False))
PYCODE
curl --fail-with-body "$SKILLFORGE/api/projects/openai/236/v1/chat/completions" \
  -H "Authorization: Bearer $SKILLFORGE_PROJECT_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/skillforge-model-request.json
```

调用前读取模型目录返回的 `context_window`、`max_input_tokens`、`recommended_input_tokens`、`max_output_tokens` 与验证状态。未测量模型采用代码中的保守回退值，不代表硬件或上游服务已支持某一上下文长度。

该路由在默认不裁剪时对超限输入返回错误；`truncation=auto` 会改变实际提交上下文，需检查响应中的裁剪标记。`skillforge.usage_reliable=false` 表示没有可靠用量，不应用于精确计费。

网关有模型文本工具调用到 `message.tool_calls` 的兼容转换，**不会替调用方执行工具**。转换存在不代表所有模型的工具输出均已通过验收，调用方仍须校验参数、权限和执行结果。

`scripts/verify_236_context_window.py` 是部署环境的探测工具，不是已经完成实测的证明。它会调用模型，相关选项可暂时或长期修改测量配置，并产生运行记录；不要作为普通安装步骤自动执行。

### 数据记录与训练出口

| 环节 | 当前实现与边界 |
|---|---|
| 运行与 trace | 记录已接入的 run、输入输出、能力调用与用量；载荷受限制并有脱敏处理，不代表所有业务字段都天然适合收集 |
| 学习事件与样本 | 部分调用路径会创建学习事件和样本候选，不能把存在样本记录视为已审核的数据集 |
| 训练出口任务 | `_project_training_sink_config` 在没有覆盖配置时仍默认 `enabled=true`，目标回退为旧逻辑标识 `GB10 237`。这不是随公开包提供的设备，也不保证任务已同步 |
| 模型训练与部署 | 仍需要训练运行器、任务、评估和审批；创建出口任务不会自动证明模型训练或上线 |

新部署应先审核数据范围与保留策略，再设置训练出口。项目元数据 `training_sink.enabled` 优先于全局 `SystemConfig` 的 `project.training_sink.enabled`；将有效配置设为 `false` 可关闭该出口排队，但**不会关闭其他 run/trace/学习记录**。启用时显式设置自己的目标节点，并核对同步任务结果。

平台的自动训练开关默认关闭，与这里的样本／出口任务默认值是两回事。固定目标回退与节点别名属于待改造的可迁移性限制，本次只修正文档，没有改变这段运行逻辑。代码见 [Project 服务](../../app/projects/service.py) 与 [路由](../../app/projects/router.py)。

### 接入 FAQ

- SDK 源码：`sdk/node/index.ts`、`sdk/python/skillforge_project_sdk.py`；先用合成数据验证一个项目运行。
- 服务端 SDK 使用 Project SDK token，不能用它直接访问外部模型服务，也不能把 token 当作模型密钥。
- 普通项目能力通过 `/api/projects/sdk/runs/{run_id}/capability` 调用，能力必须在项目授权范围内；常驻模型兼容通道另有上述限制。
- 查看成功时分别核对：API 调用、模型结果、trace 入库、出口同步和训练任务。任何一项成功都不能代替其余项。

## 可运行示例

- `docs/examples/projects/ai-report-dashboard/`：部门 AI 报告看板，演示输入记录、平台 AI 能力调用、报告/待办回传和 AI 自动分析闭环。
- `docs/examples/projects/org-data-search/`：组织数据查询小工具，演示项目网页调用已声明的只读 MCP 数据能力并回传报告和 proof。

## 安全边界

- 项目网页不保存、不读取平台 AI key、MCP env、Cookie 或业务系统密钥。
- 生产 AI/数据能力只通过 Project Gateway 调用；平台会记录 `project_capability_calls` 并进入学习闭环。
- SDK 只在平台项目 iframe 内可用；项目被单独打开时不能绕过平台调用能力。
- `input/ingest/heartbeat/analyze/capability` 等特权消息必须携带平台通过 `skillforge.project.context` 下发的 `gateway_token`；SDK 会自动处理。若手写 `postMessage`，只能临时使用当前上下文中的 token，不要持久化、写日志或暴露给第三方脚本。

## 报告设计模板

项目上传/运行后生成报告时，可在运行输入或 ingest payload 中传 `report_design_template_id`。平台使用 [报告设计目录](../../app/inbox/report_design_catalog.json)中的设计标准，写入每条 `reports[].payload._report_design`，并在收件报告详情页允许用户继续切换模板预览。目录基于 Open Design 派生并保留来源说明；具体可选模板以当前目录和接口为准，不等于包含或运行上游全部工作流。

```js
await window.SkillForgeProjectBridge.input({
  input: { report_design_template_id: 'od-design-meta' },
})

await window.SkillForgeProjectBridge.output({
  output: { summary: '今日运营报告' },
  reports: [{ title: '今日运营报告', summary: '先结论、再证据、再行动。' }],
})
```
