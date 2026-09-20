# Skill 输出契约（SkillForge 平台规范）

> 你在创建 Skill，写 `scripts/main.py` 前**必须 Read 本文件一次**。
> 本文件定义 `main.py` 的返回结构，让 SkillForge 平台自动把结果分发为待办 / 报告 / 数据。

---

## 0. `main.py` 执行模型（先理解，再动手）

**`main.py` 是 payload 优先的采集 + 分析函数**：

```
  stdin JSON payload  ──►  main(payload: dict) -> dict  ──►  stdout JSON
```

- **payload 已带真实数据时**：直接分析 payload，保证样例沙箱和回归测试可离线跑通
- **payload 缺 datasource 数据时**：生成阶段先看当前会话已注入的 MCP 工具；有匹配业务 MCP 时必须先用平台 MCP 验证真实样本、字段结构、原始 JSON、URL/page_url/参数和 collection proofs，再在 `main.py` 中通过 `skillforge_sdk.SkillForge.fetch_api("mcp://tool_name", ...)` 调用同一平台能力；没有匹配 MCP 时先补平台 MCP/collection scope，不能把私有 MCP server 放进 Skill 仓库。
- **MCP 是平台能力，不是 Skill 内置能力**：运行时 `main.py` 不能直接 import/call MCP server，也不能在 `scripts/` 新增 `*_mcp_server.py`、`collection_client.py`、cookie 读取或 subprocess 采集器。若平台已把业务 MCP 暴露为 SkillForge SDK adapter，必须通过 `sf.fetch_api("mcp://tool_name", ...)` 调用稳定业务工具；平台尚无能力时，先扩展平台 MCP 并验证真实 raw JSON，再更新 Skill。
- **禁止伪数据兜底**：平台 MCP 返回空、HTTP 200 但业务 `ok=false`、cookie 不可用或 collection proof 失败时，必须输出明确 `data_errors` / `待补采` / `不可判定`，不能用商品整体指标、账户汇总、0 值、fixture 或半个月前旧口径替代商品级真实数据。
- **禁止**把 `fixtures/sample_input.json` 当运行数据源；fixture 只用于平台 schema 自检和测试
- **禁止**直接 `import requests`/`subprocess.run` 去访问业务系统；业务数据采集统一走 SkillForge SDK。也不要 `import smtplib`、`import dingtalk*`
- **禁止**采集失败静默返回 `{}` / `[]` / `None`：必须抛出明确错误，或在输出里写入 `data_errors` 并让报告标明数据源失败
- **禁止**读 DB / 读敏感系统文件：运行环境是沙箱，远端 OpenClaw / AIClaw 可能在内网根本访问不了 SkillForge server
- **禁止**后台 thread / 异步任务：函数返回即结束
- **reports 是默认能力**：`main.py` 必须始终返回 `reports`；没有报告内容时也要返回空数组，不能把报告能力做成可关闭开关

标准采集骨架：

```python
import os
from skillforge_sdk import SkillForge


def collect_inputs(payload: dict) -> dict:
    if payload.get("生意参谋_店铺排行榜"):
        return payload
    sf = SkillForge(os.environ.get("SKILLFORGE_SKILL_ID", "your-skill-id"))
    payload = dict(payload)
    payload["生意参谋_店铺排行榜"] = sf.fetch_api(
        "https://sycm.taobao.com/...",
        page_url="https://sycm.taobao.com/...",
    )
    return payload
```

平台运行 `main.py` 时会自动把受信任的 SDK 目录加入 `PYTHONPATH`。
SDK 真源文件位于主仓库 `app/skill_runtime_sdk/skillforge_sdk.py`，不是 `skills-repo/_shared/`。
若 Skill 目录里已有旧 `scripts/skillforge_sdk.py`，发布同步会用平台受信任 SDK 覆盖；AI 不得复制旧 SDK 或修改本地 SDK 来绕过平台 MCP Gateway。

**分发谁来做**：SkillForge server 拿到你 return 的 dict 后，会自动：
- 看 `output.todos` → 写入 `/todos` 待办中心 + SLA 催办
- 看 `output.reports` → 写入报告中心 / inbox（是否外发由平台策略处理）
- 看 `output.adapter` (contract 声明) → 存 CSV / 回调 webhook

**待办分发硬规则**：Skill 产出的 `todos` 只能先进入 SkillForge 平台待办中心，不允许在运行完成时直接推钉钉。只有平台待办被人工点击通过后，`kind="dispatch"` 的子任务才会由平台推送到执行人钉钉。

**你只需要保证采集真实输入、完成分析，并让 return dict 结构符合下面契约。所有发送 / 推送 / 入库 不归 main.py 管。**

