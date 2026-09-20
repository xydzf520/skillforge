import asyncio

import pytest
from sqlalchemy import func, select

from app.auth.models import User
from app.execution.models import DecisionLog, ExecutionRun
from app.learning.models import LearningEvent
from app.org.models import OrgUnit, UserOrgMembership
from app.projects.models import Project, ProjectCapabilityCall, ProjectIngressEvent, ProjectRun


@pytest.mark.asyncio
async def test_project_100_concurrent_runs_keep_department_io_and_ai_loop(client, monkeypatch):
    """Enterprise scale proof: 100 browser runs can all enter the platform loop.

    Each generated page run opens, records input through Project Gateway, ingests
    output with auto_analyze enabled, and produces one platform AI capability
    call.  This verifies the requested "部门输入输出经过系统并进入 AI 循环"
    behavior at the 100-concurrent target, not only one demo run.
    """

    from app.projects import service as project_service

    monkeypatch.setattr(project_service.settings, "PROJECT_TARGET_CONCURRENT_RUNS", 100)

    ai_calls: list[dict] = []

    async def fake_platform_ai(db, user, args, *, effective_skill_id, effective_run_id):
        ai_calls.append(
            {
                "run_id": effective_run_id,
                "prompt": args.get("prompt"),
                "project_id": args.get("context_pack", {}).get("project", {}).get("id"),
                "department": args.get("context_pack", {}).get("project", {}).get("department"),
            }
        )
        return {
            "ok": True,
            "model": "scale-loop-model",
            "output": f"AI 已分析 {effective_run_id}",
            "prompt_hash": f"scale-hash-{len(ai_calls)}",
            "credential_location": "platform_only",
        }

    monkeypatch.setattr(project_service.codex_service, "_builtin_platform_ai_analyze", fake_platform_ai)

    created = await client.post(
        "/api/projects/",
        json={
            "id": "scale_loop_page",
            "name": "100 并发闭环页面",
            "type": "dashboard",
            "entry_url": "https://example.com/scale-loop",
            "department_id": "dept-ai",
            "department": "AI小组",
            "metadata": {"capabilities": ["ai.generate", "ai.cheap.generate"]},
        },
    )
    assert created.status_code == 200, created.text

    async def open_run(idx: int):
        resp = await client.post(
            "/api/projects/scale_loop_page/runs",
            json={"request_id": f"scale-open-{idx:03d}", "params": {"idx": idx, "department": "AI小组"}},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    runs = await asyncio.gather(*(open_run(idx) for idx in range(100)))
    assert len({run["id"] for run in runs}) == 100
    assert len({run["execution_run_id"] for run in runs}) == 100

    async def record_input(idx: int, run: dict):
        resp = await client.post(
            f"/api/projects/runs/{run['id']}/input",
            json={
                "request_id": f"scale-input-{idx:03d}",
                "input": {"idx": idx, "query": f"部门输入 {idx}"},
                "metadata": {"source": "scale_test", "department": "AI小组"},
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    input_results = await asyncio.gather(*(record_input(idx, run) for idx, run in enumerate(runs)))
    assert all(result["input_event_id"] for result in input_results)

    async def ingest_output(idx: int, run: dict):
        resp = await client.post(
            f"/api/projects/runs/{run['id']}/ingest",
            json={
                "request_id": f"scale-output-{idx:03d}",
                "input": {"idx": idx, "query": f"部门输入 {idx}"},
                "output": {"summary": f"部门输出 {idx}", "score": idx},
                "reports": [{"title": f"规模报告 {idx}", "summary": f"部门输出 {idx} 已进入报告"}],
                "metadata": {"source": "scale_test", "department": "AI小组"},
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    output_results = await asyncio.gather(*(ingest_output(idx, run) for idx, run in enumerate(runs)))
    assert all(result["status"] == "ai_completed" for result in output_results)
    assert all(result["analysis"]["ok"] is True for result in output_results)
    assert len(ai_calls) == 100
    assert {call["project_id"] for call in ai_calls} == {"scale_loop_page"}
    assert {call["department"] for call in ai_calls} == {"AI小组"}

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        run_count = (
            await db.execute(select(func.count(ProjectRun.id)).where(ProjectRun.project_id == "scale_loop_page"))
        ).scalar()
        ai_completed_count = (
            await db.execute(
                select(func.count(ProjectRun.id)).where(
                    ProjectRun.project_id == "scale_loop_page",
                    ProjectRun.department_id == "dept-ai",
                    ProjectRun.department == "AI小组",
                    ProjectRun.status == "ai_completed",
                )
            )
        ).scalar()
        input_count = (
            await db.execute(
                select(func.count(ProjectIngressEvent.id))
                .join(ProjectRun, ProjectRun.id == ProjectIngressEvent.project_run_id)
                .where(ProjectRun.project_id == "scale_loop_page", ProjectIngressEvent.event_type == "input")
            )
        ).scalar()
        output_count = (
            await db.execute(
                select(func.count(ProjectIngressEvent.id))
                .join(ProjectRun, ProjectRun.id == ProjectIngressEvent.project_run_id)
                .where(ProjectRun.project_id == "scale_loop_page", ProjectIngressEvent.event_type == "output")
            )
        ).scalar()
        capability_count = (
            await db.execute(
                select(func.count(ProjectCapabilityCall.id))
                .join(ProjectRun, ProjectRun.id == ProjectCapabilityCall.project_run_id)
                .where(
                    ProjectRun.project_id == "scale_loop_page",
                    ProjectCapabilityCall.capability_key == "ai.analyze",
                    ProjectCapabilityCall.status == "completed",
                )
            )
        ).scalar()
        decision_count = (
            await db.execute(
                select(func.count(DecisionLog.id))
                .join(ExecutionRun, ExecutionRun.id == DecisionLog.run_id)
                .where(ExecutionRun.business_ref_id == "scale_loop_page")
            )
        ).scalar()
        learning_by_type = dict(
            (
                await db.execute(
                    select(LearningEvent.source_type, func.count(LearningEvent.id))
                    .where(
                        LearningEvent.source_type.in_(
                            [
                                "project_run",
                                "project_ingress_event",
                                "project_capability_call",
                                "decision_log",
                                "execution_run",
                            ]
                        )
                    )
                    .group_by(LearningEvent.source_type)
                )
            ).all()
        )

    assert run_count == 100
    assert ai_completed_count == 100
    assert input_count == 100
    assert output_count == 100
    assert capability_count == 100
    assert decision_count == 100
    assert learning_by_type["project_run"] >= 100
    assert learning_by_type["project_ingress_event"] >= 200
    assert learning_by_type["project_capability_call"] >= 100
    assert learning_by_type["decision_log"] >= 100
    assert learning_by_type["execution_run"] >= 100


@pytest.mark.asyncio
async def test_project_thousand_inventory_isolates_department_visibility_and_run_stats(client):
    """千级项目目录下，部门用户只能看本部门项目和自己的运行态。

    This guards the enterprise requirement that hundreds/thousands of projects
    can coexist while department input/output/run statistics do not leak across
    departments or users.
    """

    from types import SimpleNamespace

    from app.common.time_utils import now_bjt
    from app.projects import service as project_service

    import app.database as db_mod

    now = now_bjt()
    current_user = SimpleNamespace(
        id="dept-user-03",
        username="dept-user-03",
        name="部门 03 用户",
        role="operator",
        department="部门 03",
        can_view_all=False,
        is_active=True,
        state="active",
    )
    async with db_mod.async_session_factory() as db:
        db.add(
            User(
                id=current_user.id,
                username=current_user.username,
                name=current_user.name,
                role=current_user.role,
                department=current_user.department,
                can_view_all=False,
                is_active=True,
                state="active",
                must_change_password=False,
            )
        )
        db.add_all(
            [
                OrgUnit(
                    id=f"dept-{dept:02d}",
                    name=f"部门 {dept:02d}",
                    type="department",
                    path=f"/root/dept-{dept:02d}",
                    created_at=now,
                    updated_at=now,
                )
                for dept in range(10)
            ]
        )
        db.add(
            UserOrgMembership(
                user_id=current_user.id,
                org_unit_id="dept-03",
                membership_type="primary",
                is_manager=False,
                joined_at=now,
            )
        )
        db.add_all(
            [
                Project(
                    id=f"tenant_proj_{idx:04d}",
                    name=f"租户规模项目 {idx:04d}",
                    type="dashboard" if idx % 2 else "external_web",
                    department_id=f"dept-{idx % 10:02d}",
                    department=f"部门 {idx % 10:02d}",
                    owner_user_id=f"owner-{idx % 10:02d}",
                    visibility="department",
                    status="published",
                    entry_url=f"https://example.com/tenant/{idx}",
                    metadata_json={},
                    created_at=now,
                    updated_at=now,
                )
                for idx in range(1000)
            ]
        )
        db.add_all(
            [
                ExecutionRun(
                    id=f"exec_tenant_{idx:03d}",
                    skill_id=f"project:tenant_proj_{idx:04d}",
                    run_mode="sdk_submit",
                    trigger_type="project_gateway",
                    status="running",
                    business_ref_id=f"tenant_proj_{idx:04d}",
                    metadata_json={
                        "project_id": f"tenant_proj_{idx:04d}",
                        "user_id": current_user.id if idx % 10 == 3 else f"other-user-{idx % 10:02d}",
                        "department_id": f"dept-{idx % 10:02d}",
                        "department": f"部门 {idx % 10:02d}",
                    },
                    started_at=now,
                )
                for idx in range(100)
            ]
        )
        db.add_all(
            [
                ProjectRun(
                    id=f"prun_tenant_{idx:03d}",
                    project_id=f"tenant_proj_{idx:04d}",
                    execution_run_id=f"exec_tenant_{idx:03d}",
                    request_id=f"tenant-open-{idx:03d}",
                    user_id=current_user.id if idx % 10 == 3 else f"other-user-{idx % 10:02d}",
                    department_id=f"dept-{idx % 10:02d}",
                    department=f"部门 {idx % 10:02d}",
                    status="running",
                    input_snapshot={"idx": idx, "department": f"部门 {idx % 10:02d}"},
                    output_result={},
                    report_count=0,
                    todo_count=0,
                    capability_call_count=0,
                    created_at=now,
                    started_at=now,
                    last_heartbeat_at=now,
                    updated_at=now,
                )
                for idx in range(100)
            ]
        )
        await db.commit()

    async with db_mod.async_session_factory() as db:
        listed = await project_service.list_projects(db, current_user, include_playbooks=False, page=1, page_size=100)
        runtime = await project_service.get_project_runtime_status(db, current_user)

    assert listed["pagination"] == {"page": 1, "page_size": 100, "total": 100, "has_more": False}
    assert {item["department_id"] for item in listed["items"]} == {"dept-03"}
    assert listed["stats"]["by_department"] == {"部门 03": 100}
    assert listed["runtime_policy"]["query_strategy"] == "server_side_latest_run_page"
    assert sum(1 for item in listed["items"] if item.get("latest_run")) == 10
    assert {
        item["latest_run"]["id"]
        for item in listed["items"]
        if item.get("latest_run")
    } == {f"prun_tenant_{idx:03d}" for idx in range(3, 100, 10)}

    assert runtime["capacity"]["visible_projects"] == 100
    assert runtime["capacity"]["active_runs"] == 10
    assert runtime["runs"]["by_status"] == {"running": 10}
    assert runtime["projects"]["by_department"] == {"部门 03": 100}
    assert runtime["gateway"]["supports_hundreds_to_thousands_projects"] is True
