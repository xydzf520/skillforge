# 生产级 Skill 编写流程

> 日期：2026-04-30
> 目的：把 Skill 编写从“AI 写脚本”升级为“有数据合同、指标口径、来源证明、测试门禁和持续优化闭环的生产流程”。

## 1. 核心结论

更好的 Skill 编写，不是让 AI 写更多代码，而是让 AI 在更强的约束下写更少、更准、更可维护的代码。

生产级 Skill 应该满足：

- 业务目标清楚：只解决一个明确的业务决策。
- 数据来源可证明：每个关键指标都有 source contract 和 provenance。
- 指标口径统一：不靠自然语言猜“搜索访客”“免费访客”“转化率”这些概念。
- 代码结构可维护：采集、归一化、规则、报告、来源证明分层。
- 测试可回放：有正常、缺失、边界、错误来源、多商品真实样例。
- 发布有门禁：contract、source contract、fixture、真实数据 smoke 全部通过后才能发布。
- 用户纠错能沉淀：每次纠错都变成 fixture、反例、规则或来源合同。

## 2. 好 Skill 的标准结构

不建议只保留 `SKILL.md + main.py`。建议生产级 Skill 至少包含：

```text
skill/
  SKILL.md
  contract.json
  source_contract.yaml
  metric_registry.yaml
  scripts/
    main.py
    collect.py
    normalize.py
    rules.py
    report.py
    provenance.py
  tests/
    fixtures/
      normal_case.json
      missing_required_metric.json
      wrong_source_realtime.json
      anti_bot_html.json
      multi_item_case.json
    test_collect.py
    test_normalize.py
    test_rules.py
    test_contract.py
    test_source_contract.py
  README.md
```

职责边界：

- `SKILL.md`：说明业务意图、适用场景、决策逻辑、不可用场景。
- `contract.json`：定义输入输出字段、类型、单位、必填项。
- `source_contract.yaml`：定义每个指标必须来自哪里、是否允许 fallback。
- `metric_registry.yaml`：定义指标 ID、业务名称、页面节点、粒度、单位、禁用兜底来源。
- `scripts/main.py`：只做编排，不堆采集和业务细节。
- `scripts/collect.py`：只负责调用 ResourceBroker / MCP / API。
- `scripts/normalize.py`：只负责把外部返回转成统一字段。
- `scripts/rules.py`：只放业务判断规则和阈值逻辑。
- `scripts/report.py`：只负责生成输出摘要和结构化报告。
- `scripts/provenance.py`：只负责来源证明、source_node 校验和 fallback 检查。
- `tests/fixtures/`：沉淀真实案例、异常案例、用户纠错案例。

## 3. 编写流程：先合同，后代码

错误流程：用户说需求后，AI 直接开始写代码。

推荐流程：

```text
需求澄清
  -> 数据与指标确认
  -> 来源合同确认
  -> 输出合同确认
  -> 目录与模块生成
  -> 代码实现
  -> 静态 lint
  -> contract 校验
  -> source contract 校验
  -> fixture 测试
  -> 多商品真实数据 smoke
  -> 人工确认
  -> git / Docker / 运行节点发布
```

每一步的产物：

| 阶段 | 必须产物 | 不通过时 |
|---|---|---|
| 需求澄清 | 目标、适用场景、禁止场景 | 继续追问，不写代码 |
| 数据确认 | 指标清单、数据产品、页面节点 | 不允许调用真实平台 |
| 来源合同 | `source_contract.yaml` | 不允许输出业务结论 |
| 输出合同 | `contract.json` | 不允许生成 `main.py` |
| 代码实现 | 分层脚本 | 不允许把全部逻辑堆进 `main.py` |
| 测试 | fixtures + tests | 不允许发布 |
| 真实验证 | 多商品 smoke 结果 | 不允许推运行节点 |

## 4. 数据来源必须前置绑定

电商类 Skill 最常见错误是指标口径混用。例如“免费搜索/免费访客环比”和“搜索/免费转化环比”必须来自搜索节点日数据时，不能让 Skill 自己猜 URL 或用实时数据兜底。

建议在 `source_contract.yaml` 中声明：

```yaml
version: 1
products:
  - product_id: sycm.item_flow_daily.v1
    owner: data-platform
    freshness_sla_hours: 30
    metrics:
      - metric_id: sycm.item.search.free_visitors.day
        label: 搜索节点免费访客（日）
        required_source:
          platform: sycm
          page: item_archives
          node: search
          granularity: day
        fallback_allowed: false
        provenance_required: true
        forbidden_fallback:
          - realtime_search_visitors
          - market_snapshot_visitors
          - store_total_free_visitors

      - metric_id: sycm.item.search.free_conversion_rate.day
        label: 搜索节点免费转化率（日）
        required_source:
          platform: sycm
          page: item_archives
          node: search
          granularity: day
        fallback_allowed: false
        provenance_required: true
```

