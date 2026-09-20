"""C3：跨同前缀部门 lineage 回归测试。

历史背景：v2.0.12 `_compute_visible_skill_ids` 用 `path.startswith()` 比对部门
路径，对 `RT/EC` 与 `RT/ECX` 这种"前缀相同但段落不同"的部门会误匹配，导致
跨部门数据泄露。v2.0.13 引入 `_path_is_in_lineage`（两侧补尾斜杠）已修，
本测试做端到端 + 单元两层回归，防止未来回退。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import app.database as db_mod
from app.auth.dependencies import get_current_user
from app.common.exceptions import AppError, app_error_handler
from app.execution.models import DecisionLog, ExecutionRun
from app.inbox.router import router as inbox_router
from app.inbox.service import (
    _compute_visible_skill_ids,
    _path_is_in_lineage,
)
from app.org.models import OrgUnit, UserOrgMembership
from app.skills.core.models import Skill
from tests.test_inbox_reports import _make_user


# ============================================================================
# 单元层：_path_is_in_lineage 字符串语义
# ============================================================================


def test_path_lineage_same_path_returns_true():
    assert _path_is_in_lineage("RT/EC", "RT/EC") is True


def test_path_lineage_descendant_in_target_returns_true():
    """user="RT/EC"（祖先），target="RT/EC/sub"（子孙）：能看子孙的报告。"""
    assert _path_is_in_lineage("RT/EC", "RT/EC/sub") is True


def test_path_lineage_descendant_in_user_returns_true():
    """user="RT/EC/sub"（子孙），target="RT/EC"（祖先）：能看祖先级公共报告。"""
    assert _path_is_in_lineage("RT/EC/sub", "RT/EC") is True


def test_path_lineage_cross_sibling_with_shared_prefix_returns_false():
    """关键回归：RT/EC 与 RT/ECX 是兄弟（共享前缀但段落不同）→ 不可见。"""
    assert _path_is_in_lineage("RT/EC", "RT/ECX") is False
    assert _path_is_in_lineage("RT/ECX", "RT/EC") is False


def test_path_lineage_handles_trailing_slash():
    """补尾斜杠后比对的健壮性：传入带/不带尾斜杠都同义。"""
    assert _path_is_in_lineage("RT/EC/", "RT/EC") is True
    assert _path_is_in_lineage("RT/EC", "RT/EC/") is True
    assert _path_is_in_lineage("RT/EC/", "RT/ECX/") is False


def test_path_lineage_empty_inputs_return_false():
    assert _path_is_in_lineage("", "RT/EC") is False
    assert _path_is_in_lineage("RT/EC", "") is False
    assert _path_is_in_lineage("", "") is False


def test_path_lineage_unrelated_paths_return_false():
    assert _path_is_in_lineage("RT/EC", "FIN/HR") is False


# ============================================================================
# 端到端层：HTTP /api/inbox/reports 路由验证
# ============================================================================


async def _seed_cross_prefix_orgs() -> dict[str, object]:
    """造 RT / RT/EC / RT/ECX / RT/ECY 四个 org，配两个用户与两个 Skill。

    用一个 session + commit + 显式 expunge_all + close，避免 session-bound 对象
    残留在 identity_map 引发 conftest 后续 fixture 重建时的连接持有。
    """
    async with db_mod.async_session_factory() as s:
        s.add_all(
            [
                OrgUnit(id="rt", name="RT", type="company", path="RT"),
                OrgUnit(id="rt-ec", name="EC", type="department", parent_id="rt", path="RT/EC"),
                OrgUnit(id="rt-ecx", name="ECX", type="department", parent_id="rt", path="RT/ECX"),
                OrgUnit(id="rt-ecy", name="ECY", type="department", parent_id="rt", path="RT/ECY"),
                _make_user("alice_ec", role="dept_admin", department="EC"),
                _make_user("bob_ecx", role="dept_admin", department="ECX"),
                UserOrgMembership(user_id="alice_ec", org_unit_id="rt-ec", is_manager=True),
                UserOrgMembership(user_id="bob_ecx", org_unit_id="rt-ecx", is_manager=True),
                Skill(
                    id="skill-ec",
                    name="EC Skill",
                    description="EC",
                    department="EC",
                    org_unit_id="rt-ec",
                    visibility="department",
                    status="active",
                ),
                Skill(
                    id="skill-ecx",
                    name="ECX Skill",
                    description="ECX",
                    department="ECX",
                    org_unit_id="rt-ecx",
                    visibility="department",
                    status="active",
                ),
            ]
        )
        await s.commit()
        s.expunge_all()

    # 重新构造分离的 User stub 给 _compute_visible_skill_ids / 路由调用，
    # 不依赖 session identity（避免跨 session 引用激活 lazy attributes）。
    alice = _make_user("alice_ec", role="dept_admin", department="EC")
    bob = _make_user("bob_ecx", role="dept_admin", department="ECX")
    return {"alice": alice, "bob": bob}


async def _seed_cross_prefix_runs_and_logs() -> None:
    async with db_mod.async_session_factory() as s:
        s.add_all(
            [
                ExecutionRun(
                    id="run-ec",
                    trigger_type="cron",
                    status="completed",
                    started_at=datetime(2026, 4, 17, 8, 0, 0),
                ),
                ExecutionRun(
                    id="run-ecx",
                    trigger_type="cron",
                    status="completed",
                    started_at=datetime(2026, 4, 17, 8, 0, 0),
                ),
                DecisionLog(
                    skill_id="skill-ec",
                    run_id="run-ec",
                    input_snapshot={"d": "x"},
                    output_result={
                        "reports": [
                            {
                                "channel": "dingtalk_card",
                                "title": "EC report",
                                "summary": "EC",
                            }
                        ]
                    },
                    is_sandbox=False,
                    created_at=datetime(2026, 4, 17, 9, 0, 0),
                ),
                DecisionLog(
                    skill_id="skill-ecx",
                    run_id="run-ecx",
                    input_snapshot={"d": "x"},
                    output_result={
                        "reports": [
                            {
                                "channel": "dingtalk_card",
                                "title": "ECX report",
                                "summary": "ECX",
                            }
                        ]
                    },
                    is_sandbox=False,
                    created_at=datetime(2026, 4, 17, 9, 0, 0),
                ),
            ]
        )
        await s.commit()
        s.expunge_all()


@pytest_asyncio.fixture
async def cross_prefix_client(client):
    """复用 conftest 的 PG client，但替换 inbox 路由 + 可切换 current_user。"""
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    state: dict[str, object] = {"current_user": None}
    app.dependency_overrides[get_current_user] = lambda: state["current_user"]
    app.include_router(inbox_router, prefix="/api/inbox")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, state


@pytest.mark.asyncio
async def test_compute_visible_skill_ids_isolates_cross_prefix_dept_admin(client):
    """单元层：直接调 _compute_visible_skill_ids，断言两个 dept_admin 互看不到。"""
    users = await _seed_cross_prefix_orgs()
    await _seed_cross_prefix_runs_and_logs()

    async with db_mod.async_session_factory() as s:
        alice_visible = await _compute_visible_skill_ids(s, users["alice"])
        bob_visible = await _compute_visible_skill_ids(s, users["bob"])

    assert "skill-ec" in alice_visible
    assert "skill-ecx" not in alice_visible, "alice (RT/EC) 不应看到 RT/ECX 的 Skill"

    assert "skill-ecx" in bob_visible
    assert "skill-ec" not in bob_visible, "bob (RT/ECX) 不应看到 RT/EC 的 Skill"


@pytest.mark.asyncio
async def test_inbox_reports_isolates_cross_prefix_alice(cross_prefix_client):
    """端到端：alice 只能看到 RT/EC 的报告。"""
    http, state = cross_prefix_client
    users = await _seed_cross_prefix_orgs()
    await _seed_cross_prefix_runs_and_logs()

    state["current_user"] = users["alice"]
    resp = await http.get("/api/inbox/reports")
    assert resp.status_code == 200
    skills_in_view = {item["skill_id"] for item in resp.json()["items"]}
    assert "skill-ec" in skills_in_view
    assert "skill-ecx" not in skills_in_view


@pytest.mark.asyncio
async def test_inbox_reports_isolates_cross_prefix_bob(cross_prefix_client):
    """端到端：bob 只能看到 RT/ECX 的报告。"""
    http, state = cross_prefix_client
    users = await _seed_cross_prefix_orgs()
    await _seed_cross_prefix_runs_and_logs()

    state["current_user"] = users["bob"]
    resp = await http.get("/api/inbox/reports")
    assert resp.status_code == 200
    skills_in_view = {item["skill_id"] for item in resp.json()["items"]}
    assert "skill-ecx" in skills_in_view
    assert "skill-ec" not in skills_in_view


# ============================================================================
# M3：_compute_visible_skill_ids 空 candidate 边界
# ============================================================================


async def _seed_dept_admin_without_membership() -> dict[str, object]:
    """造一个 dept_admin 用户，但没有任何 UserOrgMembership 记录。

    边界场景：管理员误把人挂成 dept_admin role 但没分配部门 → managed_org_subtree=[]。
    再造三个 Skill：
      - skill-pub  visibility='company' → 应可见
      - skill-dep  visibility='department' org_unit_id='rt-ec' → 应不可见（无 dept 关系）
      - skill-priv visibility='private' org_unit_id='rt-ec' member=alice_orphan → 应可见（按 SkillMember）
    """
    from app.skills.members import SkillMember

    async with db_mod.async_session_factory() as s:
        s.add_all(
            [
                OrgUnit(id="rt-ec", name="EC", type="department", parent_id=None, path="RT/EC"),
                _make_user("alice_orphan", role="dept_admin", department=None),
                Skill(
                    id="skill-pub",
                    name="Pub",
                    description="pub",
                    department="EC",
                    org_unit_id="rt-ec",
                    visibility="company",
                    status="active",
                ),
                Skill(
                    id="skill-dep",
                    name="Dep",
                    description="dep",
                    department="EC",
                    org_unit_id="rt-ec",
                    visibility="department",
                    status="active",
                ),
                Skill(
                    id="skill-priv",
                    name="Priv",
                    description="priv",
                    department="EC",
                    org_unit_id="rt-ec",
                    visibility="private",
                    status="active",
                ),
            ]
        )
        await s.flush()  # 让 Skill 主键先入库，再加 SkillMember（FK 依赖）
        s.add(SkillMember(skill_id="skill-priv", user_id="alice_orphan", role="reviewer"))
        await s.commit()
        s.expunge_all()

    alice = _make_user("alice_orphan", role="dept_admin", department=None)
    return {"alice": alice}


@pytest.mark.asyncio
async def test_visible_skills_dept_admin_with_empty_membership_only_sees_company_and_member(
    client,
):
    """M3：dept_admin 但 managed_org_subtree=[] →
    - 仍能看到 visibility='company' 的 Skill（与部门无关）
    - 仍能看到自己作为 SkillMember 的 private Skill
    - 不能看到任何 visibility='department' 的 Skill（无任何 dept 关系，符合预期）
    """
    users = await _seed_dept_admin_without_membership()
    async with db_mod.async_session_factory() as s:
        visible = await _compute_visible_skill_ids(s, users["alice"])
    assert "skill-pub" in visible
    assert "skill-priv" in visible
    assert "skill-dep" not in visible


@pytest.mark.asyncio
async def test_visible_skills_pure_observer_without_membership_only_sees_company(client):
    """M3 续：普通 observer 没 membership 也没 dept_admin → 只能看 company。"""
    async with db_mod.async_session_factory() as s:
        s.add_all(
            [
                OrgUnit(id="rt-ec", name="EC", type="department", parent_id=None, path="RT/EC"),
                _make_user("nobody", role="observer", department=None),
                Skill(
                    id="skill-pub2",
                    name="Pub2",
                    description="pub2",
                    department="EC",
                    org_unit_id="rt-ec",
                    visibility="company",
                    status="active",
                ),
                Skill(
                    id="skill-dep2",
                    name="Dep2",
                    description="dep2",
                    department="EC",
                    org_unit_id="rt-ec",
                    visibility="department",
                    status="active",
                ),
                Skill(
                    id="skill-priv2",
                    name="Priv2",
                    description="priv2",
                    department="EC",
                    org_unit_id="rt-ec",
                    visibility="private",
                    status="active",
                ),
            ]
        )
        await s.commit()
        s.expunge_all()

    observer = _make_user("nobody", role="observer", department=None)
    async with db_mod.async_session_factory() as s:
        visible = await _compute_visible_skill_ids(s, observer)
    assert visible == ["skill-pub2"]
