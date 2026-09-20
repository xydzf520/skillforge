"""
Skill 创建运行器（一步到位的 task-contract + 完整 skill 生成）。

输入：用户的一段中文 SOP（自然语言业务需求）。
输出：
  - 流式 CreationEvent（前端 WS 直接转发，用于「全屏进度页」展示）
  - 完成时 yield 一个 SKILL_READY 事件，附带解析好的 contract dict + 完整 skill 文件 dict

工作流：
  1. 创建临时 scratch 目录 /tmp/sf_skill_creation/<draft_id>/
  2. spawn aiclawcode 子进程（work_dir = scratch），permission_mode = bypassPermissions
  3. 注入严格 system prompt（含 6 条强约束 + JSON Schema）
  4. 发送用户 SOP 作为 user message
  5. 流式收事件 → 翻译成 CreationEvent → yield
     - 检测到 contract.json 写完：立即 yield CONTRACT_READY（前端可以开始预览只读内容）
     - 检测到其它文件写完：yield FILE_WRITTEN
     - 检测到 result 事件：从 scratch 读 6 个文件，yield SKILL_READY
  6. 失败时（spawn 失败 / 超时 / 必需文件缺失 / JSON parse 失败）→ 自动重试 1 次
  7. 重试也失败 → yield ERROR，**保留 scratch dir** 供 debug

调用方负责：
  - 确认成功后把 scratch 目录的文件 cp 到 skill_repo/<final_skill_id>/ + git commit
  - 失败时把 scratch 路径写到 SkillStudioDraft.error_detail
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from loguru import logger

from app.coding_agent.subprocess_session import SubprocessSession
from app.coding_agent.runtime_availability import RUNTIME_MESSAGE, RUNTIME_UNAVAILABLE, runtime_status
from app.coding_agent.session_service import SessionService


# ─────────────────────────────────────────────────────────
# 事件协议（前端 WS 直接收这些）
# ─────────────────────────────────────────────────────────

class CreationEventType:
    """前端 WS 协议事件类型。值是字符串，前端用 switch case 分发。"""
    STARTED = "started"             # 子进程已 spawn，attempt=1|2
    MILESTONE = "milestone"         # 6 个固定 milestone 之一进入新状态
    THINKING = "thinking"           # 助手思考的 text 块（流式追加到右下角日志）
    TOOL_CALL = "tool_call"         # 助手调用工具（Write/Read/Bash 等）
    TOOL_RESULT = "tool_result"     # 工具返回结果
    CONTRACT_READY = "contract_ready"  # contract.json 写完，附 contract dict（左下角预览用）
    SKILL_READY = "skill_ready"     # 全部 6 文件写完，附 contract + 6 文件 dict
    RETRY = "retry"                 # 第一次失败，准备重试
    ERROR = "error"                 # 终态错误（重试也失败）
    DONE = "done"                   # 流结束


# 7 个固定 milestone（前端顶部步骤条用）
MILESTONES = [
    "identify_intent",       # 识别意图
    "draft_contract",        # 起草任务合同（contract.json）
    "write_skill_md",        # 写 SKILL.md
    "write_main_py",         # 写 scripts/main.py
    "write_tests",           # 写 tests/test_main.py
    "verify_import",         # 跑测试自验证
    "verify_schema",         # 跑 main.py < sample_input 校验 output_schema
]

MILESTONE_LABELS = {
    "identify_intent": "识别意图",
    "draft_contract": "起草任务合同",
    "write_skill_md": "写 SKILL.md",
    "write_main_py": "写 scripts/main.py",
    "write_tests": "写 tests/test_main.py",
    "verify_import": "跑测试自验证",
    "verify_schema": "跑契约 schema 自检",
}

SKILL_CREATION_MAX_TOOL_USE_CONCURRENCY = 2
RATE_LIMIT_RETRY_DELAYS = (15, 30)


@dataclass
class CreationEvent:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.payload}


# ─────────────────────────────────────────────────────────
# System prompt（严格版，含 6 条强约束 + 6 文件契约）
# ─────────────────────────────────────────────────────────

SKILL_CREATION_SYSTEM_PROMPT = """你正在为 SkillForge 创建一个新的 Skill。

# 任务
用户会给你一段中文业务 SOP（可能含会议纪要 / 接口文档片段）。基于这段 SOP 生成一个**完整的、可运行的** Skill 目录，包含 7 个文件：

```
./contract.json                (json，task-contract 抽取结果，必须最先写，含机器可校验 output_schema)
./SKILL.md                     (markdown，含 frontmatter + 决策步骤 + 输出定义 + 测试用例)
./intent.md                    (markdown，6 个章节：原始需求 / 任务理解 / 输入依赖 / 所需权限 / 失败策略 / 回归用例摘要)
./policy.yaml                  (yaml，version / trigger / output / permissions / risk / failure_policy)
./scripts/main.py              (python，从 stdin 读 JSON 写 stdout JSON；datasource 缺失时按 MCP 优先策略验证后，用 SkillForge SDK 采集真实数据)
./tests/test_main.py           (python，pytest 风格，至少 3 条覆盖原文真实业务场景的测试)
./fixtures/sample_input.json   (json，让 main.py 端到端跑通的最小样例输入；只给平台校验 output_schema，不能当运行数据源)
```

# contract.json 严格 schema
{
  "goal": "<一句话目标。必须覆盖：周期性(如有) + 多维归因(如有) + 输出形式 + 闭环动作(如有)>",
  "trigger": {
    "type": "cron"|"manual"|"webhook"|"event",
    "expression": "<cron 表达式，仅在 type=cron 时给>",
    "description": "<触发场景中文描述。如果用户列出了多个手动触发场景，必须全部保留，用①②③④分隔，禁止合并成一句模糊话>"
  },
  "input": [
    {"name": "<具体数据源名，必须按原文 1:1 对应，禁止泛化>",
     "type": "json"|"csv"|"string",
     "source": "datasource"|"user"|"previous_skill",
     "required": true|false}
  ],
  "output": {
    "adapter": "dingtalk_card"|"email"|"slack"|"json_webhook"|"csv_file",
    "recipient": "<具体收件人>",
    "schema": {<可选：每个输出字段的中文说明，给人看>}
  },
  "output_schema": {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["<必须包含的 key 列表，必须和 main.py return dict 一字不差对齐>", "reports"],
    "properties": {
      "<字段名>": {"type": "string|number|array|object|boolean"},
      "todos": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["kind", "title"],
          "properties": {
            "kind": {"type": "string", "enum": ["review", "dispatch"]},
            "title": {"type": "string", "maxLength": 200},
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
      },
      "reports": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["channel", "title", "summary", "recipients"],
          "properties": {
            "channel": {"type": "string", "enum": ["dingtalk_card", "dingtalk_markdown", "email", "feishu"]},
            "title": {"type": "string", "maxLength": 80},
            "summary": {"type": "string"},
            "content_markdown": {"type": "string"},
            "recipients": {"type": "object"},
            "payload": {"type": "object"}
          }
        }
      }
    }
  },
  "permissions": [
    {"action": "read_data"|"write_data"|"send_message"|"external_api",
     "target": "<具体>",
     "reversible": true|false}
  ],
  "risks": {
    "level": "R1"|"R2"|"R3",
    "data_classification": "public"|"internal"|"confidential",
    "department": "<部门名>"
  },
  "test_cases": [
    {"name": "<贴合该业务的具体场景名>",
     "input": {<场景数据>},
     "expected_keywords": [<关键词>]}
  ]
}

