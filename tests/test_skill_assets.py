"""Skill 资产管理测试：模板 / 发布记录 / 血缘追踪 / Fork。

所有依赖 Git 文件系统的操作通过 mock 绕过。
源 Skill 直接写测试库，避免 API 请求在同一个 client fixture 内串接时依赖提交时机。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


async def _seed_skill(client, skill_id: str = "test-asset-src"):
    """直接写测试数据库，给资产接口提供一个稳定的源 Skill。"""
    import app.database as db_mod
    from app.skills.core.models import Skill

    async with db_mod.async_session_factory() as session:
        existing = await session.get(Skill, skill_id)
        if existing:
            return skill_id
        session.add(
            Skill(
                id=skill_id,
                name="资产测试源Skill",
                description="资产测试源Skill",
                department="AI小组",
                role="ai_engineer",
                trigger_type="manual",
                risk_level="R2",
                approval_level=1,
                status="draft",
                owner="admin",
            )
        )
        await session.commit()
    return skill_id


# ────────────────────────── 模板管理 ──────────────────────────

@pytest.mark.asyncio
async def test_publish_as_template(client):
    """从 Skill 创建模板"""
    skill_id = await _seed_skill(client)

    with patch("app.skills.asset_service.git_service") as mock_git, \
         patch("app.skills.asset_service.audit") as mock_audit:
        mock_git.read_file.return_value = "---\nname: test\n---\n# 测试"
        mock_audit.log = AsyncMock()

        resp = await client.post(f"/api/skills/{skill_id}/publish-as-template")
        assert resp.status_code == 200
        data = resp.json()
        assert data["template_id"] == f"tpl-{skill_id}"
        assert data["source_skill_id"] == skill_id


@pytest.mark.asyncio
async def test_publish_as_template_duplicate(client):
    """重复创建模板应该 409"""
    skill_id = await _seed_skill(client)

    with patch("app.skills.asset_service.git_service") as mock_git, \
         patch("app.skills.asset_service.audit") as mock_audit:
        mock_git.read_file.return_value = "---\nname: test\n---\n# 测试"
        mock_audit.log = AsyncMock()

        # 第一次创建
        await client.post(f"/api/skills/{skill_id}/publish-as-template")
        # 第二次应该 409
        resp = await client.post(f"/api/skills/{skill_id}/publish-as-template")
        assert resp.status_code == 409


@pytest.mark.asyncio
async def test_list_templates_published_filter(client):
    """列出模板：默认只列已发布，published_only=False 列所有"""
    skill_id = await _seed_skill(client)

    with patch("app.skills.asset_service.git_service") as mock_git, \
         patch("app.skills.asset_service.audit") as mock_audit:
        mock_git.read_file.return_value = "---\nname: test\n---\n"
        mock_audit.log = AsyncMock()
        await client.post(f"/api/skills/{skill_id}/publish-as-template")

    # 未发布时，published_only=True 看不到
    resp = await client.get("/api/skills/templates", params={"published_only": "true"})
    assert resp.status_code == 200
    assert len(resp.json()) == 0

    # published_only=False 可以看到
    resp = await client.get("/api/skills/templates", params={"published_only": "false"})
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_get_template_detail(client):
    """获取单个模板详情"""
    skill_id = await _seed_skill(client)

    with patch("app.skills.asset_service.git_service") as mock_git, \
         patch("app.skills.asset_service.audit") as mock_audit:
        mock_git.read_file.return_value = "---\nname: test\n---\n"
        mock_audit.log = AsyncMock()
        await client.post(f"/api/skills/{skill_id}/publish-as-template")

    template_id = f"tpl-{skill_id}"
    resp = await client.get(f"/api/skills/templates/{template_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == template_id
    assert data["source_skill_id"] == skill_id


@pytest.mark.asyncio
async def test_get_template_not_found(client):
    """查询不存在的模板应该 404"""
    resp = await client.get("/api/skills/templates/tpl-nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_publish_template_to_market(client):
    """发布模板后 published_only=True 可见"""
    skill_id = await _seed_skill(client)

    with patch("app.skills.asset_service.git_service") as mock_git, \
         patch("app.skills.asset_service.audit") as mock_audit:
        mock_git.read_file.return_value = "---\nname: test\n---\n"
        mock_audit.log = AsyncMock()
        await client.post(f"/api/skills/{skill_id}/publish-as-template")

    template_id = f"tpl-{skill_id}"

    with patch("app.skills.asset_service.audit") as mock_audit:
        mock_audit.log = AsyncMock()
        resp = await client.post(f"/api/skills/templates/{template_id}/publish")
        assert resp.status_code == 200
        assert resp.json()["is_published"] is True

    # published_only=True 应该能看到了
    resp = await client.get("/api/skills/templates", params={"published_only": "true"})
    assert resp.status_code == 200
    assert any(t["id"] == template_id for t in resp.json())


# ────────────────────────── 发布记录 ──────────────────────────

@pytest.mark.asyncio
async def test_create_and_list_releases(client):
    """创建发布记录并列出"""
    skill_id = await _seed_skill(client)

    with patch("app.skills.asset_service.git_service") as mock_git, \
         patch("app.skills.asset_service.audit") as mock_audit:
        mock_git.read_file.return_value = "---\nname: test\n---\n# 内容"
        mock_audit.log = AsyncMock()

        resp1 = await client.post(
            f"/api/skills/{skill_id}/releases",
            json={"version": "v1.0", "git_ref": "abc1234", "release_notes": "初始发布"},
        )
        assert resp1.status_code == 200
        assert resp1.json()["version"] == "v1.0"
        assert resp1.json()["artifact_digest"]  # sha256 应有值

        resp2 = await client.post(
            f"/api/skills/{skill_id}/releases",
            json={"version": "v1.1", "release_notes": "Bug修复"},
        )
        assert resp2.status_code == 200

    resp = await client.get(f"/api/skills/{skill_id}/releases")
    assert resp.status_code == 200
    releases = resp.json()
    assert len(releases) == 2
    # 应按时间倒序：v1.1 在前
    assert releases[0]["version"] == "v1.1"
    assert releases[1]["version"] == "v1.0"


@pytest.mark.asyncio
async def test_release_skill_not_found(client):
    """给不存在的 Skill 创建 release 应该 404"""
    with patch("app.skills.asset_service.git_service") as mock_git, \
         patch("app.skills.asset_service.audit") as mock_audit:
        mock_git.read_file.return_value = ""
        mock_audit.log = AsyncMock()

        resp = await client.post(
            "/api/skills/nonexistent-skill/releases",
            json={"version": "v1.0"},
        )
        assert resp.status_code == 404


# ────────────────────────── 血缘追踪 ──────────────────────────

@pytest.mark.asyncio
async def test_lineage_after_fork(client):
    """Fork 后血缘关系应自动创建"""
    skill_id = await _seed_skill(client)

    with patch("app.skills.asset_service.git_service") as mock_git, \
         patch("app.skills.asset_service.audit") as mock_audit, \
         patch("app.skills.asset_service.shutil") as mock_shutil, \
         patch("app.skills.asset_service.Path") as mock_path_cls:
        mock_git.read_file.return_value = "---\nname: test\ndepartment: AI小组\n---\n"
        mock_git.create_skill_dir.return_value = MagicMock()
        mock_git.write_file.return_value = MagicMock()
        mock_git.commit_all.return_value = "fork111"
        mock_audit.log = AsyncMock()
        # mock Path(...) / skill_id → is_dir() = True
        mock_path_inst = MagicMock()
        mock_path_inst.is_dir.return_value = True
        mock_path_cls.return_value.__truediv__ = MagicMock(return_value=mock_path_inst)

        resp = await client.post(
            f"/api/skills/{skill_id}/fork",
            json={"new_skill_id": "test-fork-lineage", "department": "EC"},
        )
        assert resp.status_code == 200

    # 查询源 Skill 的 downstream
    resp = await client.get(
        f"/api/skills/{skill_id}/lineage", params={"direction": "downstream"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["downstream"]) >= 1
    assert data["downstream"][0]["target_id"] == "test-fork-lineage"
    assert data["downstream"][0]["relation_type"] == "fork"

    # 查询 fork Skill 的 upstream
    resp2 = await client.get(
        "/api/skills/test-fork-lineage/lineage", params={"direction": "upstream"},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["upstream"]) >= 1
    assert data2["upstream"][0]["source_id"] == skill_id


# ────────────────────────── Skill-to-Skill Fork ──────────────────────────

@pytest.mark.asyncio
async def test_fork_skill_basic(client):
    """Fork Skill 基本流程"""
    skill_id = await _seed_skill(client)

    with patch("app.skills.asset_service.git_service") as mock_git, \
         patch("app.skills.asset_service.audit") as mock_audit, \
         patch("app.skills.asset_service.shutil") as mock_shutil, \
         patch("app.skills.asset_service.Path") as mock_path_cls:
        mock_git.read_file.return_value = "---\nname: test\ndepartment: AI小组\n---\n"
        mock_git.create_skill_dir.return_value = MagicMock()
        mock_git.write_file.return_value = MagicMock()
        mock_git.commit_all.return_value = "fork222"
        mock_audit.log = AsyncMock()
        mock_path_inst = MagicMock()
        mock_path_inst.is_dir.return_value = True
        mock_path_cls.return_value.__truediv__ = MagicMock(return_value=mock_path_inst)

        resp = await client.post(
            f"/api/skills/{skill_id}/fork",
            json={"new_skill_id": "test-fork-basic", "department": "EC"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["skill_id"] == "test-fork-basic"
        assert data["source_skill_id"] == skill_id
        assert data["git_commit"] == "fork222"
        assert data["lineage_id"]


@pytest.mark.asyncio
async def test_fork_skill_duplicate_target(client):
    """Fork 到已存在的 skill_id 应该 409"""
    skill_id = await _seed_skill(client)

    with patch("app.skills.asset_service.git_service") as mock_git, \
         patch("app.skills.asset_service.audit") as mock_audit, \
         patch("app.skills.asset_service.shutil") as mock_shutil, \
         patch("app.skills.asset_service.Path") as mock_path_cls:
        mock_git.read_file.return_value = "---\nname: test\ndepartment: AI小组\n---\n"
        mock_git.create_skill_dir.return_value = MagicMock()
        mock_git.write_file.return_value = MagicMock()
        mock_git.commit_all.return_value = "fork333"
        mock_audit.log = AsyncMock()
        mock_path_inst = MagicMock()
        mock_path_inst.is_dir.return_value = True
        mock_path_cls.return_value.__truediv__ = MagicMock(return_value=mock_path_inst)

        # 第一次 fork 成功
        await client.post(
            f"/api/skills/{skill_id}/fork",
            json={"new_skill_id": "test-fork-dup", "department": "AI小组"},
        )
        # 第二次同 target_id 应该 409
        resp = await client.post(
            f"/api/skills/{skill_id}/fork",
            json={"new_skill_id": "test-fork-dup", "department": "AI小组"},
        )
        assert resp.status_code == 409


@pytest.mark.asyncio
async def test_fork_nonexistent_source(client):
    """Fork 不存在的源 Skill 应该 404"""
    with patch("app.skills.asset_service.git_service") as mock_git, \
         patch("app.skills.asset_service.audit") as mock_audit:
        mock_audit.log = AsyncMock()

        resp = await client.post(
            "/api/skills/nonexistent-xyz/fork",
            json={"new_skill_id": "test-fork-404", "department": "AI小组"},
        )
        assert resp.status_code == 404
