"""
钉钉互动卡片JSON模板。
根据Skill执行结果、审核事件、报告等生成不同格式的钉钉推送内容。

所有函数返回 dict，作为 outbox.enqueue() 的 payload 参数。
钉钉工作通知 markdown 格式：{"title": "...", "markdown": "..."}
互动卡片额外带 buttons 列表。
"""

from app.config import settings
from app.dingtalk.open_links import create_dispatch_task_view_token, create_todo_view_token


def _public_url(path: str) -> str:
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{path}" if not path.startswith("http") else path


def _todo_detail_url(todo_id: int, assignee_user) -> str:
    dingtalk_user_id = getattr(assignee_user, "dingtalk_user_id", None)
    if not dingtalk_user_id:
        return _public_url(f"/inbox/todos/{todo_id}")
    token = create_todo_view_token(
        todo_id=todo_id,
        assignee_user_id=assignee_user.id,
        dingtalk_user_id=dingtalk_user_id,
    )
    return _public_url(f"/api/dingtalk/open/todos/{todo_id}?token={token}")


def _dispatch_detail_url(task_id: int, executor_user) -> str:
    dingtalk_user_id = getattr(executor_user, "dingtalk_user_id", None)
    if not dingtalk_user_id:
        return _public_url(f"/todos/dispatch/{task_id}")
    token = create_dispatch_task_view_token(
        task_id=task_id,
        executor_user_id=executor_user.id,
        dingtalk_user_id=dingtalk_user_id,
    )
    return _public_url(f"/api/dingtalk/open/dispatch/{task_id}?token={token}")


def _dispatch_ack_url(task_id: int, executor_user) -> str:
    dingtalk_user_id = getattr(executor_user, "dingtalk_user_id", None)
    if not dingtalk_user_id:
        return _public_url(f"/todos/dispatch/{task_id}")
    token = create_dispatch_task_view_token(
        task_id=task_id,
        executor_user_id=executor_user.id,
        dingtalk_user_id=dingtalk_user_id,
    )
    return _public_url(f"/api/dingtalk/open/dispatch/{task_id}/ack?token={token}")


def _format_notice_text(text: str, *, max_len: int = 1200) -> str:
    """Turn semicolon-packed task text into readable DingTalk markdown."""
    import re

    raw = str(text or "").strip()
    if not raw:
        return ""
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" in raw:
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
    else:
        labels = (
            "Top300", "需要执行", "数据支撑",
            "全店主线", "决策", "关键证据", "运营动作", "禁止动作", "后续动作",
            "验证指标", "主因", "免费访客环比", "免费转化环比", "付费访客环比",
            "付费转化环比", "截止", "接收人", "任务编号",
        )
        pattern = r"；(?=(?:" + "|".join(re.escape(label) for label in labels) + r")(?:[:：\s]|$))"
        lines = [part.strip(" ；") for part in re.split(pattern, raw) if part and part.strip(" ；")] or [raw]
    out: list[str] = []
    for line in lines:
        cleaned = line.strip(" ；")
        if not cleaned:
            continue
        if len(cleaned) > 180:
            cleaned = cleaned[:177] + "..."
        out.append(f"- {cleaned}")
        if sum(len(item) for item in out) >= max_len:
            break
    return "\n".join(out)


def _text_value(value) -> str:
    text = str(value or "").strip()
    return text


def _action_text(item) -> str:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return ""
    action = _text_value(item.get("human_action") or item.get("action") or item.get("content") or item.get("summary"))
    if not action:
        return ""
    dimension = _text_value(item.get("dimension"))
    priority = _text_value(item.get("priority"))
    evidence = _text_value(item.get("evidence"))
    prefix = " / ".join(part for part in (dimension, priority) if part)
    return f"{prefix}：{action}" + (f"（{evidence}）" if evidence else "") if prefix else action


def _metric_label(item: dict) -> str:
    return _text_value(item.get("name") or item.get("label") or item.get("dimension"))


def _metric_value(item: dict, *keys: str) -> str:
    for key in keys:
        value = _text_value(item.get(key))
        if value:
            return value
    return ""


