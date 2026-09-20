# Skill 个人图形化界面与 AI 调整规格

> **阅读范围**：本文保留原项目的设计与版本演进记录，不作为公开准备版的安装或验收指南。正文中的“当前／已完成／已上线”须结合当时版本理解；现行可用范围见[能力地图](../public/CAPABILITIES.md)，实测范围见[验证记录](../public/VALIDATION.md)。规范性权限与审核要求不因该说明而放宽。


> 日期：2026-05-21
> 原项目状态记录：已实现 / 持续演进（公开版验证范围另列）
> 关联文档：`AGENTS.md`、`docs/spec/skillstudio-v2-interaction-design.md`、`docs/spec/conversational-skill-workbench-functional-spec.md`、`docs/spec/skill-permission-unification.md`

## 1. 一句话定义

SkillForge 为每个 Skill 自动生成图形化运行和结果界面；用户可以让 AI 只调整“自己在当前 Skill 上看到的交互界面”。调整结果是 `user_id + skill_id + surface` 维度的个人 UI overlay，不修改 Skill 本体、不影响其他用户、不影响其他 Skill、不绕过 Skill Git / 审核 / 发布链路。

## 2. 背景

当前平台已经有基础字段：

- `Skill.param_ui_schema`：Portal 运行表单的参数 schema。
- `Skill.result_ui_schema`：Portal 执行结果的展示 schema。
- `contract.json.input_schema` / `output_schema`：能力大厅和直接运行能力已消费的输入输出 contract。

这些能力能生成基础表单和表格，但还不满足两个新需求：

1. Skill 需要更完整的图形化界面，包括表单分组、指标卡、图表、表格、报告段落、时间线等。
2. 每个用户对同一个 Skill 的使用方式不同，因此需要个人化界面调整，且只能影响当前用户当前 Skill。

## 3. 核心原则

### 3.1 当前用户、当前 Skill、当前界面

AI 的作用域必须被限定在：

```text
current_user + current_skill + current_surface
```

含义：

- 只能调整当前用户正在看的这个 Skill。
- 只能调整当前界面 surface，例如 `run_form`、`result`、`dashboard`。
- 不能访问、引用、修改其他 Skill。
- 不能把个人界面改动提升成团队默认或 Skill 默认，除非另走受控 patch + 审核发布链路。

### 3.2 AI 只生成声明式 overlay

AI 只能输出声明式 JSON：

- `ui_schema`
- `json_patch`
- `user_ui_overlay`

AI 禁止输出或执行：

- Vue / React / JS / TS / HTML / CSS 代码。
- 任意 iframe、script、style、事件处理器、表达式执行字符串。
- 需要运行时解释执行的 DSL。

### 3.3 图形化界面不是 Skill 逻辑

个人图形化界面调整不能改变：

- Skill 业务逻辑。
- Skill Git 内容。
- Skill 审核状态。
- Skill 发布态。
- 节点部署版本。
- 运行时 MCP scope / 数据源 / trigger。

它只能改变“当前用户看到什么、怎么排版、默认怎么展示”。

## 4. 目标与非目标

### 4.1 目标

1. 自动把 Skill 的输入输出 contract 适配成图形化界面。
2. 支持用户用自然语言调整自己在当前 Skill 上看到的界面。
3. 支持每个用户对同一个 Skill 保存不同界面偏好。
4. 支持运行审计回放，还原当时用户看到的界面、默认值和最终提交参数。
5. 保证 UI 自定义不突破统一 Skill 权限。

### 4.2 非目标

1. 不提供任意前端代码编辑器。
2. 不允许用户上传自定义 JS / HTML / CSS。
3. 不允许 AI 调用未授权 MCP 或读取业务原始数据来“设计界面”。
4. 不允许个人 overlay 改 Skill 默认发布界面。
5. 不允许用个人 overlay 绕过必填参数、后端参数校验或执行权限。

## 5. 概念模型

### 5.1 Skill 默认 UI schema

Skill 默认 UI schema 是 Skill 的正式展示 contract，来自：

- `param_ui_schema`
- `result_ui_schema`
- `contract.json`
- 未来可新增的 `ui.schema.json`

它属于 Skill 默认能力的一部分。如果要修改默认 UI，应通过 Skill Git、验证、审核、发布链路。

### 5.2 用户个人 overlay

用户个人 overlay 是当前用户对某个 Skill 某个界面的偏好：

```text
user_id + skill_id + surface -> overlay_json
```

它只影响当前用户。

典型内容：

- 字段顺序。
- 字段分组。
- 字段标题别名。
- 默认展开/收起。
- 结果表格列顺序。
- 图表类型。
- 图表字段映射。
- 指标卡组合。
- 隐藏当前用户本来可见的非必填字段；必填运行参数不得隐藏。
- 个人默认参数建议。

### 5.3 合并后 UI schema

后端每次返回界面时，按以下顺序合并：

```text
Skill 默认 UI schema
  + 当前用户当前 Skill 当前 surface 的 overlay
  = merged_ui_schema
```

前端只渲染 `merged_ui_schema`，不在本地私自拼权限或字段。

## 6. 声明式 UI schema

### 6.1 顶层结构