---

## 1. 输出形态判定（先判断，再写）

| SOP 特征 | 形态 | return 必须含 |
|---------|------|-------------|
| 有「人做事」的动作（整改/审批/回复差评/调整预算/排查） | 待办 | `todos` |
| 有「只读通知」（日报/周报/推送给部门/同步给老板） | 报告 | `reports` |
| 两者都有（日报给老板 + 整改给运营） | 待办+报告 | `todos` **和** `reports` |
| 纯数据产出（csv / 给下游 skill 消费 / 导出文件） | 数据 | 业务字段 + contract.output.adapter=`csv_file`/`json_webhook` |

> 业务字段（中文，如 `整改建议清单` / `日报正文`）**无论哪种形态都保留**—— 它是平台卡片 / 前端看板的渲染源。

---

## 2. TodoSpec — 待办契约

每条记录都要对应到一条 todo：

```json
{
  "kind": "review" | "dispatch",
  "title": "<≤200 字简短标题，含关键标识>",
  "summary": "<2-3 句，问题/建议>",
  "payload": { <运营可读的决策卡片，不是原始 JSON 快照> },
  "reviewer_role": "biz_owner" | "operator" | "ai_engineer" | "admin" | "director",
  "reviewers": ["<user_id>", ...],
  "sla_hours": 24,
  "decision_mode": "any_of" | "all_of" | "independent"
}
```

**字段含义**：

| 字段 | 必填 | 说明 |
|------|-----|------|
| `kind` | ✅ | `review`=审核确认型 / `dispatch`=派发执行型。人工动作通常 `dispatch` |
| `title` | ✅ | ≤200 字，含关键标识（如 `TM001 免费流下滑`） |
| `summary` | 推荐 | 问题/建议 2-3 句描述 |
| `payload` | 推荐 | 只放运营决策所需字段（item_id / 标题 / 优先级 / 关键指标 / 分析依据 / 建议动作 / 禁止动作 / 推荐决策 / 数据来源）；禁止把完整 input_snapshot、完整 output_result、页面原始响应或外部平台原始 JSON 塞进去 |
| `reviewer_role` | 二选一 | 接收角色组（biz_owner / operator / ...）|
| `reviewers` | 二选一 | 具体 user_id 列表，从 payload 传入 |
| `sla_hours` | 可选 | 超期钉钉催办，默认 24 |
| `decision_mode` | 可选 | 多人审批语义，默认 `any_of` |

**reviewer 怎么填**：
- SOP 说"推给运营负责人" → `reviewer_role: "biz_owner"`
- SOP 说"推给张三" → payload 里传 `reviewer_user_id="zhangsan"`，然后 `reviewers=[input.reviewer_user_id]`
- 不确定 → `reviewer_role` 设为该 skill 的 department 对应的默认角色，让平台兜底

**映射规则**：业务字段有 N 条记录 → `todos` 也必须有 N 条对应。

**运营可读 payload 硬规则**：
- payload 必须能让业务人员不看 debug JSON 也能回答“通过/驳回/派发给谁做什么”。
- 如果业务记录是商品/客户/订单/广告计划，优先输出“一对象一卡”：`priority`、`type`、`object_id/object_name`、`data_overview`、`analysis_basis`、`operation_actions`、`data_sources`、`recommended_decision`、`approval_question`、`forbidden_actions`。
- `payload.input` / `payload.output` 如需提供，只能是小摘要：`input={对象ID, 时间, 口径}`，`output={summary, recommendation, reasoning, suggestions, metrics}`。不要放完整执行入参或完整返回结果。
- `tasks[].deadline` 必须是 ISO 时间字符串（如 `2026-04-23T23:59:59`），不要写“今天/明天/本周五”。
- 调试证据放在报告 `payload`、`data_proofs` 或 execution debug 链路中，不放在待办决策上下文首屏。
- 电商竞品/价格类待办必须展示同价位口径：商品金额、上下浮动 10% 的价格区间、同价位竞品数量和样例竞品；缺商品金额时作为数据缺口呈现。

### 示例

```python
def main(payload: dict) -> dict:
    items = analyze(payload)  # 业务逻辑
    todos = [
        {
            "kind": "dispatch",
            "title": f"{it['item_id']} {it['维度']}问题",
            "summary": it['问题'] + '；' + it['建议动作'],
            "payload": {
                "object_id": it["item_id"],
                "priority": it["优先级"],
                "data_overview": it["关键指标"],
                "analysis_basis": it["依据"],
                "operation_actions": [it["建议动作"]],
                "recommended_decision": it["推荐决策"],
            },
            "reviewer_role": "biz_owner",
            "sla_hours": 24,
            "tasks": [{"content": it["建议动作"], "deadline": it["截止时间ISO"]}],
        }
        for it in items
    ]
    return {
        "整改建议清单": items,  # 业务字段（给平台卡片看的）
        "todos": todos,          # 给平台登记待办
    }
```