def _payload_metrics(payload: dict) -> list[dict]:
    rows: list[dict] = []

    def extend_metrics(value) -> None:
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))

    def extend_sections(value) -> None:
        if not isinstance(value, list):
            return
        for section in value:
            if isinstance(section, dict):
                extend_metrics(section.get("metrics"))

    extend_metrics(payload.get("key_metrics"))
    extend_metrics(payload.get("metrics"))
    extend_sections(payload.get("metric_sections"))
    output = payload.get("output")
    if isinstance(output, dict):
        extend_metrics(output.get("key_metrics"))
        extend_metrics(output.get("metrics"))
        extend_sections(output.get("metric_sections"))
    return rows


def _find_metric(payload: dict, *labels: str) -> dict | None:
    label_set = set(labels)
    for item in _payload_metrics(payload):
        if _metric_label(item) in label_set:
            return item
    return None


def _payment_change_text(payload: dict) -> str:
    metric = _find_metric(payload, "—变化率—")
    if metric:
        note = _metric_value(metric, "note", "delta", "value")
        import re

        match = re.search(r"(支付|成交额)\s*([-+]?\d+(?:\.\d+)?%)", note)
        if match:
            return match.group(2)
    metric = _find_metric(payload, "支付金额变化率", "支付金额环比", "实时支付金额变化率")
    if metric:
        return _metric_value(metric, "value", "delta", "note")
    return ""


def _top300_label_from_parts(value: str, status: str, detail: str) -> str:
    text = f"{value} {status} {detail}"
    if "榜内下滑" in text:
        return "榜内下滑"
    if "榜内上升" in text:
        return "榜内上升"
    if "榜内持平" in text:
        return "榜内持平"
    if "掉榜" in text:
        return "掉榜"
    if "进榜" in text:
        return "进榜"
    if "当前在Top300" in text or "在Top300" in text:
        return "在榜"
    if "覆盖不足" in text:
        return "覆盖不足"
    if "未进Top300" in text or "未进 Top300" in text:
        return "未进Top300"
    return value or status or "未知"


def _compact_top300_detail(detail: str) -> str:
    import re

    text = _text_value(detail)
    if not text:
        return ""
    match = re.search(r"当前\s*第\s*\d+\s*[，,]\s*昨日\s*第\s*\d+", text)
    if match:
        return match.group(0)
    match = re.search(r"昨日\s*第\s*\d+\s*[，,]\s*当前未进\s*Top300", text)
    if match:
        return match.group(0)
    match = re.search(r"昨日未进\s*Top300\s*[，,]\s*当前第\s*\d+", text)
    if match:
        return match.group(0)
    match = re.search(r"当前第\s*\d+", text)
    if match:
        return match.group(0)
    if "当前未进 Top300" in text or "当前未进Top300" in text:
        return "当前未进Top300"
    if "覆盖不足" in text:
        return "覆盖不足"
    return text[:40]


def _store_top300_summary(payload: dict) -> str:
    metric = _find_metric(payload, "市场Top300状态")
    if metric:
        value = _metric_value(metric, "value", "status")
        status = _metric_value(metric, "status")
        detail = _metric_value(metric, "delta", "note")
        label = _top300_label_from_parts(value, status, detail)
        compact_detail = _compact_top300_detail(detail)
        return f"Top300：{label}" + (f"（{compact_detail}）" if compact_detail else "")
    return ""


def _split_notice_segments(text: str) -> list[str]:
    import re

    labels = (
        "Top300", "需要执行", "数据支撑",
        "全店主线", "决策", "关键证据", "运营动作", "禁止动作", "后续动作",
        "验证指标", "主因", "免费访客环比", "免费转化环比", "付费访客环比",
        "付费转化环比", "截止", "接收人", "任务编号",
    )
    raw = str(text or "")
    pattern = r"；(?=(?:" + "|".join(re.escape(label) for label in labels) + r")(?:[:：\s]|$))"
    return [part.strip(" ；") for part in re.split(pattern, raw) if part and part.strip(" ；")]


