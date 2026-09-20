"""任务树投影服务。"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import Integer, case, cast, false, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.aiclaw.script_generator import BRIDGE_VERSION
from app.common.cache import cache_delete, cache_delete_pattern, cache_get, cache_set_adaptive as cache_set
from app.common.exceptions import AppError
from app.common.metrics import (
    tasktree_active_roots,
    tasktree_get_tree_p99_seconds,
    tasktree_slow_query_total,
)
from app.common.models import UsageLog
from app.config import settings
from app.execution.models import (
    DecisionLog,
    ExecutionRun,
    ExecutionStep,
    NodeScheduleConfig,
    OpenClawInstance,
    SkillSyncAttempt,
)
from app.org.models import OrgUnit
from app.skills.core.access import (
    get_skill_member,
    list_user_member_departments,
    list_user_member_scope,
)
from app.skills.core.models import Skill
from app.tasktree.metrics import (
    CACHE_HIT,
    CACHE_MISS,
    NODE_STATE_ERRORS,
    TASKTREE_VIRTUAL_GROUP_HITS,
)
from app.tasktree.models import TaskNodeLight
from app.common.time_utils import isoformat_bjt, now_bjt, parse_bjt_datetime
from app.learning.models import LearningArtifact, LearningFlowEdge
from app.tasktree.schemas import (
    DepartmentNode,
    FailureDiagnosisResponse,
    InstanceNode,
    InstanceSyncItem,
    NodeDetailResponse,
    NodeStatus,
    RunChainResponse,
    RunChainStep,
    SkillValueResponse,
    SkillRunItem,
    SkillRunStatus,
    TaskTreeDashboardResponse,
    TaskTreeResponse,
    TaskTreeStatsResponse,
    TrainingJobSummaryItem,
    WritebackStatus,
)
from app.todos.models import AITodo, DecisionRequest, TodoDispatchTask
from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment

VIRTUAL_AI_ID = "__virtual_ai__"
VIRTUAL_AI_NAME = "AI 组"
VIRTUAL_UNASSIGNED_ID = "__virtual_unassigned__"
VIRTUAL_UNASSIGNED_NAME = "平台主节点"
VIRTUAL_ORPHAN_ID = "__virtual_orphan__"
VIRTUAL_ORPHAN_NAME = "未归属节点"
ALL_LV1_SLOT = "__all_lv1__"
TASKTREE_WINDOW_SECONDS: dict[str, int] = {
    "1h": 3600,
    "24h": 24 * 3600,
    "7d": 7 * 24 * 3600,
}


def _version_tuple(value: str | None) -> tuple[int, ...]:
    text = str(value or "").strip().lstrip("v")
    parts: list[int] = []
    for part in text.split("."):
        if not part.isdigit():
            break
        parts.append(int(part))
    return tuple(parts)


def _bridge_update_available(current: str | None) -> bool:
    current_tuple = _version_tuple(current)
    latest_tuple = _version_tuple(BRIDGE_VERSION)
    return bool(current_tuple and latest_tuple and current_tuple < latest_tuple)


def _elapsed_seconds_since(value: datetime) -> float:
    """Handle legacy UTC-naive heartbeats and current BJT-naive heartbeats."""
    legacy_utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    ages = [
        (now_bjt() - value).total_seconds(),
        (legacy_utc_now - value).total_seconds(),
    ]
    non_negative = [age for age in ages if age >= 0]
    return min(non_negative) if non_negative else 0


def _instance_runtime_type(inst: OpenClawInstance) -> str:
    agent_type = (getattr(inst, "agent_type", "") or "").strip().lower()
    if agent_type == "hermes":
        return "hermes"
    gateway_kind = (getattr(inst, "bridge_gateway_kind", "") or "").strip().lower()
    if gateway_kind and gateway_kind != "unknown":
        return gateway_kind
    return agent_type or "aiclaw"


def _is_platform_machine(inst: OpenClawInstance) -> bool:
    instance_id = str(getattr(inst, "id", "") or "")
    return bool(
        getattr(inst, "is_platform_default", False)
        and (instance_id == "platform" or instance_id.startswith("platform-"))
    )


def _instance_machine_summary(inst: OpenClawInstance) -> dict[str, Any]:
    """Return a stable, presentation-safe hardware summary for task-tree cards."""
    try:
        capabilities = json.loads(getattr(inst, "bridge_capabilities_json", "") or "{}")
    except (TypeError, ValueError):
        capabilities = {}

    memory = capabilities.get("memory") if isinstance(capabilities.get("memory"), dict) else {}
    training = capabilities.get("training") if isinstance(capabilities.get("training"), dict) else {}
    media = capabilities.get("media") if isinstance(capabilities.get("media"), dict) else {}
    gpu_items = capabilities.get("gpu") if isinstance(capabilities.get("gpu"), list) else []
    gpu = gpu_items[0] if gpu_items and isinstance(gpu_items[0], dict) else {}
    workload_roles = capabilities.get("workload_roles")
    if not isinstance(workload_roles, list):
        workload_roles = media.get("workload_roles")
    if not isinstance(workload_roles, list):
        workload_roles = []
    normalized_roles = {str(item).strip().lower() for item in workload_roles if str(item).strip()}
    agent_purpose = str(getattr(inst, "agent_purpose", "") or "").strip().lower()
    is_media_node = agent_purpose == "media" or "video_generation" in normalized_roles
    supported_modes = media.get("supported_modes")
    if not isinstance(supported_modes, list):
        supported_modes = []
    media_available = bool(
        is_media_node
        and media.get("configured")
        and media.get("online") is not False
        and supported_modes
    )
    # A dedicated media node may expose generic training probes from its GPU,
    # but Task Tree must not present it as a training gateway. Dispatch enforces
    # the media-only purpose independently.
    training_available = bool(training.get("gateway")) and not is_media_node
    gpu_backend = str(gpu.get("backend") or "").strip().lower() or None

    if is_media_node:
        machine_role = "视频生成"
    elif training_available:
        machine_role = "训练 / 推理"
    elif gpu_backend in {"cuda", "metal"}:
        machine_role = "推理 / 调用"
    else:
        machine_role = "调度 / Skill"

    def mb_to_gb(value: Any) -> float | None:
        try:
            return round(float(value) / 1024.0, 1)
        except (TypeError, ValueError):
            return None

    try:
        memory_total_gb = round(float(memory.get("total_gb")), 1)
    except (TypeError, ValueError):
        memory_total_gb = None

    supported_tasks = training.get("supported_tasks")
    if not isinstance(supported_tasks, list):
        supported_tasks = []

    return {
        "is_platform_node": _is_platform_machine(inst),
        "machine_role": machine_role,
        "gpu_name": str(gpu.get("name") or "").strip() or None,
        "gpu_backend": gpu_backend,
        "gpu_unified_memory": bool(gpu.get("unified_memory")),
        "gpu_memory_total_gb": mb_to_gb(gpu.get("vram_total_mb")),
        "gpu_memory_used_gb": mb_to_gb(gpu.get("vram_used_mb")),
        "memory_total_gb": memory_total_gb,
        "training_available": training_available,
        "media_available": media_available,
        "supported_tasks": [str(item) for item in supported_tasks if str(item).strip()],
    }


def _run_duration_ms_expr():
    """Prefer node-reported runtime when bridge placeholder steps have 0ms duration."""
    return func.coalesce(
        func.nullif(ExecutionStep.duration_ms, 0),
        cast(func.jsonb_extract_path_text(ExecutionRun.metadata_json, "runtime", "duration_ms"), Integer),
        cast(func.jsonb_extract_path_text(ExecutionRun.metadata_json, "runtime", "script_duration_ms"), Integer),
        ExecutionStep.duration_ms,
    )


class TaskTreeProjection:
    """任务树读投影。"""

    async def get_tree(
        self,
        db: AsyncSession,
        *,
        department: str | None = None,
        user_department: str | None = None,
        current_user: User | None = None,
        is_admin: bool = False,
        status_filter: str | None = None,
    ) -> TaskTreeResponse:
        # P0-4：埋点端到端耗时 + 慢查询阈值（0.5s 视为慢）
        started = time.perf_counter()
        is_admin, user_department = _authoritative_identity(
            current_user,
            is_admin_hint=is_admin,
            user_department_hint=user_department,
        )
        member_skill_ids, member_departments = await self._get_member_scope(
            db,
            current_user=current_user,
            is_admin=is_admin,
        )
        user_lv1 = None
        accessible_departments: set[str] | None = None
        visible_department_names: set[str] | None = None
        try:
            lv1_by_id, lv1_by_name, lv1_names, org_by_name = await _load_org_indexes(db)
            requested_department = _normalize_requested_department(
                department,
                lv1_by_id=lv1_by_id,
                lv1_names=lv1_names,
                org_by_name=org_by_name,
            )
            if is_admin:
                if requested_department and requested_department not in {VIRTUAL_AI_ID, VIRTUAL_UNASSIGNED_ID, VIRTUAL_ORPHAN_ID}:
                    lv1_unit = lv1_by_name.get(requested_department)
                    if lv1_unit is not None:
                        accessible_departments = _collect_descendant_department_names(
                            lv1_unit,
                            org_by_name=org_by_name,
                        )
                        visible_department_names = {requested_department}
            else:
                user_lv1 = _resolve_user_lv1(
                    user_department,
                    lv1_by_id=lv1_by_id,
                    lv1_names=lv1_names,
                    org_by_name=org_by_name,
                )
                if user_lv1 is None:
                    if department:
                        self._observe_duration(started)
                        return _empty_tree_response()
                    # Users without a mapped department still need the shared
                    # platform pool. Private department nodes remain excluded
                    # because the resulting department scope is empty.
                    user_lv1 = VIRTUAL_UNASSIGNED_ID
                # C5a：跨部门 SkillMember 通道——把成员代管部门归并到 Lv1 名集合
                member_lv1_names: set[str] = set()
                for dept in member_departments or set():
                    normalized = _normalize_requested_department(
                        dept,
                        lv1_by_id=lv1_by_id,
                        lv1_names=lv1_names,
                        org_by_name=org_by_name,
                    )
                    if normalized:
                        member_lv1_names.add(normalized)
                allowed_lv1 = {user_lv1} | member_lv1_names
                # 若 router 已通过、但 service 收到非自身且非代管的部门 → 兜底降级
                if requested_department and requested_department not in allowed_lv1:
                    requested_department = user_lv1
                # C5b：AI 虚拟组用户没有真实 Lv1 单元，直接给虚拟组可见性
                if requested_department in {VIRTUAL_AI_ID, VIRTUAL_UNASSIGNED_ID, VIRTUAL_ORPHAN_ID}:
                    accessible_departments = {"AI"} if requested_department == VIRTUAL_AI_ID else set()
                    visible_department_names = {requested_department}
                elif user_lv1 in {VIRTUAL_AI_ID, VIRTUAL_UNASSIGNED_ID, VIRTUAL_ORPHAN_ID} and not requested_department:
                    accessible_departments = {"AI"} if user_lv1 == VIRTUAL_AI_ID else set()
                    visible_department_names = {user_lv1}
                else:
                    # requested_department 已经收口到 allowed_lv1 中的某个真实 Lv1 名
                    target_lv1 = requested_department if requested_department in lv1_by_name else user_lv1
                    lv1_unit = lv1_by_name[target_lv1]
                    accessible_departments = _collect_descendant_department_names(
                        lv1_unit,
                        org_by_name=org_by_name,
                    )
                    visible_department_names = {target_lv1}
        except Exception as exc:  # noqa: BLE001
            logger.debug("tasktree get_tree rollup indexes unavailable, fallback legacy scope: {}", exc)
            lv1_by_id, lv1_by_name, lv1_names, org_by_name = {}, {}, set(), {}
            requested_department = department
            accessible_departments = self._resolve_accessible_departments(
                user_department=user_department,
                requested_department=requested_department,
                member_departments=member_departments,
                is_admin=is_admin,
            )
            effective_department = _resolve_effective_department(
                user_department=user_department,
                requested_department=requested_department,
                allowed_departments=accessible_departments,
                is_admin=is_admin,
            )
            user_lv1 = effective_department if not is_admin else None
            visible_department_names = accessible_departments if not is_admin else None
        else:
            effective_department = requested_department if is_admin else user_lv1
        auth_scope_hash = _auth_scope_hash(
            current_user=current_user,
            member_departments=member_departments,
            member_skill_ids=member_skill_ids,
            is_admin=is_admin,
        )
        normalized_status = status_filter or "all"
        dept_slot = ALL_LV1_SLOT if is_admin and effective_department is None else (effective_department or "all")
        filters_hash = hashlib.sha1(normalized_status.encode("utf-8")).hexdigest()[:8]
        use_cache = not (is_admin and requested_department in {VIRTUAL_AI_ID, VIRTUAL_UNASSIGNED_ID, VIRTUAL_ORPHAN_ID})
        cache_key = f"tasktree:tree:{dept_slot}:{auth_scope_hash}:{filters_hash}"
        if use_cache:
            cached = await _safe_cache_get(cache_key)
            if cached is not None:
                self._observe_duration(started)
                return TaskTreeResponse(**cached)

        tree: TaskTreeResponse | None = None
        if settings.USE_TNL_READ:
            try:
                tree = await self._get_tree_from_tnl(
                    db,
                    accessible_departments=accessible_departments,
                    requested_department=requested_department,
                    user_department=user_department,
                    member_skill_ids=member_skill_ids,
                    is_admin=is_admin,
                    status_filter=normalized_status,
                    visible_department_names=visible_department_names,
                    lv1_by_id=lv1_by_id,
                    lv1_by_name=lv1_by_name,
                    lv1_names=lv1_names,
                    org_by_name=org_by_name,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("USE_TNL_READ 路径异常，降级到旧 JOIN 路径: {}", exc)
                tree = None

        if tree is None:
            org_units = await self._query_department_units(db, visible_department_names)
            instances = await self._query_instances(db, departments=accessible_departments)
            instance_syncs = await self._query_latest_sync_attempts(db, instances)
            instance_runs, dept_runs = await self._query_recent_runs(
                db,
                instances,
                user_department=user_department,
                member_skill_ids=member_skill_ids,
                is_admin=is_admin,
            )
            tree = self._assemble_tree(
                instances,
                dept_runs,
                department_units=org_units,
                instance_runs=instance_runs,
                instance_syncs=instance_syncs,
                status_filter=normalized_status,
                is_admin=is_admin,
                requested_department=requested_department,
                lv1_by_id=lv1_by_id,
                lv1_by_name=lv1_by_name,
                lv1_names=lv1_names,
                org_by_name=org_by_name,
            )

        try:
            tasktree_active_roots.labels(department=_safe_metric_dept(dept_slot)).set(tree.total_running)
        except Exception as e:
            logger.debug("tasktree_active_roots 指标上报失败 dept={}: {}", dept_slot, e)
        if use_cache:
            await _safe_cache_set(
                cache_key,
                tree.model_dump(mode="json"),
                ttl=settings.CACHE_TTL_TASKTREE,
            )
            await _safe_cache_set(
                _etag_cache_key(dept_slot, auth_scope_hash, normalized_status),
                {"etag": tree.etag},
                ttl=settings.CACHE_TTL_TASKTREE,
            )
        self._observe_duration(started)
        return tree

    @staticmethod
    def _observe_duration(started: float) -> None:
        elapsed = time.perf_counter() - started
        try:
            tasktree_get_tree_p99_seconds.observe(elapsed)
        except Exception as e:
            logger.debug("tasktree_get_tree_p99_seconds 指标上报失败: {}", e)
        if elapsed > 0.5:
            try:
                tasktree_slow_query_total.labels(operation="get_tree").inc()
            except Exception as e:
                logger.debug("tasktree_slow_query_total 指标上报失败: {}", e)

    async def _get_tree_from_tnl(
        self,
        db: AsyncSession,
        *,
        accessible_departments: set[str] | None,
        requested_department: str | None,
        user_department: str | None,
        member_skill_ids: set[str],
        is_admin: bool,
        status_filter: str,
        visible_department_names: set[str] | None,
        lv1_by_id: dict[str, OrgUnit],
        lv1_by_name: dict[str, OrgUnit],
        lv1_names: set[str],
        org_by_name: dict[str, OrgUnit],
    ) -> TaskTreeResponse:
        """P0-4：从 task_nodes_light 单表读任务树。

        - 用 ix_tnl_tree 索引 (department_id, root_id, parent_id, sort_key)
        - 只取 execution/run 类型节点 + 最近 N 条
        - 组装层级时复用旧 _assemble_tree（依赖 instance + dept_runs）
        """
        dept_filter = accessible_departments
        org_units = await self._query_department_units(db, visible_department_names)
        instances = await self._query_instances(db, departments=dept_filter)
        instance_syncs = await self._query_latest_sync_attempts(db, instances)

        if not instances:
            tree = self._assemble_tree(
                instances,
                {},
                department_units=org_units,
                instance_runs={},
                instance_syncs=instance_syncs,
                status_filter=status_filter,
                is_admin=is_admin,
                requested_department=requested_department,
                lv1_by_id=lv1_by_id,
                lv1_by_name=lv1_by_name,
                lv1_names=lv1_names,
                org_by_name=org_by_name,
            )
            return tree

        departments = sorted({inst.department for inst in instances if inst.department})
        instance_ids = [inst.id for inst in instances if inst.id]
        if not departments and not instance_ids:
            return self._assemble_tree(
                instances,
                {},
                department_units=org_units,
                instance_runs={},
                instance_syncs=instance_syncs,
                status_filter=status_filter,
                is_admin=is_admin,
                requested_department=requested_department,
                lv1_by_id=lv1_by_id,
                lv1_by_name=lv1_by_name,
                lv1_names=lv1_names,
                org_by_name=org_by_name,
            )

        # 主查询：按部门 + 状态过滤。
        # [M2] ORDER BY 改用 started_at DESC，命中新的部分索引 ix_tnl_main_read
        # (department_id, status, started_at DESC) WHERE source_run_id IS NOT NULL
        active_states = {"running", "queued", "stale", "completed", "failed", "timeout", "blocked"}
        stmt = (
            select(
                TaskNodeLight.source_run_id,
                TaskNodeLight.source_instance_id,
                TaskNodeLight.department_id,
                TaskNodeLight.title,
                TaskNodeLight.status,
                TaskNodeLight.started_at,
                TaskNodeLight.finished_at,
                TaskNodeLight.sort_key,
            )
            .where(TaskNodeLight.status.in_(active_states))
            .where(TaskNodeLight.node_type.in_(["execution", "run"]))
            .where(TaskNodeLight.source_run_id.is_not(None))
            .order_by(
                TaskNodeLight.started_at.desc().nullslast(),
                TaskNodeLight.created_at.desc(),
            )
            .limit(max(1, len(instances)) * settings.TASKTREE_MAX_RECENT_RUNS * 2)
        )
        scope_clauses = []
        if departments:
            scope_clauses.append(TaskNodeLight.department_id.in_(departments))
        if instance_ids:
            scope_clauses.append(TaskNodeLight.source_instance_id.in_(instance_ids))
        if scope_clauses:
            stmt = stmt.where(or_(*scope_clauses))
        rows = (await db.execute(stmt)).all()

        # 补齐 run meta（skill / trigger_type / duration / error）
        run_ids = [row.source_run_id for row in rows if row.source_run_id]
        meta_by_run: dict[str, Any] = {}
        if run_ids:
            meta_stmt = (
                select(
                    ExecutionRun.id.label("run_id"),
                    ExecutionRun.status.label("run_status"),
                    ExecutionRun.started_at,
                    ExecutionRun.completed_at,
                    ExecutionRun.summary,
                    ExecutionRun.trigger_type,
                    func.coalesce(ExecutionStep.skill_id, ExecutionRun.skill_id).label("skill_id"),
                    ExecutionStep.input_data,
                    _run_duration_ms_expr().label("duration_ms"),
                    ExecutionStep.error_message,
                    DecisionLog.suggested_action,
                    Skill.name.label("skill_name"),
                    Skill.department.label("skill_department"),
                )
                .select_from(ExecutionRun)
                .outerjoin(ExecutionStep, ExecutionStep.run_id == ExecutionRun.id)
                .outerjoin(Skill, Skill.id == func.coalesce(ExecutionStep.skill_id, ExecutionRun.skill_id))
                .outerjoin(DecisionLog, DecisionLog.run_id == ExecutionRun.id)
                .where(ExecutionRun.id.in_(run_ids))
                .order_by(ExecutionRun.started_at.desc(), ExecutionStep.step_order.asc().nullsfirst())
            )
            meta_rows = (await db.execute(meta_stmt)).all()
            for meta_row in meta_rows:
                meta_by_run.setdefault(meta_row.run_id, meta_row)

        writebacks = await self._query_writebacks(db, run_ids)
        instance_runs: dict[str, list[SkillRunItem]] = {}
        dept_runs: dict[str, list[SkillRunItem]] = {}
        for row in rows:
            department = row.department_id or ""
            meta = meta_by_run.get(row.source_run_id)
            skill_id = (meta.skill_id if meta else row.source_run_id) or row.source_run_id
            if not is_admin and department != user_department and skill_id not in member_skill_ids:
                continue
            writeback = writebacks.get(row.source_run_id, {})
            item = _build_skill_run_item(
                run_id=row.source_run_id,
                skill_id=skill_id,
                skill_name=(meta.skill_name if meta and meta.skill_name else row.title) or row.source_run_id,
                run_status=(meta.run_status if meta else row.status),
                trigger_type=meta.trigger_type if meta else None,
                started_at=(meta.started_at if meta else row.started_at),
                completed_at=(meta.completed_at if meta else row.finished_at),
                duration_ms=meta.duration_ms if meta else None,
                input_data=meta.input_data if meta else None,
                output_summary=_summarize_run_output(
                    meta.summary if meta else None,
                    meta.suggested_action if meta else None,
                ),
                error_message=meta.error_message if meta else None,
                writeback_status=writeback.get("status"),
                writeback_detail=writeback.get("detail"),
            )
            dept_list = dept_runs.setdefault(department, [])
            if len(dept_list) < settings.TASKTREE_MAX_RECENT_RUNS:
                dept_list.append(item)
            if row.source_instance_id:
                inst_list = instance_runs.setdefault(row.source_instance_id, [])
                if len(inst_list) < settings.TASKTREE_MAX_RECENT_RUNS:
                    inst_list.append(item)

        return self._assemble_tree(
            instances,
            dept_runs,
            department_units=org_units,
            instance_runs=instance_runs,
            instance_syncs=instance_syncs,
            status_filter=status_filter,
            is_admin=is_admin,
            requested_department=requested_department,
            lv1_by_id=lv1_by_id,
            lv1_by_name=lv1_by_name,
            lv1_names=lv1_names,
            org_by_name=org_by_name,
        )

    async def get_node_detail(
        self,
        db: AsyncSession,
        instance_id: str,
        *,
        current_user: User,
    ) -> NodeDetailResponse:
        cache_key = f"tasktree:node:{instance_id}"
        cached = await _safe_cache_get(cache_key)
        if cached is not None:
            result = NodeDetailResponse(**cached)
            await self._ensure_instance_access(db, result.department, current_user)
            return result

        inst = await db.get(OpenClawInstance, instance_id)
        if not inst:
            raise AppError("NOT_FOUND", 404)
        await self._ensure_instance_access(db, inst.department, current_user)
        member_skill_ids, _member_departments = await self._get_member_scope(
            db,
            current_user=current_user,
            is_admin=_is_global_viewer(current_user),
        )

        status = _compute_node_status(inst)
        active = await self._query_runs_for_instance(
            db,
            inst,
            statuses=["running", "queued"],
            user_department=current_user.department,
            member_skill_ids=member_skill_ids,
            is_admin=_is_global_viewer(current_user),
        )
        recent = await self._query_runs_for_instance(
            db,
            inst,
            statuses=["completed", "failed", "timeout", "blocked"],
            limit=10,
            user_department=current_user.department,
            member_skill_ids=member_skill_ids,
            is_admin=_is_global_viewer(current_user),
        )
        latest_sync = (await self._query_latest_sync_attempts(db, [inst])).get(inst.id)

        result = NodeDetailResponse(
            instance_id=inst.id,
            name=inst.name,
            department=inst.department,
            agent_type=inst.agent_type,
            runtime_type=_instance_runtime_type(inst),
            bridge_gateway_kind=getattr(inst, "bridge_gateway_kind", None),
            bridge_gateway_version=getattr(inst, "bridge_gateway_version", None),
            node_status=status,
            last_heartbeat_at=inst.last_heartbeat,
            capacity=_default_instance_capacity(inst),
            bridge_version=getattr(inst, "bridge_version", None),
            bridge_latest_version=BRIDGE_VERSION,
            bridge_update_available=_bridge_update_available(getattr(inst, "bridge_version", None)),
            bridge_platform=getattr(inst, "bridge_platform", None),
            bridge_skills_dir=getattr(inst, "bridge_skills_dir", None),
            last_sync_at=getattr(inst, "last_sync_at", None),
            last_sync_ok=getattr(inst, "last_sync_ok", None),
            latest_sync=latest_sync,
            heartbeat_history=await self._query_heartbeat_history(db, inst),
            active_runs=active,
            recent_completed=recent,
        )
        await _safe_cache_set(
            cache_key,
            result.model_dump(mode="json"),
            ttl=settings.CACHE_TTL_TASKTREE,
        )
        return result

    async def get_run_chain(
        self,
        db: AsyncSession,
        run_id: str,
        *,
        current_user: User,
    ) -> RunChainResponse:
        cache_key = f"tasktree:chain:v3:{run_id}"
        cached = await _safe_cache_get(cache_key)
        if cached is not None:
            result = RunChainResponse(**cached)
            if result.skill_id:
                await self._ensure_run_access(db, result.skill_id, current_user)
            return result

        run = await db.get(ExecutionRun, run_id)
        if not run:
            raise AppError("NOT_FOUND", 404)

        step_result = await db.execute(
            select(ExecutionStep.skill_id, Skill.name, Skill.department)
            .select_from(ExecutionStep)
            .outerjoin(Skill, Skill.id == ExecutionStep.skill_id)
            .where(ExecutionStep.run_id == run_id)
            .order_by(ExecutionStep.step_order.asc().nullsfirst(), ExecutionStep.id.asc())
            .limit(1)
        )
        step_row = step_result.first()
        skill_id = step_row[0] if step_row else (run.skill_id or "")
        skill_name = step_row[1] if step_row else None
        skill_department = step_row[2] if step_row else None
        if skill_id and skill_name is None:
            skill = await db.get(Skill, skill_id)
            if skill:
                skill_name = skill.name
                skill_department = skill.department
        await self._ensure_instance_access(db, skill_department, current_user)

        chain: list[RunChainStep] = [
            RunChainStep(
                type="execution",
                id=run.id,
                status=run.status,
                title=run.summary or skill_name or skill_id or f"执行 {run.id}",
                created_at=run.started_at,
            )
        ]

        dl_result = await db.execute(
            select(DecisionLog)
            .where(DecisionLog.run_id == run_id)
            .order_by(DecisionLog.created_at.desc())
            .limit(1)
        )
        decision_log = dl_result.scalar_one_or_none()
        if decision_log:
            chain.append(
                RunChainStep(
                    type="decision",
                    id=decision_log.id,
                    status=decision_log.approval_status or "auto",
                    title=f"L{decision_log.approval_level or 0} 决策",
                    created_at=decision_log.created_at,
                )
            )

        dr_result = await db.execute(
            select(DecisionRequest)
            .where(DecisionRequest.run_id == run_id)
            .order_by(DecisionRequest.created_at.asc())
            .limit(1)
        )
        request = dr_result.scalar_one_or_none()
        chain_complete = False
        todos_payload: list[dict[str, Any]] = []
        dispatch_payload: list[dict[str, Any]] = []

        if request:
            request_status = request.aggregate_decision or request.aggregate_status
            chain.append(
                RunChainStep(
                    type="request",
                    id=request.id,
                    status=request_status,
                    title=request.title,
                    created_at=request.created_at,
                )
            )

            todos_result = await db.execute(
                select(AITodo)
                .where(AITodo.request_id == request.id)
                .order_by(AITodo.created_at.asc(), AITodo.id.asc())
            )
            todos = list(todos_result.scalars().all())
            for todo in todos:
                todos_payload.append(
                    {
                        "id": todo.id,
                        "request_id": todo.request_id,
                        "kind": todo.kind,
                        "assignee": todo.assignee,
                        "status": todo.status,
                        "decided_at": todo.decided_at,
                        "decided_by": todo.decided_by,
                        "created_at": todo.created_at,
                    }
                )
                chain.append(
                    RunChainStep(
                        type="todo",
                        id=todo.id,
                        status=todo.status,
                        title=f"{todo.kind} → {todo.assignee}",
                        assignee=todo.assignee,
                        decided_at=todo.decided_at,
                        created_at=todo.created_at,
                    )
                )

            dispatch_result = await db.execute(
                select(TodoDispatchTask)
                .where(TodoDispatchTask.request_id == request.id)
                .order_by(TodoDispatchTask.created_at.asc(), TodoDispatchTask.id.asc())
            )
            dispatch_tasks = list(dispatch_result.scalars().all())
            for task in dispatch_tasks:
                dispatch_payload.append(
                    {
                        "id": task.id,
                        "request_id": task.request_id,
                        "executor": task.executor,
                        "content": task.content,
                        "status": task.status,
                        "assigned_at": task.assigned_at,
                        "dispatched_at": task.dispatched_at,
                        "ack_at": task.ack_at,
                        "created_at": task.created_at,
                    }
                )
                chain.append(
                    RunChainStep(
                        type="dispatch",
                        id=task.id,
                        status=task.status,
                        title=(task.content or "派发任务")[:80],
                        assignee=task.executor,
                        created_at=task.created_at,
                    )
                )

            if dispatch_tasks:
                chain_complete = all(task.status in {"done", "cancelled"} for task in dispatch_tasks)
            else:
                chain_complete = request.aggregate_status == "completed"

        training_jobs = await self._query_run_training_jobs(db, run_id, skill_id=skill_id)
        result = RunChainResponse(
            run_id=run_id,
            skill_id=skill_id,
            skill_name=skill_name,
            execution={
                "id": run.id,
                "status": run.status,
                "trigger_type": run.trigger_type,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "summary": run.summary,
            },
            decision={
                "id": decision_log.id,
                "approval_level": decision_log.approval_level,
                "approval_status": decision_log.approval_status,
                "created_at": decision_log.created_at,
                "suggested_action": decision_log.suggested_action,
            } if decision_log else None,
            request={
                "id": request.id,
                "title": request.title,
                "kind": request.kind,
                "aggregate_status": request.aggregate_status,
                "aggregate_decision": request.aggregate_decision,
                "callback_status": request.callback_status,
                "created_at": request.created_at,
                "completed_at": request.completed_at,
            } if request else None,
            todos=todos_payload,
            dispatch_tasks=dispatch_payload,
            training_jobs=training_jobs,
            chain=chain,
            chain_complete=chain_complete,
        )
        chain_ttl = 15 if _has_active_training_summary(training_jobs) else (300 if chain_complete else 30)
        await _safe_cache_set(
            cache_key,
            result.model_dump(mode="json"),
            ttl=chain_ttl,
        )
        return result

    async def _query_run_training_jobs(
        self,
        db: AsyncSession,
        run_id: str,
        *,
        skill_id: str | None,
    ) -> list[TrainingJobSummaryItem]:
        artifact_rows = list(
            (
                await db.execute(
                    select(LearningArtifact)
                    .where(LearningArtifact.run_id == run_id)
                    .where(LearningArtifact.artifact_kind.in_(["training_sample", "eval_case"]))
                    .where(LearningArtifact.status == "materialized")
                    .order_by(LearningArtifact.created_at.desc(), LearningArtifact.id.asc())
                    .limit(200)
                )
            ).scalars().all()
        )
        artifact_ids = [row.id for row in artifact_rows if row.id]
        if not artifact_ids:
            return []

        edge_rows = list(
            (
                await db.execute(
                    select(LearningFlowEdge)
                    .where(LearningFlowEdge.from_type == "learning_artifact")
                    .where(LearningFlowEdge.from_id.in_(artifact_ids))
                    .where(LearningFlowEdge.to_type == "training_job")
                    .where(LearningFlowEdge.relation == "trained_from")
                    .order_by(LearningFlowEdge.created_at.desc(), LearningFlowEdge.id.desc())
                    .limit(200)
                )
            ).scalars().all()
        )
        run_edge_rows = list(
            (
                await db.execute(
                    select(LearningFlowEdge)
                    .where(LearningFlowEdge.from_type == "run")
                    .where(LearningFlowEdge.from_id == run_id)
                    .where(LearningFlowEdge.to_type == "training_job")
                    .where(LearningFlowEdge.relation == "trained_from")
                    .order_by(LearningFlowEdge.created_at.desc(), LearningFlowEdge.id.desc())
                    .limit(50)
                )
            ).scalars().all()
        )
        job_ids = _ordered_unique([str(row.to_id) for row in [*run_edge_rows, *edge_rows] if row.to_id])
        training_items: list[TrainingJobSummaryItem] = []
        if job_ids:
            job_rows = list(
                (
                    await db.execute(
                        select(TrainingJob)
                        .where(TrainingJob.id.in_(job_ids))
                        .order_by(TrainingJob.created_at.desc(), TrainingJob.id.desc())
                    )
                ).scalars().all()
            )
            tasks_by_job = await self._latest_training_tasks_by_job(db, job_ids)
            deployments_by_job = await self._latest_training_deployments_by_job(db, job_ids)
            sample_count_by_job: dict[str, int] = {job_id: 0 for job_id in job_ids}
            for edge in edge_rows:
                if edge.to_id in sample_count_by_job:
                    sample_count_by_job[str(edge.to_id)] += 1
            hidden_job_ids: set[str] = set()
            for job in job_rows:
                latest_task = tasks_by_job.get(job.id)
                deployment = deployments_by_job.get(job.id)
                if _should_hide_legacy_training_candidate(job, latest_task, deployment):
                    hidden_job_ids.add(job.id)
                    continue
                training_items.append(
                    _training_job_summary_item(
                        job,
                        source_run_id=run_id,
                        source_sample_count=sample_count_by_job.get(job.id, 0),
                        latest_task=latest_task,
                        deployment=deployment,
                    )
                )
        else:
            hidden_job_ids = set()

        covered_artifact_ids = {
            str(row.from_id)
            for row in edge_rows
            if row.from_id and str(row.to_id or "") not in hidden_job_ids
        }
        pending_artifacts = [row for row in artifact_rows if row.id not in covered_artifact_ids]
        if pending_artifacts:
            training_items.insert(
                0,
                _pending_daily_training_summary_item(
                    pending_artifacts,
                    source_run_id=run_id,
                    skill_id=skill_id,
                ),
            )
        return training_items

    async def _latest_training_tasks_by_job(
        self,
        db: AsyncSession,
        job_ids: list[str],
    ) -> dict[str, TrainingJobTask]:
        if not job_ids:
            return {}
        rows = list(
            (
                await db.execute(
                    select(TrainingJobTask)
                    .where(TrainingJobTask.job_id.in_(job_ids))
                    .order_by(
                        TrainingJobTask.job_id.asc(),
                        TrainingJobTask.updated_at.desc(),
                        TrainingJobTask.id.desc(),
                    )
                )
            ).scalars().all()
        )
        by_job: dict[str, TrainingJobTask] = {}
        for row in rows:
            by_job.setdefault(row.job_id, row)
        return by_job

    async def _latest_training_deployments_by_job(
        self,
        db: AsyncSession,
        job_ids: list[str],
    ) -> dict[str, TrainingModelDeployment]:
        if not job_ids:
            return {}
        rows = list(
            (
                await db.execute(
                    select(TrainingModelDeployment)
                    .where(TrainingModelDeployment.job_id.in_(job_ids))
                    .order_by(
                        TrainingModelDeployment.job_id.asc(),
                        TrainingModelDeployment.updated_at.desc(),
                        TrainingModelDeployment.created_at.desc(),
                    )
                )
            ).scalars().all()
        )
        by_job: dict[str, TrainingModelDeployment] = {}
        for row in rows:
            by_job.setdefault(row.job_id, row)
        return by_job

    async def get_stats(
        self,
        db: AsyncSession,
        *,
        department: str | None = None,
        window: str = "24h",
        user_department: str | None = None,
        current_user: User | None = None,
        is_admin: bool = False,
    ) -> TaskTreeStatsResponse:
        is_admin, user_department = _authoritative_identity(
            current_user,
            is_admin_hint=is_admin,
            user_department_hint=user_department,
        )
        member_skill_ids, member_departments = await self._get_member_scope(
            db,
            current_user=current_user,
            is_admin=is_admin,
        )
        try:
            lv1_by_id, lv1_by_name, lv1_names, org_by_name = await _load_org_indexes(db)
            requested_department = _normalize_requested_department(
                department,
                lv1_by_id=lv1_by_id,
                lv1_names=lv1_names,
                org_by_name=org_by_name,
            )
            user_lv1 = None if is_admin else _resolve_user_lv1(
                user_department,
                lv1_by_id=lv1_by_id,
                lv1_names=lv1_names,
                org_by_name=org_by_name,
            )
            if not is_admin and user_lv1 is None:
                return TaskTreeStatsResponse()
            if not is_admin and requested_department and requested_department != user_lv1:
                requested_department = user_lv1
        except Exception as exc:  # noqa: BLE001
            logger.debug("tasktree get_stats rollup indexes unavailable, fallback legacy scope: {}", exc)
            lv1_by_id, lv1_by_name, lv1_names, org_by_name = {}, {}, set(), {}
            requested_department = department
            user_lv1 = _resolve_effective_department(
                user_department=user_department,
                requested_department=requested_department,
                allowed_departments=self._resolve_accessible_departments(
                    user_department=user_department,
                    requested_department=requested_department,
                    member_departments=member_departments,
                    is_admin=is_admin,
                ),
                is_admin=is_admin,
            ) if not is_admin else None
            # H12：legacy 分支非 admin 解不出 user_lv1 必须 403，否则下面 dept_slot
            # 兜底成 "all" 时会命中 admin 全局缓存（跨权限串读）
            if not is_admin and user_lv1 is None:
                raise AppError("AUTH_DEPARTMENT_DENIED", 403)

        effective_department = requested_department if is_admin else user_lv1
        auth_scope_hash = _auth_scope_hash(
            current_user=current_user,
            member_departments=member_departments,
            member_skill_ids=member_skill_ids,
            is_admin=is_admin,
        )
        # H12：dept_slot 必须按身份维度独立——非 admin 且 effective_department 仍为空时
        # 落到 user 维度桶，避免任何路径把非 admin user 串到 "all" 全局缓存键上
        if is_admin and effective_department is None:
            dept_slot = ALL_LV1_SLOT
        elif effective_department:
            dept_slot = effective_department
        else:
            user_id_for_slot = getattr(current_user, "id", None) or "anon"
            dept_slot = f"user_{user_id_for_slot}"
        cache_key = f"tasktree:stats:{dept_slot}:{auth_scope_hash}:{window}"
        cached = await _safe_cache_get(cache_key)
        if cached is not None:
            return TaskTreeStatsResponse(**cached)

        now = now_bjt()
        since = now - timedelta(seconds=_window_seconds(window))
        tree = await self.get_tree(
            db,
            department=department,
            user_department=user_department,
            current_user=current_user,
            is_admin=is_admin,
            status_filter="all",
        )
        windowed_runs = _tree_runs_in_window(tree, now=now, window=window)
        visible_instance_ids = [
            instance.instance_id
            for tree_department in tree.departments
            for instance in tree_department.instances
        ]
        finished_runs = [
            run for run in windowed_runs
            if run.status in {SkillRunStatus.COMPLETED, SkillRunStatus.FAILED}
        ]
        today_executions = len(windowed_runs)
        today_finished = len(finished_runs)
        today_failed = sum(1 for run in finished_runs if run.status == SkillRunStatus.FAILED)
        success_count = sum(1 for run in finished_runs if run.status == SkillRunStatus.COMPLETED)
        # v2.0.16 H1：success_rate 在无完成样本时返回 None，避免前端显示 "0%" 误导；
        # 分母保持 finished_runs（完成率口径），并新增 today_finished 字段让前端能展示
        # "N 个运行中 / K 个已完成 / x% 成功率"，不再把"1h 窗口内多数 RUNNING"误算成 100% 失败
        success_rate: float | None = (
            round(success_count / today_finished, 3) if today_finished > 0 else None
        )

        metrics_departments: set[str] | None = None
        if effective_department and effective_department not in {VIRTUAL_AI_ID, VIRTUAL_UNASSIGNED_ID, VIRTUAL_ORPHAN_ID}:
            lv1_unit = lv1_by_name.get(effective_department)
            if lv1_unit is not None:
                metrics_departments = _collect_descendant_department_names(
                    lv1_unit,
                    org_by_name=org_by_name,
                )
        elif effective_department == VIRTUAL_AI_ID:
            # C5b：admin 跨查 AI 虚拟组、AI 用户访问自己部门 → 都按 'AI' 部门统计
            metrics_departments = {"AI"}

        saved_hours = await self._compute_saved_hours(
            db,
            department=None,
            departments=metrics_departments,
            since=since,
        )
        token_cost_today = await self._compute_token_cost(
            db,
            department=None,
            departments=metrics_departments,
            since=since,
        )
        human_takeover_rate = await self._compute_human_takeover_rate(
            db,
            department=None,
            departments=metrics_departments,
            since=since,
        )
        scheduled_due_soon = await self._count_schedules_due_soon(
            db,
            instance_ids=visible_instance_ids,
            now=now,
        )

        result = TaskTreeStatsResponse(
            online_nodes=tree.total_online,
            total_nodes=tree.total_online + tree.total_offline,
            today_executions=today_executions,
            today_finished=today_finished,
            today_success_rate=success_rate,
            today_failed=today_failed,
            saved_hours=saved_hours,
            token_cost_today=token_cost_today,
            human_takeover_rate=human_takeover_rate,
            department_count=len(tree.departments),
            instance_count=sum(len(department.instances) for department in tree.departments),
            running_count=tree.total_running,
            scheduled_due_soon=scheduled_due_soon,
        )
        await _safe_cache_set(
            cache_key,
            result.model_dump(mode="json"),
            ttl=settings.CACHE_TTL_TASKTREE,
        )
        return result

    async def _count_schedules_due_soon(
        self,
        db: AsyncSession,
        *,
        instance_ids: list[str],
        now: datetime,
    ) -> int:
        """Count active node schedules whose next local fire is within 1 hour."""
        if not instance_ids:
            return 0
        rows = (await db.execute(
            select(NodeScheduleConfig.cron_expression)
            .where(NodeScheduleConfig.instance_id.in_(instance_ids))
            .where(NodeScheduleConfig.ack_ok.is_(True))
        )).scalars().all()
        if not rows:
            return 0

        scheduler_tz = ZoneInfo(settings.SCHEDULER_TIMEZONE)
        current = now.replace(tzinfo=scheduler_tz) if now.tzinfo is None else now.astimezone(scheduler_tz)
        deadline = current + timedelta(hours=1)
        count = 0
        for cron_expression in rows:
            expression = str(cron_expression or "").strip()
            if not expression:
                continue
            try:
                trigger = CronTrigger.from_crontab(expression, timezone=scheduler_tz)
                next_fire = trigger.get_next_fire_time(None, current)
            except Exception as exc:  # noqa: BLE001
                logger.debug("tasktree skip invalid node schedule cron={} error={}", expression, exc)
                continue
            if next_fire is not None and next_fire <= deadline:
                count += 1
        return count

    async def get_dashboard(
        self,
        db: AsyncSession,
        *,
        department: str | None = None,
        period_days: int = 30,
        user_department: str | None = None,
        current_user: User | None = None,
        is_admin: bool = False,
    ) -> TaskTreeDashboardResponse:
        is_admin, user_department = _authoritative_identity(
            current_user,
            is_admin_hint=is_admin,
            user_department_hint=user_department,
        )
        member_skill_ids, member_departments = await self._get_member_scope(
            db,
            current_user=current_user,
            is_admin=is_admin,
        )
        try:
            lv1_by_id, lv1_by_name, lv1_names, org_by_name = await _load_org_indexes(db)
            requested_department = _normalize_requested_department(
                department,
                lv1_by_id=lv1_by_id,
                lv1_names=lv1_names,
                org_by_name=org_by_name,
            )
            user_lv1 = None if is_admin else _resolve_user_lv1(
                user_department,
                lv1_by_id=lv1_by_id,
                lv1_names=lv1_names,
                org_by_name=org_by_name,
            )
            if not is_admin and user_lv1 is None:
                return TaskTreeDashboardResponse()
            if not is_admin and requested_department and requested_department != user_lv1:
                requested_department = user_lv1
        except Exception as exc:  # noqa: BLE001
            logger.debug("tasktree get_dashboard rollup indexes unavailable, fallback legacy scope: {}", exc)
            lv1_by_id, lv1_by_name, lv1_names, org_by_name = {}, {}, set(), {}
            requested_department = department
            user_lv1 = _resolve_effective_department(
                user_department=user_department,
                requested_department=requested_department,
                allowed_departments=self._resolve_accessible_departments(
                    user_department=user_department,
                    requested_department=requested_department,
                    member_departments=member_departments,
                    is_admin=is_admin,
                ),
                is_admin=is_admin,
            ) if not is_admin else None

        effective_department = requested_department if is_admin else user_lv1
        auth_scope_hash = _auth_scope_hash(
            current_user=current_user,
            member_departments=member_departments,
            member_skill_ids=member_skill_ids,
            is_admin=is_admin,
        )
        since = now_bjt() - timedelta(days=period_days)
        dept_slot = ALL_LV1_SLOT if is_admin and effective_department is None else (effective_department or "all")
        cache_key = f"tasktree:dashboard:{dept_slot}:{period_days}:{auth_scope_hash}"
        cached = await _safe_cache_get(cache_key)
        if cached is not None:
            return TaskTreeDashboardResponse(**cached)

        metrics_departments: set[str] | None = None
        if effective_department and effective_department not in {VIRTUAL_AI_ID, VIRTUAL_UNASSIGNED_ID, VIRTUAL_ORPHAN_ID}:
            lv1_unit = lv1_by_name.get(effective_department)
            if lv1_unit is not None:
                metrics_departments = _collect_descendant_department_names(
                    lv1_unit,
                    org_by_name=org_by_name,
                )
        elif effective_department == VIRTUAL_AI_ID:
            # C5b：admin 跨查 AI 虚拟组、AI 用户访问自己部门 → 都按 'AI' 部门统计
            metrics_departments = {"AI"}

        executions = await self._count_runs(
            db,
            department=None,
            departments=metrics_departments,
            since=since,
        )
        saved_hours = await self._compute_saved_hours(
            db,
            department=None,
            departments=metrics_departments,
            since=since,
        )
        token_cost = await self._compute_token_cost(
            db,
            department=None,
            departments=metrics_departments,
            since=since,
        )
        top_skills = await self._top_skills(
            db,
            department=None,
            departments=metrics_departments,
            since=since,
        )
        estimated_cost_saving = round(saved_hours * 50.0, 2)
        roi = round(estimated_cost_saving / token_cost, 2) if token_cost > 0 else 0.0

        result = TaskTreeDashboardResponse(
            department=effective_department,
            period_days=period_days,
            executions=executions,
            saved_hours=saved_hours,
            token_cost=token_cost,
            roi=roi,
            top_skills=top_skills,
        )
        await _safe_cache_set(
            cache_key,
            result.model_dump(mode="json"),
            ttl=settings.CACHE_TTL_TASKTREE,
        )
        return result

    async def _count_runs(
        self,
        db: AsyncSession,
        *,
        department: str | None,
        departments: set[str] | None = None,
        since: datetime,
        statuses: list[str] | None = None,
    ) -> int:
        department_filter = departments or ({department} if department else None)
        stmt = select(func.count(func.distinct(ExecutionRun.id))).where(ExecutionRun.started_at >= since)
        if statuses:
            stmt = stmt.where(ExecutionRun.status.in_(statuses))
        if department_filter:
            stmt = (
                stmt.select_from(ExecutionRun)
                .join(ExecutionStep, ExecutionStep.run_id == ExecutionRun.id)
                .join(Skill, Skill.id == ExecutionStep.skill_id)
                .where(Skill.department.in_(sorted(department_filter)))
            )
        return int((await db.scalar(stmt)) or 0)

    async def _compute_saved_hours(
        self,
        db: AsyncSession,
        *,
        department: str | None,
        departments: set[str] | None = None,
        since: datetime,
    ) -> float:
        department_filter = departments or ({department} if department else None)
        manual_baseline_col = getattr(ExecutionRun, "manual_baseline_minutes", None)
        skill_baseline_col = getattr(Skill, "default_baseline_minutes", None)

        if manual_baseline_col is None or skill_baseline_col is None:
            completed_runs = await self._count_runs(
                db,
                department=department,
                departments=department_filter,
                since=since,
                statuses=["completed"],
            )
            return round((completed_runs * settings.TASKTREE_DEFAULT_BASELINE) / 60.0, 1)

        stmt = (
            select(
                ExecutionRun.id.label("run_id"),
                func.max(manual_baseline_col).label("manual_baseline"),
                func.max(skill_baseline_col).label("skill_baseline"),
            )
            .select_from(ExecutionRun)
            .outerjoin(ExecutionStep, ExecutionStep.run_id == ExecutionRun.id)
            .outerjoin(Skill, Skill.id == func.coalesce(ExecutionStep.skill_id, ExecutionRun.skill_id))
            .where(ExecutionRun.started_at >= since, ExecutionRun.status == "completed")
            .where(Skill.id.is_not(None))
            .group_by(ExecutionRun.id)
        )
        if department_filter:
            stmt = stmt.where(Skill.department.in_(sorted(department_filter)))

        rows = (await db.execute(stmt)).all()
        total_minutes = 0.0
        for row in rows:
            baseline = row.manual_baseline
            if baseline is None:
                baseline = row.skill_baseline
            if baseline is None:
                baseline = settings.TASKTREE_DEFAULT_BASELINE
            total_minutes += float(baseline)
        return round(total_minutes / 60.0, 1)

    async def _compute_token_cost(
        self,
        db: AsyncSession,
        *,
        department: str | None,
        departments: set[str] | None = None,
        since: datetime,
    ) -> float:
        department_filter = departments or ({department} if department else None)
        stmt = select(func.coalesce(func.sum(UsageLog.cost_usd), 0)).where(UsageLog.ts >= since)
        if department_filter:
            stmt = stmt.where(UsageLog.department.in_(sorted(department_filter)))
        total = await db.scalar(stmt)
        return round(float(total or 0), 4)

    async def _compute_human_takeover_rate(
        self,
        db: AsyncSession,
        *,
        department: str | None,
        departments: set[str] | None = None,
        since: datetime,
    ) -> float:
        department_filter = departments or ({department} if department else None)
        stmt = (
            select(
                func.count(DecisionLog.id).label("total"),
                func.count(DecisionLog.id)
                .filter(
                    DecisionLog.user_action.is_not(None),
                    DecisionLog.user_action.notin_(["na", "pending"]),
                )
                .label("takeover"),
            )
            .select_from(DecisionLog)
            .where(DecisionLog.created_at >= since)
            .where(DecisionLog.is_sandbox == False)  # noqa: E712
        )
        if department_filter:
            stmt = stmt.join(Skill, Skill.id == DecisionLog.skill_id).where(
                Skill.department.in_(sorted(department_filter))
            )
        row = (await db.execute(stmt)).one()
        total = int(row.total or 0)
        takeover = int(row.takeover or 0)
        return round(takeover / total, 3) if total else 0.0

    async def _top_skills(
        self,
        db: AsyncSession,
        *,
        department: str | None,
        departments: set[str] | None = None,
        since: datetime,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        department_filter = departments or ({department} if department else None)
        stmt = (
            select(
                Skill.id.label("skill_id"),
                Skill.name.label("skill_name"),
                func.count(func.distinct(ExecutionRun.id)).label("executions"),
            )
            .select_from(ExecutionRun)
            .outerjoin(ExecutionStep, ExecutionStep.run_id == ExecutionRun.id)
            .outerjoin(Skill, Skill.id == func.coalesce(ExecutionStep.skill_id, ExecutionRun.skill_id))
            .where(ExecutionRun.started_at >= since)
            .where(Skill.id.is_not(None))
            .group_by(Skill.id, Skill.name)
            .order_by(func.count(func.distinct(ExecutionRun.id)).desc(), Skill.name.asc())
            .limit(limit)
        )
        if department_filter:
            stmt = stmt.where(Skill.department.in_(sorted(department_filter)))
        rows = (await db.execute(stmt)).all()
        return [
            {"skill_id": row.skill_id, "skill_name": row.skill_name, "executions": int(row.executions or 0)}
            for row in rows
        ]

    async def _query_department_units(
        self,
        db: AsyncSession,
        departments: set[str] | None = None,
    ) -> list[OrgUnit]:
        root_id = settings.TASKTREE_TOP_LEVEL_PARENT_ID
        stmt = (
            select(OrgUnit)
            .where(OrgUnit.type == "department")
            .where(OrgUnit.parent_id == root_id)
            .order_by(OrgUnit.sort_order.asc(), OrgUnit.name.asc())
        )
        if departments is not None:
            if not departments:
                return []
            stmt = stmt.where(OrgUnit.name.in_(sorted(departments)))
        return list((await db.execute(stmt)).scalars().all())

    async def _get_member_scope(
        self,
        db: AsyncSession,
        *,
        current_user: User | None,
        is_admin: bool,
    ) -> tuple[set[str], set[str]]:
        if is_admin or current_user is None:
            return set(), set()
        return await list_user_member_scope(db, current_user.id)

    async def _resolve_department_name(
        self,
        db: AsyncSession,
        department: str | None,
    ) -> str | None:
        if not department:
            return None
        row = (
            await db.execute(
                select(OrgUnit.name)
                .where((OrgUnit.id == department) | (OrgUnit.name == department))
                .order_by(OrgUnit.name.asc())
                .limit(1)
            )
        ).first()
        return row[0] if row else department

    @staticmethod
    def _resolve_accessible_departments(
        *,
        user_department: str | None,
        requested_department: str | None,
        member_departments: set[str],
        is_admin: bool,
    ) -> set[str] | None:
        if is_admin:
            return {requested_department} if requested_department else None
        # [m3] 与 _auth_scope_hash 使用同一 helper，保证 allowed 集合算法一致
        allowed = _compute_user_accessible_departments(
            user_department=user_department,
            member_departments=member_departments,
        )
        if requested_department:
            return {requested_department} if requested_department in allowed else set()
        return allowed

    async def _query_heartbeat_history(
        self,
        db: AsyncSession,
        instance: OpenClawInstance,
        limit: int = 10,
    ) -> list[datetime]:
        history: list[datetime] = []
        try:
            rows = (
                await db.execute(
                    select(TaskNodeLight.last_heartbeat_at)
                    .where(TaskNodeLight.source_instance_id == instance.id)
                    .where(TaskNodeLight.node_type == "heartbeat")
                    .where(TaskNodeLight.last_heartbeat_at.is_not(None))
                    .order_by(TaskNodeLight.last_heartbeat_at.desc())
                    .limit(limit)
                )
            ).all()
            seen: set[datetime] = set()
            for row in rows:
                if row[0] and row[0] not in seen:
                    history.append(row[0])
                    seen.add(row[0])
        except SQLAlchemyError as exc:
            logger.warning("tasktree _query_heartbeat_history SQL 失败: {}", exc)
            history = []
        if not history and instance.last_heartbeat:
            history.append(instance.last_heartbeat)
        return history

    async def _query_instances(
        self,
        db: AsyncSession,
        *,
        departments: set[str] | None = None,
    ) -> list[OpenClawInstance]:
        stmt = select(OpenClawInstance).where(OpenClawInstance.is_active == True)  # noqa: E712
        if departments is not None:
            platform_machine = (
                (OpenClawInstance.is_platform_default == True)  # noqa: E712
                & or_(
                    OpenClawInstance.id == "platform",
                    OpenClawInstance.id.like("platform-%"),
                )
            )
            if departments:
                stmt = stmt.where(or_(
                    OpenClawInstance.department.in_(sorted(departments)),
                    platform_machine,
                ))
            else:
                stmt = stmt.where(platform_machine)
        stmt = stmt.order_by(OpenClawInstance.department.asc().nullsfirst(), OpenClawInstance.name.asc())
        return list((await db.execute(stmt)).scalars().all())

    async def _query_latest_sync_attempts(
        self,
        db: AsyncSession,
        instances: list[OpenClawInstance],
    ) -> dict[str, InstanceSyncItem]:
        instance_ids = [inst.id for inst in instances if inst.id]
        if not instance_ids or not hasattr(db, "execute"):
            return {}

        # 每个实例只展示最新一条同步尝试。这里按 created_at/id 倒序取少量候选，
        # 避免窗口函数在旧测试数据库上引入兼容风险。
        stmt = (
            select(SkillSyncAttempt, Skill.name.label("skill_name"))
            .outerjoin(Skill, Skill.id == SkillSyncAttempt.skill_id)
            .where(SkillSyncAttempt.instance_id.in_(instance_ids))
            .order_by(
                SkillSyncAttempt.created_at.desc().nullslast(),
                SkillSyncAttempt.id.desc(),
            )
            .limit(max(1, len(instance_ids)) * 8)
        )
        try:
            rows = (await db.execute(stmt)).all()
        except SQLAlchemyError as exc:
            logger.warning("tasktree _query_latest_sync_attempts SQL 失败: {}", exc)
            return {}

        latest: dict[str, InstanceSyncItem] = {}
        for attempt, skill_name in rows:
            instance_id = getattr(attempt, "instance_id", None)
            if not instance_id or instance_id in latest:
                continue
            result = attempt.result if isinstance(attempt.result, dict) else {}
            latest[instance_id] = InstanceSyncItem(
                job_id=int(attempt.job_id) if attempt.job_id is not None else None,
                attempt_id=int(attempt.id) if attempt.id is not None else None,
                skill_id=attempt.skill_id,
                skill_name=skill_name or attempt.skill_id,
                version_tag=attempt.version_tag,
                review_id=attempt.review_id,
                status=attempt.status,
                error=attempt.error,
                trigger=attempt.trigger,
                target_scope=result.get("target_scope"),
                fallback_target=bool(result.get("fallback_target")),
                fallback_reason=result.get("fallback_reason"),
                started_at=attempt.started_at,
                completed_at=attempt.completed_at,
                created_at=attempt.created_at,
            )
        return latest

    async def _query_recent_runs(
        self,
        db: AsyncSession,
        instances: list[OpenClawInstance],
        *,
        user_department: str | None,
        member_skill_ids: set[str],
        is_admin: bool,
    ) -> tuple[dict[str, list[SkillRunItem]], dict[str, list[SkillRunItem]]]:
        departments = sorted({inst.department for inst in instances if inst.department})
        instance_ids = [inst.id for inst in instances if inst.id]
        if not departments and not instance_ids:
            return {}, {}

        light_projection = await self._query_recent_runs_light(
            db,
            instances,
            user_department=user_department,
            member_skill_ids=member_skill_ids,
            is_admin=is_admin,
        )
        if light_projection is not None:
            return light_projection

        cutoff = now_bjt() - timedelta(hours=24)
        per_department_limit = max(1, settings.TASKTREE_MAX_RECENT_RUNS)
        stmt = (
            select(
                ExecutionRun.id.label("run_id"),
                ExecutionRun.status.label("run_status"),
                ExecutionRun.started_at,
                ExecutionRun.completed_at,
                ExecutionRun.summary,
                ExecutionRun.trigger_type,
                func.coalesce(ExecutionStep.skill_id, ExecutionRun.skill_id).label("skill_id"),
                ExecutionStep.input_data,
                _run_duration_ms_expr().label("duration_ms"),
                ExecutionStep.error_message,
                DecisionLog.suggested_action,
                Skill.name.label("skill_name"),
                Skill.department.label("skill_department"),
            )
            .select_from(ExecutionRun)
            .outerjoin(ExecutionStep, ExecutionStep.run_id == ExecutionRun.id)
            .outerjoin(Skill, Skill.id == func.coalesce(ExecutionStep.skill_id, ExecutionRun.skill_id))
            .outerjoin(DecisionLog, DecisionLog.run_id == ExecutionRun.id)
            .where(ExecutionRun.started_at >= cutoff, Skill.department.in_(departments))
            .order_by(
                Skill.department.asc(),
                case((ExecutionRun.status == "running", 0), (ExecutionRun.status == "queued", 1), else_=2),
                ExecutionRun.started_at.desc(),
                ExecutionStep.step_order.asc().nullsfirst(),
            )
            .limit(per_department_limit * max(1, len(departments)) * 5)
        )
        rows = (await db.execute(stmt)).all()
        writebacks = await self._query_writebacks(db, [row.run_id for row in rows])

        grouped: dict[str, list[SkillRunItem]] = {}
        for row in rows:
            dept = row.skill_department or ""
            items = grouped.setdefault(dept, [])
            if len(items) >= per_department_limit:
                continue
            if not is_admin and row.skill_department != user_department and row.skill_id not in member_skill_ids:
                continue

            writeback = writebacks.get(row.run_id, {})
            items.append(
                _build_skill_run_item(
                    run_id=row.run_id,
                    skill_id=row.skill_id,
                    skill_name=row.skill_name or row.skill_id,
                    run_status=row.run_status,
                    trigger_type=row.trigger_type,
                    started_at=row.started_at,
                    completed_at=row.completed_at,
                    duration_ms=row.duration_ms,
                    input_data=row.input_data,
                    output_summary=_summarize_run_output(row.summary, row.suggested_action),
                    error_message=row.error_message,
                    writeback_status=writeback.get("status"),
                    writeback_detail=writeback.get("detail"),
                )
            )
        instance_runs = {
            inst.id: grouped.get(inst.department or "", [])[: settings.TASKTREE_MAX_RECENT_RUNS]
            for inst in instances
        }
        return instance_runs, grouped

    async def _query_recent_runs_light(
        self,
        db: AsyncSession,
        instances: list[OpenClawInstance],
        *,
        user_department: str | None,
        member_skill_ids: set[str],
        is_admin: bool,
    ) -> tuple[dict[str, list[SkillRunItem]], dict[str, list[SkillRunItem]]] | None:
        instance_ids = [inst.id for inst in instances if inst.id]
        departments = sorted({inst.department for inst in instances if inst.department})
        if not departments and not instance_ids:
            return None

        stmt = (
            select(
                TaskNodeLight.source_run_id,
                TaskNodeLight.source_instance_id,
                TaskNodeLight.department_id,
                TaskNodeLight.title,
                TaskNodeLight.status,
                TaskNodeLight.started_at,
                TaskNodeLight.finished_at,
            )
            .where(TaskNodeLight.source_run_id.is_not(None))
            .where(TaskNodeLight.node_type.in_(["execution", "run"]))
            .order_by(TaskNodeLight.started_at.desc().nullslast(), TaskNodeLight.created_at.desc())
            .limit(max(1, len(instances)) * settings.TASKTREE_MAX_RECENT_RUNS * 2)
        )
        scope_clauses = []
        if departments:
            scope_clauses.append(TaskNodeLight.department_id.in_(departments))
        if instance_ids:
            scope_clauses.append(TaskNodeLight.source_instance_id.in_(instance_ids))
        if scope_clauses:
            stmt = stmt.where(or_(*scope_clauses))

        try:
            rows = (await db.execute(stmt)).all()
        except SQLAlchemyError as exc:
            logger.warning("tasktree _query_recent_runs_light SQL 失败，降级 fallback: {}", exc)
            return None
        if not rows:
            return None

        run_ids = [row.source_run_id for row in rows if row.source_run_id]
        meta_by_run = await self._load_run_meta(db, run_ids)
        writebacks = await self._query_writebacks(db, run_ids)
        instance_runs: dict[str, list[SkillRunItem]] = {}
        dept_runs: dict[str, list[SkillRunItem]] = {}

        for row in rows:
            department = row.department_id or ""
            meta = meta_by_run.get(row.source_run_id)
            if not is_admin:
                skill_id = (meta.skill_id if meta else row.source_run_id) or row.source_run_id
                if department != user_department and skill_id not in member_skill_ids:
                    continue
            writeback = writebacks.get(row.source_run_id, {})
            item = _build_skill_run_item(
                run_id=row.source_run_id,
                skill_id=(meta.skill_id if meta else row.source_run_id) or row.source_run_id,
                skill_name=(meta.skill_name if meta and meta.skill_name else row.title) or row.source_run_id,
                run_status=(meta.run_status if meta else row.status),
                trigger_type=meta.trigger_type if meta else None,
                started_at=(meta.started_at if meta else row.started_at),
                completed_at=(meta.completed_at if meta else row.finished_at),
                duration_ms=meta.duration_ms if meta else None,
                input_data=meta.input_data if meta else None,
                output_summary=_summarize_run_output(
                    meta.summary if meta else None,
                    meta.suggested_action if meta else None,
                ),
                error_message=meta.error_message if meta else None,
                writeback_status=writeback.get("status"),
                writeback_detail=writeback.get("detail"),
            )
            dept_list = dept_runs.setdefault(department, [])
            if len(dept_list) < settings.TASKTREE_MAX_RECENT_RUNS:
                dept_list.append(item)
            if row.source_instance_id:
                instance_list = instance_runs.setdefault(row.source_instance_id, [])
                if len(instance_list) < settings.TASKTREE_MAX_RECENT_RUNS:
                    instance_list.append(item)

        return instance_runs, dept_runs

    async def _query_runs_for_instance(
        self,
        db: AsyncSession,
        instance: OpenClawInstance,
        *,
        statuses: list[str],
        limit: int = 20,
        user_department: str | None,
        member_skill_ids: set[str],
        is_admin: bool,
    ) -> list[SkillRunItem]:
        light_items = await self._query_runs_for_instance_light(
            db,
            instance=instance,
            statuses=statuses,
            limit=limit,
            user_department=user_department,
            member_skill_ids=member_skill_ids,
            is_admin=is_admin,
        )
        if light_items is not None:
            return light_items

        if not instance.department:
            return []

        stmt = (
            select(
                ExecutionRun.id.label("run_id"),
                ExecutionRun.status.label("run_status"),
                ExecutionRun.started_at,
                ExecutionRun.completed_at,
                ExecutionRun.summary,
                ExecutionRun.trigger_type,
                func.coalesce(ExecutionStep.skill_id, ExecutionRun.skill_id).label("skill_id"),
                ExecutionStep.input_data,
                _run_duration_ms_expr().label("duration_ms"),
                ExecutionStep.error_message,
                DecisionLog.suggested_action,
                Skill.name.label("skill_name"),
            )
            .select_from(ExecutionRun)
            .outerjoin(ExecutionStep, ExecutionStep.run_id == ExecutionRun.id)
            .outerjoin(Skill, Skill.id == func.coalesce(ExecutionStep.skill_id, ExecutionRun.skill_id))
            .outerjoin(DecisionLog, DecisionLog.run_id == ExecutionRun.id)
            .where(ExecutionRun.status.in_(statuses), Skill.department == instance.department)
            .order_by(
                case((ExecutionRun.status == "running", 0), (ExecutionRun.status == "queued", 1), else_=2),
                ExecutionRun.started_at.desc(),
                ExecutionStep.step_order.asc().nullsfirst(),
            )
            .limit(limit)
        )
        if not is_admin and instance.department != user_department:
            stmt = stmt.where(Skill.id.in_(member_skill_ids or {"__none__"}))
        rows = (await db.execute(stmt)).all()
        writebacks = await self._query_writebacks(db, [row.run_id for row in rows])
        return [
            _build_skill_run_item(
                run_id=row.run_id,
                skill_id=row.skill_id,
                skill_name=row.skill_name or row.skill_id,
                run_status=row.run_status,
                trigger_type=row.trigger_type,
                started_at=row.started_at,
                completed_at=row.completed_at,
                duration_ms=row.duration_ms,
                input_data=row.input_data,
                output_summary=_summarize_run_output(row.summary, row.suggested_action),
                error_message=row.error_message,
                writeback_status=writebacks.get(row.run_id, {}).get("status"),
                writeback_detail=writebacks.get(row.run_id, {}).get("detail"),
            )
            for row in rows
        ]

    async def _query_runs_for_instance_light(
        self,
        db: AsyncSession,
        *,
        instance: OpenClawInstance,
        statuses: list[str],
        limit: int,
        user_department: str | None,
        member_skill_ids: set[str],
        is_admin: bool,
    ) -> list[SkillRunItem] | None:
        stmt = (
            select(
                TaskNodeLight.source_run_id,
                TaskNodeLight.title,
                TaskNodeLight.status,
                TaskNodeLight.started_at,
                TaskNodeLight.finished_at,
                TaskNodeLight.department_id,
            )
            .where(TaskNodeLight.source_instance_id == instance.id)
            .where(TaskNodeLight.source_run_id.is_not(None))
            .where(TaskNodeLight.status.in_(statuses))
            .where(TaskNodeLight.node_type.in_(["execution", "run"]))
            .order_by(TaskNodeLight.started_at.desc().nullslast(), TaskNodeLight.created_at.desc())
            .limit(limit)
        )
        try:
            rows = (await db.execute(stmt)).all()
        except SQLAlchemyError as exc:
            logger.warning("tasktree _query_runs_for_instance_light SQL 失败，降级 fallback: {}", exc)
            return None
        if not rows:
            return None

        run_ids = [row.source_run_id for row in rows if row.source_run_id]
        meta_by_run = await self._load_run_meta(db, run_ids)
        writebacks = await self._query_writebacks(db, run_ids)

        items: list[SkillRunItem] = []
        for row in rows:
            meta = meta_by_run.get(row.source_run_id)
            skill_id = (meta.skill_id if meta else row.source_run_id) or row.source_run_id
            if not is_admin and instance.department != user_department and skill_id not in member_skill_ids:
                continue
            items.append(
                _build_skill_run_item(
                    run_id=row.source_run_id,
                    skill_id=skill_id,
                    skill_name=(meta.skill_name if meta and meta.skill_name else row.title) or row.source_run_id,
                    run_status=(meta.run_status if meta else row.status),
                    trigger_type=meta.trigger_type if meta else None,
                    started_at=(meta.started_at if meta else row.started_at),
                    completed_at=(meta.completed_at if meta else row.finished_at),
                    duration_ms=meta.duration_ms if meta else None,
                    input_data=meta.input_data if meta else None,
                    output_summary=_summarize_run_output(
                        meta.summary if meta else None,
                        meta.suggested_action if meta else None,
                    ),
                    error_message=meta.error_message if meta else None,
                    writeback_status=writebacks.get(row.source_run_id, {}).get("status"),
                    writeback_detail=writebacks.get(row.source_run_id, {}).get("detail"),
                )
            )
        return items

    async def _load_run_meta(
        self,
        db: AsyncSession,
        run_ids: list[str],
    ) -> dict[str, Any]:
        """批量加载 ExecutionRun + 首个 Step + Skill 元数据，返回 {run_id: row}。

        供 _query_recent_runs_light / _query_runs_for_instance_light 复用，
        避免两处重复定义相同 SELECT + ExecutionStep.step_order 顺序取首个的语义。
        """
        unique_ids = [rid for rid in dict.fromkeys(run_ids) if rid]
        if not unique_ids:
            return {}
        stmt = (
            select(
                ExecutionRun.id.label("run_id"),
                ExecutionRun.status.label("run_status"),
                ExecutionRun.started_at,
                ExecutionRun.completed_at,
                ExecutionRun.summary,
                ExecutionRun.trigger_type,
                func.coalesce(ExecutionStep.skill_id, ExecutionRun.skill_id).label("skill_id"),
                ExecutionStep.input_data,
                _run_duration_ms_expr().label("duration_ms"),
                ExecutionStep.error_message,
                DecisionLog.suggested_action,
                Skill.name.label("skill_name"),
            )
            .select_from(ExecutionRun)
            .outerjoin(ExecutionStep, ExecutionStep.run_id == ExecutionRun.id)
            .outerjoin(Skill, Skill.id == func.coalesce(ExecutionStep.skill_id, ExecutionRun.skill_id))
            .outerjoin(DecisionLog, DecisionLog.run_id == ExecutionRun.id)
            .where(ExecutionRun.id.in_(unique_ids))
            .order_by(ExecutionRun.started_at.desc(), ExecutionStep.step_order.asc().nullsfirst())
        )
        rows = (await db.execute(stmt)).all()
        meta_by_run: dict[str, Any] = {}
        for row in rows:
            meta_by_run.setdefault(row.run_id, row)
        return meta_by_run

    async def _query_writebacks(
        self,
        db: AsyncSession,
        run_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        unique_run_ids = [run_id for run_id in dict.fromkeys(run_ids) if run_id]
        if not unique_run_ids:
            return {}

        requests_result = await db.execute(
            select(
                DecisionRequest.id,
                DecisionRequest.run_id,
                DecisionRequest.aggregate_status,
                DecisionRequest.aggregate_decision,
                DecisionRequest.callback_status,
            )
            .where(DecisionRequest.run_id.in_(unique_run_ids))
        )
        request_rows = requests_result.all()
        if not request_rows:
            return {}

        request_ids = [row.id for row in request_rows]
        dispatch_result = await db.execute(
            select(
                TodoDispatchTask.request_id,
                func.count(TodoDispatchTask.id).label("dispatch_count"),
            )
            .where(TodoDispatchTask.request_id.in_(request_ids))
            .group_by(TodoDispatchTask.request_id)
        )
        dispatch_counts = {row.request_id: row.dispatch_count for row in dispatch_result}

        todo_result = await db.execute(
            select(
                AITodo.request_id,
                AITodo.id,
                AITodo.assignee,
                AITodo.status,
            )
            .where(AITodo.request_id.in_(request_ids))
            .order_by(AITodo.created_at.asc(), AITodo.id.asc())
        )
        first_todos: dict[str, tuple[int, str, str]] = {}
        for row in todo_result:
            first_todos.setdefault(row.request_id, (row.id, row.assignee, row.status))

        result: dict[str, dict[str, Any]] = {}
        for row in request_rows:
            status = _map_writeback_status(
                aggregate_status=row.aggregate_status,
                aggregate_decision=row.aggregate_decision,
                dispatch_count=dispatch_counts.get(row.id, 0),
            )
            todo_info = first_todos.get(row.id)
            result[row.run_id] = {
                "status": status,
                "detail": {
                    "request_id": row.id,
                    "aggregate_status": row.aggregate_status,
                    "aggregate_decision": row.aggregate_decision,
                    "callback_status": row.callback_status,
                    "dispatch_count": dispatch_counts.get(row.id, 0),
                    "todo_id": todo_info[0] if todo_info else None,
                    "assignee": todo_info[1] if todo_info else None,
                    "todo_status": todo_info[2] if todo_info else None,
                },
            }
        return result

    def _assemble_tree(
        self,
        instances: list[OpenClawInstance],
        dept_runs: dict[str, list[SkillRunItem]],
        *,
        department_units: list[OrgUnit] | None = None,
        instance_runs: dict[str, list[SkillRunItem]] | None = None,
        instance_syncs: dict[str, InstanceSyncItem] | None = None,
        status_filter: str = "all",
        is_admin: bool = False,
        requested_department: str | None = None,
        lv1_by_id: dict[str, OrgUnit] | None = None,
        lv1_by_name: dict[str, OrgUnit] | None = None,
        lv1_names: set[str] | None = None,
        org_by_name: dict[str, OrgUnit] | None = None,
    ) -> TaskTreeResponse:
        now = now_bjt()
        dept_map: dict[str, DepartmentNode] = {}
        department_order: list[str] = []

        if lv1_by_id is None or lv1_by_name is None or lv1_names is None or org_by_name is None:
            total_online = 0
            total_offline = 0

            for unit in department_units or []:
                department_order.append(unit.name)
                dept_map[unit.name] = DepartmentNode(
                    department_id=unit.id,
                    department_name=unit.name,
                    today_executions=len(dept_runs.get(unit.name, [])),
                )

            for department, runs in dept_runs.items():
                if department not in dept_map:
                    department_order.append(department)
                    dept_map[department] = DepartmentNode(
                        department_id=department,
                        department_name=department,
                        today_executions=len(runs),
                    )

            for inst in instances:
                status = _compute_node_status(inst)
                if not _match_status_filter(status, status_filter):
                    continue

                department = (
                    VIRTUAL_UNASSIGNED_NAME
                    if _is_platform_machine(inst)
                    else (inst.department or VIRTUAL_ORPHAN_NAME)
                )
                dept_node = dept_map.setdefault(
                    department,
                    DepartmentNode(
                        department_id=department,
                        department_name=department,
                        today_executions=len(dept_runs.get(department, [])),
                    ),
                )

                if status == NodeStatus.ONLINE:
                    dept_node.online_count += 1
                    total_online += 1
                else:
                    dept_node.offline_count += 1
                    total_offline += 1

                instance_items = (instance_runs or {}).get(inst.id) or dept_runs.get(department, [])
                latest_sync = (instance_syncs or {}).get(inst.id)
                running_count = sum(1 for item in instance_items if item.status == SkillRunStatus.RUNNING)
                dept_node.node_count += 1
                dept_node.instances.append(
                    InstanceNode(
                        instance_id=inst.id,
                        name=inst.name,
                        department=inst.department,
                        agent_type=inst.agent_type,
                        runtime_type=_instance_runtime_type(inst),
                        bridge_gateway_kind=getattr(inst, "bridge_gateway_kind", None),
                        bridge_gateway_version=getattr(inst, "bridge_gateway_version", None),
                        node_status=status,
                        heartbeat_ago_text=_heartbeat_ago_text(inst.last_heartbeat, now),
                        last_heartbeat_ago=_heartbeat_ago_text(inst.last_heartbeat, now),
                        last_heartbeat_at=inst.last_heartbeat,
                        bridge_skills_dir=getattr(inst, "bridge_skills_dir", None),
                        last_sync_at=getattr(inst, "last_sync_at", None),
                        last_sync_ok=getattr(inst, "last_sync_ok", None),
                        latest_sync=latest_sync,
                        capacity=_default_instance_capacity(inst),
                        bridge_version=getattr(inst, "bridge_version", None),
                        bridge_latest_version=BRIDGE_VERSION,
                        bridge_update_available=_bridge_update_available(getattr(inst, "bridge_version", None)),
                        bridge_platform=getattr(inst, "bridge_platform", None),
                        active_count=running_count,
                        recent_skills=instance_items[: settings.TASKTREE_MAX_RECENT_RUNS],
                        skills=instance_items[: settings.TASKTREE_MAX_RECENT_RUNS],
                        **_instance_machine_summary(inst),
                    )
                )

            ordered_units = [dept_map[name] for name in department_order if name in dept_map]
            remaining = [dept for name, dept in dept_map.items() if name not in department_order]
            departments = ordered_units + sorted(remaining, key=lambda item: item.department_name)
            total_running = sum(
                1
                for runs in dept_runs.values()
                for item in runs
                if item.status == SkillRunStatus.RUNNING
            )
            today_failed = sum(
                1
                for runs in dept_runs.values()
                for item in runs
                if item.status == SkillRunStatus.FAILED
            )
            today_executions = sum(len(runs) for runs in dept_runs.values())

            tree = TaskTreeResponse(
                departments=departments,
                projected_at=isoformat_bjt(now),
                etag="",
                total_online=total_online,
                total_offline=total_offline,
                total_running=total_running,
                today_failed=today_failed,
                today_executions=today_executions,
            )
            tree.etag = _compute_etag(tree)
            return tree

        total_online = 0
        total_offline = 0

        for unit in department_units or []:
            department_order.append(unit.name)
            dept_map[unit.name] = DepartmentNode(
                department_id=unit.id,
                department_name=unit.name,
                today_executions=0,
                is_virtual=False,
            )

        if (
            is_admin
            and settings.TASKTREE_SHOW_VIRTUAL_GROUPS
            and requested_department in {VIRTUAL_AI_ID, VIRTUAL_UNASSIGNED_ID, VIRTUAL_ORPHAN_ID}
        ):
            virtual_name = {
                VIRTUAL_AI_ID: VIRTUAL_AI_NAME,
                VIRTUAL_UNASSIGNED_ID: VIRTUAL_UNASSIGNED_NAME,
                VIRTUAL_ORPHAN_ID: VIRTUAL_ORPHAN_NAME,
            }[requested_department]
            dept_map[virtual_name] = DepartmentNode(
                department_id=requested_department,
                department_name=virtual_name,
                is_virtual=True,
            )
            department_order.append(virtual_name)

        for inst in instances:
            status = _compute_node_status(inst)
            if not _match_status_filter(status, status_filter):
                continue

            department_id, department_name, is_virtual = _resolve_instance_lv1(
                inst,
                lv1_by_id=lv1_by_id,
                lv1_by_name=lv1_by_name,
                lv1_names=lv1_names,
                org_by_name=org_by_name,
            )
            if is_virtual and not settings.TASKTREE_SHOW_VIRTUAL_GROUPS:
                continue
            if is_virtual and not is_admin and department_id != VIRTUAL_UNASSIGNED_ID:
                continue
            if department_id != VIRTUAL_UNASSIGNED_ID and not _matches_requested_department(
                department_id=department_id,
                department_name=department_name,
                requested_department=requested_department,
            ):
                continue

            dept_node = dept_map.get(department_name)
            if dept_node is None:
                dept_node = DepartmentNode(
                    department_id=department_id,
                    department_name=department_name,
                    is_virtual=is_virtual,
                )
                dept_map[department_name] = dept_node
                department_order.append(department_name)

            if status == NodeStatus.ONLINE:
                dept_node.online_count += 1
                total_online += 1
            else:
                dept_node.offline_count += 1
                total_offline += 1

            instance_items = (instance_runs or {}).get(inst.id) or dept_runs.get(inst.department or "", [])
            latest_sync = (instance_syncs or {}).get(inst.id)
            dept_node.today_executions += len(instance_items)
            running_count = sum(1 for item in instance_items if item.status == SkillRunStatus.RUNNING)
            dept_node.node_count += 1
            dept_node.instances.append(
                InstanceNode(
                    instance_id=inst.id,
                    name=inst.name,
                    department=department_name,
                    agent_type=inst.agent_type,
                    runtime_type=_instance_runtime_type(inst),
                    bridge_gateway_kind=getattr(inst, "bridge_gateway_kind", None),
                    bridge_gateway_version=getattr(inst, "bridge_gateway_version", None),
                    node_status=status,
                    heartbeat_ago_text=_heartbeat_ago_text(inst.last_heartbeat, now),
                    last_heartbeat_ago=_heartbeat_ago_text(inst.last_heartbeat, now),
                    last_heartbeat_at=inst.last_heartbeat,
                    bridge_skills_dir=getattr(inst, "bridge_skills_dir", None),
                    last_sync_at=getattr(inst, "last_sync_at", None),
                    last_sync_ok=getattr(inst, "last_sync_ok", None),
                    latest_sync=latest_sync,
                    capacity=_default_instance_capacity(inst),
                    bridge_version=getattr(inst, "bridge_version", None),
                    bridge_latest_version=BRIDGE_VERSION,
                    bridge_update_available=_bridge_update_available(getattr(inst, "bridge_version", None)),
                    bridge_platform=getattr(inst, "bridge_platform", None),
                    active_count=running_count,
                    recent_skills=instance_items[: settings.TASKTREE_MAX_RECENT_RUNS],
                    skills=instance_items[: settings.TASKTREE_MAX_RECENT_RUNS],
                    **_instance_machine_summary(inst),
                )
            )

        for department in dept_map.values():
            department.anomaly_count = sum(
                1
                for instance in department.instances
                if instance.node_status in {NodeStatus.OFFLINE, NodeStatus.MAYBE_OFFLINE}
            )
            department.instances.sort(key=lambda item: item.name)

        department_index = {name: index for index, name in enumerate(department_order)}
        names_sorted = sorted(
            dept_map.keys(),
            key=lambda name: (
                0 if dept_map[name].anomaly_count > 0 else 1,
                1 if dept_map[name].is_virtual else 0,
                -dept_map[name].anomaly_count,
                -dept_map[name].node_count,
                department_index.get(name, 999),
                name,
            ),
        )
        departments = [dept_map[name] for name in names_sorted]
        total_running = sum(
            1
            for department in departments
            for instance in department.instances
            for item in instance.recent_skills
            if item.status == SkillRunStatus.RUNNING
        )
        today_failed = sum(
            1
            for department in departments
            for instance in department.instances
            for item in instance.recent_skills
            if item.status == SkillRunStatus.FAILED
        )
        today_executions = sum(
            len(instance.recent_skills)
            for department in departments
            for instance in department.instances
        )

        tree = TaskTreeResponse(
            departments=departments,
            projected_at=isoformat_bjt(now),
            etag="",
            total_online=total_online,
            total_offline=total_offline,
            total_running=total_running,
            today_failed=today_failed,
            today_executions=today_executions,
        )
        tree.etag = _compute_etag(tree)
        return tree

    @staticmethod
    def _check_department_access(
        department: str | None,
        current_user: User,
        *,
        allowed_departments: set[str] | None = None,
    ) -> None:
        """统一部门级访问校验：全局查看者放行；否则要求部门匹配或在允许集合内。"""
        if is_global_viewer(current_user):
            return
        if not department or _is_virtual_department(department):
            raise AppError("AUTH_DEPARTMENT_DENIED", 403)
        if allowed_departments and department in allowed_departments:
            return
        if department != current_user.department:
            raise AppError("AUTH_DEPARTMENT_DENIED", 403)

    @classmethod
    async def _ensure_run_access(cls, db: AsyncSession, skill_id: str, current_user: User) -> None:
        if is_global_viewer(current_user):
            return
        member = await get_skill_member(db, skill_id=skill_id, user_id=current_user.id)
        if member is not None:
            return
        department = await db.scalar(select(Skill.department).where(Skill.id == skill_id))
        if not department:
            raise AppError("AUTH_DEPARTMENT_DENIED", 403)
        lv1_by_id, _lv1_by_name, lv1_names, org_by_name = await _load_org_indexes(db)
        user_lv1 = _resolve_user_lv1(
            current_user.department,
            lv1_by_id=lv1_by_id,
            lv1_names=lv1_names,
            org_by_name=org_by_name,
        )
        requested_lv1 = _normalize_requested_department(
            department,
            lv1_by_id=lv1_by_id,
            lv1_names=lv1_names,
            org_by_name=org_by_name,
        )
        if user_lv1 is None or requested_lv1 != user_lv1:
            raise AppError("AUTH_DEPARTMENT_DENIED", 403)

    @classmethod
    async def _ensure_instance_access(cls, db: AsyncSession, department: str | None, current_user: User) -> None:
        if is_global_viewer(current_user):
            return
        member_departments = await list_user_member_departments(db, current_user.id)
        if department in member_departments:
            return
        if not department or _is_virtual_department(department):
            raise AppError("AUTH_DEPARTMENT_DENIED", 403)
        lv1_by_id, _lv1_by_name, lv1_names, org_by_name = await _load_org_indexes(db)
        user_lv1 = _resolve_user_lv1(
            current_user.department,
            lv1_by_id=lv1_by_id,
            lv1_names=lv1_names,
            org_by_name=org_by_name,
        )
        requested_lv1 = _normalize_requested_department(
            department,
            lv1_by_id=lv1_by_id,
            lv1_names=lv1_names,
            org_by_name=org_by_name,
        )
        if user_lv1 is None or requested_lv1 != user_lv1:
            raise AppError("AUTH_DEPARTMENT_DENIED", 403)


projection = TaskTreeProjection()


# ─ [M3] Prometheus label 基数防爆：dept 白名单 ────────────────────────────
_METRIC_DEPT_WHITELIST: set[str] = set()
_METRIC_DEPT_WHITELIST_LOADED: bool = False


async def warmup_metric_dept_whitelist() -> None:
    """从 org_units 加载部门白名单，供 _safe_metric_dept 过滤 label。

    幂等：startup 阶段调一次；失败不抛异常，失败时 _safe_metric_dept 会放行
    """
    global _METRIC_DEPT_WHITELIST, _METRIC_DEPT_WHITELIST_LOADED
    try:
        from app.database import async_session_factory

        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(OrgUnit.name).where(OrgUnit.type == "department")
                )
            ).scalars().all()
            _METRIC_DEPT_WHITELIST = {row for row in rows if row}
            _METRIC_DEPT_WHITELIST_LOADED = True
            logger.info(
                "tasktree metric dept 白名单预热完成: count={}",
                len(_METRIC_DEPT_WHITELIST),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("tasktree metric dept 白名单加载失败（降级放行）: {}", exc)
        _METRIC_DEPT_WHITELIST = set()
        _METRIC_DEPT_WHITELIST_LOADED = False


def _safe_metric_dept(dept_slot: str | None) -> str:
    """把任意 dept_slot 映射成安全的 Prometheus label 值。

    - None / 空 → "all"（admin 汇总）
    - "all" → 保留
    - 命中白名单 → 原值
    - 白名单未加载 → 退化直接返回（避免开发期空 metric）
    - 否则 → "unknown"（防基数爆炸）
    """
    if not dept_slot:
        return "all"
    if dept_slot in {"all", ALL_LV1_SLOT, VIRTUAL_AI_ID, VIRTUAL_UNASSIGNED_ID, VIRTUAL_ORPHAN_ID}:
        return dept_slot
    if not _METRIC_DEPT_WHITELIST_LOADED:
        return dept_slot
    if dept_slot in _METRIC_DEPT_WHITELIST:
        return dept_slot
    return "unknown"


async def invalidate_tasktree(department: str | None = None) -> None:
    """失效任务树缓存。

    P0-3：按 department 粒度失效。传入 department 时仅清该部门（及 etag），
    避免一次 bridge heartbeat 打穿所有部门缓存。
    传入 None 时退化为全清（用于启动期一次性清理、管理员强制刷新等兜底场景）。
    """
    try:
        if department:
            for slot in await _resolve_tasktree_invalidation_slots(department):
                await cache_delete_pattern(f"tasktree:tree:{slot}:*")
                await cache_delete_pattern(f"tasktree:stats:{slot}:*")
                await cache_delete_pattern(f"tasktree:etag:{slot}:*")
                await cache_delete_pattern(f"tasktree:dashboard:{slot}:*")
            # node/chain 仍是全局粒度（以 instance_id / run_id 为 key），保留全清
            await cache_delete_pattern("tasktree:node:*")
            await cache_delete_pattern("tasktree:chain:*")
        else:
            await cache_delete_pattern("tasktree:tree:*")
            await cache_delete_pattern("tasktree:stats:*")
            await cache_delete_pattern("tasktree:etag:*")
            await cache_delete_pattern("tasktree:dashboard:*")
            await cache_delete_pattern("tasktree:node:*")
            await cache_delete_pattern("tasktree:chain:*")
    except Exception as exc:  # noqa: BLE001
        logger.warning("invalidate_tasktree 失败 department={} err={}", department, exc)
        return


def _compute_node_status(inst: OpenClawInstance) -> NodeStatus:
    if not inst.is_active:
        return NodeStatus.OFFLINE
    try:
        from app.aiclaw.bridge_registry import bridge_registry

        if bridge_registry.is_online(inst.id):
            return NodeStatus.ONLINE
    except Exception as e:
        logger.debug("bridge_registry.is_online 查询失败 inst={}: {}", inst.id, e)
    if not inst.last_heartbeat:
        return NodeStatus.OFFLINE

    age = _elapsed_seconds_since(inst.last_heartbeat)
    if age <= settings.TASKTREE_HEARTBEAT_ONLINE:
        return NodeStatus.ONLINE
    if age <= settings.TASKTREE_HEARTBEAT_MAYBE:
        return NodeStatus.MAYBE_OFFLINE
    return NodeStatus.OFFLINE


def _heartbeat_ago_text(heartbeat: datetime | None, now: datetime) -> str:
    if not heartbeat:
        return "未知"
    seconds = max(0, int((now - heartbeat).total_seconds()))
    if seconds < 60:
        return f"{seconds}s 前"
    if seconds < 3600:
        return f"{seconds // 60}min 前"
    return f"{seconds // 3600}h 前"


async def _safe_cache_get(key: str) -> Any | None:
    try:
        value = await cache_get(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "tasktree _safe_cache_get 失败 key={}: {}", key, exc, exc_info=True
        )
        return None
    label = _cache_key_type(key)
    if value is None:
        CACHE_MISS.labels(cache_key=label).inc()
    else:
        CACHE_HIT.labels(cache_key=label).inc()
    return value


async def _safe_cache_set(key: str, value: Any, *, ttl: int) -> None:
    try:
        await cache_set(key, value, ttl=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "tasktree _safe_cache_set 失败 key={} ttl={}: {}",
            key, ttl, exc, exc_info=True,
        )
        return


def _cache_key_type(key: str) -> str:
    parts = key.split(":")
    return parts[1] if len(parts) > 1 else "unknown"


ADMIN_ROLES: frozenset[str] = frozenset({"admin", "ai_engineer", "director"})


def is_global_viewer(user: User) -> bool:
    """全局查看者：admin / ai_engineer / director 角色，或 can_view_all 显式开启。"""
    return user.role in ADMIN_ROLES or bool(getattr(user, "can_view_all", False))


_is_global_viewer = is_global_viewer


def _authoritative_identity(
    current_user: User | None,
    *,
    is_admin_hint: bool,
    user_department_hint: str | None,
) -> tuple[bool, str | None]:
    """[codex-2026-04-14] service 层 ABAC 兜底：权威身份必须从 current_user 推导。

    规则：
    - current_user 不为 None：is_admin / user_department 都以 current_user 为准，
      任何 hint 都被忽略（之前的漏洞：内部调用伪造 is_admin=True 能越权）
    - current_user 为 None：视为未登录/未授权上下文，**强制非 admin 且无
      user_department**，允许集合为空 → 实际会降级到"看不到任何部门"。
      这样即使内部调用忘传 current_user 也无法放大权限（codex 二次评审漏洞）

    hint 参数保留是为了签名兼容，不再参与授权决策。
    """
    if current_user is None:
        return False, None
    derived_admin = _is_global_viewer(current_user)
    derived_department = getattr(current_user, "department", None)
    return derived_admin, derived_department


def _match_status_filter(status: NodeStatus, status_filter: str) -> bool:
    if status_filter in ("", "all", None):
        return True
    if status_filter == "online":
        return status == NodeStatus.ONLINE
    if status_filter == "maybe_offline":
        return status == NodeStatus.MAYBE_OFFLINE
    if status_filter == "offline":
        return status == NodeStatus.OFFLINE
    return True


def _normalize_skill_status(run_status: str | None) -> SkillRunStatus:
    if run_status == "running":
        return SkillRunStatus.RUNNING
    if run_status == "queued":
        return SkillRunStatus.QUEUED
    if run_status == "completed":
        return SkillRunStatus.COMPLETED
    if run_status in {"failed", "timeout", "blocked"}:
        return SkillRunStatus.FAILED
    return SkillRunStatus.IDLE


def _default_instance_capacity(inst: OpenClawInstance) -> int | None:
    if (inst.connection_mode or "bridge") == "bridge":
        return settings.BRIDGE_ACTIVE_RUNS_MAX_PER_INSTANCE
    return None


def _build_skill_run_item(
    *,
    run_id: str,
    skill_id: str,
    skill_name: str,
    run_status: str | None,
    trigger_type: str | None,
    started_at: datetime | None,
    completed_at: datetime | None,
    duration_ms: int | None,
    input_data: dict[str, Any] | None,
    output_summary: str | None,
    error_message: str | None,
    writeback_status: WritebackStatus | None,
    writeback_detail: dict[str, Any] | None,
) -> SkillRunItem:
    duration_seconds = None
    if duration_ms is not None:
        duration_seconds = round(duration_ms / 1000.0, 1)
    elif started_at and completed_at:
        duration_seconds = round((completed_at - started_at).total_seconds(), 1)

    return SkillRunItem(
        run_id=run_id,
        skill_id=skill_id,
        skill_name=skill_name,
        status=_normalize_skill_status(run_status),
        trigger_type=trigger_type,
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=duration_seconds,
        input_summary=_extract_input_summary(input_data),
        output_summary=output_summary,
        error_message=error_message,
        writeback=writeback_status or WritebackStatus.NONE,
        writeback_detail=writeback_detail,
    )


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _clip_text(value: Any, limit: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:limit]


def _parse_training_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return parse_bjt_datetime(text)
    except Exception:
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _training_job_model_family(job: TrainingJob, spec: dict[str, Any]) -> str | None:
    deployment = _safe_dict(spec.get("deployment"))
    parent_model = _safe_dict(spec.get("parent_model"))
    for value in (
        deployment.get("model_family"),
        spec.get("model_family"),
        parent_model.get("model_family"),
    ):
        text = _clip_text(value, 120)
        if text:
            return text
    if job.target_skill_id:
        return f"{job.target_skill_id}:{job.job_type or 'lora'}"
    return None


def _training_job_summary_item(
    job: TrainingJob,
    *,
    source_run_id: str,
    source_sample_count: int,
    latest_task: TrainingJobTask | None,
    deployment: TrainingModelDeployment | None,
) -> TrainingJobSummaryItem:
    spec = _safe_dict(job.spec_json)
    dataset = _safe_dict(spec.get("dataset"))
    window = _safe_dict(dataset.get("window"))
    parent_model = _safe_dict(spec.get("parent_model"))
    governance = _safe_dict(spec.get("governance"))
    automation = _safe_dict(job.gateway_payload_json).get("automation_status")
    automation_status = automation if isinstance(automation, dict) else {}
    package = _safe_dict(_safe_dict(job.gateway_payload_json).get("dataset_package"))
    artifact_ref = _safe_dict(deployment.artifact_ref_json) if deployment else {}
    return TrainingJobSummaryItem(
        job_id=job.id,
        title=job.title,
        status=job.status,
        failure_stage=job.failure_stage,
        relation="trained_from",
        target_skill_id=job.target_skill_id,
        target_gateway_id=job.target_gateway_id,
        model_family=_training_job_model_family(job, spec),
        training_strategy=job.training_strategy,
        training_mode=_clip_text(spec.get("training_mode"), 80),
        full_history_cycle_index=_int_or_none(governance.get("cycle_index")),
        full_history_cycle_count=_int_or_none(governance.get("cycle_count")),
        automation_step=_clip_text(automation_status.get("current_step") or automation_status.get("step"), 80),
        dataset_ref=job.dataset_ref or _clip_text(dataset.get("ref"), 500),
        dataset_window_date=_clip_text(window.get("date"), 20),
        dataset_window_start=_parse_training_datetime(window.get("start")),
        dataset_window_end=_parse_training_datetime(window.get("end")),
        dataset_timezone=_clip_text(window.get("timezone"), 60),
        source_run_id=source_run_id,
        source_sample_count=source_sample_count,
        sample_count=_int_or_none(package.get("sample_count") or dataset.get("sample_total")),
        train_count=_int_or_none(package.get("train_count")),
        eval_count=_int_or_none(package.get("eval_count") or dataset.get("eval_count")),
        parent_model_deployment_id=_clip_text(parent_model.get("model_deployment_id"), 80),
        parent_model_family=_clip_text(parent_model.get("model_family"), 120),
        parent_artifact_id=_clip_text(parent_model.get("artifact_id"), 120),
        approved_by=job.approved_by,
        approved_at=job.approved_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        latest_task_id=latest_task.id if latest_task else None,
        latest_task_status=latest_task.status if latest_task else None,
        latest_task_progress=_float_or_none(latest_task.progress if latest_task else None),
        latest_task_error=latest_task.error_message if latest_task else None,
        latest_task_updated_at=latest_task.updated_at if latest_task else None,
        deployment_id=deployment.id if deployment else None,
        deployment_status=deployment.status if deployment else None,
        rollout_percent=deployment.rollout_percent if deployment else None,
        artifact_id=(deployment.artifact_id if deployment else None),
        artifact_sha256=_clip_text(artifact_ref.get("sha256"), 64),
    )


def _should_hide_legacy_training_candidate(
    job: TrainingJob,
    latest_task: TrainingJobTask | None,
    deployment: TrainingModelDeployment | None,
) -> bool:
    """Hide pre-daily-loop auto candidates that never entered execution."""
    if latest_task is not None or deployment is not None:
        return False
    if job.created_by != "learning_auto_flow":
        return False
    if job.status != "awaiting_review":
        return False
    spec = _safe_dict(job.spec_json)
    if spec.get("training_mode") == "daily_incremental":
        return False
    dataset_ref = str(job.dataset_ref or _safe_dict(spec.get("dataset")).get("ref") or "")
    return dataset_ref.endswith("/training/latest")


def _pending_daily_training_summary_item(
    artifacts: list[LearningArtifact],
    *,
    source_run_id: str,
    skill_id: str | None,
) -> TrainingJobSummaryItem:
    first_created = min((row.created_at for row in artifacts if row.created_at), default=None)
    window_date = first_created.date().isoformat() if first_created else None
    planned_after = None
    if first_created:
        planned_after = datetime.combine(first_created.date() + timedelta(days=1), datetime.min.time())
    target_skill_id = skill_id or next((row.skill_id or row.target_id for row in artifacts if row.skill_id or row.target_id), None)
    sample_count = len([row for row in artifacts if row.artifact_kind == "training_sample"])
    eval_count = len([row for row in artifacts if row.artifact_kind == "eval_case"])
    return TrainingJobSummaryItem(
        status="pending_daily_training",
        relation="pending_daily_training",
        title="已产出训练样本，待日批训练",
        target_skill_id=target_skill_id,
        source_run_id=source_run_id,
        source_sample_count=len(artifacts),
        sample_count=sample_count,
        train_count=sample_count,
        eval_count=eval_count,
        dataset_window_date=window_date,
        dataset_timezone="Asia/Shanghai",
        next_training_window_date=window_date,
        planned_training_after=planned_after,
        training_strategy="learning_sample_threshold",
        training_mode="daily_incremental",
    )


def _has_active_training_summary(items: list[TrainingJobSummaryItem]) -> bool:
    active_statuses = {"pending_daily_training", "awaiting_review", "queued", "running", "evaluating"}
    return any(str(item.status or "") in active_statuses for item in items)


def _extract_input_summary(input_data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(input_data, dict) or not input_data:
        return None
    summary: dict[str, Any] = {}
    for key in list(input_data.keys())[:3]:
        summary[key] = input_data[key]
    return summary


def _summarize_run_output(summary: str | None, suggested_action: dict[str, Any] | None) -> Any | None:
    if summary:
        return summary
    if isinstance(suggested_action, dict) and suggested_action:
        compact: dict[str, Any] = {}
        for key in ("action", "summary", "reason", "decision", "assignee", "content"):
            if key in suggested_action:
                compact[key] = suggested_action[key]
        return compact or suggested_action
    return None


def _map_writeback_status(
    *,
    aggregate_status: str | None,
    aggregate_decision: str | None,
    dispatch_count: int,
) -> WritebackStatus:
    if aggregate_status in {None, ""}:
        return WritebackStatus.NONE
    if aggregate_status == "pending":
        return WritebackStatus.PENDING
    if aggregate_status == "expired":
        return WritebackStatus.REJECTED
    if aggregate_decision == "rejected":
        return WritebackStatus.REJECTED
    if dispatch_count > 0:
        return WritebackStatus.DISPATCHED
    if aggregate_decision == "approved" or aggregate_status == "completed":
        return WritebackStatus.APPROVED
    return WritebackStatus.NONE


def _compute_etag(tree: TaskTreeResponse) -> str:
    payload = {
        "departments": tree.model_dump(mode="json")["departments"],
        "total_online": tree.total_online,
        "total_offline": tree.total_offline,
        "total_running": tree.total_running,
        "today_failed": tree.today_failed,
        "today_executions": tree.today_executions,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _etag_cache_key(department_slot: str, auth_scope_hash: str, status_filter: str) -> str:
    """etag key 需和主 tree key 同粒度：department + auth_scope + filters。"""
    return f"tasktree:etag:{department_slot}:{auth_scope_hash}:{status_filter or 'all'}"


def _tasktree_cache_scope(*, current_user: User | None, department: str | None, is_admin: bool) -> str:
    """保留旧 helper 用于 dashboard / chain 等非 department-sharded 路径。"""
    if not is_admin and current_user is not None:
        return f"user:{current_user.id}"
    return department or "all"


def _compute_user_accessible_departments(
    *,
    user_department: str | None,
    member_departments: set[str] | None,
) -> set[str]:
    """[m3] 计算用户可访问部门集合，供 _auth_scope_hash 与 _resolve_accessible_departments 共用。

    allowed = user.department ∪ member_departments（去空串/None）。
    """
    allowed: set[str] = set()
    if user_department:
        allowed.add(str(user_department))
    if member_departments:
        allowed.update(dept for dept in member_departments if dept)
    return allowed


def _auth_scope_hash(
    *,
    current_user: User | None,
    member_departments: set[str],
    member_skill_ids: set[str] | None = None,
    is_admin: bool,
) -> str:
    """生成授权域哈希，用于缓存 key 隔离不同 role/scope 的用户。

    必须包含每一维 ABAC 过滤输入：role + allowed_departments + member_skill_ids。
    否则两个用户只要角色同、部门同就会共享缓存，但他们代管的 skill 集合不同
    时仍然会读到越权数据（codex 2026-04-14 审计发现）。
    """
    if current_user is None:
        return "anon"
    role = getattr(current_user, "role", "") or ""
    # [m3] 复用 _compute_user_accessible_departments 保持与 ABAC 判断一致
    allowed = _compute_user_accessible_departments(
        user_department=getattr(current_user, "department", None),
        member_departments=member_departments,
    )
    if is_admin or getattr(current_user, "can_view_all", False):
        # admin 看所有部门，用 "*" 标记，单独一个 scope
        allowed_signature = "*"
        # admin 不受 member_skill_ids 过滤，skill 维度也用 "*"
        skill_signature = "*"
    else:
        allowed_signature = ",".join(sorted(dept for dept in allowed if dept))
        # 非 admin：把 member_skill_ids 也纳入签名，避免同角色同部门但不同代管 skill 的用户串读
        skill_signature = ",".join(sorted(sid for sid in (member_skill_ids or set()) if sid))
    raw = f"{role}:{allowed_signature}:{skill_signature}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def _resolve_effective_department(
    *,
    user_department: str | None,
    requested_department: str | None,
    allowed_departments: set[str] | None,
    is_admin: bool,
) -> str | None:
    """[B1] service 层 effective_department 兜底 helper。

    规则：
    - admin/ai_engineer/director → requested（支持跨部门，由 router 负责审计）
    - 非 admin：
        - requested in allowed_departments → requested（合法跨部门代管）
        - 否则 → user_department（强制降级到自己部门，防越权）
    - requested 为空时保持原语义：admin 看全部（None）、非 admin 限定到 user_department
    """
    if is_admin:
        return requested_department
    if requested_department is None:
        return user_department
    allowed = allowed_departments or set()
    if requested_department in allowed:
        return requested_department
    # 非 admin 传入不在白名单的部门 → 降级到自己的部门（service 层兜底）
    return user_department


def _normalize_text(value: str | None) -> str:
    return (value or "").strip()


def _iter_path_segments(path: str | None) -> list[str]:
    if not path:
        return []
    return [segment for segment in path.split("/") if segment]


async def _load_org_indexes_inner(
    db: AsyncSession,
) -> tuple[dict[str, OrgUnit], dict[str, OrgUnit], set[str], dict[str, OrgUnit]]:
    """[H13] 真实数据库扫描；外层 _load_org_indexes 负责进程级 60s TTL 缓存。"""
    root_id = settings.TASKTREE_TOP_LEVEL_PARENT_ID
    all_rows = (
        await db.execute(select(OrgUnit).where(OrgUnit.type == "department"))
    ).scalars().all()
    all_by_name = {unit.name: unit for unit in all_rows if unit.name}
    lv1_rows = [unit for unit in all_rows if unit.parent_id == root_id]
    lv1_rows.sort(key=lambda unit: (unit.sort_order or 0, unit.name or ""))
    lv1_by_id = {unit.id: unit for unit in lv1_rows}
    lv1_by_name = {unit.name: unit for unit in lv1_rows if unit.name}
    lv1_names = set(lv1_by_name)
    return lv1_by_id, lv1_by_name, lv1_names, all_by_name


# H13 / v2.0.15 C3：进程级 60s TTL 缓存 + 跨 worker 版本号戳。
# - 多 worker 部署时，单进程 invalidate 只清本内存，别的 worker 仍在 TTL 内读旧索引
# - 版本号戳 `system_meta.org_units_cache_version` 由写入路径（org/service.py）bump
# - 每次读缓存前 SELECT version（< 1ms），cached_version != db_version → 强制 miss
_ORG_INDEXES_CACHE: dict[str, Any] = {
    "value": None,
    "expires_at": 0.0,
    "version": None,  # 末次 load 时的 db version，None 表示无版本号（轻量测试夹具）
}
_ORG_INDEXES_TTL: float = 60.0


async def _load_org_indexes(
    db: AsyncSession,
) -> tuple[dict[str, OrgUnit], dict[str, OrgUnit], set[str], dict[str, OrgUnit]]:
    """返回 (lv1_by_id, lv1_by_name, lv1_names, all_org_by_name)。

    H13：每次 get_tree/get_stats/_ensure_*_access 都会调本函数，原本会全表扫
    org_units WHERE type='department'。
    - 本进程走 60s TTL dict 缓存
    - 跨进程失效靠 `system_meta.org_units_cache_version`：读前 SELECT version，
      不匹配则强制重查。PG 多 worker 一致性保证来自此处。
    """
    from app.common.system_meta import ORG_UNITS_CACHE_KEY, read_cache_version

    now = time.monotonic()
    cached = _ORG_INDEXES_CACHE["value"]
    cached_version = _ORG_INDEXES_CACHE["version"]
    db_version = await read_cache_version(db, ORG_UNITS_CACHE_KEY)
    version_match = (
        # 无版本号列（SQLite 测试夹具）→ 仅靠本地 TTL
        db_version is None
        or cached_version == db_version
    )
    if (
        cached is not None
        and _ORG_INDEXES_CACHE["expires_at"] > now
        and version_match
    ):
        return cached
    value = await _load_org_indexes_inner(db)
    _ORG_INDEXES_CACHE["value"] = value
    _ORG_INDEXES_CACHE["expires_at"] = now + _ORG_INDEXES_TTL
    _ORG_INDEXES_CACHE["version"] = db_version
    return value


async def invalidate_tasktree_org_indexes() -> None:
    """H13：清空本进程内的 org_indexes 缓存。

    org_units 写入（create_org_unit / update_org_unit / delete_org_unit / merge_org /
    move_org_unit / sync_dingtalk_org）通过 `_invalidate_org_cache` 调本函数。
    跨 worker 一致性由版本号戳负责（见 `_load_org_indexes`）。
    """
    _ORG_INDEXES_CACHE["value"] = None
    _ORG_INDEXES_CACHE["expires_at"] = 0.0
    _ORG_INDEXES_CACHE["version"] = None


def _collect_descendant_department_names(
    lv1_unit: OrgUnit,
    *,
    org_by_name: dict[str, OrgUnit],
) -> set[str]:
    names = {lv1_unit.name}
    for unit in org_by_name.values():
        if not unit.name:
            continue
        if lv1_unit.id in _iter_path_segments(unit.path):
            names.add(unit.name)
    return names


def _resolve_instance_lv1(
    inst: OpenClawInstance,
    *,
    lv1_by_id: dict[str, OrgUnit],
    lv1_by_name: dict[str, OrgUnit],
    lv1_names: set[str],
    org_by_name: dict[str, OrgUnit],
) -> tuple[str, str, bool]:
    """返回 (lv1_or_virtual_id, lv1_or_virtual_name, is_virtual)。"""
    if _is_platform_machine(inst):
        TASKTREE_VIRTUAL_GROUP_HITS.labels(group="platform").inc()
        return VIRTUAL_UNASSIGNED_ID, VIRTUAL_UNASSIGNED_NAME, True
    raw = _normalize_text(inst.department)
    if not raw:
        TASKTREE_VIRTUAL_GROUP_HITS.labels(group="orphan").inc()
        return VIRTUAL_ORPHAN_ID, VIRTUAL_ORPHAN_NAME, True
    if raw in lv1_names:
        unit = lv1_by_name[raw]
        return unit.id, unit.name, False
    if raw == "AI":
        TASKTREE_VIRTUAL_GROUP_HITS.labels(group="ai").inc()
        return VIRTUAL_AI_ID, VIRTUAL_AI_NAME, True

    node = org_by_name.get(raw)
    if node is not None:
        for segment_id in _iter_path_segments(node.path):
            if segment_id in lv1_by_id:
                unit = lv1_by_id[segment_id]
                return unit.id, unit.name, False

    TASKTREE_VIRTUAL_GROUP_HITS.labels(group="orphan").inc()
    logger.info("tasktree instance routed to unassigned: instance_id={} department={}", inst.id, inst.department)
    return VIRTUAL_ORPHAN_ID, VIRTUAL_ORPHAN_NAME, True


def _resolve_user_lv1(
    user_department: str | None,
    *,
    lv1_by_id: dict[str, OrgUnit],
    lv1_names: set[str],
    org_by_name: dict[str, OrgUnit],
) -> str | None:
    """把任意 user.department 归约到 Lv1（总经办直属一级）或虚拟组 ID。

    AI 部门用户：raw=='AI' 时返回 VIRTUAL_AI_ID，让 ABAC 能识别"AI 用户访问 AI 虚拟组"
    （C5b：之前返回 None 导致 AI 用户访问自己部门 403）。
    """
    raw = _normalize_text(user_department)
    if not raw:
        return None
    if raw in lv1_names:
        return raw
    if raw in {"AI", VIRTUAL_AI_ID, VIRTUAL_AI_NAME}:
        # AI 用户没有真实 Lv1，归到 AI 虚拟组
        return VIRTUAL_AI_ID
    node = org_by_name.get(raw)
    if node is None:
        return None
    for segment_id in _iter_path_segments(node.path):
        if segment_id in lv1_by_id:
            return lv1_by_id[segment_id].name
    return None


def _normalize_requested_department(
    department: str | None,
    *,
    lv1_by_id: dict[str, OrgUnit],
    lv1_names: set[str],
    org_by_name: dict[str, OrgUnit],
) -> str | None:
    raw = _normalize_text(department)
    if not raw or raw in {"all", ALL_LV1_SLOT}:
        return None
    if raw in {VIRTUAL_AI_ID, VIRTUAL_AI_NAME, "AI"}:
        return VIRTUAL_AI_ID
    if raw in {VIRTUAL_UNASSIGNED_ID, VIRTUAL_UNASSIGNED_NAME}:
        return VIRTUAL_UNASSIGNED_ID
    if raw in {VIRTUAL_ORPHAN_ID, VIRTUAL_ORPHAN_NAME}:
        return VIRTUAL_ORPHAN_ID
    if raw in lv1_names:
        return raw
    if raw in lv1_by_id:
        return lv1_by_id[raw].name
    node = org_by_name.get(raw)
    if node is None:
        return raw
    for segment_id in _iter_path_segments(node.path):
        if segment_id in lv1_by_id:
            return lv1_by_id[segment_id].name
    return VIRTUAL_UNASSIGNED_ID


def _matches_requested_department(
    *,
    department_id: str,
    department_name: str,
    requested_department: str | None,
) -> bool:
    if requested_department in (None, "", ALL_LV1_SLOT):
        return True
    return requested_department in {department_id, department_name}


def _is_virtual_department(department: str | None) -> bool:
    return department in {
        VIRTUAL_AI_ID,
        VIRTUAL_UNASSIGNED_ID,
        VIRTUAL_ORPHAN_ID,
        VIRTUAL_AI_NAME,
        VIRTUAL_UNASSIGNED_NAME,
        VIRTUAL_ORPHAN_NAME,
    }


def _empty_tree_response() -> TaskTreeResponse:
    tree = TaskTreeResponse(
        departments=[],
        projected_at=isoformat_bjt(now_bjt()),
        etag="",
        total_online=0,
        total_offline=0,
        total_running=0,
        today_failed=0,
        today_executions=0,
    )
    tree.etag = _compute_etag(tree)
    return tree


async def _resolve_tasktree_invalidation_slots(department: str) -> set[str]:
    raw = _normalize_text(department)
    slots: set[str] = {ALL_LV1_SLOT}
    if raw:
        slots.add(raw)
    if raw in {"AI", VIRTUAL_AI_ID, VIRTUAL_AI_NAME}:
        slots.add(VIRTUAL_AI_ID)
        return slots
    if raw in {VIRTUAL_UNASSIGNED_ID, VIRTUAL_UNASSIGNED_NAME}:
        slots.add(VIRTUAL_UNASSIGNED_ID)
        return slots
    if raw in {VIRTUAL_ORPHAN_ID, VIRTUAL_ORPHAN_NAME}:
        slots.add(VIRTUAL_ORPHAN_ID)
        return slots

    try:
        from app.database import async_session_factory

        async with async_session_factory() as session:
            lv1_by_id, _lv1_by_name, lv1_names, org_by_name = await _load_org_indexes(session)
            normalized = _normalize_requested_department(
                raw,
                lv1_by_id=lv1_by_id,
                lv1_names=lv1_names,
                org_by_name=org_by_name,
            )
            if normalized:
                slots.add(normalized)
    except Exception as exc:  # noqa: BLE001
        logger.debug("tasktree invalidation slot resolution fallback: department={} err={}", department, exc)

    return {slot for slot in slots if slot}


def _window_seconds(window: str | None) -> int:
    return TASKTREE_WINDOW_SECONDS.get(window or "24h", TASKTREE_WINDOW_SECONDS["24h"])


def _run_started_in_window(
    started_at: datetime | None,
    *,
    now: datetime,
    window: str | None,
) -> bool:
    if started_at is None:
        return False
    return started_at >= now - timedelta(seconds=_window_seconds(window))


def _tree_runs_in_window(
    tree: TaskTreeResponse,
    *,
    now: datetime,
    window: str | None,
) -> list[SkillRunItem]:
    runs: list[SkillRunItem] = []
    for department in tree.departments:
        for instance in department.instances:
            for run in instance.recent_skills:
                if _run_started_in_window(run.started_at, now=now, window=window):
                    runs.append(run)
    return runs
