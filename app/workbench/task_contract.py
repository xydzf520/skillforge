"""TaskContract helpers for the v7 SkillStudio flow."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta

import yaml

from app.common.time_utils import isoformat_bjt, now_bjt
from app.workbench.schemas import SkillStructure


TASK_CONTRACT_PROMPT_VERSION = "task_contract_v1"
CHECKPOINT_ORDER = ["target", "permission", "preview", "responsibility"]


def _now_bjt() -> datetime:
    return now_bjt()


def _normalize_message(message: str) -> str:
    text = re.sub(r"\s+", " ", (message or "")).strip()
    return text[:500]


def _slugify_name(message: str) -> str:
    text = _normalize_message(message)
    text = re.sub(r"^(每天|每周|每月)\s*", "", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text, flags=re.UNICODE)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:24] or "新建 Skill"


def _guess_department(message: str) -> str:
    if any(word in message for word in ("销售", "商机", "营收", "订单")):
        return "销售运营"
    if any(word in message for word in ("投放", "ROI", "广告", "转化")):
        return "市场投放"
    if any(word in message for word in ("库存", "仓", "物流")):
        return "供应链"
    if any(word in message for word in ("财务", "回款", "发票")):
        return "财务"
    return "业务团队"


def infer_trigger(message: str) -> dict:
    text = _normalize_message(message)

    daily = re.search(r"每天\s*(\d{1,2})(?:[:：点时](\d{1,2}))?", text)
    if daily:
        hour = int(daily.group(1))
        minute = int(daily.group(2) or 0)
        return {
            "type": "cron",
            "expression": f"{minute} {hour} * * *",
            "description": f"每天 {hour:02d}:{minute:02d} 执行",
        }

    weekly = re.search(r"每周([一二三四五六日天])\s*(\d{1,2})(?:[:：点时](\d{1,2}))?", text)
    if weekly:
        weekday_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 0, "天": 0}
        weekday = weekday_map.get(weekly.group(1), 1)
        hour = int(weekly.group(2))
        minute = int(weekly.group(3) or 0)
        return {
            "type": "cron",
            "expression": f"{minute} {hour} * * {weekday}",
            "description": f"每周{weekly.group(1)} {hour:02d}:{minute:02d} 执行",
        }

    if "webhook" in text.lower():
        return {
            "type": "webhook",
            "expression": "",
            "description": "接收 Webhook 事件时执行",
        }

    if any(word in text for word in ("触发", "事件", "变更")):
        return {
            "type": "event",
            "expression": "",
            "description": "事件触发执行",
        }

    return {"type": "manual", "expression": "", "description": "手动触发"}


def infer_output(message: str) -> dict:
    text = _normalize_message(message)
    recipient_match = re.search(r"发(?:送)?给?([^\s，。；]+?(?:群|团队|同学|负责人|邮箱))", text)
    recipient = recipient_match.group(1) if recipient_match else ""

    if "钉钉" in text:
        if not recipient:
            group_match = re.search(r"([^\s，。；]+群)", text)
            recipient = group_match.group(1) if group_match else "钉钉群"
        return {
            "adapter": "dingtalk_card",
            "schema": {
                "title": "日报标题",
                "summary": "摘要区",
                "metrics": ["关键指标", "变化趋势", "负责人"],
                "actions": ["查看详情", "我已知道"],
            },
            "recipient": recipient,
        }

    if "邮件" in text or "email" in text.lower():
        return {
            "adapter": "email",
            "schema": {"subject": "邮件标题", "body": "邮件正文", "attachments": []},
            "recipient": recipient or "业务邮箱",
        }

    if "slack" in text.lower():
        return {
            "adapter": "slack",
            "schema": {"title": "消息标题", "blocks": ["摘要", "指标", "链接"]},
            "recipient": recipient or "Slack channel",
        }

    if "webhook" in text.lower():
        return {
            "adapter": "json_webhook",
            "schema": {"payload": {"summary": "", "items": []}},
            "recipient": recipient or "Webhook endpoint",
        }

    if "csv" in text.lower():
        return {
            "adapter": "csv_file",
            "schema": {"columns": ["字段一", "字段二", "字段三"]},
            "recipient": recipient or "文件目录",
        }

    return {
        "adapter": "dingtalk_card",
        "schema": {
            "title": "通知标题",
            "summary": "摘要区",
            "metrics": ["关键指标"],
            "actions": ["查看详情"],
        },
        "recipient": recipient or "业务群",
    }


def infer_inputs(message: str) -> list[dict]:
    text = _normalize_message(message)
    inputs: list[dict] = []

    if any(word in text for word in ("销售", "营收", "订单")):
        inputs.append({"name": "昨日销售数据", "type": "json", "source": "datasource", "required": True})
    if any(word in text for word in ("BI", "报表", "dashboard", "看板")):
        inputs.append({"name": "BI 报表结果", "type": "json", "source": "datasource", "required": True})
    if any(word in text for word in ("ROI", "投放", "预算")):
        inputs.append({"name": "投放表现指标", "type": "json", "source": "datasource", "required": True})
    if any(word in text for word in ("库存", "缺货", "仓")):
        inputs.append({"name": "库存状态", "type": "json", "source": "datasource", "required": True})
    if any(word in text for word in ("群", "邮箱", "接收人")):
        inputs.append({"name": "接收人配置", "type": "string", "source": "user", "required": True})

    if not inputs:
        inputs.append({"name": "业务输入", "type": "json", "source": "user", "required": True})
    return inputs


def infer_permissions(message: str, output: dict, inputs: list[dict]) -> list[dict]:
    permissions: list[dict] = []

    if any(item.get("source") == "datasource" for item in inputs):
        permissions.append({"action": "read_data", "target": "业务数据源", "reversible": True})

    adapter = output.get("adapter")
    recipient = output.get("recipient") or "目标接收方"
    if adapter == "dingtalk_card":
        permissions.append({"action": "send_message", "target": f"钉钉:{recipient}", "reversible": False})
    elif adapter == "email":
        permissions.append({"action": "send_message", "target": f"邮件:{recipient}", "reversible": False})
    elif adapter == "slack":
        permissions.append({"action": "send_message", "target": f"Slack:{recipient}", "reversible": False})
    elif adapter == "json_webhook":
        permissions.append({"action": "external_api", "target": recipient, "reversible": False})
    elif adapter == "csv_file":
        permissions.append({"action": "write_data", "target": recipient, "reversible": True})

    if any(word in message for word in ("同步", "回写", "更新", "写入")):
        permissions.append({"action": "write_data", "target": "业务系统", "reversible": False})

    return permissions


def infer_risks(message: str, permissions: list[dict]) -> dict:
    irreversible = any(not item.get("reversible", True) for item in permissions)
    level = "R2" if irreversible else "R1"
    if any(word in message for word in ("财务", "客户", "合同", "回款")):
        data_classification = "confidential"
        level = "R3"
    elif any(word in message for word in ("销售", "投放", "订单", "库存")):
        data_classification = "internal"
    else:
        data_classification = "public"

    return {
        "level": level,
        "data_classification": data_classification,
        "department": _guess_department(message),
    }


def infer_fixtures(message: str) -> list[dict]:
    if any(word in message for word in ("销售", "营收", "订单")):
        return [
            {
                "title": "昨日销售日报",
                "sales_amount": 3540000,
                "mom_change": "+5.2%",
                "top_channel": "天猫",
                "top_channel_share": "35%",
            }
        ]
    if any(word in message for word in ("ROI", "投放", "预算")):
        return [
            {
                "title": "投放表现日报",
                "roi": 1.82,
                "spend": 128000,
                "suggestion": "维持投放，关注素材疲劳",
            }
        ]
    return [{"title": "预演样例", "summary": "使用默认样例数据完成一次预演"}]


def infer_test_cases(message: str, output: dict) -> list[dict]:
    title = "日报已生成" if output.get("adapter") == "dingtalk_card" else "输出已生成"
    return [
        {
            "name": "正常数据场景",
            "input": {"scenario": "normal"},
            "expected_keywords": [title, "成功", "摘要"],
        },
        {
            "name": "空数据回退场景",
            "input": {"scenario": "empty"},
            "expected_keywords": ["暂无数据", "回退", "提示"],
        },
        {
            "name": "接收方失败场景",
            "input": {"scenario": "delivery_failure"},
            "expected_keywords": ["失败", "重试", "告警"],
        },
    ]


def build_task_contract(message: str) -> dict:
    text = _normalize_message(message)
    output = infer_output(text)
    inputs = infer_inputs(text)
    permissions = infer_permissions(text, output, inputs)
    risks = infer_risks(text, permissions)

    return {
        "goal": text,
        "trigger": infer_trigger(text),
        "input": inputs,
        "output": output,
        "permissions": permissions,
        "risks": risks,
        "fixtures": infer_fixtures(text),
        "test_cases": infer_test_cases(text, output),
    }


def render_intent_md(message: str, contract: dict) -> str:
    trigger = contract.get("trigger") or {}
    output = contract.get("output") or {}
    permissions = contract.get("permissions") or []
    inputs = contract.get("input") or []

    input_lines = "\n".join(
        f"- {item.get('name')} ({item.get('source')}/{item.get('type')})"
        for item in inputs
    ) or "- 无"
    permission_lines = "\n".join(
        f"- {item.get('action')}: {item.get('target')}"
        + ("（不可逆）" if not item.get("reversible", True) else "")
        for item in permissions
    ) or "- 无"

    return (
        "# intent.md\n\n"
        "## 原始需求\n"
        f"{_normalize_message(message)}\n\n"
        "## 任务理解\n"
        f"- 目标：{contract.get('goal', '')}\n"
        f"- 触发：{trigger.get('description') or trigger.get('type')}\n"
        f"- 输出：{output.get('adapter')} → {output.get('recipient') or '待确认'}\n"
        f"- 风险：{(contract.get('risks') or {}).get('level', 'R1')}\n\n"
        "## 输入依赖\n"
        f"{input_lines}\n\n"
        "## 所需权限\n"
        f"{permission_lines}\n"
    )


def render_policy_yaml(contract: dict) -> str:
    payload = {
        "version": 1,
        "trigger": contract.get("trigger") or {},
        "output": {
            "adapter": (contract.get("output") or {}).get("adapter"),
            "recipient": (contract.get("output") or {}).get("recipient"),
        },
        "permissions": contract.get("permissions") or [],
        "risk": contract.get("risks") or {},
        "failure_policy": {
            "notify": "skill_owner",
            "auto_disable_after_failures": 3,
            "rollback": "restore_last_published_version",
        },
    }
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)


def _render_dingtalk_card(contract: dict, fixture: dict) -> tuple[str, list[str]]:
    # 返回 (markdown正文, 结构化按钮)。按钮由前端渲染成真实可点击按钮，markdown 不再
    # 自己描述"操作按钮"文本，避免用户误以为 pre 里的文字是按钮。
    title = fixture.get("title") or "预演结果"
    if "sales_amount" in fixture:
        md = (
            f"**{title}**\n\n"
            f"- 销售额：¥{fixture['sales_amount']:,}\n"
            f"- 环比：{fixture.get('mom_change', '--')}\n"
            f"- TOP 渠道：{fixture.get('top_channel', '--')}（{fixture.get('top_channel_share', '--')}）"
        )
        return md, ["查看详情", "我已知道"]
    if "roi" in fixture:
        md = (
            f"**{title}**\n\n"
            f"- ROI：{fixture.get('roi')}\n"
            f"- 消耗：¥{fixture.get('spend', 0):,}\n"
            f"- 建议：{fixture.get('suggestion', '--')}"
        )
        return md, ["查看详情", "处理建议"]
    md = (
        f"**{title}**\n\n"
        f"{fixture.get('summary', '已根据任务合同生成预演内容')}"
    )
    return md, ["查看详情"]


def build_preview(
    contract: dict,
    *,
    cache_key: str,
    cached: bool = False,
    files: dict[str, str] | None = None,
) -> dict:
    from app.common.contract_schema import get_preview_input

    output = contract.get("output") or {}
    adapter = output.get("adapter") or "dingtalk_card"
    fixture = get_preview_input(contract, files)
    generated_at = isoformat_bjt(_now_bjt())

    if adapter == "dingtalk_card":
        markdown, actions = _render_dingtalk_card(contract, fixture)
        card_payload = {
            "title": fixture.get("title") or "预演卡片",
            "markdown": markdown,
            "actions": actions,
        }
        rendered_output = markdown
    elif adapter == "email":
        rendered_output = (
            f"主题：{fixture.get('title') or '业务通知'}\n\n"
            f"{fixture.get('summary', '这里是邮件正文预演')}"
        )
        card_payload = {"subject": fixture.get("title") or "业务通知", "body": rendered_output}
    elif adapter == "json_webhook":
        card_payload = {"payload": fixture}
        rendered_output = json.dumps(card_payload, ensure_ascii=False, indent=2)
    else:
        card_payload = {"summary": fixture}
        rendered_output = json.dumps(card_payload, ensure_ascii=False, indent=2)

    return {
        "adapter": adapter,
        "cache_key": cache_key,
        "rendered_output": rendered_output,
        "card_payload": card_payload,
        "fixture_used": fixture,
        "cached": cached,
        "generated_at": generated_at,
        "success": True,
    }


def build_preview_cache_key(contract: dict, files: dict[str, str] | None = None) -> str:
    from app.common.contract_schema import get_preview_input

    output = contract.get("output") or {}
    key_payload = {
        "goal": contract.get("goal"),
        "fixture": get_preview_input(contract, files),
        "adapter": output.get("adapter"),
        "schema": output.get("schema"),
        "prompt_version": TASK_CONTRACT_PROMPT_VERSION,
    }
    if (files or {}).get("scripts/main.py"):
        key_payload["main_py_sha"] = hashlib.sha256(files["scripts/main.py"].encode("utf-8")).hexdigest()[:12]
    raw = json.dumps(key_payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def default_review_state(contract: dict) -> dict:
    permission_required = any(not item.get("reversible", True) for item in contract.get("permissions") or [])
    preview_required = (contract.get("output") or {}).get("adapter") in {
        "dingtalk_card", "email", "slack", "json_webhook",
    }
    state = {
        "target": {
            "key": "target",
            "title": "目标确认",
            "description": "确认平台理解的业务目标、触发方式和输出形式。",
            "required": True,
            "approved": False,
            "decision": "pending",
            "detail": {},
        },
        "permission": {
            "key": "permission",
            "title": "权限授予",
            "description": "确认外部读取、推送或写入动作。",
            "required": permission_required,
            "approved": not permission_required,
            "decision": "pending" if permission_required else "not_required",
            "detail": {},
        },
        "preview": {
            "key": "preview",
            "title": "不可逆动作预演",
            "description": "确认预演输出长相符合预期。",
            "required": preview_required,
            "approved": not preview_required,
            "decision": "pending" if preview_required else "not_required",
            "detail": {},
        },
        "responsibility": {
            "key": "responsibility",
            "title": "失败责任",
            "description": "部署后失败通知、自动停用和回滚策略。",
            "required": True,
            "approved": False,
            "decision": "pending",
            "detail": {
                "notify": "skill_owner",
                "auto_disable_after_failures": 3,
                "rollback": "restore_last_published_version",
            },
        },
    }
    return state


def merge_review_state(contract: dict, current: dict | None = None) -> dict:
    merged = default_review_state(contract)
    for key, value in (current or {}).items():
        if key not in merged or not isinstance(value, dict):
            continue
        merged[key].update(value)
        if not merged[key].get("required"):
            merged[key]["approved"] = True
            if merged[key].get("decision") == "pending":
                merged[key]["decision"] = "not_required"
    return merged


def build_gate_status(contract: dict, review_state: dict, preview: dict) -> dict:
    schema_valid = bool((contract.get("output") or {}).get("adapter") and (contract.get("output") or {}).get("schema"))
    regression_ready = len(contract.get("test_cases") or []) >= 3
    preview_required = review_state["preview"]["required"]
    permission_required = review_state["permission"]["required"]

    # 契约一致闸门：检查 output_schema（jsonschema Draft-07）本身合法。
    # sample_input 的"真跑"校验在 skill_creation_runner 的 verify_schema milestone 做了，
    # 这里只做结构闸。legacy 无 output_schema 的 skill 跳过不阻断。
    from app.common.contract_schema import get_output_schema, lint_output_schema

    contract_consistency_passed = True
    contract_consistency_detail = "未升级到机器可校验 output_schema（legacy skill，跳过）"
    output_schema = get_output_schema(contract)
    if output_schema:
        schema_lint_errors = lint_output_schema(output_schema)
        if schema_lint_errors:
            contract_consistency_passed = False
            contract_consistency_detail = "output_schema 不合法: " + "; ".join(schema_lint_errors[:3])
        else:
            contract_consistency_detail = (
                f"output_schema 已声明 {len(output_schema.get('required') or [])} 个 required 字段"
            )

    items = [
        {
            "key": "intent_clear",
            "label": "意图清晰",
            "passed": bool(review_state["target"].get("approved")),
            "detail": "业务目标已由业务方确认",
        },
        {
            "key": "schema_valid",
            "label": "输出 schema 合规",
            "passed": schema_valid,
            "detail": f"适配器 {((contract.get('output') or {}).get('adapter') or '未选择')} 已绑定稳定输出结构",
        },
        {
            "key": "contract_consistency",
            "label": "契约一致",
            "passed": contract_consistency_passed,
            "detail": contract_consistency_detail,
        },
        {
            "key": "sandbox_preview_passed",
            "label": "沙箱预演通过",
            "passed": bool(preview.get("success")),
            "detail": "已生成真实预演结果，可直接核对输出形态",
        },
        {
            "key": "regression_ready",
            "label": "3 个回归用例就绪",
            "passed": regression_ready,
            "detail": f"当前生成 {len(contract.get('test_cases') or [])} 个回归用例",
        },
        {
            "key": "manual_confirmation",
            "label": "人工确认",
            "passed": bool(review_state["preview"].get("approved")),
            "detail": "业务方已确认预演结果",
        },
    ]

    can_generate_skill = bool(review_state["target"].get("approved"))
    if permission_required:
        can_generate_skill = can_generate_skill and bool(review_state["permission"].get("approved"))
    if preview_required:
        can_generate_skill = can_generate_skill and bool(review_state["preview"].get("approved"))

    can_publish = all(item["passed"] for item in items)
    can_publish = can_publish and bool(review_state["responsibility"].get("approved"))
    if permission_required:
        can_publish = can_publish and bool(review_state["permission"].get("approved"))

    required_count = sum(1 for value in review_state.values() if value.get("required"))
    completed_count = sum(1 for value in review_state.values() if value.get("approved"))

    return {
        "items": items,
        "required_count": required_count,
        "completed_count": completed_count,
        "can_generate_skill": can_generate_skill,
        "can_publish": can_publish,
    }


def build_skill_draft_from_contract(contract: dict) -> SkillStructure:
    output = contract.get("output") or {}
    trigger = contract.get("trigger") or {}
    risks = contract.get("risks") or {}
    inputs = contract.get("input") or []

    params = []
    if trigger.get("type") == "cron":
        params.append({
            "name": "schedule_cron",
            "default_value": trigger.get("expression") or "",
            "description": "定时触发表达式",
        })
    if output.get("recipient"):
        params.append({
            "name": "target_recipient",
            "default_value": output.get("recipient"),
            "description": "结果接收方",
        })

    steps = [
        {
            "id": "step_1",
            "name": "准备输入数据",
            "description": "读取业务数据并完成基础校验",
            "branches": [
                {
                    "condition": "输入完整且数据源可用",
                    "conclusion": "进入输出生成",
                    "action": "拉取并标准化输入数据",
                    "next_step": "step_2",
                },
                {
                    "condition": "输入缺失或数据源异常",
                    "conclusion": "终止并告警",
                    "action": "通知责任人检查数据源",
                    "next_step": None,
                },
            ],
        },
        {
            "id": "step_2",
            "name": "生成并分发结果",
            "description": "根据适配器要求渲染输出并发送给目标接收方",
            "branches": [
                {
                    "condition": f"{output.get('adapter')} 渲染成功",
                    "conclusion": "发送结果",
                    "action": f"推送到 {output.get('recipient') or '目标接收方'}",
                    "next_step": None,
                },
                {
                    "condition": "渲染失败或接收方不可达",
                    "conclusion": "失败重试",
                    "action": "写入失败日志并告警",
                    "next_step": None,
                },
            ],
        },
    ]

    output_table = []
    schema = output.get("schema") or {}
    if output.get("adapter") == "dingtalk_card":
        for field in ("title", "summary"):
            output_table.append({
                "name": field,
                "field": field,
                "format": "text",
                "recipient": output.get("recipient") or "",
                "approval_level": risks.get("level") or "R1",
            })
    else:
        for field in schema.keys() or ["result"]:
            output_table.append({
                "name": field,
                "field": field,
                "format": "text",
                "recipient": output.get("recipient") or "",
                "approval_level": risks.get("level") or "R1",
            })

    test_cases = [
        {
            "name": case.get("name") or "测试场景",
            "input_data": case.get("input") or {},
            "expected_output": {"expected_keywords": case.get("expected_keywords") or []},
            "assert_rules": [],
        }
        for case in (contract.get("test_cases") or [])
    ]

    return SkillStructure(
        meta={
            "name": _slugify_name(contract.get("goal") or "新建 Skill"),
            "department": risks.get("department") or "业务团队",
            "trigger_type": trigger.get("type") or "manual",
            "risk_level": risks.get("level") or "R2",
            "description": contract.get("goal") or "",
        },
        goal=contract.get("goal") or "",
        rules=steps,
        params=params,
        output_table=output_table,
        test_cases=test_cases,
        workflow={
            "nodes": [
                {"id": "input", "label": "输入准备", "detail": ", ".join(item.get("name", "") for item in inputs)},
                {"id": "output", "label": "输出分发", "detail": output.get("adapter") or "adapter"},
            ],
            "edges": [{"source": "input", "target": "output"}],
            "bindings": [],
            "summary": contract.get("goal") or "",
            "status": "draft",
        },
        custom_sections={},
    )


def build_bundle_from_contract(message: str, contract: dict) -> dict:
    """纯后处理：把已有 contract 渲染成完整 bundle（intent_md / preview / gate / skill 等）。

    LLM 路径与 regex fallback 路径都通过这里收口，避免逻辑分裂。
    """
    cache_key = build_preview_cache_key(contract)
    preview = build_preview(contract, cache_key=cache_key, cached=False)
    review_state = default_review_state(contract)
    gate = build_gate_status(contract, review_state, preview)
    skill_struct = build_skill_draft_from_contract(contract)
    return {
        "contract": contract,
        "intent_md": render_intent_md(message, contract),
        "policy_yaml": render_policy_yaml(contract),
        "preview": preview,
        "review_state": review_state,
        "gate": gate,
        "skill": skill_struct.model_dump() if hasattr(skill_struct, "model_dump") else skill_struct,
        "skill_struct": skill_struct,  # 保留原对象给 service.py 用
        "cache_key": cache_key,
    }


def build_task_contract_bundle(message: str) -> dict:
    """同步入口：仅走 regex fallback。生产路径请用 agent_core.llm.generate_contract_bundle。"""
    contract = build_task_contract(message)
    return build_bundle_from_contract(message, contract)


def checkpoint_list(review_state: dict) -> list[dict]:
    return [review_state[key] for key in CHECKPOINT_ORDER if key in review_state]


def review_expires_at(minutes: int = 30) -> datetime:
    return _now_bjt() + timedelta(minutes=minutes)