def _replace_or_append_top300_segment(segments: list[str], top300: str) -> list[str]:
    if not top300:
        return segments
    replaced = False
    out: list[str] = []
    for segment in segments:
        if segment.startswith("Top300"):
            if not replaced:
                out.append(top300)
                replaced = True
            continue
        out.append(segment)
    if not replaced:
        insert_at = 1 if out and out[0].startswith("全店主线") else 0
        out.insert(insert_at, top300)
    return out


def _dispatch_execution_lines(request, dispatch_task) -> list[str]:
    payload = request.payload if isinstance(getattr(request, "payload", None), dict) else {}
    base_segments = _split_notice_segments(getattr(dispatch_task, "content", ""))
    top300 = _store_top300_summary(payload)
    lines = _replace_or_append_top300_segment(base_segments, top300)

    actions = payload.get("operation_actions")
    if not isinstance(actions, list) or not actions:
        actions = payload.get("top_actions") if isinstance(payload.get("top_actions"), list) else []
    action_lines = [_action_text(item) for item in actions]
    action_lines = [line for line in action_lines if line]
    if action_lines:
        lines.append("需要执行：" + "；".join(action_lines[:5]))

    payment_change = _payment_change_text(payload)
    visitor_rank = _find_metric(payload, "全店访客排名")
    order_rank = _find_metric(payload, "全店成交排名")
    evidence = []
    if payment_change:
        evidence.append(f"支付金额变化率 {payment_change}")
    if visitor_rank:
        evidence.append(f"全店访客排名 {_metric_value(visitor_rank, 'value')}")
    if order_rank:
        evidence.append(f"全店成交排名 {_metric_value(order_rank, 'value')}（实时支付金额）")
    if evidence:
        lines.append("数据支撑：" + "；".join(evidence))
    return lines


def build_dispatch_execution_content(request, dispatch_task) -> str:
    return "；".join(_dispatch_execution_lines(request, dispatch_task))


def _extract_summary(output: dict, max_len: int = 300) -> str:
    """从执行结果中提取摘要文本"""
    if not isinstance(output, dict):
        return str(output)[:max_len]

    summary = output.get("message", "") or output.get("summary", "")
    if not summary:
        # 没有预置摘要字段时，拼接前几个 key=value
        summary = ", ".join(f"{k}={v}" for k, v in list(output.items())[:5])
    return summary[:max_len]


# ──────────────────────────────────────────────
# 执行相关模板
# ──────────────────────────────────────────────

def build_execution_report(
    skill_name: str,
    run_id: str,
    output_summary: str,
    approval_level: int,
) -> dict:
    """L0自动执行结果通知 -- 只读报告，无交互按钮"""
    level_label = f"L{approval_level}" if approval_level is not None else "L0"
    return {
        "title": f"{skill_name} 执行报告",
        "markdown": (
            f"**{skill_name} 执行报告**\n\n"
            f"{output_summary}\n\n"
            f"> 审批级别: {level_label}（自动执行）\n\n"
            f"---\n"
            f"run_id: {run_id[:8]}"
        ),
    }


def build_approval_card(
    skill_name: str,
    run_id: str,
    output_summary: str,
    suggestions: str | list | None = None,
) -> dict:
    """
    L1运营确认 -- 带确认/驳回/需要帮助按钮的互动卡片。
    buttons 的 action_url 使用 dingtalk://action?run_id=xxx&action=yyy 格式，
    与 dingtalk/router.py _handle_card_callback() 中解析逻辑一致。
    """
    # 如果 suggestions 是列表，格式化为编号文本
    if isinstance(suggestions, list):
        suggestion_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(suggestions))
    elif suggestions:
        suggestion_text = str(suggestions)
    else:
        suggestion_text = ""

    markdown_parts = [
        f"**{skill_name} 需要确认**\n",
        f"AI建议已生成，请确认执行：\n",
        f"{output_summary}\n",
    ]

    if suggestion_text:
        markdown_parts.append(f"> AI建议:\n> {suggestion_text}\n")

    markdown_parts.append(f"---\nrun_id: {run_id[:8]}")

    return {
        "title": f"{skill_name} 需要确认",
        "markdown": "\n".join(markdown_parts),
        "buttons": [
            {"title": "确认执行", "action_url": f"dingtalk://action?run_id={run_id}&action=confirm"},
            {"title": "驳回", "action_url": f"dingtalk://action?run_id={run_id}&action=reject"},
            {"title": "需要帮助", "action_url": f"dingtalk://action?run_id={run_id}&action=help"},
        ],
    }