含义：

- 必须来自 `item_archives` 页面搜索节点日数据。
- 不允许拿实时搜索访客兜底。
- 不允许拿市场排行快照兜底。
- 不允许拿全店免费访客兜底。
- MCP 或 ResourceBroker 返回缺少 `source_node` 时，Skill 必须失败，而不是生成错误报告。

## 5. 指标字典：避免同名不同义

建议每个业务域维护标准指标字典。Skill 不直接使用“访客”“转化率”这类模糊词，而是绑定 `metric_id`。

示例：

```yaml
metrics:
  sycm.item.search.free_visitors.day:
    label: 搜索节点免费访客（日）
    platform: sycm
    page: item_archives
    node: search
    granularity: day
    unit: person
    allowed_connectors:
      - tmall_mcp_browser
      - sycm_api
    forbidden_fallback:
      - realtime_search_visitors
      - market_snapshot_visitors
      - store_total_free_visitors

  alimama.item.search_plan.summary.total_cost.day:
    label: 付费计划明细合计花费（日）
    platform: alimama
    page: manage_search
    row_scope: summary_total
    granularity: day
    unit: yuan
    required_row: total
```

收益：

- AI 写 Skill 时选择标准指标，不凭自然语言猜。
- 测试可以按 `metric_id` 校验来源和单位。
- 多个 Skill 可以复用同一套指标定义。
- 后续 MCP / API / ResourceBroker 可以按指标自动路由。

## 6. Skill 代码分层原则

### 6.1 `main.py` 只做编排

```python
from scripts.collect import collect_inputs
from scripts.normalize import normalize_metrics
from scripts.rules import evaluate
from scripts.report import build_report
from scripts.provenance import assert_sources


def run(params):
    raw = collect_inputs(params)
    normalized = normalize_metrics(raw)
    assert_sources(normalized)
    decision = evaluate(normalized, params)
    return build_report(decision, normalized)
```

`main.py` 不应该：

- 直接拼平台 URL。
- 直接处理 Cookie / API Key。
- 堆大量字段映射。
- 混合采集、判断和报告。
- 使用和业务无关的兜底数据。

### 6.2 `collect.py` 只调能力，不持有凭证

不推荐：

```python
sf.fetch_api("https://sycm.taobao.com/cc/item_archives?...itemId=800559674590")
```

推荐：

```python
sf.get_data_product(
    "sycm.item_flow_daily.v1",
    params={"item_id": item_id, "date": target_date, "node": "search"},
    require_source="search_node_daily",
    fallback_allowed=False,
)
```

### 6.3 `rules.py` 只放业务判断

- 阈值必须来自参数或 policy，不写 magic number。
- 每个规则要有可解释原因。
- 多规则冲突时要有优先级。
- 兜底规则必须放最后。

## 7. 测试必须覆盖来源错误

生产级 Skill 至少覆盖这些测试：

| 类型 | 目的 |
|---|---|
| 正常场景 | 标准输入能输出正确结论 |
| 缺字段 | 必需指标为空时提示清楚 |
| 来源错误 | 拿到错误 `source_node` 时必须失败 |
| 边界值 | 阈值等于边界时输出稳定 |
| 多商品 | 至少 3-5 个商品验证，不只测一个 itemId |
| 反验证 | 故意给实时数据，确认不会被当作日数据 |
| 平台异常 | 反验证 / 登录失效 / anti-bot HTML 能识别 |
| 合计行错误 | 付费计划明细必须用底部合计，不用单模块行 |

建议 fixture：

```text
tests/fixtures/source_correct_search_daily.json
tests/fixtures/source_wrong_realtime.json
tests/fixtures/missing_paid_summary_total.json
tests/fixtures/rank_sorted_by_wrong_metric.json
tests/fixtures/anti_bot_html.json
tests/fixtures/item_not_in_top300_yesterday.json
```

示例断言：

```python
def test_reject_wrong_source_node(load_fixture):
    data = load_fixture("source_wrong_realtime.json")
    with pytest.raises(SourceContractError):
        assert_sources(data)
```

## 8. AI 编写 Skill 的多 Agent 分工

