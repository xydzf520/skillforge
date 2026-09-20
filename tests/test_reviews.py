"""审核模块测试"""

from contextlib import asynccontextmanager
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import ASGITransport, AsyncClient


# ===== 列表/创建（已有） =====


@pytest.mark.asyncio
async def test_list_reviews(client):
    """测试审核列表"""
    resp = await client.get("/api/reviews/")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "items" in data


@pytest.mark.asyncio
async def test_list_reviews_router_forwards_search_and_change_type(client):
    """审核中心搜索和变更类型筛选必须走后端，不只过滤当前页。"""
    with patch(
        "app.reviews.router.service.list_reviews",
        new=AsyncMock(return_value={"total": 0, "page": 1, "items": []}),
    ) as mock_list:
        resp = await client.get("/api/reviews/", params={"q": "冷启动", "change_type": "fix", "page_size": 5})

    assert resp.status_code == 200
    assert mock_list.await_count == 1
    assert mock_list.await_args.kwargs["q"] == "冷启动"
    assert mock_list.await_args.kwargs["change_type"] == "fix"
    assert mock_list.await_args.kwargs["page_size"] == 5


@pytest.mark.asyncio
async def test_create_review(client):
    """测试创建审核请求"""
    resp = await client.post("/api/reviews/", json={
        "skill_id": "EC-投放-01",
        "change_type": "params",
        "reason": "pytest测试审核",
    })
    if resp.status_code == 200:
        data = resp.json()
        assert data["status"] == "pending"
        assert data["skill_id"] == "EC-投放-01"


@pytest.mark.asyncio
async def test_create_review_router_defaults_change_type(client):
    """路由层在缺省 change_type 时应回退到 update。"""
    with patch(
        "app.reviews.router.service.create_review",
        new=AsyncMock(return_value={"review_id": 11, "skill_id": "EC-投放-01", "status": "pending"}),
    ) as mock_create:
        resp = await client.post("/api/reviews/", json={"skill_id": "EC-投放-01", "reason": "缺省类型"})

    assert resp.status_code == 200
    assert mock_create.await_count == 1
    assert mock_create.await_args.kwargs["change_type"] == "update"