def build_todo_actioncard(request, todo, skill_name: str, assignee_user) -> dict:
    """AI 待办互动卡片，使用 HTTP callbackUrl。

    private_data 严格收敛到 (todo_id, request_id, assignee_user_id, nonce) 四个短字段，
    业务上下文走 SkillForge DB 查询；assignee_user_id 用于服务端做 actor==assignee 校验。
    """
    import secrets

    callback_url = settings.DINGTALK_CARD_CALLBACK_URL or f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/dingtalk/card-callback"
    detail_url = _todo_detail_url(todo.id, assignee_user)
    return {
        "title": f"[待办] {skill_name} 需要审批",
        "markdown": (
            f"**{skill_name} 决策待审批**\n\n"
            f"{_format_notice_text(request.summary or '请查看详情并进行审批')}\n\n"
            f"> 截止: {request.sla_at.strftime('%Y-%m-%d %H:%M')}\n\n"
            f"> 接收人: {assignee_user.name}（仅你可以审批）\n\n"
            f"---\n"
            f"request: {request.id[:12]}"
        ),
        "callback_url": callback_url,
        "private_data": {
            "todo_id": str(todo.id),
            "request_id": request.id,
            "assignee_user_id": assignee_user.id,
            "nonce": secrets.token_hex(8),
        },
        "buttons": [
            {
                "title": "通过",
                "action_id": "approve",
                "params": {"action": "approve"},
            },
            {
                "title": "驳回",
                "action_id": "reject",
                "params": {"action": "reject"},
            },
            {
                "title": "查看详情",
                "action_url": detail_url,
                "action_type": "open_url",
            },
        ],
    }


def build_todo_unauthorized_notice(expected_assignee_name: str) -> dict:
    """非接收人误点 → 私信提示卡片。"""
    return {
        "title": "⚠️ 该待办不属于你",
        "markdown": (
            f"你刚才点击的待办接收人是 **{expected_assignee_name}**，"
            f"你没有审批权限。请联系 {expected_assignee_name} 处理。\n\n"
            f"---\n如果你认为这是误派，请联系管理员。"
        ),
    }


def build_todo_expired_notice(skill_name: str, request_id: str, assignee_name: str | None = None) -> dict:
    """待办过期告警卡片：发给原 reviewer 与 admin。"""
    target_hint = f"接收人 {assignee_name}" if assignee_name else "接收人"
    return {
        "title": f"[过期] {skill_name} 待办未处理",
        "markdown": (
            f"**待办已自动过期**\n\n"
            f"- Skill: **{skill_name}**\n"
            f"- {target_hint}\n"
            f"- 决策请求: {request_id[:12]}\n\n"
            f"> 24 小时内未处理，已被自动归档。如需重新审批，请到 SkillForge 待办中心或重新触发执行。"
        ),
    }


# ──────────────────────────────────────────────
# 派发任务（kind=dispatch）相关模板
# ──────────────────────────────────────────────


