"""Skill管理模块测试"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.common.exceptions import AppError, app_error_handler


def _make_skill_user(*, user_id: str, role: str, department: str = "AI小组", can_view_all: bool = False):
    user = MagicMock()
    user.id = user_id
    user.name = user_id
    user.username = user_id
    user.role = role
    user.department = department
    user.can_view_all = can_view_all
    user.is_active = True
    user.must_change_password = False
    return user


def _build_skills_app(mock_user):
    from app.auth.dependencies import get_current_user
    from app.database import get_db
    from app.skills.router import router as skills_router

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.dependency_overrides[get_current_user] = lambda: mock_user

    async def _fake_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _fake_db
    app.include_router(skills_router, prefix="/api/skills")
    return app


# ===== 列表/详情/历史（已有） =====


@pytest.mark.asyncio
async def test_list_skills(client):
    """测试Skill列表"""
    resp = await client.get("/api/skills/")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert "status_counts" in data
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_list_skills_returns_real_usage_trend(client):
    """Skill 列表返回近 7 天真实执行趋势，不依赖前端固定 sparkline。"""
    from datetime import datetime, time, timedelta
    from uuid import uuid4

    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.execution.models import ExecutionRun
    from app.skills.core.models import Skill

    skill_id = f"TREND-{uuid4().hex[:8]}"
    today = now_bjt().date()

    async with async_session_factory() as session:
        session.add(Skill(
            id=skill_id,
            name="趋势 Skill",
            description="usage trend",
            department="AI小组",
            role="分析师",
            trigger_type="manual",
            risk_level="R2",
            owner="admin",
            status="active",
            current_version="v1.0",
        ))
        run_days = [6, 1, 0, 0, 7]
        for index, day_offset in enumerate(run_days):
            session.add(ExecutionRun(
                id=f"run-{skill_id}-{index}",
                skill_id=skill_id,
                trigger_type="manual",
                run_mode="manual_real",
                status="completed",
                started_at=datetime.combine(today - timedelta(days=day_offset), time(hour=10, minute=index)),
            ))
        await session.commit()

    resp = await client.get("/api/skills/", params={"q": skill_id, "page_size": 10})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["id"] == skill_id
    assert item["usage_count"] == 5
    assert item["usage_today"] == 2

    trend = item["usage_trend"]
    assert len(trend) == 7
    assert [point["date"] for point in trend] == [
        (today - timedelta(days=offset)).isoformat()
        for offset in range(6, -1, -1)
    ]
    assert [point["count"] for point in trend] == [1, 0, 0, 0, 0, 1, 2]


@pytest.mark.asyncio
async def test_list_skills_view_filter_uses_server_side_counts(client):
    """Skills 左侧视图筛选不能只过滤当前页；后端返回全量 view_counts。"""
    from uuid import uuid4

    from app.database import async_session_factory
    from app.skills.core.models import Skill, UserSkillPin

    prefix = f"VIEW-{uuid4().hex[:8]}"
    bad_id = f"{prefix}-bad"
    good_id = f"{prefix}-good"

    async with async_session_factory() as session:
        session.add_all([
            Skill(
                id=bad_id,
                name="不健康 Skill",
                description="bad health",
                department="AI小组",
                role="分析师",
                trigger_type="manual",
                risk_level="R2",
                owner="admin",
                status="active",
                current_version="v1.0",
            ),
            Skill(
                id=good_id,
                name="健康 Skill",
                description="good health",
                department="AI小组",
                role="分析师",
                trigger_type="manual",
                risk_level="R1",
                owner="other-owner",
                status="active",
                current_version="v1.0",
            ),
            UserSkillPin(user_id="admin", skill_id=good_id),
        ])
        await session.commit()

    async def fake_health(_db, skill_id: str):
        return {"skill_id": skill_id, "score": 62 if skill_id == bad_id else 94, "breakdown": {}}

    with patch("app.dashboard.service.get_skill_health_score", new=fake_health):
        resp = await client.get(
            "/api/skills/",
            params={"q": prefix, "include_health": True, "view_filter": "unhealthy", "page_size": 10},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert [item["id"] for item in data["items"]] == [bad_id]
    assert data["items"][0]["health_score"] == 62
    assert data["view_counts"]["mine_created"] == 1
    assert data["view_counts"]["favorited"] == 1
    assert data["view_counts"]["unhealthy"] == 1


@pytest.mark.asyncio
async def test_get_skill_detail(client):
    """测试Skill详情"""
    resp = await client.get("/api/skills/EC-投放-01")
    if resp.status_code == 200:
        data = resp.json()
        assert data["id"] == "EC-投放-01"
        assert "parsed" in data
        assert "steps" in data["parsed"]


@pytest.mark.asyncio
async def test_get_skill_not_found(client):
    """测试不存在的Skill"""
    resp = await client.get("/api/skills/NOT-EXIST-99")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_unified_create_architect_converts_skill_dict_to_skill_structure(client):
    from app.workbench.schemas import SkillStructure

    captured: dict = {}

    async def _fake_create(draft, user_id, skill_id=None):
        captured["draft"] = draft
        captured["user_id"] = user_id
        captured["skill_id"] = skill_id
        return {"skill_id": "ARCHITECT-001", "git_commit": "deadbeef", "quality_score": 92}

    with patch(
        "app.skills.router.workbench_service_module.workbench_service.create_skill_from_draft",
        new=AsyncMock(side_effect=_fake_create),
    ):
        resp = await client.post(
            "/api/skills/create",
            json={
                "skill_id": "ARCHITECT-001",
                "source": {
                    "type": "architect",
                    "payload": {
                        "skill_dict": {
                            "meta": {
                                "name": "架构生成Skill",
                                "department": "AI小组",
                                "trigger_type": "manual",
                                "risk_level": "R2",
                            },
                            "goal": "根据访谈结果生成可落地的 Skill 草稿",
                            "rules": [{"id": "rule-1", "name": "判断", "branches": []}],
                            "params": [{"name": "threshold", "default_value": 1.5}],
                            "output_table": [{"name": "result", "format": "text", "recipient": "运营"}],
                            "test_cases": [{"name": "case-1", "input_data": {}, "expected_output": {}}],
                            "workflow": {},
                        }
                    },
                },
            },
        )

    assert resp.status_code == 200
    assert isinstance(captured["draft"], SkillStructure)
    assert captured["draft"].meta["name"] == "架构生成Skill"
    assert captured["draft"].goal == "根据访谈结果生成可落地的 Skill 草稿"
    assert captured["user_id"] == "admin"
    assert captured["skill_id"] == "ARCHITECT-001"
    assert resp.json()["skill_id"] == "ARCHITECT-001"


def test_build_skill_manifest_from_dir(tmp_path):
    from app.skills.tooling.manifest_service import build_skill_manifest_from_dir, render_manifest_summary

    skill_dir = tmp_path / "EC-投放-01"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "tests").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# demo", encoding="utf-8")
    (skill_dir / "policy_pack.yaml").write_text("roi: 1.5\n", encoding="utf-8")
    (skill_dir / "scripts" / "main.py").write_text('"""分析昨日数据并输出建议"""', encoding="utf-8")
    (skill_dir / "references" / "api.md").write_text("api", encoding="utf-8")
    (skill_dir / "tests" / "test_main.py").write_text("def test_ok(): pass", encoding="utf-8")

    manifest = build_skill_manifest_from_dir(skill_dir, skill_id="EC-投放-01")
    assert manifest["skill_id"] == "EC-投放-01"
    assert manifest["key_files"]["skill_md"] is True
    assert manifest["key_files"]["policy_pack"] is True
    assert manifest["key_files"]["main_script"] is True
    assert manifest["script_count"] == 1
    assert manifest["reference_count"] == 1
    assert manifest["scripts"][0]["path"] == "scripts/main.py"
    assert "分析昨日数据并输出建议" in manifest["scripts"][0]["purpose"]

    summary = render_manifest_summary(manifest)
    assert "Skill Manifest" in summary
    assert "scripts/main.py" in summary
    assert "policy_pack=yes" in summary


def test_effective_skill_git_commit_prefers_repo_head(monkeypatch):
    from app.skills.lifecycle import service

    class SkillRow:
        id = "DEMO-SKILL"
        git_commit = "oldcommit"

    latest = {
        "hash": "newcommi",
        "hash_full": "newcommit1234567890",
        "message": "更新 SKILL.md",
        "date": "2026-04-23T00:00:00",
    }
    monkeypatch.setattr(service.git_service, "log", lambda skill_id, max_count=1: [latest])

    effective, git_head, synced = service._effective_skill_git_commit(SkillRow())

    assert effective == latest["hash_full"]
    assert git_head == latest
    assert synced is False


def test_list_scripts_from_manifest(tmp_path, monkeypatch):
    from app.skills import service as skill_service

    skill_dir = tmp_path / "EC-投放-01"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "main.py").write_text('"""脚本说明"""', encoding="utf-8")
    monkeypatch.setattr(skill_service.git_service, "_repo_path", tmp_path)

    items = skill_service.list_scripts("EC-投放-01")
    assert len(items) == 1
    assert items[0]["path"] == "scripts/main.py"
    assert "脚本说明" in items[0]["purpose"]


def test_run_script_uses_sandbox(tmp_path, monkeypatch):
    from app.skills import service as skill_service

    skill_dir = tmp_path / "EC-投放-01"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "main.py").write_text("def execute(x): return {'ok': True}", encoding="utf-8")
    monkeypatch.setattr(skill_service.git_service, "_repo_path", tmp_path)

    def fake_run(script_path, payload=None, timeout=10):
        assert script_path.endswith("scripts/main.py")
        assert payload == {"x": 1}
        assert timeout == 12
        return {"success": True, "output": {"ok": True}, "error": None, "duration_ms": 5}

    monkeypatch.setattr("app.sandbox.executor.run_script", fake_run)
    result = skill_service.run_script("EC-投放-01", "scripts/main.py", payload={"x": 1}, timeout=12)
    assert result["success"] is True
    assert result["output"] == {"ok": True}


def test_run_script_rejects_non_scripts_path(tmp_path, monkeypatch):
    from app.skills import service as skill_service
    from app.common.exceptions import AppError

    skill_dir = tmp_path / "EC-投放-01"
    skill_dir.mkdir(parents=True)
    monkeypatch.setattr(skill_service.git_service, "_repo_path", tmp_path)

    with pytest.raises(AppError):
        skill_service.run_script("EC-投放-01", "README.md", payload={})


@pytest.mark.asyncio
async def test_list_skill_scripts_api(client, monkeypatch):
    async def fake_get_skill(_db, skill_id):
        return {"id": skill_id, "department": "AI"}

    def fake_list_scripts(skill_id):
        return [{"path": "scripts/main.py", "purpose": "运行主流程", "bytes": 123}]

    async def fake_ensure_access(_db, _sid, _user, _action="read"):
        return MagicMock(id="EC-投放-01", department="AI")

    monkeypatch.setattr("app.skills.router_files.service.get_skill", fake_get_skill)
    monkeypatch.setattr("app.skills.router_files.service.list_scripts", fake_list_scripts)
    monkeypatch.setattr("app.skills.router_files.ensure_skill_access", fake_ensure_access)

    resp = await client.get("/api/skills/EC-投放-01/scripts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"][0]["path"] == "scripts/main.py"


@pytest.mark.asyncio
async def test_run_skill_script_api(client, monkeypatch):
    async def fake_get_skill(_db, skill_id):
        return {"id": skill_id, "department": "AI"}

    def fake_run_script(skill_id, path, payload=None, timeout=10):
        assert skill_id == "EC-投放-01"
        assert path == "scripts/main.py"
        assert payload == {"x": 1}
        assert timeout == 12
        return {"script": path, "success": True, "output": {"ok": True}, "error": None, "duration_ms": 7, "raw": None}

    async def fake_ensure_access(_db, _sid, _user, _action="read"):
        return MagicMock(id="EC-投放-01", department="AI")

    monkeypatch.setattr("app.skills.router_files.service.get_skill", fake_get_skill)
    monkeypatch.setattr("app.skills.router_files.service.run_script", fake_run_script)
    monkeypatch.setattr("app.skills.router_files.ensure_skill_access", fake_ensure_access)

    resp = await client.post(
        "/api/skills/EC-投放-01/scripts/run",
        json={"path": "scripts/main.py", "payload": {"x": 1}, "timeout": 12},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["output"] == {"ok": True}


@pytest.mark.asyncio
async def test_skill_history(client):
    """测试版本历史"""
    resp = await client.get("/api/skills/EC-投放-01/history")
    if resp.status_code == 200:
        assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_scan_repo_dry_run_allows_ai_engineer():
    app = _build_skills_app(_make_skill_user(user_id="ae-1", role="ai_engineer"))

    with patch(
        "app.skills.router.service.scan_repo",
        new=AsyncMock(return_value={"found": 1, "imported": [], "skipped": ["SK-001"], "errors": [], "commit_sha": None, "dry_run": True}),
    ) as mock_scan, patch("app.common.audit.audit.log", new=AsyncMock()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/api/skills/scan-repo?dry_run=true")

    assert resp.status_code == 200
    assert resp.json()["dry_run"] is True
    assert mock_scan.await_args.kwargs["user_id"] == "ae-1"
    assert mock_scan.await_args.kwargs["dry_run"] is True


@pytest.mark.asyncio
async def test_scan_repo_non_dry_run_rejects_ai_engineer_even_with_confirm():
    app = _build_skills_app(_make_skill_user(user_id="ae-1", role="ai_engineer"))

    with patch("app.skills.router.service.scan_repo", new=AsyncMock()) as mock_scan, \
         patch("app.common.audit.audit.log", new=AsyncMock()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/skills/scan-repo",
                headers={"X-Confirm": "I-UNDERSTAND"},
            )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTH_PERMISSION_DENIED"
    mock_scan.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_repo_non_dry_run_requires_confirm_and_allows_system_admin():
    app = _build_skills_app(_make_skill_user(user_id="sys-1", role="system_admin", can_view_all=True))

    with patch(
        "app.skills.router.service.scan_repo",
        new=AsyncMock(return_value={"found": 0, "imported": [], "skipped": [], "errors": [], "commit_sha": None, "dry_run": False}),
    ) as mock_scan, patch("app.common.audit.audit.log", new=AsyncMock()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            missing_confirm = await ac.post("/api/skills/scan-repo")
            confirmed = await ac.post(
                "/api/skills/scan-repo",
                headers={"X-Confirm": "I-UNDERSTAND"},
            )

    assert missing_confirm.status_code == 400
    assert missing_confirm.json()["error"]["code"] == "CONFIRM_REQUIRED"
    assert confirmed.status_code == 200
    assert confirmed.json()["dry_run"] is False
    assert mock_scan.await_count == 1
    assert mock_scan.await_args.kwargs["user_id"] == "sys-1"
    assert mock_scan.await_args.kwargs["dry_run"] is False


@pytest.mark.asyncio
async def test_scan_repo_forwards_target_skill_ids():
    app = _build_skills_app(_make_skill_user(user_id="sys-1", role="system_admin", can_view_all=True))

    with patch(
        "app.skills.router.service.scan_repo",
        new=AsyncMock(return_value={
            "found": 1,
            "imported": [{"id": "target-skill"}],
            "skipped": [],
            "errors": [],
            "commit_sha": None,
            "dry_run": False,
            "requested": ["target-skill"],
        }),
    ) as mock_scan, patch("app.common.audit.audit.log", new=AsyncMock()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/skills/scan-repo?skill_id=target-skill",
                headers={"X-Confirm": "I-UNDERSTAND"},
            )

    assert resp.status_code == 200
    assert resp.json()["requested"] == ["target-skill"]
    assert mock_scan.await_args.kwargs["skill_ids"] == ["target-skill"]


def test_validate_params_block():
    import asyncio
    from app.skills.tooling.validation_service import validate_block

    result = asyncio.run(validate_block(None, "SK-1", "params", [
        {"name": "roi_threshold", "default_value": 1.5},
        {"name": "budget_limit", "default_value": 100},
    ]))
    assert result["valid"] is True


@pytest.mark.asyncio
async def test_read_skill_module_api(client, monkeypatch):
    async def fake_get_skill(_db, skill_id):
        return {
            "id": skill_id,
            "department": "AI",
            "policy_pack": {"roi": 1.5},
            "parsed": {
                "frontmatter": {"name": "Test"},
                "purpose": "目标",
                "steps": [{"id": "step_1"}],
                "output_definition": [{"name": "结论"}],
                "antipatterns": [{"scenario": "误判"}],
                "data_inputs": [{"name": "日报"}],
                "test_cases": [{"name": "case1"}],
            },
        }

    async def fake_ensure_access(_db, _sid, _user, _action="read"):
        return MagicMock(id="EC-投放-01", department="AI")

    monkeypatch.setattr("app.skills.router_quality.service.get_skill", fake_get_skill)
    monkeypatch.setattr("app.skills.router_quality.ensure_skill_access", fake_ensure_access)
    resp = await client.get("/api/skills/EC-投放-01/modules/params")
    assert resp.status_code == 200
    data = resp.json()
    assert data["module"] == "params"
    assert data["data"][0]["name"] == "roi"


@pytest.mark.asyncio
async def test_validate_skill_module_api(client, monkeypatch):
    async def fake_get_skill(_db, skill_id):
        return {"id": skill_id, "department": "AI", "parsed": {}, "policy_pack": {}}

    async def fake_validate(_db, skill_id, block_type, content):
        return {"skill_id": skill_id, "block_type": block_type, "valid": True, "content": content}

    async def fake_ensure_access(_db, _sid, _user, _action="read"):
        return MagicMock(id="EC-投放-01", department="AI")

    monkeypatch.setattr("app.skills.router_quality.service.get_skill", fake_get_skill)
    monkeypatch.setattr("app.skills.router_quality.validation_service.validate_block", fake_validate)
    monkeypatch.setattr("app.skills.router_quality.ensure_skill_access", fake_ensure_access)
    resp = await client.post(
        "/api/skills/EC-投放-01/modules/params/validate",
        json={"content": [{"name": "roi", "default_value": 1.5}]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["block_type"] == "params"
    assert data["valid"] is True


@pytest.mark.asyncio
async def test_apply_skill_module_patch_api(client, monkeypatch):
    async def fake_get_skill(_db, skill_id):
        return {"id": skill_id, "department": "AI", "parsed": {}, "policy_pack": {}}

    async def fake_save_structured(_db, skill_id, user_id="system", **kwargs):
        return {"skill_id": skill_id, "user_id": user_id, "kwargs": kwargs, "noop": False}

    async def fake_ensure_access(_db, _sid, _user, _action="read"):
        return MagicMock(id="EC-投放-01", department="AI")

    monkeypatch.setattr("app.skills.router_quality.service.get_skill", fake_get_skill)
    monkeypatch.setattr("app.skills.router_quality.service.save_skill_structured", fake_save_structured)
    monkeypatch.setattr("app.skills.router_quality.ensure_skill_access", fake_ensure_access)
    monkeypatch.setattr("app.skills.router_quality.invalidate_skill", AsyncMock())

    resp = await client.post(
        "/api/skills/EC-投放-01/modules/params/apply",
        json={"content": [{"name": "roi", "default_value": 1.5}]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["skill_id"] == "EC-投放-01"
    assert data["kwargs"]["policy_pack"] == {"roi": 1.5}


# ===== 校验（已有） =====


@pytest.mark.asyncio
async def test_validate_skill(client):
    """测试Skill校验接口"""
    resp = await client.post("/api/skills/EC-投放-01/validate")
    if resp.status_code == 200:
        data = resp.json()
        assert "valid" in data
        assert "blocks" in data
        assert isinstance(data["blocks"], list)
        # 检查所有块都有name和status
        for block in data["blocks"]:
            assert "name" in block
            assert block["status"] in ("pass", "warning", "fail", "skip")
            assert "message" in block


@pytest.mark.asyncio
async def test_validate_skill_not_found(client):
    """测试校验不存在的Skill"""
    resp = await client.post("/api/skills/NOT-EXIST-99/validate")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_validate_skill_blocks():
    """测试validate_skill逻辑——各块校验"""
    from app.skills.core.parser import SkillStructured, DecisionStep, Branch, Antipattern, OutputItem, TestCase

    # 构造一个完整的Skill
    parsed = SkillStructured(
        frontmatter={
            "name": "测试",
            "description": "这是一个完整的测试 Skill 描述",
            "department": "电商",
            "trigger_type": "cron",
        },
        purpose="这是一个足够长的目的描述文本内容",
        steps=[DecisionStep(id="1", name="步骤1", branches=[Branch(condition="条件A")])],
        antipatterns=[Antipattern(scenario="误判1", correct_action="正确做法1")],
        output_definition=[OutputItem(name="报告", format="钉钉", recipient="运营", approval_level="L0")],
        test_cases=[
            TestCase(name="用例1", input_data={"a": 1}, expected_output={"b": 2}),
            TestCase(name="用例2", input_data={"a": 2}, expected_output={"b": 3}),
            TestCase(name="用例3", input_data={"a": 3}, expected_output={"b": 4}),
        ],
    )

    skill_md = "---\nname: 测试\ndescription: 这是一个完整的测试 Skill 描述\ndepartment: 电商\ntrigger_type: cron\n---\n"

    # 构造mock db session
    mock_skill = MagicMock()
    mock_skill.id = "TEST-01"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_skill
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("app.skills.validation_service.git_service") as mock_git, \
         patch("app.skills.validation_service.skill_parser") as mock_parser:
        mock_git.read_file.side_effect = lambda sid, path: {
            "SKILL.md": skill_md,
            "policy_pack.yaml": "roi_threshold: 1.5\n",
            "scripts/main.py": "# 主脚本",
        }.get(path)
        mock_parser.parse.return_value = parsed

        from app.skills.tooling.validation_service import validate_skill
        result = await validate_skill(mock_db, "TEST-01")

    assert result["valid"] is True
    assert result["skill_id"] == "TEST-01"
    # 所有块应该都是pass或skip
    for block in result["blocks"]:
        assert block["status"] in ("pass", "skip"), f"{block['name']} 未通过: {block['message']}"


@pytest.mark.asyncio
async def test_validate_skill_failures():
    """测试validate_skill逻辑——各种失败情况"""
    from app.skills.core.parser import SkillStructured

    # 构造一个几乎空的Skill
    parsed = SkillStructured(
        frontmatter={"name": "测试"},  # 缺少department和trigger_type
        purpose="太短",               # 不到10字符
    )

    mock_skill = MagicMock()
    mock_skill.id = "TEST-02"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_skill
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("app.skills.validation_service.git_service") as mock_git, \
         patch("app.skills.validation_service.skill_parser") as mock_parser:
        mock_git.read_file.side_effect = lambda sid, path: {
            "SKILL.md": "---\nname: 测试\n---\n",
            "policy_pack.yaml": None,   # 文件不存在
            "scripts/main.py": None,    # 文件不存在
        }.get(path)
        mock_parser.parse.return_value = parsed

        from app.skills.tooling.validation_service import validate_skill
        result = await validate_skill(mock_db, "TEST-02")

    assert result["valid"] is False
    block_map = {b["name"]: b for b in result["blocks"]}
    assert block_map["frontmatter"]["status"] == "fail"
    assert block_map["purpose"]["status"] == "fail"
    assert block_map["steps"]["status"] == "fail"
    assert block_map["antipatterns"]["status"] == "warning"
    assert block_map["output_definition"]["status"] == "warning"
    assert block_map["policy_pack"]["status"] == "fail"
    assert block_map["test_cases"]["status"] == "fail"
    assert block_map["script"]["status"] == "fail"


@pytest.mark.asyncio
async def test_validate_skill_accepts_contract_driven_assets():
    """新版 Skill 可用 contract/frontmatter、Markdown 决策阶梯、fixtures/tests 通过提交前校验。"""
    from app.skills.core.parser import Antipattern, OutputItem, SkillStructured

    parsed = SkillStructured(
        frontmatter={
            "name": "contract-skill",
            "description": "使用 contract 和 Markdown 决策阶梯的现代 Skill",
            "department": "EC",
            "trigger_type": "manual",
            "params": {"target_date": {"type": "string", "required": True}},
        },
        purpose="分析客服会话质量并生成报告和待办",
        steps=[],
        antipatterns=[Antipattern(scenario="误判标签", correct_action="先做交叉验证")],
        output_definition=[OutputItem(name="reports", format="json", recipient="收件", approval_level="L0")],
        test_cases=[],
    )

    skill_md = """---