```json
{
  "schema_version": "skill-ui/v1",
  "skill_id": "tmall-link-decline-analysis-v2",
  "surface": "result",
  "base_skill_commit": "abc123",
  "layout": {
    "type": "grid",
    "columns": 12,
    "gap": 12
  },
  "components": [
    {
      "id": "summary_metrics",
      "type": "metric_group",
      "title": "核心指标",
      "span": 12,
      "bindings": [
        { "label": "下滑商品数", "path": "result.overview.down_item_count", "format": "integer" },
        { "label": "成交金额", "path": "result.overview.total_pay_amt", "format": "currency" }
      ]
    }
  ]
}
```

### 6.2 组件白名单

首期只允许以下组件类型：

| type | 用途 |
|---|---|
| `form_group` | 参数表单分组 |
| `field` | 单个参数字段 |
| `metric` | 单指标卡 |
| `metric_group` | 指标卡组 |
| `table` | 表格 |
| `line_chart` | 折线图 |
| `bar_chart` | 柱状图 |
| `pie_chart` | 饼图 / 占比图 |
| `report_section` | 报告段落 |
| `markdown_summary` | 受限 Markdown 摘要 |
| `timeline` | 执行 / 事件时间线 |
| `alert` | 风险提示 |
| `tabs` | 页面内 tab |

禁止组件：

- `html`
- `script`
- `iframe`
- `custom_component`
- `remote_widget`
- 任意可以加载外部资源或执行代码的组件。

即使字段名没有使用 `html` / `style` 等禁用 key，服务端也必须扫描所有字符串值，拒绝 HTML tag、事件处理属性、`javascript:` / `vbscript:`、HTML/SVG data URI、CSS `url()` / `@import` / `expression()` 等可执行或可加载外部资源的内容。`markdown_summary` 只表示受限文本摘要，不能绕过该规则渲染任意 HTML。服务端还必须拒绝 overlay 任意字符串值中的疑似密钥值，例如 `api_key=...`、`Authorization: Bearer ...` 或 `sk-...`，防止标题、说明、notes、表格列名或个人默认值变成长期密钥存储。

### 6.3 字段绑定规则

组件字段只能绑定到后端给出的可见字段集合：

```text
params.*
result.*
reports.*
todos.*
run_trace.safe_summary.*
```

`params.*` 必须精确匹配当前 Skill `param_ui_schema` 中存在且非密钥类的字段路径，不能只匹配根字段后继续追加不存在的嵌套路径。`personal_defaults` 同样只能设置这些已存在字段路径，并且只允许出现在 `run_form` surface；默认值必须通过对应字段的类型、枚举、格式和疑似密钥文本校验，对 object 和 array 默认值还必须递归校验 `properties` / `items` 的字段白名单、required 和类型，不能用个人默认值制造后端参数校验必然失败的运行表单，也不能把 API key/token 存成个人默认值。疑似密钥默认值必须拒绝，不能脱敏后保存，否则会改变用户实际提交参数语义。

组件上的 `field` 简写也必须按同一规则精确匹配当前 Skill 参数路径；表格列、图表和其它组件的 `binding` 都必须走同一绑定白名单校验，不能因为藏在 `columns`、`children` 或分组里跳过校验。`hidden_component_ids`、`component_order` 等组件 id 列表只接受安全 id，不能携带任意字符串。

禁止绑定：

- 平台密钥。
- Cookie。
- MCP env。
- API key。
- 钉钉 token。
- 未脱敏原始业务数据。
- 当前用户无权读取的字段。
- 其他 Skill 的字段。

即使某个 Skill 的 `param_ui_schema` 中声明了 `api_key`、`access_token`、`refresh_token`、`private_key`、`ssh_key`、`service_account_key`、`password`、`cookie`、`credential`、`secret`、`mcp_env`、`dingtalk_token` 等疑似密钥字段，或字段 `format` / `title` / `label` 暗示密码、token、API Key、Private Key，Portal 默认表单、个人 overlay、run trace UI 快照和 AI UI Designer 上下文都不能展示、绑定、重命名、设为个人默认值或传给模型。此类值应走平台凭据 / MCP scope / 后端配置，不应作为个人图形化界面的可调参数。

Portal Skill 详情返回的 `param_ui_schema` / `result_ui_schema` 也属于前端可见界面输入：后端必须在返回前剔除密钥类字段、移除疑似密钥默认值和枚举值，并脱敏 description / result columns 等历史文本中的密钥值，不能只依赖运行提交或 run trace 阶段兜底。`param_ui_schema` 必须只返回 Portal 表单需要的 JSON Schema 白名单字段，删除 `x-*`、examples、内部配置等未知扩展；清理后的 `required` 只能包含当前仍存在且非密钥类的字段，不能让历史 ghost required 或已剔除密钥字段导致前端不可填写、后端不可提交；`properties` / `items` 等结构字段如果不是对象，返回前必须规范化为空对象或移除，不能把数组、字符串等 malformed schema 原样透给前端渲染；默认 UI 的 `options` / `format` 也必须过滤疑似密钥值，不能通过枚举选项或格式字符串泄露 token；`dependsOn` 只能保留安全的 `field` / `value` / `values` / `operator`，条件中出现密钥字段或密钥值时必须删除整个条件。`result_ui_schema` 顶层也必须做白名单裁剪，只返回 `type`、`title`、`columns` 等展示必要字段；`result_ui_schema.columns` 必须返回结构化安全列：密钥类 key/title/binding 的列直接删除，`format` 等辅助字段中误填的密钥值只删除该辅助字段，不能返回被脱敏 key 造成前端渲染异常或泄露字段存在性。

### 6.4 Overlay Patch 操作白名单

允许操作：