def build_dispatch_manager_card(
    request,
    todo,
    skill_name: str,
    manager_user,
    tasks_preview: list[dict],
) -> dict:
    """给管理者审批的派发任务卡片：通过 → 自动 fan-out 给每个执行人个人钉钉。"""
    import secrets

    callback_url = settings.DINGTALK_CARD_CALLBACK_URL or f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/dingtalk/card-callback"
    detail_url = _todo_detail_url(todo.id, manager_user)

    preview_lines = []
    for idx, t in enumerate(tasks_preview[:8], 1):
        deadline_str = ""
        if t.get("deadline"):
            try:
                deadline_str = f"  | 截止 {t['deadline'][:16].replace('T', ' ')}"
            except Exception:
                deadline_str = ""
        content_short = (t.get("content") or "")[:60]
        preview_lines.append(f"{idx}. **@{t.get('executor')}** {content_short}{deadline_str}")
    if len(tasks_preview) > 8:
        preview_lines.append(f"... 共 {len(tasks_preview)} 条")
    preview_md = "\n".join(preview_lines) if preview_lines else "（暂无子任务）"

    return {
        "title": f"[派发审批] {skill_name}",
        "markdown": (
            f"**{skill_name} 申请派发任务**\n\n"
            f"{_format_notice_text(request.summary or '')}\n\n"
            f"**待派发清单（通过后会自动推送给每位执行人）：**\n\n"
            f"{preview_md}\n\n"
            f"> 截止: {request.sla_at.strftime('%Y-%m-%d %H:%M')}\n"
            f"> 接收人: {manager_user.name}（仅你可以审批）\n"
        ),
        "callback_url": callback_url,
        "private_data": {
            "todo_id": str(todo.id),
            "request_id": request.id,
            "assignee_user_id": manager_user.id,
            "kind": "dispatch",
            "nonce": secrets.token_hex(8),
        },
        "buttons": [
            {
                "title": "通过并派发",
                "action_id": "approve",
                "params": {"action": "approve"},
            },
            {
                "title": "驳回",
                "action_id": "reject",
                "params": {"action": "reject"},
            },
            {
                "title": "查看详情",
                "action_url": detail_url,
                "action_type": "open_url",
            },
        ],
    }


def build_dispatch_executor_card(
    request,
    dispatch_task,
    executor_user,
) -> dict:
    """派发审批通过后给执行人推的钉钉卡片：含「完成」按钮回执。"""
    import secrets

    callback_url = settings.DINGTALK_CARD_CALLBACK_URL or f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/dingtalk/card-callback"
    detail_url = _dispatch_detail_url(dispatch_task.id, executor_user)
    ack_url = _dispatch_ack_url(dispatch_task.id, executor_user)

    deadline_md = ""
    if dispatch_task.deadline:
        deadline_md = f"\n\n> 截止: {dispatch_task.deadline.strftime('%Y-%m-%d %H:%M')}"
    body_md = _format_notice_text(build_dispatch_execution_content(request, dispatch_task))

    return {
        "title": f"[任务] {request.title}",
        "markdown": (
            f"**{request.title}**\n\n"
            f"{body_md}\n"
            f"{deadline_md}\n\n"
            f"> 接收人: {executor_user.name}\n"
            f"> 任务编号: {dispatch_task.id}"
        ),
        "callback_url": callback_url,
        "private_data": {
            "dispatch_task_id": str(dispatch_task.id),
            "executor_user_id": executor_user.id,
            "kind": "dispatch_ack",
            "nonce": secrets.token_hex(8),
        },
        "buttons": [
            {
                "title": "标记完成",
                "action_id": "ack",
                "action_url": ack_url,
                "action_type": "open_url",
                "params": {"action": "ack"},
            },
            {
                "title": "查看详情",
                "action_url": detail_url,
                "action_type": "open_url",
            },
        ],
    }


# ──────────────────────────────────────────────
# 审核相关模板
# ──────────────────────────────────────────────

_CHANGE_TYPE_LABELS = {
    "params": "参数调整",
    "logic": "逻辑修改",
    "code": "代码修改",
    "new_skill": "新Skill",
}


def build_review_notification(
    review_id: int,
    skill_id: str,
    change_type: str,
    submitter: str,
    diff_summary: str,
) -> dict:
    """审核通知 -- 新审核请求推送给审核人"""
    type_label = _CHANGE_TYPE_LABELS.get(change_type, change_type)

    return {
        "title": f"Skill变更审核 #{review_id}",
        "markdown": (
            f"**Skill变更审核 #{review_id}**\n\n"
            f"- Skill: {skill_id}\n"
            f"- 变更类型: {type_label}\n"
            f"- 提交人: {submitter}\n"
            f"- 变更概要: {diff_summary or '无'}\n\n"
            f"请前往 SkillForge 平台审核。"
        ),
    }