建议由 SkillForge 的 Skill Authoring Orchestrator 管理可替换的编程 Harness。

角色拆分：

| Agent | 职责 | 是否写文件 |
|---|---|---|
| 需求分析 Agent | 提炼目标、输入、输出、禁止场景 | 否 |
| 数据源 Agent | 选择 DataProduct / MCP / API / 指标 | 否 |
| 口径 Agent | 检查指标定义、来源、fallback | 否 |
| 代码 Agent | 实现模块化脚本 | 是 |
| 测试 Agent | 生成 fixtures 和测试 | 是 |
| 验证 Agent | 找冲突、漏判、错误来源、字段缺失 | 否 |
| 发布 Agent | 检查 git、Docker、运行节点 | 可执行命令 |

规则：

- 不让多个 Agent 同时改同一个文件。
- 只读 Agent 先探索，主 Agent 统一合并方案。
- 写文件前必须有合同和测试计划。
- 发布前必须有测试证据和回滚方案。

## 9. Workbench 需要展示 Skill 编写证据

更好的 Skill 编写工具不应该只有聊天窗口。建议 Workbench 增加：

- 需求区：用户原始需求、AI 提炼目标、禁止场景。
- 数据区：DataProduct、metric_id、source_node、fallback 策略。
- 规则区：决策树、阈值、优先级、兜底。
- 代码区：文件 diff、模块职责、变更摘要。
- 测试区：fixtures、单测、真实数据 smoke、多商品验证。
- 证据区：MCP 工具调用、source_url、source_node、date_range、credential_ref、fallback_used。
- 发布区：git 版本、Docker 版本、运行节点状态、回滚命令。

每次工具调用至少展示：

- 调了哪个 MCP / DataProduct。
- 参数是什么，例如 item_id、date、node。
- 返回的 source_node 是什么。
- 是否 fallback。
- 是否遇到验证。
- 是否使用缓存。
- 输出摘要和原始日志入口。

## 10. 用户纠错要进入持续优化闭环

用户每次指出“这个数据不对”“这个排名口径错了”“这里不能兜底”，系统都应该沉淀为资产。

闭环：

```text
用户纠错
  -> 归因：规则错 / 来源错 / 字段错 / 页面错 / 测试缺失
  -> 生成 fixture
  -> 补 source contract 或 metric registry
  -> 补规则或归一化逻辑
  -> 补测试
  -> 回放历史案例
  -> 发布新版本
```

示例：

用户说“搜索转化不对，不是这个页面的数据”。系统应生成：

```text
tests/fixtures/wrong_search_conversion_source.json
```

并补一条测试：

```text
当 source_node != search_node_daily 时，禁止输出搜索/免费转化环比。
```

这样同类错误不会反复出现。

## 11. 编程 Harness 接入建议

公开版先移除旧运行时，再按 [替换评估](../../public/HARNESS_REPLACEMENT.md) 接入验收：

1. 定义 `CodingAgentProvider` 契约，优先验证 OpenCode 适配器；新适配器验收前保持不可用。
2. 拆 `skill_creation_runner.py`：
   - `interview.py`
   - `contract_builder.py`
   - `source_contract_builder.py`
   - `implementation_builder.py`
   - `test_builder.py`
   - `verifier.py`
   - `publisher.py`
3. Workbench 增加工具调用卡片、权限请求、阶段进度和 source proof。
4. Skill 创建时强制输出 `source_contract.yaml`。
5. MCP 返回强制带 `source_node`、`source_url`、`date_range`、`fallback_used`。
6. 增加多商品真实验证阶段。
7. 将用户纠错自动转成 fixture / test / source contract 修正。

## 12. 推荐优先级

| 优先级 | 动作 | 价值 | 风险 |
|---|---|---:|---:|
| P0 | 引入 `source_contract.yaml` | 直接解决口径混用 | 中 |
| P0 | 建 Metric Registry | 统一指标定义 | 中 |
| P0 | MCP 输出 source proof | 让 Skill 能硬校验来源 | 中 |
| P1 | 拆 `skill_creation_runner.py` | 降低维护难度 | 中高 |
| P1 | Workbench 工具卡片 / 证据区 | 提升可观察性 | 中 |
| P1 | 多商品真实验证门禁 | 降低线上误判 | 中 |
| P2 | 多 Agent 分工 | 提升复杂 Skill 质量 | 中高 |
| P2 | 接第二 provider 做 shadow run | 选型更稳 | 中 |

建议先从天猫 / 阿里妈妈两个高频 Skill 做样板，形成标准后再推广到其他业务域。