name: contract-skill
description: 使用 contract 和 Markdown 决策阶梯的现代 Skill
department: EC
trigger_type: manual
params:
  target_date:
    type: string
    required: true
---

# contract-skill

## 决策阶梯

### step_1：数据质量是否达标？

| 条件 | 结论 | 动作 | 下一步 |
|------|------|------|--------|
| 有效会话占比 >= 50% | 数据可用 | 进入标签验证 | step_2 |
"""
    contract = {
        "input": [{"name": "target_date", "type": "string", "required": True}],
        "output_schema": {"type": "object", "properties": {"reports": {"type": "array"}}},
    }

    mock_skill = MagicMock()
    mock_skill.id = "TEST-CONTRACT"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_skill
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("app.skills.validation_service.git_service") as mock_git, \
         patch("app.skills.validation_service.skill_parser") as mock_parser:
        mock_git.read_file.side_effect = lambda sid, path: {
            "SKILL.md": skill_md,
            "policy_pack.yaml": None,
            "contract.json": json.dumps(contract),
            "scripts/main.py": "# 主脚本",
        }.get(path)
        mock_git.list_skill_files.return_value = [
            "fixtures/sample_input.json",
            "tests/test_main.py",
        ]
        mock_parser.parse.return_value = parsed

        from app.skills.tooling.validation_service import validate_skill
        result = await validate_skill(mock_db, "TEST-CONTRACT")

    block_map = {b["name"]: b for b in result["blocks"]}
    assert result["valid"] is True
    assert block_map["steps"]["status"] == "pass"
    assert block_map["policy_pack"]["status"] == "pass"
    assert block_map["test_cases"]["status"] == "pass"


# ===== 解析器（已有+增强） =====


@pytest.mark.asyncio
async def test_parser_antipatterns_bold_markers():
    """测试解析器：反例章节的bold标记（**误判场景** / **正确做法** / **来源**）"""
    from app.skills.core.parser import skill_parser

    md = """---