# SKILL.md 格式
```markdown
---
name: <skill 名>
department: <部门>
trigger_type: cron|manual|webhook|event
risk_level: R1|R2|R3
description: <一句话描述>
---

## 目的
<一段说明>

## 决策步骤
- **step_1**: <步骤名>
  - 分支条件: <条件>
  - 结论: <conclusion>
  - 动作: <action>
  - next_step: step_2 | 结束

## 输出定义
- <字段>: <说明>

## 测试用例
1. **<场景名>**: <说明>
```

# 强约束（违反则视为输出无效）
1. **input 数据源命名禁止泛化**：用户原文写"生意参谋-商品360"，你必须写"生意参谋_商品360"，禁止写"店铺数据"或"商品数据"
2. **trigger 多场景必须全保留**：cron + N 个手动场景必须全部写出来，用①②③④分隔
3. **test_cases 禁止通用占位**：禁止使用 "normal" / "empty" / "error" / "datasource_failure" / "happy_path" 这类通用名。每条 test_case 必须基于用户原文的真实业务场景
4. **scripts/main.py 必须真的能 import json/sys 跑通**，函数签名 `def main(payload: dict) -> dict`，从 stdin JSON 读 payload，写 stdout JSON
5. **scripts/main.py 必须把 SOP 里的关键判断真的写进代码**（例如下滑系数计算、阈值比较、多维度归因），不能只写 `pass` 或返回固定字面量。不能在函数里 hardcode 业务数据
6. **外部/平台数据必须有真实采集路径**：只要 `contract.input[].source == "datasource"`、`permissions[].action == "external_api"`，或 SOP/SKILL.md/contract 提到业务 API URL，`scripts/main.py` 必须实现 `collect_inputs(payload)`：payload 已带真实数据时直接分析；payload 缺失时先看当前会话已注入的 MCP 工具是否覆盖该数据源；有匹配 MCP 时必须先调用它验证真实样本和字段结构，再把可复现的 URL/page_url/参数/parser 写成 SkillForge SDK 运行时代码；没有匹配 MCP 时再用 `skillforge_sdk.SkillForge.fetch_api/extract/capture_apis/explore` 自探索。禁止只靠 `fixtures/sample_input.json` 或写死假数据。
7. **运行时禁止读取 fixture**：`fixtures/sample_input.json` 只用于平台 schema 自检和测试；`main.py` 不得读取这个文件，也不得把 fixture 内容当线上数据。
8. **tests/test_main.py 至少 3 条测试**，每条对应 SOP 的真实异常场景，用 `from main import main` 然后 `assert` 关键字段
9. **send_message 的 reversible 必须为 false**
10. **不要凭空增加用户没提的字段**（如"触发条件配置"这种幻觉 input）
11. **goal/description 必须含闭环动作**（如果原文提到了"形成闭环""下次验证"）
12. **goal/trigger/input 等所有字段内容必须严格来自用户原文**，不得根据 URL 域名、行业经验、常识推断用户没有提到的业务对象或平台名。如果用户只写了"抓取研究报告"，goal 就写"抓取研究报告"，不要自己加"抖音/快手/某某平台"
13. **contract.json 必须含 `output_schema`（JSON Schema Draft-07）**：顶层 type=object、至少 1 个 required 字段、properties 的 key 与 main.py `return` 的 dict key 一字不差对齐。禁止 `{"type": "any"}`。禁止把中文描述写在 output_schema 里（中文描述放 output.schema）
14. **reports 是默认能力**：`main.py` 必须始终返回 `reports`；没有报告内容也要返回空数组，不能把报告能力做成可关闭开关
15. **平台字段不手写变体**：`todos` / `reports` 的 item schema 必须使用上方固定字段集，尤其 `tasks` / `reviewers` / `callback` 不得漏写；平台会二次规范化这些字段，但 `main.py` return 必须包含对应顶层 key
16. **必须写 `fixtures/sample_input.json`（第 7 个文件）**：一份能让 `python3 scripts/main.py < fixtures/sample_input.json` 端到端跑通并产出合法 JSON 的最小样例输入。不是 test_cases 里的局部 fixture，必须是完整 payload。平台会用它跑 schema 自检。能拿到真实 API 样本时必须裁剪真实字段；拿不到时只能做结构样例，并在 `intent.md` 标明真实采集由 SDK 在运行时完成。
17. **`SKILL.md` 的 `## 输出定义` 不是手写真源**：平台会按 `output_schema.required` 自动渲染/校验这一节。你可以先写占位说明，但禁止在这里单独改字段名、增删字段或写与 required 不一致的别名
18. **MCP 优先但不硬编码**：不要假设固定平台或固定工具名。创建阶段工具列表里提供什么业务 MCP，就按工具名/描述/返回字段匹配 SOP 数据源，优先使用匹配 MCP 获取真实样本；没有匹配 MCP 才用 browser/SDK 自己探索。运行时 `main.py` 不能直接 import/call MCP server；若平台已把业务 MCP 暴露为 SkillForge SDK adapter，可通过 `sf.fetch_api("mcp://tool_name", ...)` 调用稳定业务工具，否则把 MCP 验证出的请求路径、page_url、参数和字段解析逻辑转写为 SkillForge SDK 采集。
19. **采集失败禁止静默空结果**：`collect_inputs` 或 fetch helper 不能 `except Exception: return {}` / `return []` / `pass`。必须抛出明确错误，或在输出中写入 `data_errors` 并让报告标明数据源失败，避免真实运行误判为“无问题”。
20. **待办只进平台，不直推钉钉**：`output.todos` 代表 SkillForge 平台待办。`main.py` 只能返回 todos，禁止调用钉钉/邮件/IM API；平台会先写入待办中心，只有用户在平台点击通过后，`kind="dispatch"` 的子任务才会由平台推送给执行人。
21. **待办 payload 必须运营可读**：`todos[].payload` 只能放决策卡片级字段（对象ID/名称、优先级、关键指标、分析依据、建议动作、禁止动作、推荐决策、数据来源）。禁止把完整 `input_snapshot`、完整 `output_result`、页面原始响应或外部平台原始 JSON 塞进 payload。若需要 `payload.input`/`payload.output`，只能放小摘要。
22. **待办必须能直接决策**：每条待办必须回答“是否建议通过/驳回、为什么、派给谁做什么、什么时候完成、哪些动作不能做”。`tasks[].deadline` 必须是 ISO 时间字符串，不要写“今日/明天/本周五”。