---

## 3. ReportSpec — 报告契约

每次执行产出 0 或多条 report。多数场景只有 1 条（整份日报就一份）。

```json
{
  "channel": "dingtalk_card" | "dingtalk_markdown" | "email" | "feishu",
  "title": "<≤80 字标题，含日期/关键指标>",
  "summary": "<2-3 句核心结论，平台卡片首屏展示>",
  "content_markdown": "<完整 markdown 正文>",
  "recipients": {
    "users": ["<user_id>", ...],
    "roles": ["biz_owner", ...],
    "departments": ["<中文部门名>", ...]
  },
  "payload": { <结构化数据，给前端看板动态渲染> }
}
```

**字段含义**：

| 字段 | 必填 | 说明 |
|------|-----|------|
| `channel` | ✅ | 发送渠道；必须是枚举值之一，禁止自创 |
| `title` | ✅ | ≤80 字，含日期 / 关键数字 |
| `summary` | ✅ | 平台卡片首屏 2-3 句，要自包含 |
| `content_markdown` | 可选 | 点开卡片查看的完整报告 |
| `recipients` | ✅ | `users` / `roles` / `departments` 至少一个非空 |
| `payload` | 可选 | 结构化数据（表格行 / 指标卡）供前端渲染 |

**recipients 怎么填**：
- SOP 说"推给电商运营部负责人" → `{"roles": ["biz_owner"], "departments": ["电商运营部"]}`（平台会取交集）
- SOP 说"推给张三、李四" → payload 里放具体 user_id 数组 → `{"users": input.recipient_user_ids}`
- SOP 说"推给全公司" → 不存在此场景，拒绝创建（让用户缩小范围）

### 示例

```python
def main(payload: dict) -> dict:
    report_md = generate_daily_report(payload)  # 业务逻辑
    return {
        "日报正文": report_md,  # 业务字段
        "reports": [
            {
                "channel": "dingtalk_card",
                "title": f"{today} 店铺下滑日报",
                "summary": f"今日 top5 下滑链接：{', '.join(top5_ids)}",
                "content_markdown": report_md,
                "recipients": {"roles": ["biz_owner"], "departments": ["电商运营部"]},
                "payload": {"top5_items": top5_items, "市场对比": market_diff},
            }
        ],
    }
```

---

## 4. 两者都要（常见组合）

"日报给老板看数据 + 具体整改建议给运营去做"：

```python
return {
    "日报正文": report_md,
    "整改建议清单": items,
    "reports": [{...}],   # 给老板
    "todos": [{...}, ...] # 给运营
}
```

---

## 5. 禁止项（违反 review 会拒）

- ❌ **只写业务字段不写 `todos`/`reports`**：SkillForge 看不见，无法入库/推送
- ❌ **把 `reports` 关掉或省略**：报告是默认能力，`main.py` 仍要返回 `reports: []`
- ❌ **main.py 里 `import requests` / `dingtalk_sdk` 调外网**：沙箱禁止 + 内网也可能没网
- ❌ **`channel` 自创值**（比如 `wechat` / `sms`）：只支持上面枚举的 4 种
- ❌ **`recipients` 三项全空**：不知道发给谁
- ❌ **「整改建议清单」只发 `reports` 不发 `todos`**：执行动作退化为只读信息，运营不会响应
- ❌ **用 `pass` 或固定字面量**占位：main.py 必须把 SOP 里的关键判断真实写成代码

---

## 6. 判定流程（先想清再动手）

1. 通读 SOP，划出所有动作动词
2. 对每个动词判：是"只读通知"还是"需要人做事"？
3. 如果两者都有，列好分类：哪些进 reports，哪些进 todos
4. 业务字段保留原文称呼（`整改建议清单` / `日报正文` / `诊断报告`）
5. 写 main.py，`return` 时三类字段（业务 + todos + reports）按需组合

**一句话**：你负责拿到真实输入并声明产出了什么，平台负责发出去。

---

## 7. 输出结构契约（硬约束，平台运行时校验）

**自 2026-04 起**：`contract.json` 必须声明机器可校验的 `output_schema`（JSON Schema Draft-07），并提供 `fixtures/sample_input.json` 样例输入。平台在 3 个阶段都会跑 schema 校验：