- `reorder_component`
- `hide_component`
- `show_component`
- `rename_title`
- `set_default_view`
- `set_chart_type`
- `set_chart_binding`
- `set_table_columns`
- `set_form_group`
- `set_personal_default`

禁止操作：

- `add_data_source`
- `add_mcp_scope`
- `change_trigger`
- `change_skill_code`
- `change_workflow`
- `change_permission`
- `change_visibility`
- `change_required_validation`
- `change_publish_status`
- `write_skill_git`

### 6.5 大小与复杂度上限

服务端必须对个人 overlay 和执行 UI 快照做预算限制，防止 AI 或用户提交超大声明式结构造成数据库膨胀、API 响应过大或递归渲染风险：

- overlay JSON 最大 64KB。
- merged UI schema 最大 512KB。
- run trace / submission 中的 `ui_snapshot_json` 最大 768KB。
- overlay 组件数最大 500。
- overlay 组件树深度最大 8。
- AI 调整指令和保存的提示词最大 2000 字符。

超过限制时必须 fail-closed，返回参数错误；不能静默裁剪后保存，否则 run trace 无法可靠还原用户当时确认的界面。

## 7. AI UI Designer 流程

### 7.1 输入上下文

AI 只能收到以下上下文：

```json
{
  "user": {
    "id": "current_user",
    "role": "aibp",
    "permissions_rev": 12
  },
  "skill": {
    "id": "current_skill",
    "display_name": "当前 Skill",
    "base_skill_commit": "abc123",
    "permissions": {
      "read": true,
      "execute": true,
      "edit": false
    }
  },
  "surface": "result",
  "allowed_fields": ["result.overview.down_item_count"],
  "component_whitelist": ["metric", "table", "bar_chart"],
  "current_ui_schema": {},
  "current_overlay": {},
  "redacted_sample_output": {}
}
```

不得把以下内容放进 AI 上下文：

- 无权限 Skill。
- 全量 Skill 仓库。
- 平台 `.env`。
- Cookie / session / token。
- MCP server 环境变量。
- 未脱敏业务原始返回。
- 其它用户的 overlay。
- 当前 Skill 参数 schema 中疑似密钥的字段名、绑定路径或个人默认值。

### 7.1.1 AI key 调用边界

AI UI Designer 只能通过后端统一封装 `app.common.ai.call_llm(require_system_config=True)` 调用模型。key 来源只允许读取后台配置：

1. 读取 `SystemConfig` 中的 `ai.api_base`、`ai.api_key`、`ai.model`、`ai.max_tokens`、`ai.temperature`、`ai.timeout`。
2. `ai.api_base`、`ai.api_key`、`ai.model` 三项都必须来自后台配置；任一缺失时 AI preview 只能进入安全 fallback，不得改用环境变量 key 或无鉴权调用。

安全要求：

- `ai.api_key` 只在服务端调用模型时作为 Authorization header 使用。
- Portal 前端、AI preview 返回值、run trace、执行参数、个人 overlay 都不得返回或记录 key。
- 系统配置列表接口返回 `ai.api_key` 等 secret key 时只能返回空值/已配置状态，不得回传明文。
- 上游模型错误正文、`ai_reason`、失效 overlay 的 `ui_pref_invalidated.reason`、流式 error 事件和服务端日志必须经过 secret redaction，避免 provider 用普通文本、JSON 字段名、JSON 字段值、Authorization Basic/Bearer、Cookie、URL query、PEM private key 或 URL/DSN 连接串回显的 key/token/password/private key 外泄。
- 传给模型的 payload 只包含已脱敏的用户 `instruction` 与脱敏后的当前 Skill UI 上下文；如果用户误把 key/token 粘进指令，发送给 provider 前必须保留语义但替换密钥值。
- 即使 `current_overlay.notes`、标题、描述等普通文本字段或 overlay JSON key 中出现用户误填的 key/token，进入 LLM payload 或返回错误摘要前也必须递归脱敏或泛化；脱敏只用于 AI 上下文和公开错误摘要，不改变数据库中已经保存的 overlay 原文。
- `prompt_summary` 会长期按用户 + Skill + surface 保存，也必须在入库前脱敏，不能把误填密钥作为“默认提示词”返回到前端或再次发送给模型。读取 `saved_prompt` 时也必须再次脱敏，兼容历史数据中可能已经存在的明文提示词。
- 该上下文必须排除 Cookie、session、Authorization header、MCP env、平台 API key、钉钉 token、业务系统 token 和其它用户的 overlay。

### 7.2 生成步骤

1. 后端校验当前用户对当前 Skill 至少有 `read` 权限；涉及运行界面时还要有 `execute`。
2. 后端构造 allowed fields、组件白名单、脱敏样例。
3. AI 生成 `overlay_patch`。
4. 后端把 `overlay_patch` 作为增量变更合并到 `current_overlay`，不能用浅层覆盖丢失用户已有的标题、隐藏、排序、默认值或分组设置；当 patch 明确 show/hide 同一组件时，以本次 patch 的显式意图为准，并清理旧 overlay 中相反的 show/hide 操作。
5. 后端校验合并后的 overlay：
   - 字段必须存在于 allowed fields。
   - 组件必须在白名单。
   - overlay 不能包含任意代码。
   - overlay 不能改变 Skill 逻辑和权限。
6. 后端返回预览用的 `merged_ui_schema`。
7. 用户点击应用后，后端保存个人 overlay。

### 7.3 应用规则

AI 生成结果不能直接落盘。必须先预览，再由用户确认。