# Skill 输出契约 — 写 main.py 前必读

**在写 `scripts/main.py` 之前，必须用 Read 工具读一次 `./SKILL_OUTPUT_CONTRACT.md`**（已拷贝到 cwd，只读）。

该文件定义：main.py 执行模型（payload 优先 + datasource 通过 SkillForge SDK 采集；禁止 fixture 当运行数据）/ 输出形态判定（待办 / 报告 / 两者 / 纯数据）/ TodoSpec / ReportSpec 字段契约 / reviewer/recipient 怎么填 / 禁止项

**判定要点**（细节看契约文件）：
- 「需要人做事」→ return 里带 `todos` 字段
- 「只读通知」→ return 里带 `reports` 字段
- 两者都要 → 两个字段并存
- 业务字段（如 `整改建议清单` / `日报正文`）无论哪种形态都保留

# 工作步骤要求（必须严格按顺序执行，不得跳过）
1. 第一段 plain text 思考：识别原文的所有数据源、流程步骤、异常类型、闭环动作
2. **如果用户 SOP 中提到了任何 URL**：
   - **必须**用 Bash 工具执行 `curl -s -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" "<URL>"` 去实际抓取
   - **禁止用 WebFetch 工具**（当前环境会被安全策略拦截，浪费时间）
   - 如果 curl 拿到的是 JS 渲染的空壳 HTML，从 HTML 里找 API 端点，再 curl 调 API 拿 JSON
   - 从真实 API 响应中提取字段名和数据结构，写入 contract.json 和 main.py
3. **写 ./contract.json**（这是最先要产出的文件，前端会立即拿去预览）**必须包含 output_schema（标准 JSON Schema Draft-07 格式）**
   - SOP 只出现平台/数据源名称、没有具体 URL 时，禁止先调用 browser_get_platform_apis / browser_get_cached_apis；先按 SOP 写 contract.json。
   - contract.json 写完后，如确实需要 API 样本再查缓存；每个平台页面最多查 1 次，禁止 `full=true`，只用压缩摘要里的 URL 和 `browser_fetch_json(registry_id, api_index)` 单点验证。
4. 用 Write 工具按顺序创建 SKILL.md、intent.md、policy.yaml、scripts/main.py、tests/test_main.py、fixtures/sample_input.json
5. **如果 contract.input 有 datasource，或 SOP/SKILL.md/contract 提到了外部业务 API，main.py 必须用 SkillForge SDK 采集真实数据**，不要直接 `requests/curl` 访问业务系统。生成阶段先看已注入 MCP：有匹配业务 MCP 就先用 MCP 验证样本并复用其字段结构；没有匹配 MCP 再用 SDK/browser 自探索。标准导入：
   ```python
   import os
   from skillforge_sdk import SkillForge
   ```
   采集逻辑必须放在 `main(payload)` 或 `collect_inputs(payload)` 内，禁止 import 时就联网。平台会自动注入受信任 SDK 目录（真源位于主仓库 `app/skill_runtime_sdk/skillforge_sdk.py`）；手工跑测试时用 `PYTHONPATH="scripts:$SKILLFORGE_TRUSTED_SDK_DIR"`。示例：payload 缺 `生意参谋_店铺排行榜` 时再 `sf.fetch_api(...)`；如果 MCP 验证需要页面上下文，运行时代码应使用 `sf.fetch_api(api_url, page_url=page_url, headers=..., body=...)` 复现。`SkillForge` 初始化优先使用 `os.environ.get("SKILLFORGE_SKILL_ID", "<当前 skill id>")`，不要写死别的 skill_id。
6. 用 Bash 工具跑：`cd scripts && python3 -c "from main import main; print('OK')"` 确认 import 通过
7. 用 Bash 工具跑 `PYTHONPATH="scripts:$SKILLFORGE_TRUSTED_SDK_DIR" python3 -m pytest tests/test_main.py -q` 看测试通过率
8. **用 Bash 跑 schema 自检**：`python3 scripts/main.py < fixtures/sample_input.json | python3 -c "import json,sys; json.loads(sys.stdin.read())"` —— 这是样例沙箱校验，不代表真实数据采集。通过后平台会继续检查 datasource 是否写了 SDK 采集逻辑。
9. 全部完成后说一句 "DONE"

注意：
- cwd 已经是空目录，所有路径用相对路径（./contract.json、./scripts/main.py 等）
- scripts/ 和 tests/ 是子目录，用 Write 工具创建文件时父目录会自动创建