def build_review_result(
    review_id: int,
    skill_id: str,
    status: str,
    reviewer: str,
) -> dict:
    """审核结果通知 -- 审核通过/驳回推送给提交人"""
    status_label = "已通过" if status == "approved" else "已驳回"
    emoji_hint = "通过" if status == "approved" else "驳回"

    return {
        "title": f"审核{emoji_hint}: {skill_id}",
        "markdown": (
            f"**审核结果通知**\n\n"
            f"- 审核ID: #{review_id}\n"
            f"- Skill: {skill_id}\n"
            f"- 结果: **{status_label}**\n"
            f"- 审核人: {reviewer}\n\n"
            f"请前往 SkillForge 平台查看详情。"
        ),
    }


# ──────────────────────────────────────────────
# 周报/告警/影子运行报告
# ──────────────────────────────────────────────

def build_weekly_report(stats: dict) -> dict:
    """
    董事长周报 -- 本周执行概况、采纳率、业务影响。
    stats 字段：total_executions, adoption_rate, active_skills,
               top_skills (list), business_impact (str), period (str)
    """
    period = stats.get("period", "本周")

    # 热门Skill排行
    top_skills = stats.get("top_skills", [])
    if top_skills:
        top_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(top_skills[:5]))
    else:
        top_text = "  暂无数据"

    # 业务影响说明
    impact = stats.get("business_impact", "")
    impact_section = f"\n**业务影响**\n\n{impact}\n" if impact else ""

    return {
        "title": f"SkillForge {period}周报",
        "markdown": (
            f"**SkillForge {period}周报**\n\n"
            f"**执行概况**\n\n"
            f"- Skill运行: {stats.get('total_executions', 0)} 次\n"
            f"- 采纳率: {stats.get('adoption_rate', 0)}%\n"
            f"- 活跃Skill: {stats.get('active_skills', 0)} 个\n\n"
            f"**热门Skill**\n\n{top_text}\n"
            f"{impact_section}\n"
            f"---\n"
            f"SkillForge 自动生成"
        ),
    }


def build_data_alert(
    source_name: str,
    hours_since_update: int,
    affected_skills: list[str],
) -> dict:
    """
    数据源过期告警卡片。
    参数:
      source_name: 数据源名称
      hours_since_update: 距离上次成功更新已过去的小时数
      affected_skills: 受影响的Skill ID列表
    """
    skills_text = "、".join(affected_skills[:5]) if affected_skills else "无"
    overflow_note = f"等{len(affected_skills)}个" if len(affected_skills) > 5 else ""

    # 超过48小时标记为严重
    severity = "严重" if hours_since_update >= 48 else "警告"
    days = hours_since_update // 24
    remaining_hours = hours_since_update % 24

    if days > 0:
        time_desc = f"{days}天{remaining_hours}小时"
    else:
        time_desc = f"{hours_since_update}小时"

    return {
        "title": f"[{severity}] 数据源过期: {source_name}",
        "markdown": (
            f"**数据源过期告警**\n\n"
            f"- 数据源: **{source_name}**\n"
            f"- 已过期: {time_desc}未更新\n"
            f"- 受影响Skill: {skills_text}{overflow_note}\n\n"
            f"请尽快上传最新数据或检查数据拉取配置，避免Skill产出过时结果。"
        ),
    }


def build_cost_alert(
    department: str,
    total_cost: float,
    threshold: float,
    date_str: str,
    top_call_sources: list[tuple[str, float]] | None = None,
) -> dict:
    """
    F4: 部门成本告警卡片。

    参数:
      department: 部门名称
      total_cost: 当日累计成本（USD）
      threshold: 告警阈值（USD）
      date_str: 日期字符串（YYYY-MM-DD）
      top_call_sources: 可选，top N 调用来源 [(call_source, cost), ...]
    """
    overflow_pct = ((total_cost - threshold) / threshold * 100) if threshold > 0 else 0
    severity = "严重" if overflow_pct >= 50 else "警告"

    markdown_parts = [
        f"**[{severity}] {department} 部门 AI 成本超阈值**\n",
        f"- 部门: **{department}**",
        f"- 日期: {date_str}",
        f"- 当日累计: **${total_cost:.4f}** USD",
        f"- 告警阈值: ${threshold:.2f} USD",
        f"- 超出: {overflow_pct:+.1f}%\n",
    ]

    if top_call_sources:
        markdown_parts.append("**Top 来源：**")
        for src, c in top_call_sources[:5]:
            markdown_parts.append(f"  - {src}: ${c:.4f}")
        markdown_parts.append("")

    markdown_parts.append("> 请前往 SkillForge 成本看板查看明细，核对是否存在异常调用。")
    markdown_parts.append("---")
    markdown_parts.append("> 同一部门 24 小时内不会重复推送本告警。")

    return {
        "title": f"[{severity}] {department} AI 成本告警",
        "markdown": "\n".join(markdown_parts),
    }


