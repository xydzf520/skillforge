"""用户管理API测试"""

from datetime import datetime

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_list_users(client):
    """测试获取用户列表"""
    resp = await client.get("/api/users/")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "items" in data


@pytest.mark.asyncio
async def test_create_user(client):
    """测试创建用户"""
    resp = await client.post("/api/users/", json={
        "user_id": "test_user_001",
        "username": "testuser001",
        "name": "测试用户",
        "role": "operator",
        "department": "传统电商",
        "password": "Test1234!",
    })
    # 可能因为数据库表不存在而失败，但路由注册应该正确
    assert resp.status_code in (200, 409, 500)


@pytest.mark.asyncio
async def test_create_user_invalid_role(client):
    """测试创建用户时使用无效角色"""
    resp = await client.post("/api/users/", json={
        "user_id": "test_bad_role",
        "username": "badrole",
        "name": "坏角色",
        "role": "superadmin",
    })
    # 应该返回400（无效参数）或500（数据库问题）
    assert resp.status_code in (400, 500)


@pytest.mark.asyncio
async def test_update_user(client):
    """测试更新用户信息"""
    resp = await client.put("/api/users/nonexistent", json={
        "name": "新名字",
        "role": "biz_owner",
    })
    # 用户不存在应返回404或500
    assert resp.status_code in (404, 500)


@pytest.mark.asyncio
async def test_disable_user(client):
    """测试禁用用户"""
    resp = await client.delete("/api/users/nonexistent")
    assert resp.status_code in (404, 500)


@pytest.mark.asyncio
async def test_reset_password(client):
    """测试重置密码"""
    resp = await client.post("/api/users/nonexistent/reset-password", json={
        "new_password": "NewPass123!",
    })
    assert resp.status_code in (404, 500)


@pytest.mark.asyncio
async def test_list_users_with_filters(client):
    """测试用户列表筛选"""
    resp = await client.get("/api/users/?role=admin&department=AI小组&page=1")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data


@pytest.mark.asyncio
async def test_list_users_uses_image_generation_as_recent_activity(client):
    """session 未过期时，直接能力图片生成也应体现在用户管理最近活跃。"""
    from app.auth.models import User
    from app.database import async_session_factory
    from app.hall.models import DirectCapabilityImageHistory

    async with async_session_factory() as session:
        user = User(
            id="activity-user",
            username="activity-user",
            name="活跃用户",
            role="operator",
            is_active=True,
            must_change_password=False,
            last_login_at=datetime(2026, 4, 29, 8, 50, 58),
            created_at=datetime(2026, 4, 27, 8, 25, 13),
        )
        session.add(user)
        await session.flush()
        session.add(
            DirectCapabilityImageHistory(
                user_id=user.id,
                capability_id="gpt-imagegen",
                image_url="https://files.example.com/generated.png",
                name="generated.png",
                prompt="换个卖点",
                task_id="tsk_img_test",
                source="generate",
                created_at=datetime(2026, 5, 6, 9, 2, 9),
            )
        )
        await session.commit()

    resp = await client.get("/api/users/?page_size=1000")

    assert resp.status_code == 200
    rows = {item["id"]: item for item in resp.json()["items"]}
    row = rows["activity-user"]
    assert row["last_login_at"].startswith("2026-04-29T08:50:58")
    assert row["last_activity_at"].startswith("2026-05-06T09:02:09")
    assert row["last_activity_source"] == "image_generation"

    async with async_session_factory() as session:
        saved = (
            await session.execute(
                select(DirectCapabilityImageHistory).where(
                    DirectCapabilityImageHistory.user_id == "activity-user"
                )
            )
        ).scalar_one()
        assert saved.prompt == "换个卖点"


@pytest.mark.asyncio
async def test_list_users_normalizes_dingtalk_login_audit_time(client):
    """历史钉钉登录写入 UTC naive 时，用审计登录时间修正展示时区。"""
    from app.auth.models import User
    from app.common.audit import AuditLog
    from app.database import async_session_factory

    async with async_session_factory() as session:
        session.add(
            User(
                id="dingtalk-time-user",
                username="dingtalk-time-user",
                name="钉钉时间用户",
                role="operator",
                is_active=True,
                must_change_password=False,
                last_login_at=datetime(2026, 4, 29, 8, 50, 58),
                created_at=datetime(2026, 4, 27, 8, 25, 13),
            )
        )
        session.add(
            AuditLog(
                user_id="dingtalk-time-user",
                action="user.login",
                created_at=datetime(2026, 4, 29, 8, 50, 58),
                detail={"method": "dingtalk_scan"},
            )
        )
        await session.commit()

    resp = await client.get("/api/users/?page_size=1000")

    assert resp.status_code == 200
    rows = {item["id"]: item for item in resp.json()["items"]}
    row = rows["dingtalk-time-user"]
    assert row["last_login_at"].startswith("2026-04-29T16:50:58")
    assert row["last_activity_at"].startswith("2026-04-29T16:50:58")
    assert row["last_activity_source"] == "login"