保存位置：

```text
user_skill_ui_preferences
```

不允许写：

```text
skills-repo/<skill_id>/
```

## 8. 权限与隔离

### 8.1 读取权限

`GET /portal/skills/{skill_id}/ui` 必须先调用统一 Skill 权限：

```text
require_skill_access(db, skill_id, user, "read")
```

返回内容必须按当前用户权限裁剪。

### 8.2 执行权限

提交执行仍以 `execute` 权限为准：

```text
require_skill_access(db, skill_id, user, "execute")
```

个人 overlay 不能让无执行权限用户看到运行按钮或调用执行接口。
提交执行只能固化 `run_form` surface 的 UI 快照；即使用户同时有 `result` / `dashboard` 个人界面，也不能把这些展示 surface 作为执行提交的 `ui_surface` 写入 run trace。结果界面偏好只影响提交后的展示，不解释本次执行参数来源。
运行表单中的必填参数不能被 `hidden_component_ids`、`visible=false`、`hide_component` 或 operation 生成的子组件隐藏；服务端必须 fail-closed。
Portal 提交的 `actual_params` 必须是当前 Skill `param_ui_schema` 的白名单字段集合：有 schema 时拒绝顶层和嵌套未知字段；对象字段和数组 `items` 中的对象字段都必须逐层校验 `type`、`required` 和未知字段，不能让数组对象绕过 schema 白名单。若 `properties` / `items` 等嵌套 schema 结构 malformed，服务端必须 fail-closed 拒绝对应提交或个人默认值，不能跳过校验或抛 500。无论是否有 schema，都拒绝 `_execution`、`_execution_backend`、`_aiclaw_instance_id`、`_script_path`、`_script_timeout`、`_target_dir`、`instance_id` 等运行时控制字段，防止业务用户通过参数覆盖 Bridge/AIClaw runtime、脚本路径或目标节点。`api_key`、`access_token`、`refresh_token`、`private_key`、`ssh_key`、`service_account_key`、`password`、`cookie`、`credential`、`secret`、`mcp_env`、`dingtalk_token` 等疑似密钥参数同样必须递归拒绝提交，即使它们出现在 Skill schema 中；普通字符串、数组值、数组内对象或对象内字符串中误填 `api_key=...`、`Authorization: Bearer ...`、`sk-...`、PEM private key 等疑似密钥值时也必须拒绝，不允许进入执行入参、提交记录或 run trace。

### 8.3 编辑权限

用户保存个人 overlay 不要求 `edit` 权限，只要求：

- 当前 Skill 可读。
- 对运行 surface 调整时可执行。
- overlay 不修改 Skill 本体。

权限口径必须按 surface 区分：`run_form` 的获取、AI 预览、保存和删除要求 `execute`；`result` / `dashboard` 的获取、AI 预览、保存和删除只要求 `read`。前端 `permissions.customize_ui` 必须反映同一口径，不能因为用户不能执行 Skill 就阻止其调整自己有权查看的结果界面。

如果用户要把个人界面设为 Skill 默认界面，必须具备 `edit`，并创建受控 Workbench patch。

### 8.4 用户隔离

所有缓存 key 必须包含：

```text
user.id
permissions_rev
skill_id
base_skill_commit
surface
ui_pref_version
```

禁止只按 `skill_id` 缓存合并后的 UI。

### 8.5 Skill schema 变更兼容

个人 overlay 绑定的是某个 Skill commit 下的声明式界面。如果 Skill 默认 `param_ui_schema` / `result_ui_schema` 更新后，当前用户的 active overlay 绑定了已删除字段、已变更类型或当前版本不再允许的组件，Portal 获取界面时必须自动停用该个人 overlay 并返回当前 Skill 的默认 UI，不能让旧个人偏好把 Skill 详情页或结果页卡死。返回内容应携带被停用的 `ui_pref_id` / `ui_pref_version` 和安全原因摘要，便于前端提示和审计。

提交执行时如果用户显式携带旧的 `ui_pref_id` / `ui_pref_version`、已被禁用的历史 UI 偏好或旧 `merged_ui_schema_hash`，服务端必须拒绝本次提交，要求用户刷新界面后重新确认参数；不能静默降级到默认 UI 后继续执行，否则 run trace 无法证明用户当时确认的界面。仅携带 `ui_pref_version` 时也必须能定位到当前用户、当前 Skill、当前 surface 的当前 active 偏好版本；找不到或该版本已不再 active 时必须拒绝。若调用方没有携带任何显式 UI 上下文，服务端不能自动套用当前 active 个人 overlay，只能使用当前默认 UI 提交；如果发现 active overlay 已失效，可以停用并在本次 UI snapshot 中记录 `ui_pref_invalidated`，证明执行没有沿用旧个人界面。

## 9. 数据模型

新增表建议：

```text
user_skill_ui_preferences
- id
- user_id
- skill_id
- surface
- base_skill_commit
- overlay_json
- overlay_schema_version
- generated_by        manual | ai
- prompt_summary      用户自定义提示词摘要，按 user_id + skill_id + surface 保存
- enabled
- version
- created_at
- updated_at
```

唯一约束：

```text
unique(user_id, skill_id, surface, enabled=true)
```

