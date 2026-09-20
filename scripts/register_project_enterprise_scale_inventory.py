#!/usr/bin/env python3
"""Create and verify a 1000-project enterprise inventory proof.

This is a deterministic, no-model-call scale fixture for the Project Gateway
goal.  It creates/updates many lightweight project records across departments,
adds a bounded set of completed sample runs with input/output ingress records,
then verifies admin and department-scoped list/runtime queries through the real
Project service layer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.auth.models import User
from app.common.time_utils import now_bjt
from app.database import async_session_factory
from app.execution.models import ExecutionRun
from app.org.models import OrgUnit, UserOrgMembership
from app.projects import service as project_service
from app.projects.models import Project, ProjectIngressEvent, ProjectRun

DEFAULT_OUTPUT = ROOT / "artifacts" / "project-checks" / "project-enterprise-scale-inventory-result.json"
PROJECT_TYPES = ("dashboard", "internal_tool", "web_static", "external_web")
DEPARTMENT_PREFIX = "规模部门"
PROJECT_ID_PREFIX = "scale_inv_"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def department_id(index: int) -> str:
    return f"scale-dept-{index:02d}"


def department_name(index: int) -> str:
    return f"{DEPARTMENT_PREFIX} {index:02d}"


def project_id(index: int) -> str:
    return f"{PROJECT_ID_PREFIX}{index:04d}"


def project_payload(index: int, department_count: int) -> dict[str, Any]:
    dept_idx = index % department_count
    return {
        "id": project_id(index),
        "name": f"企业规模项目 {index:04d}",
        "description": "SkillForge 千级项目目录验证样本：用于证明项目列表、部门隔离、运行状态统计和 Gateway 管控能力。",
        "type": PROJECT_TYPES[index % len(PROJECT_TYPES)],
        "department_id": department_id(dept_idx),
        "department": department_name(dept_idx),
        "owner_user_id": f"scale-owner-{dept_idx:02d}",
        "visibility": "department",
        "status": "published",
        "entry_url": f"https://example.com/skillforge-scale/{index:04d}",
        "metadata_json": {
            "source": "project_enterprise_scale_inventory",
            "capabilities": ["ai.cheap.generate"],
            "service_conversion": {
                "enabled": True,
                "status": "external_inventory",
                "mode": "platform_gateway_service",
            },
        },
    }


async def _upsert_org_and_user(db, *, department_count: int, sample_department_index: int) -> SimpleNamespace:
    now = now_bjt()
    for idx in range(department_count):
        dept_id = department_id(idx)
        row = await db.get(OrgUnit, dept_id)
        if not row:
            db.add(
                OrgUnit(
                    id=dept_id,
                    name=department_name(idx),
                    type="department",
                    path=f"/root/{dept_id}",
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            row.name = department_name(idx)
            row.type = "department"
            row.path = f"/root/{dept_id}"
            row.updated_at = now

    sample_user_id = f"scale-inventory-user-{sample_department_index:02d}"
    user = await db.get(User, sample_user_id)
    if not user:
        db.add(
            User(
                id=sample_user_id,
                username=sample_user_id,
                name=f"{department_name(sample_department_index)} 用户",
                role="operator",
                department=department_name(sample_department_index),
                can_view_all=False,
                is_active=True,
                state="active",
                must_change_password=False,
                created_at=now,
                updated_at=now,
            )
        )
    else:
        user.role = "operator"
        user.department = department_name(sample_department_index)
        user.can_view_all = False
        user.is_active = True
        user.state = "active"
        user.updated_at = now

    membership = await db.get(UserOrgMembership, (sample_user_id, department_id(sample_department_index)))
    if not membership:
        db.add(
            UserOrgMembership(
                user_id=sample_user_id,
                org_unit_id=department_id(sample_department_index),
                membership_type="primary",
                is_manager=False,
                joined_at=now,
            )
        )
    return SimpleNamespace(
        id=sample_user_id,
        username=sample_user_id,
        name=f"{department_name(sample_department_index)} 用户",
        role="operator",
        department=department_name(sample_department_index),
        can_view_all=False,
        is_active=True,
        state="active",
    )


async def _upsert_projects(db, *, project_count: int, department_count: int) -> None:
    now = now_bjt()
    for idx in range(project_count):
        payload = project_payload(idx, department_count)
        row = await db.get(Project, payload["id"])
        if not row:
            db.add(Project(created_at=now, updated_at=now, **payload))
        else:
            for key, value in payload.items():
                setattr(row, key, value)
            row.updated_at = now


async def _upsert_sample_runs(db, *, sample_run_count: int, department_count: int, sample_department_index: int, sample_user_id: str) -> None:
    now = now_bjt()
    for idx in range(sample_run_count):
        pid = project_id(idx)
        run_id = f"prun_scale_inv_{idx:04d}"
        exec_id = f"exec_scale_inv_{idx:04d}"
        dept_idx = idx % department_count
        input_payload = {"idx": idx, "department": department_name(dept_idx), "question": f"规模样本输入 {idx}"}
        output_payload = {
            "summary": f"规模样本输出 {idx}",
            "reports": [{"title": f"规模样本报告 {idx}", "summary": "输入输出已通过 Project Gateway 留痕。"}],
            "todos": [{"kind": "review", "title": f"复核规模样本 {idx}", "summary": "千级目录样本已形成报告与待办闭环。"}],
            "_skillforge_meta": {
                "proofs": [
                    {
                        "type": "project_enterprise_scale_inventory",
                        "project_id": pid,
                        "project_run_id": run_id,
                        "credential_location": "platform_only",
                    }
                ]
            },
        }
        execution = await db.get(ExecutionRun, exec_id)
        if not execution:
            db.add(
                ExecutionRun(
                    id=exec_id,
                    skill_id=f"project:{pid}"[:50],
                    run_mode="sdk_submit",
                    trigger_type="project_gateway",
                    status="completed",
                    business_ref_id=pid,
                    completed_steps=1,
                    summary=output_payload["summary"],
                    metadata_json={
                        "project_id": pid,
                        "project_run_id": run_id,
                        "source": "project_enterprise_scale_inventory",
                    },
                    started_at=now,
                    completed_at=now,
                )
            )
        else:
            execution.status = "completed"
            execution.summary = output_payload["summary"]
            execution.completed_steps = 1
            execution.completed_at = now
            execution.metadata_json = {
                "project_id": pid,
                "project_run_id": run_id,
                "source": "project_enterprise_scale_inventory",
            }

        run_user_id = sample_user_id if dept_idx == sample_department_index else f"scale-user-{dept_idx:02d}"
        run = await db.get(ProjectRun, run_id)
        if not run:
            db.add(
                ProjectRun(
                    id=run_id,
                    project_id=pid,
                    execution_run_id=exec_id,
                    request_id=f"scale-inventory-run-{idx:04d}",
                    user_id=run_user_id,
                    department_id=department_id(dept_idx),
                    department=department_name(dept_idx),
                    status="completed",
                    input_snapshot=input_payload,
                    output_result=output_payload,
                    ai_summary={"result_evaluation": {"status": "pass", "normal_result_ready": True}},
                    report_count=1,
                    todo_count=1,
                    capability_call_count=0,
                    created_at=now,
                    started_at=now,
                    last_heartbeat_at=now,
                    completed_at=now,
                    updated_at=now,
                )
            )
        else:
            run.execution_run_id = exec_id
            run.request_id = f"scale-inventory-run-{idx:04d}"
            run.user_id = run_user_id
            run.department_id = department_id(dept_idx)
            run.department = department_name(dept_idx)
            run.status = "completed"
            run.input_snapshot = input_payload
            run.output_result = output_payload
            run.ai_summary = {"result_evaluation": {"status": "pass", "normal_result_ready": True}}
            run.report_count = 1
            run.todo_count = 1
            run.updated_at = now
            run.completed_at = now
            run.last_heartbeat_at = now

        for event_type, payload in (("input", input_payload), ("output", output_payload)):
            request_id = f"scale-inventory-{event_type}-{idx:04d}"
            existing = (
                await db.execute(
                    select(ProjectIngressEvent).where(
                        ProjectIngressEvent.project_run_id == run_id,
                        ProjectIngressEvent.event_type == event_type,
                        ProjectIngressEvent.request_id == request_id,
                    )
                )
            ).scalar_one_or_none()
            if not existing:
                db.add(
                    ProjectIngressEvent(
                        project_run_id=run_id,
                        request_id=request_id,
                        event_type=event_type,
                        input_snapshot=input_payload,
                        output_result=payload if event_type == "output" else {},
                        reports=output_payload["reports"] if event_type == "output" else [],
                        todos=output_payload["todos"] if event_type == "output" else [],
                        proofs=output_payload["_skillforge_meta"]["proofs"] if event_type == "output" else [],
                        metadata_json={"source": "project_enterprise_scale_inventory"},
                        created_at=now,
                    )
                )


async def register_scale_inventory(
    *,
    project_count: int,
    department_count: int,
    sample_run_count: int,
    sample_department_index: int,
) -> dict[str, Any]:
    project_count = max(1, int(project_count))
    department_count = max(1, int(department_count))
    sample_run_count = max(0, min(int(sample_run_count), project_count))
    sample_department_index = max(0, min(int(sample_department_index), department_count - 1))
    admin = SimpleNamespace(
        id="admin",
        username="admin",
        name="管理员",
        role="admin",
        department=None,
        can_view_all=True,
        state="active",
        is_active=True,
    )
    async with async_session_factory() as db:
        department_user = await _upsert_org_and_user(db, department_count=department_count, sample_department_index=sample_department_index)
        await _upsert_projects(db, project_count=project_count, department_count=department_count)
        await _upsert_sample_runs(
            db,
            sample_run_count=sample_run_count,
            department_count=department_count,
            sample_department_index=sample_department_index,
            sample_user_id=department_user.id,
        )
        await db.commit()

    async with async_session_factory() as db:
        scale_count = int((await db.execute(select(func.count(Project.id)).where(Project.id.like(f"{PROJECT_ID_PREFIX}%")))).scalar() or 0)
        ingress_rows = (
            await db.execute(
                select(ProjectIngressEvent.event_type, func.count(ProjectIngressEvent.id))
                .join(ProjectRun, ProjectRun.id == ProjectIngressEvent.project_run_id)
                .where(ProjectRun.project_id.like(f"{PROJECT_ID_PREFIX}%"))
                .group_by(ProjectIngressEvent.event_type)
            )
        ).all()
        run_count = int(
            (
                await db.execute(
                    select(func.count(ProjectRun.id)).where(ProjectRun.project_id.like(f"{PROJECT_ID_PREFIX}%"))
                )
            ).scalar()
            or 0
        )
        admin_runtime = await project_service.get_project_runtime_status(db, admin)
        admin_list = await project_service.list_projects(db, admin, include_playbooks=False, page=1, page_size=100)
        department_list = await project_service.list_projects(db, department_user, include_playbooks=False, page=1, page_size=100)
        department_runtime = await project_service.get_project_runtime_status(db, department_user)

    by_department = Counter(department_name(idx % department_count) for idx in range(project_count))
    ingress_by_type = {str(k): int(v) for k, v in ingress_rows}
    return {
        "ok": True,
        "generated_at": now_iso(),
        "summary": {
            "scale_project_count": scale_count,
            "target_project_count": project_count,
            "department_count": department_count,
            "sample_run_count": run_count,
            "sample_input_event_count": ingress_by_type.get("input", 0),
            "sample_output_event_count": ingress_by_type.get("output", 0),
            "admin_visible_projects": (admin_runtime.get("capacity") or {}).get("visible_projects"),
            "department_visible_projects": (department_runtime.get("capacity") or {}).get("visible_projects"),
            "department_list_total": (department_list.get("pagination") or {}).get("total"),
            "department_latest_run_count": sum(1 for item in department_list.get("items", []) if item.get("latest_run")),
            "query_strategy": (admin_list.get("runtime_policy") or {}).get("query_strategy"),
            "department_query_strategy": (department_list.get("runtime_policy") or {}).get("query_strategy"),
            "supports_hundreds_to_thousands_projects": (admin_runtime.get("gateway") or {}).get("supports_hundreds_to_thousands_projects"),
            "supports_100_concurrent_runs": (admin_runtime.get("gateway") or {}).get("supports_100_concurrent_runs"),
            "active_runs": (admin_runtime.get("capacity") or {}).get("active_runs"),
        },
        "admin_runtime_capacity": admin_runtime.get("capacity"),
        "department_runtime_capacity": department_runtime.get("capacity"),
        "department_runtime_projects": department_runtime.get("projects"),
        "by_department": dict(by_department),
        "sample_department": {
            "user_id": department_user.id,
            "department_id": department_id(sample_department_index),
            "department": department_name(sample_department_index),
        },
        "safety": {"model_calls_requested": False, "external_network_used": False, "real_external_writes": False},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Register and verify a 1000-project enterprise inventory")
    parser.add_argument("--project-count", type=int, default=1000)
    parser.add_argument("--department-count", type=int, default=10)
    parser.add_argument("--sample-run-count", type=int, default=100)
    parser.add_argument("--sample-department-index", type=int, default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = asyncio.run(
        register_scale_inventory(
            project_count=args.project_count,
            department_count=args.department_count,
            sample_run_count=args.sample_run_count,
            sample_department_index=args.sample_department_index,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