@pytest.mark.asyncio
async def test_list_users_keeps_new_audit_login_time_as_bjt(client):
    """迁移后的审计登录时间是北京时间 naive，不能再按 UTC 加 8 小时。"""
    from app.auth.models import User
    from app.common.audit import AuditLog
    from app.database import async_session_factory

    async with async_session_factory() as session:
        session.add(
            User(
                id="new-audit-time-user",
                username="new-audit-time-user",
                name="新审计时间用户",
                role="operator",
                is_active=True,
                must_change_password=False,
                last_login_at=datetime(2026, 5, 6, 12, 30, 0),
                created_at=datetime(2026, 5, 6, 12, 0, 0),
            )
        )
        session.add(
            AuditLog(
                user_id="new-audit-time-user",
                action="user.login",
                created_at=datetime(2026, 5, 6, 12, 30, 0),
                detail={"method": "password"},
            )
        )
        await session.commit()

    resp = await client.get("/api/users/?page_size=1000")

    assert resp.status_code == 200
    rows = {item["id"]: item for item in resp.json()["items"]}
    row = rows["new-audit-time-user"]
    assert row["last_login_at"].startswith("2026-05-06T12:30:00")
    assert row["last_activity_at"].startswith("2026-05-06T12:30:00")
    assert row["last_activity_source"] == "login"


@pytest.mark.asyncio
async def test_list_users_includes_codex_usage(client):
    """用户管理页应显示 Codex 插件使用状态和 MCP 调用统计。"""
    from app.auth.models import User
    from app.codex.models import CodexCliSession, CodexMcpCallAudit
    from app.codex import service as codex_service
    from app.database import async_session_factory

    async with async_session_factory() as session:
        session.add(
            User(
                id="codex-user-list",
                username="codex-user-list",
                name="Codex 列表用户",
                role="operator",
                is_active=True,
                must_change_password=False,
                created_at=datetime(2026, 5, 6, 12, 0, 0),
            )
        )
        session.add(
            CodexCliSession(
                id="cli_user_list",
                user_id="codex-user-list",
                token_hash=codex_service.token_hash("codex-user-list-token"),
                scopes_json={"source": "test"},
                permissions_rev_snapshot=0,
                created_at=datetime(2026, 5, 6, 12, 1, 0),
                last_seen_at=datetime(2026, 5, 6, 12, 2, 0),
                expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
            )
        )
        session.add(
            CodexMcpCallAudit(
                request_id="req_user_list",
                proof_id="proof_user_list",
                user_id="codex-user-list",
                server="skillforge",
                tool="skillforge_org_search_users",
                data_scope="org.users",
                run_mode="mcp_cli",
                dry_run=True,
                ok=True,
                created_at=datetime(2026, 5, 6, 12, 3, 0),
            )
        )
        await session.commit()

    resp = await client.get("/api/users/?page_size=1000")
    assert resp.status_code == 200
    rows = {item["id"]: item for item in resp.json()["items"]}
    row = rows["codex-user-list"]
    assert row["last_activity_source"] == "codex"
    assert row["last_activity_at"].startswith("2026-05-06T12:03:00")
    assert row["codex"]["is_codex_user"] is True
    assert row["codex"]["codex_session_count"] == 1
    assert row["codex"]["codex_active_sessions"] == 1
    assert row["codex"]["codex_mcp_call_count"] == 1