name: 反例Bold测试
department: 测试
---

# 反例Bold测试

## 反例

1. **误判场景**: 将测试误判为上线
   **正确做法**: 增加预热期判断
   **来源**: QA反馈-2026

2. **误判场景**: 将灰度误判为全量
   **正确做法**: 检查流量比例
"""
    parsed = skill_parser.parse(md)
    assert len(parsed.antipatterns) == 2

    ap1 = parsed.antipatterns[0]
    assert ap1.scenario == "将测试误判为上线"
    assert ap1.correct_action == "增加预热期判断"
    assert ap1.source == "QA反馈-2026"

    ap2 = parsed.antipatterns[1]
    assert ap2.scenario == "将灰度误判为全量"
    assert ap2.correct_action == "检查流量比例"
    assert ap2.source == ""  # 没有来源行时为空字符串


def test_parser_roundtrip():
    """测试SKILL.md解析器双向转换"""
    from app.skills.core.parser import skill_parser

    md = """---
name: 测试Skill
department: 测试部门
---

# 测试Skill

## 目的

这是测试用途。

## 反例

1. **误判场景**: 测试误判
   **正确做法**: 正确处理
"""
    parsed = skill_parser.parse(md)
    assert parsed.frontmatter["name"] == "测试Skill"
    assert "测试用途" in parsed.purpose
    assert len(parsed.antipatterns) == 1

    rendered = skill_parser.render(parsed)
    re_parsed = skill_parser.parse(rendered)
    assert re_parsed.frontmatter["name"] == "测试Skill"
    assert len(re_parsed.antipatterns) == 1


@pytest.mark.asyncio
async def test_save_skill_content_syncs_frontmatter_approval_level(client, monkeypatch, tmp_path):
    # 使用 client fixture 把 async_session_factory 切到测试 DB, 避免污染生产
    import shutil
    from git import Repo
    from app.config import settings
    from app.database import async_session_factory
    from app.skills.core.git_service import git_service
    from app.skills.lifecycle.service import create_skill, save_skill_content
    from app.skills.core.models import Skill
    from sqlalchemy import select
    from uuid import uuid4
    skill_id = f"SYNC-APPROVAL-{uuid4().hex[:8]}"
    monkeypatch.setattr(settings, "AICLAW_SKILLS_DIR", "")
    repo = Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "test")
        cw.set_value("user", "email", "test@example.com")
    monkeypatch.setattr(git_service, "_repo_path", tmp_path)
    monkeypatch.setattr(git_service, "_repo", None)

    try:
        async with async_session_factory() as session:
            await create_skill(
                session,
                skill_id=skill_id,
                name="同步测试",
                department="AI",
                role="工程师",
                trigger_type="manual",
                risk_level="R2",
                approval_level=0,
                skill_md="---\nname: 同步测试\ndescription: 同步测试 Skill\ndepartment: AI\ntrigger_type: manual\nrisk_level: R2\n---\n\n# 同步测试\n",
                user_id="admin",
            )
            await session.commit()

        new_md = """---