生产数据库必须使用 partial unique index 保证同一时刻只有一个 enabled 偏好；同时保留 `(user_id, skill_id, surface, version)` 唯一约束用于历史版本定位。如果测试或临时数据库不支持 partial unique，服务端保存逻辑仍要先停用旧 active 行并 flush，再创建新版本，避免同一事务内插入新 active 版本时撞上旧 active 版本的唯一约束；生产不能只依赖应用层顺序。若两个请求并发保存同一用户、同一 Skill、同一 surface 的个人界面，数据库唯一约束拦截后端写入时，服务端必须转成 `409 UI_PREF_CONFLICT`，提示前端刷新后重试，不能暴露原始数据库异常或返回 500。

## 10. API 设计

### 10.1 获取合并后界面

```http
GET /api/portal/skills/{skill_id}/ui?surface=run_form
```

返回：

```json
{
  "skill_id": "skill_a",
  "surface": "run_form",
  "base_skill_commit": "abc123",
  "ui_pref_id": "pref_1",
  "ui_pref_version": 3,
  "generated_by": "ai",
  "saved_prompt": "把日期字段叫投放日期，默认平台选万相台",
  "merged_ui_schema_hash": "sha256:...",
  "overlay": {},
  "merged_schema": {},
  "permissions": {}
}
```

### 10.2 AI 预览调整

```http
POST /api/portal/skills/{skill_id}/ui/ai-preview
```

请求：

```json
{
  "surface": "result",
  "instruction": "把异常商品放在最前面，并增加一个成交金额柱状图",
  "current_overlay": {}
}
```

返回：

```json
{
  "overlay": {},
  "merged_schema": {},
  "merged_ui_schema_hash": "sha256:...",
  "ai_status": "llm",
  "ai_reason": null,
  "ai_context": {
    "skill_id": "skill_a",
    "surface": "result",
    "base_skill_commit": "abc123",
    "allowed_component_types": ["table", "bar_chart"],
    "allowed_binding_prefixes": ["params.", "result."]
  }
}
```

`ai_context` 是给前端展示/调试的安全摘要，不得包含 API key、Authorization header、Cookie、MCP env、业务 token 或其它用户数据。

### 10.3 保存个人 overlay

```http
PUT /api/portal/skills/{skill_id}/ui/preferences
```

请求：

```json
{
  "surface": "result",
  "overlay": {},
  "generated_by": "ai",
  "prompt_summary": "把异常商品放在最前面，并增加成交金额柱状图"
}
```

`prompt_summary` 用作当前用户在当前 Skill 当前 surface 的默认 AI 调整提示词；前端下次打开该 Skill 时回填该提示词。保存维度必须是 `user_id + skill_id + surface`，不能做全局默认，也不能影响其它用户或其它 Skill。服务端保存前必须对 `prompt_summary` 做 secret redaction，避免用户误填的 token/API key 进入长期配置；返回 `saved_prompt` 时也必须再次 redaction，防止历史明文被回显。

返回：

```json
{
  "ui_pref_id": "pref_1",
  "ui_pref_version": 4,
  "saved_prompt": "把异常商品放在最前面，并增加成交金额柱状图",
  "merged_ui_schema_hash": "sha256:..."
}
```

### 10.4 历史版本与恢复

```http
GET /api/portal/skills/{skill_id}/ui/preferences?surface=run_form
POST /api/portal/skills/{skill_id}/ui/preferences/{ui_pref_id}/restore?surface=run_form
GET /api/hall/direct-capabilities/{capability_id}/ui/preferences?surface=run_form
POST /api/hall/direct-capabilities/{capability_id}/ui/preferences/{ui_pref_id}/restore?surface=run_form
```

历史接口只返回当前用户、当前 Skill/直连能力、当前 surface 的版本摘要，不返回完整 overlay。恢复历史版本时，服务端必须重新按当前 Skill schema 校验该 overlay，停用当前 active 偏好，再复制历史 overlay 生成一个新的 active 版本；不能直接把旧行重新标记为 active。

### 10.5 恢复默认

```http
DELETE /api/portal/skills/{skill_id}/ui/preferences?surface=result
```

只删除当前用户当前 Skill 当前 surface 的个人 overlay。

### 10.6 提升为默认界面

```http
POST /api/skills/{skill_id}/workbench/ui-patch
```

要求：

- `permissions.edit == true`
- 生成 Workbench patch。
- 写入 Skill Git。
- 进入验证、审核、发布链路。

## 11. 运行快照与审计

提交执行时必须记录：

```text
ui_surface=run_form
ui_pref_id
ui_pref_version
base_skill_commit
merged_ui_schema_hash
actual_params
param_defaults_source
overlay
merged_schema
```

目的：

1. run trace 能还原当时用户看到的界面。
2. 能区分“Skill 默认参数”和“个人界面默认参数”。
3. 方便排查同一个 Skill 不同用户运行结果不同的原因。

`actual_params` 仍然是执行的唯一输入事实；个人 overlay 只解释这些参数如何展示、如何默认填充。

`personal_defaults` 中允许的相对日期占位值（例如 `yesterday`）在返回 `merged_schema` 和写入提交 UI 快照前必须解析为本次界面确认时的具体日期，避免前端把相对值原样提交后与后端日期格式校验冲突。原始 overlay 可以保留相对值，run trace 中的 `merged_schema.personal_defaults` / `personal_defaults` 必须记录解析后的值。

提交记录和 `ExecutionRun.metadata_json.ui` 必须保存一次不可变 UI 快照，至少包含 `ui_pref_id`、`ui_pref_version`、`base_skill_commit`、`merged_ui_schema_hash`、`overlay`、`merged_schema`、`personal_defaults`、`param_defaults_source` 和 `actual_params`。UI 快照大小校验必须在写入 `actual_params` 后再次执行，不能只校验不含执行入参的空快照。不能只保存 hash 或 component id 列表，否则偏好被更新、禁用或 Skill 默认 schema 变化后，run trace 无法独立还原当时界面。