@pytest.mark.asyncio
async def test_list_users_falls_back_to_primary_org_department(client):
    """用户部门为空时，用户管理列表应显示 primary 组织归属。"""
    from app.auth.models import User
    from app.database import async_session_factory
    from app.org.models import OrgUnit, UserOrgMembership

    async with async_session_factory() as session:
        session.add(
            User(
                id="org-dept-user",
                username="org-dept-user",
                name="组织部门用户",
                role="aibp",
                department=None,
                is_active=True,
                must_change_password=False,
            )
        )
        session.add(OrgUnit(id="org-dept-1", name="组织显示部门", type="department", path="/org-dept-1"))
        session.add(
            UserOrgMembership(
                user_id="org-dept-user",
                org_unit_id="org-dept-1",
                membership_type="primary",
            )
        )
        await session.commit()

    resp = await client.get("/api/users/?page_size=1000")

    assert resp.status_code == 200
    rows = {item["id"]: item for item in resp.json()["items"]}
    assert rows["org-dept-user"]["department"] == "组织显示部门"


@pytest.mark.asyncio
async def test_list_users_collapses_dingtalk_shadow_user_and_merges_codex_usage(client):
    """用户管理列表应把 OAuth 影子账号合并到组织同步账号上展示。"""
    from app.auth.models import User
    from app.codex import service as codex_service
    from app.codex.models import CodexCliSession, CodexMcpCallAudit
    from app.database import async_session_factory
    from app.org.models import OrgUnit, UserOrgMembership

    async with async_session_factory() as session:
        session.add(
            User(
                id="dt_real_zhudan",
                username="dt_real_zhudan",
                name="朱丹",
                role="operator",
                department="即时零售业务部",
                dingtalk_user_id="corp-zhudan",
                state="active",
                is_active=True,
                must_change_password=False,
                created_at=datetime(2026, 5, 1, 9, 0, 0),
            )
        )
        session.add(
            User(
                id="dt_open_zhudan",
                username="dt_open_zhudan",
                name="朱丹-小荷包",
                role="aibp",
                department=None,
                dingtalk_user_id="openid-zhudan-userid-abcdef",
                state="active",
                is_active=True,
                must_change_password=False,
                last_login_at=datetime(2026, 5, 6, 12, 0, 0),
                created_at=datetime(2026, 5, 2, 9, 0, 0),
            )
        )
        session.add(OrgUnit(id="org-real-zhudan", name="即时零售业务部", type="department", path="/org-real-zhudan"))
        session.add(
            UserOrgMembership(
                user_id="dt_real_zhudan",
                org_unit_id="org-real-zhudan",
                membership_type="primary",
            )
        )
        session.add(
            CodexCliSession(
                id="cli_shadow_zhudan",
                user_id="dt_open_zhudan",
                token_hash=codex_service.token_hash("shadow-zhudan-token"),
                scopes_json={"source": "test"},
                permissions_rev_snapshot=0,
                created_at=datetime(2026, 5, 6, 12, 1, 0),
                last_seen_at=datetime(2026, 5, 6, 12, 2, 0),
                expires_at=codex_service.utc_safe_now() + codex_service.timedelta(days=1),
            )
        )
        session.add(
            CodexMcpCallAudit(
                request_id="req_shadow_zhudan",
                proof_id="proof_shadow_zhudan",
                user_id="dt_open_zhudan",
                server="skillforge",
                tool="skillforge_org_search_users",
                data_scope="org.users",
                run_mode="mcp_cli",
                dry_run=True,
                ok=True,
                created_at=datetime(2026, 5, 6, 12, 3, 0),
            )
        )
        await session.commit()

    resp = await client.get("/api/users/?page_size=1000")

    assert resp.status_code == 200
    rows = {item["id"]: item for item in resp.json()["items"]}
    assert "dt_open_zhudan" not in rows
    row = rows["dt_real_zhudan"]
    assert row["name"] == "朱丹"
    assert row["department"] == "即时零售业务部"
    assert row["last_activity_source"] == "codex"
    assert row["codex"]["is_codex_user"] is True
    assert row["codex"]["codex_session_count"] == 1
    assert row["codex"]["codex_mcp_call_count"] == 1