name: 同步测试
description: 同步测试 Skill
department: AI
trigger_type: manual
risk_level: R2
approval_level: 2
reviewer:
  - alice
decision_mode: all_of
---

# 同步测试
"""

        async with async_session_factory() as session:
            await save_skill_content(
                session,
                skill_id,
                skill_md=new_md,
                user_id="admin",
            )
            await session.commit()

        async with async_session_factory() as session:
            skill = (await session.execute(select(Skill).where(Skill.id == skill_id))).scalar_one()
            assert skill.approval_level == 2
    finally:
        # 清理 skills-repo 里产生的临时目录, 避免污染
        sd = git_service.skill_dir(skill_id)
        if sd.exists():
            shutil.rmtree(sd)
        git_service._repo = None


# ===== 新增: Skill创建 =====


@pytest.mark.asyncio
async def test_create_skill_service():
    """测试通过service创建Skill——验证数据库写入和Git commit"""
    mock_skill_result = MagicMock()
    mock_skill_result.scalar_one_or_none.return_value = None  # 不存在

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_skill_result)
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    with patch("app.skills.service.git_service") as mock_git, \
         patch("app.skills.service.audit") as mock_audit:
        mock_git.commit_all.return_value = "abc123"
        mock_audit.log = AsyncMock()

        from app.skills.lifecycle.service import create_skill
        result = await create_skill(
            mock_db,
            skill_id="TEST-新建-01",
            name="测试新建Skill",
            department="测试部",
            role="运营",
            trigger_type="cron",
            user_id="admin",
        )

    assert result["skill_id"] == "TEST-新建-01"
    assert result["git_commit"] == "abc123"
    mock_git.create_skill_dir.assert_called_once_with("TEST-新建-01")
    # 验证write_file被调用过，且第一个调用是SKILL.md
    write_calls = mock_git.write_file.call_args_list
    assert any(c.args[1] == "SKILL.md" for c in write_calls), "SKILL.md should be written"
    script_writes = [c for c in write_calls if c.args[1] == "scripts/main.py"]
    assert script_writes, "scripts/main.py should be written"
    script_content = script_writes[0].args[2]
    assert "def main(payload" in script_content
    assert "空白草稿" in script_content
    assert "待实现" not in script_content
    assert "请补充业务逻辑" not in script_content
    # create_skill adds both Skill and SkillMember (owner)
    assert mock_db.add.call_count >= 1
    mock_audit.log.assert_called_once()


@pytest.mark.asyncio
async def test_create_skill_duplicate():
    """测试创建已存在的Skill——应返回409"""
    from app.common.exceptions import AppError

    existing_skill = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_skill  # 已存在

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(AppError) as exc_info:
        from app.skills.lifecycle.service import create_skill
        await create_skill(
            mock_db,
            skill_id="EXISTS-01",
            name="已存在",
            department="测试部",
            role="",
            user_id="admin",
        )

    assert exc_info.value.code == "SKILL_ALREADY_EXISTS"


# ===== 新增: Skill内容保存 =====


@pytest.mark.asyncio
async def test_save_skill_content_service():
    """测试保存Skill内容——SKILL.md + policy_pack + script"""
    from app.skills.core.parser import SkillStructured

    mock_skill = MagicMock()
    mock_skill.id = "SAVE-01"
    mock_skill.name = "旧名"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_skill

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.flush = AsyncMock()

    with patch("app.skills.service.git_service") as mock_git, \
         patch("app.skills.service.skill_parser") as mock_parser, \
         patch("app.skills.service.audit") as mock_audit:
        mock_parser.parse.return_value = SkillStructured(
            frontmatter={"name": "新名", "department": "电商", "trigger_type": "cron"}
        )
        mock_git.commit_all.return_value = "def456"
        mock_audit.log = AsyncMock()

        from app.skills.lifecycle.service import save_skill_content
        result = await save_skill_content(
            mock_db,
            skill_id="SAVE-01",
            skill_md="---\nname: 新名\ndescription: 测试 Skill\ntrigger_type: manual\nrisk_level: R1\n---\n",
            policy_pack_raw="threshold: 2.0\n",
            script_content="print('hello')",
            user_id="admin",
        )

    assert result["skill_id"] == "SAVE-01"
    assert result["git_commit"] == "def456"
    assert "SKILL.md" in result["changes"]
    assert "policy_pack.yaml" in result["changes"]
    assert "scripts/main.py" in result["changes"]
    # 验证frontmatter同步到数据库
    assert mock_skill.name == "新名"


# ===== 新增: 参数影响预览 =====


@pytest.mark.asyncio
async def test_compare_params_no_change():
    """测试参数对比——无变化时返回空列表"""
    mock_skill_result = MagicMock()
    mock_skill_result.scalar_one_or_none.return_value = MagicMock()
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_skill_result)

    with patch("app.skills.validation_service.git_service") as mock_git:
        mock_git.read_file.return_value = "roi_threshold: 1.5\n"

        from app.skills.tooling.validation_service import compare_params
        result = await compare_params(
            mock_db, "TEST-01", {"roi_threshold": 1.5}
        )

    assert result["changed_params"] == []
    assert result["affected_decisions"] == 0


@pytest.mark.asyncio
async def test_compare_params_with_changes():
    """测试参数对比——有变化时返回受影响的决策"""
    from app.execution.models import DecisionLog

    mock_db = AsyncMock()

    # 模拟决策记录：input_snapshot中包含被修改参数的引用
    mock_log = MagicMock(spec=DecisionLog)
    mock_log.id = 1
    mock_log.input_snapshot = {"roi_threshold": 1.5, "data": [1, 2]}
    mock_log.output_result = {"decision": "绿灯"}
    mock_log.created_at = MagicMock()
    mock_log.created_at.isoformat.return_value = "2026-01-01T00:00:00"
    mock_log.user_action = "completed"

    mock_logs_result = MagicMock()
    mock_logs_result.scalars.return_value.all.return_value = [mock_log]
    mock_db.execute = AsyncMock(return_value=mock_logs_result)

    with patch("app.skills.validation_service.git_service") as mock_git:
        mock_git.read_file.return_value = "roi_threshold: 1.5\n"

        from app.skills.tooling.validation_service import compare_params
        result = await compare_params(
            mock_db, "TEST-01", {"roi_threshold": 2.0}
        )

    assert len(result["changed_params"]) == 1
    assert result["changed_params"][0]["name"] == "roi_threshold"
    assert result["total_decisions"] == 1


# ===== 新增: 生成from周报（mock LLM） =====


@pytest.mark.asyncio
async def test_generate_from_report_success():
    """测试从周报生成SKILL.md——LLM返回结构化JSON"""
    mock_llm_response = {
        "choices": [{
            "message": {
                "content": '{"skill_name":"周报Skill","purpose":"测试目的","steps":[{"name":"步骤1","branches":[{"condition":"ROI>1.5","conclusion":"绿灯"}]}],"params":{"roi_threshold":1.5},"antipatterns":[]}'
            }
        }]
    }

    with patch("app.skills.generation_service.httpx.AsyncClient") as MockClient:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_llm_response

        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        mock_instance.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_instance

        from app.skills.intelligence.generation_service import generate_from_report
        result = await generate_from_report("本周ROI 1.5的计划加预算", "电商", "运营")

    assert "skill_md_draft" in result
    assert isinstance(result["skill_md_draft"], str)


@pytest.mark.asyncio
async def test_generate_from_report_llm_timeout():
    """测试从周报生成——LLM超时"""
    import httpx
    from app.common.exceptions import AppError

    with patch("app.skills.generation_service.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        mock_instance.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        MockClient.return_value = mock_instance

        with pytest.raises(AppError) as exc_info:
            from app.skills.intelligence.generation_service import generate_from_report
            await generate_from_report("测试内容", "电商", "运营")

    assert exc_info.value.code == "LLM_TIMEOUT"


# ===== 新增: 影子运行 =====


@pytest.mark.asyncio
async def test_start_shadow_service():
    """测试开始影子运行：draft → shadow"""
    from datetime import date

    mock_skill = MagicMock()
    mock_skill.id = "SHADOW-01"
    mock_skill.status = "draft"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_skill
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.flush = AsyncMock()

    with patch("app.skills.shadow_service.audit") as mock_audit:
        mock_audit.log = AsyncMock()

        from app.skills.lifecycle.shadow_service import start_shadow
        result = await start_shadow(mock_db, "SHADOW-01", "admin")

    assert result["status"] == "shadow"
    assert mock_skill.status == "shadow"
    assert mock_skill.shadow_start_date == date.today()


@pytest.mark.asyncio
async def test_stop_shadow_service():
    """测试停止影子运行：shadow → draft"""
    mock_skill = MagicMock()
    mock_skill.id = "SHADOW-02"
    mock_skill.status = "shadow"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_skill
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.flush = AsyncMock()

    with patch("app.skills.shadow_service.audit") as mock_audit:
        mock_audit.log = AsyncMock()

        from app.skills.lifecycle.shadow_service import stop_shadow
        result = await stop_shadow(mock_db, "SHADOW-02", "admin")

    assert result["status"] == "draft"
    assert mock_skill.status == "draft"
    assert mock_skill.shadow_start_date is None


@pytest.mark.asyncio
async def test_stop_shadow_not_in_shadow_state():
    """测试非影子状态下stop_shadow——应失败"""
    from app.common.exceptions import AppError

    mock_skill = MagicMock()
    mock_skill.id = "SHADOW-03"
    mock_skill.status = "active"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_skill
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("app.skills.shadow_service.audit"):
        with pytest.raises(AppError) as exc_info:
            from app.skills.lifecycle.shadow_service import stop_shadow
            await stop_shadow(mock_db, "SHADOW-03", "admin")

    assert exc_info.value.code == "SKILL_VERSION_CONFLICT"


@pytest.mark.asyncio
async def test_promote_shadow_too_early():
    """测试影子转正——运行不足3天应拒绝"""
    from datetime import date
    from app.common.exceptions import AppError

    mock_skill = MagicMock()
    mock_skill.id = "SHADOW-04"
    mock_skill.status = "shadow"
    mock_skill.shadow_start_date = date.today()  # 今天才开始，不足3天

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_skill
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("app.skills.shadow_service.audit"):
        with pytest.raises(AppError) as exc_info:
            from app.skills.lifecycle.shadow_service import promote_shadow
            await promote_shadow(mock_db, "SHADOW-04", "admin")

    assert exc_info.value.code == "SKILL_VERSION_CONFLICT"
    assert "至少需要3天" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_shadow_stats_service():
    """测试影子运行统计"""
    from datetime import date

    mock_skill = MagicMock()
    mock_skill.id = "SHADOW-05"
    mock_skill.status = "shadow"
    mock_skill.shadow_start_date = date.today()

    # 构造影子决策日志
    mock_log1 = MagicMock()
    mock_log1.user_action = "completed"
    mock_log1.created_at = MagicMock()
    mock_log1.created_at.strftime.return_value = "2026-03-30"

    mock_log2 = MagicMock()
    mock_log2.user_action = "rejected"
    mock_log2.created_at = MagicMock()
    mock_log2.created_at.strftime.return_value = "2026-03-30"

    mock_log3 = MagicMock()
    mock_log3.user_action = None  # 尚无人工记录
    mock_log3.created_at = MagicMock()
    mock_log3.created_at.strftime.return_value = "2026-03-31"

    # 第一次execute返回skill，第二次返回logs
    call_count = [0]
    def mock_execute(stmt):
        call_count[0] += 1
        if call_count[0] == 1:
            result = MagicMock()
            result.scalar_one_or_none.return_value = mock_skill
            return result
        else:
            result = MagicMock()
            result.scalars.return_value.all.return_value = [mock_log1, mock_log2, mock_log3]
            return result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("app.skills.shadow_service.audit"):
        from app.skills.lifecycle.shadow_service import get_shadow_stats
        result = await get_shadow_stats(mock_db, "SHADOW-05")

    assert result["skill_id"] == "SHADOW-05"
    assert result["total_shadow_runs"] == 3
    assert result["total_with_human"] == 2
    assert result["consistent_count"] == 1
    assert result["consistency_rate"] == 50.0


@pytest.mark.asyncio
async def test_record_human_decision_service():
    """测试记录人工决策"""
    from app.execution.models import DecisionLog

    mock_log = MagicMock(spec=DecisionLog)
    mock_log.skill_id = "SHADOW-06"
    mock_log.run_id = "run-001"
    mock_log.user_action = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_log
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.flush = AsyncMock()

    with patch("app.skills.shadow_service.audit") as mock_audit:
        mock_audit.log = AsyncMock()

        from app.skills.lifecycle.shadow_service import record_human_decision
        result = await record_human_decision(
            mock_db, "SHADOW-06", "run-001", "completed", "admin"
        )

    assert result["human_action"] == "completed"
    assert mock_log.user_action == "completed"


@pytest.mark.asyncio
async def test_record_human_decision_invalid_action():
    """测试记录无效的人工决策——应报错"""
    from app.common.exceptions import AppError

    mock_log = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_log
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("app.skills.shadow_service.audit"):
        with pytest.raises(AppError) as exc_info:
            from app.skills.lifecycle.shadow_service import record_human_decision
            await record_human_decision(
                mock_db, "SHADOW-07", "run-002", "invalid_action", "admin"
            )

    assert exc_info.value.code == "SKILL_VERSION_CONFLICT"


# ===== 新增: 解析器——多种格式测试 =====


def test_parser_tree_markers():
    """测试解析器：树形符号格式（├─ 条件 → 结论）"""
    from app.skills.core.parser import skill_parser

    md = """---