| 阶段 | 动作 | 失败处置 |
|------|------|---------|
| **生成时（verify_schema milestone）** | 真跑 `python3 scripts/main.py < fixtures/sample_input.json`，用 output_schema 校验 stdout JSON | 返工让 Agent 补字段，最多 3 次 |
| **finalize 闸门** | 复跑 `python3 scripts/main.py < fixtures/sample_input.json`，再次校验 output_schema + SKILL.md §输出定义 ↔ required ↔ return keys 三处一致 | 抛 `CONTRACT_DRIFT` 400，阻塞发布 |
| **runtime 校验** | 每次 OpenClaw 跑完后对比实际 output vs schema | warn-only，写 `contract_drifts` 表，提醒 owner |

### 7.1 `contract.json` 必须新增两个字段

```json
{
  "goal": "...",
  "input": [...],
  "output": {
    "adapter": "dingtalk_card",
    "recipient": "...",
    "schema": {"<旧字段，自然语言说明>": "保留，给人看"}
  },
  "output_schema": {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["字段A", "字段B", "reports", "todos"],
    "properties": {
      "字段A": {"type": "string"},
      "字段B": {"type": "array", "items": {"type": "object"}},
      "reports": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["channel", "title", "summary", "recipients"],
          "properties": {
            "channel": {"type": "string", "enum": ["dingtalk_card", "dingtalk_markdown", "email", "feishu"]},
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "content_markdown": {"type": "string"},
            "recipients": {"type": "object"},
            "payload": {"type": "object"}
          }
        }
      },
      "todos": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["kind", "title"],
          "properties": {
            "kind": {"type": "string", "enum": ["review", "dispatch"]},
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "payload": {"type": "object"},
            "tasks": {"type": "array", "items": {"type": "object"}},
            "reviewers": {"type": "array", "items": {"type": "string"}},
            "reviewer_role": {"type": "string"},
            "sla_hours": {"type": "number"},
            "decision_mode": {"type": "string", "enum": ["any_of", "all_of", "independent"]},
            "callback": {"type": "object"}
          }
        }
      }
    }
  }
}
```

**和旧 `output.schema`（自然语言说明）的关系**：两者并存。`output.schema` 给人读，`output_schema` 给机器校。平台会按 `output_schema.required` 自动收口 `SKILL.md` 的 `## 输出定义`，不要把它当成第二份手写 schema。

### 7.2 `fixtures/sample_input.json`（新增第 7 个文件）

一份能让 `main.py` 完整跑通的**最小样例输入**。不是 test_cases 里的局部数据，必须是端到端能跑出合法输出的完整 payload。它只用于 schema 自检和回归测试，不是运行时数据源；能拿到真实 API 样本时用真实字段裁剪，拿不到时写结构样例，并在 `intent.md` 标明运行时由 SDK 采集。

```json
{
  "date": "2026-04-14",
  "业务字段1": ["item1", "item2"],
  "业务字段2": {}
}
```

平台会跑：`python3 scripts/main.py < fixtures/sample_input.json`，把 stdout 当 JSON 解析后对比 `output_schema`。

### 7.3 jsonschema 迷你速查（LLM 写歪时可照抄）

| 业务场景 | schema 写法 |
|---------|----------|
| 字符串字段 | `{"type": "string"}` |
| 数字字段（含范围） | `{"type": "number", "minimum": 0, "maximum": 100}` |
| 枚举字段 | `{"type": "string", "enum": ["R1","R2","R3"]}` |
| 数组字段（元素 object 且至少 1 条） | `{"type": "array", "minItems": 1, "items": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}}` |
| 嵌套 object | `{"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}` |

**写 schema 自检清单**：
1. 顶层必须 `"type": "object"`
2. 必须有 `"required": [...]` 且至少 1 项（否则平台 lint 会报"过松"）
3. 业务字段名要和 `main.py` return 的 dict key **一字不差对齐**（中文字段也要对齐）
4. `todos` / `reports` 如果 main.py 会返回，也要进 properties 和 required
5. 不要用 `{"type": "any"}`（会被 lint 报过松），宁可省略不写也比写 any 好

### 7.4 禁止

- ❌ `contract.json` 没有 `output_schema`
- ❌ `fixtures/sample_input.json` 缺失或非合法 JSON
- ❌ `scripts/main.py` 读取 `fixtures/sample_input.json` 当线上数据
- ❌ `contract.input` 声明 `source=datasource`，但 `scripts/main.py` 只消费 payload、没有 SkillForge SDK 采集逻辑
- ❌ `output_schema` 没有 `required` 字段（或 required 为空数组）
- ❌ `output_schema` 的 properties 与 `main.py` return 的 key 对不上（必返工）
- ❌ 把 `output_schema` 写成自然语言 dict（如 `{"诊断报告": "markdown 正文"}`）—— 必须是标准 jsonschema 语法