def build_shadow_report(
    skill_id: str,
    consistency_rate: float,
    day_count: int,
    details: dict | None = None,
) -> dict:
    """
    影子运行报告 -- 一致率统计。
    consistency_rate: 0-100 的百分比数值。
    details: 可选的详细统计字段（total_runs, matched, mismatched 等）。
    """
    details = details or {}
    total_runs = details.get("total_runs", 0)
    matched = details.get("matched", 0)
    mismatched = details.get("mismatched", 0)

    # 一致率评价
    if consistency_rate >= 95:
        verdict = "表现优秀，可考虑转为正式上线"
    elif consistency_rate >= 80:
        verdict = "基本稳定，建议继续观察"
    else:
        verdict = "一致率偏低，需排查差异原因"

    markdown_parts = [
        f"**影子运行报告: {skill_id}**\n",
        f"- 观察天数: {day_count} 天",
        f"- 一致率: **{consistency_rate:.1f}%**",
        f"- 总运行: {total_runs} 次（一致 {matched} / 不一致 {mismatched}）\n",
        f"> {verdict}\n",
        f"---\n请前往 SkillForge 查看详细对比。",
    ]

    return {
        "title": f"影子运行报告: {skill_id}",
        "markdown": "\n".join(markdown_parts),
    }


# ──────────────────────────────────────────────
# 向后兼容旧函数名（execution_service.py 中曾直接使用）
# ──────────────────────────────────────────────

def build_task_card(skill_name: str, output: dict, run_id: str) -> dict:
    """L0自动推送（向后兼容）"""
    return build_execution_report(
        skill_name=skill_name,
        run_id=run_id,
        output_summary=_extract_summary(output),
        approval_level=0,
    )


def build_confirm_card(skill_name: str, output: dict, run_id: str) -> dict:
    """L1确认卡片（向后兼容）"""
    return build_approval_card(
        skill_name=skill_name,
        run_id=run_id,
        output_summary=_extract_summary(output),
    )


def build_review_card(
    skill_id: str,
    review_id: int,
    submitter: str,
    change_type: str,
    reason: str,
) -> dict:
    """审核通知卡片（向后兼容）"""
    return build_review_notification(
        review_id=review_id,
        skill_id=skill_id,
        change_type=change_type,
        submitter=submitter,
        diff_summary=reason,
    )


# ──────────────────────────────────────────────
# T+1 效果回更卡片数据
# ──────────────────────────────────────────────

def build_t1_impact_update(impact: dict) -> dict:
    """构建 T+1 效果回更的卡片数据（用于更新已发送的互动卡片）。"""
    adopted = impact.get("adopted", False)
    amount = impact.get("amount")
    conclusion = impact.get("decision_conclusion", "")
    date = impact.get("date", "")

    parts = [f"**T+1 效果回收** ({date})"]
    parts.append(f"- 决策结论：{conclusion}")
    parts.append(f"- 是否采纳：{'已采纳' if adopted else '未采纳'}")
    if amount is not None:
        parts.append(f"- 影响金额：¥{amount:,.0f}")
    ds_name = impact.get("datasource", "")
    if ds_name:
        parts.append(f"- 数据源：{ds_name}")

    return {
        "cardParamMap": {
            "t1_impact_text": "\n".join(parts),
            "t1_collected": "true",
            "t1_adopted": str(adopted).lower(),
        },
    }
