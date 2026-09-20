"""M3 模板市场测试。"""

import pytest


def test_list_templates_returns_metadata():
    from app.skills.template_market import list_templates

    items = list_templates()
    assert isinstance(items, list)
    if items:
        first = items[0]
        assert "id" in first
        assert "display_name" in first
        assert "category" in first


def test_get_template_detail_contains_files():
    from app.skills.template_market import get_template_detail, list_templates

    templates = list_templates()
    assert templates
    template_id = templates[0]["id"]
    detail = get_template_detail(template_id)
    assert detail is not None
    assert detail["id"] == template_id
    assert "SKILL.md" in detail["files"]


@pytest.mark.asyncio
async def test_fork_template_creates_skill_in_db(client):
    from app.skills.template_market import list_templates

    template_id = list_templates()[0]["id"]
    resp = await client.post(f"/api/skills/templates/{template_id}/fork", json={
        "skill_id": "EC-TPL-01",
        "department": "总部",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["skill_id"] == "EC-TPL-01"

    skill_resp = await client.get("/api/skills/EC-TPL-01")
    assert skill_resp.status_code == 200
    detail = skill_resp.json()
    assert detail["id"] == "EC-TPL-01"
    assert detail["department"] == "总部"

    member_resp = await client.get("/api/skills/EC-TPL-01/members")
    assert member_resp.status_code == 200
    members = member_resp.json()["items"]
    assert any(item["role"] == "owner" for item in members)
