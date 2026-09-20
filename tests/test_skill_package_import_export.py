from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import UploadFile

from app.skills import router_assets


def _build_skill_package(*, current_version: str | None = None) -> bytes:
    manifest = {
        "id": "pkg-skill",
        "name": "打包测试",
        "department": "EC",
        "trigger_type": "manual",
        "risk_level": "R2",
    }
    if current_version:
        manifest["current_version"] = current_version

    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr(
            "SKILL.md",
            """---
name: 打包测试
department: EC
risk_level: R2
trigger_type: manual
---

# 打包测试
""",
        )
    return raw.getvalue()


@pytest.mark.asyncio
async def test_export_skill_includes_current_version_in_manifest(monkeypatch, tmp_path):
    skill_dir = tmp_path / "pkg-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# pkg", encoding="utf-8")

    monkeypatch.setattr(router_assets.settings, "SKILL_REPO_PATH", str(tmp_path))
    monkeypatch.setattr(router_assets, "ensure_skill_department_access", AsyncMock())

    skill = SimpleNamespace(
        id="pkg-skill",
        name="打包测试",
        display_name="打包测试",
        department="EC",
        role="operator",
        trigger_type="manual",
        risk_level="R2",
        category="ops",
        description="desc",
        updated_at=datetime(2026, 4, 23, 9, 0, 0),
        current_version="v2.3",
    )
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = skill
    db = MagicMock()
    db.execute = AsyncMock(return_value=query_result)
    user = SimpleNamespace(id="admin")

    response = await router_assets.export_skill("pkg-skill", db=db, current_user=user)

    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest["current_version"] == "v2.3"
        assert zf.read("SKILL.md").decode("utf-8") == "# pkg"

    assert response.headers["content-disposition"] == 'attachment; filename="pkg-skill-v2.3.zip"'


@pytest.mark.asyncio
async def test_import_skill_uses_manifest_current_version(monkeypatch):
    create_mock = AsyncMock(return_value={"skill_id": "pkg-skill", "git_commit": "abc123", "current_version": "v1.4"})
    audit_log = AsyncMock()

    monkeypatch.setattr(router_assets.skill_service, "create_skill_from_files", create_mock)
    monkeypatch.setattr("app.common.audit.audit.log", audit_log)

    upload = UploadFile(filename="pkg-skill.zip", file=io.BytesIO(_build_skill_package(current_version="v1.4")))
    user = SimpleNamespace(id="admin", role="admin", department="EC", can_view_all=True)

    result = await router_assets.import_skill(
        file=upload,
        new_skill_id=None,
        department="EC",
        current_version=None,
        db=MagicMock(),
        current_user=user,
    )

    assert create_mock.await_args.kwargs["current_version"] == "v1.4"
    assert result["current_version"] == "v1.4"


@pytest.mark.asyncio
async def test_import_skill_query_version_overrides_manifest(monkeypatch):
    create_mock = AsyncMock(return_value={"skill_id": "pkg-skill", "git_commit": "abc123", "current_version": "v2.0"})
    audit_log = AsyncMock()

    monkeypatch.setattr(router_assets.skill_service, "create_skill_from_files", create_mock)
    monkeypatch.setattr("app.common.audit.audit.log", audit_log)

    upload = UploadFile(filename="pkg-skill.zip", file=io.BytesIO(_build_skill_package(current_version="v1.4")))
    user = SimpleNamespace(id="admin", role="admin", department="EC", can_view_all=True)

    result = await router_assets.import_skill(
        file=upload,
        new_skill_id=None,
        department="EC",
        current_version="2.0",
        db=MagicMock(),
        current_user=user,
    )

    assert create_mock.await_args.kwargs["current_version"] == "v2.0"
    assert result["current_version"] == "v2.0"