name: 树形测试
department: 电商
---

# 树形测试

## 执行步骤

### Step 1: 按ROI排序

  ├─ ROI > 盈亏线 × 1.2 → 绿灯
      动作: 加预算
  └─ ROI < 盈亏线 × 0.8 → 红灯
      动作: 暂停投放
"""
    parsed = skill_parser.parse(md)
    assert len(parsed.steps) == 1
    step = parsed.steps[0]
    assert step.name == "按ROI排序"
    assert len(step.branches) == 2
    assert "ROI > 盈亏线" in step.branches[0].condition
    assert step.branches[0].conclusion == "绿灯"
    assert step.branches[0].action == "加预算"
    assert "ROI < 盈亏线" in step.branches[1].condition
    assert step.branches[1].conclusion == "红灯"


def test_parser_condition_prefix():
    """测试解析器：条件前缀格式（条件: ROI > 1.5）"""
    from app.skills.core.parser import skill_parser

    md = """---
name: 条件前缀测试
department: 电商
---

# 条件前缀测试

## 执行步骤

### Step 1: 检查ROI

条件: ROI > 1.5
结论: 绿灯
动作: 可以加预算

条件: ROI <= 0.8
结论: 红灯
动作: 暂停投放
"""
    parsed = skill_parser.parse(md)
    assert len(parsed.steps) == 1
    step = parsed.steps[0]
    assert len(step.branches) == 2
    assert "ROI > 1.5" in step.branches[0].condition
    assert step.branches[0].conclusion == "绿灯"


def test_parser_plain_conditions():
    """测试解析器：纯文本条件行（含比较运算符的行）"""
    from app.skills.core.parser import skill_parser

    md = """---
