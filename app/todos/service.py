"""AI 待办服务。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from uuid import uuid4

from loguru import logger
from sqlalchemy import and_, case, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import expand_department_subtree, get_primary_department_id
from app.auth.dependencies import require_department_access
from app.auth.models import User
from app.common.audit import audit
from app.common.exceptions import AppError
from app.config import settings
from app.dingtalk.outbox import outbox
from app.dingtalk.recipients import resolve_work_notice_user
from app.notifications.models import Notification
from app.org.models import OrgUnit, UserOrgMembership
from app.skills.core.models import Skill

from ._acl import assert_can_modify_todo, is_global_inbox_reader
from .assignee_scope import (
    visible_inbox_assignee_ids_for_user,
    visible_inbox_assignee_scope_for_user,
)
from .models import AITodo, DecisionRequest, TodoDispatchPreference, TodoDispatchTask
from .post_training_evaluation import (
    POST_TRAINING_EVALUATION_KEY,
    evaluate_todo_with_post_training_model,
    post_training_evaluation_summary,
    should_evaluate_post_training_model,
)
from .reviewer_resolver import resolve_reviewers, resolve_reviewers_from_spec
from .todo_spec import TodoSpec, TodoSpecParseError, parse_todo_specs
from app.common.time_utils import isoformat_bjt, now_bjt, parse_bjt_datetime


_TODO_METRIC_PREVIEW_PRIORITY = {
    "支付金额变化率": -1,
    "免费搜索/免费访客环比": 0,
    "搜索/免费转化环比": 1,
    "付费访客环比": 2,
    "付费转化环比": 3,
    "昨日免费搜索/免费访客环比": 4,
    "昨日搜索/免费转化环比": 5,
    "昨日付费访客环比": 6,
    "昨日付费转化环比": 7,
    "市场Top300状态": 8,
    "活动/价格力": 9,
    "评价证据": 10,
    "实时下滑": 11,
}

_TODO_RANK_METRIC_LABELS = {"全店访客排名", "全店成交排名"}
_PAYMENT_CHANGE_KEYS = {
    "payment_change_pct",
    "payment_change_rate",
    "pay_amt_change_pct",
    "pay_amt_change",
    "revenue_change_rate",
}
_TODO_ACTIVE_DISPATCH_STATUSES = {"sent", "pushed_no_dingtalk", "in_progress", "blocked"}
SAMPLEBRAND_LOW_CONSUMPTION_SKILL_ID = "samplebrand-video-low-consumption-operator-v1"
TMALL_LINK_DECLINE_OPERATOR_SKILL_ID = "tmall-link-decline-operator-v1"
TMALL_LINK_DECLINE_OPERATOR_INBOX_ASSIGNEE_QUERY = settings.LINK_DECLINE_OPERATOR_RECIPIENT_QUERY.strip()
TMALL_LINK_DECLINE_OPERATOR_INBOX_DINGTALK_USER_ID = settings.LINK_DECLINE_OPERATOR_DINGTALK_USER_ID.strip()
TMALL_LINK_DECLINE_OPERATOR_BULK_TODO_EVALUATION_LIMIT = 10


def _is_execution_failure_output(output: dict | None) -> bool:
    if not isinstance(output, dict):
        return False
    meta = output.get("_skillforge_meta")
    meta = meta if isinstance(meta, dict) else {}
    status = str(output.get("status") or meta.get("status") or "").strip().lower()
    code = str(output.get("code") or meta.get("code") or "").strip().lower()
    failure_statuses = {"failed", "failure", "error", "timeout", "timed_out"}
    failure_codes = {
        "execution_failed",
        "runtime_execution_failed",
        "script_execution_failed",
        "script_timeout",
        "timeout",
    }
    return bool(
        output.get("error")
        or meta.get("error")
        or meta.get("error_message")
        or status in failure_statuses
        or code in failure_codes
    )


class TodoService:
    @staticmethod
    def _as_dict(value: object) -> dict:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _as_list(value: object) -> list:
        return value if isinstance(value, list) else []

    @staticmethod
    def _snapshot_item(output: dict | None, item_id: str | None) -> dict:
        if not isinstance(output, dict) or not item_id:
            return {}
        node = TodoService._as_dict(output.get("0855快照对比"))
        for item in TodoService._as_list(node.get("items")):
            if isinstance(item, dict) and str(item.get("item_id") or "").strip() == item_id:
                return item
        return {}

    @staticmethod
    def _snapshot_collection_item(output: dict | None, item_id: str | None) -> dict:
        if not isinstance(output, dict) or not item_id:
            return {}
        items = TodoService._as_dict(TodoService._as_dict(output.get("数据采集快照")).get("items"))
        return TodoService._as_dict(items.get(item_id))

    @staticmethod
    def _section_item(output: dict | None, section_key: str, item_id: str | None) -> dict:
        if not isinstance(output, dict) or not item_id:
            return {}
        for item in TodoService._as_list(output.get(section_key)):
            if isinstance(item, dict) and str(item.get("item_id") or "").strip() == item_id:
                return item
        return {}

    @staticmethod
    def _snapshot_period(output: dict | None, item: dict | None = None) -> dict:
        item_period = TodoService._as_dict((item or {}).get("period"))
        if item_period:
            return {
                "current_label": item_period.get("current_label"),
                "compare_label": item_period.get("compare_label"),
            }
        periods = TodoService._as_dict((item or {}).get("periods"))
        for key in ("flow", "promotion"):
            period = TodoService._as_dict(periods.get(key))
            if period:
                return {
                    "current_label": period.get("current_label"),
                    "compare_label": period.get("compare_label"),
                }
        node = TodoService._as_dict((output or {}).get("0855快照对比"))
        market = TodoService._as_dict(node.get("market_category"))
        period = TodoService._as_dict(market.get("period"))
        return {
            "current_label": period.get("current_label"),
            "compare_label": period.get("compare_label"),
        }

    @staticmethod
    def _parse_snapshot_time(value: object) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return parse_bjt_datetime(text)
        except (TypeError, ValueError):
            pass
        match = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(?::\d{2})?)", text)
        if match:
            try:
                return parse_bjt_datetime(match.group(1))
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _parse_period_end(label: object) -> datetime | None:
        text = str(label or "").strip()
        if not text:
            return None
        tail = text.split("~")[-1].strip()
        return TodoService._parse_snapshot_time(tail)

    @staticmethod
    def _snapshot_candidate_time(output: dict | None, item_id: str | None) -> datetime | None:
        if not isinstance(output, dict):
            return None
        snapshot = TodoService._as_dict(output.get("数据采集快照"))
        collection_item = TodoService._snapshot_collection_item(output, item_id)
        candidates: list[object] = [
            snapshot.get("as_of_time"),
            snapshot.get("captured_at"),
            snapshot.get("snapshot_time"),
            snapshot.get("schedule_time"),
            snapshot.get("data_time"),
        ]
        for source in (
            TodoService._as_dict(TodoService._as_dict(collection_item.get("periods")).get("flow")),
            TodoService._as_dict(TodoService._as_dict(collection_item.get("periods")).get("promotion")),
            TodoService._as_dict(collection_item.get("period")),
        ):
            candidates.extend(
                [
                    source.get("source_as_of_time"),
                    source.get("as_of_time"),
                    source.get("current_label"),
                ]
            )
        node = TodoService._as_dict(output.get("0855快照对比"))
        market_period = TodoService._as_dict(TodoService._as_dict(node.get("market_category")).get("period"))
        candidates.extend(
            [
                node.get("as_of_time"),
                node.get("captured_at"),
                node.get("baseline_captured_at"),
                market_period.get("current_label"),
            ]
        )
        for value in candidates:
            parsed = TodoService._parse_snapshot_time(value)
            if parsed is not None:
                return parsed
            parsed = TodoService._parse_period_end(value)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _baseline_snapshot_target(period: dict, baseline_captured_at: str | None) -> datetime | None:
        compare_end = TodoService._parse_period_end(period.get("compare_label"))
        if compare_end is not None:
            return compare_end
        current_end = TodoService._parse_period_end(period.get("current_label"))
        if current_end is not None:
            return current_end - timedelta(days=1)
        return TodoService._parse_snapshot_time(baseline_captured_at)

    @staticmethod
    def _number(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip().replace(",", "").replace("¥", "").replace("%", "")
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return None
        return None

    @staticmethod
    def _coalesce_number(*values: object) -> float | None:
        for value in values:
            number = TodoService._number(value)
            if number is not None:
                return number
        return None

    @staticmethod
    def _change_pct(current: float | None, previous: float | None) -> float | None:
        if current is None or previous in (None, 0):
            return None
        return round((current - previous) / previous * 100, 2)

    @staticmethod
    def _previous_from_delta(current: float | None, delta: float | None) -> float | None:
        if current is None or delta is None:
            return None
        return current - delta

    @staticmethod
    def _format_number(value: float | None, *, suffix: str = "", decimals: int | None = None) -> str | None:
        if value is None:
            return None
        places = decimals
        if places is None:
            places = 0 if float(value).is_integer() else 2
        text = f"{value:.{places}f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return f"{text}{suffix}"

    @staticmethod
    def _format_money(value: float | None, *, decimals: int | None = 2) -> str | None:
        text = TodoService._format_number(value, decimals=decimals)
        return f"¥{text}" if text is not None else None

    @staticmethod
    def _format_pct(value: float | None) -> str | None:
        if value is None:
            return None
        sign = "+" if value > 0 else ""
        return f"{sign}{value:.2f}%"

    @staticmethod
    def _format_rate(value: float | None) -> str | None:
        if value is None:
            return None
        return f"{value * 100:.2f}%"

    @staticmethod
    def _metric_payload(
        *,
        key: str,
        label: str,
        current: float | None,
        previous: float | None,
        change_pct: float | None = None,
        current_text: str | None = None,
        previous_text: str | None = None,
    ) -> dict | None:
        if current is None and previous is None:
            return None
        pct = change_pct if change_pct is not None else TodoService._change_pct(current, previous)
        return {
            "key": key,
            "label": label,
            "current": current,
            "current_text": current_text or TodoService._format_number(current) or "未采集",
            "previous": previous,
            "previous_text": previous_text or TodoService._format_number(previous) or "未采集",
            "change_pct": pct,
            "change_text": TodoService._format_pct(pct) or "未采集",
        }

    @staticmethod
    def _is_samplebrand_low_consumption_payload(skill_id: str, payload: dict) -> bool:
        return (
            skill_id == SAMPLEBRAND_LOW_CONSUMPTION_SKILL_ID
            or payload.get("card_type") == "video_low_consumption_decision_card"
            or payload.get("analysis_schema") == "samplebrand_video_low_consumption_daily_operator_v1"
        )

    @staticmethod
    def _append_unique_text(items: list, text: str) -> None:
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return
        existing = {" ".join(str(item or "").split()) for item in items}
        if normalized not in existing:
            items.append(text)

    @staticmethod
    def _append_unique_dict(items: list, row: dict, *, key_fields: tuple[str, ...]) -> None:
        key = tuple(str(row.get(field) or "").strip() for field in key_fields)
        for item in items:
            if not isinstance(item, dict):
                continue
            if tuple(str(item.get(field) or "").strip() for field in key_fields) == key:
                return
        items.append(row)

    @staticmethod
    def _chart_keys(payload: dict) -> set[str]:
        return {
            str(item.get("key") or "").strip()
            for item in TodoService._as_list(payload.get("chart_sections"))
            if isinstance(item, dict)
        }

    @staticmethod
    def _operation_lifecycle_available(operation: dict) -> bool:
        lifecycle = TodoService._as_dict(operation.get("benchmark_qianchuan_lifecycle"))
        if lifecycle.get("available") is True:
            return True
        points = TodoService._as_dict(operation.get("qianchuan_lifecycle_value_points"))
        return any(str(points.get(key) or "").strip() for key in ("manager_value", "consumer_value", "optimization_focus"))

    @staticmethod
    def _operation_has_benchmark(operation: dict) -> bool:
        return bool(
            str(operation.get("benchmark_video_id") or "").strip()
            or str(operation.get("benchmark_video_title") or operation.get("benchmark_title") or "").strip()
            or str(operation.get("benchmark_label") or "").strip()
        )

    @staticmethod
    def _samplebrand_lifecycle_gap_text(operation: dict) -> str:
        benchmark_id = str(operation.get("benchmark_video_id") or "").strip()
        benchmark_title = str(operation.get("benchmark_video_title") or operation.get("benchmark_title") or "").strip()
        target = benchmark_id or benchmark_title or "高消耗参考视频"
        return (
            f"已对{target}尝试读取千川点击生命周期，但本条没有可用秒级 interaction_lifecycle。"
            "因此本次不能把“第几秒点击峰值”写成确定结论，只能用云视频消耗/点击/转化指标、完整视频视觉证据和对标选择过程作为代理依据。"
        )

    @staticmethod
    def _enrich_samplebrand_lifecycle_gap(payload: dict) -> dict:
        if not isinstance(payload, dict):
            return payload
        operations = [
            item
            for item in TodoService._as_list(payload.get("operation_actions"))
            if isinstance(item, dict)
        ]
        gap_operations = [
            item
            for item in operations
            if TodoService._operation_has_benchmark(item)
            and not TodoService._operation_lifecycle_available(item)
        ]
        if not gap_operations:
            return payload

        payload = dict(payload)
        operations = [dict(item) if item in gap_operations else item for item in operations]
        enriched_ops: list[dict] = []
        gap_count = 0
        for operation in operations:
            if not isinstance(operation, dict):
                enriched_ops.append(operation)
                continue
            if operation not in gap_operations and TodoService._operation_lifecycle_available(operation):
                enriched_ops.append(operation)
                continue
            if not TodoService._operation_has_benchmark(operation):
                enriched_ops.append(operation)
                continue
            gap_count += 1
            gap_text = TodoService._samplebrand_lifecycle_gap_text(operation)
            benchmark_id = str(operation.get("benchmark_video_id") or "").strip()
            benchmark_title = str(operation.get("benchmark_video_title") or operation.get("benchmark_title") or "").strip()
            lifecycle = dict(TodoService._as_dict(operation.get("benchmark_qianchuan_lifecycle")))
            lifecycle.setdefault("available", False)
            lifecycle.setdefault("status", "unavailable")
            lifecycle.setdefault("reason", "qianchuan_material_lifecycle_not_available")
            lifecycle.setdefault("fallback_basis", [
                "云视频高消耗/高点击投放指标",
                "low 与 benchmark 完整视频视觉证据",
                "同主题/相似主题对标选择诊断",
            ])
            operation["benchmark_qianchuan_lifecycle"] = lifecycle
            operation["qianchuan_lifecycle_evidence"] = gap_text
            operation["qianchuan_lifecycle_value_points"] = {
                **TodoService._as_dict(operation.get("qianchuan_lifecycle_value_points")),
                "manager_value": (
                    f"{gap_text} 管理者应先补查千川 material_id 映射、计划预算、出价、人群包、学习期和频控，"
                    "再判断低消耗是投放承接受限还是内容弱。"
                ),
                "consumer_value": (
                    "消费者秒级触发点当前不能被千川曲线直接证明；只能参考高消耗视频画面里的痛点切入、信任背书、价格理由和 CTA，"
                    "并通过同主题小流量 A/B 复测验证。"
                ),
                "optimization_focus": (
                    "保留原主题做轻改，优先补首屏触发、信任证据、价格锚点和行动引导；不要直接照搬参考视频脚本。"
                ),
                "data_gap": "千川素材内容分析未返回可用秒级生命周期。",
                "benchmark_video_id": benchmark_id,
                "benchmark_video_title": benchmark_title,
            }
            root_causes = list(TodoService._as_list(operation.get("root_causes")))
            TodoService._append_unique_text(root_causes, f"高点击依据边界：{gap_text}")
            operation["root_causes"] = root_causes
            data_checks = list(TodoService._as_list(operation.get("data_checks")))
            TodoService._append_unique_text(
                data_checks,
                "千川生命周期补查：确认高消耗参考视频在千川素材分析中的 material_id、账号 aavid、营销目标和素材列表搜索是否能命中。",
            )
            operation["data_checks"] = data_checks
            enriched_ops.append(operation)
        payload["operation_actions"] = enriched_ops

        key_metrics = list(TodoService._as_list(payload.get("key_metrics")))
        TodoService._append_unique_dict(
            key_metrics,
            {
                "name": "千川生命周期",
                "value": f"{gap_count}条未命中",
                "delta": "秒级点击生命周期不可用时，只能使用云视频投放指标 + 完整视频视觉证据作为代理依据。",
                "source": "qianchuan.video_content_analysis / cloud_video.daily_analysis_input",
                "status": "需补查 material_id 映射",
            },
            key_fields=("name",),
        )
        payload["key_metrics"] = key_metrics

        data_quality_items = list(TodoService._as_list(payload.get("data_quality_items")))
        TodoService._append_unique_dict(
            data_quality_items,
            {
                "dimension": "千川点击生命周期",
                "issue": "高消耗参考未返回可用秒级 interaction_lifecycle；不能把点击峰值秒点当作确定结论。",
                "suggestion": "补查千川 material_id/aavid/营销目标映射；当前先用云视频消耗、点击、转化和完整视频证据做代理判断。",
            },
            key_fields=("dimension",),
        )
        payload["data_quality_items"] = data_quality_items

        analysis_basis = list(TodoService._as_list(payload.get("analysis_basis")))
        TodoService._append_unique_dict(
            analysis_basis,
            {
                "dimension": "千川生命周期缺口",
                "basis": (
                    f"本账号 {gap_count} 条对标视频未返回可用千川秒级生命周期。"
                    "相关结论不得写成秒级峰值判断，只能写成代理指标 + 视觉证据 + 补查项。"
                ),
                "data_gap": "千川素材列表未命中或 interaction_lifecycle 不可用。",
                "confidence": "中",
            },
            key_fields=("dimension",),
        )
        payload["analysis_basis"] = analysis_basis

        chart_sections = list(TodoService._as_list(payload.get("chart_sections")))
        if "qianchuan_lifecycle_status" not in TodoService._chart_keys(payload):
            chart_sections.append({
                "key": "qianchuan_lifecycle_status",
                "type": "bar",
                "unit": "条",
                "title": "千川生命周期数据状态",
                "description": "秒级生命周期不可用时，结论必须降级为云视频代理指标 + 视觉证据 + 运营补查，不能声称已定位点击峰值秒点。",
                "items": [
                    {"key": "qianchuan_available", "label": "千川秒级生命周期", "value": 0, "unit": "条", "detail": "未命中"},
                    {"key": "proxy_metrics", "label": "云视频代理指标", "value": gap_count, "unit": "条", "detail": "消耗/点击/转化"},
                    {"key": "visual_evidence", "label": "完整视频证据", "value": gap_count, "unit": "条", "detail": "low+benchmark"},
                ],
            })
        payload["chart_sections"] = chart_sections

        forbidden_actions = list(TodoService._as_list(payload.get("forbidden_actions")))
        TodoService._append_unique_text(
            forbidden_actions,
            "千川生命周期未命中时，不要把秒级点击峰值、流失秒点写成确定结论。",
        )
        payload["forbidden_actions"] = forbidden_actions
        return payload

    @staticmethod
    def _normalize_samplebrand_low_consumption_payload(skill_id: str, payload: dict) -> dict:
        if not TodoService._is_samplebrand_low_consumption_payload(skill_id, payload):
            return payload
        return TodoService._enrich_samplebrand_lifecycle_gap(payload)

    @staticmethod
    def _can_override_dispatch_scope(user: User | None) -> bool:
        if user is None:
            return False
        if bool(getattr(user, "can_view_all", False)):
            return True
        role = (getattr(user, "role", "") or "").lower()
        return role in {"admin", "system_admin"}

    async def _request_has_manager_todo_for_actor_identity(
        self,
        db: AsyncSession,
        *,
        request_id: str,
        actor: User,
    ) -> bool:
        visible_assignee_ids = await visible_inbox_assignee_ids_for_user(db, actor)
        if not visible_assignee_ids:
            return False
        count = await db.scalar(
            select(func.count(AITodo.id))
            .where(AITodo.request_id == request_id)
            .where(AITodo.assignee.in_(visible_assignee_ids))
        )
        return bool(count or 0)

    async def _resolve_dispatch_org_unit_id(
        self,
        db: AsyncSession,
        *,
        skill: Skill | None,
        fallback_user: User | None = None,
    ) -> str | None:
        if skill and skill.org_unit_id:
            return skill.org_unit_id
        if skill and skill.department:
            matched_org_id = await db.scalar(
                select(OrgUnit.id)
                .where(OrgUnit.name == skill.department)
                .order_by(OrgUnit.id.asc())
                .limit(1)
            )
            if matched_org_id:
                return matched_org_id
        if fallback_user is not None:
            return await get_primary_department_id(db, fallback_user)
        return None

    async def _resolve_dispatch_org_unit_ids(
        self,
        db: AsyncSession,
        *,
        skill: Skill | None,
        fallback_user: User | None = None,
    ) -> set[str]:
        org_unit_id = await self._resolve_dispatch_org_unit_id(
            db,
            skill=skill,
            fallback_user=fallback_user,
        )
        if not org_unit_id:
            return set()
        subtree_ids = await expand_department_subtree(db, {org_unit_id})
        return subtree_ids or {org_unit_id}

    async def create_from_execution(
        self,
        db: AsyncSession,
        *,
        run_id: str,
        skill_id: str,
        skill_meta: dict,
        decision: dict,
    ) -> list[DecisionRequest]:
        """Skill 跑完后调用：

        1. 优先从 `decision["output_result"]["todos"]` 解析 Skill 主动推送的待办列表;
        2. 如果 Skill output 没有 todos 字段, 回退到旧逻辑（按 frontmatter 自动生成单条 review）。
           如果 Skill 明确返回 ``todos: []``，表示本次无需推送待办，不再回退 legacy。

        返回所有创建的 DecisionRequest 列表（可能 0/1/N 条）。
        """
        output = decision.get("output_result") if isinstance(decision, dict) else None
        if _is_execution_failure_output(output):
            logger.info("Skill {} run={} 为失败输出，跳过待办创建", skill_id, run_id)
            return []
        try:
            specs = parse_todo_specs(output if isinstance(output, dict) else None)
        except TodoSpecParseError as e:
            logger.warning("Skill {} 的 output.todos 解析失败, 不再回退 legacy 待办: {}", skill_id, e)
            raise AppError(
                "TODO_SPEC_INVALID",
                400,
                {"detail": str(e), "skill_id": skill_id, "run_id": run_id},
            ) from e

        if specs:
            skip_post_training_evaluation = (
                skill_id == TMALL_LINK_DECLINE_OPERATOR_SKILL_ID
                and len(specs) > TMALL_LINK_DECLINE_OPERATOR_BULK_TODO_EVALUATION_LIMIT
            )
            requests: list[DecisionRequest] = []
            for spec in specs:
                spec = await self._apply_skill_specific_inbox_routing(
                    db,
                    skill_id=skill_id,
                    spec=spec,
                    run_id=run_id,
                )
                req = await self._create_from_spec(
                    db,
                    spec=spec,
                    run_id=run_id,
                    skill_id=skill_id,
                    skill_meta=skill_meta,
                    fallback_decision=decision,
                    skip_post_training_evaluation=skip_post_training_evaluation,
                )
                if req:
                    requests.append(req)
            if requests:
                await self._invalidate_generated_data_cache_safe()
            return requests

        if isinstance(output, dict) and "todos" in output:
            logger.info("Skill {} run={} 明确返回空 todos，跳过 legacy 待办创建", skill_id, run_id)
            return []

        # 回退：旧的 frontmatter 推断
        legacy = await self._create_from_legacy(
            db,
            run_id=run_id,
            skill_id=skill_id,
            skill_meta=skill_meta,
            decision=decision,
        )
        if legacy:
            await self._invalidate_generated_data_cache_safe()
        return [legacy] if legacy else []

    async def _apply_skill_specific_inbox_routing(
        self,
        db: AsyncSession,
        *,
        skill_id: str,
        spec: TodoSpec,
        run_id: str,
    ) -> TodoSpec:
        if skill_id != TMALL_LINK_DECLINE_OPERATOR_SKILL_ID:
            return spec
        assignee = await self._resolve_tmall_operator_inbox_assignee(db)
        if not assignee:
            logger.warning(
                "Skill {} run={} 生成待办无法解析已配置的 inbox assignee，跳过该待办",
                skill_id,
                run_id,
            )
            spec.reviewers = []
            spec.reviewer_role = None
            return spec
        spec.reviewers = [assignee.id]
        spec.reviewer_role = None
        return spec

    async def _resolve_tmall_operator_inbox_assignee(self, db: AsyncSession) -> User | None:
        query = TMALL_LINK_DECLINE_OPERATOR_INBOX_ASSIGNEE_QUERY
        dingtalk_user_id = TMALL_LINK_DECLINE_OPERATOR_INBOX_DINGTALK_USER_ID
        if not query and not dingtalk_user_id:
            return None
        recipient_filters = []
        if query:
            recipient_filters.extend((User.name == query, User.username == query, User.id == query))
        if dingtalk_user_id:
            recipient_filters.append(User.dingtalk_user_id == dingtalk_user_id)
        stmt = (
            select(User)
            .where(User.is_active == True)  # noqa: E712
            .where(User.state == "active")
            .where(or_(*recipient_filters))
            .order_by(
                case((User.name == query, 0), else_=1).asc(),
                case((User.dingtalk_user_id == dingtalk_user_id, 0), else_=1).asc(),
                User.id.asc(),
            )
            .limit(5)
        )
        candidates = list((await db.execute(stmt)).scalars().all())
        if not candidates:
            return None
        exact_name = [user for user in candidates if user.name == query]
        pool = exact_name or candidates
        if len(pool) > 1:
            canonical = next(
                (user for user in pool if user.dingtalk_user_id == dingtalk_user_id),
                None,
            )
            if canonical:
                return await resolve_work_notice_user(db, canonical) or canonical
            logger.warning(
                "已配置的 inbox assignee 匹配到多个 active 用户: {}",
                [user.id for user in pool],
            )
            return None
        return await resolve_work_notice_user(db, pool[0]) or pool[0]

    @staticmethod
    async def _invalidate_generated_data_cache_safe() -> None:
        try:
            from app.common.cache import invalidate_generated_data_cache

            await invalidate_generated_data_cache()
        except Exception as exc:  # noqa: BLE001
            logger.debug("生成待办后清理生成数据缓存失败: {}", exc)

    async def _find_existing_request(
        self,
        db: AsyncSession,
        *,
        source_type: str,
        source_id: str,
        kind: str,
        skill_id: str,
    ) -> DecisionRequest | None:
        """幂等检查: 同一个 (source_type, source_id, kind, skill_id) 是否已存在 DecisionRequest。

        Skill 重试 / OpenClaw 重发结果时, SkillForge 应该返回已有的请求, 不创建新的。
        """
        result = await db.execute(
            select(DecisionRequest)
            .where(DecisionRequest.source_type == source_type)
            .where(DecisionRequest.source_id == source_id)
            .where(DecisionRequest.kind == kind)
            .where(DecisionRequest.skill_id == skill_id)
        )
        return result.scalar_one_or_none()

    async def _attach_post_training_evaluation(
        self,
        db: AsyncSession,
        *,
        skill_id: str,
        run_id: str | None,
        title: str,
        summary: str,
        kind: str,
        payload: dict,
    ) -> dict:
        if not should_evaluate_post_training_model(skill_id):
            return payload
        if not isinstance(payload, dict):
            return payload
        if isinstance(payload.get(POST_TRAINING_EVALUATION_KEY), dict):
            return payload
        dispatch_tasks = payload.get("dispatch_tasks") if isinstance(payload.get("dispatch_tasks"), list) else None
        evaluation = await evaluate_todo_with_post_training_model(
            db,
            skill_id=skill_id,
            run_id=run_id,
            title=title,
            summary=summary,
            kind=kind,
            payload=payload,
            dispatch_tasks=dispatch_tasks,
        )
        if not evaluation:
            return payload
        return {
            **payload,
            POST_TRAINING_EVALUATION_KEY: evaluation,
        }

    async def _ensure_existing_post_training_evaluation(
        self,
        db: AsyncSession,
        request: DecisionRequest,
        *,
        skip_post_training_evaluation: bool = False,
    ) -> None:
        if skip_post_training_evaluation:
            return
        payload = request.payload if isinstance(request.payload, dict) else {}
        enriched = await self._attach_post_training_evaluation(
            db,
            skill_id=request.skill_id,
            run_id=request.run_id,
            title=request.title,
            summary=request.summary or "",
            kind=request.kind,
            payload=payload,
        )
        if enriched is not payload and enriched != payload:
            request.payload = enriched
            await db.flush()

    async def _load_baseline_paid_snapshot(
        self,
        db: AsyncSession,
        *,
        skill_id: str,
        item_id: str,
        baseline_captured_at: str | None,
        period: dict | None = None,
        current_log_id: int | None = None,
    ) -> dict:
        if not item_id:
            return {}
        from app.execution.models import DecisionLog

        target_at = self._baseline_snapshot_target(self._as_dict(period), baseline_captured_at)
        stmt = (
            select(DecisionLog)
            .where(DecisionLog.skill_id == skill_id)
            .where(DecisionLog.is_sandbox == False)  # noqa: E712
            .where(DecisionLog.output_result.is_not(None))
        )
        if current_log_id is not None:
            stmt = stmt.where(DecisionLog.id != current_log_id)
        if target_at is not None:
            stmt = (
                stmt
                .where(DecisionLog.created_at >= target_at - timedelta(days=1))
                .where(DecisionLog.created_at <= target_at + timedelta(days=1))
                .order_by(DecisionLog.created_at.desc())
            )
        else:
            stmt = stmt.order_by(DecisionLog.created_at.desc())
        rows = (await db.execute(stmt.limit(50))).scalars().all()
        candidates: list[tuple[float, dict]] = []
        for row in rows:
            output = self._as_dict(row.output_result)
            paid = self._as_dict(
                self._as_dict(
                    self._as_dict(
                        self._as_dict(output.get("数据采集快照")).get("items")
                    ).get(item_id)
                ).get("paid")
            )
            if not paid:
                continue
            if target_at is None:
                return paid
            candidate_at = self._snapshot_candidate_time(output, item_id) or row.created_at
            if candidate_at is None:
                continue
            distance = abs((candidate_at - target_at).total_seconds())
            if distance <= 10 * 60:
                candidates.append((distance, paid))
        if candidates:
            return min(candidates, key=lambda row: row[0])[1]
        return {}

    async def _build_local_snapshot_compares(
        self,
        db: AsyncSession,
        *,
        request: DecisionRequest,
        payload: dict,
        decision_log,
    ) -> dict:
        output = self._as_dict(getattr(decision_log, "output_result", None))
        item_id = str(
            payload.get("item_id")
            or self._as_dict(payload.get("input")).get("item_id")
            or ""
        ).strip()
        if not output or not item_id:
            return {}
        snapshot_item = self._snapshot_item(output, item_id)
        collection_item = self._snapshot_collection_item(output, item_id)
        if not snapshot_item and not collection_item:
            return {}
        period = self._snapshot_period(output, snapshot_item or collection_item)
        node = self._as_dict(output.get("0855快照对比"))
        paid_report_item = self._section_item(output, "付费端诊断报告", item_id)
        free_report_item = self._section_item(output, "免费流分析结果", item_id)
        item360 = self._as_dict(collection_item.get("item360"))
        compare: dict[str, dict] = {}

        free_visitor = self._metric_payload(
            key="visitor",
            label="免费搜索访客",
            current=self._coalesce_number(
                snapshot_item.get("free_search_visitor_count"),
                item360.get("free_visitor_count"),
                free_report_item.get("free_visitor_count"),
            ),
            previous=self._coalesce_number(
                snapshot_item.get("free_search_visitor_prev"),
                item360.get("free_visitor_prev"),
                free_report_item.get("free_visitor_prev"),
            ),
            change_pct=self._coalesce_number(
                snapshot_item.get("free_search_visitor_change_pct"),
                free_report_item.get("visitor_change_pct"),
            ),
        )
        free_conversion_current = self._coalesce_number(
            snapshot_item.get("free_search_conversion_rate"),
            item360.get("free_conversion_rate"),
            free_report_item.get("free_conversion_rate"),
        )
        free_conversion_previous = self._coalesce_number(
            snapshot_item.get("free_search_conversion_prev"),
            item360.get("free_conversion_prev"),
            free_report_item.get("free_conversion_prev"),
        )
        free_conversion = self._metric_payload(
            key="conversion",
            label="免费搜索转化率",
            current=free_conversion_current,
            previous=free_conversion_previous,
            change_pct=self._coalesce_number(
                snapshot_item.get("free_search_conversion_change_pct"),
                free_report_item.get("conversion_change_pct"),
            ),
            current_text=self._format_rate(free_conversion_current),
            previous_text=self._format_rate(free_conversion_previous),
        )
        free_metrics = [row for row in [free_visitor, free_conversion] if row]
        if free_metrics:
            compare["free_search_compare"] = {
                "source": "local_snapshot",
                "status": "本地历史快照回填",
                "period": period,
                "metrics": free_metrics,
            }

        current_paid = self._as_dict(collection_item.get("paid"))
        baseline_paid = await self._load_baseline_paid_snapshot(
            db,
            skill_id=request.skill_id,
            item_id=item_id,
            baseline_captured_at=str(node.get("baseline_captured_at") or ""),
            period=period,
            current_log_id=getattr(decision_log, "id", None),
        )
        roi_current = self._coalesce_number(current_paid.get("roi"), paid_report_item.get("roi_value"))
        cpc_current = self._coalesce_number(current_paid.get("ppc"), paid_report_item.get("ppc_value"))
        roi_delta = self._number(paid_report_item.get("roi_delta"))
        cpc_delta = self._number(paid_report_item.get("ppc_delta"))
        baseline_charge = self._number(baseline_paid.get("charge"))
        baseline_roi = self._number(baseline_paid.get("roi"))
        baseline_cpc = self._number(baseline_paid.get("ppc"))
        charge_current = self._coalesce_number(
            snapshot_item.get("paid_charge"),
            current_paid.get("charge"),
            paid_report_item.get("charge_value"),
        )
        charge_previous = self._coalesce_number(
            snapshot_item.get("paid_charge_prev"),
            baseline_paid.get("charge"),
            paid_report_item.get("charge_prev"),
        )
        roi_previous = self._coalesce_number(
            paid_report_item.get("roi_prev"),
            baseline_paid.get("roi"),
            self._previous_from_delta(roi_current, roi_delta),
        )
        cpc_previous = self._coalesce_number(
            paid_report_item.get("ppc_prev"),
            baseline_paid.get("ppc"),
            self._previous_from_delta(cpc_current, cpc_delta),
        )
        paid_metrics = [
            self._metric_payload(
                key="charge",
                label="实时花费金额",
                current=charge_current,
                previous=charge_previous,
                change_pct=self._coalesce_number(
                    snapshot_item.get("paid_charge_change_pct"),
                    None if baseline_charge is not None else paid_report_item.get("charge_change_pct"),
                ),
                current_text=self._format_money(charge_current),
                previous_text=self._format_money(charge_previous),
            ),
            self._metric_payload(
                key="roi",
                label="ROI",
                current=roi_current,
                previous=roi_previous,
                change_pct=None if baseline_roi is not None else self._number(paid_report_item.get("roi_change_pct")),
                current_text=self._format_number(roi_current, decimals=4),
                previous_text=self._format_number(roi_previous, decimals=4),
            ),
            self._metric_payload(
                key="cpc",
                label="CPC",
                current=cpc_current,
                previous=cpc_previous,
                change_pct=None if baseline_cpc is not None else self._number(paid_report_item.get("ppc_change_pct")),
                current_text=self._format_money(cpc_current, decimals=4),
                previous_text=self._format_money(cpc_previous, decimals=4),
            ),
        ]
        paid_metrics = [row for row in paid_metrics if row]
        if paid_metrics:
            compare["paid_realtime_compare"] = {
                "source": "local_snapshot",
                "status": "后台返回聚合值，昨日同刻来自本地历史快照",
                "period": period,
                "metrics": paid_metrics,
            }
        return compare

    async def _create_from_legacy(
        self,
        db: AsyncSession,
        *,
        run_id: str,
        skill_id: str,
        skill_meta: dict,
        decision: dict,
    ) -> DecisionRequest | None:
        reviewers = await resolve_reviewers(db, skill_meta)
        if not reviewers:
            return None

        source_type = f"skill_execution_l{skill_meta.get('approval_level', 1)}"
        existing = await self._find_existing_request(
            db,
            source_type=source_type,
            source_id=run_id,
            kind="review",
            skill_id=skill_id,
        )
        if existing:
            logger.info(
                "Skill {} 已有相同 source_id={} 的待办 {}, 幂等返回",
                skill_id, run_id, existing.id,
            )
            await self._ensure_existing_post_training_evaluation(db, existing)
            return existing

        # P3-契约 D3：execution_service 抽出的 primary_indicator 写入 payload，
        # 列表 API 能直接读，前端不用再从 output_result 反查。
        legacy_payload: dict = {
            "input": decision.get("input_snapshot"),
            "output": decision.get("output_result"),
            "suggested": decision.get("suggested_action"),
        }
        primary_indicator = decision.get("primary_indicator")
        if isinstance(primary_indicator, dict):
            legacy_payload["primary_indicator"] = primary_indicator
        title = f"{skill_meta.get('name') or skill_id} 需要审批"
        summary = self._build_summary(decision)
        legacy_payload = await self._attach_post_training_evaluation(
            db,
            skill_id=skill_id,
            run_id=run_id,
            title=title,
            summary=summary,
            kind="review",
            payload=legacy_payload,
        )

        request = DecisionRequest(
            id=f"dr-{uuid4().hex}",
            kind="review",
            source_type=source_type,
            source_id=run_id,
            skill_id=skill_id,
            run_id=run_id,
            # 回填 decision_log_id 让 /inbox 报告 ↔ 待办双向关联覆盖 legacy Skill（spec inbox §5.3）
            decision_log_id=decision.get("decision_log_id"),
            title=title,
            summary=summary,
            payload=legacy_payload,
            decision_mode=skill_meta.get("decision_mode", "any_of"),
            sla_at=now_bjt() + timedelta(hours=settings.TODO_SLA_HOURS),
        )
        db.add(request)
        await db.flush()

        todos: list[AITodo] = []
        for assignee in reviewers:
            todo = AITodo(request_id=request.id, kind="review", assignee=assignee)
            todos.append(todo)
            db.add(todo)
            db.add(
                Notification(
                    user_id=assignee,
                    type="todo_request",
                    title=request.title,
                    body=request.summary or "",
                    link=f"/todos/{todo.id}",
                )
            )

        await db.flush()

        return request

    async def _create_from_spec(
        self,
        db: AsyncSession,
        *,
        spec: TodoSpec,
        run_id: str,
        skill_id: str,
        skill_meta: dict,
        fallback_decision: dict,
        skip_post_training_evaluation: bool = False,
    ) -> DecisionRequest | None:
        """根据 Skill output 的 todo spec 创建一条 DecisionRequest + 关联 todos / dispatch tasks。"""
        try:
            reviewers = await resolve_reviewers_from_spec(
                db,
                reviewers=spec.reviewers,
                reviewer_role=spec.reviewer_role,
            )
        except AppError as e:
            logger.warning("Skill {} 的 todo spec reviewer 解析失败: {}", skill_id, e.detail)
            return None

        if not reviewers:
            logger.warning("Skill {} 的 todo spec 没有解析出审批人, 跳过", skill_id)
            return None

        sla_hours = spec.sla_hours or settings.TODO_SLA_HOURS

        # 构造 payload：spec.payload 优先。Skill 显式给 payload 时，它应是给运营看的
        # 决策卡片；不要再把完整 input/output 快照塞进去，避免详情页被原始 JSON 淹没。
        payload = dict(spec.payload or {})
        if not payload:
            payload = {
                "input": fallback_decision.get("input_snapshot"),
                "output": fallback_decision.get("output_result"),
            }
        else:
            payload.setdefault("_debug_ref", {
                "run_id": run_id,
                "decision_log_id": fallback_decision.get("decision_log_id"),
            })
        # P3-契约 D3：把 execution_service 抽出的 primary_indicator 落到 payload，
        # 供 /api/todos/ 列表 API 直读（无需反查 decision_log）。Skill 没声明时为 None。
        primary_indicator = fallback_decision.get("primary_indicator")
        if isinstance(primary_indicator, dict):
            payload["primary_indicator"] = primary_indicator
        if spec.kind == "dispatch":
            payload["dispatch_tasks"] = [
                {
                    "executor": t.executor,
                    "content": t.content,
                    "deadline": isoformat_bjt(t.deadline),
                    "extra": t.extra,
                }
                for t in spec.tasks
            ]
        payload = self._normalize_samplebrand_low_consumption_payload(skill_id, payload)

        # 同一个 spec (kind+title 在 spec 内, 但去重 key 是 source_type+source_id+kind+skill_id)
        # 一个 run 内 Skill 可能产出多条同 kind 待办, 用 spec.title 作为额外区分进 source_id
        source_id = f"{run_id}:{spec.title[:80]}"
        source_type = f"skill_execution_{spec.kind}"

        existing = await self._find_existing_request(
            db,
            source_type=source_type,
            source_id=source_id,
            kind=spec.kind,
            skill_id=skill_id,
        )
        if existing:
            logger.info(
                "Skill {} 已有相同 spec source_id={} 的待办 {}, 幂等返回",
                skill_id, source_id, existing.id,
            )
            await self._ensure_existing_post_training_evaluation(
                db,
                existing,
                skip_post_training_evaluation=skip_post_training_evaluation,
            )
            return existing
        summary = spec.summary or self._build_summary(fallback_decision)
        if not skip_post_training_evaluation:
            payload = await self._attach_post_training_evaluation(
                db,
                skill_id=skill_id,
                run_id=run_id,
                title=spec.title,
                summary=summary,
                kind=spec.kind,
                payload=payload,
            )

        request = DecisionRequest(
            id=f"dr-{uuid4().hex}",
            kind=spec.kind,
            source_type=source_type,
            source_id=source_id,
            skill_id=skill_id,
            run_id=run_id,
            decision_log_id=fallback_decision.get("decision_log_id"),
            title=spec.title,
            summary=summary,
            payload=payload,
            decision_mode=spec.decision_mode,
            sla_at=now_bjt() + timedelta(hours=sla_hours),
            callback_payload=spec.callback,
            callback_status="pending" if spec.callback else "skipped",
        )
        db.add(request)
        await db.flush()

        todos: list[AITodo] = []
        for assignee in reviewers:
            todo = AITodo(request_id=request.id, kind=spec.kind, assignee=assignee)
            todos.append(todo)
            db.add(todo)
            db.add(
                Notification(
                    user_id=assignee,
                    type=f"todo_{spec.kind}",
                    title=request.title,
                    body=request.summary or "",
                    link=f"/todos/{todo.id}",
                )
            )
        await db.flush()

        # dispatch 子任务先全部建出来，状态 awaiting_dispatch；管理者审批通过后才推钉钉
        if spec.kind == "dispatch":
            for task_spec in spec.tasks:
                db.add(
                    TodoDispatchTask(
                        request_id=request.id,
                        manager_todo_id=todos[0].id if todos else None,
                        executor=task_spec.executor,
                        content=task_spec.content,
                        deadline=task_spec.deadline,
                        extra=task_spec.extra,
                        status="awaiting_dispatch",
                    )
                )
            await db.flush()

        return request

    async def list_todos(
        self,
        db: AsyncSession,
        *,
        current_user: User,
        status: str | None = None,
        kind: str | None = None,
        skill_id: str | None = None,
        decision_log_id: int | None = None,
        run_id: str | None = None,
        priority: str | None = None,
        assignee: str | None = None,
        sla_state: str | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        date_from: str | None = None,
        date_to: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        global_reader = is_global_inbox_reader(current_user)
        filters = [DecisionRequest.archived_at.is_(None)]
        assignee_filter = (assignee or "").strip()
        if global_reader:
            if assignee_filter in {"me", current_user.id}:
                filters.append(AITodo.assignee == current_user.id)
            elif assignee_filter:
                filters.append(AITodo.assignee == assignee_filter)
        else:
            visible_assignee_ids, identity_has_global_scope = (
                await visible_inbox_assignee_scope_for_user(db, current_user)
            )
            if assignee_filter and assignee_filter not in {"me", current_user.id}:
                if assignee_filter not in visible_assignee_ids:
                    raise AppError("AUTH_PERMISSION_DENIED", 403)
                filters.append(AITodo.assignee == assignee_filter)
            else:
                filters.append(AITodo.assignee.in_(visible_assignee_ids))
            # P0-4 部门隔离：除全局可见角色外，要求 skill 不存在（orphan）或部门匹配。
            # outerjoin 兼容 skill 已被删除的 orphan todo，避免破坏 assignee 自己的可见性。
            if not identity_has_global_scope:
                filters.append(or_(Skill.id.is_(None), Skill.department == current_user.department))
        normalized_status = (status or "").strip().lower()
        if normalized_status == "feedback_done":
            filters.append(AITodo.kind == "dispatch")
            filters.append(
                exists()
                .where(TodoDispatchTask.request_id == DecisionRequest.id)
                .where(TodoDispatchTask.status == "done")
            )
        elif normalized_status:
            if normalized_status == "done":
                filters.append(AITodo.status.in_(["approved", "rejected", "resolved_by_peer"]))
            elif "," in normalized_status:
                statuses = [
                    item.strip()
                    for item in normalized_status.split(",")
                    if item.strip()
                ]
                if statuses:
                    filters.append(AITodo.status.in_(statuses))
            else:
                filters.append(AITodo.status == normalized_status)
        if kind:
            filters.append(AITodo.kind == kind)
        if skill_id:
            filters.append(DecisionRequest.skill_id == skill_id)
        if decision_log_id:
            filters.append(DecisionRequest.decision_log_id == decision_log_id)
        if run_id:
            filters.append(DecisionRequest.run_id == run_id)
        if priority and priority in {"P0", "P1", "P2", "P3"}:
            # 结构化 priority 优先，标题前缀兜底；兼容 input/dispatch task extra 里的 priority。
            filters.append(or_(
                DecisionRequest.payload["priority"].astext.ilike(priority),
                DecisionRequest.payload["input"]["priority"].astext.ilike(priority),
                DecisionRequest.title.ilike(f"{priority}｜%"),
                DecisionRequest.title.ilike(f"{priority}|%"),
                DecisionRequest.title.ilike(f"{priority} %"),
                func.upper(func.coalesce(DecisionRequest.payload["priority"].astext, "")) == priority,
                exists()
                .where(TodoDispatchTask.request_id == DecisionRequest.id)
                .where(TodoDispatchTask.extra["priority"].astext.ilike(priority)),
            ))
        normalized_sla_state = (sla_state or "").strip().lower()
        if normalized_sla_state:
            now = now_bjt()
            if normalized_sla_state == "due_soon":
                filters.append(AITodo.status == "pending")
                filters.append(DecisionRequest.sla_at <= now + timedelta(hours=24))
            elif normalized_sla_state == "due_24h":
                filters.append(AITodo.status == "pending")
                filters.append(DecisionRequest.sla_at >= now)
                filters.append(DecisionRequest.sla_at <= now + timedelta(hours=24))
            elif normalized_sla_state == "overdue":
                filters.append(AITodo.status == "pending")
                filters.append(DecisionRequest.sla_at < now)
        search_text = (search or "").strip()
        if search_text:
            like = f"%{search_text}%"
            # 仅搜稳定业务字段，不做 payload JSONB 全文 cast，避免大量不相关命中。
            filters.append(or_(
                DecisionRequest.title.ilike(like),
                DecisionRequest.summary.ilike(like),
                DecisionRequest.skill_id.ilike(like),
                DecisionRequest.payload["priority"].astext.ilike(like),
                DecisionRequest.payload["item_id"].astext.ilike(like),
                DecisionRequest.payload["object_id"].astext.ilike(like),
                DecisionRequest.payload["item_title"].astext.ilike(like),
                DecisionRequest.payload["object_title"].astext.ilike(like),
                DecisionRequest.payload["input"]["item_id"].astext.ilike(like),
                DecisionRequest.payload["input"]["object_id"].astext.ilike(like),
                DecisionRequest.payload["input"]["item_title"].astext.ilike(like),
                DecisionRequest.payload["input"]["object_title"].astext.ilike(like),
                exists()
                .where(TodoDispatchTask.request_id == DecisionRequest.id)
                .where(or_(
                    TodoDispatchTask.content.ilike(like),
                    TodoDispatchTask.ack_note.ilike(like),
                    TodoDispatchTask.extra["item_id"].astext.ilike(like),
                    TodoDispatchTask.extra["object_id"].astext.ilike(like),
                    TodoDispatchTask.extra["item_title"].astext.ilike(like),
                    TodoDispatchTask.extra["object_title"].astext.ilike(like),
                    TodoDispatchTask.extra["priority"].astext.ilike(like),
                )),
            ))
        filters.extend(self._date_filters(date_from, date_to))
        count_stmt = (
            select(func.count(AITodo.id))
            .join(DecisionRequest, DecisionRequest.id == AITodo.request_id)
            .outerjoin(Skill, Skill.id == DecisionRequest.skill_id)
            .where(*filters)
        )
        total = await db.scalar(count_stmt) or 0

        # GAP-1 富字段 list：一次 JOIN 同时取 Skill（复用已有 outerjoin），后续批量补
        # aggregate_progress / metrics_preview / requester 避免 N+1。
        stmt = (
            select(AITodo, DecisionRequest, Skill)
            .join(DecisionRequest, DecisionRequest.id == AITodo.request_id)
            .outerjoin(Skill, Skill.id == DecisionRequest.skill_id)
            .where(*filters)
        )
        offset = (page - 1) * page_size
        normalized_sort_by = sort_by if sort_by in {"created_at", "sla_at", "ranking_visitors", "ranking_orders", "decline_coef"} else "created_at"
        normalized_sort_order = "asc" if sort_order == "asc" else "desc"
        if normalized_sort_by in {"ranking_visitors", "ranking_orders", "decline_coef"}:
            rows = (await db.execute(stmt.order_by(AITodo.created_at.desc()))).all()
            rows = self._sort_todo_rows(rows, normalized_sort_by, normalized_sort_order)
            rows = rows[offset: offset + page_size]
        else:
            order_column = DecisionRequest.sla_at if normalized_sort_by == "sla_at" else AITodo.created_at
            order_expr = order_column.asc() if normalized_sort_order == "asc" else order_column.desc()
            rows = (await db.execute(
                stmt.order_by(order_expr)
                .offset(offset)
                .limit(page_size)
            )).all()
        items = await self._enrich_list_items(db, rows, current_user=current_user)
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    @staticmethod
    def _parse_metric_number(value: object) -> float | None:
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if not text:
            return None
        match = re.search(r"[-+]?\d+(?:\.\d+)?", text.replace(",", ""))
        if not match:
            return None
        try:
            return float(match.group(0))
        except ValueError:
            return None

    @classmethod
    def _payload_number(cls, payload: dict | None, key: str) -> float | None:
        if not isinstance(payload, dict):
            return None
        candidates = [payload.get(key)]
        if key == "decline_coef":
            payment_change = cls._extract_payment_change_pct(payload)
            if payment_change is not None:
                return abs(payment_change)
            primary = payload.get("primary_indicator")
            if isinstance(primary, dict):
                candidates.extend([
                    primary.get("numeric_value"),
                    primary.get("value"),
                    primary.get("display_value"),
                ])
        for value in candidates:
            parsed = cls._parse_metric_number(value)
            if parsed is not None:
                return parsed
        return None

    @classmethod
    def _extract_metric_rows(cls, payload: dict | None) -> list[dict]:
        if not isinstance(payload, dict):
            return []
        raw_metrics: list[object] = []

        def _extend_metrics(value: object) -> None:
            if isinstance(value, list):
                raw_metrics.extend(value)

        def _extend_sections(value: object) -> None:
            if not isinstance(value, list):
                return
            for section in value:
                if isinstance(section, dict):
                    _extend_metrics(section.get("metrics"))

        _extend_metrics(payload.get("metrics"))
        _extend_metrics(payload.get("key_metrics"))
        _extend_sections(payload.get("metric_sections"))
        output = payload.get("output")
        if isinstance(output, dict):
            _extend_metrics(output.get("metrics"))
            _extend_metrics(output.get("key_metrics"))
            _extend_sections(output.get("metric_sections"))
        return [item for item in raw_metrics if isinstance(item, dict)]

    @staticmethod
    def _metric_text(item: dict, *keys: str) -> str:
        for key in keys:
            value = item.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    @classmethod
    def _find_payload_metric(cls, payload: dict | None, labels: set[str]) -> dict | None:
        for item in cls._extract_metric_rows(payload):
            label = cls._metric_text(item, "label", "name", "dimension")
            if label in labels:
                return item
        return None

    @classmethod
    def _extract_payment_change_pct(cls, payload: dict | None) -> float | None:
        if not isinstance(payload, dict):
            return None
        for key in _PAYMENT_CHANGE_KEYS:
            parsed = cls._parse_metric_number(payload.get(key))
            if parsed is not None:
                return parsed
        for item in cls._extract_metric_rows(payload):
            label = cls._metric_text(item, "label", "name", "dimension")
            if label == "—变化率—":
                note = cls._metric_text(item, "note", "delta", "value")
                match = re.search(r"(?:支付|成交额)\s*([-+]?\d+(?:\.\d+)?%)", note)
                if match:
                    return cls._parse_metric_number(match.group(1))
            if label in {"支付金额变化率", "支付金额环比", "实时支付金额变化率"}:
                parsed = cls._parse_metric_number(
                    cls._metric_text(item, "value", "delta", "note", "status")
                )
                if parsed is not None:
                    return parsed
        return None

    @classmethod
    def _format_payment_change(cls, value: float | None) -> str | None:
        if value is None:
            return None
        sign = "+" if value > 0 else ""
        return f"{sign}{round(value, 2)}%"

    @classmethod
    def _sort_todo_rows(cls, rows: list, sort_by: str, sort_order: str) -> list:
        present = []
        missing = []
        for row in rows:
            request = row[1]
            value = cls._payload_number(getattr(request, "payload", None), sort_by)
            if value is None:
                missing.append(row)
            else:
                present.append((value, row))
        present.sort(key=lambda item: item[0], reverse=(sort_order == "desc"))
        return [row for _, row in present] + missing

    async def _enrich_list_items(
        self,
        db: AsyncSession,
        rows: list,
        *,
        current_user: User,
    ) -> list[dict]:
        """GAP-1：把 list_todos 的 3 元行预加载后序列化为富字段卡片。

        预加载分三组（均为 IN 批量查询，避免 N+1）：
        - aggregate_progress：按 request_id 统计同 DR 下所有 AITodo 的 total/done/waiting_on
        - decision_log：output_result.reports[0].metrics → metrics_preview；
          input_snapshot.{requester|operator|user_id} → requester_id 候选
        - requester + org：按 requester_id 批量拼用户名 + primary membership org_name
        """
        if not rows:
            return []
        request_ids = {r.id for _, r, _ in rows if r is not None}
        log_ids = {r.decision_log_id for _, r, _ in rows if r is not None and r.decision_log_id}
        progress_map = await self._fetch_aggregate_progress(db, request_ids)
        dispatch_completion_map = await self._fetch_dispatch_completions(db, request_ids)
        dispatch_default_map = await self._fetch_dispatch_default_summaries(
            db,
            rows,
            current_user=current_user,
        )
        log_extras = await self._fetch_decision_log_extras(db, log_ids)
        requester_ids = {
            info["requester_id"] for info in log_extras.values() if info.get("requester_id")
        }
        requester_map = await self._fetch_requester_info(db, requester_ids)
        return [
            self._serialize_list_item(
                todo,
                request,
                skill=skill,
                progress=progress_map.get(request.id) if request else None,
                dispatch_completions=dispatch_completion_map.get(request.id) if request else None,
                dispatch_default_summary=dispatch_default_map.get(request.id) if request else None,
                log_extra=log_extras.get(getattr(request, "decision_log_id", None)) if request else None,
                requester_map=requester_map,
            )
            for todo, request, skill in rows
        ]

    async def _fetch_aggregate_progress(
        self,
        db: AsyncSession,
        request_ids: set[str],
    ) -> dict[str, dict]:
        """一次 IN 查询拿全部相关 todo 后 Python 端聚合 —— 避免 FILTER 的 SQL
        方言差异（SQLAlchemy 2.x async 对 aggregate FILTER 支持有限）。
        """
        if not request_ids:
            return {}
        stmt = select(
            AITodo.request_id,
            AITodo.status,
            AITodo.assignee,
        ).where(AITodo.request_id.in_(list(request_ids)))
        aggregate: dict[str, dict[str, list]] = {}
        for req_id, status, assignee in (await db.execute(stmt)).all():
            bucket = aggregate.setdefault(req_id, {"total": [], "waiting_on": []})
            bucket["total"].append(status)
            if status == "pending" and assignee:
                bucket["waiting_on"].append(assignee)
        out: dict[str, dict] = {}
        for req_id, info in aggregate.items():
            total_list = info["total"]
            waiting = info["waiting_on"]
            out[req_id] = {
                "total": len(total_list),
                "done": sum(1 for s in total_list if s != "pending"),
                # 去重保持插入顺序
                "waiting_on": list(dict.fromkeys(waiting)),
            }
        return out

    async def _fetch_dispatch_completions(
        self,
        db: AsyncSession,
        request_ids: set[str],
    ) -> dict[str, list[dict]]:
        """批量拿派发子任务完成记录，供“同事已处理”列表直接展示执行结果。"""
        if not request_ids:
            return {}
        stmt = (
            select(TodoDispatchTask, User.name)
            .outerjoin(User, User.id == TodoDispatchTask.executor)
            .where(TodoDispatchTask.request_id.in_(list(request_ids)))
            .where(TodoDispatchTask.status == "done")
            .order_by(TodoDispatchTask.request_id.asc(), TodoDispatchTask.ack_at.desc(), TodoDispatchTask.id.desc())
        )
        out: dict[str, list[dict]] = {}
        for task, executor_name in (await db.execute(stmt)).all():
            bucket = out.setdefault(task.request_id, [])
            if len(bucket) >= 5:
                continue
            bucket.append({
                "task_id": task.id,
                "executor": task.executor,
                "executor_name": executor_name,
                "content": task.content,
                "ack_at": isoformat_bjt(task.ack_at),
                "ack_note": task.ack_note,
                "ack_channel": task.ack_channel,
            })
        return out

    async def _fetch_dispatch_default_summaries(
        self,
        db: AsyncSession,
        rows: list,
        *,
        current_user: User,
    ) -> dict[str, dict]:
        dispatch_rows = [
            (request, skill)
            for _, request, skill in rows
            if request is not None and request.kind == "dispatch"
        ]
        if not dispatch_rows:
            return {}
        request_ids = {request.id for request, _ in dispatch_rows}
        task_rows = (
            await db.execute(
                select(TodoDispatchTask)
                .where(TodoDispatchTask.request_id.in_(list(request_ids)))
                .order_by(TodoDispatchTask.request_id.asc(), TodoDispatchTask.id.asc())
            )
        ).scalars().all()
        tasks_by_request: dict[str, list[TodoDispatchTask]] = {}
        for task in task_rows:
            tasks_by_request.setdefault(task.request_id, []).append(task)

        request_by_id = {request.id: request for request, _ in dispatch_rows}
        skill_by_request_id = {request.id: skill for request, skill in dispatch_rows}
        default_users_by_task_id: dict[int, User] = {}
        for task in task_rows:
            executor_id = str(task.executor or "").strip()
            if executor_id:
                continue
            request = request_by_id.get(task.request_id)
            if request is None:
                continue
            default_user = await self._valid_default_dispatch_executor(
                db,
                request=request,
                skill=skill_by_request_id.get(task.request_id),
                actor=current_user,
                task=task,
            )
            if default_user:
                default_users_by_task_id[task.id] = default_user

        user_ids = {user.id for user in default_users_by_task_id.values()}
        for task in task_rows:
            if task.executor:
                user_ids.add(task.executor)
        users_by_id: dict[str, User] = {}
        if user_ids:
            users = (
                await db.execute(select(User).where(User.id.in_(list(user_ids))))
            ).scalars().all()
            users_by_id = {user.id: user for user in users}

        summaries: dict[str, dict] = {}
        for request, _ in dispatch_rows:
            tasks = [
                task for task in tasks_by_request.get(request.id, [])
                if task.status not in {"done", "cancelled"}
            ]
            if not tasks:
                continue
            executor_ids: list[str] = []
            missing_count = 0
            for task in tasks:
                executor_id = str(task.executor or "").strip()
                default_user = default_users_by_task_id.get(task.id)
                if not executor_id and default_user:
                    executor_id = default_user.id
                if executor_id:
                    executor_ids.append(executor_id)
                else:
                    missing_count += 1
            unique_executor_ids = list(dict.fromkeys(executor_ids))
            executor_names = [
                (users_by_id.get(executor_id).name if users_by_id.get(executor_id) else executor_id)
                for executor_id in unique_executor_ids
            ]
            summaries[request.id] = {
                "executor_ids": unique_executor_ids,
                "executor_names": executor_names,
                "missing_count": missing_count,
                "total_count": len(tasks),
            }
        return summaries

    async def _fetch_decision_log_extras(
        self,
        db: AsyncSession,
        log_ids: set[int],
    ) -> dict[int, dict]:
        """批量从 decision_log 取 metrics_preview（前 3 条）+ requester_id 候选。"""
        if not log_ids:
            return {}
        from app.execution.models import DecisionLog
        from app.inbox.service import extract_report_cards

        stmt = select(
            DecisionLog.id,
            DecisionLog.output_result,
            DecisionLog.input_snapshot,
        ).where(DecisionLog.id.in_(list(log_ids)))
        out: dict[int, dict] = {}
        for log_id, output_result, input_snapshot in (await db.execute(stmt)).all():
            cards = extract_report_cards(log_id, output_result if isinstance(output_result, dict) else None)
            metrics = cards[0]["metrics"][:3] if cards else []
            requester_id: str | None = None
            if isinstance(input_snapshot, dict):
                for key in ("requester", "operator", "user_id", "created_by"):
                    val = input_snapshot.get(key)
                    if isinstance(val, str) and val.strip():
                        requester_id = val.strip()
                        break
            out[log_id] = {"metrics": metrics, "requester_id": requester_id}
        return out

    async def _fetch_requester_info(
        self,
        db: AsyncSession,
        user_ids: set[str],
    ) -> dict[str, dict]:
        """批量拼用户名 + primary membership 的组织名，供 list 页 requester 字段使用。"""
        if not user_ids:
            return {}
        from app.org.models import OrgUnit, UserOrgMembership

        stmt = (
            select(
                User.id,
                User.name,
                OrgUnit.name.label("org_name"),
            )
            .outerjoin(
                UserOrgMembership,
                and_(
                    UserOrgMembership.user_id == User.id,
                    UserOrgMembership.membership_type == "primary",
                ),
            )
            .outerjoin(OrgUnit, OrgUnit.id == UserOrgMembership.org_unit_id)
            .where(User.id.in_(list(user_ids)))
        )
        out: dict[str, dict] = {}
        for uid, name, org_name in (await db.execute(stmt)).all():
            if uid in out:  # 一个用户可能有多条 membership，只留第一条 primary
                continue
            out[uid] = {"name": name, "department": org_name}
        return out

    @staticmethod
    def _extract_suggested_actions(payload: dict | None) -> list[str]:
        """对齐 get_todo_detail（line 400）的兜底逻辑：
        payload.suggested → payload.output.suggestions → []
        最多 3 条，每条 ≤ 60 字。
        """
        if not isinstance(payload, dict):
            return []
        suggested = payload.get("suggested")
        if not suggested:
            output = payload.get("output")
            if isinstance(output, dict):
                suggested = output.get("suggestions")
        if not isinstance(suggested, list):
            suggested = TodoService._extract_finding_actions(payload)
        out: list[str] = []
        for item in suggested:
            if len(out) >= 3:
                break
            text: str | None = None
            if isinstance(item, str):
                text = item
            elif isinstance(item, dict):
                for key in ("action", "text", "content", "suggestion"):
                    val = item.get(key)
                    if isinstance(val, str) and val.strip():
                        text = val
                        break
            if text and text.strip():
                out.append(text.strip()[:60])
        return out

    @staticmethod
    def _iter_finding_rows(payload: dict | None) -> list[dict]:
        if not isinstance(payload, dict):
            return []
        findings = payload.get("findings")
        if isinstance(findings, list):
            return [item for item in findings if isinstance(item, dict)]
        return []

    @staticmethod
    def _first_text(value: object, *, max_len: int = 200) -> str:
        text = str(value or "").strip()
        return text[:max_len] if text else ""

    @staticmethod
    def _format_metric_number(value: object) -> str:
        if value in (None, ""):
            return ""
        if isinstance(value, (int, float)):
            if not isinstance(value, bool):
                return f"{float(value):.2f}".rstrip("0").rstrip(".")
        return str(value).strip()

    @staticmethod
    def _extract_finding_actions(payload: dict | None) -> list[str]:
        actions: list[str] = []
        for finding in TodoService._iter_finding_rows(payload):
            for raw in finding.get("improvement_actions") or []:
                text = TodoService._first_text(raw, max_len=120)
                if text and text not in actions:
                    actions.append(text)
                if len(actions) >= 3:
                    return actions
        dispatch_tasks = payload.get("dispatch_tasks") if isinstance(payload, dict) else None
        if isinstance(dispatch_tasks, list):
            for task in dispatch_tasks:
                if not isinstance(task, dict):
                    continue
                extra = task.get("extra") if isinstance(task.get("extra"), dict) else {}
                text = TodoService._first_text(extra.get("required_output"), max_len=120)
                if text and text not in actions:
                    actions.append(text)
                if len(actions) >= 3:
                    return actions
        return actions

    @staticmethod
    def _extract_finding_reasoning(payload: dict | None) -> list[str]:
        reasoning: list[str] = []
        for finding in TodoService._iter_finding_rows(payload):
            for raw in finding.get("conclusions") or []:
                text = TodoService._first_text(raw, max_len=300)
                if text and text not in reasoning:
                    reasoning.append(text)
                if len(reasoning) >= 6:
                    return reasoning
        return reasoning

    @staticmethod
    def _extract_finding_summary(payload: dict | None) -> str:
        rows = TodoService._iter_finding_rows(payload)
        if not rows:
            return ""
        first = rows[0]
        low = first.get("low_video") if isinstance(first.get("low_video"), dict) else {}
        topic = TodoService._first_text(first.get("topic_key") or low.get("topic_key"), max_len=80)
        name = TodoService._first_text(low.get("video_name"), max_len=120)
        cost = TodoService._format_metric_number(low.get("cost"))
        roi = TodoService._format_metric_number(low.get("roi"))
        parts = []
        if name:
            parts.append(f"低消耗视频《{name}》")
        if topic:
            parts.append(f"主题 {topic}")
        metric_bits = []
        if cost:
            metric_bits.append(f"消耗 {cost}")
        if roi:
            metric_bits.append(f"ROI {roi}")
        if metric_bits:
            parts.append("、".join(metric_bits))
        conclusions = first.get("conclusions") or []
        if conclusions:
            text = TodoService._first_text(conclusions[0], max_len=180)
            if text:
                parts.append(text)
        return "；".join(parts)[:500]

    @staticmethod
    def _extract_finding_metrics(payload: dict | None, *, limit: int | None = None) -> list[dict]:
        rows = TodoService._iter_finding_rows(payload)
        if not rows:
            return []
        first = rows[0]
        low = first.get("low_video") if isinstance(first.get("low_video"), dict) else {}
        gap = first.get("gap") if isinstance(first.get("gap"), dict) else {}
        benchmark = gap.get("strongest_benchmark")
        if not isinstance(benchmark, dict):
            benchmark = {}

        candidates = [
            ("低消耗视频", low.get("video_name"), "flat", first.get("topic_key") or low.get("topic_key")),
            ("消耗", low.get("cost"), "down", f"对标 {TodoService._format_metric_number(benchmark.get('cost'))}" if benchmark.get("cost") is not None else ""),
            ("ROI", low.get("roi"), "down", f"对标 {TodoService._format_metric_number(benchmark.get('roi'))}" if benchmark.get("roi") is not None else ""),
            ("成交金额", low.get("pay_amount"), "down", f"对标 {TodoService._format_metric_number(benchmark.get('pay_amount'))}" if benchmark.get("pay_amount") is not None else ""),
            ("点击率", low.get("click_rate"), "flat", f"对标 {TodoService._format_metric_number(benchmark.get('click_rate'))}" if benchmark.get("click_rate") is not None else ""),
            ("转化率", low.get("convert_rate"), "down", f"对标 {TodoService._format_metric_number(benchmark.get('convert_rate'))}" if benchmark.get("convert_rate") is not None else ""),
        ]
        out: list[dict] = []
        for label, value, trend, delta in candidates:
            text = TodoService._format_metric_number(value)
            if not text:
                continue
            out.append({
                "label": label,
                "value": text[:200],
                "trend": trend,
                "delta": TodoService._first_text(delta, max_len=80) or None,
            })
            if limit is not None and len(out) >= limit:
                break
        return out

    @staticmethod
    def _extract_payload_metrics(payload: dict | None, *, limit: int | None = None) -> list[dict]:
        """Extract operator-facing metrics from a todo decision-card payload."""
        if not isinstance(payload, dict):
            return []

        projected: list[tuple[int, int, dict]] = []
        seen_labels: set[str] = set()
        for index, item in enumerate(TodoService._extract_metric_rows(payload)):
            label = TodoService._metric_text(item, "label", "name", "dimension")
            value = TodoService._metric_text(item, "value", "basis", "summary")
            if not label or not value:
                continue
            if label in seen_labels:
                continue
            seen_labels.add(label)
            trend = TodoService._metric_text(item, "trend", "status", "confidence")
            delta = TodoService._metric_text(item, "delta", "source", "data_gap", "detail", "note")
            metric = {
                "label": label[:80],
                "value": value[:200],
                "trend": trend[:40] if trend else None,
                "delta": delta[:80] if delta else None,
            }
            priority = _TODO_METRIC_PREVIEW_PRIORITY.get(label, len(_TODO_METRIC_PREVIEW_PRIORITY) + 1)
            projected.append((priority, index, metric))

        projected.sort(key=lambda row: (row[0], row[1]))
        out = [metric for _, _, metric in projected]
        if not out:
            out = TodoService._extract_finding_metrics(payload)
        if limit is not None:
            return out[:limit]
        return out

    @staticmethod
    def _extract_primary_indicator_from_payload(payload: dict | None) -> dict | None:
        """从 DecisionRequest.payload 取 primary_indicator 并做严格投影。

        数据来源：`execution_service.extract_primary_indicator` 已按契约
        (`docs/spec/skill-output-report-v1.md`) 抽过；这里只做二次保护，
        防止旧数据/脏数据导致前端 schema 校验失败。
        """
        if not isinstance(payload, dict):
            return None
        payment_change = TodoService._extract_payment_change_pct(payload)
        if payment_change is not None:
            return {
                "label": "支付金额变化率",
                "value": TodoService._format_payment_change(payment_change) or "",
                "severity": (
                    "high"
                    if payment_change < -10
                    else "medium"
                    if payment_change < 0
                    else "low"
                ),
                "tone": "danger" if payment_change < 0 else "success",
            }
        raw = payload.get("primary_indicator")
        if not isinstance(raw, dict):
            finding_metrics = TodoService._extract_finding_metrics(payload, limit=3)
            by_label = {str(metric.get("label") or ""): metric for metric in finding_metrics}
            for label in ("ROI", "消耗", "成交金额"):
                metric = by_label.get(label)
                if metric and metric.get("value"):
                    return {
                        "label": label[:40],
                        "value": str(metric["value"])[:80],
                        "severity": "medium" if label == "ROI" else "low",
                        "tone": "warning" if label == "ROI" else "neutral",
                    }
            return None
        label = raw.get("label")
        value = raw.get("value")
        if not isinstance(label, str) or not label.strip():
            return None
        if not isinstance(value, str) or not value.strip():
            return None
        out: dict = {"label": label.strip()[:40], "value": value.strip()[:80]}
        severity = raw.get("severity")
        if isinstance(severity, str) and severity in {"critical", "high", "medium", "low"}:
            out["severity"] = severity
        tone = raw.get("tone")
        if isinstance(tone, str) and tone in {"danger", "warning", "success", "info", "neutral"}:
            out["tone"] = tone
        return out

    @staticmethod
    def _map_approval_level(level: int | None) -> str | None:
        """Skill.approval_level (int) → 'L0'/'L1'/'L2'/'L3' 展示串。"""
        if level is None:
            return None
        return f"L{int(level)}" if int(level) in (0, 1, 2, 3) else None

    @staticmethod
    def _normalize_business_priority(value: object) -> str | None:
        text = str(value or "").strip().upper()
        return text if text in {"P0", "P1", "P2", "P3"} else None

    async def get_stats(self, db: AsyncSession, current_user: User) -> dict:
        global_reader = is_global_inbox_reader(current_user)
        # P0-4 部门隔离：全局角色看全公司；普通用户只看派给自己且本部门/orphan 的待办。
        base_filters = [DecisionRequest.archived_at.is_(None)]
        if not global_reader:
            visible_assignee_ids, identity_has_global_scope = (
                await visible_inbox_assignee_scope_for_user(db, current_user)
            )
            base_filters.append(AITodo.assignee.in_(visible_assignee_ids))
            if not identity_has_global_scope:
                base_filters.append(or_(Skill.id.is_(None), Skill.department == current_user.department))

        stmt = (
            select(AITodo.status, func.count(AITodo.id))
            .join(DecisionRequest, DecisionRequest.id == AITodo.request_id)
            .outerjoin(Skill, Skill.id == DecisionRequest.skill_id)
            .where(*base_filters)
            .group_by(AITodo.status)
        )
        counts = {status: count for status, count in (await db.execute(stmt)).all()}
        priority_expr = case(
            (
                or_(
                    DecisionRequest.title.ilike("P0｜%"),
                    DecisionRequest.title.ilike("P0|%"),
                    DecisionRequest.title.ilike("P0 %"),
                    func.upper(func.coalesce(DecisionRequest.payload["priority"].astext, "")) == "P0",
                ),
                "P0",
            ),
            (
                or_(
                    DecisionRequest.title.ilike("P1｜%"),
                    DecisionRequest.title.ilike("P1|%"),
                    DecisionRequest.title.ilike("P1 %"),
                    func.upper(func.coalesce(DecisionRequest.payload["priority"].astext, "")) == "P1",
                ),
                "P1",
            ),
            (
                or_(
                    DecisionRequest.title.ilike("P2｜%"),
                    DecisionRequest.title.ilike("P2|%"),
                    DecisionRequest.title.ilike("P2 %"),
                    func.upper(func.coalesce(DecisionRequest.payload["priority"].astext, "")) == "P2",
                ),
                "P2",
            ),
            (
                or_(
                    DecisionRequest.title.ilike("P3｜%"),
                    DecisionRequest.title.ilike("P3|%"),
                    DecisionRequest.title.ilike("P3 %"),
                    func.upper(func.coalesce(DecisionRequest.payload["priority"].astext, "")) == "P3",
                ),
                "P3",
            ),
            else_="unclassified",
        )
        now = now_bjt()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_priority_rows = (
            await db.execute(
                select(priority_expr.label("priority"), func.count(AITodo.id))
                .join(DecisionRequest, DecisionRequest.id == AITodo.request_id)
                .outerjoin(Skill, Skill.id == DecisionRequest.skill_id)
                .where(*base_filters)
                .where(AITodo.created_at >= today_start)
                .group_by(priority_expr)
            )
        ).all()
        today_by_priority = {str(priority): int(count or 0) for priority, count in today_priority_rows}
        pending_priority_rows = (
            await db.execute(
                select(priority_expr.label("priority"), func.count(AITodo.id))
                .join(DecisionRequest, DecisionRequest.id == AITodo.request_id)
                .outerjoin(Skill, Skill.id == DecisionRequest.skill_id)
                .where(*base_filters)
                .where(AITodo.status == "pending")
                .group_by(priority_expr)
            )
        ).all()
        pending_by_priority = {str(priority): int(count or 0) for priority, count in pending_priority_rows}
        dispatch_stmt = (
            select(func.count(TodoDispatchTask.id))
            .join(DecisionRequest, DecisionRequest.id == TodoDispatchTask.request_id)
            .join(Skill, Skill.id == DecisionRequest.skill_id)
            .where(DecisionRequest.archived_at.is_(None))
            .where(TodoDispatchTask.status.in_(list(_TODO_ACTIVE_DISPATCH_STATUSES)))
        )
        if not global_reader:
            dispatch_stmt = dispatch_stmt.where(TodoDispatchTask.executor.in_(visible_assignee_ids))
            if not identity_has_global_scope:
                dispatch_stmt = dispatch_stmt.where(Skill.department == current_user.department)
        dispatch_pending = (await db.execute(dispatch_stmt)).scalar() or 0
        due_soon = (
            await db.scalar(
                select(func.count(AITodo.id))
                .join(DecisionRequest, DecisionRequest.id == AITodo.request_id)
                .outerjoin(Skill, Skill.id == DecisionRequest.skill_id)
                .where(*base_filters)
                .where(AITodo.status == "pending")
                .where(DecisionRequest.sla_at <= now + timedelta(hours=24))
            )
        ) or 0
        overdue = (
            await db.scalar(
                select(func.count(AITodo.id))
                .join(DecisionRequest, DecisionRequest.id == AITodo.request_id)
                .outerjoin(Skill, Skill.id == DecisionRequest.skill_id)
                .where(*base_filters)
                .where(AITodo.status == "pending")
                .where(DecisionRequest.sla_at < now)
            )
        ) or 0
        done_count = (
            counts.get("approved", 0)
            + counts.get("rejected", 0)
            + counts.get("resolved_by_peer", 0)
        )
        feedback_stmt = (
            select(func.count(func.distinct(AITodo.id)))
            .join(DecisionRequest, DecisionRequest.id == AITodo.request_id)
            .outerjoin(Skill, Skill.id == DecisionRequest.skill_id)
            .where(AITodo.kind == "dispatch")
            .where(DecisionRequest.archived_at.is_(None))
            .where(
                exists()
                .where(TodoDispatchTask.request_id == DecisionRequest.id)
                .where(TodoDispatchTask.status == "done")
            )
        )
        if not global_reader:
            feedback_stmt = feedback_stmt.where(AITodo.assignee.in_(visible_assignee_ids))
            if not identity_has_global_scope:
                feedback_stmt = feedback_stmt.where(or_(Skill.id.is_(None), Skill.department == current_user.department))
        feedback_done = (await db.execute(feedback_stmt)).scalar() or 0
        return {
            "pending": counts.get("pending", 0),
            "approved": counts.get("approved", 0),
            "rejected": counts.get("rejected", 0),
            "expired": counts.get("expired", 0),
            "resolved_by_peer": counts.get("resolved_by_peer", 0),
            "done": done_count,
            "dispatch_pending": dispatch_pending,
            "due_soon": due_soon,
            "overdue": overdue,
            "today_new": sum(today_by_priority.values()),
            "today_by_priority": today_by_priority,
            "pending_by_priority": pending_by_priority,
            "feedback_done": feedback_done,
        }

    async def get_todo_detail(
        self,
        db: AsyncSession,
        *,
        todo_id: int,
        current_user: User,
        include_debug: bool = False,
    ) -> dict:
        todo, request = await self._load_todo_with_request(db, todo_id)
        skill = await db.get(Skill, request.skill_id)
        # P0-4 部门隔离：assignee 自己永远可见；其他人必须通过部门校验。
        # orphan skill (skill 已被删除) 走 require_department_access，admin/director 仍可见
        visible_assignee_ids = await visible_inbox_assignee_ids_for_user(db, current_user)
        if todo.assignee not in visible_assignee_ids:
            if skill is None:
                # orphan：只有全局可见角色能看
                if not is_global_inbox_reader(current_user):
                    raise AppError("AUTH_DEPARTMENT_DENIED", 403)
            elif not require_department_access(skill.department, current_user):
                raise AppError("AUTH_DEPARTMENT_DENIED", 403)
        result: dict = {
            "todo": self._serialize_todo(todo),
            "request": self._serialize_request(request),
            "skill_meta": self._serialize_skill(skill),
            "payload": request.payload or {},
        }
        if request.kind == "dispatch":
            tasks = await self.list_dispatch_tasks_for_request(db, request.id)
            result["dispatch_tasks"] = await self._serialize_dispatch_tasks_with_defaults(
                db,
                tasks=tasks,
                request=request,
                skill=skill,
                actor=current_user,
            )

        # 结构化展示：提取 payload 中的关键字段，方便前端渲染
        payload = request.payload or {}
        result["structured"] = {
            "input_params": payload.get("input") or {},
            "output_summary": self._extract_output_summary(payload.get("output")) or self._extract_finding_summary(payload),
            "decision_reasoning": self._extract_decision_reasoning(payload.get("output")) or self._extract_finding_reasoning(payload),
            "suggested_actions": self._extract_suggested_actions(payload),
            "metrics": self._extract_payload_metrics(payload),
        }
        result["post_training_model_evaluation"] = payload.get(POST_TRAINING_EVALUATION_KEY)

        # 决策链：从 DecisionLog 获取完整的决策推理过程
        decision_log = None
        decision_log_summary: dict | None = None
        if request.run_id:
            from app.execution.models import DecisionLog

            if include_debug:
                log_result = await db.execute(
                    select(DecisionLog)
                    .where(DecisionLog.run_id == request.run_id)
                    .order_by(DecisionLog.created_at.desc())
                    .limit(1)
                )
                decision_log = log_result.scalar_one_or_none()
                if decision_log:
                    decision_log_summary = {
                        "id": decision_log.id,
                        "approval_level": decision_log.approval_level,
                        "created_at": decision_log.created_at,
                        "report_title": self._first_report_title(decision_log.output_result),
                    }
            else:
                log_result = await db.execute(
                    select(
                        DecisionLog.id,
                        DecisionLog.approval_level,
                        DecisionLog.created_at,
                        func.pg_column_size(DecisionLog.output_result).label("output_bytes"),
                        func.jsonb_extract_path_text(
                            DecisionLog.output_result,
                            "reports",
                            "0",
                            "title",
                        ).label("report_title"),
                    )
                    .where(DecisionLog.run_id == request.run_id)
                    .order_by(DecisionLog.created_at.desc())
                    .limit(1)
                )
                row = log_result.one_or_none()
                if row:
                    decision_log_summary = {
                        "id": row.id,
                        "approval_level": row.approval_level,
                        "created_at": row.created_at,
                        "output_bytes": row.output_bytes or 0,
                        "report_title": row.report_title or self._first_report_title_from_payload(payload),
                    }
                    if self._should_load_detail_decision_log_for_compares(
                        payload,
                        output_bytes=row.output_bytes,
                    ):
                        decision_log = await db.get(DecisionLog, row.id)
            if decision_log_summary:
                result["decision_chain"] = {
                    "log_id": decision_log_summary["id"],
                    "approval_level": decision_log_summary["approval_level"],
                    "created_at": isoformat_bjt(decision_log_summary["created_at"]),
                }
                if include_debug and decision_log:
                    result["decision_chain"]["input_snapshot"] = decision_log.input_snapshot
                    result["decision_chain"]["output_result"] = decision_log.output_result
                if decision_log:
                    local_compares = await self._build_local_snapshot_compares(
                        db,
                        request=request,
                        payload=payload,
                        decision_log=decision_log,
                    )
                    if local_compares:
                        payload = dict(payload)
                        for key, value in local_compares.items():
                            payload.setdefault(key, value)
                        result["payload"] = payload

        # related_report：待办关联的首条 inbox 报告卡片（spec inbox §5.3）
        result["related_report"] = self._build_related_report(
            request=request,
            decision_log=decision_log,
            decision_log_summary=decision_log_summary,
        )
        return result

    async def check_data_source(
        self,
        db: AsyncSession,
        *,
        todo_id: int,
        current_user: User,
        source_key: str | None = None,
        index: int | None = None,
    ) -> dict:
        """核对待办中声明过的数据来源，避免把接口做成任意 URL 代理。"""
        todo, request = await self._load_todo_with_request(db, todo_id)
        skill = await db.get(Skill, request.skill_id)
        visible_assignee_ids = await visible_inbox_assignee_ids_for_user(db, current_user)
        if todo.assignee not in visible_assignee_ids:
            if skill is None:
                if not is_global_inbox_reader(current_user):
                    raise AppError("AUTH_DEPARTMENT_DENIED", 403)
            elif not require_department_access(skill.department, current_user):
                raise AppError("AUTH_DEPARTMENT_DENIED", 403)

        payload = request.payload if isinstance(request.payload, dict) else {}
        raw_sources = payload.get("data_source_links")
        sources = raw_sources if isinstance(raw_sources, list) else []
        normalized: list[dict] = []
        for idx, row in enumerate(sources):
            if isinstance(row, dict):
                normalized.append({**row, "_index": idx})
            elif isinstance(row, str):
                normalized.append({"key": f"source-{idx}", "label": row, "source": row, "_index": idx})

        selected = None
        if source_key:
            selected = next((row for row in normalized if str(row.get("key") or "") == source_key), None)
        if selected is None and index is not None and 0 <= index < len(normalized):
            selected = normalized[index]
        if selected is None:
            raise AppError("TODO_SOURCE_NOT_FOUND", 404, {"detail": "待办中未找到该数据来源"})

        api_url = str(selected.get("api_url") or selected.get("check_url") or "").strip()
        page_url = str(selected.get("page_url") or selected.get("url") or "").strip()
        if not api_url:
            return {
                "success": True,
                "mode": "open_page",
                "url": page_url,
                "source": selected,
                "message": "该来源没有可自动 replay 的 API，已提供来源页面用于人工核对。",
            }

        from app.browser.service import fetch_json, guard_discover_url

        guard_discover_url(api_url, field="url")
        if page_url:
            guard_discover_url(page_url, field="page_url")
        fetch_result = await fetch_json(
            url=api_url,
            method=str(selected.get("method") or "GET"),
            headers=selected.get("headers") if isinstance(selected.get("headers"), dict) else None,
            body=selected.get("body"),
            page_url=page_url or None,
        )
        proof = fetch_result.get("proof") if isinstance(fetch_result.get("proof"), dict) else {}
        data = fetch_result.get("data")
        rows_count = None
        if isinstance(data, dict):
            inner = data.get("data")
            if isinstance(inner, dict):
                rows = inner.get("data") or inner.get("rows")
                if isinstance(rows, list):
                    rows_count = len(rows)
            elif isinstance(inner, list):
                rows_count = len(inner)
        success = bool(fetch_result.get("success")) and proof.get("ok") is not False
        return {
            "success": success,
            "mode": "api_replay",
            "source": {
                "key": selected.get("key"),
                "label": selected.get("label"),
                "source": selected.get("source"),
                "url": page_url,
                "api_url": api_url,
            },
            "proof": proof,
            "rows_count": rows_count,
            "message": "数据来源 API replay 完成" if success else str(fetch_result.get("error") or "数据来源 API replay 失败"),
        }

    async def get_todo_preview(
        self,
        db: AsyncSession,
        *,
        todo_id: int,
        current_user: User,
    ) -> dict:
        """GAP-3：详情页轻量投影。返回 id / title / status / suggested_actions /
        metrics / decision_reasoning / skill_meta / related_report，省略 payload
        / decision_chain / dispatch_tasks 等重数据，用于 hover 预览或 bulk 场景。
        """
        todo, request = await self._load_todo_with_request(db, todo_id)
        skill = await db.get(Skill, request.skill_id)
        # 权限校验沿用 detail
        visible_assignee_ids = await visible_inbox_assignee_ids_for_user(db, current_user)
        if todo.assignee not in visible_assignee_ids:
            if skill is None:
                if not is_global_inbox_reader(current_user):
                    raise AppError("AUTH_DEPARTMENT_DENIED", 403)
            elif not require_department_access(skill.department, current_user):
                raise AppError("AUTH_DEPARTMENT_DENIED", 403)

        payload = request.payload or {}
        metrics: list[dict] = self._extract_payload_metrics(payload)
        decision_log = None
        if not metrics and request.decision_log_id:
            from app.execution.models import DecisionLog
            from app.inbox.service import extract_report_cards

            decision_log = await db.get(DecisionLog, request.decision_log_id)
            if decision_log:
                cards = extract_report_cards(
                    decision_log.id,
                    decision_log.output_result if isinstance(decision_log.output_result, dict) else None,
                )
                if cards:
                    metrics = cards[0]["metrics"]

        return {
            "id": todo.id,
            "title": request.title,
            "status": todo.status,
            "suggested_actions": self._extract_suggested_actions(payload),
            "metrics": metrics,
            "decision_reasoning": self._extract_decision_reasoning(payload.get("output")) or self._extract_finding_reasoning(payload),
            "skill_meta": self._serialize_skill(skill),
            "related_report": self._build_related_report(
                request=request, decision_log=decision_log
            ),
        }

    async def bulk_summary(
        self,
        db: AsyncSession,
        *,
        todo_ids: list[int],
        current_user: User,
    ) -> dict:
        """GAP-4：一次返回多个 todo 的精简摘要，不在用户权限内的 id 静默跳过。

        与 preview 保持同一 schema（summaries[id] 为 preview 的子集）。
        """
        if not todo_ids:
            return {"summaries": {}}
        # 批量拉 (AITodo, DecisionRequest, Skill)
        stmt = (
            select(AITodo, DecisionRequest, Skill)
            .join(DecisionRequest, DecisionRequest.id == AITodo.request_id)
            .outerjoin(Skill, Skill.id == DecisionRequest.skill_id)
            .where(AITodo.id.in_(list(set(todo_ids))))
        )
        rows = (await db.execute(stmt)).all()

        # decision_log 批量预查 metrics
        log_ids: set[int] = {r.decision_log_id for _, r, _ in rows if r and r.decision_log_id}
        log_metrics: dict[int, list[dict]] = {}
        if log_ids:
            from app.execution.models import DecisionLog
            from app.inbox.service import extract_report_cards

            for log_id, output_result in (
                await db.execute(
                    select(DecisionLog.id, DecisionLog.output_result).where(
                        DecisionLog.id.in_(list(log_ids))
                    )
                )
            ).all():
                cards = extract_report_cards(
                    log_id,
                    output_result if isinstance(output_result, dict) else None,
                )
                log_metrics[log_id] = cards[0]["metrics"] if cards else []

        summaries: dict[int, dict] = {}
        is_privileged = is_global_inbox_reader(current_user)
        for todo, request, skill in rows:
            # 权限过滤：非特权用户只看自己的 / 本部门的
            if todo.assignee != current_user.id and not is_privileged:
                if skill is None or not require_department_access(skill.department, current_user):
                    continue
            payload = request.payload or {}
            metrics = self._extract_payload_metrics(payload) or log_metrics.get(request.decision_log_id or -1, [])
            summaries[todo.id] = {
                "id": todo.id,
                "title": request.title,
                "status": todo.status,
                "suggested_actions": self._extract_suggested_actions(payload),
                "metrics": metrics,
                "skill_meta": self._serialize_skill(skill),
            }
        return {"summaries": summaries}

    def _build_related_report(
        self,
        *,
        request: DecisionRequest,
        decision_log=None,
        decision_log_summary: dict | None = None,
    ) -> dict | None:
        """从 DecisionRequest.decision_log_id 反查首条 reports[0] 作为关联报告摘要。

        为避免 legacy 路径 decision_log_id 为空，兜底再用 request.run_id 对应的 decision_log。
        都取不到时返回 None。
        """
        target_log = None
        if decision_log is not None and getattr(request, "decision_log_id", None) in (None, decision_log.id):
            target_log = decision_log
        if target_log is None and getattr(request, "decision_log_id", None) is not None:
            # run_id 匹配到的和 decision_log_id 不一致时，不再异步查询 — 保持方法同步；
            # 调用方如需精确关联由 inbox 路由自行处理。
            target_log = decision_log if decision_log and decision_log.id == request.decision_log_id else None

        log_id = None
        created_at = None
        title = ""
        if target_log is not None:
            output = getattr(target_log, "output_result", None)
            title = self._first_report_title(output)
            log_id = target_log.id
            created_at = target_log.created_at
        elif decision_log_summary is not None:
            summary_id = decision_log_summary.get("id")
            if getattr(request, "decision_log_id", None) in (None, summary_id):
                log_id = summary_id
                created_at = decision_log_summary.get("created_at")
                title = str(decision_log_summary.get("report_title") or "").strip()

        if not title:
            return None
        return {
            "id": f"{log_id}-0",
            "decision_log_id": log_id,
            "title": title[:200],
            "created_at": isoformat_bjt(created_at),
        }

    @staticmethod
    def _first_report_title(output: dict | None) -> str:
        if not isinstance(output, dict):
            return ""
        reports = output.get("reports")
        if not isinstance(reports, list) or not reports:
            return ""
        first = reports[0]
        if not isinstance(first, dict):
            return ""
        return str(first.get("title") or "").strip()

    @staticmethod
    def _first_report_title_from_payload(payload: dict | None) -> str:
        if not isinstance(payload, dict):
            return ""
        output = payload.get("output")
        if isinstance(output, dict):
            title = TodoService._first_report_title(output)
            if title:
                return title
        related_report = payload.get("related_report")
        if isinstance(related_report, dict):
            return str(related_report.get("title") or "").strip()
        return ""

    @staticmethod
    def _should_load_detail_decision_log_for_compares(
        payload: dict | None,
        *,
        output_bytes: int | None,
    ) -> bool:
        """Only load raw DecisionLog JSON on normal detail pages when it is small.

        Tmall collection logs can store tens of MB in output_result. Loading them
        just to backfill optional compare cards makes a single detail page slow.
        """
        if not isinstance(payload, dict):
            return False
        if payload.get("free_search_compare") or payload.get("paid_realtime_compare"):
            return False
        item_id = str(
            payload.get("item_id")
            or TodoService._as_dict(payload.get("input")).get("item_id")
            or ""
        ).strip()
        if not item_id:
            return False
        max_bytes = getattr(settings, "CACHE_MAX_VALUE_BYTES", 2 * 1024 * 1024)
        if output_bytes is not None and max_bytes and int(output_bytes) > int(max_bytes):
            return False
        return True

    async def decide(
        self,
        db: AsyncSession,
        *,
        todo_id: int,
        decision: str,
        decided_by: str,
        channel: str = "web",
        reason: str = "",
        feedback_payload: dict | None = None,
    ) -> dict:
        if decision not in {"approved", "rejected"}:
            raise AppError("TODO_INVALID_DECISION", 400)

        # P1-1 原子化：对 DecisionRequest 加行锁，确保同一 request 上的并发 decide 串行化，
        # 防止 _update_aggregate 里 pending_count 读到中间状态导致重复 fan_out / 重复回调。
        request_id_row = await db.execute(
            select(AITodo.request_id).where(AITodo.id == todo_id)
        )
        request_id = request_id_row.scalar_one_or_none()
        if not request_id:
            raise AppError("TODO_NOT_FOUND", 404)
        # FOR UPDATE 锁住 request 行，事务内同一 request 的其它 decide 必须等待
        await db.execute(
            select(DecisionRequest)
            .where(DecisionRequest.id == request_id)
            .with_for_update()
        )

        todo, request = await self._load_todo_with_request(db, todo_id)
        actor = await db.get(User, decided_by)
        if not actor:
            raise AppError("AUTH_USER_NOT_FOUND", 404)
        visible_assignee_ids = await visible_inbox_assignee_ids_for_user(db, actor)
        if todo.assignee not in visible_assignee_ids:
            # P0-4 部门隔离：非 assignee 必须有该 Skill 部门访问权（orphan skill 仅全局可见角色可代决）
            skill_for_dept = await db.get(Skill, request.skill_id)
            if skill_for_dept is None:
                if not is_global_inbox_reader(actor):
                    raise AppError("AUTH_DEPARTMENT_DENIED", 403)
            elif not require_department_access(skill_for_dept.department, actor):
                raise AppError("AUTH_DEPARTMENT_DENIED", 403)

        # 4 眼原则: Skill owner 不得审批自己 Skill 跑出的待办（admin / system_admin 例外）
        # H11：v2 system_admin 也豁免；is_global_inbox_reader 同时覆盖 can_view_all。
        if not is_global_inbox_reader(actor):
            skill = await db.get(Skill, request.skill_id)
            if skill and skill.owner and skill.owner == decided_by:
                raise AppError(
                    "REVIEW_SELF_APPROVE",
                    403,
                    {
                        "detail": {
                            "skill_id": request.skill_id,
                            "skill_owner": skill.owner,
                            "decided_by": decided_by,
                        }
                    },
                )

        if decision == "approved" and request.kind == "dispatch":
            await self._assert_dispatch_tasks_ready_for_approval(
                db,
                request,
                actor=actor,
            )
        clean_feedback_payload = self._normalize_feedback_payload(
            feedback_payload,
            decision=decision,
            channel=channel,
            reason=reason,
        )

        result = await db.execute(
            update(AITodo)
            .where(AITodo.id == todo_id)
            .where(AITodo.status == "pending")
            .values(
                status=decision,
                decided_at=now_bjt(),
                decided_by=decided_by,
                decision_reason=reason,
                decision_channel=channel,
                feedback_payload=clean_feedback_payload,
            )
        )
        if result.rowcount == 0:
            existing = await db.get(AITodo, todo_id)
            if not existing:
                raise AppError("TODO_NOT_FOUND", 404)
            raise AppError(
                "TODO_ALREADY_DECIDED",
                409,
                {
                    "detail": f"已由 {existing.decided_by or '-'} 在 {existing.decided_at} 处理"
                },
            )

        await db.flush()
        todo = await db.get(AITodo, todo_id)
        await self._update_aggregate(db, request, todo, decision)
        decision_log = await self._sync_decision_log_feedback(
            db,
            request=request,
            decision=decision,
            decided_by=decided_by,
            channel=channel,
            reason=reason,
        )
        if (
            decision == "approved"
            and request.kind == "dispatch"
            and request.aggregate_decision == "approved"
        ):
            await self._remember_dispatch_preference(
                db,
                request=request,
                actor_id=decided_by,
            )
        await db.flush()

        try:
            from app.learning.service import capture_ai_todo, capture_decision_log, capture_decision_request

            await capture_ai_todo(db, todo)
            await capture_decision_request(db, request)
            if decision_log is not None:
                await capture_decision_log(db, decision_log, user_id=decided_by)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "capture learning todo feedback failed (todo_id={}, request_id={}): {}",
                todo.id,
                request.id,
                e,
                exc_info=True,
            )

        try:
            skill = await db.get(Skill, request.skill_id)
            from app.tasktree.service import invalidate_tasktree
            await invalidate_tasktree(skill.department if skill else None)
        except Exception as e:
            logger.warning(
                "invalidate_tasktree 失败 (todo decide, request_id={}): {}",
                request.id,
                e,
                exc_info=True,
            )

        dispatch_tasks = []
        dispatch_fanout = None
        if request.kind == "dispatch":
            task_rows = await self.list_dispatch_tasks_for_request(db, request.id)
            dispatch_tasks = [self._serialize_dispatch_task(task) for task in task_rows]
            status_counts: dict[str, int] = {}
            for task in task_rows:
                status_counts[task.status] = status_counts.get(task.status, 0) + 1
            dispatch_fanout = {
                "total": len(task_rows),
                "statuses": status_counts,
                "sent": status_counts.get("sent", 0),
                "degraded": (
                    status_counts.get("pushed_no_dingtalk", 0)
                    + status_counts.get("pending_assignment", 0)
                ),
            }

        return {
            "todo": self._serialize_todo(todo),
            "request": self._serialize_request(request),
            "dispatch_tasks": dispatch_tasks,
            "dispatch_fanout": dispatch_fanout,
        }

    @staticmethod
    def _feedback_value(value) -> str | int | float | bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            text = value.strip()
            return text[:2000] if text else None
        if isinstance(value, (dict, list)):
            try:
                text = json.dumps(value, ensure_ascii=False)
            except Exception:
                text = str(value)
            text = text.strip()
            return text[:2000] if text else None
        text = str(value).strip()
        return text[:2000] if text else None

    @classmethod
    def _normalize_feedback_payload(
        cls,
        payload: dict | None,
        *,
        decision: str,
        channel: str,
        reason: str,
    ) -> dict:
        clean: dict = {
            "decision": decision,
            "source_channel": channel,
        }
        if reason:
            clean["reason_text"] = str(reason).strip()[:4000]
        if not isinstance(payload, dict):
            return clean
        reserved = {"source_channel", "decision", "reason_text"}
        fields: dict = {}
        for key, value in payload.items():
            key_text = str(key or "").strip()
            if not key_text:
                continue
            normalized_key = key_text[:80]
            normalized_value = cls._feedback_value(value)
            if normalized_value is None:
                continue
            if normalized_key in reserved:
                clean[normalized_key] = normalized_value
            else:
                fields[normalized_key] = normalized_value
        if fields:
            clean["fields"] = fields
        return clean

    async def _sync_decision_log_feedback(
        self,
        db: AsyncSession,
        *,
        request: DecisionRequest,
        decision: str,
        decided_by: str,
        channel: str,
        reason: str,
    ):
        """Mirror human todo feedback onto the linked DecisionLog when present."""
        from app.execution.models import DecisionLog

        log = None
        if request.decision_log_id:
            log = await db.get(DecisionLog, request.decision_log_id)
        if log is None and request.run_id:
            stmt = (
                select(DecisionLog)
                .where(DecisionLog.run_id == request.run_id)
                .where(DecisionLog.skill_id == request.skill_id)
                .order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc())
                .limit(1)
            )
            log = (await db.execute(stmt)).scalar_one_or_none()
        if log is None:
            return None

        mapped = "completed" if decision == "approved" else "rejected"
        log.user_action = mapped
        log.approver = decided_by
        reason_text = str(reason or "").strip()
        if reason_text:
            existing = str(log.user_feedback or "").strip()
            channel_prefix = "钉钉审批反馈" if channel == "dingtalk" else "待办审批反馈"
            feedback = f"{channel_prefix}:\n{reason_text}"
            log.user_feedback = f"{existing}\n\n{feedback}".strip()[:10000] if existing else feedback[:10000]
            reject_reason = self._extract_feedback_line(reason_text, "驳回原因")
            feedback_type = self._extract_feedback_line(reason_text, "反馈类型")
            rating = self._extract_feedback_rating(reason_text)
            if reject_reason:
                log.reject_reason = reject_reason[:50]
            if feedback_type:
                log.feedback_type = feedback_type[:50]
            if rating is not None:
                log.rating = rating
        await db.flush()
        return log

    @staticmethod
    def _extract_feedback_line(text: str, label: str) -> str:
        prefix = f"{label}:"
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            if line.startswith(prefix):
                return line[len(prefix):].strip()
        return ""

    @staticmethod
    def _extract_feedback_rating(text: str) -> int | None:
        raw = TodoService._extract_feedback_line(text, "评分")
        if not raw:
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return value if 1 <= value <= 5 else None

    async def extend_sla(
        self,
        db: AsyncSession,
        *,
        todo_id: int,
        hours: int,
        actor: User | None = None,
    ) -> dict:
        """单条延期 SLA。

        C2：必须传 actor 且通过 ``assert_can_modify_todo`` 才能改写；router 已经
        做了 ``require_state_active``，但 admin 之外 / dept_admin 之外的角色不能改 SLA。
        actor 缺省（向下兼容旧调用）时跳过 ACL，调用方应尽快显式传入。
        """
        todo, request = await self._load_todo_with_request(db, todo_id)
        if actor is not None:
            skill = await db.get(Skill, request.skill_id) if request.skill_id else None
            assert_can_modify_todo(user=actor, todo=todo, request=request, skill=skill)
        request.sla_at = request.sla_at + timedelta(hours=hours)
        await db.flush()
        return self._serialize_request(request)

    async def update_todo_draft(
        self,
        db: AsyncSession,
        *,
        todo_id: int,
        actor: User,
        title: str | None = None,
        summary: str | None = None,
        recommended_decision: str | None = None,
        approval_question: str | None = None,
        suggested_actions: list[str] | None = None,
        forbidden_actions: list[str] | None = None,
        dispatch_tasks: list[dict] | None = None,
    ) -> dict:
        """Allow the manager to edit an AI-generated todo before approval."""
        todo, request = await self._load_todo_with_request(db, todo_id)
        if todo.status not in {"pending", "approved"} or request.aggregate_decision == "rejected":
            raise AppError("TODO_ALREADY_DECIDED", 409)

        skill = await db.get(Skill, request.skill_id) if request.skill_id else None
        visible_assignee_ids = await visible_inbox_assignee_ids_for_user(db, actor)
        if todo.assignee not in visible_assignee_ids:
            if skill is None:
                if not is_global_inbox_reader(actor):
                    raise AppError("AUTH_DEPARTMENT_DENIED", 403)
            elif not require_department_access(skill.department, actor):
                raise AppError("AUTH_DEPARTMENT_DENIED", 403)

        payload = dict(request.payload or {})
        if title is not None:
            cleaned = title.strip()
            if not cleaned:
                raise AppError("PARAM_INVALID", 400, {"detail": "标题不能为空"})
            request.title = cleaned[:200]
        if summary is not None:
            request.summary = summary.strip()[:500] or None
        if recommended_decision is not None:
            payload["recommended_decision"] = recommended_decision.strip()[:1000]
            output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
            output = dict(output)
            output["recommendation"] = payload["recommended_decision"]
            payload["output"] = output
        if approval_question is not None:
            payload["approval_question"] = approval_question.strip()[:500]
        if suggested_actions is not None:
            cleaned_actions = [str(item).strip()[:500] for item in suggested_actions if str(item).strip()]
            payload["suggested"] = cleaned_actions
            existing = payload.get("operation_actions") if isinstance(payload.get("operation_actions"), list) else []
            operation_actions: list[dict] = []
            for index, action in enumerate(cleaned_actions):
                prior = existing[index] if index < len(existing) and isinstance(existing[index], dict) else {}
                next_item = dict(prior)
                next_item["action"] = action
                next_item["human_action"] = action
                next_item.setdefault("owner", "运营")
                operation_actions.append(next_item)
            payload["operation_actions"] = operation_actions
            output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
            output = dict(output)
            output["suggestions"] = cleaned_actions
            payload["output"] = output
        if forbidden_actions is not None:
            payload["forbidden_actions"] = [
                str(item).strip()[:300] for item in forbidden_actions if str(item).strip()
            ]

        updated_tasks: list[TodoDispatchTask] = []
        if dispatch_tasks is not None:
            existing_tasks = {
                task.id: task
                for task in await self.list_dispatch_tasks_for_request(db, request.id)
            }
            for item in dispatch_tasks:
                task_id = int(item.get("id") or 0)
                task = existing_tasks.get(task_id)
                if not task:
                    raise AppError("TODO_NOT_FOUND", 404, {"detail": f"派发任务不存在: {task_id}"})
                if task.status in {"done", "cancelled"}:
                    raise AppError("TODO_ALREADY_DECIDED", 409, {"detail": "已完成或已取消的任务不能编辑"})
                if "executor" in item and item.get("executor") is not None:
                    executor_id = str(item.get("executor") or "").strip()[:50]
                    if executor_id:
                        executor_user = await self._ensure_dispatch_executor(
                            db,
                            executor_id=executor_id,
                            skill=skill,
                            actor=actor,
                            fallback_user=actor,
                        )
                        task.executor = executor_user.id
                    else:
                        task.executor = None
                if "content" in item and item.get("content") is not None:
                    content = str(item.get("content") or "").strip()
                    if not content:
                        raise AppError("PARAM_INVALID", 400, {"detail": "派发任务内容不能为空"})
                    task.content = content[:2000]
                if "deadline" in item:
                    deadline_raw = item.get("deadline")
                    if deadline_raw in (None, ""):
                        task.deadline = None
                    else:
                        try:
                            task.deadline = parse_bjt_datetime(str(deadline_raw))
                        except ValueError as exc:
                            raise AppError("PARAM_INVALID", 400, {"detail": f"截止时间格式错误: {deadline_raw}"}) from exc
                task.updated_at = now_bjt()
                updated_tasks.append(task)

            if updated_tasks:
                payload["dispatch_tasks"] = [
                    {
                        "id": task.id,
                        "executor": task.executor,
                        "content": task.content,
                        "deadline": isoformat_bjt(task.deadline),
                        "extra": task.extra,
                    }
                    for task in sorted(existing_tasks.values(), key=lambda t: t.id)
                ]

        request.payload = payload
        await db.flush()
        serialized_tasks = []
        if request.kind == "dispatch":
            tasks = await self.list_dispatch_tasks_for_request(db, request.id)
            serialized_tasks = await self._serialize_dispatch_tasks_with_defaults(
                db,
                tasks=tasks,
                request=request,
                skill=skill,
                actor=actor,
            )
        return {
            "todo": self._serialize_todo(todo),
            "request": self._serialize_request(request),
            "payload": request.payload or {},
            "dispatch_tasks": serialized_tasks,
        }

    async def batch_extend_sla(
        self,
        db: AsyncSession,
        *,
        todo_ids: list[int],
        hours: int,
        actor: User | None = None,
        actor_id: str | None = None,
    ) -> dict:
        """GAP-7：批量延期。每条用 SAVEPOINT 包裹，单条失败 rollback 该 savepoint，
        其他继续；外层事务由 router 在调用完 commit。

        C2：每条都走 extend_sla 内部的 ``assert_can_modify_todo``，非 admin /
        dept_admin 的用户即使是 active 也无法改写他人 SLA。

        ``actor`` 是首选入参；``actor_id`` 仅向下兼容老 router/test 调用，会自动
        ``db.get(User, actor_id)`` 反查。
        """
        if actor is None and actor_id is not None:
            actor = await db.get(User, actor_id)
        if actor is None:
            raise AppError("AUTH_REQUIRED", 401)

        results: list[dict] = []
        for tid in todo_ids:
            try:
                # H9：每条单独 savepoint，单条失败不污染整批事务
                async with db.begin_nested():
                    await self.extend_sla(
                        db, todo_id=tid, hours=hours, actor=actor
                    )
                results.append({"todo_id": tid, "ok": True})
            except AppError as e:
                results.append({"todo_id": tid, "ok": False, "error": e.code})
            except Exception as exc:  # noqa: BLE001
                results.append({"todo_id": tid, "ok": False, "error": str(exc)[:200]})
        succeeded = sum(1 for r in results if r["ok"])
        return {
            "total": len(todo_ids),
            "succeeded": succeeded,
            "failed": len(todo_ids) - succeeded,
            "results": results,
        }

    async def batch_reassign(
        self,
        db: AsyncSession,
        *,
        todo_ids: list[int],
        to_user_id: str,
        actor: User,
        reason: str = "",
    ) -> dict:
        """GAP-8：批量把若干 Todo 的 assignee 改成 to_user_id（代办语义）。

        - actor 必须是全局可见角色（admin/system_admin/can_view_all）或 dept_admin
          且 Skill 部门在其管辖范围内（C2：通过 ``assert_can_modify_todo``）
        - 新 assignee 必须是 active 用户
        - 新 assignee 必须和 Skill 的部门匹配（或 can_view_all / admin）
        - 不改 DecisionRequest / aggregate_status，审批关系仍由 DR 表达
        - 每条成功转派写一条 ``audit.log("todo.reassigned", ...)``
        - 不直接推钉钉：Skill 产出的待办只进入平台待办中心；dispatch 任务仅在
          平台审批通过后 fan-out 给执行人钉钉。
        - H9：每条用 SAVEPOINT 包裹，单条失败 rollback 该 savepoint
        """
        target_user = await db.get(User, to_user_id)
        if target_user is None:
            raise AppError("AUTH_USER_NOT_FOUND", 404)
        # v2.0.15 C1：state 真源优先，兼容 is_active。pending（钉钉首登未激活）
        # 与 disabled 都不能被指派工作——否则前端看不到、但 todo 已绑在其头上。
        # 错误码保持 AUTH_ACCOUNT_DISABLED 以兼容历史前端文案，detail 里带出真值。
        target_state = getattr(target_user, "state", None)
        if target_state in ("disabled", "pending") or not target_user.is_active:
            raise AppError(
                "AUTH_ACCOUNT_DISABLED",
                403,
                {"detail": f"target user state={target_state or 'unknown'}"},
            )

        results: list[dict] = []
        for tid in todo_ids:
            try:
                async with db.begin_nested():
                    todo, request = await self._load_todo_with_request(db, tid)
                    skill = await db.get(Skill, request.skill_id) if request.skill_id else None
                    # C2：actor 必须有改写本 todo 的权限（admin/system_admin 或 dept_admin@该部门）
                    assert_can_modify_todo(
                        user=actor, todo=todo, request=request, skill=skill
                    )
                    # 跨部门拦截：若 skill 有部门信息，新 assignee 必须走 require_department_access
                    if skill and skill.department:
                        if not require_department_access(skill.department, target_user):
                            raise AppError(
                                "AUTH_DEPARTMENT_DENIED",
                                403,
                                {"detail": f"{to_user_id} 不在 Skill {skill.id} 所在部门"},
                            )
                    else:
                        # v2.0.15 C1 防御：orphan todo（skill 已删除或无部门归属）
                        # 非全局角色实际上走不到这里（assert_can_modify_todo 已拦），
                        # 这是防御性检查——万一 _acl 口径将来放宽，仍保证非全局角色
                        # 不能借 orphan 路径跨部门转派。
                        if not is_global_inbox_reader(actor):
                            if (
                                actor.department
                                and target_user.department
                                and actor.department != target_user.department
                            ):
                                raise AppError(
                                    "AUTH_DEPARTMENT_DENIED",
                                    403,
                                    {"detail": "orphan todo 非全局角色仅限同部门转派"},
                                )
                    previous_assignee = todo.assignee
                    if previous_assignee == to_user_id:
                        results.append({"todo_id": tid, "ok": True, "no_change": True})
                        continue
                    todo.assignee = to_user_id
                    todo.updated_at = now_bjt()
                    await db.flush()
                # savepoint 提交后再写 audit（独立 session，互不影响外层事务回滚语义）
                await audit.log(
                    actor.id,
                    "todo.reassigned",
                    "ai_todo",
                    str(tid),
                    detail={
                        "from": previous_assignee,
                        "to": to_user_id,
                        "reason": reason[:500] if reason else "",
                    },
                )
                results.append({"todo_id": tid, "ok": True})
            except AppError as e:
                results.append({"todo_id": tid, "ok": False, "error": e.code})
            except Exception as exc:  # noqa: BLE001
                results.append({"todo_id": tid, "ok": False, "error": str(exc)[:200]})
        succeeded = sum(1 for r in results if r["ok"])
        return {
            "total": len(todo_ids),
            "succeeded": succeeded,
            "failed": len(todo_ids) - succeeded,
            "results": results,
            "card_failures": [],
        }

    async def get_related_timeline(
        self,
        db: AsyncSession,
        *,
        todo_id: int,
        current_user: User,
        limit: int = 10,
    ) -> dict:
        """GAP-11：同 Skill 近 N 条历史决策（approved/rejected），倒序时间线。"""
        todo, request = await self._load_todo_with_request(db, todo_id)
        skill = await db.get(Skill, request.skill_id) if request.skill_id else None
        # 权限沿用 detail
        if todo.assignee != current_user.id:
            if skill is None:
                if not is_global_inbox_reader(current_user):
                    raise AppError("AUTH_DEPARTMENT_DENIED", 403)
            elif not require_department_access(skill.department, current_user):
                raise AppError("AUTH_DEPARTMENT_DENIED", 403)

        if not request.skill_id:
            return {"skill_id": None, "items": []}

        stmt = (
            select(AITodo, DecisionRequest)
            .join(DecisionRequest, DecisionRequest.id == AITodo.request_id)
            .where(DecisionRequest.skill_id == request.skill_id)
            .where(AITodo.status.in_(("approved", "rejected")))
            .where(AITodo.id != todo_id)
            .order_by(AITodo.decided_at.desc().nulls_last(), AITodo.id.desc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).all()
        items = [
            {
                "todo_id": t.id,
                "request_id": t.request_id,
                "decision": t.status,
                "decided_at": isoformat_bjt(t.decided_at),
                "decided_by": t.decided_by,
                "decision_reason": t.decision_reason,
                "title": r.title,
                "summary": (r.summary or "")[:200],
            }
            for t, r in rows
        ]
        return {"skill_id": request.skill_id, "items": items}

    async def get_dispatch_calendar(
        self,
        db: AsyncSession,
        *,
        current_user: User,
        date_from: datetime,
        date_to: datetime,
    ) -> dict:
        """GAP-12：按 deadline 日期分组派发任务；供日历视图渲染。"""
        stmt = (
            select(TodoDispatchTask)
            .where(TodoDispatchTask.executor == current_user.id)
            .where(TodoDispatchTask.deadline.is_not(None))
            .where(TodoDispatchTask.deadline >= date_from)
            .where(TodoDispatchTask.deadline <= date_to)
            .order_by(TodoDispatchTask.deadline.asc())
        )
        tasks = (await db.execute(stmt)).scalars().all()
        by_day: dict[str, dict] = {}
        for task in tasks:
            key = task.deadline.date().isoformat()
            bucket = by_day.setdefault(
                key, {"day": key, "count": 0, "task_ids": [], "statuses": {}}
            )
            bucket["count"] += 1
            bucket["task_ids"].append(task.id)
            status = task.status or "unknown"
            bucket["statuses"][status] = bucket["statuses"].get(status, 0) + 1
        return {
            "date_from": date_from.date().isoformat(),
            "date_to": date_to.date().isoformat(),
            "days": sorted(by_day.values(), key=lambda d: d["day"]),
        }

    async def batch_ack_dispatch(
        self,
        db: AsyncSession,
        *,
        task_ids: list[int],
        actor_id: str,
        note: str = "",
    ) -> dict:
        """GAP-9：批量标记派发任务完成。

        C2：批量入口比单条 ack 更严格——只允许执行人本人 ack 自己的 task；
        admin 走单条 ``POST /api/todos/dispatch/{id}/ack`` 修复个案，不允许走 batch
        替别人勾完成（避免一次扫平产生大批审计无主记录）。

        H9：每条用 SAVEPOINT 包裹，单条失败 rollback 不影响其他。
        """
        actor = await db.get(User, actor_id)
        visible_executor_ids = (
            await visible_inbox_assignee_ids_for_user(db, actor)
            if actor is not None
            else [actor_id]
        )
        results: list[dict] = []
        for tid in task_ids:
            try:
                async with db.begin_nested():
                    # C2：先做 ownership 校验，非 executor 直接拒；不暴露 task 内容
                    task = await db.get(TodoDispatchTask, tid)
                    if task is None:
                        raise AppError("TODO_NOT_FOUND", 404)
                    if task.executor not in visible_executor_ids:
                        raise AppError("AUTH_PERMISSION_DENIED", 403)
                    await self.ack_dispatch_task(
                        db,
                        task_id=tid,
                        actor_id=actor_id,
                        channel="web",
                        note=note,
                    )
                results.append({"task_id": tid, "ok": True})
            except AppError as e:
                results.append({"task_id": tid, "ok": False, "error": e.code})
            except Exception as exc:  # noqa: BLE001
                results.append({"task_id": tid, "ok": False, "error": str(exc)[:200]})
        succeeded = sum(1 for r in results if r["ok"])
        return {
            "total": len(task_ids),
            "succeeded": succeeded,
            "failed": len(task_ids) - succeeded,
            "results": results,
        }

    async def expire_due_todos(self, db: AsyncSession) -> list[dict]:
        """扫描过期 todo 并归档；返回过期记录的轻量摘要供 scheduler 记录日志。"""
        now = now_bjt()
        stmt = (
            select(AITodo, DecisionRequest)
            .join(DecisionRequest, DecisionRequest.id == AITodo.request_id)
            .where(AITodo.status == "pending")
            .where(DecisionRequest.sla_at < now)
        )
        rows = (await db.execute(stmt)).all()
        if not rows:
            return []

        expired_summaries: list[dict] = []
        touched_request_ids = set()
        for todo, request in rows:
            todo.status = "expired"
            todo.decided_at = now
            todo.decision_channel = "scheduler"
            touched_request_ids.add(request.id)
            expired_summaries.append({
                "todo_id": todo.id,
                "request_id": request.id,
                "assignee": todo.assignee,
                "skill_id": request.skill_id,
                "title": request.title,
            })

        if touched_request_ids:
            await db.execute(
                update(DecisionRequest)
                .where(DecisionRequest.id.in_(touched_request_ids))
                .values(
                    aggregate_status="expired",
                    completed_at=now,
                )
            )
        await db.flush()

        # aggregate_status 发生变更 → 收件箱报告的 related_pending_request_count
        # 会变,必须清 inbox 列表/详情缓存,否则依赖 60s TTL 兜底。
        if touched_request_ids:
            try:
                from app.common.cache import invalidate_inbox

                await invalidate_inbox()
            except Exception as e:
                logger.warning(
                    "invalidate_inbox 失败 (expire_due_todos, count={}): {}",
                    len(touched_request_ids),
                    e,
                )
        return expired_summaries

    async def _update_aggregate(
        self,
        db: AsyncSession,
        request: DecisionRequest,
        todo: AITodo,
        decision: str,
    ) -> None:
        if request.decision_mode == "any_of":
            request.aggregate_status = "completed"
            request.aggregate_decision = decision
            request.completed_at = now_bjt()
            await db.execute(
                update(AITodo)
                .where(AITodo.request_id == request.id)
                .where(AITodo.id != todo.id)
                .where(AITodo.status == "pending")
                .values(
                    status="resolved_by_peer",
                    decided_at=now_bjt(),
                    decided_by=todo.decided_by,
                    decision_channel=todo.decision_channel,
                )
            )
            await self._on_aggregate_completed(db, request)
            return

        if request.decision_mode != "any_of":
            invalid_statuses = await db.scalar(
                select(func.count(AITodo.id))
                .where(AITodo.request_id == request.id)
                .where(AITodo.status == "resolved_by_peer")
            )
            if invalid_statuses:
                raise AssertionError(
                    f"resolved_by_peer 不应在 {request.decision_mode} 模式出现"
                )

        pending = await db.scalar(
            select(func.count(AITodo.id))
            .where(AITodo.request_id == request.id)
            .where(AITodo.status == "pending")
        )
        if pending:
            return

        statuses = [
            row[0]
            for row in (
                await db.execute(
                    select(AITodo.status).where(AITodo.request_id == request.id)
                )
            ).all()
        ]
        request.aggregate_status = "completed"
        request.completed_at = now_bjt()
        if request.decision_mode == "independent":
            has_approved = "approved" in statuses
            has_rejected = "rejected" in statuses
            if has_approved and has_rejected:
                request.aggregate_decision = "mixed"
            elif has_rejected:
                request.aggregate_decision = "rejected"
            else:
                request.aggregate_decision = "approved"
        else:
            request.aggregate_decision = "rejected" if "rejected" in statuses else "approved"

        await self._on_aggregate_completed(db, request)

    async def _on_aggregate_completed(
        self,
        db: AsyncSession,
        request: DecisionRequest,
    ) -> None:
        """聚合状态变成 completed 时的副作用：dispatch fan-out + OpenClaw 回调。"""
        # 1. dispatch 类型 + approved → 把所有 awaiting_dispatch 子任务推钉钉
        if request.kind == "dispatch":
            if request.aggregate_decision == "approved":
                await self._fan_out_dispatch_tasks(db, request)
            else:
                # 驳回 → 全部子任务取消
                await db.execute(
                    update(TodoDispatchTask)
                    .where(TodoDispatchTask.request_id == request.id)
                    .where(TodoDispatchTask.status == "awaiting_dispatch")
                    .values(status="cancelled", updated_at=now_bjt())
                )

        # 2. OpenClaw 回调
        if request.callback_status == "pending" and request.callback_payload:
            await self._notify_openclaw_callback(db, request)

    async def _fan_out_dispatch_tasks(
        self,
        db: AsyncSession,
        request: DecisionRequest,
    ) -> None:
        """把 awaiting_dispatch 状态的子任务推送到每个 executor 个人钉钉。"""
        result = await db.execute(
            select(TodoDispatchTask)
            .where(TodoDispatchTask.request_id == request.id)
            .where(TodoDispatchTask.status.in_(["awaiting_dispatch", "pending_assignment"]))
        )
        tasks = list(result.scalars().all())
        if not tasks:
            return

        # 预取 executor user 信息
        executor_ids = [executor for executor in {t.executor for t in tasks} if executor]
        users_result = await db.execute(select(User).where(User.id.in_(executor_ids)))
        users_by_id = {u.id: u for u in users_result.scalars().all()}

        now = now_bjt()
        for task in tasks:
            if not task.executor:
                task.status = "pending_assignment"
                task.updated_at = now
                continue
            executor_user = users_by_id.get(task.executor)
            await self._dispatch_single_task(db, request, task, executor_user)

        await db.flush()

    async def _assert_dispatch_tasks_ready_for_approval(
        self,
        db: AsyncSession,
        request: DecisionRequest,
        *,
        actor: User,
    ) -> None:
        tasks = (
            await db.execute(
                select(TodoDispatchTask)
                .where(TodoDispatchTask.request_id == request.id)
                .order_by(TodoDispatchTask.id.asc())
            )
        ).scalars().all()
        if not tasks:
            return

        skill = await db.get(Skill, request.skill_id) if request.skill_id else None
        missing_task_ids: list[int] = []
        invalid_executor_errors: list[dict[str, str | int]] = []
        now = now_bjt()
        for task in tasks:
            executor_id = str(task.executor or "").strip()
            if not executor_id:
                default_user = await self._valid_default_dispatch_executor(
                    db,
                    request=request,
                    skill=skill,
                    actor=actor,
                    task=task,
                )
                if default_user:
                    task.executor = default_user.id
                    task.assigned_by = actor.id
                    task.assigned_at = now
                    task.updated_at = now
                    continue
                missing_task_ids.append(task.id)
                continue
            try:
                executor_user = await self._ensure_dispatch_executor(
                    db,
                    executor_id=executor_id,
                    skill=skill,
                    actor=actor,
                    fallback_user=actor,
                )
                if executor_user.id != executor_id:
                    task.executor = executor_user.id
                    task.updated_at = now
            except AppError as exc:
                invalid_executor_errors.append({
                    "task_id": task.id,
                    "executor": executor_id,
                    "code": exc.code,
                    "detail": str((exc.detail or {}).get("detail") or exc.message),
                })

        if missing_task_ids or invalid_executor_errors:
            raise AppError(
                "TODO_DISPATCH_EXECUTOR_REQUIRED",
                400,
                {
                    "detail": "请先从当前部门组织架构中选择执行人，再通过并派发",
                    "task_ids": missing_task_ids,
                    "invalid_executors": invalid_executor_errors,
                },
            )

    async def _dispatch_single_task(
        self,
        db: AsyncSession,
        request: DecisionRequest,
        task: TodoDispatchTask,
        executor_user: User | None,
    ) -> None:
        """派发单个执行任务给指定 executor。"""
        from app.dingtalk.card_templates import build_dispatch_executor_card

        now = now_bjt()
        if executor_user:
            canonical_user = await resolve_work_notice_user(db, executor_user)
            if canonical_user and canonical_user.id != executor_user.id:
                logger.info(
                    "派发任务 {} 执行人归一: {} -> {}",
                    task.id,
                    executor_user.id,
                    canonical_user.id,
                )
                executor_user = canonical_user
                task.executor = canonical_user.id

        if task.executor:
            db.add(
                Notification(
                    user_id=task.executor,
                    type="todo_dispatch_assigned",
                    title=f"[任务] {request.title}",
                    body=task.content[:500],
                    link=f"/todos/dispatch/{task.id}",
                )
            )

        if not executor_user or not task.executor or not executor_user.dingtalk_user_id:
            logger.warning(
                "派发子任务 {} 的执行人 {} 没有钉钉绑定, 仅站内可见", task.id, task.executor
            )
            task.status = "pushed_no_dingtalk" if task.executor else "pending_assignment"
            task.dispatched_at = now if task.executor else None
            task.updated_at = now
            return

        try:
            payload = build_dispatch_executor_card(
                request=request,
                dispatch_task=task,
                executor_user=executor_user,
            )
            msg_id = await outbox.enqueue(
                "work_notice",
                executor_user.dingtalk_user_id,
                payload,
                priority=2,
                related_type="dispatch_task",
                related_id=str(task.id),
                session=db,
            )
            task.dingtalk_msg_id = str(msg_id)
            task.status = "sent"
            task.dispatched_at = now
            task.updated_at = now
        except Exception as e:
            logger.warning("派发子任务 {} 推送钉钉失败: {}", task.id, e)
            task.status = "pushed_no_dingtalk"
            task.dispatched_at = now
            task.updated_at = now

    async def _notify_openclaw_callback(
        self,
        db: AsyncSession,
        request: DecisionRequest,
    ) -> None:
        """决策完成 → 通知 OpenClaw/AIClaw 让 Skill 继续。"""
        cb = request.callback_payload or {}
        instance_id = cb.get("instance_id")
        skill_id = cb.get("skill_id")
        skill_run_id = cb.get("run_id")
        next_step = cb.get("next_step")

        if not instance_id or not skill_id or not skill_run_id:
            request.callback_status = "failed"
            request.callback_error = "callback_payload 缺少 instance_id/skill_id/run_id"
            return

        request.callback_attempts = (request.callback_attempts or 0) + 1

        # P2-3: 内联 3 次 HTTP 重试（指数退避）解决瞬时网络抖动；仍失败保持
        # callback_status="pending"，由 scheduler.callback_retry_loop 后台扫描。
        #
        # 计数语义（codex 二轮 review 澄清）：callback_attempts 记录的是
        # **本函数被调用的次数（=调度轮次）**，而不是实际 HTTP 尝试次数。
        # 每次调用最多 3 次内联 HTTP retry。max=5（callback_attempts<5）允许：
        #   1 次主流程触发 + 4 次后台 retry loop = 共 5 轮调度 × 最多 3 次 HTTP = 上限 15 次。
        # 这个上限是有意的：我们宁可重发不可丢消息。如果未来要降级，把
        # callback_retry_loop 里的 attempts < 5 改小即可。
        from app.aiclaw.bridge_registry import bridge_registry
        import asyncio as _aio

        last_error: str | None = None
        for retry in range(3):
            try:
                connection = bridge_registry.get(instance_id)
                if not connection:
                    raise RuntimeError(f"AIClaw bridge {instance_id} 未连接")

                # 收集 dispatch 任务完成摘要
                tasks_summary = await self._collect_dispatch_summary(db, request.id)

                await connection.bridge_op(
                    "notify_decision",
                    {
                        "skill_id": skill_id,
                        "run_id": skill_run_id,
                        "next_step": next_step,
                        "decision": request.aggregate_decision,
                        "request_id": request.id,
                        "tasks_summary": tasks_summary,
                        "extra": cb.get("extra"),
                    },
                    timeout=15,
                )
                request.callback_status = "sent"
                request.callback_completed_at = now_bjt()
                request.callback_error = None
                request.callback_next_retry_at = None
                return
            except Exception as e:
                last_error = str(e)[:500]
                logger.warning(
                    "OpenClaw 决策回调失败 request={} retry={} error={}",
                    request.id, retry + 1, e,
                )
                if retry < 2:
                    await _aio.sleep(0.5 * (2 ** retry))  # 0.5s, 1s

        # 内联重试全部失败：保持 callback_status="pending"，由 scheduler 兜底
        # scheduler 扫 status="pending" 且 attempts<5 的进行重试，5 次后转 "failed"
        request.callback_status = "pending"
        request.callback_error = last_error

    async def _load_todo_with_request(
        self,
        db: AsyncSession,
        todo_id: int,
    ) -> tuple[AITodo, DecisionRequest]:
        row = (
            await db.execute(
                select(AITodo, DecisionRequest)
                .join(DecisionRequest, DecisionRequest.id == AITodo.request_id)
                .where(AITodo.id == todo_id)
            )
        ).first()
        if not row:
            raise AppError("TODO_NOT_FOUND", 404)
        return row[0], row[1]

    async def list_dispatch_tasks_for_request(
        self,
        db: AsyncSession,
        request_id: str,
    ) -> list[TodoDispatchTask]:
        result = await db.execute(
            select(TodoDispatchTask)
            .where(TodoDispatchTask.request_id == request_id)
            .order_by(TodoDispatchTask.id.asc())
        )
        return list(result.scalars().all())

    async def _serialize_dispatch_tasks_with_defaults(
        self,
        db: AsyncSession,
        *,
        tasks: list[TodoDispatchTask],
        request: DecisionRequest,
        skill: Skill | None,
        actor: User,
    ) -> list[dict]:
        executor_ids = {str(task.executor or "").strip() for task in tasks if str(task.executor or "").strip()}
        default_users_by_task_id: dict[int, User] = {}
        object_keys_by_task_id: dict[int, str] = {}
        for task in tasks:
            object_key = self._dispatch_task_object_key(task, request)
            object_keys_by_task_id[task.id] = object_key
            if str(task.executor or "").strip():
                continue
            default_user = await self._valid_default_dispatch_executor(
                db,
                request=request,
                skill=skill,
                actor=actor,
                task=task,
                dispatch_object_key=object_key,
            )
            if default_user:
                default_users_by_task_id[task.id] = default_user
                executor_ids.add(default_user.id)
        users_by_id: dict[str, User] = {}
        if executor_ids:
            users = (
                await db.execute(select(User).where(User.id.in_(list(executor_ids))))
            ).scalars().all()
            users_by_id = {user.id: user for user in users}

        serialized: list[dict] = []
        for task in tasks:
            row = self._serialize_dispatch_task(task)
            try:
                from app.dingtalk.card_templates import build_dispatch_execution_content

                row["display_content"] = build_dispatch_execution_content(request, task) or row["content"]
            except Exception:  # noqa: BLE001
                row["display_content"] = row["content"]
            row["dispatch_object_key"] = object_keys_by_task_id.get(task.id, "")
            executor_id = str(task.executor or "").strip()
            executor_user = users_by_id.get(executor_id) if executor_id else None
            if executor_user:
                row["executor_name"] = executor_user.name
                row["executor_department"] = executor_user.department
            else:
                row["executor_name"] = None
                row["executor_department"] = None

            if executor_id:
                row["default_executor"] = None
                row["default_executor_name"] = None
                row["default_executor_department"] = None
                row["executor_source"] = "assigned"
            elif default_user := default_users_by_task_id.get(task.id):
                row["default_executor"] = default_user.id
                row["default_executor_name"] = default_user.name
                row["default_executor_department"] = default_user.department
                row["executor_source"] = "default"
            else:
                row["default_executor"] = None
                row["default_executor_name"] = None
                row["default_executor_department"] = None
                row["executor_source"] = "none"
            serialized.append(row)
        return serialized

    async def list_my_dispatch_tasks(
        self,
        db: AsyncSession,
        *,
        executor_id: str,
        executor_user: User | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        identity_has_global_scope = False
        executor_ids = [executor_id]
        if executor_user is not None:
            executor_ids, identity_has_global_scope = (
                await visible_inbox_assignee_scope_for_user(db, executor_user)
            )
            if not executor_ids:
                executor_ids = [executor_id]
        filters = [TodoDispatchTask.executor.in_(executor_ids)]
        if status:
            filters.append(TodoDispatchTask.status == status)
        else:
            filters.append(
                TodoDispatchTask.status.in_(
                    ["sent", "pushed_no_dingtalk", "in_progress", "blocked"]
                )
            )

        # P0-4 部门隔离：除全局可见角色外，仅返回当前部门的派发任务
        # （executor 已过滤为本人，但 admin/api 复用接口时仍补一道防御）
        need_dept_filter = (
            executor_user is not None
            and not is_global_inbox_reader(executor_user)
            and not identity_has_global_scope
        )

        count_stmt = (
            select(func.count(TodoDispatchTask.id))
            .join(DecisionRequest, DecisionRequest.id == TodoDispatchTask.request_id)
            .join(Skill, Skill.id == DecisionRequest.skill_id)
            .where(*filters)
        )
        if need_dept_filter:
            count_stmt = count_stmt.where(Skill.department == executor_user.department)
        total = await db.scalar(count_stmt) or 0

        stmt = (
            select(TodoDispatchTask, DecisionRequest)
            .join(DecisionRequest, DecisionRequest.id == TodoDispatchTask.request_id)
            .join(Skill, Skill.id == DecisionRequest.skill_id)
            .where(*filters)
            .order_by(TodoDispatchTask.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if need_dept_filter:
            stmt = stmt.where(Skill.department == executor_user.department)
        rows = (await db.execute(stmt)).all()
        items = []
        for task, req in rows:
            row = self._serialize_dispatch_task(task)
            try:
                from app.dingtalk.card_templates import build_dispatch_execution_content

                row["display_content"] = build_dispatch_execution_content(req, task) or row["content"]
            except Exception:  # noqa: BLE001
                row["display_content"] = row["content"]
            row.update({
                "title": req.title,
                "skill_id": req.skill_id,
                "request_id": req.id,
            })
            items.append(row)
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    async def ack_dispatch_task(
        self,
        db: AsyncSession,
        *,
        task_id: int,
        actor_id: str,
        channel: str = "web",
        note: str = "",
        feedback_payload: dict | None = None,
    ) -> dict:
        """执行人确认完成派发子任务。"""
        ack_note = self._normalize_dispatch_ack_note(note)
        clean_feedback_payload = self._normalize_dispatch_ack_feedback_payload(
            feedback_payload,
            channel=channel,
            note=ack_note,
        )
        task = await db.get(TodoDispatchTask, task_id)
        if not task:
            raise AppError("TODO_NOT_FOUND", 404)
        actor = await db.get(User, actor_id)
        if not actor:
            raise AppError("AUTH_USER_NOT_FOUND", 404)
        visible_executor_ids = await visible_inbox_assignee_ids_for_user(db, actor)
        if task.executor not in visible_executor_ids:
            # P0-4 部门隔离：非 executor 必须有 Skill 部门访问权（orphan skill 仅全局可见角色可代办）
            req = await db.get(DecisionRequest, task.request_id)
            skill_for_dept = await db.get(Skill, req.skill_id) if req else None
            if skill_for_dept is None:
                if not is_global_inbox_reader(actor):
                    raise AppError("AUTH_DEPARTMENT_DENIED", 403)
            elif not require_department_access(skill_for_dept.department, actor):
                raise AppError("AUTH_DEPARTMENT_DENIED", 403)
        if task.status == "done":
            return self._serialize_dispatch_task(task)
        if task.status not in {"sent", "pushed_no_dingtalk", "in_progress", "blocked"}:
            raise AppError("TODO_ALREADY_DECIDED", 409)

        now = now_bjt()
        task.status = "done"
        task.ack_at = now
        task.ack_note = ack_note or None
        task.ack_channel = channel
        if clean_feedback_payload:
            extra = dict(task.extra or {})
            extra["ack_feedback_payload"] = clean_feedback_payload
            task.extra = extra
        task.updated_at = now
        await db.flush()
        try:
            from app.learning.service import capture_todo_dispatch_task

            await capture_todo_dispatch_task(db, task)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "capture learning dispatch ack failed (task_id={}): {}",
                task.id,
                e,
                exc_info=True,
            )
        return self._serialize_dispatch_task(task)

    @staticmethod
    def _normalize_dispatch_ack_note(note: str | None) -> str:
        normalized = str(note or "").strip()
        if len(normalized) > 1000:
            raise AppError(
                "DISPATCH_ACK_NOTE_TOO_LONG",
                400,
                {"detail": "完成说明最多 1000 字"},
            )
        return normalized

    @classmethod
    def _normalize_dispatch_ack_feedback_payload(
        cls,
        payload: dict | None,
        *,
        channel: str,
        note: str,
    ) -> dict:
        clean: dict = {
            "source_channel": str(channel or "unknown")[:80],
            "action": "dispatch_ack",
        }
        if note:
            clean["note_text"] = str(note).strip()[:1000]
        if not isinstance(payload, dict):
            return clean if note else {}
        reserved = {"source_channel", "action", "note_text"}
        fields: dict = {}
        for key, value in payload.items():
            key_text = str(key or "").strip()
            if not key_text:
                continue
            normalized_key = key_text[:80]
            normalized_value = cls._feedback_value(value)
            if normalized_value is None:
                continue
            if normalized_key in reserved:
                clean[normalized_key] = normalized_value
            else:
                fields[normalized_key] = normalized_value
        if fields:
            clean["fields"] = fields
        return clean

    @staticmethod
    def _active_user_filter():
        return (User.state == "active") & (User.is_active == True)  # noqa: E712

    @staticmethod
    def _dispatch_target_department(skill: Skill | None, fallback_user: User | None = None) -> str | None:
        if skill and skill.department:
            return skill.department
        department = getattr(fallback_user, "department", None)
        return str(department).strip() or None if department else None

    @staticmethod
    def _dispatch_scope_value(value: str | None) -> str:
        return str(value or "").strip()

    @staticmethod
    def _dispatch_object_key_from_value(value: object) -> str:
        if value is None or isinstance(value, bool):
            return ""
        normalized = re.sub(r"\s+", "", str(value).strip())
        if not normalized or normalized.lower() in {"none", "null", "nan"}:
            return ""
        return f"item:{normalized[:90]}"

    @classmethod
    def _find_dispatch_object_key(cls, data: object) -> str:
        if not isinstance(data, dict):
            return ""
        direct_keys = (
            "item_id",
            "itemId",
            "object_id",
            "objectId",
            "product_id",
            "productId",
            "goods_id",
            "goodsId",
            "sku_id",
            "skuId",
        )
        for key in direct_keys:
            object_key = cls._dispatch_object_key_from_value(data.get(key))
            if object_key:
                return object_key
        object_keys = ("item", "product", "object", "sku")
        for key in object_keys:
            value = data.get(key)
            if isinstance(value, dict):
                object_key = cls._find_dispatch_object_key(value)
                if object_key:
                    return object_key
                object_key = cls._dispatch_object_key_from_value(value.get("id"))
                if object_key:
                    return object_key
            elif re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z_-]{2,}", str(value or "").strip()):
                object_key = cls._dispatch_object_key_from_value(value)
                if object_key:
                    return object_key
        for key in ("payload", "context", "metadata", "meta"):
            object_key = cls._find_dispatch_object_key(data.get(key))
            if object_key:
                return object_key
        return ""

    @classmethod
    def _dispatch_object_key_from_content(cls, content: str | None) -> str:
        text = str(content or "")
        if not text.strip():
            return ""
        match = re.search(
            r"(?:商品\s*ID|商品ID|商品|宝贝\s*ID|宝贝ID|宝贝|链接|SKU|sku|item[_\s-]*id|itemId)"
            r"[^0-9A-Za-z]{0,12}([0-9A-Za-z][0-9A-Za-z_-]{2,})",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return ""
        return cls._dispatch_object_key_from_value(match.group(1))

    @classmethod
    def _dispatch_task_object_key(
        cls,
        task: TodoDispatchTask,
        request: DecisionRequest | None,
    ) -> str:
        object_key = cls._find_dispatch_object_key(task.extra)
        if object_key:
            return object_key
        object_key = cls._dispatch_object_key_from_content(task.content)
        if object_key:
            return object_key
        payload = request.payload if request and isinstance(request.payload, dict) else None
        return cls._find_dispatch_object_key(payload)

    async def _dispatch_preference_scope(
        self,
        db: AsyncSession,
        *,
        skill: Skill | None,
        fallback_user: User | None,
    ) -> tuple[str, str]:
        target_org_unit_id = await self._resolve_dispatch_org_unit_id(
            db,
            skill=skill,
            fallback_user=fallback_user,
        )
        return (
            self._dispatch_scope_value(target_org_unit_id),
            self._dispatch_scope_value(self._dispatch_target_department(skill, fallback_user)),
        )

    async def _load_dispatch_preference(
        self,
        db: AsyncSession,
        *,
        actor: User,
        skill_id: str,
        skill: Skill | None,
        dispatch_object_key: str = "",
    ) -> TodoDispatchPreference | None:
        target_org_unit_id, department = await self._dispatch_preference_scope(
            db,
            skill=skill,
            fallback_user=actor,
        )
        object_key = self._dispatch_scope_value(dispatch_object_key)
        return await db.scalar(
            select(TodoDispatchPreference)
            .where(TodoDispatchPreference.actor_id == actor.id)
            .where(TodoDispatchPreference.skill_id == skill_id)
            .where(TodoDispatchPreference.target_org_unit_id == target_org_unit_id)
            .where(TodoDispatchPreference.department == department)
            .where(TodoDispatchPreference.dispatch_object_key == object_key)
            .limit(1)
        )

    async def _valid_default_dispatch_executor(
        self,
        db: AsyncSession,
        *,
        request: DecisionRequest,
        skill: Skill | None,
        actor: User,
        task: TodoDispatchTask | None = None,
        dispatch_object_key: str | None = None,
    ) -> User | None:
        object_key = (
            self._dispatch_scope_value(dispatch_object_key)
            if dispatch_object_key is not None
            else self._dispatch_task_object_key(task, request) if task else ""
        )
        preference = await self._load_dispatch_preference(
            db,
            actor=actor,
            skill_id=request.skill_id,
            skill=skill,
            dispatch_object_key=object_key,
        )
        executor_id = str(getattr(preference, "last_executor_id", "") or "").strip()
        if executor_id:
            try:
                return await self._ensure_dispatch_executor(
                    db,
                    executor_id=executor_id,
                    skill=skill,
                    actor=actor,
                    fallback_user=actor,
                )
            except AppError as exc:
                logger.info(
                    "忽略失效默认派发人 actor={} skill={} executor={} code={}",
                    actor.id,
                    request.skill_id,
                    executor_id,
                    exc.code,
                )

        if not await self._request_has_manager_todo_for_actor_identity(
            db,
            request_id=request.id,
            actor=actor,
        ):
            return None
        if request.skill_id != TMALL_LINK_DECLINE_OPERATOR_SKILL_ID:
            return None
        actor_default = await resolve_work_notice_user(db, actor) or actor
        try:
            return await self._ensure_dispatch_executor(
                db,
                executor_id=actor_default.id,
                skill=skill,
                actor=actor,
                fallback_user=actor,
            )
        except AppError as exc:
            logger.info(
                "忽略当前审批人默认派发人 actor={} skill={} executor={} code={}",
                actor.id,
                request.skill_id,
                actor_default.id,
                exc.code,
            )
            return None

    async def _remember_dispatch_preference(
        self,
        db: AsyncSession,
        *,
        request: DecisionRequest,
        actor_id: str | None,
    ) -> None:
        if not actor_id or not request.skill_id:
            return
        actor = await db.get(User, actor_id)
        if not actor:
            return
        skill = await db.get(Skill, request.skill_id) if request.skill_id else None
        tasks = await self.list_dispatch_tasks_for_request(db, request.id)
        executor_by_object_key: dict[str, str] = {}
        for task in sorted(tasks, key=lambda item: item.id):
            executor_id = str(task.executor or "").strip()
            if not executor_id or task.status == "cancelled":
                continue
            object_key = self._dispatch_task_object_key(task, request)
            executor_by_object_key[object_key] = executor_id
        if not executor_by_object_key:
            return

        target_org_unit_id, department = await self._dispatch_preference_scope(
            db,
            skill=skill,
            fallback_user=actor,
        )
        now = now_bjt()
        updated = False
        for object_key, executor_id in executor_by_object_key.items():
            try:
                executor_user = await self._ensure_dispatch_executor(
                    db,
                    executor_id=executor_id,
                    skill=skill,
                    actor=actor,
                    fallback_user=actor,
                )
            except AppError as exc:
                logger.info(
                    "默认派发人写入跳过 actor={} skill={} object={} executor={} code={}",
                    actor_id,
                    request.skill_id,
                    object_key,
                    executor_id,
                    exc.code,
                )
                continue

            stmt = pg_insert(TodoDispatchPreference).values(
                actor_id=actor.id,
                skill_id=request.skill_id,
                target_org_unit_id=target_org_unit_id,
                department=department,
                dispatch_object_key=object_key,
                last_executor_id=executor_user.id,
                updated_at=now,
            ).on_conflict_do_update(
                constraint="uq_todo_dispatch_pref_scope_object",
                set_={
                    "last_executor_id": executor_user.id,
                    "updated_at": now,
                },
            )
            await db.execute(stmt)
            await db.execute(
                select(TodoDispatchPreference)
                .where(TodoDispatchPreference.actor_id == actor.id)
                .where(TodoDispatchPreference.skill_id == request.skill_id)
                .where(TodoDispatchPreference.target_org_unit_id == target_org_unit_id)
                .where(TodoDispatchPreference.department == department)
                .where(TodoDispatchPreference.dispatch_object_key == object_key)
                .execution_options(populate_existing=True)
            )
            updated = True
        if updated:
            await db.flush()

    async def _ensure_dispatch_executor(
        self,
        db: AsyncSession,
        *,
        executor_id: str,
        skill: Skill | None,
        actor: User | None = None,
        fallback_user: User | None = None,
    ) -> User:
        executor_user = await db.get(User, executor_id)
        if executor_user is None:
            raise AppError("AUTH_USER_NOT_FOUND", 404)

        executor_state = str(getattr(executor_user, "state", "") or "").strip()
        if executor_state != "active" or not executor_user.is_active:
            raise AppError(
                "AUTH_ACCOUNT_DISABLED",
                403,
                {"detail": f"executor user state={executor_state or 'unknown'}"},
            )

        executor_user = await resolve_work_notice_user(db, executor_user) or executor_user

        if self._can_override_dispatch_scope(actor):
            return executor_user

        target_org_unit_ids = await self._resolve_dispatch_org_unit_ids(
            db,
            skill=skill,
            fallback_user=fallback_user,
        )
        if target_org_unit_ids:
            membership_exists = await db.scalar(
                select(func.count())
                .select_from(UserOrgMembership)
                .where(UserOrgMembership.user_id == executor_id)
                .where(UserOrgMembership.org_unit_id.in_(sorted(target_org_unit_ids)))
            ) or 0
            if membership_exists == 0:
                raise AppError(
                    "AUTH_DEPARTMENT_DENIED",
                    403,
                    {"detail": f"{executor_id} 不在组织架构部门 {sorted(target_org_unit_ids)}"},
                )
            return executor_user

        target_department = self._dispatch_target_department(skill, fallback_user)
        if target_department and executor_user.department != target_department:
            raise AppError(
                "AUTH_DEPARTMENT_DENIED",
                403,
                {"detail": f"{executor_id} 不在部门 {target_department}"},
            )
        return executor_user

    async def list_dispatch_assignable_users(
        self,
        db: AsyncSession,
        *,
        current_user: User,
        request_id: str,
    ) -> list[dict]:
        request = await db.get(DecisionRequest, request_id)
        if not request:
            raise AppError("TODO_NOT_FOUND", 404)
        skill = await db.get(Skill, request.skill_id)
        if not is_global_inbox_reader(current_user):
            visible_assignee_ids = await visible_inbox_assignee_ids_for_user(db, current_user)
            manager_count = await db.scalar(
                select(func.count(AITodo.id))
                .where(AITodo.request_id == request_id)
                .where(AITodo.assignee.in_(visible_assignee_ids))
            ) or 0
            if manager_count == 0:
                if skill is None:
                    raise AppError("AUTH_PERMISSION_DENIED", 403)
                if not require_department_access(skill.department, current_user):
                    raise AppError("AUTH_PERMISSION_DENIED", 403)

        target_org_unit_ids = await self._resolve_dispatch_org_unit_ids(
            db,
            skill=skill,
            fallback_user=current_user,
        )
        if self._can_override_dispatch_scope(current_user):
            stmt = select(User).where(self._active_user_filter())
        elif target_org_unit_ids:
            stmt = (
                select(User)
                .join(UserOrgMembership, UserOrgMembership.user_id == User.id)
                .where(UserOrgMembership.org_unit_id.in_(sorted(target_org_unit_ids)))
                .where(self._active_user_filter())
            )
        else:
            stmt = select(User).where(self._active_user_filter())
            target_department = self._dispatch_target_department(skill, current_user)
            if target_department:
                stmt = stmt.where(User.department == target_department)
        rows = (await db.execute(stmt.order_by(User.name.asc(), User.username.asc()))).scalars().all()
        result: list[dict] = []
        seen_user_ids: set[str] = set()
        for user in rows:
            canonical = await resolve_work_notice_user(db, user) or user
            if canonical.id in seen_user_ids:
                continue
            seen_user_ids.add(canonical.id)
            result.append({
                "id": canonical.id,
                "name": canonical.name,
                "role": canonical.role,
                "department": canonical.department,
                "dingtalk_bound": bool(canonical.dingtalk_user_id),
            })
        actor_default = await resolve_work_notice_user(db, current_user) or current_user
        if actor_default.id not in seen_user_ids:
            try:
                actor_default = await self._ensure_dispatch_executor(
                    db,
                    executor_id=actor_default.id,
                    skill=skill,
                    actor=current_user,
                    fallback_user=current_user,
                )
                result.insert(0, {
                    "id": actor_default.id,
                    "name": actor_default.name,
                    "role": actor_default.role,
                    "department": actor_default.department,
                    "dingtalk_bound": bool(actor_default.dingtalk_user_id),
                })
            except AppError:
                pass
        return result

    async def assign_dispatch_task(
        self,
        db: AsyncSession,
        *,
        task_id: int,
        executor_id: str,
        actor_id: str,
    ) -> dict:
        task = await db.get(TodoDispatchTask, task_id)
        if not task:
            raise AppError("TODO_NOT_FOUND", 404)
        request = await db.get(DecisionRequest, task.request_id)
        actor = await db.get(User, actor_id)
        if not request or not actor:
            raise AppError("AUTH_USER_NOT_FOUND", 404)

        manager_todo = await db.get(AITodo, task.manager_todo_id) if task.manager_todo_id else None
        if not is_global_inbox_reader(actor):
            visible_assignee_ids = await visible_inbox_assignee_ids_for_user(db, actor)
            if not manager_todo or manager_todo.assignee not in visible_assignee_ids:
                raise AppError("AUTH_PERMISSION_DENIED", 403)

        skill = await db.get(Skill, request.skill_id)
        executor_user = await self._ensure_dispatch_executor(
            db,
            executor_id=executor_id,
            skill=skill,
            actor=actor,
            fallback_user=actor,
        )

        if task.status not in {"awaiting_dispatch", "pending_assignment", "sent", "pushed_no_dingtalk"}:
            raise AppError("TODO_ALREADY_DECIDED", 409)

        now = now_bjt()
        task.executor = executor_user.id
        task.assigned_by = actor_id
        task.assigned_at = now
        task.updated_at = now

        if request.aggregate_decision == "approved":
            await self._dispatch_single_task(db, request, task, executor_user)
            await self._remember_dispatch_preference(
                db,
                request=request,
                actor_id=actor_id,
            )
        else:
            task.status = "awaiting_dispatch"
        await db.flush()
        return self._serialize_dispatch_task(task)

    async def update_dispatch_task_status(
        self,
        db: AsyncSession,
        *,
        task_id: int,
        actor_id: str,
        status: str,
        note: str = "",
    ) -> dict:
        task = await db.get(TodoDispatchTask, task_id)
        if not task:
            raise AppError("TODO_NOT_FOUND", 404)
        actor = await db.get(User, actor_id)
        if not actor:
            raise AppError("AUTH_USER_NOT_FOUND", 404)
        visible_executor_ids = await visible_inbox_assignee_ids_for_user(db, actor)
        if task.executor not in visible_executor_ids:
            raise AppError("AUTH_PERMISSION_DENIED", 403)

        allowed_targets = {
            "sent": {"in_progress", "blocked"},
            "pushed_no_dingtalk": {"in_progress", "blocked"},
            "in_progress": {"blocked"},
            "blocked": {"in_progress"},
        }
        current = task.status or ""
        if status not in allowed_targets.get(current, set()):
            raise AppError("PARAM_INVALID", 400, {
                "detail": f"非法状态流转: {current} -> {status}",
            })

        task.status = status
        # 记录开始时间（首次进入 in_progress）
        if status == "in_progress" and not task.started_at:
            task.started_at = now_bjt()
        task.ack_note = note or task.ack_note
        task.ack_channel = "web"
        task.updated_at = now_bjt()
        await db.flush()
        try:
            from app.learning.service import capture_todo_dispatch_task

            await capture_todo_dispatch_task(db, task)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "capture learning dispatch status failed (task_id={}): {}",
                task.id,
                e,
                exc_info=True,
            )
        return self._serialize_dispatch_task(task)

    @staticmethod
    def _extract_output_summary(output: dict | None):
        """从 Skill output 提取关键摘要字段。"""
        if not output or not isinstance(output, dict):
            return ""
        for key in ("summary", "conclusion", "decision", "result", "recommendation", "status"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:500]
        summary = {}
        for key in ("conclusion", "decision", "result", "level", "status", "score", "recommendation"):
            if key in output:
                summary[key] = output[key]
        if not summary:
            return ""
        return "；".join(f"{key}：{value}" for key, value in summary.items())[:500]

    @staticmethod
    def _extract_decision_reasoning(output: dict | None) -> list[str]:
        """从 Skill output 提取决策推理步骤。"""
        if not output or not isinstance(output, dict):
            return []
        reasoning = []
        # 常见的推理字段名
        for key in ("reasoning", "steps", "decision_chain", "explanation", "logic", "analysis_basis"):
            val = output.get(key)
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        text = item.get("basis") or item.get("reason") or item.get("text") or item.get("summary")
                        if text:
                            reasoning.append(str(text))
                        else:
                            reasoning.append(str(item))
                    else:
                        reasoning.append(str(item))
            elif isinstance(val, str) and val:
                reasoning.append(val)
        return reasoning

    async def _collect_dispatch_summary(self, db: AsyncSession, request_id: str) -> list[dict]:
        """收集 dispatch 子任务的完成摘要，用于 callback 回传给 Skill。"""
        stmt = (
            select(TodoDispatchTask)
            .where(TodoDispatchTask.request_id == request_id)
            .order_by(TodoDispatchTask.id)
        )
        tasks = (await db.execute(stmt)).scalars().all()
        return [
            {
                "executor": t.executor,
                "content": (t.content or "")[:200],
                "status": t.status,
                "ack_note": t.ack_note,
                "ack_at": isoformat_bjt(t.ack_at),
                "started_at": isoformat_bjt(t.started_at),
            }
            for t in tasks
        ]

    @staticmethod
    def _build_summary(decision: dict) -> str:
        output = decision.get("output_result") or {}
        if isinstance(output, dict):
            for key in ("summary", "message", "output"):
                value = output.get(key)
                if isinstance(value, str) and value.strip():
                    return value[:500]
            return str(output)[:500]
        return str(output)[:500]

    @staticmethod
    def _serialize_todo(todo: AITodo) -> dict:
        return {
            "id": todo.id,
            "request_id": todo.request_id,
            "kind": todo.kind,
            "assignee": todo.assignee,
            "status": todo.status,
            "decided_at": isoformat_bjt(todo.decided_at),
            "decided_by": todo.decided_by,
            "decision_reason": todo.decision_reason,
            "decision_channel": todo.decision_channel,
            "feedback_payload": todo.feedback_payload or None,
            "created_at": isoformat_bjt(todo.created_at),
            "updated_at": isoformat_bjt(todo.updated_at),
        }

    @staticmethod
    def _serialize_request(request: DecisionRequest) -> dict:
        return {
            "id": request.id,
            "kind": request.kind,
            "source_type": request.source_type,
            "source_id": request.source_id,
            "skill_id": request.skill_id,
            "run_id": request.run_id,
            "title": request.title,
            "summary": request.summary,
            "decision_mode": request.decision_mode,
            "aggregate_status": request.aggregate_status,
            "aggregate_decision": request.aggregate_decision,
            "sla_at": isoformat_bjt(request.sla_at),
            "created_at": isoformat_bjt(request.created_at),
            "completed_at": isoformat_bjt(request.completed_at),
            "callback_status": request.callback_status,
            "callback_attempts": request.callback_attempts,
            "callback_error": request.callback_error,
            "callback_completed_at": (
                isoformat_bjt(request.callback_completed_at)
            ),
            "has_callback": bool(request.callback_payload),
        }

    @staticmethod
    def _serialize_dispatch_task(task: TodoDispatchTask) -> dict:
        return {
            "id": task.id,
            "request_id": task.request_id,
            "manager_todo_id": task.manager_todo_id,
            "executor": task.executor,
            "executor_name": None,
            "executor_department": None,
            "default_executor": None,
            "default_executor_name": None,
            "default_executor_department": None,
            "executor_source": "assigned" if task.executor else "none",
            "dispatch_object_key": TodoService._dispatch_task_object_key(task, None),
            "content": task.content,
            "display_content": task.content,
            "deadline": isoformat_bjt(task.deadline),
            "extra": task.extra,
            "status": task.status,
            "assigned_by": task.assigned_by,
            "assigned_at": isoformat_bjt(task.assigned_at),
            "dingtalk_msg_id": task.dingtalk_msg_id,
            "dispatched_at": isoformat_bjt(task.dispatched_at),
            "ack_at": isoformat_bjt(task.ack_at),
            "ack_note": task.ack_note,
            "ack_channel": task.ack_channel,
            "created_at": isoformat_bjt(task.created_at),
            "updated_at": isoformat_bjt(task.updated_at),
        }

    def _serialize_list_item(
        self,
        todo: AITodo,
        request: DecisionRequest,
        *,
        skill: Skill | None = None,
        progress: dict | None = None,
        log_extra: dict | None = None,
        requester_map: dict[str, dict] | None = None,
        dispatch_completions: list[dict] | None = None,
        dispatch_default_summary: dict | None = None,
    ) -> dict:
        """序列化 list 项。旧签名 (todo, request) 仍兼容；新签名带四个可选预加载
        数据产生富字段（GAP-1）。所有新字段在缺省时用空值兜底，保持前后兼容。
        """
        base: dict = {
            **self._serialize_todo(todo),
            "title": request.title,
            "summary": request.summary,
            "skill_id": request.skill_id,
            "run_id": request.run_id,
            "decision_mode": request.decision_mode,
            "aggregate_status": request.aggregate_status,
            "aggregate_decision": request.aggregate_decision,
            "sla_at": isoformat_bjt(request.sla_at),
        }
        # GAP-1 富字段（缺预加载数据时用空值兜底，兼容旧调用方）
        base["suggested_actions"] = self._extract_suggested_actions(request.payload)
        completions = dispatch_completions or []
        base["dispatch_completions"] = completions
        base["dispatch_done_count"] = len(completions)
        base["latest_dispatch_completion"] = completions[0] if completions else None
        base["dispatch_default_summary"] = dispatch_default_summary
        metrics_preview: list[dict] = self._extract_payload_metrics(request.payload, limit=3)
        requester_id: str | None = None
        if isinstance(log_extra, dict):
            if not metrics_preview:
                metrics_preview = log_extra.get("metrics") or []
            requester_id = log_extra.get("requester_id")
        base["metrics_preview"] = metrics_preview
        # P3-契约 D3：Skill output.reports[0].primary_indicator 端到端贯通；
        # payload 里有就透出，前端缺省会 fallback 到 metrics_preview[0]。
        base["primary_indicator"] = self._extract_primary_indicator_from_payload(request.payload)
        payload = request.payload if isinstance(request.payload, dict) else {}
        base["priority"] = self._normalize_business_priority(payload.get("priority"))
        base["priority_amount"] = payload.get("priority_amount")
        base["priority_basis"] = payload.get("priority_basis")
        base["priority_basis_source"] = payload.get("priority_basis_source")
        finding_rows = self._iter_finding_rows(payload)
        first_finding = finding_rows[0] if finding_rows else {}
        low_video = first_finding.get("low_video") if isinstance(first_finding.get("low_video"), dict) else {}
        base["object_id"] = payload.get("object_id") or payload.get("item_id") or low_video.get("video_id")
        base["object_title"] = payload.get("object_title") or payload.get("item_title") or low_video.get("video_name")
        base["search_text"] = payload.get("search_text") or low_video.get("video_name")
        visitor_rank_metric = self._find_payload_metric(payload, {"全店访客排名"})
        order_rank_metric = self._find_payload_metric(payload, {"全店成交排名"})
        base["ranking_visitors"] = payload.get("ranking_visitors") or (
            visitor_rank_metric.get("value") if visitor_rank_metric else None
        )
        base["ranking_orders"] = payload.get("ranking_orders") or (
            order_rank_metric.get("value") if order_rank_metric else None
        )
        base["ranking_visitors_detail"] = visitor_rank_metric.get("delta") if visitor_rank_metric else None
        base["ranking_orders_detail"] = order_rank_metric.get("delta") if order_rank_metric else None
        payment_change_pct = self._extract_payment_change_pct(payload)
        base["payment_change_pct"] = payment_change_pct
        base["payment_change_text"] = self._format_payment_change(payment_change_pct)
        base["decline_coef"] = payment_change_pct if payment_change_pct is not None else payload.get("decline_coef")
        base["post_training_model_evaluation"] = post_training_evaluation_summary(
            payload.get(POST_TRAINING_EVALUATION_KEY)
        )
        base["approval_level"] = self._map_approval_level(
            getattr(skill, "approval_level", None) if skill is not None else None
        )
        requester_info = None
        requester_department = None
        if requester_id and isinstance(requester_map, dict):
            info = requester_map.get(requester_id)
            if info:
                requester_info = {"id": requester_id, "name": info.get("name")}
                requester_department = info.get("department")
            else:
                requester_info = {"id": requester_id, "name": None}
        base["requester"] = requester_info
        base["requester_department"] = requester_department
        base["aggregate_progress"] = progress if isinstance(progress, dict) else None
        return base

    @staticmethod
    def _serialize_skill(skill: Skill | None) -> dict | None:
        if not skill:
            return None
        return {
            "id": skill.id,
            "name": skill.name,
            "department": skill.department,
            "approval_level": skill.approval_level,
            "owner": skill.owner,
            "status": skill.status,
        }

    @staticmethod
    def _date_filters(date_from: str | None, date_to: str | None) -> list:
        filters = []
        if date_from:
            try:
                filters.append(AITodo.created_at >= TodoService._parse_date_filter_value(date_from))
            except ValueError:
                pass
        if date_to:
            try:
                upper = TodoService._parse_date_filter_value(date_to)
                if TodoService._is_date_picker_day_end(date_to, upper):
                    upper = upper.replace(hour=23, minute=59, second=59, microsecond=999999)
                filters.append(AITodo.created_at <= upper)
            except ValueError:
                pass
        return filters

    @staticmethod
    def _parse_date_filter_value(value: str) -> datetime:
        return parse_bjt_datetime(str(value))

    @staticmethod
    def _is_date_picker_day_end(raw: str, parsed: datetime) -> bool:
        text = str(raw).strip()
        date_only = "T" not in text and " " not in text
        midnight = (
            parsed.hour == 0
            and parsed.minute == 0
            and parsed.second == 0
            and parsed.microsecond == 0
        )
        return date_only or midnight


todo_service = TodoService()