`ui_snapshot_json` 与 `ExecutionRun.metadata_json.ui` 是不可变审计事实，不能在读取时改写数据库本体；但 API 返回给 Portal submission detail / run trace 前必须做读取侧递归脱敏，兼容历史数据中可能已经存在的 secret-like 标题、绑定路径、字段名或默认值。Portal submission list/detail 返回历史 `params`、`result_summary`、`result_ui_schema`、`error_message` 时也必须做同样的读取侧脱敏；run trace 返回 `ExecutionRun.metadata_json`、`UsageLog.metadata_json`、`DecisionLog.output_result`、`DecisionLog.input_snapshot` 和 `PlatformCookieAudit.detail` 时也必须脱敏。返回内容不得包含 `params.api_key`、`api_key`、`access_token`、`API Key`、`sk-...`、`Authorization: Bearer ...`、`Cookie: ...` 等密钥值或密钥字段提示。

快照保存前必须校验大小预算；超限时拒绝提交并提示用户简化个人界面，而不是写入不可控的大 JSON。

Portal run trace 和 Portal submission detail 都携带个人 UI 快照、个人默认值和 `actual_params`，因此访问控制不能复用普通 Skill trace 的部门/负责人兜底可见逻辑。除平台管理员、具备全局查看权限的审计角色外，只允许本次 Portal 提交人读取对应 run trace / submission detail；同部门成员、Skill owner、`ai_engineer` 或 `aibp` 不因普通角色或 Skill 可见性自动获得该用户的个人运行界面和提交参数。后端必须通过 `ExecutionRun.metadata_json.portal_submission_id` 或 `trigger_type=portal:<user_id>` 识别 Portal run，并用 `user_id` / `requester_id` / trigger 中的提交人作为所有者判定；历史 trace 只有 `portal_submission_id` 时必须回查 `SkillSubmission.requester_id`，不能因为缺少 trigger owner 就走普通 trace 权限兜底。

Portal overview 的最近提交摘要虽然不返回 `actual_params` / UI 快照，也必须先按统一 Skill read 权限过滤；同组织关系不能作为额外兜底去展示当前用户不可读 Skill 的 submission 摘要。缓存键必须包含 `user_id` 和 `permissions_rev`，不能只按部门缓存，避免同组织但权限不同的用户互相看到不该出现的 submission 摘要。

Portal overview 可以展示当前用户可读 Skill 的最近执行摘要，但如果当前用户没有读取某条 submission detail 的权限，响应中不能返回真实 submission id 作为跳转目标，只能返回不可跳转的摘要行 id，并显式标记 `can_view_detail=false`。

Portal Skill 详情页的运行历史对普通用户只能展示自己的提交记录；平台管理员或 `can_view_all` 审计角色可以查看该 Skill 的全量最近提交摘要。运行历史摘要不得包含 `actual_params`、`ui_snapshot_json` 或个人 overlay 内容。

## 12. 前端设计

### 12.1 统一渲染器

新增统一组件：

```text
SkillVisualRenderer
```

职责：

- 渲染 `merged_ui_schema`。
- 使用组件白名单。
- 不执行 schema 中的任何代码。
- 对未知组件降级为安全占位。
- 所有字段值都来自后端返回的已授权数据。
- 嵌套参数字段必须按完整路径（如 `params.filters.platform`）应用标题、说明、顺序、隐藏和个人默认值；`hidden_component_ids` / `hide_component` 必须递归作用到分组或 tabs 内的子组件，不能只处理顶层组件，也不能把子字段 overlay 误作用到父对象。

### 12.2 用户操作入口

Portal Skill 详情页：

- Portal 首页 Skill 卡片必须同时提供“运行”和“界面”两个明确入口；“界面”入口直达该 Skill 详情页的 AI 页面调整入口。Skill Studio 顶栏也必须提供“AI 调整界面”入口，跳转到同一个 Portal 个人界面 surface。Skill 详情页必须悬浮一个小 AI 图标，点击后在当前 Skill 页面上打开对话式页面调整弹窗，不能只把个人 UI 调整藏在详情页下方；账号无调整权限时入口仍可见但禁用并提示原因。
- 对话式 AI Designer 可切换 `run_form` surface：用户用自然语言描述字段分组、排序、标题、默认值等结构变化，AI 生成声明式预览，用户确认后保存或恢复默认。
- 同一个对话式 AI Designer 可切换 `result` surface：用户用自然语言描述指标卡、图表、表格列、报告区等结果结构变化，AI 生成声明式预览，用户确认后保存或恢复默认；不要求用户先产生一次 submission 才能设置结果展示偏好。
- AI 预览一旦应用到当前运行页面，前端必须在页面上持续展示“未保存预览”状态，并提供保存和恢复默认入口；不能只把保存/恢复藏在一次性弹窗里，避免用户误以为预览已持久化。
- 已保存的个人运行/结果页面必须保留版本记录入口，用户能看到自己的历史版本、当前使用版本和安全摘要，并可把旧版本恢复成新的 active 版本；恢复仍走当前 Skill schema 校验，旧 overlay 不再适配时必须拒绝而不是强行启用。
- 两个入口都回填当前用户 + 当前 Skill + 当前 surface 的 `saved_prompt`，但保存前后都必须脱敏疑似密钥文本。
- 运行按钮只受 `run_form` 未保存预览阻断；结果界面草稿不能改变本次执行参数。