name: 纯文本测试
department: 电商
---

# 纯文本测试

## 执行步骤

### Step 1: ROI判断

ROI > 盈亏线 × 1.2 → 绿灯
ROI < 盈亏线 × 0.8 → 红灯
"""
    parsed = skill_parser.parse(md)
    assert len(parsed.steps) == 1
    assert len(parsed.steps[0].branches) == 2
    assert "绿灯" in parsed.steps[0].branches[0].conclusion
    assert "红灯" in parsed.steps[0].branches[1].conclusion


def test_parser_output_table():
    """测试解析器：输出定义Markdown表格"""
    from app.skills.core.parser import skill_parser

    md = """---
name: 表格测试
department: 测试
---

# 表格测试

## 输出

| 输出项 | 格式 | 接收人 | 审批级别 |
|---|---|---|---|
| 日报 | 钉钉卡片 | 运营组长 | L1 |
| 周报 | Excel | 部门负责人 | L2 |
"""
    parsed = skill_parser.parse(md)
    assert len(parsed.output_definition) == 2
    assert parsed.output_definition[0].name == "日报"
    assert parsed.output_definition[0].format == "钉钉卡片"
    assert parsed.output_definition[1].approval_level == "L2"


def test_parser_data_inputs_table():
    """测试解析器：数据输入表格"""
    from app.skills.core.parser import skill_parser

    md = """---
