"""任务树 Lv1 归并与 ABAC 测试。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.tasktree import service as svc_mod
from app.tasktree.schemas import SkillRunItem, SkillRunStatus

LV1_SPECS: list[tuple[str, str]] = [
    ("lv1-pmo", "PMO"),
    ("lv1-logistics", "仓储物流部"),
    ("lv1-supply", "供应链部"),
    ("lv1-it", "信息技术部"),
    ("lv1-office", "办公室"),
    ("lv1-retail", "即时零售业务部"),
    ("lv1-service", "客服部"),
    ("lv1-market", "市场部"),
    ("lv1-legal", "法务部"),
    ("lv1-rnd", "研发部"),
    ("lv1-od", "组织发展部"),
    ("lv1-design", "设计部"),
    ("lv1-finance", "财务部"),
    ("104724618", "销售一部"),
    ("lv1-sales-2", "销售二部"),
]
LV1_NAMES = [name for _, name in LV1_SPECS]
ADMIN_VISIBLE_NAMES = set(LV1_NAMES) | {
    svc_mod.VIRTUAL_AI_NAME,
    svc_mod.VIRTUAL_UNASSIGNED_NAME,
    svc_mod.VIRTUAL_ORPHAN_NAME,
}


def _make_user(*, department: str | None, role: str = "biz_owner", can_view_all: bool = False):
    return SimpleNamespace(
        id=f"user-{department or 'none'}-{role}",
        name="测试用户",
        username="tester",
        role=role,
        department=department,
        can_view_all=can_view_all,
        is_active=True,
        must_change_password=False,
    )


def _make_org_unit(
    *,
    unit_id: str,
    name: str,
    parent_id: str | None,
    path: str | None,
    sort_order: int,
):
    return SimpleNamespace(
        id=unit_id,
        name=name,
        type="department",
        parent_id=parent_id,
        path=path,
        sort_order=sort_order,
    )


def _make_instance(
    *,
    instance_id: str,
    department: str | None,
    heartbeat_age_seconds: int = 30,
    is_platform_default: bool = False,
):
    return SimpleNamespace(
        id=instance_id,
        name=f"实例-{instance_id}",
        department=department,
        agent_type="aiclaw",
        is_active=True,
        last_heartbeat=datetime.utcnow() - timedelta(seconds=heartbeat_age_seconds),
        connection_mode="bridge",
        bridge_version="1.0.0",
        bridge_platform="linux",
        is_platform_default=is_platform_default,
        bridge_capabilities_json=None,
    )


def test_platform_machine_summary_exposes_training_resources():
    instance = _make_instance(
        instance_id="platform-gpu",
        department=None,
        is_platform_default=True,
    )
    instance.bridge_capabilities_json = json.dumps({
        "memory": {"total_gb": 183.8},
        "gpu": [{
            "name": "NVIDIA RTX PRO 6000",
            "backend": "cuda",
            "vram_total_mb": 97887,
            "vram_used_mb": 40960,
        }],
        "training": {"gateway": True, "supported_tasks": ["qlora", "eval"]},
    })

    tree = _call_rollup_assemble_tree(instance=instance)
    node = next(dept for dept in tree.departments if dept.node_count).instances[0]

    assert svc_mod.VIRTUAL_UNASSIGNED_NAME == "平台主节点"
    assert node.is_platform_node is True
    assert node.machine_role == "训练 / 推理"
    assert node.gpu_name == "NVIDIA RTX PRO 6000"
    assert node.gpu_memory_used_gb == 40.0
    assert node.training_available is True
    assert node.supported_tasks == ["qlora", "eval"]


def test_dedicated_media_node_is_not_presented_as_training_gateway():
    instance = _make_instance(
        instance_id="platform-media-5080",
        department=None,
        is_platform_default=True,
    )
    instance.agent_purpose = "media"
    instance.bridge_capabilities_json = json.dumps({
        "gpu": [{
            "name": "NVIDIA GeForce RTX 5080",
            "backend": "cuda",
            "vram_total_mb": 16303,
            "vram_used_mb": 13765,
        }],
        "training": {"gateway": True, "supported_tasks": ["qlora"]},
        "media": {
            "configured": True,
            "online": True,
            "workload_roles": ["video_generation"],
            "supported_modes": ["text_to_video", "image_to_video"],
        },
    })

    tree = _call_rollup_assemble_tree(instance=instance)
    node = next(dept for dept in tree.departments if dept.node_count).instances[0]

    assert node.is_platform_node is True
    assert node.machine_role == "视频生成"
    assert node.media_available is True
    assert node.training_available is False


def _make_run(instance_id: str, status: SkillRunStatus) -> SkillRunItem:
    return SkillRunItem(
        run_id=f"run-{instance_id}-{status.value}",
        skill_id=f"skill-{instance_id}",
        skill_name=f"技能-{instance_id}",
        status=status,
    )


def _rollup_fixture() -> dict[str, object]:
    root_id = settings.TASKTREE_TOP_LEVEL_PARENT_ID
    lv1_units = [
        _make_org_unit(
            unit_id=unit_id,
            name=name,
            parent_id=root_id,
            path=f"/{root_id}/{unit_id}",
            sort_order=index,
        )
        for index, (unit_id, name) in enumerate(LV1_SPECS, start=1)
    ]
    lv1_by_id = {unit.id: unit for unit in lv1_units}
    sales1_id = next(unit_id for unit_id, name in LV1_SPECS if name == "销售一部")
    sales1 = lv1_by_id[sales1_id]

    lv2_sales = _make_org_unit(
        unit_id="896045367",
        name="对子哈特运营部",
        parent_id=sales1.id,
        path=f"/{root_id}/{sales1.id}/896045367",
        sort_order=1,
    )
    lv3_sales = _make_org_unit(
        unit_id="sales1-traditional",
        name="传统运营组",
        parent_id=lv2_sales.id,
        path=f"/{root_id}/{sales1.id}/{lv2_sales.id}/sales1-traditional",
        sort_order=1,
    )
    retail_root = _make_org_unit(
        unit_id="1033604517",
        name="新零售线下门店",
        parent_id=None,
        path="/1033604517",
        sort_order=99,
    )
    retail_child = _make_org_unit(
        unit_id="1033822431",
        name="示例企业旗舰店普陀店",
        parent_id=retail_root.id,
        path=f"/{retail_root.id}/1033822431",
        sort_order=1,
    )

    all_units = [*lv1_units, lv2_sales, lv3_sales, retail_root, retail_child]
    org_by_name = {unit.name: unit for unit in all_units}

    instances = [
        _make_instance(instance_id="inst-rnd", department="研发部"),
        _make_instance(instance_id="inst-sales-l2", department="对子哈特运营部"),
        _make_instance(instance_id="inst-sales-l3", department="传统运营组"),
        _make_instance(instance_id="inst-ai", department="AI"),
        _make_instance(instance_id="inst-none", department=None),
        _make_instance(instance_id="inst-empty", department=""),
        _make_instance(instance_id="inst-retail", department="示例企业旗舰店普陀店"),
        _make_instance(instance_id="inst-orphan", department="已删除部门"),
        _make_instance(instance_id="inst-ai-lower", department="ai"),
        _make_instance(instance_id="platform-lan", department=None, is_platform_default=True),
    ]
    instance_runs = {
        "inst-rnd": [_make_run("inst-rnd", SkillRunStatus.COMPLETED)],
        "inst-sales-l2": [_make_run("inst-sales-l2", SkillRunStatus.RUNNING)],
        "inst-sales-l3": [_make_run("inst-sales-l3", SkillRunStatus.FAILED)],
        "inst-ai": [_make_run("inst-ai", SkillRunStatus.RUNNING)],
        "inst-none": [_make_run("inst-none", SkillRunStatus.COMPLETED)],
        "inst-empty": [_make_run("inst-empty", SkillRunStatus.FAILED)],
        "inst-retail": [_make_run("inst-retail", SkillRunStatus.COMPLETED)],
        "inst-orphan": [_make_run("inst-orphan", SkillRunStatus.FAILED)],
        "inst-ai-lower": [_make_run("inst-ai-lower", SkillRunStatus.COMPLETED)],
        "platform-lan": [_make_run("platform-lan", SkillRunStatus.COMPLETED)],
    }

    return {
        "lv1_units": lv1_units,
        "lv1_by_id": lv1_by_id,
        "lv1_by_name": {unit.name: unit for unit in lv1_units},
        "lv1_names": {unit.name for unit in lv1_units},
        "org_by_name": org_by_name,
        "instances": instances,
        "instance_runs": instance_runs,
    }


def _aggregate_dept_runs(instances, instance_runs):
    dept_runs: dict[str, list[SkillRunItem]] = defaultdict(list)
    for inst in instances:
        dept_runs[inst.department or ""].extend(instance_runs.get(inst.id, []))
    return dict(dept_runs)


def _call_rollup_assemble_tree(*, instance, expected_admin: bool = True):
    fixture = _rollup_fixture()
    projection = svc_mod.TaskTreeProjection()
    signature = inspect.signature(svc_mod.TaskTreeProjection._assemble_tree)
    required = {"is_admin", "lv1_by_id", "lv1_by_name", "lv1_names", "org_by_name"}
    missing = sorted(required - set(signature.parameters))
    if missing:
        pytest.fail(f"_assemble_tree 缺少归并参数: {missing}")

    return projection._assemble_tree(
        [instance],
        _aggregate_dept_runs([instance], {}),
        department_units=fixture["lv1_units"],
        instance_runs={instance.id: []},
        status_filter="all",
        is_admin=expected_admin,
        lv1_by_id=fixture["lv1_by_id"],
        lv1_by_name=fixture["lv1_by_name"],
        lv1_names=fixture["lv1_names"],
        org_by_name=fixture["org_by_name"],
    )


@pytest.mark.parametrize(
    ("raw_department", "expected_name", "expected_id", "expected_virtual"),
    [
        ("研发部", "研发部", "lv1-rnd", False),
        ("对子哈特运营部", "销售一部", "104724618", False),
        ("传统运营组", "销售一部", "104724618", False),
        ("AI", svc_mod.VIRTUAL_AI_NAME, svc_mod.VIRTUAL_AI_ID, True),
        (None, svc_mod.VIRTUAL_ORPHAN_NAME, svc_mod.VIRTUAL_ORPHAN_ID, True),
        ("", svc_mod.VIRTUAL_ORPHAN_NAME, svc_mod.VIRTUAL_ORPHAN_ID, True),
        ("示例企业旗舰店普陀店", svc_mod.VIRTUAL_ORPHAN_NAME, svc_mod.VIRTUAL_ORPHAN_ID, True),
        ("已删除部门", svc_mod.VIRTUAL_ORPHAN_NAME, svc_mod.VIRTUAL_ORPHAN_ID, True),
        ("ai", svc_mod.VIRTUAL_ORPHAN_NAME, svc_mod.VIRTUAL_ORPHAN_ID, True),
    ],
)
def test_assemble_tree_rolls_up_instance_department(
    raw_department: str | None,
    expected_name: str,
    expected_id: str,
    expected_virtual: bool,
):
    tree = _call_rollup_assemble_tree(instance=_make_instance(instance_id="inst-case", department=raw_department))
    visible = [department for department in tree.departments if department.node_count > 0]

    assert len(visible) == 1
    department = visible[0]
    assert department.department_name == expected_name
    assert department.department_id == expected_id
    assert department.is_virtual is expected_virtual
    assert department.instances[0].department == expected_name


def _patch_rollup_tree_dependencies(monkeypatch, fixture):
    async def fake_units(self, db, departments=None):
        units = list(fixture["lv1_units"])
        if departments is None:
            return units
        allowed = set(departments)
        return [unit for unit in units if unit.name in allowed]

    async def fake_instances(self, db, *, departments=None):
        instances = list(fixture["instances"])
        if departments is None:
            return instances
        allowed = set(departments)
        return [
            inst for inst in instances
            if inst.department in allowed or svc_mod._is_platform_machine(inst)
        ]

    async def fake_recent(self, db, instances, **kwargs):
        instance_runs = {
            inst.id: list(fixture["instance_runs"].get(inst.id, []))
            for inst in instances
        }
        return instance_runs, _aggregate_dept_runs(instances, fixture["instance_runs"])

    async def fake_member_scope(self, db, *, current_user, is_admin):
        return set(), set()

    async def fake_resolve(self, db, department):
        return department

    monkeypatch.setattr(svc_mod.TaskTreeProjection, "_query_department_units", fake_units)
    monkeypatch.setattr(svc_mod.TaskTreeProjection, "_query_instances", fake_instances)
    monkeypatch.setattr(svc_mod.TaskTreeProjection, "_query_recent_runs", fake_recent)
    monkeypatch.setattr(svc_mod.TaskTreeProjection, "_get_member_scope", fake_member_scope)
    monkeypatch.setattr(svc_mod.TaskTreeProjection, "_resolve_department_name", fake_resolve)
    monkeypatch.setattr(
        svc_mod,
        "_load_org_indexes",
        AsyncMock(
            return_value=(
                fixture["lv1_by_id"],
                fixture["lv1_by_name"],
                fixture["lv1_names"],
                fixture["org_by_name"],
            )
        ),
        raising=False,
    )
    monkeypatch.setattr(svc_mod, "cache_get", AsyncMock(return_value=None))
    monkeypatch.setattr(svc_mod, "cache_set", AsyncMock(return_value=None))
    monkeypatch.setattr(svc_mod.settings, "USE_TNL_READ", False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "user_department", "can_view_all", "expected_names", "expected_counts", "expect_virtuals"),
    [
        (
            "admin",
            "研发部",
            True,
            ADMIN_VISIBLE_NAMES,
            {
                "研发部": 1,
                "销售一部": 2,
                svc_mod.VIRTUAL_AI_NAME: 1,
                svc_mod.VIRTUAL_UNASSIGNED_NAME: 1,
                svc_mod.VIRTUAL_ORPHAN_NAME: 5,
            },
            True,
        ),
        (
            "biz_owner",
            "研发部",
            False,
            {"研发部", svc_mod.VIRTUAL_UNASSIGNED_NAME},
            {"研发部": 1, svc_mod.VIRTUAL_UNASSIGNED_NAME: 1},
            True,
        ),
        (
            "biz_owner",
            "对子哈特运营部",
            False,
            {"销售一部", svc_mod.VIRTUAL_UNASSIGNED_NAME},
            {"销售一部": 2, svc_mod.VIRTUAL_UNASSIGNED_NAME: 1},
            True,
        ),
        (
            "biz_owner",
            "传统运营组",
            False,
            {"销售一部", svc_mod.VIRTUAL_UNASSIGNED_NAME},
            {"销售一部": 2, svc_mod.VIRTUAL_UNASSIGNED_NAME: 1},
            True,
        ),
        (
            "biz_owner",
            "示例企业旗舰店普陀店",
            False,
            {svc_mod.VIRTUAL_UNASSIGNED_NAME},
            {svc_mod.VIRTUAL_UNASSIGNED_NAME: 1},
            True,
        ),
        (
            "biz_owner",
            "AI",
            False,
            {svc_mod.VIRTUAL_UNASSIGNED_NAME},
            {svc_mod.VIRTUAL_UNASSIGNED_NAME: 1},
            True,
        ),
        (
            "biz_owner",
            "销售一部",
            False,
            {"销售一部", svc_mod.VIRTUAL_UNASSIGNED_NAME},
            {"销售一部": 2, svc_mod.VIRTUAL_UNASSIGNED_NAME: 1},
            True,
        ),
    ],
)
async def test_get_tree_rollup_abac(
    monkeypatch,
    role: str,
    user_department: str | None,
    can_view_all: bool,
    expected_names: set[str],
    expected_counts: dict[str, int],
    expect_virtuals: bool,
):
    fixture = _rollup_fixture()
    _patch_rollup_tree_dependencies(monkeypatch, fixture)

    tree = await svc_mod.TaskTreeProjection().get_tree(
        db=object(),
        current_user=_make_user(
            department=user_department,
            role=role,
            can_view_all=can_view_all,
        ),
    )

    department_names = {department.department_name for department in tree.departments}
    assert len(tree.departments) == len(expected_names)
    assert department_names == expected_names

    for name, expected_count in expected_counts.items():
        department = next(item for item in tree.departments if item.department_name == name)
        assert department.node_count == expected_count

    assert any(department.is_virtual for department in tree.departments) is expect_virtuals
    if not expect_virtuals:
        assert svc_mod.VIRTUAL_AI_NAME not in department_names
        assert svc_mod.VIRTUAL_UNASSIGNED_NAME not in department_names
        assert svc_mod.VIRTUAL_ORPHAN_NAME not in department_names