能力大厅直连能力：

- `hall/abilities/gpt-imagegen` 这类直连能力也必须在页面右下角悬浮一个小 AI 图标，点击后打开同样的对话式页面调整弹窗；顶部不再放大块“页面调整”按钮，页面模式切换只能作为普通辅助操作。
- 直连能力的个人页面保存维度是当前用户 + 当前 capability/skill id + `run_form` surface，复用 `user_skill_ui_preferences`、声明式 overlay、组件白名单、脱敏、AI 服务端封装和恢复默认流程。
- 直连能力仍不能通过个人 overlay 写 Skill Git、修改发布态、增加数据源/MCP scope、改变权限或执行逻辑；前端只可按后端返回的 `merged_ui_schema` 调整已授权页面字段的标题、顺序、可见性和个人默认值。
- 直连能力也必须在悬浮 AI 入口旁展示个人页面状态：未保存预览可直接保存或恢复默认，已保存个人页面可直接恢复默认。
- 直连能力的 AI Designer 内必须暴露“保存记录”，用户能看到自己的历史版本、当前使用版本和脱敏摘要，并可把旧版本恢复成新的 active 版本；恢复默认只停用 active 个人 overlay，不删除历史记录。

结果页：

- 悬浮小 AI 图标打开对话式结果页面调整弹窗。
- 支持表格、指标卡、图表、报告段落的个人布局。
- `result` surface 的前端渲染必须覆盖后端白名单中的核心展示组件：`table`、`line_chart` / `bar_chart` / `pie_chart`、`metric`、`metric_group`、`markdown_summary`、`report_section`、`tabs` 和 `alert`；`report_section` / `tabs` 至少要稳定渲染其可见子组件，不能因为容器组件出现在顶层就退化成原始 JSON；`markdown_summary` 只能按纯文本/受限文本展示，不能把内容当 HTML 注入。
- 用户用自然语言描述结果展示结构，AI 生成声明式预览，用户确认后保存或恢复默认，保存维度仍是当前用户 + 当前 Skill + `result` surface。
- 结果界面调整只改变当前用户看到的结果展示，不修改已执行的 `actual_params`、执行结果或 run trace 快照。
- 如果后端返回 `ui_pref_invalidated`，前端必须明确提示该个人界面已因 Skill schema 变化失效，并展示当前默认界面，避免用户误以为仍在使用个人布局。
- 如果保存个人界面返回 `409 UI_PREF_CONFLICT`，运行表单和结果页都必须提示并重新拉取当前 surface 的 UI，清空本地未保存草稿，避免用户在过期 overlay 上继续保存。

### 12.3 设计约束

- 不做低代码页面搭建器。
- 不暴露字段外的自由数据源。
- 不用卡片嵌套卡片堆布局。
- 默认界面要先能自动生成，AI 调整是增强，不是必须步骤。

## 13. 与 Skill Git / 审核的关系

个人 overlay：

- 不进 Skill Git。
- 不需要审核。
- 不改变发布版本。
- 不触发节点 reload。

默认 UI schema：

- 属于 Skill 本体展示 contract。
- 必须进入 Skill Git。
- 必须有 diff、验证和审核。
- 发布后运行配置引用明确 commit/tag。

推广流程：

```text
个人 overlay
  -> 生成 presentation/ui patch
  -> Workbench 预览
  -> 保存到 skills-repo/<skill_id>/
  -> Skill Git commit
  -> 提交审核
  -> 发布
```

## 14. 验证要求

### 14.1 后端测试

至少覆盖：