name: 数据输入测试
department: 测试
---

# 数据输入测试

## 数据输入

| 数据名称 | 来源 | 刷新频率 |
|---|---|---|
| 投放数据 | 直通车API | 每日 |
| 店铺销售 | 生意参谋 | 每小时 |
"""
    parsed = skill_parser.parse(md)
    assert len(parsed.data_inputs) == 2
    assert parsed.data_inputs[0].name == "投放数据"
    assert parsed.data_inputs[1].frequency == "每小时"


def test_parser_test_cases():
    """测试解析器：测试用例章节"""
    from app.skills.core.parser import skill_parser

    md = """---
name: 测试用例测试
department: 测试
---

# 测试用例测试

## 测试用例

### 高ROI场景

**输入:**
```json
{"roi": 2.5, "budget": 10000}
```

**期望输出:**
```json
{"decision": "绿灯", "action": "加预算"}
```

**断言:**
- `output.decision == '绿灯'`
"""
    parsed = skill_parser.parse(md)
    assert len(parsed.test_cases) == 1
    tc = parsed.test_cases[0]
    assert tc.name == "高ROI场景"
    assert tc.input_data == {"roi": 2.5, "budget": 10000}
    assert tc.expected_output["decision"] == "绿灯"
    assert len(tc.assert_rules) == 1


def test_parser_multiple_steps_with_next_step():
    """测试解析器：多步骤 + 步骤间跳转"""
    from app.skills.core.parser import skill_parser

    md = """---
name: 多步骤测试
department: 测试
---

# 多步骤测试

## 执行步骤

### Step 1: 初筛

  ├─ ROI > 1.5 → 绿灯
      → 进入 Step 2
  └─ ROI <= 1.5 → 黄灯

### Step 2: 复核

  ├─ 转化率 > 5% → 放行
  └─ 转化率 <= 5% → 降预算
