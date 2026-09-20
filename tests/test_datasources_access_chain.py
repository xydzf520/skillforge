from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.common.time_utils import now_bjt
from app.datasources import hall_service, router as datasource_router, service
from app.datasources.models import DataAccessGrant, DataAccessRequest, DataSource
from app.org.models import OrgUnit, UserOrgMembership


@pytest_asyncio.fixture
async def db(client):
    import app.database as db_mod

    async with db_mod.async_session_factory() as session:
        yield session


async def _mk_user(
    db: AsyncSession,
    *,
    user_id: str,
    department: str,
    role: str = "operator",
    can_view_all: bool = False,
) -> User:
    user = User(
        id=user_id,
        username=user_id,
        name=user_id,
        role=role,
        can_view_all=can_view_all,
        department=department,
        password_hash="x",
        is_active=True,
        state="active",
    )
    db.add(user)
    await db.flush()
    return user


async def _mk_source(
    db: AsyncSession,
    *,
    source_id: str,
    department: str,
    visibility: str = "department",
    owner_contact: str | None = None,
) -> DataSource:
    source = DataSource(
        id=source_id,
        name=source_id,
        department=department,
        source_type="csv_upload",
        config={},
        is_active=True,
        visibility=visibility,
        owner_contact=owner_contact,
        created_by=owner_contact,
    )
    db.add(source)
    await db.flush()
    return source


@pytest.mark.asyncio
async def test_org_membership_visibility_applies_to_hall_and_access_chain(db):
    owner = await _mk_user(db, user_id="owner-org", department="客服部")
    viewer = await _mk_user(db, user_id="viewer-org", department="客服一组")
    db.add_all(
        [
            OrgUnit(
                id="dept-root",
                name="客服部",
                type="department",
                path="/dept-root",
            ),
            OrgUnit(
                id="dept-child",
                name="客服一组",
                type="department",
                parent_id="dept-root",
                path="/dept-root/dept-child",
            ),
            UserOrgMembership(
                user_id=viewer.id,
                org_unit_id="dept-child",
                membership_type="primary",
            ),
        ]
    )
    await _mk_source(
        db,
        source_id="ds-org-lineage",
        department="客服部",
        visibility="department",
        owner_contact=owner.id,
    )
    await db.commit()

    hall = await hall_service.list_data_hall(db, viewer)
    assert [item["id"] for item in hall["items"]] == ["ds-org-lineage"]

    detail = await hall_service.get_data_hall_detail(db, viewer, "ds-org-lineage")
    assert detail["id"] == "ds-org-lineage"

    assert await service.has_data_access(
        db,
        source_id="ds-org-lineage",
        user_id=viewer.id,
        user_department=viewer.department,
    )
    await datasource_router._ensure_source_dept_access(db, "ds-org-lineage", viewer)


@pytest.mark.asyncio
async def test_list_my_pending_requests_returns_all_statuses(db):
    now = now_bjt()
    db.add_all(
        [
            DataAccessRequest(
                source_id="ds-pending",
                requester_id="u-requests",
                reason="pending request reason is long enough for this test case",
                status="pending",
                created_at=now - timedelta(minutes=4),
            ),
            DataAccessRequest(
                source_id="ds-approved",
                requester_id="u-requests",
                reason="approved request reason is long enough for this test case",
                status="approved",
                created_at=now - timedelta(minutes=3),
            ),
            DataAccessRequest(
                source_id="ds-rejected",
                requester_id="u-requests",
                reason="rejected request reason is long enough for this test case",
                status="rejected",
                created_at=now - timedelta(minutes=2),
            ),
            DataAccessRequest(
                source_id="ds-expired",
                requester_id="u-requests",
                reason="expired request reason is long enough for this test case",
                status="expired",
                created_at=now - timedelta(minutes=1),
            ),
        ]
    )
    await db.commit()

    rows = await service.list_my_pending_requests(db, current_user_id="u-requests")

    assert [row["status"] for row in rows] == ["expired", "rejected", "approved", "pending"]


@pytest.mark.asyncio
async def test_approve_request_normalizes_offset_datetime_to_bjt_naive(db):
    owner = await _mk_user(db, user_id="owner-tz", department="客服部")
    requester = await _mk_user(db, user_id="requester-tz", department="营销部")
    await _mk_source(
        db,
        source_id="ds-tz",
        department="客服部",
        visibility="private",
        owner_contact=owner.id,
    )
    request = DataAccessRequest(
        source_id="ds-tz",
        requester_id=requester.id,
        reason="timezone normalize request reason is long enough for this test",
        status="pending",
    )
    db.add(request)
    await db.commit()
    await db.refresh(request)

    resp = await datasource_router.approve_request(
        request_id=request.id,
        body=datasource_router.ApproveRequestBody(
            expires_at="2026-05-20T00:30:00+08:00",
            comment="ok",
        ),
        current_user=owner,
        db=db,
    )

    expected = datetime(2026, 5, 20, 0, 30, 0)
    grant = (
        await db.execute(
            select(DataAccessGrant).where(
                DataAccessGrant.source_id == "ds-tz",
                DataAccessGrant.grantee_user_id == requester.id,
            )
        )
    ).scalar_one()
    refreshed = (
        await db.execute(select(DataAccessRequest).where(DataAccessRequest.id == request.id))
    ).scalar_one()

    assert resp["expires_at"] == "2026-05-20T00:30:00+08:00"
    assert grant.expires_at == expected
    assert refreshed.approved_expires_at == expected


@pytest.mark.asyncio
async def test_list_data_access_grants_keeps_route_compat_shape(db):
    owner = await _mk_user(db, user_id="owner-grants", department="客服部")
    requester = await _mk_user(db, user_id="requester-grants", department="营销部")
    await _mk_source(
        db,
        source_id="ds-grants-shape",
        department="客服部",
        visibility="private",
        owner_contact=owner.id,
    )
    request = DataAccessRequest(
        source_id="ds-grants-shape",
        requester_id=requester.id,
        reason="grant shape compatibility reason is long enough for this test",
        status="pending",
    )
    db.add(request)
    await db.commit()
    await db.refresh(request)

    approve = await service.approve_data_access_request(
        db,
        request_id=request.id,
        actor_user_id=owner.id,
        actor_role=owner.role,
        comment="ok",
    )

    grants = await service.list_data_access_grants(db, "ds-grants-shape")

    assert grants["total"] == 1
    assert len(grants["items"]) == 1
    assert grants["items"][0]["id"] == approve["grant_id"]
    assert grants["items"][0]["source_id"] == "ds-grants-shape"
    assert grants["items"][0]["grantee_user_id"] == requester.id
    assert grants["items"][0]["grantee_type"] == "user"
    assert grants["items"][0]["grantee_id"] == requester.id
    assert grants["items"][0]["expires_at"] == approve["expires_at"]