1. 用户 A 的 overlay 不影响用户 B。
2. 用户 A 的 Skill X overlay 不影响 Skill Y。
3. 无 `read` 权限不能获取 merged UI。
4. 无 `execute` 权限不能通过 run_form overlay 执行。
5. AI patch 绑定无权限字段时被拒绝。
6. AI patch 包含 HTML / JS / CSS / script 时被拒绝。
7. 提交执行时只接受 `run_form` surface，并写入 `ui_pref_id`、`ui_pref_version`、`merged_ui_schema_hash`、`actual_params`、`overlay`、`merged_schema` 和 `param_defaults_source`。
8. 删除个人 overlay 后恢复默认界面。
9. `field` 简写、表格列 `binding`、嵌套 `children` 和组件 id 列表都经过服务端白名单校验。
10. overlay 大小、组件数、组件树深度、AI 指令、保存提示词和 UI 快照超限时被拒绝。
11. `personal_defaults` 只能用于 `run_form`，且默认值必须符合字段类型、枚举、格式、object/array 嵌套 schema 和疑似密钥文本校验；嵌套 schema malformed 时必须 fail-closed；相对日期默认值进入 `merged_schema` / run trace 快照前必须解析为具体日期。
12. Portal 提交参数拒绝 schema 外字段、数组 items 中 schema 外字段、运行时控制保留字段、疑似密钥字段，以及普通字段/数组值中误填的疑似密钥文本。
13. AI 预览的增量 overlay 不会丢失当前 overlay 中已有的重命名、隐藏、排序、默认值或组件设置，且显式 show/hide 不会被旧的相反操作覆盖。
14. active 个人 overlay 因 Skill schema 变更失效时，获取 UI 自动停用该 overlay 并回退默认 UI；显式提交旧 overlay、已禁用历史 UI 偏好或旧 hash 时必须拒绝；无显式 UI 上下文的提交只能使用默认 UI，不能自动套用 active 个人 overlay。
15. `user_skill_ui_preferences` 对 `enabled=true` 的 `(user_id, skill_id, surface)` 有数据库唯一约束，防止并发保存产生多个 active 个人界面。
16. 并发保存个人界面命中 active/version 唯一约束时，后端返回 `409 UI_PREF_CONFLICT`，不泄露数据库错误。
17. Portal run trace 和 submission detail 只允许提交人、平台管理员或 `can_view_all` 审计角色读取；同部门、Skill owner、`ai_engineer` / `aibp` 不能走普通 trace 兜底权限。
18. Portal overview 缓存必须按 `user_id + permissions_rev + primary_org` 隔离，不能只按部门共享。
19. Portal Skill 详情页的运行历史对普通用户必须按 `requester_id=current_user.id` 过滤，不能暴露他人 submission id、提交者和状态。
20. Portal overview 最近提交摘要对不可查看详情的记录不得返回真实 submission id，前端不得展示详情跳转。
21. AI preview 的用户 `instruction`、保存的 `prompt_summary` 和读取返回的历史 `saved_prompt` 都必须脱敏疑似密钥值，密钥值不得进入 provider payload、长期保存的默认提示词或前端回显。
22. Portal Skill detail、submission list/detail 和 run trace 返回历史 `param_ui_schema` / `result_ui_schema` / `params` / `result_summary` / `error_message` / `ui_snapshot_json` / `metadata_json` / `input_snapshot` / `output_result` / `PlatformCookieAudit.detail` 前必须递归脱敏疑似密钥字段和值，不改写历史审计记录本体；Skill detail 的 schema 还必须做字段白名单裁剪、malformed `properties` / `items` 结构规范化和 `required` 清理，包括数组 `items.properties` 和 `result_ui_schema` 顶层字段，不能把内部扩展字段、异常 schema 结构或不可填写 required 原样透给前端。
23. Portal Skill detail 必须同时暴露运行表单入口和结果展示入口；结果展示入口不能只藏在 submission detail 中。

### 14.2 前端测试

至少覆盖：

1. `SkillVisualRenderer` 渲染 form/table/chart/metric。
2. 未知组件安全降级。
3. 切换用户后 overlay 不串号。
4. 恢复默认后不再展示个人布局。
5. AI preview 只展示预览，不自动应用。
6. 后端返回 `ui_pref_invalidated` 时，运行表单和结果页都展示失效回退提示。
7. 保存个人界面命中 `UI_PREF_CONFLICT` 时，前端提示冲突并刷新当前界面。
8. AI preview 传入模型前会对 `current_overlay` 普通文本递归脱敏，误填的 key/token 不进入 provider payload。

涉及前端页面时运行：

```bash
cd web && npm run typecheck
```

每次改动后运行：

```bash
git diff --check
```

## 15. 分阶段落地

### P0：Schema 渲染器收口

- 抽出 `SkillVisualRenderer`。
- 支持现有 `param_ui_schema` / `result_ui_schema`。
- 结果页从 table/text 扩展到 metric/table/chart/report。

### P1：个人 overlay

- 增加 `user_skill_ui_preferences`。
- 增加获取、保存、删除 overlay API。
- 提交执行写 run 快照字段。

### P2：AI UI Designer

- 增加 AI preview API。
- 接入组件白名单和字段 allowlist。
- AI 输出只接受 JSON patch。

### P3：提升为默认界面

- 增加 Workbench `presentation` / `ui` 模块。
- 个人 overlay 可转为 Skill 默认 UI patch。
- 进入 Skill Git、验证、审核、发布。

## 16. Goal 文案建议

```text
新增 Skill 个人图形化界面能力：系统基于当前 Skill 的已授权底座信息自动生成图形化运行/结果界面；AI 只能调整“当前用户在当前 Skill 上看到的交互界面”，调整结果保存为 user_id + skill_id + surface 维度的个人 UI overlay。该 overlay 只对当前用户、当前 Skill 生效，不影响其他用户，不影响其他 Skill，不修改 Skill 业务逻辑、默认发布界面、Skill Git、审核状态或节点部署版本。AI 可读取的上下文仅限当前用户对当前 Skill 已有权限可见的信息：Skill 元数据、当前 commit、input/output contract、param_ui_schema、result_ui_schema、允许组件白名单、脱敏样例输出和当前个人 overlay。AI 输出只能是声明式 UI schema / JSON Patch / overlay，不得生成或执行任意 Vue/JS/HTML/CSS 代码。用户执行 Skill 时，后端必须合并 Skill 默认 UI schema 与该用户个人 overlay，并在提交记录和 run trace 中固化 ui_pref_id、ui_pref_version、base_skill_commit、merged_ui_schema_hash、实际 params，确保能还原当时用户看到的界面、默认值和最终提交参数。
```

## 17. 硬性禁止

1. 禁止 AI 修改当前 Skill 之外的界面。
2. 禁止个人 overlay 写入 `skills-repo/`。
3. 禁止个人 overlay 修改 Skill 发布态。
4. 禁止 schema 中出现任意可执行代码。
5. 禁止前端本地根据角色推断字段权限。
6. 禁止把隐藏字段当成脱敏；无权字段后端不得返回。
7. 禁止运行时只记录最终结果而不记录 UI overlay 版本。