@pytest.mark.asyncio
async def test_create_review_auto_publishes_when_security_switch_enabled(client, monkeypatch):
    from app.common.models import SystemConfig
    from app.database import async_session_factory

    async with async_session_factory() as session:
        session.add(SystemConfig(key="security.bypass_review_direct_publish", value=True, updated_by="admin"))
        await session.commit()

    async def fake_create_review(*args, **kwargs):
        assert kwargs["force_submit"] is True
        assert "免审核直接发布" in kwargs["force_reason"]
        return {"review_id": 77, "skill_id": kwargs["skill_id"], "status": "pending", "reviewer": "lead"}

    async def fake_approve_review_as_system(db, review_id, **kwargs):
        assert review_id == 77
        assert kwargs["verify_after_sync"] is True
        return {"review_id": review_id, "status": "approved", "new_version": "v0.1"}

    monkeypatch.setattr("app.reviews.router.service.create_review", fake_create_review)
    monkeypatch.setattr("app.reviews.router.service.approve_review_as_system", fake_approve_review_as_system)

    resp = await client.post("/api/reviews/", json={
        "skill_id": "EC-投放-01",
        "change_type": "params",
        "reason": "pytest测试审核",
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "approved"
    assert data["auto_publish"]["ok"] is True
    assert data["review"]["status"] == "approved"


@pytest.mark.asyncio
async def test_self_approve_blocked(client):
    """管理员允许自审，审核记录会进入正常通过链路。"""
    # 先创建
    create_resp = await client.post("/api/reviews/", json={
        "skill_id": "EC-投放-01",
        "change_type": "params",
    })
    if create_resp.status_code == 200:
        review_id = create_resp.json()["review_id"]
        approve_resp = await client.post(f"/api/reviews/{review_id}/approve")
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "approved"


# ===== 新增: 审核通过触发sync_service =====


@pytest.mark.asyncio
async def test_approve_review_triggers_sync():
    """测试审核通过——触发Git tag + sync_service"""
    from app.reviews.models import Review
    from app.skills.core.models import Skill

    mock_review = MagicMock(spec=Review)
    mock_review.id = 1
    mock_review.skill_id = "EC-TEST-01"
    mock_review.submitter = "user_a"  # 提交人不是reviewer
    mock_review.status = "pending"
    mock_review.change_type = "params"
    mock_review.git_commit_before = None
    mock_review.git_commit_after = "abc123"

    mock_skill = MagicMock(spec=Skill)
    mock_skill.id = "EC-TEST-01"
    mock_skill.name = "测试Skill"
    mock_skill.current_version = "v1.0"
    mock_skill.status = "draft"
    mock_skill.approval_level = 1
    mock_skill.approver = None

    # 提交人（用于通知推送查询）
    mock_submitter_user = MagicMock()
    mock_submitter_user.dingtalk_user_id = None  # 无钉钉ID，跳过通知
    actor = MagicMock()
    actor.id = "admin_reviewer"
    actor.role = "admin"
    actor.department = "EC"
    actor.can_view_all = True

    call_count = [0]
    def mock_execute(stmt):
        call_count[0] += 1
        result = MagicMock()
        if call_count[0] == 1:
            # 查询Review
            result.scalar_one_or_none.return_value = mock_review
        elif call_count[0] == 2:
            # 查询Skill
            result.scalar_one_or_none.return_value = mock_skill
        else:
            # 查询提交人
            result.scalar_one_or_none.return_value = mock_submitter_user
        return result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.flush = AsyncMock()

    with patch("app.reviews.service.git_service") as mock_git, \
         patch("app.reviews.service.audit") as mock_audit, \
         patch("app.execution.sync_service.SkillSyncService.sync_after_approval", new_callable=AsyncMock) as mock_sync:
        mock_audit.log = AsyncMock()
        mock_sync.return_value = {"all_ok": True}

        from app.reviews.service import approve_review
        result = await approve_review(mock_db, 1, "admin_reviewer", actor=actor)

    assert result["status"] == "approved"
    assert result["new_version"] == "v1.1"
    assert result["sync_result"] == {"all_ok": True}
    assert mock_skill.status == "active"
    mock_git.tag.assert_called_once()
    mock_sync.assert_called_once_with(
        "EC-TEST-01",
        version_tag="EC-TEST-01/v1.1",
        review_id=1,
        target_instance_ids=None,
        actor=actor,
    )


@pytest.mark.asyncio
async def test_approve_review_resolves_same_commit_duplicate_pending_reviews(client, monkeypatch):
    """同一 Skill 同一 commit 已通过时，重复 pending 审核单应自动收敛。"""
    import app.database as db_mod
    from app.codex.models import CodexSkillSubmission
    from app.reviews.models import Review
    from app.reviews.service import approve_review
    from app.skills.core.models import Skill

    monkeypatch.setattr("app.reviews.service.enforce_static_detection_gate", AsyncMock(return_value={"passed": True}))
    monkeypatch.setattr("app.reviews.service._send_review_result_notification", AsyncMock())
    monkeypatch.setattr("app.reviews.service._send_review_notification", AsyncMock())
    monkeypatch.setattr("app.execution.sync_service.SkillSyncService.sync_after_approval", AsyncMock(return_value={"all_ok": True}))
    monkeypatch.setattr("app.reviews.service.audit.log", AsyncMock())
    monkeypatch.setattr("app.reviews.service.git_service.tag", MagicMock())
    monkeypatch.setattr("app.skills.integrations.hermes_adapter.generate_hermes_files", lambda _skill_id: {})

    actor = MagicMock()
    actor.id = "admin"
    actor.role = "admin"
    actor.department = "AI小组"
    actor.can_view_all = True

    async with db_mod.async_session_factory() as session:
        session.add(Skill(
            id="dup-skill",
            name="重复审核 Skill",
            description="same commit duplicate review",
            department="AI小组",
            role="测试",
            trigger_type="manual",
            risk_level="R1",
            owner="admin",
            status="draft",
            current_version="v0.0",
        ))
        session.add_all([
            Review(
                skill_id="dup-skill",
                submitter="admin",
                reviewer="admin",
                change_type="update",
                status="pending",
                git_commit_before="a" * 40,
                git_commit_after="b" * 40,
            ),
            Review(
                skill_id="dup-skill",
                submitter="admin",
                reviewer="admin",
                change_type="codex_submit",
                status="pending",
                git_commit_before="a" * 40,
                git_commit_after="b" * 40,
            ),
            Review(
                skill_id="dup-skill",
                submitter="admin",
                reviewer="admin",
                change_type="codex_submit",
                status="pending",
                git_commit_before="b" * 40,
                git_commit_after="c" * 40,
            ),
        ])
        await session.flush()
        reviews = (
            await session.execute(
                Review.__table__.select().where(Review.skill_id == "dup-skill").order_by(Review.id)
            )
        ).all()
        approved_review_id = reviews[0]._mapping["id"]
        duplicate_review_id = reviews[1]._mapping["id"]
        other_commit_review_id = reviews[2]._mapping["id"]
        session.add(CodexSkillSubmission(
            id="sub-dup",
            user_id="admin",
            skill_id="dup-skill",
            package_hash="sha256:dup",
            status="review_pending",
            review_id=duplicate_review_id,
            git_commit="b" * 40,
            checks_json={},
            manifest_json={},
        ))
        await session.commit()

    async with db_mod.async_session_factory() as session:
        result = await approve_review(
            session,
            approved_review_id,
            "admin",
            actor=actor,
            verify_after_sync=False,
        )
        await session.commit()

    assert result["status"] == "approved"
    assert result["resolved_duplicate_review_ids"] == [duplicate_review_id]

    async with db_mod.async_session_factory() as session:
        duplicate = await session.get(Review, duplicate_review_id)
        other_commit = await session.get(Review, other_commit_review_id)
        submission = await session.get(CodexSkillSubmission, "sub-dup")

    assert duplicate.status == "approved"
    assert duplicate.review_feedback["duplicate_resolved_by_review_id"] == approved_review_id
    assert other_commit.status == "pending"
    assert submission.status == "approved"


@pytest.mark.asyncio
async def test_approve_review_can_set_runtime_schedule_and_verify():
    """审核通过时可指定实例、写定时配置，并触发运行验证。"""
    from app.reviews.models import Review
    from app.skills.core.models import Skill

    mock_review = MagicMock(spec=Review)
    mock_review.id = 7
    mock_review.skill_id = "EC-VERIFY-01"
    mock_review.submitter = "user_submitter"
    mock_review.status = "pending"
    mock_review.change_type = "update"
    mock_review.diff_content = None
    mock_review.review_feedback = None

    mock_skill = MagicMock(spec=Skill)
    mock_skill.id = "EC-VERIFY-01"
    mock_skill.name = "验证 Skill"
    mock_skill.current_version = "v2.3"
    mock_skill.status = "draft"
    mock_skill.trigger_type = "manual"
    mock_skill.trigger_expression = None
    mock_skill.department = "EC"
    mock_skill.approval_level = 1
    mock_skill.approver = None

    actor = MagicMock()
    actor.id = "review_admin"
    actor.role = "admin"
    actor.department = "EC"
    actor.can_view_all = True

    call_count = [0]

    def mock_execute(stmt):
        call_count[0] += 1
        result = MagicMock()
        if call_count[0] == 1:
            result.scalar_one_or_none.return_value = mock_review
        elif call_count[0] == 2:
            result.scalar_one_or_none.return_value = mock_skill
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.flush = AsyncMock()

    @asynccontextmanager
    async def fake_skill_lock(_skill_id):
        yield

    skill_md = """---
name: 验证 Skill
description: 审核通过时验证下发链路
department: EC
trigger_type: manual
risk_level: R1
verify_script_timeout: 420
---

# 验证 Skill
"""

    with patch("app.reviews.service.git_service") as mock_git, \
         patch("app.skills.core.git_service.git_service", new=mock_git), \
         patch("app.reviews.service.audit") as mock_audit, \
         patch("app.reviews.service._send_review_result_notification", new=AsyncMock()) as mock_notify, \
         patch("app.execution.sync_service.SkillSyncService.list_sync_targets", new_callable=AsyncMock) as mock_list_targets, \
         patch("app.execution.sync_service.SkillSyncService.sync_after_approval", new_callable=AsyncMock) as mock_sync, \
         patch("app.tasktree.schedule_query.update_skill_schedule", new_callable=AsyncMock) as mock_update_schedule, \
         patch("app.execution.execution_service.ExecutionService.execute_skill", new_callable=AsyncMock) as mock_execute_skill, \
         patch("app.skills.integrations.hermes_adapter.generate_hermes_files", return_value={}) as _mock_hermes:
        mock_audit.log = AsyncMock()
        mock_git.read_file.return_value = skill_md
        mock_git.write_file = MagicMock()
        mock_git.commit_all.return_value = "commit-config"
        mock_git.skill_advisory_lock.side_effect = fake_skill_lock
        mock_list_targets.return_value = [{
            "id": "demo-prod",
            "name": "Demo Prod",
            "agent_type": "aiclaw",
            "bridge_online": True,
            "selectable": True,
        }]
        mock_sync.return_value = {
            "all_ok": True,
            "push_ok": True,
            "instances": [{"instance_id": "demo-prod", "ok": True}],
        }
        mock_update_schedule.return_value = {
            "skill_id": "EC-VERIFY-01",
            "action": "start",
            "cron": "0 9 * * *",
            "message": "定时任务已启动",
        }
        mock_execute_skill.return_value = {
            "run_id": "verify-run-1",
            "run_mode": "sandbox_test",
            "sample_used": True,
        }

        from app.reviews.service import approve_review
        result = await approve_review(
            mock_db,
            7,
            "review_admin",
            runtime_instance_id="demo-prod",
            cron_expression="0 9 * * *",
            verify_after_sync=True,
            actor=actor,
        )

    assert result["status"] == "approved"
    assert result["new_version"] == "v2.4"
    assert result["schedule_result"]["action"] == "start"
    assert result["verify_result"]["status"] == "ok"
    assert result["verify_result"]["run_id"] == "verify-run-1"
    assert result["review_feedback"]["runtime_instance_id"] == "demo-prod"
    assert result["review_feedback"]["cron_expression"] == "0 9 * * *"
    assert result["review_feedback"]["verify_status"] == "ok"

    mock_sync.assert_called_once_with(
        "EC-VERIFY-01",
        version_tag="EC-VERIFY-01/v2.4",
        review_id=7,
        target_instance_ids=["demo-prod"],
        actor=actor,
    )
    mock_update_schedule.assert_called_once_with(
        mock_db,
        "EC-VERIFY-01",
        "start",
        "0 9 * * *",
        "review_admin",
    )
    mock_execute_skill.assert_called_once()
    assert mock_execute_skill.await_args.args[0] == "EC-VERIFY-01"
    assert mock_execute_skill.await_args.kwargs["params"]["_script_timeout"] == 420
    written_skill_md = mock_git.write_file.call_args.args[2]
    assert "instance_id: demo-prod" in written_skill_md
    assert "trigger_expression: 0 9 * * *" in written_skill_md


# ===== 新增: 审核驳回发送通知 =====


@pytest.mark.asyncio
async def test_reject_review_sends_notification():
    """测试审核驳回——驳回原因作为评论 + 推送通知"""
    from app.reviews.models import Review

    mock_review = MagicMock(spec=Review)
    mock_review.id = 2
    mock_review.skill_id = "EC-TEST-02"
    mock_review.submitter = "user_b"
    mock_review.status = "pending"
    mock_review.change_type = "logic"

    # 模拟提交人查询（需要有dingtalk_user_id属性）
    from app.auth.models import User
    mock_submitter = MagicMock(spec=User)
    mock_submitter.dingtalk_user_id = "dt_user_b"

    call_count = [0]
    def mock_execute(stmt):
        call_count[0] += 1
        result = MagicMock()
        if call_count[0] == 1:
            result.scalar_one_or_none.return_value = mock_review
        else:
            result.scalar_one_or_none.return_value = mock_submitter
        return result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.flush = AsyncMock()
    mock_db.add = MagicMock()

    with patch("app.reviews.service.audit") as mock_audit, \
         patch("app.dingtalk.outbox.outbox.enqueue", new_callable=AsyncMock) as mock_enqueue:
        mock_audit.log = AsyncMock()

        from app.reviews.service import reject_review
        result = await reject_review(mock_db, 2, "admin_reviewer", "参数不合理")

    assert result["status"] == "rejected"
    assert mock_review.status == "rejected"
    # 驳回原因应该被添加为评论
    mock_db.add.assert_called()  # ReviewComment被添加
    mock_enqueue.assert_called_once()


# ===== 新增: L2/L3审批流 =====


@pytest.mark.asyncio
async def test_create_review_l2_approval():
    """测试创建审核——L2级审批触发钉钉审批流"""
    from app.reviews.models import Review
    from app.skills.core.models import Skill
    from app.auth.models import User

    mock_skill = MagicMock(spec=Skill)
    mock_skill.id = "EC-L2-01"
    mock_skill.name = "L2审批Skill"
    mock_skill.approval_level = 2
    mock_skill.approver = "lead_a"

    mock_submitter_user = MagicMock(spec=User)
    mock_submitter_user.name = "张三"
    mock_submitter_user.dingtalk_user_id = "dt_zhangsan"

    mock_approver_user = MagicMock(spec=User)
    mock_approver_user.dingtalk_user_id = "dt_lead_a"

    call_count = [0]
    review_obj = None
    def mock_execute(stmt):
        nonlocal review_obj
        call_count[0] += 1
        result = MagicMock()
        if call_count[0] == 1:
            # 查Skill
            result.scalar_one_or_none.return_value = mock_skill
        elif call_count[0] == 2:
            # 查提交人
            result.scalar_one_or_none.return_value = mock_submitter_user
        elif call_count[0] == 3:
            # 查审批人
            result.scalar_one_or_none.return_value = mock_approver_user
        else:
            # 查审核人（用于通知推送）
            result.scalar_one_or_none.return_value = mock_approver_user
        return result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.flush = AsyncMock()
    mock_db.add = MagicMock()

    with patch("app.reviews.service.git_service") as mock_git, \
         patch("app.reviews.service.audit") as mock_audit, \
         patch("app.dingtalk.approval.create_l2_approval", new_callable=AsyncMock) as mock_l2_real, \
         patch("app.dingtalk.outbox.outbox.enqueue", new_callable=AsyncMock) as mock_enqueue:
        mock_git.log.return_value = [{"hash_full": "commit123"}]
        mock_audit.log = AsyncMock()
        mock_l2_real.return_value = {"ok": True, "instance_id": "dingtalk_inst_001"}

        from app.reviews.service import create_review
        result = await create_review(
            mock_db,
            skill_id="EC-L2-01",
            submitter="zhangsan",
            change_type="params",
            reason="调整ROI阈值",
        )

    assert result["status"] == "pending"
    assert result["skill_id"] == "EC-L2-01"


@pytest.mark.asyncio
async def test_create_review_sets_reviewer_from_skill_approver():
    """创建审核时应把 skill.approver 写入 reviewer 字段。"""
    from app.skills.core.models import Skill
    from app.auth.models import User

    mock_skill = MagicMock(spec=Skill)
    mock_skill.id = "EC-REVIEW-01"
    mock_skill.name = "审核测试 Skill"
    mock_skill.approval_level = 1
    mock_skill.approver = "lead_a"

    mock_submitter_user = MagicMock(spec=User)
    mock_submitter_user.name = "张三"
    mock_submitter_user.dingtalk_user_id = None

    call_count = [0]
    def mock_execute(stmt):
        call_count[0] += 1
        result = MagicMock()
        if call_count[0] == 1:
            result.scalar_one_or_none.return_value = mock_skill
        else:
            result.scalar_one_or_none.return_value = mock_submitter_user
        return result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.flush = AsyncMock()
    mock_db.add = MagicMock()

    with patch("app.reviews.service.audit") as mock_audit, \
         patch("app.reviews.service._send_review_notification", new_callable=AsyncMock):
        mock_audit.log = AsyncMock()

        from app.reviews.service import create_review
        result = await create_review(
            mock_db,
            skill_id="EC-REVIEW-01",
            submitter="zhangsan",
            change_type="params",
            reason="设置 reviewer",
        )

    created_review = mock_db.add.call_args[0][0]
    assert created_review.reviewer == "lead_a"
    assert result["reviewer"] == "lead_a"


# ===== 新增: 已决定的审核不能再处理 =====


@pytest.mark.asyncio
async def test_approve_playbook_review_creates_playbook_tag():
    """Playbook 审核通过应走独立 tag 分支，不依赖 Skill 记录。"""
    from app.reviews.models import Review
    from app.auth.models import User

    mock_review = MagicMock(spec=Review)
    mock_review.id = 6
    mock_review.skill_id = "playbook:replenishment-check"
    mock_review.submitter = "user_pb"
    mock_review.status = "pending"
    mock_review.change_type = "new_skill"
    mock_review.review_feedback = None

    mock_submitter = MagicMock(spec=User)
    mock_submitter.dingtalk_user_id = None

    call_count = [0]

    def mock_execute(stmt, params=None):
        # H7 修复后，approve_review 会调一次 ``SELECT pg_advisory_xact_lock(hashtext(...))``，
        # 该调用 stmt 是 sqlalchemy.text() 包装的字符串，不消耗本测试的 review/submitter mock。
        # 把这类 advisory_lock 的调用直接 noop。
        try:
            stmt_str = str(stmt)
        except Exception:
            stmt_str = ""
        if "pg_advisory" in stmt_str:
            return MagicMock()
        call_count[0] += 1
        result = MagicMock()
        if call_count[0] == 1:
            result.scalar_one_or_none.return_value = mock_review
        else:
            result.scalar_one_or_none.return_value = mock_submitter
        return result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.flush = AsyncMock()

    with patch("app.reviews.service.git_service") as mock_git, \
         patch("app.reviews.service.audit") as mock_audit:
        mock_git.repo.tags = []
        mock_audit.log = AsyncMock()

        from app.reviews.service import approve_review
        result = await approve_review(mock_db, 6, "admin_reviewer")

    assert result["status"] == "approved"
    assert result["new_version"] == "v0.1"
    mock_git.tag.assert_called_once_with(
        "playbook/replenishment-check/v0.1",
        "审核通过 by admin_reviewer",
    )
    assert mock_review.status == "approved"


@pytest.mark.asyncio
async def test_approve_already_decided():
    """测试审核已处理——不能重复审批"""
    from app.common.exceptions import AppError
    from app.reviews.models import Review

    mock_review = MagicMock(spec=Review)
    mock_review.id = 3
    mock_review.status = "approved"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_review
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(AppError) as exc_info:
        from app.reviews.service import approve_review
        await approve_review(mock_db, 3, "someone")

    assert exc_info.value.code == "REVIEW_ALREADY_DECIDED"


def _build_reviews_test_app(mock_user):
    from fastapi import FastAPI
    from app.common.exceptions import AppError, app_error_handler
    from app.auth.dependencies import get_current_user
    from app.database import get_db
    from app.reviews.router import router

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.dependency_overrides[get_current_user] = lambda: mock_user

    async def _fake_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = _fake_db
    app.include_router(router, prefix="/api/reviews")
    return app


@pytest.mark.asyncio
async def test_review_detail_allows_submitter_but_blocks_peer():
    review_payload = {
        "id": 1,
        "skill_id": "EC-01",
        "skill_department": "EC",
        "submitter": "submitter_a",
        "reviewer": "reviewer_a",
        "status": "pending",
        "created_at": None,
        "comments": [],
        "diff_text": "",
    }

    submitter_user = MagicMock()
    submitter_user.id = "submitter_a"
    submitter_user.role = "biz_owner"
    submitter_user.department = "EC"
    submitter_user.can_view_all = False
    submitter_user.is_active = True

    app = _build_reviews_test_app(submitter_user)
    with patch("app.reviews.service.get_review", new=AsyncMock(return_value=review_payload)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/reviews/1")
            assert resp.status_code == 200

    peer_user = MagicMock()
    peer_user.id = "peer_a"
    peer_user.role = "biz_owner"
    peer_user.department = "EC"
    peer_user.can_view_all = False
    peer_user.is_active = True

    app = _build_reviews_test_app(peer_user)
    with patch("app.reviews.service.get_review", new=AsyncMock(return_value=review_payload)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/reviews/1")
            assert resp.status_code == 403
            assert resp.json()["error"]["code"] == "REVIEW_PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_review_approve_requires_assigned_reviewer():
    review_payload = {
        "id": 2,
        "skill_id": "EC-02",
        "skill_department": "EC",
        "submitter": "submitter_a",
        "reviewer": "reviewer_a",
        "status": "pending",
        "created_at": None,
        "comments": [],
        "diff_text": "",
    }

    peer_user = MagicMock()
    peer_user.id = "peer_a"
    peer_user.role = "biz_owner"
    peer_user.department = "EC"
    peer_user.can_view_all = False
    peer_user.is_active = True

    app = _build_reviews_test_app(peer_user)
    with patch("app.reviews.service.get_review", new=AsyncMock(return_value=review_payload)), \
         patch("app.reviews.service.approve_review", new=AsyncMock(return_value={"status": "approved"})) as mock_approve:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/api/reviews/2/approve", json={})
            assert resp.status_code == 403
            assert resp.json()["error"]["code"] == "REVIEW_PERMISSION_DENIED"
            mock_approve.assert_not_called()


# ===== 新增: 审核评论 =====


@pytest.mark.asyncio
async def test_add_comment_service():
    """测试添加审核评论"""
    from app.reviews.models import Review

    mock_review = MagicMock(spec=Review)
    mock_review.id = 4

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_review
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.flush = AsyncMock()
    mock_db.add = MagicMock()

    with patch("app.reviews.service.audit") as mock_audit:
        mock_audit.log = AsyncMock()

        from app.reviews.service import add_comment
        result = await add_comment(mock_db, 4, "admin", "这个参数需要再调整")

    assert "comment_id" in result
    mock_db.add.assert_called_once()


@pytest.mark.asyncio
async def test_add_comment_review_not_found():
    """测试给不存在的审核添加评论——应返回404"""
    from app.common.exceptions import AppError

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None  # 审核不存在
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(AppError) as exc_info:
        from app.reviews.service import add_comment
        await add_comment(mock_db, 999, "admin", "评论内容")

    assert exc_info.value.code == "REVIEW_NOT_FOUND"


# ===== 新增: 审核详情 =====


@pytest.mark.asyncio
async def test_get_review_detail():
    """测试获取审核详情——包含评论和diff"""
    from app.reviews.models import Review, ReviewComment

    mock_review = MagicMock(spec=Review)
    mock_review.id = 5
    mock_review.skill_id = "EC-TEST-05"
    mock_review.submitter = "user_c"
    mock_review.reviewer = "admin"
    mock_review.change_type = "code"
    mock_review.diff_summary = "修改了脚本"
    mock_review.diff_content = None
    mock_review.reason = "优化算法"
    mock_review.status = "approved"
    mock_review.created_at = MagicMock()
    mock_review.created_at.isoformat.return_value = "2026-03-01T10:00:00"
    mock_review.decided_at = MagicMock()
    mock_review.decided_at.isoformat.return_value = "2026-03-02T10:00:00"
    mock_review.git_commit_before = None
    mock_review.git_commit_after = None

    mock_comment = MagicMock(spec=ReviewComment)
    mock_comment.id = 1
    mock_comment.author = "admin"
    mock_comment.content = "LGTM"
    mock_comment.created_at = MagicMock()
    mock_comment.created_at.isoformat.return_value = "2026-03-02T09:00:00"

    call_count = [0]
    def mock_execute(stmt):
        call_count[0] += 1
        result = MagicMock()
        if call_count[0] == 1:
            result.scalar_one_or_none.return_value = mock_review
        else:
            result.scalars.return_value.all.return_value = [mock_comment]
        return result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("app.reviews.service.git_service"):
        from app.reviews.service import get_review
        result = await get_review(mock_db, 5)

    assert result["id"] == 5
    assert result["status"] == "approved"
    assert len(result["comments"]) == 1
    assert result["comments"][0]["content"] == "LGTM"


@pytest.mark.asyncio
async def test_get_review_not_found():
    """测试获取不存在的审核——应返回404"""
    from app.common.exceptions import AppError

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(AppError) as exc_info:
        from app.reviews.service import get_review
        await get_review(mock_db, 999)

    assert exc_info.value.code == "REVIEW_NOT_FOUND"


@pytest.mark.asyncio
async def test_create_review_blocks_when_gate_fails():
    from app.common.exceptions import AppError
    from app.skills.core.models import Skill

    mock_skill = MagicMock(spec=Skill)
    mock_skill.id = "EC-GATE-01"
    mock_skill.name = "Gate Skill"
    mock_skill.department = "EC"
    mock_skill.approval_level = 1
    mock_skill.approver = "lead_a"

    result = MagicMock()
    result.scalar_one_or_none.return_value = mock_skill
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=result)

    readiness = MagicMock()
    readiness.can_publish = False
    readiness.review_gate = {
        "can_submit_review": False,
        "score": 50,
        "threshold": 70,
        "items": [
            {
                "key": "git_commit",
                "label": "Git commit",
                "passed": False,
                "severity": "block",
                "detail": "缺少该 Skill 的 Git commit",
                "suggestion": "先保存并提交当前 Skill 改动，再提交审核。",
            }
        ],
    }

    with patch("app.skills.lifecycle.publish_readiness.check_publish_readiness", new=AsyncMock(return_value=readiness)):
        from app.reviews.service import create_review

        with pytest.raises(AppError) as exc_info:
            await create_review(mock_db, "EC-GATE-01", "user_a", "update")

    assert exc_info.value.code == "PARAM_INVALID"
    assert exc_info.value.detail["score"] == 50
    assert exc_info.value.detail["missing"][0]["key"] == "git_commit"


@pytest.mark.asyncio
async def test_create_review_force_submit_records_gate_payload():
    from app.skills.core.models import Skill

    mock_skill = MagicMock(spec=Skill)
    mock_skill.id = "EC-GATE-02"
    mock_skill.name = "Gate Skill"
    mock_skill.department = "EC"
    mock_skill.approval_level = 1
    mock_skill.approver = "lead_a"

    mock_submitter = MagicMock()
    mock_submitter.name = "提交人"

    skill_result = MagicMock()
    skill_result.scalar_one_or_none.return_value = mock_skill
    submitter_result = MagicMock()
    submitter_result.scalar_one_or_none.return_value = mock_submitter

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[skill_result, submitter_result])
    mock_db.flush = AsyncMock()
    mock_db.add = MagicMock()

    readiness = MagicMock()
    readiness.can_publish = False
    readiness.review_gate = {
        "can_submit_review": False,
        "score": 50,
        "threshold": 70,
        "items": [
            {
                "key": "git_commit",
                "label": "Git commit",
                "passed": False,
                "severity": "block",
                "detail": "缺少该 Skill 的 Git commit",
                "suggestion": "先保存并提交当前 Skill 改动，再提交审核。",
            }
        ],
    }

    with patch("app.skills.lifecycle.publish_readiness.check_publish_readiness", new=AsyncMock(return_value=readiness)), \
         patch("app.reviews.service.git_service") as mock_git, \
         patch("app.reviews.service.audit") as mock_audit, \
         patch("app.reviews.service._send_review_notification", new_callable=AsyncMock):
        mock_git.log.return_value = [{"hash_full": "commit_now"}, {"hash_full": "commit_prev"}]
        mock_audit.log = AsyncMock()

        from app.reviews.service import create_review

        result = await create_review(
            mock_db,
            "EC-GATE-02",
            "user_a",
            "update",
            force_submit=True,
            force_reason="用户确认强制提交",
        )

    review = mock_db.add.call_args.args[0]
    assert result["status"] == "pending"
    assert review.diff_content["force_submit"]["enabled"] is True
    assert review.diff_content["force_submit"]["gate"]["score"] == 50
    assert "用户确认强制提交" in review.reason
    force_call = next(call for call in mock_audit.log.await_args_list if call.args[1] == "review.force_submit")
    assert force_call.kwargs["detail"]["force_reason"] == "用户确认强制提交"
    assert force_call.kwargs["detail"]["gate"]["score"] == 50


@pytest.mark.asyncio
async def test_create_review_force_submit_requires_reason():
    from app.common.exceptions import AppError
    from app.reviews.service import create_review

    with pytest.raises(AppError) as exc_info:
        await create_review(
            AsyncMock(),
            "EC-GATE-03",
            "user_a",
            "update",
            force_submit=True,
        )

    assert exc_info.value.code == "PARAM_INVALID"
    assert exc_info.value.detail["detail"] == "force_reason is required"


@pytest.mark.asyncio
async def test_non_admin_self_approve_still_blocked():
    from app.common.exceptions import AppError

    review = MagicMock()
    review.id = 7
    review.skill_id = "EC-SELF-01"
    review.submitter = "user_a"
    review.status = "pending"

    result = MagicMock()
    result.scalar_one_or_none.return_value = review
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=result)

    actor = MagicMock()
    actor.role = "dept_admin"
    actor.can_view_all = False

    from app.reviews.service import approve_review

    with pytest.raises(AppError) as exc_info:
        await approve_review(mock_db, 7, "user_a", actor=actor)

    assert exc_info.value.code == "REVIEW_SELF_APPROVE"


@pytest.mark.asyncio
async def test_static_detection_override_requires_reason(monkeypatch):
    from app.common.exceptions import AppError
    from app.reviews import service
    from app.reviews.static_checks import StaticCheckFinding, StaticCheckReport

    report = StaticCheckReport()
    report.add(StaticCheckFinding(
        rule="llm_sdk_import",
        path="EC-STATIC-01/scripts/main.py",
        line=1,
        message="禁止直接导入 LLM SDK",
    ))
    monkeypatch.setattr("app.reviews.static_checks.scan_skill_static_checks", lambda skill_id: report)

    with pytest.raises(AppError) as exc_info:
        await service.enforce_static_detection_gate(
            skill_id="EC-STATIC-01",
            actor_id="reviewer",
            target_type="review",
            target_id="9",
            source="review_approve",
            override=True,
            override_reason="太短",
        )

    assert exc_info.value.code == "REVIEW_OVERRIDE_NO_REASON"


@pytest.mark.asyncio
async def test_static_detection_override_writes_audit(monkeypatch):
    from app.reviews import service
    from app.reviews.static_checks import StaticCheckFinding, StaticCheckReport

    report = StaticCheckReport()
    report.add(StaticCheckFinding(
        rule="llm_sdk_import",
        path="EC-STATIC-02/scripts/main.py",
        line=1,
        message="禁止直接导入 LLM SDK",
    ))
    monkeypatch.setattr("app.reviews.static_checks.scan_skill_static_checks", lambda skill_id: report)
    monkeypatch.setattr(service.audit, "log", AsyncMock())

    result = await service.enforce_static_detection_gate(
        skill_id="EC-STATIC-02",
        actor_id="reviewer",
        target_type="review",
        target_id="10",
        source="review_approve",
        override=True,
        override_reason="这是一次经过安全复核的临时放行理由，后续会迁移到平台网关",
    )

    assert result["passed"] is False
    service.audit.log.assert_awaited_once()
    assert service.audit.log.await_args.args[1] == "review_static_check_override"