**重要：生成阶段禁止使用 WebFetch 工具，它在当前环境被安全策略拦截。生成时探查 URL 用 Bash + curl；Skill 运行时采集业务数据用 SkillForge SDK。**
"""


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def build_skill_creation_system_prompt() -> tuple[str, str]:
    """优先走 PromptRegistry，失败时 fallback 到内联模板。"""
    try:
        from app.common.prompt_registry import prompt_registry

        prompt, prompt_hash = prompt_registry.build("coding_agent_skill_creation")
        return prompt, prompt_hash
    except Exception as e:  # noqa: BLE001
        logger.warning("coding_agent_skill_creation prompt fallback to inline template: {}", e)
        return SKILL_CREATION_SYSTEM_PROMPT, _short_hash(SKILL_CREATION_SYSTEM_PROMPT)


_RATE_LIMIT_PATTERN = re.compile(r"(?:rate[\s_-]*limit|速率限制|quota)", re.IGNORECASE)


def _format_upstream_label(provider: str | None, model: str | None) -> str:
    parts = [p for p in (provider, model) if p]
    return "/".join(parts) if parts else "LLM"


def _assistant_text(raw: dict[str, Any]) -> str:
    """拼出 assistant 文本内容；tool_use/tool_result 块忽略。"""
    msg = raw.get("message")
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = str(block.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _extract_upstream_api_error(
    raw: dict[str, Any],
    *,
    provider: str | None,
    model: str | None,
) -> tuple[str, str] | None:
    """
    把 aiclawcode 合成出来的“伪 assistant 错误消息”还原成真实错误。

    典型原文：
        API Error: 429 {"error":{"code":"1302","message":"您的账户已达到速率限制，请您控制请求频率"}}
    """
    if raw.get("type") != "assistant":
        return None

    text = _assistant_text(raw)
    if not text:
        return None

    msg = raw.get("message") or {}
    synthetic_error = (
        bool(raw.get("isApiErrorMessage"))
        or bool(raw.get("error"))
        or str(msg.get("model") or "") == "<synthetic>"
    )

    status: int | None = None
    provider_code = ""
    provider_message = ""
    request_id = ""
    body = ""

    matched = re.match(r"^\s*API Error:\s*(\d{3})(?:\s+|:\s*)(.*)\s*$", text, re.S)
    if matched:
        status = int(matched.group(1))
        body = matched.group(2).strip()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict):
                provider_code = str(err.get("code") or "").strip()
                provider_message = str(err.get("message") or "").strip()
            request_id = str(payload.get("request_id") or "").strip()
    elif not synthetic_error:
        return None

    label = _format_upstream_label(provider, model)
    detail = provider_message or body or text
    status_text = f"HTTP {status}" if status is not None else "未知状态"
    provider_code_text = f"/{provider_code}" if provider_code else ""
    request_id_text = f" [request_id={request_id}]" if request_id else ""
    rate_limited = (
        status == 429
        or provider_code == "1302"
        or bool(_RATE_LIMIT_PATTERN.search(f"{provider_code} {detail}"))
    )

    if rate_limited:
        return (
            "RATE_LIMITED",
            f"{label} 请求被限流 ({status_text}{provider_code_text}): {detail}{request_id_text}",
        )

    return (
        "LLM_API_ERROR",
        f"{label} 上游接口报错 ({status_text}{provider_code_text}): {detail}{request_id_text}",
    )


# ─────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────

REQUIRED_FILES = [
    "contract.json",
    "SKILL.md",
    "intent.md",
    "policy.yaml",
    "scripts/main.py",
    "tests/test_main.py",
    "fixtures/sample_input.json",
]

# milestone 推进规则：扫描 Write 工具调用的 file_path，匹配到对应 milestone
WRITE_PATH_TO_MILESTONE = {
    "contract.json": "draft_contract",
    "SKILL.md": "write_skill_md",
    "scripts/main.py": "write_main_py",
    "tests/test_main.py": "write_tests",
    # intent.md / policy.yaml 不单独占 milestone，归入 write_skill_md 阶段一起算
}


def _scratch_dir_for(draft_id: str) -> Path:
    return Path("/tmp/sf_skill_creation") / draft_id


def _detect_milestone_from_write(file_path: str) -> str | None:
    """从 Write 工具调用的 file_path 推断完成了哪个 milestone。"""
    norm = file_path.replace("\\", "/")
    # 取相对路径末段（去掉 /tmp/sf_skill_creation/<draft_id>/ 前缀）
    for needle, milestone in WRITE_PATH_TO_MILESTONE.items():
        if norm.endswith(needle):
            return milestone
    return None


def _detect_verify_from_bash(command: str) -> bool:
    """从 Bash 命令推断是否在跑 import 验证。"""
    return "from main import" in command or "pytest" in command


def _retry_delay_for(error: "SkillCreationError | None", attempt: int) -> int:
    if error is None or error.code != "RATE_LIMITED":
        return 1
    import random
    index = max(0, min(attempt - 1, len(RATE_LIMIT_RETRY_DELAYS) - 1))
    base = RATE_LIMIT_RETRY_DELAYS[index]
    # jitter: 多用户同步 429 时分散重试时间,防 thundering herd
    return base + random.randint(0, max(5, base // 3))


async def _run_one_attempt(
    *,
    message: str,
    draft_id: str,
    attempt: int,
    scratch_dir: Path,
    user_id: str | None = None,
    previous_error: SkillCreationError | None = None,
) -> AsyncIterator[CreationEvent]:
    """单次 spawn aiclawcode 跑一遍。yield 进度事件，最后 yield SKILL_READY 或抛异常。"""
    # Check before deleting/recreating drafts, reading credentials or retrying.
    if not runtime_status()["available"]:
        raise SkillCreationError(RUNTIME_UNAVAILABLE, RUNTIME_MESSAGE, scratch_dir)

    # 初次生成从空目录开始；返工时保留上一轮产物，只让 agent 局部修复。
    # 旧逻辑每次重试都清空 scratch，会把已经通过的 contract/SKILL.md 也作废，
    # 复杂 Skill 一旦末端校验失败就被迫从头跑完整生成。
    if previous_error is None and scratch_dir.exists():
        shutil.rmtree(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    # 把 SKILL_OUTPUT_CONTRACT.md 拷贝到 scratch_dir（aiclawcode 用 Read 工具读），
    # 省去把契约内容塞进 system prompt 导致每轮都重传 1-2K tokens 的浪费。
    # 注意：不能用 os.link —— 硬链接共用 inode，agent 误改会污染平台全局契约文件，
    # 造成跨任务污染。必须用 copy + chmod 0o444（只读）。
    _contract_src = Path(__file__).parent / "SKILL_OUTPUT_CONTRACT.md"
    _contract_dst = scratch_dir / "SKILL_OUTPUT_CONTRACT.md"
    if _contract_src.exists() and not _contract_dst.exists():
        shutil.copy2(_contract_src, _contract_dst)
        try:
            _contract_dst.chmod(0o444)
        except OSError:
            pass  # 只读保护失败不致命

    env, provider, model = await SessionService._build_subprocess_env_provider_model()
    env, config_dir = SessionService._inject_runtime_isolation(
        env,
        user_id=f"_creator_{draft_id}",
        session_scope=f"draft_{draft_id}",
    )
    env["CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY"] = str(SKILL_CREATION_MAX_TOOL_USE_CONCURRENCY)
    system_prompt, prompt_hash = build_skill_creation_system_prompt()
    mcp_user_id = user_id or f"_creator_{draft_id}"
    builtin_mcp = await SessionService._build_builtin_mcp_servers(f"_creating_{draft_id}", mcp_user_id)
    from app.coding_agent.mcp_config import build_mcp_config_json, get_mcp_servers
    configured_mcp = await get_mcp_servers()
    selected_mcp = SessionService._select_creation_mcp_servers(
        {**configured_mcp, **builtin_mcp},
        builtin_mcp,
    )
    mcp_config_json = build_mcp_config_json(selected_mcp) or json.dumps({"mcpServers": {}}, ensure_ascii=False)

    logger.info(
        "[skill_creation] attempt={} spawn aiclawcode scratch={} provider={} model={} tool_cc={} builtin_mcp={}",
        attempt, scratch_dir, provider, model,
        env["CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY"],
        ",".join(sorted(selected_mcp)) or "-",
    )

    # P0 #4: 不走 coding_agent_pool，直接构造独立 SubprocessSession，避免污染共享池
    session = SubprocessSession(
        skill_id=f"_creating_{draft_id}",
        user_id=f"_creator_{draft_id}",
        work_dir=scratch_dir,
        env=env,
        provider=provider,
        model=model,
        permission_mode="bypassPermissions",  # 创建场景全程自动放行（写文件、跑 import）
        max_turns=30,
        system_prompt_append=system_prompt,
        mcp_config_json=mcp_config_json,
    )
    session.prompt_hash = prompt_hash
    session.config_dir = config_dir

    contract_emitted = False
    seen_milestones: set[str] = set()
    first_text_seen = False
    start_ts = time.monotonic()
    fs_scan_count = 0
    fs_hit_count = 0
    saw_user_event = False

    # P0 #2: session.start() 放进 try/finally，确保子进程一定被清理
    try:
        await session.start()

        yield CreationEvent(
            CreationEventType.STARTED,
            {"attempt": attempt,
             "milestones": MILESTONES,
             "milestone_labels": MILESTONE_LABELS,
             "prompt_hash": prompt_hash},
        )

        await session.send_user_message(_user_message_template(message, previous_error))

        # 进入 identify_intent 阶段，立刻告诉前端「开始思考了」，避免 GLM 首 token
        # 长延迟（20-40s）期间 UI 静止看起来像卡死。
        yield CreationEvent(
            CreationEventType.THINKING,
            {"text": "AI 正在识别你的业务意图…"},
        )

        # 心跳：独立 reader task 把 stream 事件灌进 queue；主 loop 从 queue 读。
        # ⚠️ 不能直接 wait_for(stream.__anext__(), 5s) —— asyncio 在 timeout 时
        # cancel 那个 coroutine，会破坏 async generator 内部状态，后续读取立即
        # 抛 RuntimeError（实测导致子进程 10s 内被 SIGTERM rc=143，3 次重试都挂）。
        stream_q: asyncio.Queue = asyncio.Queue()

        async def _pump_stream():
            try:
                async for raw in session.stream_events():
                    await stream_q.put(("event", raw))
            except Exception as e:  # noqa: BLE001
                await stream_q.put(("error", e))
            finally:
                await stream_q.put(("done", None))

        reader_task = asyncio.create_task(_pump_stream())
        last_event_at = time.monotonic()
        stream_done = False

        while not stream_done:
            try:
                kind, data = await asyncio.wait_for(stream_q.get(), timeout=5.0)
            except asyncio.TimeoutError:
                idle = int(time.monotonic() - last_event_at)
                elapsed = int(time.monotonic() - start_ts)
                hint = "思考中" if not first_text_seen else "AI 工作中"
                yield CreationEvent(
                    CreationEventType.THINKING,
                    {"text": f"{hint}… ({elapsed}s, idle {idle}s)"},
                )
                continue

            if kind == "done":
                stream_done = True
                break
            if kind == "error":
                raise data

            last_event_at = time.monotonic()
            raw = data
            t = raw.get("type")
            if t == "system" and raw.get("subtype") == "init":
                continue  # init 我们不暴露，前端只关心 STARTED 之后的进度

            if t == "assistant":
                api_error = _extract_upstream_api_error(
                    raw,
                    provider=provider,
                    model=model,
                )
                if api_error:
                    code, detail = api_error
                    raise SkillCreationError(
                        code=code,
                        detail=detail,
                        scratch_dir=scratch_dir,
                    )
                msg = raw.get("message") or {}
                for block in (msg.get("content") or []):
                    btype = block.get("type")
                    if btype == "text":
                        text = block.get("text") or ""
                        if not text:
                            continue
                        # 第一段 text 视为「识别意图」milestone 完成
                        if not first_text_seen:
                            first_text_seen = True
                            if "identify_intent" not in seen_milestones:
                                seen_milestones.add("identify_intent")
                                yield CreationEvent(
                                    CreationEventType.MILESTONE,
                                    {"milestone": "identify_intent",
                                     "label": MILESTONE_LABELS["identify_intent"],
                                     "elapsed_s": round(time.monotonic() - start_ts, 1)},
                                )
                        yield CreationEvent(
                            CreationEventType.THINKING,
                            {"text": text},
                        )
                    elif btype == "tool_use":
                        name = block.get("name") or "?"
                        inp = block.get("input") or {}
                        # 工具调用事件
                        yield CreationEvent(
                            CreationEventType.TOOL_CALL,
                            {"name": name, "input": _summarize_tool_input(name, inp, work_dir=scratch_dir)},
                        )
                        # 推进 milestone（注意：tool_use 阶段文件还没写盘，
                        # 这里只是给前端「看到 AI 在动手」的视觉反馈，
                        # 真正的 CONTRACT_READY 事件等 tool_result 落盘后再推）
                        if name in ("Write", "Edit", "MultiEdit"):
                            fp = inp.get("file_path", "")
                            milestone = _detect_milestone_from_write(fp)
                            if milestone and milestone not in seen_milestones:
                                seen_milestones.add(milestone)
                                yield CreationEvent(
                                    CreationEventType.MILESTONE,
                                    {"milestone": milestone,
                                     "label": MILESTONE_LABELS[milestone],
                                     "elapsed_s": round(time.monotonic() - start_ts, 1)},
                                )
                        elif name == "Bash":
                            cmd = inp.get("command", "")
                            if _detect_verify_from_bash(cmd) and "verify_import" not in seen_milestones:
                                seen_milestones.add("verify_import")
                                yield CreationEvent(
                                    CreationEventType.MILESTONE,
                                    {"milestone": "verify_import",
                                     "label": MILESTONE_LABELS["verify_import"],
                                     "elapsed_s": round(time.monotonic() - start_ts, 1)},
                                )
            elif t == "user":
                saw_user_event = True
                msg = raw.get("message") or {}
                for block in (msg.get("content") or []):
                    if block.get("type") != "tool_result":
                        continue
                    err = bool(block.get("is_error"))
                    content = str(block.get("content") or "")
                    payload = {"is_error": err, "content_preview": content[:200]}
                    if err:
                        from app.coding_agent.session_service import _classify_tool_result_error
                        classified = _classify_tool_result_error(content)
                        if classified:
                            payload.update(classified)
                    yield CreationEvent(CreationEventType.TOOL_RESULT, payload)

            # 每个 stream 事件处理完后都扫一次 scratch，fs-based milestone 回填。
            # 不限定 "user"/"assistant" 分支——GLM 等非 Claude 模型的 stream 协议里
            # 可能根本没 user 事件，全靠 fs 兜底。contract.json 同样这里回填。
            if not contract_emitted:
                contract_path = scratch_dir / "contract.json"
                if contract_path.exists():
                    try:
                        contract_text = contract_path.read_text(encoding="utf-8")
                        contract = json.loads(contract_text)
                        from app.common.contract_schema import (
                            get_output_schema,
                            lint_output_schema,
                            normalize_output_schema_platform_fields,
                            summarize_errors,
                        )

                        normalized_contract = normalize_output_schema_platform_fields(contract)
                        if normalized_contract != contract:
                            contract = normalized_contract
                            contract_text = json.dumps(contract, ensure_ascii=False, indent=2)
                            contract_path.write_text(contract_text, encoding="utf-8")
                        output_schema = get_output_schema(contract)
                        contract_errors = (
                            ["contract.json 缺少 output_schema（JSON Schema Draft-07）"]
                            if not output_schema
                            else lint_output_schema(output_schema)
                        )
                        if contract_errors:
                            raise SkillCreationError(
                                code="CONTRACT_SCHEMA_INVALID_EARLY",
                                detail=f"contract.json 早期校验失败: {summarize_errors(contract_errors)}",
                                scratch_dir=scratch_dir,
                            )
                        contract_emitted = True
                        yield CreationEvent(
                            CreationEventType.CONTRACT_READY,
                            {"contract": contract},
                        )
                        yield CreationEvent(
                            CreationEventType.TOOL_CALL,
                            {"name": "Write",
                             "input": {"file_path": "contract.json",
                                       "content": contract_text[:50000],
                                       "size": len(contract_text)}},
                        )
                    except json.JSONDecodeError:
                        pass  # 部分写入，下次事件再试
            fs_scan_count += 1
            for rel_path, milestone in WRITE_PATH_TO_MILESTONE.items():
                if milestone in seen_milestones:
                    continue
                if (scratch_dir / rel_path).exists():
                    seen_milestones.add(milestone)
                    fs_hit_count += 1
                    try:
                        file_content = (scratch_dir / rel_path).read_text(encoding="utf-8")
                    except Exception:
                        file_content = ""
                    yield CreationEvent(
                        CreationEventType.TOOL_CALL,
                        {"name": "Write",
                         "input": {"file_path": rel_path,
                                   "content": file_content[:50000],
                                   "size": len(file_content)}},
                    )
                    yield CreationEvent(
                        CreationEventType.MILESTONE,
                        {"milestone": milestone,
                         "label": MILESTONE_LABELS[milestone],
                         "elapsed_s": round(time.monotonic() - start_ts, 1)},
                    )

            if t == "result":
                # 一轮对话结束
                subtype = raw.get("subtype")
                logger.info(
                    "[skill_creation] attempt={} result subtype={} elapsed={:.1f}s "
                    "milestones={} fs_scans={} fs_hits={} saw_user_event={}",
                    attempt, subtype, time.monotonic() - start_ts,
                    sorted(seen_milestones), fs_scan_count, fs_hit_count, saw_user_event,
                )
                break
    finally:
        # 防孤儿: 主 loop 提前退出(异常/Cancelled)时 _pump_stream 仍在消费 session.stream_events(),
        # 这里必须显式 cancel + gather,否则留下 asyncio.Task warning 还可能泄 session。
        if not reader_task.done():
            reader_task.cancel()
            try:
                await asyncio.gather(reader_task, return_exceptions=True)
            except Exception:  # noqa: BLE001
                pass
        await session.close()

    # 校验 6 个必需文件都生成了
    missing = [f for f in REQUIRED_FILES if not (scratch_dir / f).exists()]
    if missing:
        raise SkillCreationError(
            code="MISSING_REQUIRED_FILES",
            detail=f"aiclawcode 没有生成必需文件: {missing}",
            scratch_dir=scratch_dir,
        )

    # P2 #3: 读文件用 to_thread 包装，避免阻塞事件循环
    import anyio

    async def _read_files():
        def _sync_read():
            return {f: (scratch_dir / f).read_text(encoding="utf-8") for f in REQUIRED_FILES}
        return await anyio.to_thread.run_sync(_sync_read)

    files = await _read_files()

    # P2 #2: 产物强校验（contract JSON / policy YAML / main.py 语法）
    try:
        contract = json.loads(files["contract.json"])
    except json.JSONDecodeError as e:
        raise SkillCreationError(
            code="CONTRACT_JSON_INVALID",
            detail=f"contract.json 解析失败: {e}",
            scratch_dir=scratch_dir,
        )

    # SSOT: `SKILL.md` 的 `## 输出定义` 由 contract.output_schema.required 收口,
    # 避免让 agent 在 SKILL.md / contract / main.py 三处各写一份字段名。
    from app.common.contract_schema import (
        normalize_output_schema_platform_fields,
        normalize_skill_bundle_files,
    )

    normalized_contract = normalize_output_schema_platform_fields(contract)
    if normalized_contract != contract:
        contract = normalized_contract
        files["contract.json"] = json.dumps(contract, ensure_ascii=False, indent=2)
        (scratch_dir / "contract.json").write_text(files["contract.json"], encoding="utf-8")

    files = normalize_skill_bundle_files(files, contract)
    skill_md_path = scratch_dir / "SKILL.md"
    skill_md_path.write_text(files["SKILL.md"], encoding="utf-8")

    try:
        import yaml as _yaml
        _yaml.safe_load(files["policy.yaml"])
    except Exception as e:
        logger.warning("[skill_creation] policy.yaml 格式警告（不阻断）: {}", e)

    try:
        import py_compile
        main_path = scratch_dir / "scripts" / "main.py"
        py_compile.compile(str(main_path), doraise=True)
    except py_compile.PyCompileError as e:
        logger.warning("[skill_creation] main.py 编译警告（不阻断）: {}", e)

    # verify_schema milestone（硬闸门，失败会走 SkillCreationError → 外层重试）
    schema_errors = await verify_schema_phase(scratch_dir, contract)
    if schema_errors:
        yield CreationEvent(
            CreationEventType.MILESTONE,
            {"milestone": "verify_schema",
             "label": MILESTONE_LABELS["verify_schema"],
             "status": "failed",
             "errors": schema_errors[:8],
             "elapsed_s": round(time.monotonic() - start_ts, 1)},
        )
        from app.common.contract_schema import summarize_errors as _sum_err
        raise SkillCreationError(
            code="CONTRACT_SCHEMA_DRIFT",
            detail=f"契约校验失败: {_sum_err(schema_errors)}",
            scratch_dir=scratch_dir,
        )

    yield CreationEvent(
        CreationEventType.MILESTONE,
        {"milestone": "verify_schema",
         "label": MILESTONE_LABELS["verify_schema"],
         "status": "passed",
         "elapsed_s": round(time.monotonic() - start_ts, 1)},
    )

    yield CreationEvent(
        CreationEventType.SKILL_READY,
        {
            "contract": contract,
            "files": files,
            "elapsed_s": round(time.monotonic() - start_ts, 1),
        },
    )


def _user_message_template(sop: str, previous_error: "SkillCreationError | None" = None) -> str:
    base = (
        "以下是用户的业务 SOP，请按 system prompt 的要求生成完整 Skill 目录：\n\n<<<\n"
        + sop
        + "\n>>>"
    )
    if previous_error is None:
        return base
    # 返工附加上次失败原因，让 agent 聚焦修点
    hint = (
        "⚠️ 这是返工修复模式，不是重新生成模式。\n"
        "当前 cwd 已保留上一轮产物。禁止从零重建整个 Skill，禁止重新识别意图后重写全部文件。\n"
        "请先读取并检查现有 contract.json、SKILL.md、scripts/main.py、tests/test_main.py、fixtures/sample_input.json；"
        "只修改校验错误涉及的文件和字段，保持已通过的业务字段、数据源命名、触发配置不变。\n"
        "如果只缺 main.py 顶层返回字段，就只修 main.py 的 return dict；"
        "如果只缺 todos/reports 平台字段，就只补平台字段结构；"
        "如果缺测试或 fixture，才补对应文件。\n\n"
        "上一次生成失败，原因：\n"
        f"- code: {previous_error.code}\n"
        f"- detail: {previous_error.detail}\n\n"
    )
    if previous_error.code.startswith("CONTRACT_SCHEMA"):
        hint += (
            "修复要点（必须三处对齐）：\n"
            "1. SKILL.md §输出定义 的字段名列表\n"
            "2. contract.json output_schema.required\n"
            "3. scripts/main.py `return` dict 的 top-level keys\n"
            "\n"
            "这三处的字段必须一字不差相同(除开 todos/reports/诊断报告 这三个平台固定字段)。\n"
            "reports 是默认能力，main.py 不能省略它；没有报告内容也必须返回 reports: []。\n"
            "\n"
            "常见错误及修法:\n"
            "- 把 N 个业务维度折进一个 markdown 字符串 `诊断报告` 返回 → 禁止。每个维度必须单独作为 top-level key。\n"
            "- SKILL.md 写 9 个字段 / output_schema 只写 6 个 → 把 output_schema.required 补齐到 9 个。\n"
            "- 字段名有斜杠/空格等符号不一致(如`同类商品下滑/上涨情况` vs `同类商品下滑上涨情况`)→ 改到严格一致。\n"
            "\n"
            "完成后跑: python3 scripts/main.py < fixtures/sample_input.json ，确认 stdout JSON 包含所有 required 字段。\n"
        )
        if "真实采集逻辑" in previous_error.detail or "datasource" in previous_error.detail:
            hint += (
                "\n数据采集修复要点：\n"
                "- contract.input 只要有 source=datasource，scripts/main.py 必须实现 collect_inputs(payload)。\n"
                "- payload 已带真实数据时直接分析；payload 缺失时优先按已注入 MCP 的真实样本复现采集；没有匹配 MCP 再用 skillforge_sdk.SkillForge.fetch_api/extract/capture_apis/explore 自探索。\n"
                "- 采集失败禁止静默返回 {} 或 []，必须抛错或把 data_errors 写入输出，避免真实运行误判为 0 问题。\n"
                "- fixtures/sample_input.json 只用于样例沙箱校验，禁止 main.py 读取它当运行数据。\n"
            )
        if "占位" in previous_error.detail or "待实现" in previous_error.detail:
            hint += (
                "\n占位实现修复要点：\n"
                "- scripts/main.py 禁止返回 `待实现` / `请补充业务逻辑` / placeholder / stub。\n"
                "- 必须把 SOP 中的真实分析、报告和 todos 生成逻辑写进 main(payload)。\n"
                "- sample_input.json 只能作为一条可运行样例，不能代替运行时数据获取。\n"
            )
    return hint + base


_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])


def _strip_root(text: str, work_dir: str | Path | None = None) -> str:
    """剥掉 work_dir / PROJECT_ROOT 前缀，让前端显示短路径（scripts/main.py 而不是 /home/skillforge/.../scripts/main.py）。

    前缀优先级：work_dir（skill scratch / skill repo）> PROJECT_ROOT（SkillForge 源码根）。
    """
    if not text:
        return text
    prefixes: list[str] = []
    if work_dir:
        wd = str(work_dir).rstrip("/")
        if wd:
            prefixes.append(wd + "/")
    prefixes.append(_PROJECT_ROOT.rstrip("/") + "/")
    for p in prefixes:
        text = text.replace(p, "")
    return text


def _summarize_tool_input(name: str, inp: dict, work_dir: str | Path | None = None) -> dict:
    """精简工具调用输入，避免把整段 file content 推给前端；同时剥绝对路径前缀。"""
    if name in ("Write", "Edit", "MultiEdit"):
        fp = _strip_root(inp.get("file_path", ""), work_dir)
        content = inp.get("content") or inp.get("new_string") or ""
        content_str = content if isinstance(content, str) else ""
        return {
            "file_path": fp,
            "content": content_str[:50000],
            "size": len(content_str),
        }
    if name == "Read":
        return {"file_path": _strip_root(inp.get("file_path", ""), work_dir)}
    if name == "Bash":
        cmd = _strip_root(inp.get("command", ""), work_dir)
        return {"command": cmd[:200]}
    if name in ("Glob", "Grep"):
        return {"pattern": inp.get("pattern", "")}
    # 兜底：序列化但截断
    s = json.dumps(inp, ensure_ascii=False)
    return {"raw": _strip_root(s, work_dir)[:200]}


class SkillCreationError(Exception):
    """skill 创建过程中可识别的错误，带 scratch_dir 用于失败现场保留。"""

    def __init__(self, code: str, detail: str, scratch_dir: Path):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.scratch_dir = scratch_dir


async def verify_schema_phase(scratch_dir: Path, contract: dict) -> list[str]:
    """跑 verify_schema 四步校验，返回错误消息列表（空表示通过）。

    四步：
    1. lint contract.output_schema 自身合法
    2. 读 fixtures/sample_input.json
    3. subprocess 跑 main.py < sample_input，stdout 解析为 JSON 后 jsonschema.validate
    4. Triple alignment: SKILL.md §输出定义 ↔ output_schema.required ↔ main.py return keys 三处一致
    """
    from app.common.contract_schema import (
        check_triple_alignment,
        get_output_schema,
        lint_placeholder_implementation,
        lint_placeholder_output,
        lint_runtime_data_acquisition,
        lint_output_schema,
        load_sample_input,
        parse_stdout_json,
        run_main_with_sample,
        validate_output,
    )

    errors: list[str] = []
    output_schema = get_output_schema(contract)
    if not output_schema:
        errors.append("contract.json 缺少 output_schema（JSON Schema Draft-07）")
    else:
        errors.extend(lint_output_schema(output_schema))

    sample_input = load_sample_input(scratch_dir)
    if sample_input is None:
        errors.append("fixtures/sample_input.json 缺失或非合法 JSON")

    skill_md_path = scratch_dir / "SKILL.md"
    skill_md = ""
    if skill_md_path.exists():
        skill_md = skill_md_path.read_text(encoding="utf-8", errors="replace")

    main_py_path = scratch_dir / "scripts" / "main.py"
    if main_py_path.exists():
        main_py_text = main_py_path.read_text(encoding="utf-8", errors="replace")
        errors.extend(lint_placeholder_implementation(main_py_text))
        errors.extend(
            lint_runtime_data_acquisition(
                contract,
                main_py_text,
                skill_md_text=skill_md,
            )
        )
    else:
        errors.append("scripts/main.py 缺失")

    main_output: dict | None = None
    if not errors and sample_input is not None and output_schema:
        rc, stdout, stderr = await run_main_with_sample(scratch_dir, sample_input)
        if rc != 0:
            errors.append(
                f"main.py 退出码 {rc}，stderr 尾段: {(stderr or '').strip()[-300:]}"
            )
        else:
            parsed, parse_err = parse_stdout_json(stdout)
            if parse_err:
                errors.append(
                    f"main.py stdout 不是合法 JSON: {parse_err}; stdout 前 200 字: {stdout[:200]}"
                )
            else:
                main_output = parsed if isinstance(parsed, dict) else None
                errors.extend(lint_placeholder_output(parsed))
                errors.extend(validate_output(parsed, output_schema))

    # 第 4 步:triple alignment（SKILL.md ↔ schema ↔ main.py）
    if skill_md and output_schema:
        errors.extend(check_triple_alignment(skill_md, output_schema, main_output))

    return errors


# ─────────────────────────────────────────────────────────
# 对外主入口（带 1 次自动重试）
# ─────────────────────────────────────────────────────────

async def run_skill_creation(
    *,
    message: str,
    draft_id: str,
    user_id: str | None = None,
) -> AsyncIterator[CreationEvent]:
    """
    主入口。yield CreationEvent 流。

    成功路径以 SKILL_READY 事件结束（包含 contract + 6 文件 dict）。
    失败路径以 ERROR 事件结束（重试 1 次仍失败时）。
    每个 yield 完后，立即跟一个 DONE 事件标记流终止（前端可关 WS）。
    """
    scratch_dir = _scratch_dir_for(draft_id)
    last_error: SkillCreationError | None = None
    succeeded = False

    # 最多 3 次尝试（1 初次 + 2 次返工）— schema drift 返工上限
    MAX_ATTEMPTS = 3
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                success_payload: dict | None = None
                async for ev in _run_one_attempt(
                    message=message,
                    draft_id=draft_id,
                    attempt=attempt,
                    scratch_dir=scratch_dir,
                    user_id=user_id,
                    previous_error=last_error,
                ):
                    if ev.type == CreationEventType.SKILL_READY:
                        success_payload = ev.payload
                    yield ev
                if success_payload is not None:
                    succeeded = True
                    yield CreationEvent(CreationEventType.DONE, {"status": "ok"})
                    return
                # 没拿到 SKILL_READY 但也没异常 — 走兜底报错
                last_error = SkillCreationError(
                    code="NO_SKILL_READY",
                    detail="aiclawcode 跑完但没有产出有效 skill",
                    scratch_dir=scratch_dir,
                )
            except SkillCreationError as e:
                last_error = e
                logger.warning(
                    "[skill_creation] attempt {}/{} failed: {} - {}",
                    attempt, MAX_ATTEMPTS, e.code, e.detail,
                )
            except Exception as e:  # noqa: BLE001
                last_error = SkillCreationError(
                    code="UNEXPECTED",
                    detail=f"{type(e).__name__}: {e}",
                    scratch_dir=scratch_dir,
                )
                logger.exception("[skill_creation] attempt {}/{} unexpected error", attempt, MAX_ATTEMPTS)

            if last_error and last_error.code == RUNTIME_UNAVAILABLE:
                break

            if attempt < MAX_ATTEMPTS:
                retry_delay_s = _retry_delay_for(last_error, attempt)
                yield CreationEvent(
                    CreationEventType.RETRY,
                    {"reason": last_error.detail if last_error else "unknown",
                     "code": last_error.code if last_error else "UNKNOWN",
                     "next_attempt": attempt + 1,
                     "max_attempts": MAX_ATTEMPTS,
                     "retry_in_s": retry_delay_s},
                )
                await asyncio.sleep(retry_delay_s)

        # 所有尝试都失败
        err = last_error or SkillCreationError(
            code="UNKNOWN", detail="unknown failure", scratch_dir=scratch_dir,
        )
        yield CreationEvent(
            CreationEventType.ERROR,
            {"code": err.code, "detail": err.detail},
        )
        yield CreationEvent(CreationEventType.DONE, {"status": "error"})
    finally:
        # P0 #3: scratch 清理策略
        if succeeded:
            # 成功路径：文件已经读到内存 (SKILL_READY payload)，清理 scratch
            try:
                shutil.rmtree(scratch_dir, ignore_errors=True)
                logger.info("[skill_creation] cleaned scratch {}", scratch_dir)
            except Exception as e:
                logger.debug("[skill_creation] scratch 清理失败 dir={}: {}", scratch_dir, e)
        else:
            # 失败路径：保留现场供 debug（管理后台可查 SkillStudioDraft.error_detail）
            logger.info("[skill_creation] keeping scratch for debug: {}", scratch_dir)