"""
    parsed = skill_parser.parse(md)
    assert len(parsed.steps) == 2
    assert parsed.steps[0].branches[0].next_step == "2"
    assert parsed.steps[1].name == "复核"


def test_parser_render_roundtrip_full():
    """测试完整Skill的render→parse双向转换无损"""
    from app.skills.core.parser import (
        skill_parser, SkillStructured, DecisionStep, Branch,
        Antipattern, OutputItem, DataInput, TestCase,
    )

    original = SkillStructured(
        frontmatter={"name": "双向测试", "department": "电商", "trigger_type": "cron"},
        purpose="这是一个完整的双向转换测试Skill。",
        steps=[
            DecisionStep(
                id="1", name="ROI判断",
                branches=[
                    Branch(condition="ROI > 1.5", conclusion="绿灯", action="加预算"),
                    Branch(condition="ROI < 0.8", conclusion="红灯", action="暂停"),
                ],
            ),
        ],
        antipatterns=[
            Antipattern(scenario="新品期误判", correct_action="延长观察期"),
        ],
        output_definition=[
            OutputItem(name="日报", format="钉钉", recipient="运营", approval_level="L1"),
        ],
        data_inputs=[
            DataInput(name="投放数据", source="API", frequency="每日"),
        ],
        test_cases=[
            TestCase(name="绿灯用例", input_data={"roi": 2.0}, expected_output={"decision": "绿灯"}),
        ],
    )

    rendered = skill_parser.render(original)
    re_parsed = skill_parser.parse(rendered)

    assert re_parsed.frontmatter["name"] == "双向测试"
    assert "完整的双向转换测试" in re_parsed.purpose
    assert len(re_parsed.steps) == 1
    assert len(re_parsed.steps[0].branches) == 2
    assert len(re_parsed.antipatterns) == 1
    assert len(re_parsed.output_definition) == 1
    assert len(re_parsed.data_inputs) == 1
    assert len(re_parsed.test_cases) == 1


# ===== 新增: Skill ID 格式校验 =====


def test_skill_id_validation_valid():
    """测试合法的Skill ID"""
    from app.skills.lifecycle.service import validate_skill_id
    assert validate_skill_id("EC-投放-01") == "EC-投放-01"
    assert validate_skill_id("test_skill_1") == "test_skill_1"


def test_skill_id_validation_path_traversal():
    """测试路径穿越的Skill ID——应拒绝"""
    from app.common.exceptions import AppError
    from app.skills.lifecycle.service import validate_skill_id

    with pytest.raises(AppError) as exc_info:
        validate_skill_id("../../etc/passwd")
    assert exc_info.value.code == "SKILL_ID_INVALID"


# ===== delete_skill 关联表清理回归测试 =====
# 防止 ExecutionRun.skill_id 这种"模型不存在的字段"错误再次出现


@pytest.mark.asyncio
async def test_delete_skill_cleans_all_related(client):
    """delete_skill 应清理所有关联表，不报 AttributeError / IntegrityError"""
    from datetime import datetime
    from sqlalchemy import select
    from app.database import async_session_factory
    from app.skills.lifecycle.service import delete_skill
    from app.skills.core.models import Skill, UserSkillPin, SkillLock
    from app.execution.models import DecisionLog, ExecutionStep, ShadowComparison
    from app.testing.models import Conversation

    skill_id = "test-delete-skill-001"

    async with async_session_factory() as db:
        # 1. 创建主 Skill
        db.add(Skill(
            id=skill_id, name="待删除", department="EC",
            status="draft", created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ))
        # 2. 创建各种关联记录（覆盖所有清理路径）
        db.add(UserSkillPin(user_id="alice", skill_id=skill_id))
        db.add(SkillLock(skill_id=skill_id, user_id="alice"))
        db.add(DecisionLog(skill_id=skill_id, run_id="run-1"))
        db.add(ExecutionStep(run_id="run-1", skill_id=skill_id))
        db.add(ShadowComparison(skill_id=skill_id, run_id="run-1"))
        db.add(Conversation(
            id="conv-del-1", skill_id=skill_id, user_id="alice",
            messages=[], model_id="test", total_tokens=0,
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ))
        await db.commit()

    # 3. 执行删除（不应抛异常）
    async with async_session_factory() as db:
        result = await delete_skill(db, skill_id, "admin")
        await db.commit()
        assert result == {"skill_id": skill_id, "deleted": True}

    # 4. 验证所有关联记录都已被清理
    async with async_session_factory() as db:
        assert (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none() is None
        assert (await db.execute(select(UserSkillPin).where(UserSkillPin.skill_id == skill_id))).scalars().all() == []
        assert (await db.execute(select(SkillLock).where(SkillLock.skill_id == skill_id))).scalar_one_or_none() is None
        assert (await db.execute(select(DecisionLog).where(DecisionLog.skill_id == skill_id))).scalars().all() == []
        assert (await db.execute(select(ExecutionStep).where(ExecutionStep.skill_id == skill_id))).scalars().all() == []
        assert (await db.execute(select(ShadowComparison).where(ShadowComparison.skill_id == skill_id))).scalars().all() == []
        assert (await db.execute(select(Conversation).where(Conversation.skill_id == skill_id))).scalars().all() == []


@pytest.mark.asyncio
async def test_delete_skill_with_optimizer_candidates(client):
    """删除带 OptimizerCandidate（FK 子表）的 Skill 应先清理 candidates"""
    from datetime import datetime
    from sqlalchemy import select
    from app.database import async_session_factory
    from app.skills.lifecycle.service import delete_skill
    from app.skills.core.models import Skill
    from app.optimizer.models import OptimizerSession, OptimizerCandidate

    skill_id = "test-delete-with-optimizer"

    async with async_session_factory() as db:
        db.add(Skill(
            id=skill_id, name="带优化器", department="EC",
            status="draft", created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ))
        await db.flush()
        db.add(OptimizerSession(
            id="opt-sess-1", skill_id=skill_id, name="session1",
            goal="提高准确率", config={}, created_by="alice",
        ))
        await db.flush()
        db.add(OptimizerCandidate(
            id="cand-1", session_id="opt-sess-1", iteration=1,
        ))
        await db.commit()

    # 删除 — 必须先删 candidates 才能删 session（FK 约束）
    async with async_session_factory() as db:
        result = await delete_skill(db, skill_id, "admin")
        await db.commit()
        assert result["deleted"] is True

    async with async_session_factory() as db:
        assert (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none() is None
        assert (await db.execute(select(OptimizerSession).where(OptimizerSession.skill_id == skill_id))).scalars().all() == []
        assert (await db.execute(select(OptimizerCandidate).where(OptimizerCandidate.id == "cand-1"))).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_skill_not_found(client):
    """删除不存在的 Skill 应抛 SKILL_NOT_FOUND，不是 500"""
    from app.common.exceptions import AppError
    from app.database import async_session_factory
    from app.skills.lifecycle.service import delete_skill

    async with async_session_factory() as db:
        with pytest.raises(AppError) as exc_info:
            await delete_skill(db, "nonexistent-skill-xyz", "admin")
        assert exc_info.value.code == "SKILL_NOT_FOUND"
        assert exc_info.value.status == 404


def test_commit_all_scoped_delete_does_not_commit_other_staged(tmp_path):
    """按 skill_id 提交删除时，不应把其它已 staged 变更一起提交。"""
    import shutil
    from git import Repo
    from app.skills.core.git_service import GitService

    repo = Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "test")
        cw.set_value("user", "email", "test@example.com")

    remove_dir = tmp_path / "remove-skill"
    keep_dir = tmp_path / "keep-skill"
    remove_dir.mkdir()
    keep_dir.mkdir()
    (remove_dir / "SKILL.md").write_text("---\nname: remove\n---\n", encoding="utf-8")
    (keep_dir / "SKILL.md").write_text("---\nname: keep\n---\n", encoding="utf-8")
    repo.git.add("-A")
    repo.index.commit("init")

    (keep_dir / "SKILL.md").write_text("---\nname: keep changed\n---\n", encoding="utf-8")
    repo.git.add("keep-skill/SKILL.md")
    shutil.rmtree(remove_dir)

    git_service = GitService(str(tmp_path))
    commit_sha = git_service.commit_all("删除 Skill: remove-skill", "tester", skill_id="remove-skill")

    assert commit_sha
    committed_paths = Repo(tmp_path).git.show("--name-only", "--format=", "HEAD").splitlines()
    assert committed_paths == ["remove-skill/SKILL.md"]
    assert Repo(tmp_path).git.status("--short").strip() == "M  keep-skill/SKILL.md"


@pytest.mark.asyncio
async def test_delete_skill_removes_repo_dir_and_commits(client, tmp_path, monkeypatch):
    """删除 Skill 应物理删除 skills-repo 目录，并提交该目录的删除记录。"""
    from datetime import datetime
    from git import Repo
    from app.database import async_session_factory
    from app.skills.core.git_service import GitService
    from app.skills.core.models import Skill
    from app.skills.lifecycle import service_runtime

    skill_id = "test-delete-files-001"
    repo = Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "test")
        cw.set_value("user", "email", "test@example.com")

    skill_dir = tmp_path / skill_id
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: test\n---\n", encoding="utf-8")
    (skill_dir / "scripts" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    repo.git.add("-A")
    repo.index.commit("init")

    monkeypatch.setattr(service_runtime, "git_service", GitService(str(tmp_path)))
    monkeypatch.setattr(service_runtime.settings, "AICLAW_SKILLS_DIR", "")

    async with async_session_factory() as db:
        db.add(Skill(
            id=skill_id, name="待物理删除", department="EC",
            status="draft", created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ))
        await db.commit()

    async with async_session_factory() as db:
        result = await service_runtime.delete_skill(db, skill_id, "admin")
        await db.commit()

    assert result == {"skill_id": skill_id, "deleted": True}
    assert not skill_dir.exists()
    committed_paths = Repo(tmp_path).git.show("--name-only", "--format=", "HEAD").splitlines()
    assert f"{skill_id}/SKILL.md" in committed_paths
    assert f"{skill_id}/scripts/main.py" in committed_paths


def test_git_service_rejects_uninitialized_skill_repo(tmp_path):
    from app.common.exceptions import AppError
    from app.skills.core.git_service import GitService

    service = GitService(str(tmp_path))

    with pytest.raises(AppError) as exc_info:
        _ = service.repo

    assert exc_info.value.code == "SKILL_GIT_REPO_MISSING"


@pytest.mark.asyncio
async def test_batch_publish_allows_draft_skill(client):
    from app.database import async_session_factory
    from app.skills.core.models import Skill
    from sqlalchemy import select

    skill_id = "PUBLISH-DRAFT-01"
    async with async_session_factory() as session:
        session.add(Skill(
            id=skill_id,
            name="待发布Skill",
            description="draft can publish",
            department="AI小组",
            role="分析师",
            trigger_type="manual",
            risk_level="R2",
            owner="admin",
            status="draft",
            current_version="v0.0",
        ))
        await session.commit()

    ready_report = MagicMock(can_publish=True, blocker_count=0, blockers=[])
    with patch("app.skills.publish_readiness.check_publish_readiness", new=AsyncMock(return_value=ready_report)), \
         patch("app.execution.sync_service.sync_service.reload_all", new=AsyncMock(return_value={"ok": True})):
        resp = await client.post("/api/skills/batch-publish", json={"skill_ids": [skill_id]})

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] == 1
    assert data["results"][0]["status"] == "ok"

    async with async_session_factory() as session:
        skill = (await session.execute(select(Skill).where(Skill.id == skill_id))).scalar_one()
        assert skill.status == "active"