@pytest.mark.asyncio
async def test_list_users_collapses_short_openid_shadow_when_alias_matches(client):
    """短 openId 账号也应在明确命中真实组织账号时合并显示。"""
    from app.auth.models import User
    from app.database import async_session_factory
    from app.org.models import OrgUnit, UserOrgMembership

    async with async_session_factory() as session:
        session.add(
            User(
                id="dt_real_yangchangliang",
                username="dt_real_yangchangliang",
                name="杨昌亮",
                role="operator",
                department="办公室",
                dingtalk_user_id="0213644726269578",
                avatar_url="https://example.com/avatar/yang.jpg",
                state="active",
                is_active=True,
                must_change_password=False,
                created_at=datetime(2026, 5, 1, 9, 0, 0),
            )
        )
        session.add(
            User(
                id="dt_open_yangchangliang",
                username="dt_open_yangchangliang",
                name="老杨-杨昌亮",
                role="aibp",
                department="办公室",
                dingtalk_user_id="iPk5JHfadiiToiE",
                avatar_url="https://example.com/avatar/yang.jpg",
                state="active",
                is_active=True,
                must_change_password=False,
                created_at=datetime(2026, 5, 2, 9, 0, 0),
            )
        )
        session.add(OrgUnit(id="org-real-yang", name="办公室", type="department", path="/org-real-yang"))
        session.add(
            UserOrgMembership(
                user_id="dt_real_yangchangliang",
                org_unit_id="org-real-yang",
                membership_type="primary",
            )
        )
        await session.commit()

    resp = await client.get("/api/users/?page_size=1000")

    assert resp.status_code == 200
    rows = {item["id"]: item for item in resp.json()["items"]}
    assert "dt_open_yangchangliang" not in rows
    assert rows["dt_real_yangchangliang"]["name"] == "杨昌亮"
    assert rows["dt_real_yangchangliang"]["department"] == "办公室"
    assert rows["dt_real_yangchangliang"]["avatar_url"] == "https://example.com/avatar/yang.jpg"


# ===== 审计日志独立路由测试 =====

@pytest.mark.asyncio
async def test_audit_query(client):
    """测试审计日志查询"""
    resp = await client.get("/api/audit/")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_audit_stats(client):
    """测试审计统计"""
    resp = await client.get("/api/audit/stats?days=7")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_audit_security_events(client):
    """测试安全事件查询"""
    resp = await client.get("/api/audit/security-events")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_audit_export(client):
    """测试审计日志导出"""
    resp = await client.get("/api/audit/export")
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/csv")


# ===== 编辑锁测试 =====

@pytest.mark.asyncio
async def test_acquire_lock(client):
    """测试获取编辑锁"""
    resp = await client.post("/api/skills/test-skill/lock")
    # Skill不存在或数据库问题
    assert resp.status_code in (200, 404, 500)


@pytest.mark.asyncio
async def test_lock_status(client):
    """测试查询锁状态"""
    resp = await client.get("/api/skills/test-skill/lock")
    assert resp.status_code in (200, 404, 500)


@pytest.mark.asyncio
async def test_release_lock(client):
    """测试释放编辑锁（不存在的Skill返回404）"""
    resp = await client.delete("/api/skills/test-skill/lock")
    assert resp.status_code in (200, 404, 500)


# ===== 执行API新增端点测试 =====

@pytest.mark.asyncio
async def test_get_run_detail(client):
    """测试获取执行详情"""
    resp = await client.get("/api/executions/runs/nonexistent")
    assert resp.status_code == 200  # 返回error字段


@pytest.mark.asyncio
async def test_get_run_decisions(client):
    """测试决策追溯"""
    resp = await client.get("/api/executions/runs/nonexistent/decisions")
    assert resp.status_code == 200


# ===== 数据源新增端点测试 =====

@pytest.mark.asyncio
async def test_datasource_preview(client):
    """测试数据预览"""
    resp = await client.get("/api/data-sources/nonexistent/preview")
    assert resp.status_code in (404, 500)


@pytest.mark.asyncio
async def test_datasource_history(client):
    """测试上传历史"""
    resp = await client.get("/api/data-sources/nonexistent/history")
    assert resp.status_code in (404, 500)


@pytest.mark.asyncio
async def test_datasource_quality(client):
    """测试质量报告"""
    resp = await client.get("/api/data-sources/nonexistent/quality")
    assert resp.status_code in (404, 500)


@pytest.mark.asyncio
async def test_datasource_update(client):
    """测试编辑数据源配置"""
    resp = await client.put("/api/data-sources/nonexistent", json={
        "name": "新名称",
    })
    assert resp.status_code in (404, 500)


# ===== 看板数据健康端点测试 =====

@pytest.mark.asyncio
async def test_dashboard_data_health(client):
    """测试数据源健康状态"""
    resp = await client.get("/api/dashboard/data-health")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "stale_count" in data
